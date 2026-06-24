"""Service Principal (SPN) management commands."""
"""
TODO : abstract away dry-run and error handling

"""


import click
import subprocess
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from .env_config import ENVIRONMENTS


# Dictionary mapping SPN types to naming patterns
# Pattern uses {env} and {code} placeholders
SPN_TYPES: Dict[str, Dict[str, str]] = {
    "spnadl": {
        "name_pattern": "{env}d9{code}spnadl",
        "secret_name": "{env}d9{code}spnadl-secret",
        "appid_secret_name": "{env}d9{code}spnadl-clientID",
        "description": "Project spn"
    },
    "devops": {
        "name_pattern": "sc-datahub-cicd-{env}d9{code}",
        "secret_name": "sc-datahub-cicd-{env}d9{code}-secret",
        "appid_secret_name": "sc-datahub-cicd-{env}d9{code}-clientID",
        "description": "Devops spn"
    }
}


@click.group()
def spn():
    """Service Principal management commands"""
    pass


@spn.command()
@click.argument("project-code", type=str)
@click.argument("spn-type", type=click.Choice(list(SPN_TYPES.keys()), case_sensitive=False))
@click.option("--env", "-e", "environments", multiple=True, help="Environment codes to process (can specify multiple)")
@click.option("--ttl", "--time-to-live", default=365, type=int, help="Secret expiration time in days (default: 365)")
@click.option("--store-appid", is_flag=True, help="Store the appId as a separate secret in Key Vault")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
def renew_secret(
    project_code: str,
    spn_type: str,
    environments: tuple,
    ttl: int,
    store_appid: bool,
    dry_run: bool
):
    """
    Renew secrets for a Service Principal and store in project keyvault.
    
    This command:
    1. Derives the SPN name from the type and project code
    2. Resets the SPN credential (generates new secret)
    3. Stores the new secret in the project's Azure Key Vault
    4. Optionally stores the appId as a separate secret
    
    The Key Vault name is derived as: {env}d9{code}akv01
    
    Examples:
        # Renew databricks SPN secret for dev and test environments
        mycli azure spn renew-secret 1381 databricks --env d --env t
        
        # Renew datalake SPN for all default environments (dry-run)
        mycli azure spn renew-secret 1381 datalake --dry-run
        
        # Renew SPN for production with 90-day expiration
        mycli azure spn renew-secret 1381 spnadl --env p --ttl 90
        
        # Renew with custom TTL and store appId
        mycli azure spn renew-secret 1381 spnadl --env d --ttl 180 --store-appid
        
        # Store both secret and appId for multiple environments
        mycli azure spn renew-secret 1381 datalake --env d --env t --store-appid
    """
    environments_dict = ENVIRONMENTS
    
    # Default to all environments if none specified
    if len(environments) < 1:
        environments = ("d", "t", "r", "p")
    
    # Validate environments
    for env in environments:
        if env not in environments_dict.keys():
            click.secho(f"❌ Environment prefix '{env}' not supported", fg="red", err=True)
            click.secho(f"   Available: {', '.join(environments_dict.keys())}", fg="yellow", err=True)
            raise click.Abort()
    
    # Get SPN type configuration
    spn_config = SPN_TYPES[spn_type]
    
    # Calculate end date
    end_date = datetime.now(timezone.utc) + timedelta(days=ttl)
    end_date_str = end_date.strftime("%Y-%m-%d")
    
    click.secho("\n" + "="*70, fg="cyan", bold=True)
    click.secho(f"🔐 Renewing {spn_config['description']}", fg="cyan", bold=True)
    click.secho(f"📦 Project Code: {project_code}", fg="cyan")
    click.secho(f"🌍 Environments: {', '.join(environments)}", fg="cyan")
    click.secho(f"🏷️  SPN Type: {spn_type}", fg="cyan")
    click.secho(f"⏱️  Secret TTL: {ttl} day(s) (expires: {end_date_str})", fg="cyan")
    if store_appid:
        click.secho(f"🆔 Store AppId: Yes", fg="cyan")
    if dry_run:
        click.secho(f"⚠️  DRY RUN MODE - No changes will be made", fg="yellow", bold=True)
    click.secho("="*70 + "\n", fg="cyan", bold=True)
    
    # Process each environment
    for env in environments:
        click.secho(f"\n{'='*70}", fg="magenta", bold=True)
        click.secho(f"🌍 Processing Environment: {env.upper()} ({environments_dict[env][0]})", fg="magenta", bold=True)
        click.secho(f"{'='*70}", fg="magenta", bold=True)
        
        # Derive SPN and Key Vault names
        spn_name = spn_config["name_pattern"].format(env=env, code=project_code)
        keyvault_name = f"{env}d9{project_code}akv01"
        secret_name = spn_config["secret_name"].format(env=env, code=project_code)
        
        click.secho(f"\n📝 SPN Name: {spn_name}", fg="white", bold=True)
        click.secho(f"🔑 Key Vault: {keyvault_name}", fg="white", bold=True)
        click.secho(f"🔒 Secret Name: {secret_name}", fg="white", bold=True)
        
        # Set subscription for this environment
        subscription_id = environments_dict[env][1]
        if not dry_run:
            click.secho(f"\n🔧 Setting subscription to {environments_dict[env][0]} ({subscription_id})...", fg="yellow")
            try:
                subprocess.run(
                    ["az", "account", "set", "--subscription", subscription_id],
                    check=True,
                    capture_output=True
                )
                click.secho(f"✅ Subscription set successfully", fg="green")
            except subprocess.CalledProcessError as e:
                click.secho(f"❌ Error setting subscription: {e.stderr.decode()}", fg="red", err=True)
                continue
        else:
            click.secho(f"\n⚠️  [DRY RUN] Would set subscription to {subscription_id}", fg="yellow")
        
        # Step 1: Get SPN Object ID/App ID
        click.secho(f"\n🔍 Looking up app '{spn_name}'...", fg="yellow")
        if not dry_run:
            try:
                result = subprocess.run(
                    ["az", "ad", "app", "list", 
                     "--display-name", spn_name,
                     "--query", "[0].{appId:appId,objectId:id}",
                     "-o", "json"],
                    capture_output=True,
                    text=True,
                    check=True
                )

                click.echo(result.stdout)
                spn_info = json.loads(result.stdout)
                app_id = spn_info.get("appId", "").strip()
                object_id = spn_info.get("objectId", "").strip()
                
                if not app_id or not object_id:
                    click.secho(f"❌ app '{spn_name}' not found", fg="red", err=True)
                    click.secho(f"   Skipping this environment...", fg="yellow", err=True)
                    continue
                
                click.secho(f"✅ Found app with App ID: {app_id}", fg="green")
                click.secho(f"   Object ID: {object_id}", fg="white", dim=True)
            except subprocess.CalledProcessError as e:
                click.secho(f"❌ Error looking up SPN: {e.stderr}", fg="red", err=True)
                continue
        else:
            click.secho(f"⚠️  [DRY RUN] Would look up SPN: {spn_name}", fg="yellow")
            app_id = "00000000-0000-0000-0000-000000000000"  # Placeholder for dry run
            object_id = "11111111-1111-1111-1111-111111111111"  # Placeholder for dry run
        
        # Step 2: Reset app credential (generate new secret)
        click.secho(f"\n🔄 Resetting app credential (expires in {ttl} day(s) on {end_date_str})...", fg="yellow")
        if not dry_run:
            try:
                # Reset the credential
                result = subprocess.run(
                    ["az", "ad", "app", "credential", "reset",
                     "--id", app_id,
                     "--end-date", end_date_str,
                     "-o", "json"],
                    capture_output=True,
                    text=True,
                    check=True
                )
                credential_info = json.loads(result.stdout)
                new_secret = credential_info.get("password", "").strip()
                
                if not new_secret:
                    click.secho(f"❌ Failed to generate new secret", fg="red", err=True)
                    continue
                
                click.secho(f"✅ New secret generated successfully (expires: {end_date_str}, {ttl} days)", fg="green")
                
                # Get credential ID from the credential list
                click.secho(f"   Fetching credential ID...", fg="white", dim=True)
                cred_list_result = subprocess.run(
                    ["az", "ad", "app", "credential", "list",
                     "--id", app_id,
                     "-o", "json"],
                    capture_output=True,
                    text=True,
                    check=True
                )
                credentials = json.loads(cred_list_result.stdout)
                
                # Get the most recent credential (should be the one we just created)
                if credentials:
                    # Sort by endDateTime descending to get the newest one
                    credentials_sorted = sorted(credentials, key=lambda x: x.get("endDateTime", ""), reverse=True)
                    credential_id = credentials_sorted[0].get("keyId", "").strip()
                    click.secho(f"   Credential ID: {credential_id}", fg="white", dim=True)
                else:
                    click.secho(f"⚠️  Warning: Could not retrieve credential ID", fg="yellow", err=True)
                    credential_id = ""
                
            except subprocess.CalledProcessError as e:
                click.secho(f"❌ Error resetting credential: {e.stderr}", fg="red", err=True)
                continue
        else:
            click.secho(f"⚠️  [DRY RUN] Would reset app credential for App ID: {app_id} with end date: {end_date_str} ({ttl} days)", fg="yellow")
            new_secret = "dry-run-secret-placeholder"
            credential_id = "22222222-2222-2222-2222-222222222222"  # Placeholder for dry run
        
        # Step 3: Store secret in Key Vault with tags
        click.secho(f"\n💾 Storing secret in Key Vault '{keyvault_name}' with metadata tags...", fg="yellow")
        if not dry_run:
            try:
                subprocess.run(
                    ["az", "keyvault", "secret", "set",
                     "--vault-name", keyvault_name,
                     "--name", secret_name,
                     "--value", new_secret,
                     "--tags",
                     f"ValidityPeriodDays={ttl}",
                     f"ProviderId={object_id}",
                     "Type=spn-secret",
                     f"CredentialId={credential_id}"],
                    capture_output=True,
                    text=True,
                    check=True
                )
                click.secho(f"✅ Secret '{secret_name}' stored successfully in Key Vault", fg="green")
                click.secho(f"   Tags: ValidityPeriodDays={ttl}, ProviderId={object_id}, Type=spn-secret, CredentialId={credential_id}", fg="white", dim=True)
            except subprocess.CalledProcessError as e:
                click.secho(f"❌ Error storing secret in Key Vault: {e.stderr}", fg="red", err=True)
                click.secho(f"⚠️  WARNING: Secret was renewed but not stored in Key Vault!", fg="red", bold=True, err=True)
                click.secho(f"   Please store the secret manually or retry the command.", fg="yellow", err=True)
                continue
        else:
            click.secho(f"⚠️  [DRY RUN] Would store secret in: {keyvault_name}/{secret_name}", fg="yellow")
            click.secho(f"   With tags: ValidityPeriodDays={ttl}, ProviderId={object_id}, Type=spn-secret, CredentialId={credential_id}", fg="white", dim=True)
        
        # Step 4: Store appId as a separate secret if requested
        if store_appid:
            appid_secret_name = spn_config["appid_secret_name"].format(env=env, code=project_code)
            click.secho(f"\n💾 Storing appId in Key Vault '{keyvault_name}'...", fg="yellow")
            if not dry_run:
                try:
                    subprocess.run(
                        ["az", "keyvault", "secret", "set",
                         "--vault-name", keyvault_name,
                         "--name", appid_secret_name,
                         "--value", app_id,
                         "--tags",
                         f"ProviderId={object_id}",
                         "Type=spn-appid"],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    click.secho(f"✅ AppId '{appid_secret_name}' stored successfully in Key Vault", fg="green")
                    click.secho(f"   Value: {app_id}", fg="white", dim=True)
                except subprocess.CalledProcessError as e:
                    click.secho(f"❌ Error storing appId in Key Vault: {e.stderr}", fg="red", err=True)
                    click.secho(f"⚠️  WARNING: AppId was not stored in Key Vault!", fg="yellow", err=True)
            else:
                click.secho(f"⚠️  [DRY RUN] Would store appId in: {keyvault_name}/{appid_secret_name}", fg="yellow")
                click.secho(f"   Value: {app_id}, Tags: ProviderId={object_id}, Type=spn-appid", fg="white", dim=True)
        
        click.secho(f"\n✅ Environment {env.upper()} completed successfully!\n", fg="green", bold=True)
    
    click.secho("\n" + "="*70, fg="cyan", bold=True)
    click.secho("✅ Secret renewal process completed!", fg="green", bold=True)
    click.secho("="*70 + "\n", fg="cyan", bold=True)
