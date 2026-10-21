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
    _generate_spc_for_keyvault,
    _generate_spc_for_certificates,
    _generate_secret_keeper_pod,
)
from .azureFile import (
    _generate_pvc,
    _generate_pv,
    _generate_storage_class,
)
from .utils import dump_manifests, write_output
from helpers.auth import elevated_cicd_spn
from helpers.shell import run_cmd, run_json, run_text
from helpers.ui import banner

POOL_NAME_TAG = "aks-managed-poolName"
SMB_ROLE = "Storage File Data SMB MI Admin"

@click.group()
def aks():
    """AKS (Azure Kubernetes Service) management commands"""
    pass


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
    aks_resource_group = f"r400{aks_env}d9{aks_code}"
    aks_name = f"{aks_env}d9{aks_code}aks01"
    multi_kv_name = f"{aks_env}d9{MULTI_KV_CODE}akv01"
    cert_kv_name = f"{aks_env}d9{SOCLE_KV_CODE}akv01"

    secret_objects_mapping = None
    if secret_objects_config:
        config_path = Path(secret_objects_config)
        with open(config_path, 'r') as f:
            secret_objects_mapping = yaml.safe_load(f) if config_path.suffix in ['.yaml', '.yml'] else json.load(f)
        click.echo(f"Loaded secret objects configuration from: {secret_objects_config}")

    # 2. AKS Key Vault Secrets Provider addon
    banner("Checking Azure Key Vault Secrets Provider addon...")
    addon_enabled = run_text(
        ["az", "aks", "show",
         "--resource-group", aks_resource_group,
         "--name", aks_name,
         "--query", "addonProfiles.azureKeyvaultSecretsProvider.enabled",
         "-o", "tsv"],
        error_msg=f"Error checking addons on {aks_name}",
    )
    if addon_enabled.lower() == "true":
        click.echo(f"  ✅ Azure Key Vault Secrets Provider addon is already enabled on {aks_name}")
    else:
        click.echo(f"  ⚠️  Azure Key Vault Secrets Provider addon is not enabled, enabling it on {aks_name}...")
        run_cmd(
            ["az", "aks", "enable-addons",
             "--addons", "azure-keyvault-secrets-provider",
             "--name", aks_name,
             "--resource-group", aks_resource_group],
            error_msg=f"Error enabling addon on {aks_name}",
            success_msg="Azure Key Vault Secrets Provider addon enabled successfully",
        )

    # 3. Addon managed identity
    if not identity_id:
        click.echo(f"Fetching identity ID from AKS cluster: {aks_name}...")
        identity_id = run_text(
            ["az", "aks", "show",
             "--resource-group", aks_resource_group,
             "--name", aks_name,
             "--query", "addonProfiles.azureKeyvaultSecretsProvider.identity.clientId",
             "-o", "tsv"],
            error_msg=f"Error fetching identity ID from {aks_name}",
        )
        if not identity_id:
            raise click.ClickException(
                "Could not fetch identity ID from AKS cluster. "
                "Make sure the Azure Key Vault Secrets Provider addon is enabled."
            )
        click.echo(f"Using identity ID: {identity_id}")

    # 4. Grant the identity access to every keyvault it will read
    keyvaults_to_access = [keyvault_name]
    if include_multi_kv:
        keyvaults_to_access.append(multi_kv_name)
    if include_certificates_spc:
        keyvaults_to_access.append(cert_kv_name)

    banner("Checking Key Vault access policies...")
    identity_object_id = run_text(
        ["az", "ad", "sp", "show", "--id", identity_id, "--query", "id", "-o", "tsv"],
        error_msg=f"Error fetching object ID for client ID {identity_id}",
    )
    if not identity_object_id:
        raise click.ClickException(f"Could not fetch object ID for client ID {identity_id}")
    click.echo(f"Identity object ID: {identity_object_id}\n")

    for kv_name in keyvaults_to_access:
        click.echo(f"Checking access to {kv_name}...")
        policies = run_json(
            ["az", "keyvault", "show",
             "--name", kv_name,
             "--query", f"properties.accessPolicies[?objectId=='{identity_object_id}']",
             "-o", "json"],
            error_msg=f"Error fetching access policies for {kv_name}",
        )
        if policies:
            click.echo(f"  ✅ Identity already has access to {kv_name}")
            continue

        click.echo(f"  ⚠️  Identity does not have access to {kv_name}, granting it...")
        set_policy_cmd = ["az", "keyvault", "set-policy", "--name", kv_name, "--object-id", identity_object_id,
                          "--secret-permissions", "get", "list"]
        if include_certificates_spc and kv_name == cert_kv_name:
            set_policy_cmd += ["--certificate-permissions", "get", "list", "--key-permissions", "get", "list"]
        run_cmd(set_policy_cmd, error_msg=f"Error granting access to {kv_name}", success_msg=f"Access granted to {kv_name}")

    # 5. Collect the secrets of each keyvault: (keyvault, kube secret name, custom secret objects)
    banner("Collecting keyvault configurations...")
    kv_sources = [(keyvault_name, f"{project_name}-secret", (secret_objects_mapping or {}).get(namespace))]
    if include_multi_kv:
        kv_sources.append((multi_kv_name, "multi-secret", None))

    keyvaults_config = {}
    for kv_name, kube_secret_name, secret_objects in kv_sources:
        click.echo(f"Fetching secrets from Key Vault: {kv_name}...")
        all_secrets = run_json(
            ["az", "keyvault", "secret", "list", "--vault-name", kv_name, "--query", "[].name", "-o", "json"],
            error_msg=f"Error listing secrets in {kv_name}",
        ) or []
        click.echo(f"  Found {len(all_secrets)} total secret(s)")

        kv_secrets = all_secrets
        if secrets:
            kv_secrets = [s for s in all_secrets if s in secrets]
            missing = set(secrets) - set(kv_secrets)
            if missing:
                click.echo(f"  Warning: The following secrets were not found: {', '.join(missing)}", err=True)

        if not kv_secrets:
            click.echo(f"  No secrets found in the Key Vault: {kv_name}", err=True)
            continue

        click.echo(f"  Using {len(kv_secrets)} secret(s)")
        keyvaults_config[kv_name] = {
            "secrets": kv_secrets,
            "secret_name": kube_secret_name,
            "secret_objects": secret_objects,
            "spc_name": f"az-{kv_name}-spc",
            "mount_path": f"/mnt/{kv_name}-secrets",
        }
    if not keyvaults_config:
        raise click.ClickException("No keyvaults configured")

    # 6. Generate the manifests
    banner("Generating SecretProviderClass manifests...")
    spcs = []
    for kv_name, kv_config in keyvaults_config.items():
        click.echo(f"  Generating SPC for {kv_name}")
        spcs.append(_generate_spc_for_keyvault(
            keyvault_name=kv_name,
            secrets=kv_config["secrets"],
            secret_name=kv_config["secret_name"],
            tenant_id=tenant_id,
            identity_id=identity_id,
            namespace=namespace,
            project_name=project_name,
            secret_objects_config=kv_config.get("secret_objects")
        ))
    if include_certificates_spc:
        click.echo(f"  Generating certificate SPC for {cert_kv_name}")
        spcs.append(_generate_spc_for_certificates(
            tenant_id=tenant_id,
            identity_id=identity_id,
            namespace=namespace,
            cert_name=cert_name,
            env=aks_env,
            socle_code=SOCLE_KV_CODE,
            kube_secret_name=cert_secret_name
        ))

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

    # 7. Write outputs
    banner("Writing manifests...")
    if not separate_files:
        write_output(
            dump_manifests(spcs + [pod_manifest]), output, f"{project_name}-{aks_env}-akv-manifests.yaml", "All manifests"
        )
    else:
        output_dir = Path(output).expanduser()
        if not output_dir.is_dir() and output_dir.suffix:
            output_dir = output_dir.parent
        write_output(dump_manifests(spcs), str(output_dir), f"{project_name}-{aks_env}-spcs.yaml", "SecretProviderClass(es)")
        write_output(dump_manifests([pod_manifest]), str(output_dir), f"{project_name}-{aks_env}-secret-keeper.yaml", "Secret-keeper Deployment")
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
        banner("Setting up Azure File CSI infrastructure...")

        identity_name = f"azurefilecsi-{env}d9{aks_code}aks01"
        aks_name = f"{env}d9{aks_code}aks01"
        aks_resource_group = f"r400{env}d9{aks_code}"
        storage_account = f"{env}d9{adl_code}adl01"
        storage_resource_group = f"r400{env}d9{adl_code}"

        # AKS node resource group (managed RG where the identity lives)
        click.echo("Getting AKS node resource group...")
        node_resource_group = run_text(
            ["az", "aks", "show", "--name", aks_name, "--resource-group", aks_resource_group,
             "--query", "nodeResourceGroup", "-o", "tsv"],
            error_msg=f"Error getting node resource group for {aks_name}",
        )
        click.echo(f"  Node resource group: {node_resource_group}")

        # Managed identity in the node resource group (created if missing)
        click.echo(f"\nChecking managed identity: {identity_name}...")
        identity_query = ["--query", "{clientId:clientId,id:id,principalId:principalId}", "-o", "json"]
        existing_identity = run_cmd(
            ["az", "identity", "show", "--name", identity_name, "--resource-group", node_resource_group, *identity_query],
            check=False,
        )
        if existing_identity.returncode == 0 and existing_identity.stdout.strip():
            identity = json.loads(existing_identity.stdout)
            click.echo(f"  ✅ Managed identity exists with client ID: {identity['clientId']}")
        else:
            click.echo(f"  Creating managed identity: {identity_name}...")
            identity = run_json(
                ["az", "identity", "create", "--name", identity_name, "--resource-group", node_resource_group, *identity_query],
                error_msg=f"Error creating managed identity {identity_name}",
            )
            click.echo(f"  ✅ Managed identity created with client ID: {identity['clientId']}")

        # Assign the identity to the node pool VMSS
        click.echo(f"\nGetting VMSS from pool {pool_name}...")
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
        for vmss_name in vmss_names:
            click.echo(f"  Assigning identity to VMSS: {vmss_name}...")
            run_cmd(
                ["az", "vmss", "identity", "assign",
                 "--identities", identity["id"],
                 "--name", vmss_name,
                 "--resource-group", node_resource_group],
                error_msg=f"Error assigning identity to VMSS {vmss_name}",
                success_msg=f"Identity assigned to VMSS: {vmss_name}",
            )

        banner("⚠️  MANUAL STEP REQUIRED")
        click.echo("Please enable 'Managed Identity for SMB' on the storage account:")
        click.echo(f"  Storage Account: {storage_account}")
        click.echo(f"  Resource Group: {storage_resource_group}")
        click.echo("\nSteps:")
        click.echo(f"  1. Go to Azure Portal > Storage Account > {storage_account}")
        click.echo("  2. Navigate to Configuration")
        click.echo("  3. Enable 'Managed Identity for SMB'")
        click.echo("  4. Save changes")

        # RBAC role on the storage account (requires elevated permissions)
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
        client_id = identity["clientId"]
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
    write_output(
        dump_manifests(manifests),
        output,
        f"{project_name}-{env}-{adl_code}-azurefile-csi.yaml",
        "Azure File CSI manifests",
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
