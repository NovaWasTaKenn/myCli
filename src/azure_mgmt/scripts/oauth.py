"""OAuth setup for Azure AD app registrations."""

import click
import json

from helpers.env_config import require_environments
from helpers.shell import run_cmd, run_cmd_or_dry
from helpers.auth import elevated_spn

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
DEFAULT_PERMISSIONS = [
    "offline_access", "openid", "profile", "User.Read",
    "GroupMember.Read.All", "email",
]
LOCAL_REDIRECT_BASES = ["http://localhost:8000", "http://127.0.0.1:8000"]
LOCAL_ENV = "d"


def _with_suffix(url: str, suffix: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith(suffix) else f"{url}/{suffix}"


@click.command("setup-oauth")
@click.argument("extended-code", type=str)
@click.option("--webapp-code", "-w", default=None, show_default=True, help="Webapp extended code used in its name (d9{code}webapp{increment})")
@click.option("--increment", "-i", default="01", show_default=True, help="Webapp increment used in the webapp hostname")
@click.option("--env", "-e", "environments", multiple=True, help="Environment codes to process (can specify multiple)")
@click.option("--permission", "-p", "extra_permissions", multiple=True, help="Additional Microsoft Graph delegated permission")
@click.option("--redirect-url", "-r", "extra_redirect_urls", multiple=True, help="Additional redirect URL (callback suffix is appended)")
@click.option("--callback-suffix", default="oauth2/callback", show_default=True, help="Redirect URL path suffix")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
def setup_oauth(
    extended_code: str,
    webapp_code: str,
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
        mycli azure setup-oauth 1381 --env d --dry-run
        mycli azure setup-oauth 1381-i 02 -e d -e t -p Mail.Read -r https://my.app
    """
    environments_dict = require_config("azure_environments")
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

    webapp_code = webapp_code if webapp_code else extended_code

    # Read phase (as the current user): gather everything the writes will need.
    plans = []
    for env in environments:
        app_name = f"{env}d9{extended_code}spnadl"
        webapp_url = f"https://{env}d9{webapp_code}webapp{increment}.azurewebsites.net"
        bases = [webapp_url]
        if env == LOCAL_ENV:
            bases += LOCAL_REDIRECT_BASES
        bases += list(extra_redirect_urls)
        wanted_urls = list(dict.fromkeys(_with_suffix(b, callback_suffix) for b in bases))

        click.secho(f"\n{'=' * 70}", fg="magenta", bold=True)
        click.secho(f"🌍 {env.upper()} ({environments_dict[env]["name"]}) - app '{app_name}'", fg="magenta", bold=True)
        click.secho(f"{'=' * 70}", fg="magenta", bold=True)
        click.echo(f"Permissions: {', '.join(wanted)}")
        click.echo("Redirect URLs:\n  " + "\n  ".join(wanted_urls))

        # Read: select subscription (no dry-run)
        run_cmd(
            ["az", "account", "set", "--subscription", environments_dict[env][1]],
            error_msg="Error setting subscription",
            capture=False,
        )

        # Read: find the app registration (no dry-run)
        apps = json.loads(run_cmd(
            ["az", "ad", "app", "list", "--display-name", app_name,
             "--query", "[].{appId:appId,id:id}", "-o", "json"],
            error_msg=f"Error listing app '{app_name}'",
        ).stdout)
        if not apps:
            click.secho(f"❌ app '{app_name}' not found, skipping", fg="red", err=True)
            continue
        app_id = apps[0]["appId"]

        # Read: Microsoft Graph service principal (object id + delegated scopes)
        graph_sp = json.loads(run_cmd(
            ["az", "ad", "sp", "show", "--id", GRAPH_APP_ID,
             "--query", "{id:id,scopes:oauth2PermissionScopes[].{id:id,value:value}}", "-o", "json"],
            error_msg="Error fetching Microsoft Graph service principal",
        ).stdout)
        graph_sp_id = graph_sp["id"]
        scope_ids = {s["value"]: s["id"] for s in graph_sp["scopes"]}
        unknown = [p for p in wanted if p not in scope_ids]
        if unknown:
            click.secho(f"❌ Unknown Graph delegated permission(s): {', '.join(unknown)}", fg="red", err=True)
            continue

        # Read: the app's service principal object id (admin-consent client)
        app_sp_id = run_cmd(
            ["az", "ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv"],
            error_msg=f"Error fetching service principal for app '{app_name}'",
        ).stdout.strip()

        # Read: existing redirect URLs to merge with (no dry-run)
        existing = json.loads(run_cmd(
            ["az", "ad", "app", "show", "--id", app_id, "--query", "web.redirectUris", "-o", "json"],
            error_msg="Error fetching current redirect URLs",
        ).stdout)
        merged = list(dict.fromkeys([*(existing or []), *wanted_urls]))

        plans.append({
            "env": env,
            "app_name": app_name,
            "app_id": app_id,
            "app_sp_id": app_sp_id,
            "graph_sp_id": graph_sp_id,
            "scope_ids": scope_ids,
            "merged": merged,
        })

    if not plans:
        return

    # Write phase (elevated as the CICD SPN; permissions lowered on exit).
    with elevated_spn(dry_run=dry_run):
        for plan in plans:
            app_id, app_name = plan["app_id"], plan["app_name"]
            app_sp_id, graph_sp_id = plan["app_sp_id"], plan["graph_sp_id"]
            scope_ids, merged = plan["scope_ids"], plan["merged"]

            # Write: add API permissions (dry-run aware)
            api_permissions = [f"{scope_ids[p]}=Scope" for p in wanted]
            run_cmd_or_dry(
                ["az", "ad", "app", "permission", "add", "--id", app_id, "--api", GRAPH_APP_ID,
                 "--api-permissions", *api_permissions],
                dry_run=dry_run,
                dry_run_msg=(
                    f"Would add {len(wanted)} Graph delegated permission(s) on app {app_id} ({app_name})"
                    f" via API {GRAPH_APP_ID}:\n    "
                    + "\n    ".join(f"{p} ({scope_ids[p]}=Scope)" for p in wanted)
                ),
                error_msg="Error adding permissions",
                success_msg="Permissions added",
            )

            # Grant admin consent via Graph oauth2PermissionGrants.
            # The CLI's `admin-consent` rejects SPN tokens, so upsert the grant directly.
            grants = json.loads(run_cmd(
                ["az", "rest", "--method", "GET",
                 "--url", f"https://graph.microsoft.com/v1.0/servicePrincipals/{app_sp_id}/oauth2PermissionGrants"],
                error_msg="Error reading existing admin consent grants",
            ).stdout).get("value", [])
            grant = next((g for g in grants if g.get("resourceId") == graph_sp_id), None)
            granted_scope = " ".join(dict.fromkeys([*(grant or {}).get("scope", "").split(), *wanted]))

            if grant:
                run_cmd_or_dry(
                    ["az", "rest", "--method", "PATCH",
                     "--url", f"https://graph.microsoft.com/v1.0/oauth2PermissionGrants/{grant['id']}",
                     "--headers", "Content-Type=application/json",
                     "--body", json.dumps({"scope": granted_scope})],
                    dry_run=dry_run,
                    dry_run_msg=(
                        f"Would update admin consent grant {grant['id']} on SP {app_sp_id} ({app_name})"
                        f" → scope: {granted_scope}"
                    ),
                    error_msg="Error updating admin consent",
                    success_msg="Admin consent updated",
                )
            else:
                run_cmd_or_dry(
                    ["az", "rest", "--method", "POST",
                     "--url", "https://graph.microsoft.com/v1.0/oauth2PermissionGrants",
                     "--headers", "Content-Type=application/json",
                     "--body", json.dumps({
                         "clientId": app_sp_id,
                         "consentType": "AllPrincipals",
                         "resourceId": graph_sp_id,
                         "scope": granted_scope,
                     })],
                    dry_run=dry_run,
                    dry_run_msg=(
                        f"Would grant admin consent on SP {app_sp_id} ({app_name})"
                        f" for Graph {graph_sp_id} → scope: {granted_scope}"
                    ),
                    error_msg="Error granting admin consent",
                    success_msg="Admin consent granted",
                )

            # Write: update redirect URLs (dry-run aware)
            run_cmd_or_dry(
                ["az", "ad", "app", "update", "--id", app_id, "--web-redirect-uris", *merged],
                dry_run=dry_run,
                dry_run_msg=(
                    f"Would set {len(merged)} redirect URI(s) on app {app_id} ({app_name}):\n    "
                    + "\n    ".join(merged)
                ),
                error_msg="Error updating redirect URLs",
                success_msg="Redirect URLs updated",
            )
