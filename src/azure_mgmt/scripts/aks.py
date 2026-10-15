"""AKS-specific commands - Key Vault and Azure File CSI Driver."""

import click
import json
import re
import yaml
from pathlib import Path
from typing import Optional

from .aksAkv import (
    MULTI_KV_CODE,
    SOCLE_KV_CODE,
    _ensure_akv_addon_enabled,
    _fetch_identity_id,
    _ensure_keyvault_access,
    _collect_keyvault_configs,
    _generate_all_spcs,
    _generate_secret_keeper_pod,
    _write_manifests as _write_akv_manifests,
)
from .azureFile import (
    _setup_infrastructure,
    _generate_pvc,
    _generate_pv,
    _generate_storage_class,
    _write_manifests as _write_file_manifests,
)
from helpers.shell import run_cmd, run_text
from helpers.ui import banner


@click.group()
def aks():
    """AKS (Azure Kubernetes Service) management commands"""
    pass


def _load_secret_objects_config(path: Optional[str]):
    if not path:
        return None
    config_path = Path(path)
    with open(config_path, 'r') as f:
        mapping = yaml.safe_load(f) if config_path.suffix in ['.yaml', '.yml'] else json.load(f)
    click.echo(f"Loaded secret objects configuration from: {path}")
    return mapping


@aks.command()
@click.argument("keyvault-name", type=str)
@click.option("--tenant-id", required=True, help="Azure tenant ID")
@click.option("--identity-id", help="User assigned managed identity ID (auto-fetched from AKS if not provided)")
@click.option("--aks-code", required=True, help="AKS project code")
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
@click.option("--cert-secret-name", default="ingress-tls", show_default=True, help="Name of the kube secret for the certificates")
def secretStore_csi_setup(
    keyvault_name: str,
    tenant_id: str,
    identity_id: Optional[str],
    aks_code: str,
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
    cert_secret_name: str
):
    """
    Generate all Azure Key Vault manifests: SecretProviderClass(es) and secret-keeper Pod.

    This command generates all Kubernetes manifests needed to mount Azure Key Vault
    secrets in AKS using the Secrets Store CSI driver.

    Examples:
        # Single keyvault
        mycli aks secretstore-csi-setup name \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --output manifests.yaml

        # Multiple keyvaults with separate files
        mycli aks secretstore-csi-setup name \\
            --include-multi-kv \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --output ./k8s/ \\
            --separate-files

        # With custom project name (for long namespaces)
        mycli aks secretstore-csi-setup name \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace my-very-long-namespace-name \\
            --project-name cfm \\
            --output manifests.yaml

        # With multiple secret objects (using config file)
        mycli aks secretstore-csi-setup name \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --secret-objects-config ./secret-objects.yaml \\
            --output manifests.yaml

        # With TLS certificates
        mycli aks secretstore-csi-setup name \\
            --include-certificates-spc \\
            --cert-name my-ingress-cert \\
            --tenant-id <tenant-id> \\
            --aks-code code \\
            --namespace cfm \\
            --output manifests.yaml
    """
    # 1. Validate inputs before touching Azure
    if include_multi_kv and not MULTI_KV_CODE:
        raise click.UsageError("multi_kv_code is not configured. Set it with `mycli configure multi_kv_code=<value>`.")
    if include_certificates_spc and not SOCLE_KV_CODE:
        raise click.UsageError("socle_kv_code is not configured. Set it with `mycli configure socle_kv_code=<value>`.")
    if include_certificates_spc and not cert_name:
        raise click.UsageError("--cert-name required with --include-certificates-spc")
    if separate_files and not output:
        raise click.UsageError("--output directory required with --separate-files")

    aks_env = keyvault_name[0]
    project_name = project_name or namespace
    secret_objects_mapping = _load_secret_objects_config(secret_objects_config)

    # 2. AKS addon and its managed identity
    _ensure_akv_addon_enabled(aks_code, aks_env)
    identity_id = identity_id or _fetch_identity_id(aks_code, aks_env)

    # 3. Grant the identity access to every keyvault it will read
    keyvaults_to_access = [keyvault_name]
    if include_multi_kv:
        keyvaults_to_access.append(f"{aks_env}d9{MULTI_KV_CODE}akv01")
    cert_kv_name = f"{aks_env}d9{SOCLE_KV_CODE}akv01" if include_certificates_spc else None
    if cert_kv_name:
        keyvaults_to_access.append(cert_kv_name)
    _ensure_keyvault_access(keyvaults_to_access, identity_id, certificate_kv_name=cert_kv_name)

    # 4. Collect the secrets of each keyvault
    banner("Collecting keyvault configurations...")
    keyvaults_config = _collect_keyvault_configs(
        keyvault_name=keyvault_name,
        include_multi_kv=include_multi_kv,
        aks_env=aks_env,
        namespace=namespace,
        project_name=project_name,
        secrets_filter=secrets or None,
        secret_objects_mapping=secret_objects_mapping
    )
    if not keyvaults_config:
        raise click.ClickException("No keyvaults configured")

    # 5. Generate the manifests
    banner("Generating SecretProviderClass manifests...")
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

    banner("Generating secret-keeper deployment...")
    pod_manifest = _generate_secret_keeper_pod(
        pod_name=pod_name,
        namespace=namespace,
        keyvaults_config=keyvaults_config,
        project_name=project_name,
        cert_kube_secret_name=cert_secret_name,
        include_certificates=include_certificates_spc,
        aks_env=aks_env
    )

    # 6. Write outputs
    banner("Writing manifests...")
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
    click.echo(f"  3. Verify the secret-keeper deployment is running: kubectl get deployment -n {namespace}")


@aks.command()
@click.argument("adl-code", type=str)
@click.option("--env", required=True, help="Environment code (d/t/r/p)")
@click.option("--aks-code", required=True, help="AKS project code")
@click.option("--namespace", required=True, help="Kubernetes namespace")
@click.option("--project-name", help="Project name for resource naming (defaults to namespace)")
@click.option("--client-id", help="Azure File CSI driver managed identity client ID (auto-created if not provided)")
@click.option("--setup-infrastructure", is_flag=True, help="Set up managed identity, VMSS assignment, and storage permissions")
@click.option("--pool-name", help="Name of the node pool (defaults to {namespace}pool)")
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
    - Assign the 'Storage File Data SMB MI Admin' role on the storage account (elevated as the CICD SPN)
    - Display instructions to manually enable 'Managed Identity for SMB' in Azure Portal

    Examples:
        # Set up infrastructure and generate manifests
        mycli aks azurefile-csi-setup code \\
            --env env \\
            --aks-code code \\
            --namespace cfm \\
            --pool-name cfmpool \\
            --setup-infrastructure \\
            --output ./k8s/

        # Generate manifests with existing identity and custom project name
        mycli aks azurefile-csi-setup code \\
            --env env \\
            --aks-code code \\
            --namespace my-very-long-namespace-name \\
            --project-name cfm \\
            --client-id <azure-file-csi-umi-client-id> \\
            --output ./k8s/
    """
    if not setup_infrastructure and not client_id:
        raise click.UsageError("--client-id is required when --setup-infrastructure is not used")

    project_name = project_name or namespace
    pool_name = pool_name or f"{namespace}pool"

    # 1. Infrastructure (managed identity, VMSS, RBAC)
    if setup_infrastructure:
        client_id = _setup_infrastructure(env, aks_code, adl_code, pool_name)

    # 2. Manifests
    banner("Generating Azure File CSI Driver manifests...")
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
    click.echo("✅ Generated PersistentVolumeClaim, PersistentVolume and StorageClass")

    # 3. Write outputs
    banner("Writing manifests...")
    _write_file_manifests(
        manifests=manifests,
        output=output,
        namespace=namespace,
        project_name=project_name,
        env=env,
        adl_code=adl_code
    )


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
    click.echo(f"Switching to cicd-agents context and deleting pods matching suffix: '{name_suffix}'...")
    run_cmd(
        ["kubectl", "config", "use-context", "cicd-agents"],
        error_msg="Error switching to cicd-agents context",
    )

    click.echo("Retrieving agent name")
    pods = run_text(["kubectl", "get", "pods", "-o", "name"], error_msg="Error listing pods").splitlines()
    pattern = re.compile(rf"^pod/azdevopswin-pipeline-d9.*{re.escape(name_suffix)}$")
    matching_agents = [pod.strip() for pod in pods if pattern.match(pod.strip())]

    if not matching_agents:
        click.echo(f"No agent with suffix {name_suffix} found")
        return
    if len(matching_agents) > 1:
        raise click.ClickException(
            f"Multiple agents match suffix '{name_suffix}'. Please use a more specific suffix:\n"
            + "\n".join(f"  - {pod}" for pod in matching_agents)
        )

    agent_name = matching_agents[0]
    click.echo(f"Matched agent: {agent_name}")
    deleted = run_text(["kubectl", "delete", agent_name], error_msg="Error deleting pod")
    if deleted:
        click.echo(deleted)
    click.echo("✅ Pods deleted successfully!")
