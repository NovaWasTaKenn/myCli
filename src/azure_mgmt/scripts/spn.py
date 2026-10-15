"""Service Principal (SPN) management commands."""

import click
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

from helpers.azure import resolve_environments, set_subscription
from helpers.shell import run_cmd_or_dry, run_json
from helpers.ui import banner


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


def _lookup_spn(spn_name: str, dry_run: bool) -> Tuple[str, str]:
    """Return the (app_id, object_id) of an app registration (placeholders in dry-run)."""
    if dry_run:
        click.secho(f"⚠️  [DRY RUN] Would look up SPN: {spn_name}", fg="yellow")
        return "00000000-0000-0000-0000-000000000000", "11111111-1111-1111-1111-111111111111"

    spn_info = run_json(
        ["az", "ad", "app", "list",
         "--display-name", spn_name,
         "--query", "[0].{appId:appId,objectId:id}",
         "-o", "json"],
        error_msg=f"Error looking up app '{spn_name}'",
    ) or {}
    app_id = (spn_info.get("appId") or "").strip()
    object_id = (spn_info.get("objectId") or "").strip()

    if not app_id or not object_id:
        click.secho(f"❌ app '{spn_name}' not found", fg="red", err=True)
        raise click.Abort()

    click.secho(f"✅ Found app with App ID: {app_id}", fg="green")
    click.secho(f"   Object ID: {object_id}", fg="white", dim=True)
    return app_id, object_id


def _reset_spn_credential(app_id: str, end_date_str: str, ttl: int, dry_run: bool) -> Tuple[str, str]:
    """Reset the app credential and return (new_secret, credential_id) (placeholders in dry-run)."""
    if dry_run:
        click.secho(f"⚠️  [DRY RUN] Would reset app credential for App ID: {app_id} with end date: {end_date_str} ({ttl} days)", fg="yellow")
        return "dry-run-secret-placeholder", "22222222-2222-2222-2222-222222222222"

    new_secret = run_json(
        ["az", "ad", "app", "credential", "reset", "--id", app_id, "--end-date", end_date_str, "-o", "json"],
        error_msg="Error resetting credential",
    ).get("password", "").strip()
    if not new_secret:
        click.secho("❌ Failed to generate new secret", fg="red", err=True)
        raise click.Abort()
    click.secho(f"✅ New secret generated successfully (expires: {end_date_str}, {ttl} days)", fg="green")

    # Get the credential ID of the secret we just created (the one expiring last)
    click.secho("   Fetching credential ID...", fg="white", dim=True)
    credentials = run_json(
        ["az", "ad", "app", "credential", "list", "--id", app_id, "-o", "json"],
        error_msg="Error listing credentials",
    )
    if not credentials:
        click.secho("⚠️  Warning: Could not retrieve credential ID", fg="yellow", err=True)
        return new_secret, ""

    latest = max(credentials, key=lambda c: c.get("endDateTime", ""))
    credential_id = latest.get("keyId", "").strip()
    click.secho(f"   Credential ID: {credential_id}", fg="white", dim=True)
    return new_secret, credential_id


def _store_secret(keyvault_name: str, secret_name: str, value: str, tags: list, dry_run: bool) -> None:
    """Store a secret with metadata tags in a Key Vault."""
    run_cmd_or_dry(
        ["az", "keyvault", "secret", "set",
         "--vault-name", keyvault_name,
         "--name", secret_name,
         "--value", value,
         "--tags", *tags],
        dry_run=dry_run,
        dry_run_msg=(
            f"Would store secret in: {keyvault_name}/{secret_name}\n"
            f"   With tags: {', '.join(tags)}"
        ),
        error_msg=(
            f"Error storing secret '{secret_name}' in Key Vault '{keyvault_name}' — "
            "the value was generated but NOT stored; store it manually or retry"
        ),
        success_msg=f"Secret '{secret_name}' stored successfully in Key Vault",
    )
    click.secho(f"   Tags: {', '.join(tags)}", fg="white", dim=True)
    if not dry_run and secret_name.endswith("clientID"):
        click.secho(f"   Value: {value}", fg="white", dim=True)


def _renew_secret_for_env(
    env: str,
    env_cfg: Dict[str, Any],
    spn_config: Dict[str, str],
    project_code: str,
    ttl: int,
    end_date_str: str,
    store_appid: bool,
    dry_run: bool,
) -> None:
    """Renew one SPN's secret in one environment. Aborts on error."""
    spn_name = spn_config["name_pattern"].format(env=env, code=project_code)
    keyvault_name = f"{env}d9{project_code}akv01"
    secret_name = spn_config["secret_name"].format(env=env, code=project_code)

    banner(f"🌍 Processing Environment: {env.upper()} ({env_cfg['name']})", fg="magenta", width=70)
    click.secho(f"📝 SPN Name: {spn_name}", fg="white", bold=True)
    click.secho(f"🔑 Key Vault: {keyvault_name}", fg="white", bold=True)
    click.secho(f"🔒 Secret Name: {secret_name}\n", fg="white", bold=True)

    set_subscription(env_cfg, dry_run)

    click.secho(f"\n🔍 Looking up app '{spn_name}'...", fg="yellow")
    app_id, object_id = _lookup_spn(spn_name, dry_run)

    click.secho(f"\n🔄 Resetting app credential (expires in {ttl} day(s) on {end_date_str})...", fg="yellow")
    new_secret, credential_id = _reset_spn_credential(app_id, end_date_str, ttl, dry_run)

    click.secho(f"\n💾 Storing secret in Key Vault '{keyvault_name}' with metadata tags...", fg="yellow")
    _store_secret(
        keyvault_name,
        secret_name,
        new_secret,
        [
            f"ValidityPeriodDays={ttl}",
            f"ProviderId={object_id}",
            "Type=spn-secret",
            f"CredentialId={credential_id}",
        ],
        dry_run,
    )

    if store_appid:
        appid_secret_name = spn_config["appid_secret_name"].format(env=env, code=project_code)
        click.secho(f"\n💾 Storing appId in Key Vault '{keyvault_name}'...", fg="yellow")
        _store_secret(
            keyvault_name,
            appid_secret_name,
            app_id,
            [f"ProviderId={object_id}", "Type=spn-appid"],
            dry_run,
        )


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
        # Renew project SPN secret for dev and test environments
        mycli azure spn renew-secret 1381 spnadl --env d --env t

        # Renew devops SPN for all default environments (dry-run)
        mycli azure spn renew-secret 1381 devops --dry-run

        # Renew SPN for production with 90-day expiration
        mycli azure spn renew-secret 1381 spnadl --env p --ttl 90

        # Renew with custom TTL and store appId
        mycli azure spn renew-secret 1381 spnadl --env d --ttl 180 --store-appid
    """
    envs = resolve_environments(environments)
    spn_config = SPN_TYPES[spn_type]
    end_date_str = (datetime.now(timezone.utc) + timedelta(days=ttl)).strftime("%Y-%m-%d")

    click.secho("\n" + "="*70, fg="cyan", bold=True)
    click.secho(f"🔐 Renewing {spn_config['description']}", fg="cyan", bold=True)
    click.secho(f"📦 Project Code: {project_code}", fg="cyan")
    click.secho(f"🌍 Environments: {', '.join(envs)}", fg="cyan")
    click.secho(f"🏷️  SPN Type: {spn_type}", fg="cyan")
    click.secho(f"⏱️  Secret TTL: {ttl} day(s) (expires: {end_date_str})", fg="cyan")
    if store_appid:
        click.secho("🆔 Store AppId: Yes", fg="cyan")
    if dry_run:
        click.secho("⚠️  DRY RUN MODE - No changes will be made", fg="yellow", bold=True)
    click.secho("="*70 + "\n", fg="cyan", bold=True)

    # Process each environment, skipping to the next one on error
    for env, env_cfg in envs.items():
        try:
            _renew_secret_for_env(env, env_cfg, spn_config, project_code, ttl, end_date_str, store_appid, dry_run)
        except click.Abort:
            click.secho(f"⚠️  Skipping environment {env.upper()} due to previous error", fg="yellow", err=True)
            continue
        click.secho(f"\n✅ Environment {env.upper()} completed successfully!\n", fg="green", bold=True)

    banner("✅ Secret renewal process completed!", fg="green", width=70)
