"""Azure File CSI Driver commands."""

from typing import Dict, Any


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
