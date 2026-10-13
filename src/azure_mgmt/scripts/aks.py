"""AKS-specific commands - Key Vault and Azure File CSI Driver."""

import click
import subprocess
import json
import yaml
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

# Import helper functions from aksAkv
from .aksAkv import (
    _ensure_akv_addon_enabled,
    _fetch_identity_id,
    _ensure_keyvault_access,
    _collect_keyvault_configs,
    _generate_all_spcs,
    _generate_secret_keeper_pod,
    _write_manifests as _write_akv_manifests,
    _backup_and_delete_existing_secrets
)
from helpers.env_config import MULTI_KV_CODE, SOCLE_KV_CODE

# Import helper functions from azureFile
from .azureFile import (
    _setup_infrastructure,
    _generate_pvc,
    _generate_pv,
    _generate_storage_class,
    _write_manifests as _write_file_manifests,
)


@click.group()
def aks():
    """AKS (Azure Kubernetes Service) management commands"""
    pass

@aks.command()
@click.argument("keyvault-name", type=str)
@click.option("--tenant-id", required=True, help="Azure tenant ID")
@click.option("--identity-id", help="User assigned managed identity ID (auto-fetched from AKS if not provided)")
@click.option("--aks-code", help="AKS project code (required if --identity-id not provided)")
@click.option("--namespace", default="default", help="Kubernetes namespace")
@click.option("--project-name", help="Project name for resource naming (defaults to namespace)")
@click.option("--output", "-o", type=click.Path(), help="Output directory or file path (defaults to stdout)")
@click.option("--pod-name", default="secret-keeper", help="Name of the secret-keeper pod")
@click.option("--include-multi-kv", is_flag=True, help=f"Include multi keyvault ({MULTI_KV_CODE}akv01)")
@click.option("--include-certificates-spc", is_flag=True, help=f"Generate TLS certificate SecretProviderClass (uses {SOCLE_KV_CODE}akv01)")
@click.option("--cert-name", help="Certificate name in Key Vault (required with --include-certificates-spc)")
@click.option("--secrets", multiple=True, help="Specific secrets to include (if not specified, all secrets will be included)")
@click.option("--secret-objects-config", type=click.Path(exists=True), help="Path to YAML/JSON file mapping namespaces to multiple secret objects (see secret-objects-config.example.yaml)")
@click.option("--separate-files", is_flag=True, help="Output SPCs and pod to separate files instead of combined YAML")
@click.option("--cert-secret-name", help="Name of the kube secret for the certificates")
def secretStore_csi_setup(
    keyvault_name: str,
    tenant_id: str,
    identity_id: Optional[str],
    aks_code: Optional[str],
    namespace: str,
    project_name: Optional[str],
    output: Optional[str],
    pod_name: str,
    include_multi_kv: bool,
    include_certificates_spc: bool,
    cert_name: Optional[str],
    secrets: tuple,
    secret_objects_config: Optional[str],
    separate_files: bool,
    cert_secret_name: Optional[str]
):
    """
    Generate all Azure Key Vault manifests: SecretProviderClass(es) and secret-keeper Pod.
    
    This command generates all Kubernetes manifests needed to mount Azure Key Vault 
    secrets in AKS using the Secrets Store CSI driver.

    Examples:
        # Single keyvault
        mycli aks generate-all name \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --output manifests.yaml

        # Multiple keyvaults with separate files
        mycli aks generate-all name \\
            --include-multi-kv \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --output ./k8s/ \\
            --separate-files
        
        # With custom project name (for long namespaces)
        mycli aks generate-all name \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace my-very-long-namespace-name \\
            --project-name cfm \\
            --output manifests.yaml
        
        # With multiple secret objects (using config file)
        mycli aks generate-all name \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --secret-objects-config ./secret-objects.yaml \\
            --output manifests.yaml

        # With TLS certificates
        mycli aks generate-all name \\
            --include-certificates-spc \\
            --cert-name my-ingress-cert \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --output manifests.yaml
    """
    try:
        # 1. Validate inputs and derive environment
        aks_env = keyvault_name[0]


        cert_secret_name = cert_secret_name if cert_secret_name else f"ingress-tls"
        
        # Use project_name for naming, default to namespace if not provided
        if not project_name:
            project_name = namespace
        
        # Load secret objects configuration if provided
        secret_objects_mapping = None
        if secret_objects_config:
            config_path = Path(secret_objects_config)
            with open(config_path, 'r') as f:
                if config_path.suffix in ['.yaml', '.yml']:
                    secret_objects_mapping = yaml.safe_load(f)
                else:
                    secret_objects_mapping = json.load(f)
            click.echo(f"Loaded secret objects configuration from: {secret_objects_config}")
        
        # 2. Validate that we have either identity_id or aks_code
        if not aks_code:
            click.echo("Error: --aks-code required", err=True)
            raise click.Abort()

        if include_multi_kv and not MULTI_KV_CODE:
            click.echo(
                "Error: MULTI_KV_CODE is not configured. Set it in .env or environment variables.",
                err=True,
            )
            raise click.Abort()

        if include_certificates_spc and not SOCLE_KV_CODE:
            click.echo(
                "Error: SOCLE_KV_CODE is not configured. Set it in .env or environment variables.",
                err=True,
            )
            raise click.Abort()
        
        # 3. Ensure Azure Key Vault Secrets Provider addon is enabled (if aks_code provided)
        if aks_code:
            _ensure_akv_addon_enabled(aks_code, aks_env)
        
        # 4. Fetch identity ID if not provided
        if not identity_id:
            identity_id = _fetch_identity_id(aks_code, aks_env)
        
        # 5. Build list of all keyvaults that need access
        keyvaults_to_access = [keyvault_name]
        cert_kv_name = None
        
        if include_multi_kv:
            keyvaults_to_access.append(f"{aks_env}d9{MULTI_KV_CODE}akv01")
        
        if include_certificates_spc:
            cert_kv_name = f"{aks_env}d9{SOCLE_KV_CODE}akv01"
            keyvaults_to_access.append(cert_kv_name)
        
        # 6. Ensure identity has access to all keyvaults
        _ensure_keyvault_access( 
            keyvault_names=keyvaults_to_access,
            identity_client_id=identity_id,
            certificate_kv_name=cert_kv_name
        )
        
        # 7. Collect keyvault configurations (fetches secrets once)
        click.echo(f"\n{'='*60}")
        click.echo("Collecting keyvault configurations...")
        click.echo(f"{'='*60}\n")
        
        keyvaults_config = _collect_keyvault_configs(
            keyvault_name=keyvault_name,
            include_multi_kv=include_multi_kv,
            aks_env=aks_env,
            namespace=namespace,
            project_name=project_name,
            secrets_filter=secrets if secrets else None,
            secret_objects_mapping=secret_objects_mapping
        )

        click.echo(f"kv confi : {keyvaults_config}")
        
        if not keyvaults_config:
            click.echo("Error: No keyvaults configured", err=True)
            raise click.Abort()
        
        # 8. Generate all SecretProviderClass manifests
        click.echo(f"\n{'='*60}")
        click.echo("Generating SecretProviderClass manifests...")
        click.echo(f"{'='*60}\n")
        
        spcs = _generate_all_spcs(
            keyvaults_config=keyvaults_config,
            identity_id=identity_id,
            tenant_id=tenant_id,
            include_certificates_spc=include_certificates_spc,
            aks_env=aks_env,
            cert_name=cert_name,
            namespace=namespace,
            project_name=project_name,
            cert_kube_secret_name=cert_secret_name
        )
        
        # 9. Generate secret-keeper deployment
        click.echo(f"\n{'='*60}")
        click.echo("Generating secret-keeper deployment...")
        click.echo(f"{'='*60}\n")
        
        pod_manifest = _generate_secret_keeper_pod(
            pod_name=pod_name,
            namespace=namespace,
            keyvaults_config=keyvaults_config,
            project_name=project_name,
            cert_kube_secret_name=cert_secret_name,
            include_certificates=include_certificates_spc,
            aks_env=aks_env
        )
        
        # 10. Backup and delete existing secrets if they exist
        # _backup_and_delete_existing_secrets(
        #     keyvaults_config=keyvaults_config,
        #     namespace=namespace,
        #     include_certificates_spc=include_certificates_spc,
        #     cert_kube_secret_name=cert_secret_name,
        #     output=output
        # )
        
        # 11. Write outputs
        click.echo(f"\n{'='*60}")
        click.echo("Writing manifests...")
        click.echo(f"{'='*60}\n")
        
        _write_akv_manifests(
            spcs=spcs,
            pod=pod_manifest,
            output=output,
            separate_files=separate_files,
            pod_name=pod_name,
            namespace=namespace,
            project_name=project_name,
            aks_env=aks_env
        )
        
        click.echo("\n✅ All manifests generated successfully!")
        click.echo("\nNext steps:")
        click.echo("  1. Review the generated manifests")
        click.echo("  2. Apply them to your cluster: kubectl apply -f <manifest-file>")
        click.echo("  3. Verify the secret-keeper deployment is running: kubectl get deployment -n " + namespace)
        
    except Exception as e:
        import subprocess
        if isinstance(e, subprocess.CalledProcessError):
            click.echo(f"Error executing Azure CLI command: {e.stderr}", err=True)
            click.echo("Make sure you're logged in with 'az login' and have appropriate permissions.", err=True)
        else:
            click.echo(f"Unexpected error: {e}", err=True)
        raise click.Abort()


@aks.command()
@click.argument("adl-code", type=str)
@click.option("--env", required=True, help="Environment code (d/t/r/p)")
@click.option("--aks-code", required=True, help="AKS project code")
@click.option("--namespace", required=True, help="Kubernetes namespace")
@click.option("--project-name", help="Project name for resource naming (defaults to namespace)")
@click.option("--client-id", help="Azure File CSI driver managed identity client ID (auto-created if not provided)")
@click.option("--setup-infrastructure", is_flag=True, help="Set up managed identity, VMSS assignment, and storage permissions")
@click.option("--pool-name", help="Name of the node pool")
@click.option("--output", "-o", type=click.Path(), help="Output file path (defaults to stdout)")
def azurefile_csi_setup(
    adl_code: str,
    env: str,
    aks_code: str,
    namespace: str,
    project_name: Optional[str],
    client_id: Optional[str],
    setup_infrastructure: bool,
    pool_name: Optional[str],
    output: Optional[str]
):
    """
    Generate Azure File CSI Driver manifests for migration from in-tree volumes.
    
    This command generates PersistentVolume, PersistentVolumeClaim, and StorageClass
    manifests needed to migrate from in-tree Azure File volumes to the CSI driver.
    
    With --setup-infrastructure, the command will:
    - Create or verify the azurefilecsi-{env}d9{aks-code}aks01 managed identity in the node RG
    - Assign it to the AKS VMSS with tag 'aks-managed-poolName' matching the pool name
    - Display the command to assign 'Storage File Data SMB Share Elevated Contributor' role (requires elevated permissions)
    - Display instructions to manually enable 'Managed Identity for SMB' in Azure Portal

    Examples:
        # Set up infrastructure and generate manifests
        mycli aks generate-csi-manifests code \\
            --env env \\
            --aks-code code \\
            --namespace cfm \\
            --pool-name cfmpool \\
            --setup-infrastructure \\
            --output ./k8s/
            
        # Generate manifests with custom project name (for long namespaces)
        mycli aks generate-csi-manifests code \\
            --env env \\
            --aks-code code \\
            --namespace my-very-long-namespace-name \\
            --project-name cfm \\
            --client-id <azure-file-csi-umi-client-id> \\
            --output ./k8s/
            
        # Generate manifests with existing identity
        mycli aks generate-csi-manifests code \\
            --env env \\
            --aks-code code \\
            --namespace cfm \\
            --client-id <azure-file-csi-umi-client-id> \\
            --output ./k8s/
    """
    import subprocess

    # Use project_name for naming, default to namespace if not provided
    if not project_name:
        project_name = namespace
    
    if not pool_name:
        pool_name = f"{namespace}pool"

    try:
        # 1. Set up infrastructure if requested or get client ID
        if setup_infrastructure:
            client_id = _setup_infrastructure(env, aks_code, adl_code, pool_name)
        elif not client_id:
            click.echo("Error: --client-id is required when --setup-infrastructure is not used", err=True)
            raise click.Abort()
        
        # 2. Generate manifests
        click.echo(f"\n{'='*60}")
        click.echo("Generating Azure File CSI Driver manifests...")
        click.echo(f"{'='*60}\n")
        
        click.echo(f"Environment: {env}")
        click.echo(f"ADL Code: {adl_code}")
        click.echo(f"Namespace: {namespace}")
        click.echo(f"Project Name: {project_name}")
        click.echo(f"Storage Account: {env}d9{adl_code}adl01")
        click.echo(f"Resource Group: r400{env}d9{adl_code}")
        click.echo(f"Client ID: {client_id}\n")
        
        manifests = [
            _generate_pvc(env, adl_code, namespace, project_name),
            _generate_pv(env, adl_code, namespace, project_name, client_id),
            _generate_storage_class(env, adl_code, namespace, client_id)
        ]
        
        click.echo("✅ Generated PersistentVolumeClaim")
        click.echo("✅ Generated PersistentVolume")
        click.echo("✅ Generated StorageClass\n")
        
        # 3. Write outputs
        click.echo(f"{'='*60}")
        click.echo("Writing manifests...")
        click.echo(f"{'='*60}\n")
        
        _write_file_manifests(
            manifests=manifests,
            output=output,
            namespace=namespace,
            project_name=project_name,
            env=env,
            adl_code=adl_code
        )
        
    except subprocess.CalledProcessError as e:
        click.echo(f"Error executing Azure CLI command: {e.stderr}", err=True)
        click.echo("Make sure you're logged in with 'az login' and have appropriate permissions.", err=True)
        raise click.Abort()
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        raise click.Abort()


@aks.command()
@click.argument("name-suffix", type=str)
def kill_agent(name_suffix: str):
    """
    Delete CICD agent pods in the cicd-agents context by name suffix.
    
    This command switches to the cicd-agents kubectl context, finds pods matching
    the specified name suffix, and deletes them.
    
    Examples:
        # Delete agent pod with specific suffix
        mycli aks kill-agent my-agent-123
    """
    import re
    import subprocess
    
    try:
        command = "kubectl config use-context cicd-agents"
        
        click.echo(f"Switching to cicd-agents context and deleting pods matching suffix :'{name_suffix}'...")
        
        # Execute the command using shell
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True
        )
        
        click.echo("Retrieving agent name")
        pods_result = subprocess.run(
            ["kubectl", "get", "pods", "-o", "name"],
            capture_output=True,
            text=True
        )

        if pods_result.returncode != 0:
            click.echo(f"Error: {pods_result.stderr}", err=True)
            raise click.Abort()

        all_pods = [line.strip() for line in pods_result.stdout.splitlines() if line.strip()]
        pattern = re.compile(rf"^pod/azdevopswin-pipeline-d9.*{re.escape(name_suffix)}$")
        matching_agents = [pod for pod in all_pods if pattern.match(pod)]

        if not matching_agents:
            click.echo(f"No agent with suffix {name_suffix} found")
            return

        if len(matching_agents) > 1:
            click.echo(
                f"Error: Multiple agents match suffix '{name_suffix}'. Please use a more specific suffix:",
                err=True,
            )
            for pod_name in matching_agents:
                click.echo(f"  - {pod_name}", err=True)
            raise click.Abort()

        agent_name = matching_agents[0]
        click.echo(f"Matched agent: {agent_name}")

        delete_result = subprocess.run(
            ["kubectl", "delete", agent_name],
            capture_output=True,
            text=True
        )

        if delete_result.stdout:
            click.echo(delete_result.stdout)

        if delete_result.returncode != 0:
            click.echo(f"Error: {delete_result.stderr}", err=True)
            raise click.Abort()

        click.echo("✅ Pods deleted successfully!")


 

        
    except subprocess.CalledProcessError as e:
        click.echo(f"Error executing kubectl command: {e.stderr}", err=True)
        raise click.Abort()
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        raise click.Abort()
