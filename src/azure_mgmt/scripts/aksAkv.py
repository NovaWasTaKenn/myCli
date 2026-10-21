"""Azure Key Vault Secrets Store CSI driver helpers for AKS."""

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
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from .utils import format_objects_array
from helpers.config import get_config
from helpers.shell import run_cmd
from helpers.ui import banner

MULTI_KV_CODE = get_config("multi_kv_code")
SOCLE_KV_CODE = get_config("socle_kv_code")
DOCKER_CONFIG_SECRET = "docker-config-json"
CSI_DRIVER = "secrets-store.csi.k8s.io"


def _env_var_name(secret: str) -> str:
    return secret.upper().replace("-", "_")


def _secret_env_var(name: str, secret_name: str, key: str) -> Dict[str, Any]:
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": key}}}


def _csi_volume(name: str, spc_name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "csi": {"driver": CSI_DRIVER, "readOnly": True, "volumeAttributes": {"secretProviderClass": spc_name}},
    }


def _spc(
    spc_name: str,
    keyvault_name: str,
    namespace: str,
    tenant_id: str,
    identity_id: str,
    secret_objects: List[Dict[str, Any]],
    objects: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a SecretProviderClass manifest using the AKS VM managed identity."""
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
                "objects": format_objects_array(objects),
                "tenantId": tenant_id,
                "usePodIdentity": "false",
                "useVMManagedIdentity": "true",
                "userAssignedIdentityID": identity_id
            }
        }
    }


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
    regular_secrets = [s for s in secrets if s != DOCKER_CONFIG_SECRET]

    # Custom config: one Opaque secret per configured object; default: a single one with all secrets
    groups = (
        [(obj.get("secret_name"), obj.get("secrets", [])) for obj in secret_objects_config]
        if secret_objects_config
        else [(secret_name, regular_secrets)]
    )
    secret_objects = []
    for name, names in groups:
        data = [{"objectName": s, "key": _env_var_name(s)} for s in names if s in regular_secrets]
        if data:
            secret_objects.append({"secretName": name, "type": "Opaque", "data": data})

    if DOCKER_CONFIG_SECRET in secrets:
        secret_objects.append({
            "secretName": f"{project_name}-secret-acr",
            "type": "kubernetes.io/dockerconfigjson",
            "data": [{"objectName": DOCKER_CONFIG_SECRET, "key": ".dockerconfigjson"}]
        })

    return _spc(
        spc_name=f"az-{keyvault_name}-spc",
        keyvault_name=keyvault_name,
        namespace=namespace,
        tenant_id=tenant_id,
        identity_id=identity_id,
        secret_objects=secret_objects,
        objects=[{"objectName": s, "objectType": "secret", "objectVersion": ""} for s in secrets],
    )


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
    keyvault_name = f"{env}d9{socle_code}akv01"
    return _spc(
        spc_name=f"az-{keyvault_name}-spc",
        keyvault_name=keyvault_name,
        namespace=namespace,
        tenant_id=tenant_id,
        identity_id=identity_id,
        secret_objects=[{
            "secretName": kube_secret_name,
            "type": "kubernetes.io/tls",
            "data": [
                {"objectName": f"{cert_name}_Certificate", "key": "tls.crt"},
                {"objectName": f"{cert_name}_PrivateKey", "key": "tls.key"},
            ]
        }],
        objects=[
            {"objectName": cert_name, "objectAlias": f"{cert_name}_Certificate", "objectType": "cert", "objectVersion": ""},
            {"objectName": cert_name, "objectAlias": f"{cert_name}_PrivateKey", "objectType": "secret", "objectVersion": ""},
        ],
    )


def _generate_secret_keeper_pod(
    keyvaults_config: Dict[str, Dict[str, Any]],
    namespace: str,
    pod_name: str,
    project_name: str,
    cert_kube_secret_name: str,
    include_certificates: bool = False,
    aks_env: Optional[str] = None,
    socle_code: Optional[str] = SOCLE_KV_CODE,
) -> Dict[str, Any]:
    """
    Generate a secret-keeper Deployment that mounts all SPCs.

    The deployment creates a pod that references one key from each kube secret to trigger
    the Secrets Store CSI driver to create the Kubernetes secrets.
    """
    env_vars = []
    volume_mounts = []
    volumes = []

    for kv_name, config in keyvaults_config.items():
        # One env var per kube secret, referencing its first key
        if config.get("secret_objects"):
            for obj_config in config["secret_objects"]:
                obj_secrets = obj_config.get("secrets", [])
                if obj_secrets:
                    key = _env_var_name(obj_secrets[0])
                    env_vars.append(_secret_env_var(key, obj_config.get("secret_name"), key))
        else:
            regular_secrets = [s for s in config["secrets"] if s != DOCKER_CONFIG_SECRET]
            if regular_secrets:
                key = _env_var_name(regular_secrets[0])
                env_vars.append(_secret_env_var(key, config["secret_name"], key))

        if DOCKER_CONFIG_SECRET in config["secrets"]:
            env_vars.append(_secret_env_var("DOCKER_CONFIG_JSON", f"{project_name}-secret-acr", ".dockerconfigjson"))

        volume_name = f"{kv_name}-vol"
        volume_mounts.append({"name": volume_name, "mountPath": config["mount_path"], "readOnly": True})
        volumes.append(_csi_volume(volume_name, config["spc_name"]))

    if include_certificates and aks_env:
        env_vars.append(_secret_env_var("TLS_CERT", cert_kube_secret_name, "tls.crt"))
        volume_mounts.append({"name": "certs-vol", "mountPath": "/mnt/certs", "readOnly": True})
        volumes.append(_csi_volume("certs-vol", f"az-{aks_env}d9{socle_code}akv01-spc"))

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


def _backup_and_delete_existing_secrets(
    keyvaults_config: Dict[str, Dict[str, Any]],
    namespace: str,
    include_certificates_spc: bool,
    cert_kube_secret_name: str,
    output: Optional[str]
) -> None:
    """
    Back up then delete the kube secrets the CSI driver will (re)create, if they already exist.

    Backups are written to a `secret-backups` directory next to output (or in the cwd).
    """
    secret_names = []
    for config in keyvaults_config.values():
        if config.get("secret_objects"):
            secret_names += [obj.get("secret_name") for obj in config["secret_objects"]]
        else:
            secret_names.append(config["secret_name"])
    if include_certificates_spc:
        secret_names.append(cert_kube_secret_name)

    banner("Checking for existing secrets...")

    if output:
        output_path = Path(output)
        backup_dir = (output_path if output_path.is_dir() else output_path.parent) / "secret-backups"
    else:
        backup_dir = Path.cwd() / "secret-backups"

    for secret_name in secret_names:
        existing = run_cmd(["kubectl", "get", "secret", secret_name, "-n", namespace, "-o", "json"], check=False)
        if existing.returncode != 0:
            click.echo(f"✓ No existing secret found: {secret_name}")
            continue

        click.echo(f"⚠️  Found existing secret: {secret_name}")
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{secret_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"
        backup_path.write_text(yaml.dump(json.loads(existing.stdout), default_flow_style=False))
        click.echo(f"  ✓ Backed up to: {backup_path}")

        deleted = run_cmd(["kubectl", "delete", "secret", secret_name, "-n", namespace], check=False)
        if deleted.returncode == 0:
            click.echo(f"  ✓ Deleted existing secret: {secret_name}")
        else:
            click.echo(f"  ⚠️  Warning: Could not delete secret: {deleted.stderr}", err=True)
