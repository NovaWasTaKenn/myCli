"""Databricks management commands."""

import json
import os
from urllib import error, request
from urllib.parse import quote

import click


DEFAULT_HOST = "https://accounts.azuredatabricks.net"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _scim_get(host: str, path: str, token: str) -> dict:
    url = f"{host.rstrip('/')}{path}"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def _lookup_group_id(host: str, account_id: str, token: str, group_name: str) -> str:
    data = _scim_get(host, f"/api/2.0/accounts/{account_id}/scim/v2/Groups?filter={quote(f'displayName eq \"{group_name}\"')}", token)
    resources = data.get("Resources", [])
    if not resources:
        raise click.ClickException(f"Group '{group_name}' not found")
    return resources[0]["id"]


def _lookup_user_id(host: str, account_id: str, token: str, username: str) -> str:
    data = _scim_get(host, f"/api/2.0/accounts/{account_id}/scim/v2/Users?filter={quote(f'userName eq \"{username}\"')}", token)
    resources = data.get("Resources", [])
    if not resources:
        raise click.ClickException(f"User '{username}' not found")
    return resources[0]["id"]


@click.group()
def databricks():
    """Databricks management commands"""
    pass


@databricks.command(name="patch-group-member")
@click.option("--account-id", default=lambda: os.getenv("DATABRICKS_ACCOUNT_ID"), show_default="env:DATABRICKS_ACCOUNT_ID", required=True, help="Azure Databricks account ID")
@click.option("--group", required=True, help="Databricks group display name")
@click.option("--user", required=True, help="Databricks username (email)")
@click.option("--action", type=click.Choice(["add", "delete"], case_sensitive=False), required=True, help="Whether to add or delete the user from the group")
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Databricks account host")
@click.option("--token", default=lambda: os.getenv("DATABRICKS_TOKEN"), show_default="env:DATABRICKS_TOKEN", help="Databricks OAuth/PAT token")
def patch_group_member(account_id: str, group: str, user: str, action: str, host: str, token: str):
    """Add or remove a user from a Databricks account group using SCIM PATCH."""
    if not token:
        click.secho("❌ Missing Databricks token. Set DATABRICKS_TOKEN or pass --token.", fg="red", err=True)
        raise click.Abort()

    try:
        click.secho(f"Looking up group '{group}'...", fg="cyan")
        group_id = _lookup_group_id(host, account_id, token, group)
        click.secho(f"Looking up user '{user}'...", fg="cyan")
        user_id = _lookup_user_id(host, account_id, token, user)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        click.secho(f"❌ SCIM lookup failed with HTTP {exc.code}", fg="red", err=True)
        if body.strip():
            click.echo(body, err=True)
        raise click.Abort()
    except error.URLError as exc:
        click.secho(f"❌ Failed to reach Databricks API: {exc.reason}", fg="red", err=True)
        raise click.Abort()

    if action == "add":
        operations = [{"op": "add", "path": "members", "value": [{"value": user_id}]}]
    else:
        operations = [{"op": "remove", "path": f'members[value eq "{user_id}"]'}]

    url = f"{host.rstrip('/')}/api/2.0/accounts/{account_id}/scim/v2/Groups/{group_id}"
    payload = json.dumps({"schemas": [PATCH_SCHEMA], "Operations": operations}).encode("utf-8")

    req = request.Request(
        url,
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/scim+json",
            "Accept": "application/json",
        },
    )

    click.secho(f"Patching group '{group}' ({group_id}) for user '{user}' ({user_id})...", fg="cyan")

    try:
        with request.urlopen(req) as response:
            click.secho(f"✅ User {action}ed successfully", fg="green")
            if response.status != 204:
                body = response.read().decode("utf-8", errors="replace")
                if body.strip():
                    click.echo(body)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        click.secho(f"❌ Databricks API request failed with HTTP {exc.code}", fg="red", err=True)
        if body.strip():
            click.echo(body, err=True)
        raise click.Abort()
    except error.URLError as exc:
        click.secho(f"❌ Failed to reach Databricks API: {exc.reason}", fg="red", err=True)
        raise click.Abort()
