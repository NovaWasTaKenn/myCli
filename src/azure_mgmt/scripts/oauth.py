"""OAuth setup for Azure AD app registrations."""

import click
import subprocess
import json
from typing import List, Optional

from helpers.env_config import require_environments

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
DEFAULT_PERMISSIONS = [
    "offline_access", "openid", "profile", "User.Read",
    "GroupMember.Read.All", "email",
]
LOCAL_REDIRECT_BASES = ["http://localhost:8000", "http://127.0.0.1:8000"]
LOCAL_ENV = "d"


def _az(args: List[str]) -> str:
    result = subprocess.run(["az", *args], capture_output=True, text=True, check=True)
    return result.stdout


def _with_suffix(url: str, suffix: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith(suffix) else f"{url}/{suffix}"


@click.command("setup-oauth")
@click.argument("extended-code", type=str)
@click.option("--increment", "-i", default="01", show_default=True, help="Webapp increment used in the webapp hostname")
@click.option("--env", "-e", "environments", multiple=True, help="Environment codes to process (can specify multiple)")
@click.option("--permission", "-p", "extra_permissions", multiple=True, help="Additional Microsoft Graph delegated permission")
@click.option("--redirect-url", "-r", "extra_redirect_urls", multiple=True, help="Additional redirect URL (callback suffix is appended)")
@click.option("--callback-suffix", default="oauth2/callback", show_default=True, help="Redirect URL path suffix")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
def setup_oauth(
    extended_code: str,
    increment: str,
    environments: tuple,
    extra_permissions: tuple,
    extra_redirect_urls: tuple,
    callback_suffix: str,
    dry_run: bool,
):
    """
    Configure API permissions, admin consent and redirect URLs on the project app registration.

    EXTENDED_CODE is the project code followed by the project increment (e.g. 138101).
    The app is named {env}d9{EXTENDED_CODE}spnadl.

    Redirect URLs: every env gets https://{env}d9{code}webapp{increment}.azurewebsites.net/{suffix};
    env 'd' also gets http://localhost:8000 and http://127.0.0.1:8000 (with suffix).

    Examples:
        mycli azure setup-oauth 138101 --env d --dry-run
        mycli azure setup-oauth 138101 -i 02 -e d -e t -p Mail.Read -r https://my.app
    """
    environments_dict = require_environments()
    if not environments:
        environments = ("d", "t", "r", "p")

    for env in environments:
        if env not in environments_dict:
            click.secho(f"❌ Environment prefix '{env}' not supported", fg="red", err=True)
            click.secho(f"   Available: {', '.join(environments_dict.keys())}", fg="yellow", err=True)
            raise click.Abort()

    wanted = list(dict.fromkeys([*DEFAULT_PERMISSIONS, *extra_permissions]))
    if dry_run:
        click.secho("⚠️  DRY RUN MODE - No changes will be made", fg="yellow", bold=True)

    for env in environments:
        app_name = f"{env}d9{extended_code}spnadl"
        webapp_url = f"https://{env}d9{extended_code}webapp{increment}.azurewebsites.net"
        bases = [webapp_url]
        if env == LOCAL_ENV:
            bases += LOCAL_REDIRECT_BASES
        bases += list(extra_redirect_urls)
        wanted_urls = list(dict.fromkeys(_with_suffix(b, callback_suffix) for b in bases))

        click.secho(f"\n{'=' * 70}", fg="magenta", bold=True)
        click.secho(f"🌍 {env.upper()} ({environments_dict[env][0]}) - app '{app_name}'", fg="magenta", bold=True)
        click.secho(f"{'=' * 70}", fg="magenta", bold=True)
        click.echo(f"Permissions: {', '.join(wanted)}")
        click.echo("Redirect URLs:\n  " + "\n  ".join(wanted_urls))

        if dry_run:
            click.secho("⚠️  [DRY RUN] Would set subscription, add permissions, grant admin consent and update redirect URLs", fg="yellow")
            continue

        try:
            _az(["account", "set", "--subscription", environments_dict[env][1]])

            apps = json.loads(_az(["ad", "app", "list", "--display-name", app_name,
                                   "--query", "[].{appId:appId,id:id}", "-o", "json"]))
            if not apps:
                click.secho(f"❌ app '{app_name}' not found, skipping", fg="red", err=True)
                continue
            app_id = apps[0]["appId"]

            scopes = json.loads(_az(["ad", "sp", "show", "--id", GRAPH_APP_ID,
                                     "--query", "oauth2PermissionScopes[].{id:id,value:value}", "-o", "json"]))
            scope_ids = {s["value"]: s["id"] for s in scopes}
            unknown = [p for p in wanted if p not in scope_ids]
            if unknown:
                click.secho(f"❌ Unknown Graph delegated permission(s): {', '.join(unknown)}", fg="red", err=True)
                continue

            _az(["ad", "app", "permission", "add", "--id", app_id, "--api", GRAPH_APP_ID,
                 "--api-permissions", *[f"{scope_ids[p]}=Scope" for p in wanted]])
            click.secho("✅ Permissions added", fg="green")

            _az(["ad", "app", "permission", "admin-consent", "--id", app_id])
            click.secho("✅ Admin consent granted", fg="green")

            existing: Optional[List[str]] = json.loads(
                _az(["ad", "app", "show", "--id", app_id, "--query", "web.redirectUris", "-o", "json"])
            )
            merged = list(dict.fromkeys([*(existing or []), *wanted_urls]))
            _az(["ad", "app", "update", "--id", app_id, "--web-redirect-uris", *merged])
            click.secho("✅ Redirect URLs updated", fg="green")
        except subprocess.CalledProcessError as e:
            click.secho(f"❌ az command failed: {e.stderr}", fg="red", err=True)
            continue
