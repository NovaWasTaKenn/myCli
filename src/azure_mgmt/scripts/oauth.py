"""OAuth setup for Azure AD app registrations."""

import click
import json

from helpers.auth import elevated_cicd_spn
from helpers.azure import resolve_environments, set_subscription
from helpers.shell import run_cmd_or_dry, run_json, run_text
from helpers.ui import banner

GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
GRAPH_URL = "https://graph.microsoft.com/v1.0"
DEFAULT_PERMISSIONS = [
    "offline_access", "openid", "profile", "User.Read",
    "GroupMember.Read.All", "email",
]
LOCAL_REDIRECT_BASES = ["http://localhost:8000", "http://127.0.0.1:8000"]
LOCAL_ENV = "d"


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
    envs = resolve_environments(environments)
    wanted = list(dict.fromkeys([*DEFAULT_PERMISSIONS, *extra_permissions]))
    webapp_code = webapp_code or extended_code
    if dry_run:
        click.secho("⚠️  DRY RUN MODE - No changes will be made", fg="yellow", bold=True)

    # Read phase (as the current user)
    plans = []
    for env, env_cfg in envs.items():
        app_name = f"{env}d9{extended_code}spnadl"
        webapp_url = f"https://{env}d9{webapp_code}webapp{increment}.azurewebsites.net"
        bases = [webapp_url, *(LOCAL_REDIRECT_BASES if env == LOCAL_ENV else []), *extra_redirect_urls]
        wanted_urls = list(dict.fromkeys(
            b.rstrip("/") if b.rstrip("/").endswith(callback_suffix) else f"{b.rstrip('/')}/{callback_suffix}"
            for b in bases
        ))

        banner(f"🌍 {env.upper()} ({env_cfg['name']}) - app '{app_name}'", fg="magenta", width=70)
        click.echo(f"Permissions: {', '.join(wanted)}")
        click.echo("Redirect URLs:\n  " + "\n  ".join(wanted_urls))

        set_subscription(env_cfg)

        apps = run_json(
            ["az", "ad", "app", "list", "--display-name", app_name,
             "--query", "[].{appId:appId,id:id}", "-o", "json"],
            error_msg=f"Error listing app '{app_name}'",
        )
        if not apps:
            click.secho(f"❌ app '{app_name}' not found, skipping", fg="red", err=True)
            continue
        app_id = apps[0]["appId"]

        graph_sp = run_json(
            ["az", "ad", "sp", "show", "--id", GRAPH_APP_ID,
             "--query", "{id:id,scopes:oauth2PermissionScopes[].{id:id,value:value}}", "-o", "json"],
            error_msg="Error fetching Microsoft Graph service principal",
        )
        scope_ids = {s["value"]: s["id"] for s in graph_sp["scopes"]}
        unknown = [p for p in wanted if p not in scope_ids]
        if unknown:
            click.secho(f"❌ Unknown Graph delegated permission(s): {', '.join(unknown)}", fg="red", err=True)
            continue

        app_sp_id = run_text(
            ["az", "ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "tsv"],
            error_msg=f"Error fetching service principal for app '{app_name}'",
        )

        existing_urls = run_json(
            ["az", "ad", "app", "show", "--id", app_id, "--query", "web.redirectUris", "-o", "json"],
            error_msg="Error fetching current redirect URLs",
        )

        plans.append({
            "app_name": app_name,
            "app_id": app_id,
            "app_sp_id": app_sp_id,
            "graph_sp_id": graph_sp["id"],
            "scope_ids": scope_ids,
            "redirect_urls": list(dict.fromkeys([*(existing_urls or []), *wanted_urls])),
        })

    if not plans:
        return

    # Write phase (elevated as the CICD SPN; permissions lowered on exit)
    with elevated_cicd_spn(dry_run=dry_run):
        for plan in plans:
            app_name, app_id = plan["app_name"], plan["app_id"]
            app_sp_id, graph_sp_id = plan["app_sp_id"], plan["graph_sp_id"]
            scope_ids, urls = plan["scope_ids"], plan["redirect_urls"]

            run_cmd_or_dry(
                ["az", "ad", "app", "permission", "add", "--id", app_id, "--api", GRAPH_APP_ID,
                 "--api-permissions", *(f"{scope_ids[p]}=Scope" for p in wanted)],
                dry_run=dry_run,
                dry_run_msg=(
                    f"Would add {len(wanted)} Graph delegated permission(s) on app {app_id} ({app_name})"
                    f" via API {GRAPH_APP_ID}:\n    "
                    + "\n    ".join(f"{p} ({scope_ids[p]}=Scope)" for p in wanted)
                ),
                error_msg="Error adding permissions",
                success_msg="Permissions added",
            )

            # Upsert the Graph oauth2PermissionGrant (the CLI's `admin-consent` rejects SPN tokens)
            grants = run_json(
                ["az", "rest", "--method", "GET",
                 "--url", f"{GRAPH_URL}/servicePrincipals/{app_sp_id}/oauth2PermissionGrants"],
                error_msg="Error reading existing admin consent grants",
            ).get("value", [])
            grant = next((g for g in grants if g.get("resourceId") == graph_sp_id), None)
            scope = " ".join(dict.fromkeys([*(grant or {}).get("scope", "").split(), *wanted]))

            if grant:
                method, url, body = "PATCH", f"{GRAPH_URL}/oauth2PermissionGrants/{grant['id']}", {"scope": scope}
                action = f"update admin consent grant {grant['id']}"
            else:
                method, url = "POST", f"{GRAPH_URL}/oauth2PermissionGrants"
                body = {"clientId": app_sp_id, "consentType": "AllPrincipals", "resourceId": graph_sp_id, "scope": scope}
                action = "grant admin consent"

            run_cmd_or_dry(
                ["az", "rest", "--method", method, "--url", url,
                 "--headers", "Content-Type=application/json", "--body", json.dumps(body)],
                dry_run=dry_run,
                dry_run_msg=f"Would {action} on SP {app_sp_id} ({app_name}) → scope: {scope}",
                error_msg=f"Error trying to {action}",
                success_msg=f"Admin consent {'updated' if grant else 'granted'}",
            )

            run_cmd_or_dry(
                ["az", "ad", "app", "update", "--id", app_id, "--web-redirect-uris", *urls],
                dry_run=dry_run,
                dry_run_msg=(
                    f"Would set {len(urls)} redirect URI(s) on app {app_id} ({app_name}):\n    "
                    + "\n    ".join(urls)
                ),
                error_msg="Error updating redirect URLs",
                success_msg="Redirect URLs updated",
            )
