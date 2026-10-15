"""Azure File CSI Driver commands."""

import click
import json

from typing import Optional, Dict, Any, List

from helpers.auth import elevated_cicd_spn
from helpers.shell import run_cmd, run_json, run_text
from helpers.ui import banner

from .utils import dump_manifests, write_output

POOL_NAME_TAG = "aks-managed-poolName"
SMB_ROLE = "Storage File Data SMB MI Admin"

@click.group()
def azureFile():
    """Azure File CSI Driver management commands"""
    pass

def _get_or_create_identity(identity_name: str, resource_group: str) -> Dict[str, str]:
    """Return {clientId, id, principalId} of a managed identity, creating it if missing."""
    query = ["--query", "{clientId:clientId,id:id,principalId:principalId}", "-o", "json"]
    existing = run_cmd(
        ["az", "identity", "show", "--name", identity_name, "--resource-group", resource_group, *query],
        check=False,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        identity = json.loads(existing.stdout)
        click.echo(f"  ✅ Managed identity exists with client ID: {identity['clientId']}")
        return identity

    click.echo(f"  Creating managed identity: {identity_name}...")
    identity = run_json(
        ["az", "identity", "create", "--name", identity_name, "--resource-group", resource_group, *query],
        error_msg=f"Error creating managed identity {identity_name}",
    )
    click.echo(f"  ✅ Managed identity created with client ID: {identity['clientId']}")
    return identity

def _get_vmss_names_by_pool(node_resource_group: str, pool_name: str) -> List[str]:
    """Return the VMSS of the node resource group tagged with the given AKS pool name."""
    vmss_names = run_json(
        ["az", "vmss", "list",
         "--resource-group", node_resource_group,
         "--query", f"[?tags.\"{POOL_NAME_TAG}\"=='{pool_name}'].name",
         "-o", "json"],
        error_msg=f"Error listing VMSS in {node_resource_group}",
    ) or []

    for vmss_name in vmss_names:
        click.echo(f"  Found VMSS: {vmss_name} (pool: {pool_name})")
    if not vmss_names:
        click.echo(f"  ⚠️  No VMSS found with tag '{POOL_NAME_TAG}={pool_name}'", err=True)
    return vmss_names

def _assign_identity_to_vmss(identity_resource_id: str, vmss_names: List[str], node_resource_group: str) -> None:
    for vmss_name in vmss_names:
        click.echo(f"  Assigning identity to VMSS: {vmss_name}...")
        run_cmd(
            ["az", "vmss", "identity", "assign",
             "--identities", identity_resource_id,
             "--name", vmss_name,
             "--resource-group", node_resource_group],
            error_msg=f"Error assigning identity to VMSS {vmss_name}",
            success_msg=f"Identity assigned to VMSS: {vmss_name}",
        )

def _print_smb_manual_step(storage_account: str, storage_resource_group: str) -> None:
    banner("⚠️  MANUAL STEP REQUIRED")
    click.echo("Please enable 'Managed Identity for SMB' on the storage account:")
    click.echo(f"  Storage Account: {storage_account}")
    click.echo(f"  Resource Group: {storage_resource_group}")
    click.echo("\nSteps:")
    click.echo(f"  1. Go to Azure Portal > Storage Account > {storage_account}")
    click.echo("  2. Navigate to Configuration")
    click.echo("  3. Enable 'Managed Identity for SMB'")
    click.echo("  4. Save changes")

def _setup_infrastructure(
    env: str,
    aks_code: str,
    adl_code: str,
    pool_name: str
) -> str:
    """
    Set up the required infrastructure for Azure File CSI.

    Returns the client ID of the managed identity.
    """
    banner("Setting up Azure File CSI infrastructure...")

    identity_name = f"azurefilecsi-{env}d9{aks_code}aks01"
    aks_name = f"{env}d9{aks_code}aks01"
    aks_resource_group = f"r400{env}d9{aks_code}"
    storage_account = f"{env}d9{adl_code}adl01"
    storage_resource_group = f"r400{env}d9{adl_code}"

    # 1. AKS node resource group (managed RG where the identity lives)
    click.echo("Getting AKS node resource group...")
    node_resource_group = run_text(
        ["az", "aks", "show", "--name", aks_name, "--resource-group", aks_resource_group,
         "--query", "nodeResourceGroup", "-o", "tsv"],
        error_msg=f"Error getting node resource group for {aks_name}",
    )
    click.echo(f"  Node resource group: {node_resource_group}")

    # 2. Managed identity in the node resource group
    click.echo(f"\nChecking managed identity: {identity_name}...")
    identity = _get_or_create_identity(identity_name, node_resource_group)

    # 3. Assign the identity to the node pool VMSS
    click.echo(f"\nGetting VMSS from pool {pool_name}...")
    vmss_names = _get_vmss_names_by_pool(node_resource_group, pool_name)
    _assign_identity_to_vmss(identity["id"], vmss_names, node_resource_group)

    # 4. Manual step: enable Managed Identity for SMB
    _print_smb_manual_step(storage_account, storage_resource_group)

    # 5. RBAC role on the storage account (requires elevated permissions)
    banner("Assigning RBAC role to storage account...")
    storage_account_id = run_text(
        ["az", "storage", "account", "show", "--name", storage_account, "--resource-group", storage_resource_group,
         "--query", "id", "-o", "tsv"],
        error_msg=f"Error getting resource ID of {storage_account}",
    )
    with elevated_cicd_spn():
        run_cmd(
            ["az", "role", "assignment", "create",
             "--assignee", identity["principalId"],
             "--role", SMB_ROLE,
             "--scope", storage_account_id],
            error_msg=f"Error assigning role '{SMB_ROLE}'",
            success_msg=f"Role '{SMB_ROLE}' assigned",
        )

    banner("✅ Infrastructure setup complete (except the manual step above)")
    return identity["clientId"]

def _generate_pvc(
    env: str,
    adl_code: str,
    namespace: str,
    project_name: str
) -> Dict[str, Any]:
    """Generate PersistentVolumeClaim manifest."""
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"azurefile-{project_name}-pvc",
            "namespace": namespace
        },
        "spec": {
            "accessModes": ["ReadWriteMany"],
            "storageClassName": f"{env}d9{adl_code}adl01-file-storageclass",
            "volumeName": f"{env}d9{adl_code}adl01-{project_name}-pv",
            "resources": {
                "requests": {
                    "storage": "100Gi"
                }
            }
        }
    }

def _generate_pv(
    env: str,
    adl_code: str,
    namespace: str,
    project_name: str,
    client_id: str
) -> Dict[str, Any]:
    """Generate PersistentVolume manifest."""
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {
            "annotations": {
                "pv.kubernetes.io/provisioned-by": "file.csi.azure.com"
            },
            "name": f"{env}d9{adl_code}adl01-{project_name}-pv",
            "namespace": namespace
        },
        "spec": {
            "capacity": {
                "storage": "100Gi"
            },
            "accessModes": ["ReadWriteMany"],
            "persistentVolumeReclaimPolicy": "Retain",
            "storageClassName": f"{env}d9{adl_code}adl01-file-storageclass",
            "csi": {
                "driver": "file.csi.azure.com",
                "volumeHandle": f"r400{env}d9{adl_code}#{env}d9{adl_code}adl01#aks",
                "volumeAttributes": {
                    "shareName": "aks",
                    "resourceGroup": f"r400{env}d9{adl_code}",
                    "storageAccount": f"{env}d9{adl_code}adl01",
                    "mountWithManagedIdentity": "true",
                    "clientID": client_id
                }
            },
            "mountOptions": [
                "dir_mode=0755",
                "file_mode=0755",
                "uid=1000",
                "gid=1000",
                "mfsymlinks",
                "cache=strict",
                "nosharesock",
                "actimeo=30",
                "nobrl"
            ]
        }
    }

def _generate_storage_class(
    env: str,
    adl_code: str,
    namespace: str,
    client_id: str
) -> Dict[str, Any]:
    """Generate StorageClass manifest."""
    return {
        "apiVersion": "storage.k8s.io/v1",
        "kind": "StorageClass",
        "metadata": {
            "name": f"{env}d9{adl_code}adl01-file-storageclass",
            "namespace": namespace
        },
        "provisioner": "file.csi.azure.com",
        "parameters": {
            "resourceGroup": f"r400{env}d9{adl_code}",
            "storageAccount": f"{env}d9{adl_code}adl01",
            "mountWithManagedIdentity": "true",
            "clientID": client_id
        },
        "reclaimPolicy": "Retain",
        "volumeBindingMode": "Immediate",
        "allowVolumeExpansion": True,
        "mountOptions": [
            "dir_mode=0755",
            "file_mode=0755",
            "mfsymlinks",
            "cache=strict",
            "nosharesock",
            "actimeo=30",
            "nobrl"
        ]
    }

def _write_manifests(
    manifests: List[Dict[str, Any]],
    output: Optional[str],
    namespace: str,
    project_name: str,
    env: str,
    adl_code: str
) -> None:
    """Write manifests to file or stdout."""
    write_output(
        dump_manifests(manifests),
        output,
        f"{project_name}-{env}-{adl_code}-azurefile-csi.yaml",
        "Azure File CSI manifests",
    )
