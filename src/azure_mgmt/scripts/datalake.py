"""Azure Data Lake Gen2 commands."""

import click
import subprocess
from datetime import datetime, timedelta, timezone

from helpers.env_config import require_environments, require_ingestion_root_folders
from helpers.shell import run_cmd, run_cmd_or_dry


@click.group()
def datalake():
    """Azure Data Lake Gen2 management commands"""
    pass


@datalake.command()
@click.argument("project_group_name", type=str)
@click.argument("project_folder_name", type=str)
@click.option("--sa-code", default="0021", help="Azure Storage Account name")
@click.option("--no-root-folder", is_flag=True, help="Doesnt use root folders. Path is /{project_folder_name}")
@click.option("--root-folders", multiple=True, help="Root folder paths (can specify multiple)")
@click.option("--environments", multiple=True, help="Environments")
@click.option("--contributors-permissions", default="rwx", help="Permissions for first group (default: r-x)")
@click.option("--readers-permissions", default="r-x", help="Permissions for second group (default: rwx)")
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
    environments_dict = require_environments()

    if len(environments)<1: 
        environments = ("d", "t", "r", "p")

    if len(root_folders)<1 and not no_root_folder: 
        root_folders = require_ingestion_root_folders()

    if no_root_folder: 
        root_folders = ["none"]


    for env in environments:
        if not env in environments_dict.keys():
            click.secho(f"❌ Environment prefix {env} not supported", fg="red", err=True)
    

    click.secho("\n" + "="*60, fg="cyan", bold=True)
    click.secho(f"💡 Environments: {environments}", fg="cyan")
    click.secho(f"💡 Storage Account Code: {sa_code}", fg="cyan")
    click.secho("="*60 + "\n", fg="cyan", bold=True)

    # Process each environment
    for env in environments:
        click.secho(f"\n{'='*60}", fg="magenta", bold=True)
        click.secho(f"🌍 Processing Environment: {env.upper()} ({environments_dict[env][0]})", fg="magenta", bold=True)
        click.secho(f"{'='*60}", fg="magenta", bold=True)
        
        # Set subscription for this environment
        subscription_id = environments_dict[env][1]
        click.secho(f"🔧 Setting subscription to {environments_dict[env][0]} ({subscription_id})...", fg="yellow")
        run_cmd_or_dry(
            ["az", "account", "set", "--subscription", subscription_id],
            dry_run=dry_run,
            dry_run_msg=f"Would run: az account set --subscription {subscription_id}",
            error_msg="Error setting subscription",
            success_msg="Subscription set successfully",
            capture=False,
        )

        storage_account = f"{env}d9{sa_code}adl01"
        filesystem = f"{env}d9{sa_code}fs01" 
        
        # Derive group names from project name
        contributors_name = f"GR_SEC_DATALAB_AZU_C-{environments_dict[env][0]}-{project_group_name}"
        readers_name = f"GR_SEC_DATALAB_AZU_R-{environments_dict[env][0]}-{project_group_name}"
        
        click.secho(f"📦 Project: {project_group_name}", fg="cyan", bold=True)
        click.secho(f"  👥 Contributors: {contributors_name}", fg="cyan")
        click.secho(f"     Permissions: {contributors_permissions}", fg="white", dim=True)
        click.secho(f"  👥 Readers: {readers_name}", fg="cyan")
        click.secho(f"     Permissions: {readers_permissions}", fg="white", dim=True)
        click.secho(f"  💾 Storage Account: {storage_account}", fg="cyan")
        click.secho(f"  📂 Filesystem: {filesystem}", fg="cyan")
        click.secho(f"  📁 Root folders: {', '.join(root_folders)}", fg="cyan")
        click.echo("")
        
        if dry_run:
            click.secho("⚠️  === DRY RUN MODE - No changes will be made ===", fg="yellow", bold=True)
        
        click.secho(f"🔍 Fetching Object ID for group: {contributors_name}...", fg="yellow")
        contributors_oid = run_cmd(
            ["az", "ad", "group", "show", "--group", contributors_name, "--query", "id", "-o", "tsv"],
            error_msg=f"Error fetching group {contributors_name}",
        ).stdout.strip()
        click.secho(f"  ✅ Contributors Object ID: {contributors_oid}", fg="green")

        click.secho(f"🔍 Fetching Object ID for group: {readers_name}...", fg="yellow")
        readers_oid = run_cmd(
            ["az", "ad", "group", "show", "--group", readers_name, "--query", "id", "-o", "tsv"],
            error_msg=f"Error fetching group {readers_name}",
        ).stdout.strip()
        click.secho(f"  ✅ Readers Object ID: {readers_oid}\n", fg="green")

        # Process each root folder
        for root_folder in root_folders:
            # Build folder path: {root_folder}/{project_name}

            folder_path = f"{root_folder}/{project_folder_name}"
            if no_root_folder:
                folder_path = f"/{project_folder_name}"
            
            click.secho(f"\n  🎯 Setting ACLs on: {folder_path}", fg="bright_cyan", bold=True)
            
            # Build ACL string for both groups
            # Format: group:objectId:permissions
            acl_contributors = f"group:{contributors_oid}:{contributors_permissions}"
            acl_readers = f"group:{readers_oid}:{readers_permissions}"
            
            # Default ACLs ensure child items inherit permissions
            default_acl_contributors = f"default:group:{contributors_oid}:{contributors_permissions}"
            default_acl_readers = f"default:group:{readers_oid}:{readers_permissions}"
            
            if dry_run:
                click.secho(f"    ⚠️  [DRY RUN] Would recursively update access ACL: {acl_contributors}", fg="yellow")
                click.secho(f"    ⚠️  [DRY RUN] Would recursively update access ACL: {acl_readers}", fg="yellow")
                click.secho(f"    ⚠️  [DRY RUN] Would recursively update default ACL: {default_acl_contributors}", fg="yellow")
                click.secho(f"    ⚠️  [DRY RUN] Would recursively update default ACL: {default_acl_readers}", fg="yellow")
            else:
                try:
                    # Update access ACL for contributors recursively
                    click.secho(f"    🔧 Updating access ACL for contributors...", fg="yellow")
                    click.secho(f"       {acl_contributors}", fg="white", dim=True)
                    subprocess.run(
                        [
                            "az", "storage", "fs", "access", "update-recursive",
                            "--acl", acl_contributors,
                            "--path", folder_path,
                            "--file-system", filesystem,
                            "--account-name", storage_account,
                        ],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    
                    # Update access ACL for readers recursively
                    click.secho(f"    🔧 Updating access ACL for readers...", fg="yellow")
                    click.secho(f"       {acl_readers}", fg="white", dim=True)
                    subprocess.run(
                        [
                            "az", "storage", "fs", "access", "update-recursive",
                            "--acl", acl_readers,
                            "--path", folder_path,
                            "--file-system", filesystem,
                            "--account-name", storage_account,
                        ],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    
                    # Update default ACL for contributors recursively
                    click.secho(f"    🔧 Updating default ACL for contributors...", fg="yellow")
                    click.secho(f"       {default_acl_contributors}", fg="white", dim=True)
                    subprocess.run(
                        [
                            "az", "storage", "fs", "access", "update-recursive",
                            "--acl", default_acl_contributors,
                            "--path", folder_path,
                            "--file-system", filesystem,
                            "--account-name", storage_account,
                        ],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    
                    # Update default ACL for readers recursively
                    click.secho(f"    🔧 Updating default ACL for readers...", fg="yellow")
                    click.secho(f"       {default_acl_readers}", fg="white", dim=True)
                    subprocess.run(
                        [
                            "az", "storage", "fs", "access", "update-recursive",
                            "--acl", default_acl_readers,
                            "--path", folder_path,
                            "--file-system", filesystem,
                            "--account-name", storage_account,
                        ],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    
                    click.secho(f"    ✅ Access and default ACLs set recursively for all children", fg="green", bold=True)
                    
                except subprocess.CalledProcessError as e:
                    click.secho(f"    ❌ Error setting ACLs: {e.stderr}", fg="red", err=True)
                    click.secho("    ⚠️  Continuing with next folder...", fg="yellow", err=True)
        
        click.echo("")
    
    click.secho("\n" + "="*60, fg="cyan", bold=True)
    if dry_run:
        click.secho("⚠️  === DRY RUN COMPLETE - No changes were made ===", fg="yellow", bold=True)
    else:
        click.secho("✅ === ACL Configuration Complete ===", fg="green", bold=True)
        click.secho(f"🎉 Successfully configured ACLs for project '{project_group_name}' across {len(environments)} environment(s)", fg="green")
    click.secho("="*60, fg="cyan", bold=True)


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
    root_folders = require_ingestion_root_folders()
    environments_dict = require_environments()

    if len(environments) < 1:
        environments = ("d", "t", "r", "p")


    for env in environments:
        if not env in environments_dict.keys():
            click.secho(f"❌ Environment prefix {env} not supported", fg="red", err=True)
    
    click.secho("\n" + "="*60, fg="cyan", bold=True)
    click.secho(f"💡 Project Folder Name: {project_folder_name}", fg="cyan")
    click.secho(f"💡 Environments: {environments}", fg="cyan")
    click.secho(f"💡 Storage Account Code: {sa_code}", fg="cyan")
    click.secho(f"💡 Root folders: {', '.join(root_folders)}", fg="cyan")
    click.secho("="*60 + "\n", fg="cyan", bold=True)

    # Process each environment
    for env in environments:
        click.secho(f"\n{'='*60}", fg="magenta", bold=True)
        click.secho(f"🌍 Processing Environment: {env.upper()} ({environments_dict[env][0]})", fg="magenta", bold=True)
        click.secho(f"{'='*60}", fg="magenta", bold=True)
        
        # Set subscription for this environment
        subscription_id = environments_dict[env][1]
        click.secho(f"🔧 Setting subscription to {environments_dict[env][0]} ({subscription_id})...", fg="yellow")
        run_cmd_or_dry(
            ["az", "account", "set", "--subscription", subscription_id],
            dry_run=dry_run,
            dry_run_msg=f"Would run: az account set --subscription {subscription_id}",
            error_msg="Error setting subscription",
            success_msg="Subscription set successfully",
            capture=False,
        )

        storage_account = f"{env}d9{sa_code}adl01"
        filesystem = f"{env}d9{sa_code}fs01"
        
        click.secho(f"💾 Storage Account: {storage_account}", fg="cyan")
        click.secho(f"📂 Filesystem: {filesystem}", fg="cyan")
        click.echo("")
        
        if dry_run:
            click.secho("⚠️  === DRY RUN MODE - No folders will be created ===\n", fg="yellow", bold=True)
        
        # Process each root folder
        for root_folder in root_folders:
            # Build folder path: {root_folder}/{project_folder_name}
            folder_path = f"{root_folder}/{project_folder_name}"
            
            click.secho(f"  📁 Creating folder: {folder_path}", fg="bright_cyan", bold=True)
            
            if dry_run:
                click.secho(f"    ⚠️  [DRY RUN] Would create directory: {folder_path}", fg="yellow")
            else:
                try:
                    # Create directory
                    subprocess.run(
                        [
                            "az", "storage", "fs", "directory", "create",
                            "--name", folder_path,
                            "--file-system", filesystem,
                            "--account-name", storage_account,
                        ],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    
                    click.secho(f"    ✅ Folder created successfully", fg="green", bold=True)
                    
                except subprocess.CalledProcessError as e:
                    # Check if the error is because the directory already exists
                    if "PathAlreadyExists" in e.stderr or "already exists" in e.stderr.lower():
                        click.secho(f"    ℹ️  Folder already exists (skipping)", fg="blue")
                    else:
                        click.secho(f"    ❌ Error creating folder: {e.stderr}", fg="red", err=True)
                        click.secho("    ⚠️  Continuing with next folder...", fg="yellow", err=True)
        
        click.echo("")
    
    click.secho("\n" + "="*60, fg="cyan", bold=True)
    if dry_run:
        click.secho("⚠️  === DRY RUN COMPLETE - No changes were made ===", fg="yellow", bold=True)
    else:
        click.secho("✅ === Folder Creation Complete ===", fg="green", bold=True)
        click.secho(f"🎉 Successfully created folders for project '{project_folder_name}' across {len(environments)} environment(s)", fg="green")
    click.secho("="*60, fg="cyan", bold=True)


def _setup_blob_container_for_env(
    storage_account_name: str,
    container_name: str,
    project_group_name: str,
    policy_name,
    policy_permissions: str,
    policy_expiry_years: int,
    contributors_role: str,
    readers_role: str,
    kv_code: str,
    dry_run: bool,
):
    """
    Set up a blob container with group permissions, an access policy, and a SAS token.

    Creates the container, assigns RBAC roles to contributor and reader groups derived
    from PROJECT_GROUP_NAME, creates a stored access policy, and generates a SAS token.

    Example:
        mycli azure datalake setup-blob-container mystorageaccount mycontainer myproject
    """

    sa_code = storage_account_name[-2:]
    env_short = storage_account_name[0]
    env = require_environments()[env_short][0]

    if policy_name is None:
        policy_name = f"ap{sa_code}-{container_name}"

    contributors_name = f"GR_SEC_DATALAB_AZU_C-{env}-{project_group_name.upper()}"
    readers_name = f"GR_SEC_DATALAB_AZU_R-{env}-{project_group_name.upper()}"
    expiry_date = (datetime.now(timezone.utc) + timedelta(days=365 * policy_expiry_years)).strftime("%Y-%m-%dT%H:%MZ")

    click.secho("\n" + "="*60, fg="cyan", bold=True)
    click.secho(f"💡 Storage Account: {storage_account_name}", fg="cyan")
    click.secho(f"💡 Container: {container_name}", fg="cyan")
    click.secho(f"💡 Project Group: {project_group_name}", fg="cyan")
    click.secho(f"💡 Policy: {policy_name} | Permissions: {policy_permissions} | Expiry: {expiry_date}", fg="cyan")
    click.secho("="*60 + "\n", fg="cyan", bold=True)

    if dry_run:
        click.secho("⚠️  === DRY RUN MODE - No changes will be made ===\n", fg="yellow", bold=True)

    # Step 1: Create the blob container
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
    click.secho(f"  🔍 {contributors_name}...", fg="yellow")
    contributors_oid = run_cmd(
        ["az", "ad", "group", "show", "--group", contributors_name, "--query", "id", "-o", "tsv"],
        error_msg=f"Error fetching group {contributors_name}",
    ).stdout.strip()
    click.secho(f"  ✅ Contributors OID: {contributors_oid}", fg="green")

    click.secho(f"  🔍 {readers_name}...", fg="yellow")
    readers_oid = run_cmd(
        ["az", "ad", "group", "show", "--group", readers_name, "--query", "id", "-o", "tsv"],
        error_msg=f"Error fetching group {readers_name}",
    ).stdout.strip()
    click.secho(f"  ✅ Readers OID: {readers_oid}", fg="green")

    click.secho("\n🔐 Step 3: Assigning RBAC roles...", fg="bright_cyan", bold=True)
    sa_resource_id = run_cmd(
        ["az", "storage", "account", "show", "--name", storage_account_name, "--query", "id", "-o", "tsv"],
        error_msg="Error fetching storage account resource ID",
    ).stdout.strip()

    container_scope = f"{sa_resource_id}/blobServices/default/containers/{container_name}"

    for group_name, group_oid, role in [
        (contributors_name, contributors_oid, contributors_role),
        (readers_name, readers_oid, readers_role),
    ]:
        click.secho(f"  🔧 '{role}' → {group_name}...", fg="yellow")
        if dry_run:
            click.secho(f"    ⚠️  [DRY RUN] Would assign role to OID {group_oid} at scope {container_scope}", fg="yellow")
        else:
            try:
                subprocess.run(
                    ["az", "role", "assignment", "create",
                     "--assignee", group_oid, "--role", role, "--scope", container_scope],
                    capture_output=True, text=True, check=True
                )
                click.secho(f"    ✅ Role assigned", fg="green")
            except subprocess.CalledProcessError as e:
                if "RoleAssignmentExists" in e.stderr or "already exists" in e.stderr.lower():
                    click.secho(f"    ℹ️  Role assignment already exists (skipping)", fg="blue")
                else:
                    click.secho(f"    ❌ Error assigning role: {e.stderr}", fg="red", err=True)
                    raise click.Abort()

    account_key = run_cmd_or_dry(
            ["az", "storage", "account", "keys", "list",
             "--account-name", storage_account_name, "--query", "[0].value", "-o", "tsv"],
            error_msg="Error retrieving account key",
            dry_run_msg="Would retrieve account key",
            dry_run=dry_run,
    )
    account_key = account_key.stdout.strip() if account_key else account_key

    click.secho("\n📋 Step 4: Creating stored access policy...", fg="bright_cyan", bold=True)
    click.secho(f"  Policy: {policy_name} | Permissions: {policy_permissions} | Expiry: {expiry_date}", fg="cyan")
    run_cmd_or_dry(
        [
            "az", "storage", "container", "policy", "create",
            "--container-name", container_name,
            "--name", policy_name,
            "--account-name", storage_account_name,
            "--account-key", account_key,
            "--permissions", policy_permissions,
            "--expiry", expiry_date,
        ],
        dry_run=dry_run,
        dry_run_msg=f"Would create policy '{policy_name}' on container '{container_name}'",
        error_msg="Error creating access policy",
        success_msg=f"Access policy '{policy_name}' created",
    )

    click.secho("\n🔑 Step 5: Generating shared access signature...", fg="bright_cyan", bold=True)
    sas_result = run_cmd_or_dry(
        [
            "az", "storage", "container", "generate-sas",
            "--name", container_name,
            "--account-name", storage_account_name,
            "--account-key", account_key,
            "--policy-name", policy_name,
            "-o", "tsv",
        ],
        dry_run=dry_run,
        dry_run_msg=f"Would generate SAS using policy '{policy_name}'",
        error_msg="Error generating SAS",
        success_msg="SAS token generated",
    )
    sas_token = None
    if sas_result:
        sas_token = sas_result.stdout.strip()
        click.secho(f"  🔑 ?{sas_token}", fg="bright_green", bold=True)
        click.secho(f"\n  📎 Blob endpoint with SAS:", fg="cyan")
        click.secho(f"  https://{storage_account_name}.blob.core.windows.net/{container_name}?{sas_token}", fg="white")

    # Step 6: Store SAS token to Key Vault (optional)
    if kv_code:
        click.secho("\n🗝️  Step 6: Storing SAS token to Key Vault...", fg="bright_cyan", bold=True)
        secret_name = f"{storage_account_name}-{container_name}-sas"
        keyvault_name = f"{env_short}d9{kv_code}akv01"
        click.secho(f"  Vault: {keyvault_name}", fg="cyan")
        click.secho(f"  Secret: {secret_name}", fg="cyan")
        run_cmd_or_dry(
            [
                "az", "keyvault", "secret", "set",
                "--vault-name", keyvault_name,
                "--name", secret_name,
                "--value", sas_token,
            ],
            dry_run=dry_run,
            dry_run_msg="Would store SAS token to vault",
            error_msg="Error storing SAS to Key Vault",
            success_msg="SAS token stored in Key Vault",
        )

    click.secho("\n" + "="*60, fg="cyan", bold=True)
    if dry_run:
        click.secho("⚠️  === DRY RUN COMPLETE - No changes were made ===", fg="yellow", bold=True)
    else:
        click.secho("✅ === Blob Container Setup Complete ===", fg="green", bold=True)
        click.secho(f"🎉 Container '{container_name}' configured for project '{project_group_name}'", fg="green")
    click.secho("="*60, fg="cyan", bold=True)


def _strip_environment_prefix(resource_name: str) -> str:
    normalized = resource_name.lower()
    if normalized[:1] in {"d", "t", "r", "p"} and normalized[1:3] == "d9":
        return resource_name[1:]
    return resource_name


@datalake.command()
@click.argument("storage_account_name", type=str)
@click.argument("container_name", type=str)
@click.argument("project_group_name", type=str)
@click.option("--envs", multiple=True, default=("d", "t", "r", "p"), show_default=True, help="Environment prefixes to process")
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
    policy_name,
    policy_permissions: str,
    policy_expiry_years: int,
    contributors_role: str,
    readers_role: str,
    kv_code: str,
    dry_run: bool,
):
    """Set up a blob container across one or more environments.

    STORAGE_ACCOUNT_NAME may omit the environment prefix or include one that will
    be replaced. For example, d90021sa01 targets dd90021sa01 in development.
    """
    environments = require_environments()
    invalid_envs = [env for env in envs if env not in environments]
    if invalid_envs:
        raise click.ClickException(f"Unsupported environment prefix(es): {', '.join(invalid_envs)}")

    storage_account_suffix = _strip_environment_prefix(storage_account_name)

    for env_short in envs:
        env_name, subscription_id = environments[env_short]
        account_name = f"{env_short}{storage_account_suffix}"

        click.secho(f"\n{'='*60}", fg="magenta", bold=True)
        click.secho(f"🌍 Processing {env_short.upper()} ({env_name})", fg="magenta", bold=True)
        click.secho(f"{'='*60}", fg="magenta", bold=True)

        run_cmd_or_dry(
            ["az", "account", "set", "--subscription", subscription_id],
            dry_run=dry_run,
            dry_run_msg=f"Would set subscription to {env_name} ({subscription_id})",
            error_msg=f"Error setting subscription for {env_name}",
            success_msg=f"Subscription set to {env_name}",
            capture=False,
        )

        _setup_blob_container_for_env(
            storage_account_name=account_name,
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
