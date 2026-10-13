"""Azure File CSI Driver commands."""

import click
import yaml
import subprocess
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from helpers.env_config import AKVAKS_SETUP_CICD_SPN_SECRET

@click.group()
def azureFile():
    """Azure File CSI Driver management commands"""
    pass

def _cicd_spn_logout():

    click.echo("Logging out as cicd spn")
    cmd = ["az", "logout"]
    subprocess.run(cmd)


def _cicd_spn_login():

    click.echo("Logging in as cicd spn for elevated permissions")
    tenant_id = "ebaeb74c-eeae-40f8-b755-10b0e2bf7528"
    client_id = "0cc19a4c-bf76-4723-a014-f1111f501ce1"
    secret = AKVAKS_SETUP_CICD_SPN_SECRET

    cmd = ["az", "login", "--service-principal", "--username", client_id, "--tenant", tenant_id, "-p", secret]
    subprocess.run(cmd)

def _check_managed_identity_exists(identity_name: str, resource_group: str) -> Optional[str]:
    """Check if a managed identity exists and return its client ID."""
    result = subprocess.run(
        ["az", "identity", "show",
         "--name", identity_name,
         "--resource-group", resource_group,
         "--query", "clientId",
         "-o", "tsv"],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _create_managed_identity(identity_name: str, resource_group: str) -> str:
    """Create a managed identity and return its client ID."""
    click.echo(f"  Creating managed identity: {identity_name}...")
    
    result = subprocess.run(
        ["az", "identity", "create",
         "--name", identity_name,
         "--resource-group", resource_group,
         "--query", "clientId",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    
    client_id = result.stdout.strip()
    click.echo(f"  ✅ Managed identity created with client ID: {client_id}")
    return client_id


def _get_aks_node_resource_group(aks_name: str, aks_resource_group: str) -> str:
    """Get the AKS managed/node resource group."""
    result = subprocess.run(
        ["az", "aks", "show",
         "--name", aks_name,
         "--resource-group", aks_resource_group,
         "--query", "nodeResourceGroup",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    
    return result.stdout.strip()


def _get_vmss_names_by_tag(node_resource_group: str, pool_name: str ) -> List[str]:
    """Get all VMSS names that have the specified tag from the AKS node resource group."""
    # Get all VMSS with their resource IDs
    result = subprocess.run(
        ["az", "vmss", "list",
         "--resource-group", node_resource_group,
         "--query", "[].{name:name, id:id}",
         "-o", "json"],
        capture_output=True,
        text=True,
        check=True
    )
    
    vmss_list = json.loads(result.stdout)
    tag_key = "aks-managed-poolName"
    vmss_names = []

    # Check tags for each VMSS
    for vmss in vmss_list:
        vmss_name = vmss["name"]
        vmss_id = vmss["id"]
        
        # Get tags for this VMSS using az tag list
        tag_result = subprocess.run(
            ["az", "tag", "list",
             "--resource-id", vmss_id,
             "--query", "properties.tags",
             "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        
        tags = json.loads(tag_result.stdout) if tag_result.stdout.strip() else {}

        # Check if VMSS has the aks-managed-poolname tag matching the pool name
        if tags and tag_key in tags and tags[tag_key] == pool_name:
            vmss_names.append(vmss_name)
            click.echo(f"  Found VMSS: {vmss_name} (pool: {tags[tag_key]})")
    
    if not vmss_names:
        click.echo(f"  Warning: No VMSS found with tag '{tag_key}={pool_name}'", err=True)
    
    return vmss_names


def _get_identity_resource_id(identity_name: str, resource_group: str) -> str:
    """Get the resource ID of a managed identity."""
    result = subprocess.run(
        ["az", "identity", "show",
         "--name", identity_name,
         "--resource-group", resource_group,
         "--query", "id",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    
    return result.stdout.strip()


def _assign_identity_to_vmss_list(identity_resource_id: str, vmss_names: List[str], node_resource_group: str) -> None:
    """Assign a managed identity to multiple VMSS."""
    if not vmss_names:
        click.echo("  ⚠️  No VMSS to assign identity to", err=True)
        return
    
    for vmss_name in vmss_names:
        click.echo(f"  Assigning identity to VMSS: {vmss_name}...")
        
        subprocess.run(
            ["az", "vmss", "identity", "assign",
             "--identities", identity_resource_id,
             "--name", vmss_name,
             "--resource-group", node_resource_group],
            capture_output=True,
            text=True,
            check=True
        )
        
        click.echo(f"  ✅ Identity assigned to VMSS: {vmss_name}")


def _check_role_assignment(principal_id: str, scope: str, role: str) -> bool:
    """Check if a role assignment exists."""
    result = subprocess.run(
        ["az", "role", "assignment", "list",
         "--assignee", principal_id,
         "--scope", scope,
         "--role", role,
         "--query", "[].id",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    
    return bool(result.stdout.strip())


def _assign_role(principal_id: str, scope: str, role: str) -> None:
    """Assign a role to a principal on a scope."""
    click.echo(f"  Assigning role '{role}'...")
    
    subprocess.run(
        ["az", "role", "assignment", "create",
         "--assignee", principal_id,
         "--role", role,
         "--scope", scope],
        capture_output=True,
        text=True,
        check=True
    )
    
    click.echo(f"  ✅ Role assigned")


def _get_identity_principal_id(identity_name: str, resource_group: str) -> str:
    """Get the principal ID of a managed identity."""
    result = subprocess.run(
        ["az", "identity", "show",
         "--name", identity_name,
         "--resource-group", resource_group,
         "--query", "principalId",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    
    return result.stdout.strip()


def _get_storage_account_id(storage_account: str, resource_group: str) -> str:
    """Get the resource ID of a storage account."""
    result = subprocess.run(
        ["az", "storage", "account", "show",
         "--name", storage_account,
         "--resource-group", resource_group,
         "--query", "id",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    
    return result.stdout.strip()


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
    click.echo(f"\n{'='*60}")
    click.echo("Setting up Azure File CSI infrastructure...")
    click.echo(f"{'='*60}\n")
    
    # Resource names
    identity_name = f"azurefilecsi-{env}d9{aks_code}aks01"
    aks_name = f"{env}d9{aks_code}aks01"
    aks_resource_group = f"r400{env}d9{aks_code}"
    storage_account = f"{env}d9{adl_code}adl01"
    storage_resource_group = f"r400{env}d9{adl_code}"
    
    # 1. Get AKS node resource group (managed RG where identity should be)
    click.echo(f"Getting AKS node resource group...")
    node_resource_group = _get_aks_node_resource_group(aks_name, aks_resource_group)
    click.echo(f"  Node resource group: {node_resource_group}")
    
    # 2. Check or create managed identity in the node resource group
    click.echo(f"\nChecking managed identity: {identity_name}...")
    client_id = _check_managed_identity_exists(identity_name, node_resource_group)
    
    if client_id:
        click.echo(f"  ✅ Managed identity exists with client ID: {client_id}")
    else:
        client_id = _create_managed_identity(identity_name, node_resource_group)
    
    # 3. Get VMSS from pool
    click.echo(f"\nGetting VMSS from pool {pool_name} ...")
    vmss_names = _get_vmss_names_by_tag(node_resource_group, pool_name=pool_name)
    
    # 4. Assign identity to node pool VMSS
    click.echo(f"\nAssigning identity to node pool VMSS...")
    identity_resource_id = _get_identity_resource_id(identity_name, node_resource_group)
    _assign_identity_to_vmss_list(identity_resource_id, vmss_names, node_resource_group)
    
    # 5. Manual step: Enable Managed Identity for SMB
    click.echo(f"\n{'='*60}")
    click.echo("⚠️  MANUAL STEP REQUIRED")
    click.echo(f"{'='*60}")
    click.echo(f"\nPlease enable 'Managed Identity for SMB' on the storage account:")
    click.echo(f"  Storage Account: {storage_account}")
    click.echo(f"  Resource Group: {storage_resource_group}")
    click.echo(f"\nSteps:")
    click.echo(f"  1. Go to Azure Portal > Storage Account > {storage_account}")
    click.echo(f"  2. Navigate to Configuration")
    click.echo(f"  3. Enable 'Managed Identity for SMB'")
    click.echo(f"  4. Save changes")
    
    # 6. Assign RBAC role to storage account (requires elevated permissions)
    click.echo(f"\n{'='*60}")
    click.echo("Assigning RBAC role to storage account...")
    click.echo(f"{'='*60}\n")
    
    principal_id = _get_identity_principal_id(identity_name, node_resource_group)
    storage_account_id = _get_storage_account_id(storage_account, storage_resource_group)
    role = "Storage File Data SMB MI Admin"
    
    # Login with CICD SPN for elevated permissions
    _cicd_spn_login()
    
    try:
        _assign_role(principal_id, storage_account_id, role)
    finally:
        # Always logout, even if role assignment fails
        _cicd_spn_logout()
    
    click.echo(f"\n{'='*60}")
    click.echo("✅ Infrastructure setup complete (except manual step 5 above)")
    click.echo(f"{'='*60}\n")
    
    return client_id


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
    yaml_output = "---\n".join([
        yaml.dump(manifest, default_flow_style=False, sort_keys=False, width=4096, indent=2)
        for manifest in manifests
    ])
    
    if output:
        output_path = Path(output).expanduser()
        if output_path.is_dir() or not output_path.suffix:
            output_path = output_path / f"{project_name}-{env}-{adl_code}-azurefile-csi.yaml"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_output)
        click.echo(f"✅ Azure File CSI manifests written to: {output_path}")
    else:
        click.echo("\n" + yaml_output)

