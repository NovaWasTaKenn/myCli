"""Azure Key Vault commands."""

"""
TODO:

aks akv : 
- Install csi driver
- Give permissions (acl) to csi driver umi
- Generate spc
- Generate secret-keeper pod
- Deploy
- Modify application pod
- Test

azurefile csi driver :
- Create umi
- Give Storage File Data SMB MI Admin
- Enable smb oauth / Managed Identity for SMB
- Generate pv/pvc/storageClass
- Deploy
- Modify application pod
- Test


"""

import click
import subprocess
import json
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import os

from .utils import format_objects_array
from helpers.env_config import MULTI_KV_CODE, SOCLE_KV_CODE, AKVAKS_SETUP_CICD_SPN_SECRET
from datetime import datetime


@click.group()
def aksAkv():
    """Azure Key Vault management commands"""
    pass


def _ensure_akv_addon_enabled(aks_code: str, aks_env: str) -> None:
    """Ensure the Azure Key Vault Secrets Provider addon is enabled on the AKS cluster."""
    resource_group = f"r400{aks_env}d9{aks_code}"
    cluster_name = f"{aks_env}d9{aks_code}aks01"
    
    click.echo(f"\n{'='*60}")
    click.echo("Checking Azure Key Vault Secrets Provider addon...")
    click.echo(f"{'='*60}\n")
    
    # Check if addon is already enabled
    result = subprocess.run(
        ["az", "aks", "show", 
         "--resource-group", resource_group,
         "--name", cluster_name,
         "--query", "addonProfiles.azureKeyvaultSecretsProvider.enabled",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    
    is_enabled = result.stdout.strip().lower() == "true"
    
    if is_enabled:
        click.echo(f"  ✅ Azure Key Vault Secrets Provider addon is already enabled on {cluster_name}")
    else:
        click.echo(f"  ⚠️  Azure Key Vault Secrets Provider addon is not enabled")
        click.echo(f"  Enabling addon on {cluster_name}...")
        
        subprocess.run(
            ["az", "aks", "enable-addons",
             "--addons", "azure-keyvault-secrets-provider",
             "--name", cluster_name,
             "--resource-group", resource_group],
            capture_output=True,
            text=True,
            check=True
        )
        
        click.echo(f"  ✅ Azure Key Vault Secrets Provider addon enabled successfully")


def _fetch_identity_id(aks_code: str, aks_env: str) -> str:
    """Fetch the managed identity ID from an AKS cluster."""
    resource_group = f"r400{aks_env}d9{aks_code}"
    kube_cluster = f"{aks_env}d9{aks_code}aks01"
    
    click.echo(f"Fetching identity ID from AKS cluster: {kube_cluster}...")
    result = subprocess.run(
        ["az", "aks", "show", 
         "--resource-group", resource_group,
         "--name", kube_cluster,
         "--query", "addonProfiles.azureKeyvaultSecretsProvider.identity.clientId",
         "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    identity_id = result.stdout.strip()
    
    if not identity_id:
        click.echo("Error: Could not fetch identity ID from AKS cluster. Make sure the Azure Key Vault Secrets Provider addon is enabled.", err=True)
        raise click.Abort()
    
    click.echo(f"Using identity ID: {identity_id}")
    return identity_id


def _get_identity_object_id(client_id: str) -> str:
    """Get the object ID of a managed identity from its client ID."""
    result = subprocess.run(
        ["az", "ad", "sp", "show", "--id", client_id, "--query", "id", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=True
    )
    object_id = result.stdout.strip()
    
    if not object_id:
        click.echo(f"Error: Could not fetch object ID for client ID {client_id}", err=True)
        raise click.Abort()
    
    return object_id


def _check_keyvault_access(keyvault_name: str, object_id: str) -> bool:
    """Check if an identity has access policies configured for a Key Vault."""
    result = subprocess.run(
        ["az", "keyvault", "show", 
         "--name", keyvault_name,
         "--query", f"properties.accessPolicies[?objectId=='{object_id}']",
         "-o", "json"],
        capture_output=True,
        text=True,
        check=True
    )
    
    access_policies = json.loads(result.stdout)
    return len(access_policies) > 0

def _grant_keyvault_access(keyvault_name: str, object_id: str, include_certificates: bool = False) -> None:
    """Grant access policy to an identity for a Key Vault."""
    click.echo(f"  Granting access to {keyvault_name}...")
    
    cmd = [
        "az", "keyvault", "set-policy",
        "--name", keyvault_name,
        "--object-id", object_id,
        "--secret-permissions", "get", "list"
    ]
    
    if include_certificates:
        cmd.extend([
            "--certificate-permissions", "get", "list",
            "--key-permissions", "get", "list"
        ])
    
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    click.echo(f"  ✅ Access granted to {keyvault_name}")


def _ensure_keyvault_access(
    keyvault_names: List[str],
    identity_client_id: str,
    certificate_kv_name: Optional[str] = None
) -> None:
    """Ensure the managed identity has access to all specified Key Vaults."""
    click.echo(f"\n{'='*60}")
    click.echo("Checking Key Vault access policies...")
    click.echo(f"{'='*60}\n")
    
    # Get object ID from client ID
    object_id = _get_identity_object_id(identity_client_id)
    click.echo(f"Identity object ID: {object_id}\n")
    
    for kv_name in keyvault_names:
        click.echo(f"Checking access to {kv_name}...")
        has_access = _check_keyvault_access(kv_name, object_id)
        
        if has_access:
            click.echo(f"  ✅ Identity already has access to {kv_name}")
        else:
            click.echo(f"  ⚠️  Identity does not have access to {kv_name}")
            # Check if this is the certificate keyvault
            is_cert_kv = certificate_kv_name is not None and kv_name == certificate_kv_name
            _grant_keyvault_access(kv_name, object_id, include_certificates=is_cert_kv)


def _fetch_keyvault_secrets(keyvault_name: str, secrets_filter: Optional[tuple] = None) -> List[str]:
    """Fetch secrets from a Key Vault, optionally filtering to specific secrets."""
    click.echo(f"Fetching secrets from Key Vault: {keyvault_name}...")
    
    result = subprocess.run(
        ["az", "keyvault", "secret", "list", "--vault-name", keyvault_name, "--query", "[].name", "-o", "json"],
        capture_output=True,
        text=True,
        check=True
    )
    
    all_secrets = json.loads(result.stdout)
    click.echo(f"  Found {len(all_secrets)} total secret(s)")
    
    if secrets_filter:
        secret_list = [s for s in all_secrets if s in secrets_filter]
        if len(secret_list) != len(secrets_filter):
            missing = set(secrets_filter) - set(secret_list)
            click.echo(f"  Warning: The following secrets were not found: {', '.join(missing)}", err=True)
    else:
        secret_list = all_secrets
    
    if not secret_list:
        click.echo(f"  No secrets found in the Key Vault: {keyvault_name}", err=True)
        return []
    
    click.echo(f"  Using {len(secret_list)} secret(s)")
    return secret_list


def _collect_keyvault_configs(
    keyvault_name: str,
    include_multi_kv: bool,
    aks_env: str,
    namespace: str,
    project_name: str,
    secrets_filter: Optional[tuple],
    secret_objects_mapping: Optional[Dict[str, List[Dict[str, Any]]]] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Collect all keyvault configurations with their secrets.
    
    Args:
        secret_objects_mapping: Optional dict mapping namespace to list of secret object configs.
            Each config has 'secret_name' and 'secrets' (list of KV secret names).
            Example: {
                "my-namespace": [
                    {"secret_name": "app-config", "secrets": ["db-pass", "api-key"]},
                    {"secret_name": "app-creds", "secrets": ["sa-key"]}
                ]
            }
    
    Returns:
        Dict mapping keyvault names to their configuration:
        {
            "kv_name": {
                "secrets": ["secret1", "secret2", ...],
                "secret_objects": [
                    {"secret_name": "name", "secrets": ["s1", "s2"]},
                    ...
                ],
                "spc_name": "az-kv-name-spc",
                "mount_path": "/mnt/kv-name-secrets"
            }
        }
    """

    click.echo(f"mapping : {secret_objects_mapping}")
    click.echo(f"namespace : {namespace}")

    keyvaults_config = {}
    
    # Add main keyvault (always provided)
    secrets = _fetch_keyvault_secrets(keyvault_name, secrets_filter)
    if secrets:
        # Check if there's a custom secret objects configuration for this namespace
        secret_objects = None
        if secret_objects_mapping and namespace in secret_objects_mapping:
            secret_objects = secret_objects_mapping[namespace]
        
        keyvaults_config[keyvault_name] = {
            "secrets": secrets,
            "secret_name": f"{project_name}-secret",
            "secret_objects": secret_objects,  # List of secret object configs or None
            "spc_name": f"az-{keyvault_name}-spc",
            "mount_path": f"/mnt/{keyvault_name}-secrets"
        }
    
    # Add multi keyvault if requested
    if include_multi_kv:
        kv_name = f"{aks_env}d9{MULTI_KV_CODE}akv01"
        secrets = _fetch_keyvault_secrets(kv_name, secrets_filter)
        if secrets:
            keyvaults_config[kv_name] = {
                "secrets": secrets,
                "secret_name": "multi-secret",
                "secret_objects": None,  # Could be extended to support config for multi-kv
                "spc_name": f"az-{kv_name}-spc",
                "mount_path": f"/mnt/{kv_name}-secrets"
            }

    click.echo(f"kv vonfig collect: {keyvaults_config}")
    
    return keyvaults_config


def _generate_spc_for_keyvault(
    keyvault_name: str,
    secrets: List[str],
    secret_name: str,
    tenant_id: str,
    identity_id: str,
    namespace: str,
    project_name: str,
    secret_objects_config: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Generate a SecretProviderClass for a specific Key Vault.
    
    Args:
        secret_objects_config: Optional list of custom secret object configurations.
            Each item should have 'secret_name' and 'secrets' keys.
    """
    spc_name = f"az-{keyvault_name}-spc"
    
    # Check if docker-config-json secret exists
    has_docker_config = "docker-config-json" in secrets
    
    # Separate docker-config-json from other secrets
    regular_secrets = [s for s in secrets if s != "docker-config-json"]
    
    # Build secretObjects data for regular secrets
    secret_objects_data = []
    for secret in regular_secrets:
        env_var_name = secret.upper().replace("-", "_")
        secret_objects_data.append({
            "objectName": secret,
            "key": env_var_name
        })
    
    # Build parameters objects array (includes all secrets)
    objects_array = []
    for secret in secrets:
        objects_array.append({
            "objectName": secret,
            "objectType": "secret",
            "objectVersion": ""
        })
    
    # Build secretObjects list
    secret_objects = []

    click.echo(f"secret objects config: {secret_objects_config}")
    
    # If custom secret objects config is provided, use it for regular secrets
    if secret_objects_config:
        for obj_config in secret_objects_config:
            obj_secret_name = obj_config.get("secret_name")
            obj_secrets = obj_config.get("secrets", [])
            
            # Build data for this secret object
            obj_data = []
            for secret in obj_secrets:
                if secret in regular_secrets:
                    env_var_name = secret.upper().replace("-", "_")
                    obj_data.append({
                        "objectName": secret,
                        "key": env_var_name
                    })

            # Only add if there's data
            if obj_data:
                secret_objects.append({
                    "secretName": obj_secret_name,
                    "type": "Opaque",
                    "data": obj_data
                })
    else:
        # Default behavior: single Opaque secret with all regular secrets
        if regular_secrets:
            secret_objects.append({
                "secretName": secret_name,
                "type": "Opaque",
                "data": secret_objects_data
            })
    
    # Add docker-config-json secret if present
    if has_docker_config:
        secret_objects.append({
            "secretName": f"{project_name}-secret-acr",
            "type": "kubernetes.io/dockerconfigjson",
            "data": [
                {
                    "objectName": "docker-config-json",
                    "key": ".dockerconfigjson"
                }
            ]
        })

    click.echo(f"secret objects : {secret_objects}")
    
    # Construct the SecretProviderClass
    return {
        "apiVersion": "secrets-store.csi.x-k8s.io/v1",
        "kind": "SecretProviderClass",
        "metadata": {
            "name": spc_name,
            "namespace": namespace
        },
        "spec": {
            "provider": "azure",
            "secretObjects": secret_objects,
            "parameters": {
                "keyvaultName": keyvault_name,
                "objects": format_objects_array(objects_array),
                "tenantId": tenant_id,
                "usePodIdentity": "false",
                "useVMManagedIdentity": "true",
                "userAssignedIdentityID": identity_id
            }
        }
    }


def _generate_spc_for_certificates(
    tenant_id: str,
    identity_id: str,
    namespace: str,
    cert_name: str,
    env: str,
    socle_code: str,
    kube_secret_name: str
) -> Dict[str, Any]:
    """Generate a SecretProviderClass for TLS certificates from Key Vault."""
    spc_name = f"az-{env}d9{socle_code}akv01-spc"
    
    
    secret_objects_data = [
        {"objectName": f"{cert_name}_Certificate", "key": "tls.crt"},
        {"objectName": f"{cert_name}_PrivateKey", "key": "tls.key"}
    ]
    
    objects_array = [
        {
            "objectName": cert_name,
            "objectAlias": f"{cert_name}_Certificate",
            "objectType": "cert",
            "objectVersion": ""
        },
        {
            "objectName": cert_name,
            "objectAlias": f"{cert_name}_PrivateKey",
            "objectType": "secret",
            "objectVersion": ""
        }
    ]
    
    return {
        "apiVersion": "secrets-store.csi.x-k8s.io/v1",
        "kind": "SecretProviderClass",
        "metadata": {
            "name": spc_name,
            "namespace": namespace
        },
        "spec": {
            "provider": "azure",
            "secretObjects": [
                {
                    "secretName": kube_secret_name,
                    "type": "kubernetes.io/tls",
                    "data": secret_objects_data
                }
            ],
            "parameters": {
                "keyvaultName": f"{env}d9{socle_code}akv01",
                "objects": format_objects_array(objects_array),
                "tenantId": tenant_id,
                "usePodIdentity": "false",
                "useVMManagedIdentity": "true",
                "userAssignedIdentityID": identity_id
            }
        }
    }


def _generate_all_spcs(
    keyvaults_config: Dict[str, Dict[str, Any]],
    tenant_id: str,
    identity_id: str,
    namespace: str,
    project_name: str,
    include_certificates_spc: bool,
    cert_name: Optional[str],
    aks_env: str,
    cert_kube_secret_name: str
) -> List[Dict[str, Any]]:
    """Generate all SecretProviderClass manifests."""
    spcs = []

    click.echo(keyvaults_config)
    
    # Generate SPCs for regular keyvaults
    for kv_name, config in keyvaults_config.items():
        click.echo(f"  Generating SPC for {kv_name}")
        spc = _generate_spc_for_keyvault(
            keyvault_name=kv_name,
            secrets=config["secrets"],
            secret_name=config["secret_name"],
            tenant_id=tenant_id,
            identity_id=identity_id,
            namespace=namespace,
            project_name=project_name,
            secret_objects_config=config.get("secret_objects")
        )
        spcs.append(spc)
    
    # Generate certificate SPC if requested
    if include_certificates_spc:
        if not cert_name:
            click.echo("Error: --cert-name required with --include-certificates-spc", err=True)
            raise click.Abort()
        click.echo(f"  Generating certificate SPC for {aks_env}d9{SOCLE_KV_CODE}akv01")
        spc = _generate_spc_for_certificates(
            tenant_id=tenant_id,
            identity_id=identity_id,
            namespace=namespace,
            cert_name=cert_name,
            env=aks_env,
            socle_code=SOCLE_KV_CODE,
            kube_secret_name=cert_kube_secret_name
        )
        spcs.append(spc)
    
    return spcs


def _generate_secret_keeper_pod(
    keyvaults_config: Dict[str, Dict[str, Any]],
    namespace: str,
    pod_name: str,
    project_name: str,
    cert_kube_secret_name: str,
    include_certificates: bool = False,
    aks_env: Optional[str] = None,
    socle_code: str = SOCLE_KV_CODE,
) -> Dict[str, Any]:
    """
    Generate a secret-keeper Deployment that mounts all SPCs.
    
    The deployment creates a pod that references one key from each keyvault's secret to trigger
    the Secrets Store CSI driver to create the Kubernetes secrets.
    """
    env_vars = []
    volume_mounts = []
    volumes = []
    
    for kv_name, config in keyvaults_config.items():
        # Check if docker-config-json exists in this keyvault
        has_docker_config = "docker-config-json" in config["secrets"]
        
        # Handle environment variables based on custom secret objects or default
        if config.get("secret_objects"):
            # Custom secret objects: add env var for first secret from first object
            for obj_config in config["secret_objects"]:
                obj_secrets = obj_config.get("secrets", [])
                if obj_secrets:
                    first_secret = obj_secrets[0]
                    env_var_name = first_secret.upper().replace("-", "_")
                    
                    env_vars.append({
                        "name": env_var_name,
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": obj_config.get("secret_name"),
                                "key": env_var_name
                            }
                        }
                    })

        else:
            # Default behavior: use the first non-docker-config-json secret
            if config["secrets"]:
                regular_secrets = [s for s in config["secrets"] if s != "docker-config-json"]
                if regular_secrets:
                    first_secret = regular_secrets[0]
                    env_var_name = first_secret.upper().replace("-", "_")
                    
                    env_vars.append({
                        "name": env_var_name,
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": config["secret_name"],
                                "key": env_var_name
                            }
                        }
                    })
        
        # Add environment variable for docker-config-json if present
        if has_docker_config:
            env_vars.append({
                "name": "DOCKER_CONFIG_JSON",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": f"{project_name}-secret-acr",
                        "key": ".dockerconfigjson"
                    }
                }
            })
        
        # Create volume mount
        volume_name = f"{kv_name}-vol"
        volume_mounts.append({
            "name": volume_name,
            "mountPath": config["mount_path"],
            "readOnly": True
        })
        
        # Create CSI volume
        volumes.append({
            "name": volume_name,
            "csi": {
                "driver": "secrets-store.csi.k8s.io",
                "readOnly": True,
                "volumeAttributes": {
                    "secretProviderClass": config["spc_name"]
                }
            }
        })
    
    click.echo(f"include certificates : {include_certificates}")
    click.echo(f"aks env : {aks_env}")

    # Add certificate volume and env var if requested
    if include_certificates and aks_env:
        cert_spc_name = f"az-{aks_env}d9{socle_code}akv01-spc"
        cert_secret_name = cert_kube_secret_name
        cert_volume_name = "certs-vol"
        
        # Add environment variable referencing certificate secret
        env_vars.append({
            "name": "TLS_CERT",
            "valueFrom": {
                "secretKeyRef": {
                    "name": cert_secret_name,
                    "key": "tls.crt"
                }
            }
        })
        
        # Add volume mount for certificates
        volume_mounts.append({
            "name": cert_volume_name,
            "mountPath": "/mnt/certs",
            "readOnly": True
        })
        
        # Add CSI volume for certificates
        volumes.append({
            "name": cert_volume_name,
            "csi": {
                "driver": "secrets-store.csi.k8s.io",
                "readOnly": True,
                "volumeAttributes": {
                    "secretProviderClass": cert_spc_name
                }
            }
        })

        click.echo(f"volumes : {volumes}")
        click.echo(f"volumes mount : {volume_mounts}")
    
    return {
        "kind": "Deployment",
        "apiVersion": "apps/v1",
        "metadata": {
            "name": pod_name,
            "namespace": namespace
        },
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": {
                    "app": pod_name
                }
            },
            "template": {
                "metadata": {
                    "labels": {
                        "app": pod_name
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "busybox",
                            "image": "registry.k8s.io/e2e-test-images/busybox:1.29-4",
                            "command": ["/bin/sleep", "10000"],
                            "env": env_vars,
                            "volumeMounts": volume_mounts
                        }
                    ],
                    "volumes": volumes
                }
            }
        }
    }


def _write_manifests(
    spcs: List[Dict[str, Any]],
    pod: Dict[str, Any],
    output: Optional[str],
    separate_files: bool,
    pod_name: str,
    namespace: str,
    project_name: str,
    aks_env: str
) -> None:
    """Write manifests to file(s) or stdout."""
    if separate_files:
        if not output:
            click.echo("Error: --output directory required with --separate-files", err=True)
            raise click.Abort()
        
        output_dir = Path(output).expanduser()
        if not output_dir.is_dir() and output_dir.suffix:
            output_dir = output_dir.parent
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Write SPCs
        spc_yaml = "---\n".join([
            yaml.dump(spc, default_flow_style=False, sort_keys=False, width=4096, indent=2)
            for spc in spcs
        ])
        spc_file = output_dir / f"{project_name}-{aks_env}-spcs.yaml"
        spc_file.write_text(spc_yaml)
        click.echo(f"✅ SecretProviderClass(es) written to: {spc_file}")
        
        # Write deployment
        pod_yaml = yaml.dump(pod, default_flow_style=False, sort_keys=False, width=4096, indent=2)
        pod_file = output_dir / f"{project_name}-{aks_env}-secret-keeper.yaml"
        pod_file.write_text(pod_yaml)
        click.echo(f"✅ Secret-keeper Deployment written to: {pod_file}")
        
    else:
        # Combined output
        all_manifests = spcs + [pod]
        yaml_output = "---\n".join([
            yaml.dump(manifest, default_flow_style=False, sort_keys=False, width=4096, indent=2)
            for manifest in all_manifests
        ])
        
        if output:
            output_path = Path(output).expanduser()
            if output_path.is_dir() or not output_path.suffix:
                output_path = output_path / f"{project_name}-{aks_env}-akv-manifests.yaml"
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(yaml_output)
            click.echo(f"✅ All manifests written to: {output_path}")
        else:
            click.echo("\n" + yaml_output)

def _backup_and_delete_existing_secrets(
    keyvaults_config: Dict[str, Dict[str, Any]],
    namespace: str,
    include_certificates_spc: bool,
    cert_kube_secret_name: str,
    output: Optional[str]
) -> None:
    """
    Check if secrets already exist in the namespace.
    If they do, backup their manifests and delete them.
    
    Args:
        keyvaults_config: Dictionary of keyvault configurations with secret_name
        namespace: Kubernetes namespace
        include_certificates_spc: Whether certificate SPC is included
        output: Output path for backup files
    """
    # Collect all secret names that will be created by the CSI driver
    secret_names = []
    
    # Add secrets from keyvault configs
    for config in keyvaults_config.values():
        # Check if custom secret objects are defined
        if config.get("secret_objects"):
            # Add each custom secret object name
            for obj_config in config["secret_objects"]:
                secret_names.append(obj_config.get("secret_name"))
        else:
            # Add the default secret name
            secret_names.append(config["secret_name"])
    
    # Add certificate secret if enabled
    if include_certificates_spc:
        secret_names.append(cert_kube_secret_name)
    
    click.echo(f"\n{'='*60}")
    click.echo("Checking for existing secrets...")
    click.echo(f"{'='*60}\n")
    
    # Determine backup directory
    if output:
        output_path = Path(output)
        if output_path.is_dir():
            backup_dir = output_path / "secret-backups"
        else:
            backup_dir = output_path.parent / "secret-backups"
    else:
        backup_dir = Path.cwd() / "secret-backups"
    
    for secret_name in secret_names:
        try:
            # Check if secret exists
            result = subprocess.run(
                ["kubectl", "get", "secret", secret_name, "-n", namespace, "-o", "json"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                # Secret exists - backup and delete it
                click.echo(f"⚠️  Found existing secret: {secret_name}")
                
                # Parse the secret manifest
                secret_manifest = json.loads(result.stdout)
                
                # Create backup directory if needed
                backup_dir.mkdir(parents=True, exist_ok=True)
                
                # Generate backup filename with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_filename = f"{secret_name}_{timestamp}.yaml"
                backup_path = backup_dir / backup_filename
                
                # Write backup
                with open(backup_path, 'w') as f:
                    yaml.dump(secret_manifest, f, default_flow_style=False)
                
                click.echo(f"  ✓ Backed up to: {backup_path}")
                
                # Delete the secret
                delete_result = subprocess.run(
                    ["kubectl", "delete", "secret", secret_name, "-n", namespace],
                    capture_output=True,
                    text=True
                )
                
                if delete_result.returncode == 0:
                    click.echo(f"  ✓ Deleted existing secret: {secret_name}")
                else:
                    click.echo(f"  ⚠️  Warning: Could not delete secret: {delete_result.stderr}", err=True)
            else:
                # Secret doesn't exist - nothing to do
                click.echo(f"✓ No existing secret found: {secret_name}")
                
        except Exception as e:
            click.echo(f"Warning: Error checking secret {secret_name}: {e}", err=True)
            # Continue with other secrets

