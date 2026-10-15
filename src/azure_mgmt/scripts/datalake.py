"""Azure Data Lake Gen2 commands."""

import click
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from helpers.azure import resolve_environments, set_subscription, strip_environment_prefix
from helpers.config import require_config
from helpers.shell import run_cmd_or_dry, run_idempotent, run_text
from helpers.ui import banner


@click.group()
def datalake():
    """Azure Data Lake Gen2 management commands"""
    pass


def _group_names(env_name: str, project_group_name: str) -> Tuple[str, str]:
    """Return the (contributors, readers) security group names of a project."""
    return (
        f"GR_SEC_DATALAB_AZU_C-{env_name}-{project_group_name}",
        f"GR_SEC_DATALAB_AZU_R-{env_name}-{project_group_name}",
    )


def _group_oid(group_name: str) -> str:
    click.secho(f"🔍 Fetching Object ID for group: {group_name}...", fg="yellow")
    oid = run_text(
        ["az", "ad", "group", "show", "--group", group_name, "--query", "id", "-o", "tsv"],
        error_msg=f"Error fetching group {group_name}",
    )
    click.secho(f"  ✅ Object ID: {oid}", fg="green")
    return oid


def _print_completion(dry_run: bool, title: str, details: str) -> None:
    click.secho("\n" + "="*60, fg="cyan", bold=True)
    if dry_run:
        click.secho("⚠️  === DRY RUN COMPLETE - No changes were made ===", fg="yellow", bold=True)
    else:
        click.secho(f"✅ === {title} ===", fg="green", bold=True)
        click.secho(f"🎉 {details}", fg="green")
    click.secho("="*60, fg="cyan", bold=True)


@datalake.command()
@click.argument("project_group_name", type=str)
@click.argument("project_folder_name", type=str)
@click.option("--sa-code", default="0021", help="Azure Storage Account name")
@click.option("--no-root-folder", is_flag=True, help="Doesnt use root folders. Path is /{project_folder_name}")
@click.option("--root-folders", multiple=True, help="Root folder paths (can specify multiple)")
@click.option("--environments", multiple=True, help="Environments")
@click.option("--contributors-permissions", default="rwx", help="Permissions for first group (default: rwx)")
@click.option("--readers-permissions", default="r-x", help="Permissions for second group (default: r-x)")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
def set_project_groups_acls(
    project_group_name: str,
    project_folder_name: str,
    sa_code: str,
    environments: tuple,
    root_folders: tuple,
    contributors_permissions: str,
    readers_permissions: str,
    dry_run: bool,
    no_root_folder: bool
):
    """
    Set ACLs on Azure Data Lake Gen2 folders for a project across multiple environments.

    This command adds ACLs for two groups on folders derived from the project name
    and root folder paths. It operates across the specified environments.

    The group names are derived as: {project_name}-{group_suffix}
    The folder paths are: {root_folder}/{project_name}

    Example:
        mycli azure datalake set-project-groups-acls myproject myproject-folder \\
            --environments d t \\
            --root-folders bronze silver gold
    """
    envs = resolve_environments(environments)
    if no_root_folder:
        folder_paths = [f"/{project_folder_name}"]
    else:
        folder_paths = [f"{root}/{project_folder_name}" for root in root_folders or require_config("ingestion_root_folders")]

    banner(f"💡 Environments: {', '.join(envs)}\n💡 Storage Account Code: {sa_code}", fg="cyan")
    if dry_run:
        click.secho("⚠️  === DRY RUN MODE - No changes will be made ===", fg="yellow", bold=True)

    for env, env_cfg in envs.items():
        banner(f"🌍 Processing Environment: {env.upper()} ({env_cfg['name']})", fg="magenta")
        set_subscription(env_cfg, dry_run)

        storage_account = f"{env}d9{sa_code}adl01"
        filesystem = f"{env}d9{sa_code}fs01"
        contributors_name, readers_name = _group_names(env_cfg["name"], project_group_name)

        click.secho(f"📦 Project: {project_group_name}", fg="cyan", bold=True)
        click.secho(f"  👥 Contributors: {contributors_name} ({contributors_permissions})", fg="cyan")
        click.secho(f"  👥 Readers: {readers_name} ({readers_permissions})", fg="cyan")
        click.secho(f"  💾 Storage Account: {storage_account}", fg="cyan")
        click.secho(f"  📂 Filesystem: {filesystem}", fg="cyan")
        click.secho(f"  📁 Folders: {', '.join(folder_paths)}\n", fg="cyan")

        contributors_acl = f"group:{_group_oid(contributors_name)}:{contributors_permissions}"
        readers_acl = f"group:{_group_oid(readers_name)}:{readers_permissions}"
        # Default ACLs ensure child items inherit permissions
        acls = [
            ("access ACL for contributors", contributors_acl),
            ("access ACL for readers", readers_acl),
            ("default ACL for contributors", f"default:{contributors_acl}"),
            ("default ACL for readers", f"default:{readers_acl}"),
        ]

        for folder_path in folder_paths:
            click.secho(f"\n  🎯 Setting ACLs on: {folder_path}", fg="bright_cyan", bold=True)
            try:
                for label, acl in acls:
                    run_cmd_or_dry(
                        ["az", "storage", "fs", "access", "update-recursive",
                         "--acl", acl,
                         "--path", folder_path,
                         "--file-system", filesystem,
                         "--account-name", storage_account],
                        dry_run=dry_run,
                        dry_run_msg=f"Would recursively update {label}: {acl}",
                        error_msg=f"Error setting {label} on {folder_path}",
                        success_msg=f"Updated {label}: {acl}",
                    )
            except click.Abort:
                click.secho("    ⚠️  Continuing with next folder...", fg="yellow", err=True)

    _print_completion(
        dry_run,
        "ACL Configuration Complete",
        f"Successfully configured ACLs for project '{project_group_name}' across {len(envs)} environment(s)",
    )


@datalake.command()
@click.argument("project_folder_name", type=str)
@click.option("--environments", multiple=True, help="Environments (d, t, r, p)")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
def create_ingestion_folders(
    project_folder_name: str,
    environments: tuple,
    dry_run: bool
):
    """
    Create project folders in Azure Data Lake Gen2 across multiple environments.

    This command creates folder structures for a project under specified root folders
    across the specified environments.

    The folder paths created are: {root_folder}/{project_folder_name}

    Example:
        mycli azure datalake create-ingestion-folders myproject \\
            --environments d t
    """
    sa_code = "0021"
    envs = resolve_environments(environments)
    root_folders = require_config("ingestion_root_folders")

    banner(
        f"💡 Project Folder Name: {project_folder_name}\n"
        f"💡 Environments: {', '.join(envs)}\n"
        f"💡 Storage Account Code: {sa_code}\n"
        f"💡 Root folders: {', '.join(root_folders)}",
        fg="cyan",
    )
    if dry_run:
        click.secho("⚠️  === DRY RUN MODE - No folders will be created ===", fg="yellow", bold=True)

    for env, env_cfg in envs.items():
        banner(f"🌍 Processing Environment: {env.upper()} ({env_cfg['name']})", fg="magenta")
        set_subscription(env_cfg, dry_run)

        storage_account = f"{env}d9{sa_code}adl01"
        filesystem = f"{env}d9{sa_code}fs01"
        click.secho(f"💾 Storage Account: {storage_account}", fg="cyan")
        click.secho(f"📂 Filesystem: {filesystem}\n", fg="cyan")

        for root_folder in root_folders:
            folder_path = f"{root_folder}/{project_folder_name}"
            click.secho(f"  📁 Creating folder: {folder_path}", fg="bright_cyan", bold=True)
            try:
                run_idempotent(
                    ["az", "storage", "fs", "directory", "create",
                     "--name", folder_path,
                     "--file-system", filesystem,
                     "--account-name", storage_account],
                    exists_markers=["PathAlreadyExists", "already exists"],
                    dry_run=dry_run,
                    dry_run_msg=f"Would create directory: {folder_path}",
                    error_msg="Error creating folder",
                    success_msg="Folder created successfully",
                    exists_msg="Folder already exists (skipping)",
                )
            except click.Abort:
                click.secho("    ⚠️  Continuing with next folder...", fg="yellow", err=True)

    _print_completion(
        dry_run,
        "Folder Creation Complete",
        f"Successfully created folders for project '{project_folder_name}' across {len(envs)} environment(s)",
    )


def _setup_blob_container_for_env(
    env: str,
    env_name: str,
    storage_account_name: str,
    container_name: str,
    project_group_name: str,
    policy_name: Optional[str],
    policy_permissions: str,
    policy_expiry_years: int,
    contributors_role: str,
    readers_role: str,
    kv_code: Optional[str],
    dry_run: bool,
):
    """
    Set up a blob container with group permissions, an access policy, and a SAS token.

    Creates the container, assigns RBAC roles to contributor and reader groups derived
    from PROJECT_GROUP_NAME, creates a stored access policy, generates a SAS token and
    optionally stores it in a Key Vault.
    """
    policy_name = policy_name or f"ap{storage_account_name[-2:]}-{container_name}"
    contributors_name, readers_name = _group_names(env_name, project_group_name.upper())
    expiry_date = (datetime.now(timezone.utc) + timedelta(days=365 * policy_expiry_years)).strftime("%Y-%m-%dT%H:%MZ")

    banner(
        f"💡 Storage Account: {storage_account_name}\n"
        f"💡 Container: {container_name}\n"
        f"💡 Project Group: {project_group_name}\n"
        f"💡 Policy: {policy_name} | Permissions: {policy_permissions} | Expiry: {expiry_date}",
        fg="cyan",
    )

    click.secho("📦 Step 1: Creating blob container...", fg="bright_cyan", bold=True)
    run_cmd_or_dry(
        ["az", "storage", "container", "create", "--name", container_name,
         "--account-name", storage_account_name, "--auth-mode", "login"],
        dry_run=dry_run,
        dry_run_msg=f"Would create container '{container_name}' in '{storage_account_name}'",
        error_msg="Error creating container",
        success_msg=f"Container '{container_name}' created",
    )

    click.secho("\n👥 Step 2: Fetching group object IDs...", fg="bright_cyan", bold=True)
    role_assignments = [
        (contributors_name, _group_oid(contributors_name), contributors_role),
        (readers_name, _group_oid(readers_name), readers_role),
    ]

    click.secho("\n🔐 Step 3: Assigning RBAC roles...", fg="bright_cyan", bold=True)
    sa_resource_id = run_text(
        ["az", "storage", "account", "show", "--name", storage_account_name, "--query", "id", "-o", "tsv"],
        error_msg="Error fetching storage account resource ID",
    )
    container_scope = f"{sa_resource_id}/blobServices/default/containers/{container_name}"
    for group_name, group_oid, role in role_assignments:
        click.secho(f"  🔧 '{role}' → {group_name}...", fg="yellow")
        run_idempotent(
            ["az", "role", "assignment", "create",
             "--assignee", group_oid, "--role", role, "--scope", container_scope],
            exists_markers=["RoleAssignmentExists", "already exists"],
            dry_run=dry_run,
            dry_run_msg=f"Would assign role to OID {group_oid} at scope {container_scope}",
            error_msg="Error assigning role",
            success_msg="Role assigned",
            exists_msg="Role assignment already exists (skipping)",
        )

    account_key_result = run_cmd_or_dry(
        ["az", "storage", "account", "keys", "list",
         "--account-name", storage_account_name, "--query", "[0].value", "-o", "tsv"],
        dry_run=dry_run,
        dry_run_msg="Would retrieve account key",
        error_msg="Error retrieving account key",
    )
    account_key = account_key_result.stdout.strip() if account_key_result else "<account-key>"

    click.secho("\n📋 Step 4: Creating stored access policy...", fg="bright_cyan", bold=True)
    run_cmd_or_dry(
        ["az", "storage", "container", "policy", "create",
         "--container-name", container_name,
         "--name", policy_name,
         "--account-name", storage_account_name,
         "--account-key", account_key,
         "--permissions", policy_permissions,
         "--expiry", expiry_date],
        dry_run=dry_run,
        dry_run_msg=f"Would create policy '{policy_name}' on container '{container_name}'",
        error_msg="Error creating access policy",
        success_msg=f"Access policy '{policy_name}' created",
    )

    click.secho("\n🔑 Step 5: Generating shared access signature...", fg="bright_cyan", bold=True)
    sas_result = run_cmd_or_dry(
        ["az", "storage", "container", "generate-sas",
         "--name", container_name,
         "--account-name", storage_account_name,
         "--account-key", account_key,
         "--policy-name", policy_name,
         "-o", "tsv"],
        dry_run=dry_run,
        dry_run_msg=f"Would generate SAS using policy '{policy_name}'",
        error_msg="Error generating SAS",
        success_msg="SAS token generated",
    )
    sas_token = sas_result.stdout.strip() if sas_result else "<sas-token>"
    if sas_result:
        click.secho(f"  🔑 ?{sas_token}", fg="bright_green", bold=True)
        click.secho(f"\n  📎 Blob endpoint with SAS:", fg="cyan")
        click.secho(f"  https://{storage_account_name}.blob.core.windows.net/{container_name}?{sas_token}", fg="white")

    if kv_code:
        click.secho("\n🗝️  Step 6: Storing SAS token to Key Vault...", fg="bright_cyan", bold=True)
        secret_name = f"{storage_account_name}-{container_name}-sas"
        keyvault_name = f"{env}d9{kv_code}akv01"
        click.secho(f"  Vault: {keyvault_name}", fg="cyan")
        click.secho(f"  Secret: {secret_name}", fg="cyan")
        run_cmd_or_dry(
            ["az", "keyvault", "secret", "set",
             "--vault-name", keyvault_name,
             "--name", secret_name,
             "--value", sas_token],
            dry_run=dry_run,
            dry_run_msg="Would store SAS token to vault",
            error_msg="Error storing SAS to Key Vault",
            success_msg="SAS token stored in Key Vault",
        )

    _print_completion(
        dry_run,
        "Blob Container Setup Complete",
        f"Container '{container_name}' configured for project '{project_group_name}'",
    )


@datalake.command()
@click.argument("storage_account_name", type=str)
@click.argument("container_name", type=str)
@click.argument("project_group_name", type=str)
@click.option("--envs", multiple=True, help="Environment prefixes to process (default: d, t, r, p)")
@click.option("--policy-name", default=None, help="Stored access policy name (defaults to a name derived from the storage account)")
@click.option("--policy-permissions", default="racwdl", show_default=True, help="Stored access policy permissions")
@click.option("--policy-expiry-years", default=1, type=int, show_default=True, help="Stored access policy expiry in years")
@click.option("--contributors-role", default="Storage Blob Data Contributor", show_default=True, help="Azure RBAC role for contributors")
@click.option("--readers-role", default="Storage Blob Data Reader", show_default=True, help="Azure RBAC role for readers")
@click.option("--kv-code", default=None, help="Key Vault code for storing the SAS token (e.g., '0021')")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
def setup_blob_container(
    storage_account_name: str,
    container_name: str,
    project_group_name: str,
    envs: tuple,
    policy_name: Optional[str],
    policy_permissions: str,
    policy_expiry_years: int,
    contributors_role: str,
    readers_role: str,
    kv_code: Optional[str],
    dry_run: bool,
):
    """Set up a blob container across one or more environments.

    STORAGE_ACCOUNT_NAME may omit the environment prefix or include one that will
    be replaced. For example, d90021sa01 targets dd90021sa01 in development.
    """
    storage_account_suffix = strip_environment_prefix(storage_account_name)
    if dry_run:
        click.secho("⚠️  === DRY RUN MODE - No changes will be made ===", fg="yellow", bold=True)

    for env, env_cfg in resolve_environments(envs).items():
        banner(f"🌍 Processing {env.upper()} ({env_cfg['name']})", fg="magenta")
        set_subscription(env_cfg, dry_run)
        _setup_blob_container_for_env(
            env=env,
            env_name=env_cfg["name"],
            storage_account_name=f"{env}{storage_account_suffix}",
            container_name=container_name,
            project_group_name=project_group_name,
            policy_name=policy_name,
            policy_permissions=policy_permissions,
            policy_expiry_years=policy_expiry_years,
            contributors_role=contributors_role,
            readers_role=readers_role,
            kv_code=kv_code,
            dry_run=dry_run,
        )
