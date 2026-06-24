"""Azure Data Lake Gen2 commands."""

import click
import subprocess

from .env_config import ENVIRONMENTS, INGESTION_ROOT_FOLDERS


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
    environments_dict = ENVIRONMENTS

    if len(environments)<1: 
        environments = ("d", "t", "r", "p")

    if len(root_folders)<1 and not no_root_folder: 
        root_folders = INGESTION_ROOT_FOLDERS

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
        if not dry_run:
            click.secho(f"🔧 Setting subscription to {environments_dict[env][0]} ({subscription_id})...", fg="yellow")
            try:
                subprocess.run(
                    ["az", "account", "set", "--subscription", subscription_id],
                    check=True
                )
                click.secho(f"✅ Subscription set successfully\n", fg="green")
            except subprocess.CalledProcessError as e:
                click.secho(f"❌ Error setting subscription: {e}", fg="red", err=True)
                raise click.Abort()
        else:
            click.secho(f"⚠️  [DRY RUN] Would run: az account set --subscription {subscription_id}\n", fg="yellow")

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
        
        # Get Object IDs for the groups
        try:
            click.secho(f"🔍 Fetching Object ID for group: {contributors_name}...", fg="yellow")
            result = subprocess.run(
                ["az", "ad", "group", "show", "--group", contributors_name, "--query", "id", "-o", "tsv"],
                capture_output=True,
                text=True,
                check=True
            )
            contributors_oid = result.stdout.strip()
            click.secho(f"  ✅ Contributors Object ID: {contributors_oid}", fg="green")
            
            click.secho(f"🔍 Fetching Object ID for group: {readers_name}...", fg="yellow")
            result = subprocess.run(
                ["az", "ad", "group", "show", "--group", readers_name, "--query", "id", "-o", "tsv"],
                capture_output=True,
                text=True,
                check=True
            )
            readers_oid = result.stdout.strip()
            click.secho(f"  ✅ Readers Object ID: {readers_oid}\n", fg="green")
            
        except subprocess.CalledProcessError as e:
            click.secho(f"❌ Error fetching group information: {e.stderr}", fg="red", err=True)
            click.secho("💡 Make sure you're logged in with 'az login' and the groups exist.", fg="yellow", err=True)
            raise click.Abort()

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
    root_folders = INGESTION_ROOT_FOLDERS
    environments_dict = ENVIRONMENTS

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
        if not dry_run:
            click.secho(f"🔧 Setting subscription to {environments_dict[env][0]} ({subscription_id})...", fg="yellow")
            try:
                subprocess.run(
                    ["az", "account", "set", "--subscription", subscription_id],
                    check=True
                )
                click.secho(f"✅ Subscription set successfully\n", fg="green")
            except subprocess.CalledProcessError as e:
                click.secho(f"❌ Error setting subscription: {e}", fg="red", err=True)
                raise click.Abort()
        else:
            click.secho(f"⚠️  [DRY RUN] Would run: az account set --subscription {subscription_id}\n", fg="yellow")

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
