"""Databricks management commands."""

import configparser
import json
import os
import subprocess
import time
from urllib import error, request
from urllib.parse import quote
from typing import Optional, Tuple

import click

from helpers.config import require_config

DEFAULT_HOST = "https://accounts.azuredatabricks.net"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ENV_PREFIXES = {"d", "t", "r", "p"}
TERMINAL_RUN_STATES = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR", "BLOCKED"}
RUN_POLL_INTERVAL_SECONDS = 10
RUN_TIMEOUT_SECONDS = 3600


def _strip_environment_prefix(resource_name: str) -> str:
    normalized = resource_name.lower()
    if normalized[:1] in ENV_PREFIXES and normalized[1:3] == "d9":
        return resource_name[1:]
    return resource_name


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
@click.option("--host", default=lambda: os.getenv("DATABRICKS_HOST"), show_default="env:DATABRICKS_HOST", help="Databricks account host")
@click.option("--token", default=lambda: os.getenv("DATABRICKS_TOKEN"), show_default="env:DATABRICKS_TOKEN", help="Databricks OAuth/PAT token")
@click.option("--profile", default=lambda: os.getenv("DATABRICKS_CONFIG_PROFILE", "DEFAULT"), show_default="env:DATABRICKS_CONFIG_PROFILE", help="Databricks CLI profile")
def patch_group_member(account_id: str, group: str, user: str, action: str, host: Optional[str], token: Optional[str], profile: str):
    """Add or remove a user from a Databricks account group using SCIM PATCH."""
    host = host or DEFAULT_HOST
    _, token = _resolve_connection(host, token, profile)

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


def _trigger_job(host: str, token: str, job_id: int, params: dict, params_key: str) -> int:
    """Trigger a Databricks job with a JSON payload and return the run_id."""
    url = f"{host.rstrip('/')}/api/2.1/jobs/run-now"
    body = {"job_id": job_id, "notebook_params": {params_key: json.dumps(params)}}
    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))["run_id"]


def _get_run(host: str, token: str, run_id: int) -> dict:
    url = f"{host.rstrip('/')}/api/2.1/jobs/runs/get?run_id={run_id}"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_run_output(host: str, token: str, run_id: int) -> dict:
    url = f"{host.rstrip('/')}/api/2.1/jobs/runs/get-output?run_id={run_id}"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_run(host: str, token: str, run_id: int) -> dict:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    previous_state = None

    while time.monotonic() < deadline:
        run = _get_run(host, token, run_id)
        state = run.get("state", {})
        life_cycle_state = state.get("life_cycle_state", "UNKNOWN")
        if life_cycle_state != previous_state:
            click.secho(f"  Run {run_id}: {life_cycle_state}", fg="cyan")
            previous_state = life_cycle_state
        if life_cycle_state in TERMINAL_RUN_STATES:
            return run
        time.sleep(RUN_POLL_INTERVAL_SECONDS)

    raise click.ClickException(f"Run {run_id} did not finish within {RUN_TIMEOUT_SECONDS // 60} minutes")


def _find_job_id(host: str, token: str, job_name: str) -> int:
    """Return the ID of the uniquely named Databricks job."""
    url = f"{host.rstrip('/')}/api/2.1/jobs/list?name={quote(job_name)}"
    req = request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with request.urlopen(req) as response:
            jobs = json.loads(response.read().decode("utf-8")).get("jobs", [])
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise click.ClickException(f"Job lookup failed with HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise click.ClickException(f"Failed to reach Databricks API: {exc.reason}") from exc

    exact_matches = [job for job in jobs if job.get("settings", {}).get("name") == job_name]
    if not exact_matches:
        raise click.ClickException(f"Databricks job '{job_name}' not found")
    if len(exact_matches) > 1:
        raise click.ClickException(f"Multiple Databricks jobs named '{job_name}' found")
    return exact_matches[0]["job_id"]


def _resolve_connection(profile: str) -> Tuple[str, str]:
    """Resolve host and token, using the Databricks CLI profile."""

    cfg_path = os.path.expanduser("~/.databrickscfg")
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)
    section = profile if cfg.has_section(profile) else "DEFAULT"
    host = cfg.get(section, "host", fallback=None)
    if not host:
        click.secho(f"❌ No host found for profile '{profile}' in {cfg_path}. Pass --host or configure the Databricks CLI.", fg="red", err=True)
        raise click.Abort()

    token = cfg.get(section, "token", fallback=None)
    if token:
        return host, token

    try:
        result = subprocess.run(
            ["databricks", "auth", "token", "--host", host, "--profile", profile],
            capture_output=True, text=True, check=True
        )
        token = json.loads(result.stdout)["access_token"]
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
        click.secho(f"❌ Failed to obtain token from Databricks CLI: {e}", fg="red", err=True)
        click.secho("💡 Run 'databricks auth login' to configure your profile.", fg="yellow", err=True)
        raise click.Abort()

    return host, token


def _run_mount_job(host: str, token: str, job_id: int, params: dict, dry_run: bool) -> Optional[int]:
    payload_str = json.dumps(params, indent=2)
    click.secho(f"\n📋 Job payload list_mount_point_string:", fg="cyan")
    click.echo(payload_str)
    if dry_run:
        click.secho("\n⚠️  [DRY RUN] Job not triggered.", fg="yellow")
        return None
    try:
        run_id = _trigger_job(host, token, job_id, params, "list_mount_point_string")
        click.secho(f"\n✅ Job triggered successfully — run_id: {run_id}", fg="green")
        return run_id
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        click.secho(f"❌ Databricks API failed with HTTP {exc.code}", fg="red", err=True)
        if body.strip():
            click.echo(body, err=True)
        raise click.Abort()
    except error.URLError as exc:
        click.secho(f"❌ Failed to reach Databricks API: {exc.reason}", fg="red", err=True)
        raise click.Abort()


def _report_mount_jobs(runs: list[Tuple[str, str, int, str]]) -> None:
    failed = False
    for host, token, run_id, environment in runs:
        click.secho(f"\n🌍 Checking {environment.upper()} run {run_id}", fg="magenta", bold=True)
        try:
            run = _wait_for_run(host, token, run_id)
            state = run.get("state", {})
            result_state = state.get("result_state")
            if result_state == "SUCCESS":
                click.secho(f"✅ Run {run_id} completed successfully", fg="green")
                continue

            failed = True
            state_message = state.get("state_message")
            click.secho(f"❌ Run {run_id} failed: {result_state or state.get('life_cycle_state', 'UNKNOWN')}", fg="red", err=True)
            if state_message:
                click.echo(state_message, err=True)
            try:
                output = _get_run_output(host, token, run_id)
                if output.get("error") and output["error"] != state_message:
                    click.echo(output["error"], err=True)
                if output.get("error_trace"):
                    click.echo(output["error_trace"], err=True)
            except (error.HTTPError, error.URLError):
                pass
        except (click.ClickException, error.HTTPError, error.URLError) as exc:
            failed = True
            click.secho(f"❌ Could not get result for run {run_id}: {exc}", fg="red", err=True)

    if failed:
        raise click.Abort()


@databricks.command(name="mount-blob")
@click.argument("storage_account", type=str, metavar="STORAGE_ACCOUNT")
@click.argument("container", type=str)
@click.argument("mount_path", type=str)
@click.argument("project_code", type=str)
@click.option("--envs", multiple=True, default=("d", "t", "r", "p"), show_default=True, help="Environment prefixes to process")
@click.option("--dry-run", is_flag=True, help="Print the job payload without triggering the job")
@click.option("--force", default="false", show_default=True, type=click.Choice(["true", "false"]), help="Force remount if already mounted")
def mount_blob(
    storage_account: str,
    container: str,
    mount_path: str,
    project_code: str,
    envs: Tuple[str, ...],
    dry_run: bool,
    force: bool
):
    """Trigger a Databricks job to mount a Blob Storage container via SAS key.

    STORAGE_ACCOUNT may omit the environment prefix or include one that will be replaced.

    Requires a databricks workspace named after the workspace for authentication

    Example:
        mycli databricks mount-blob 'Mount blob {environment}' d90021sa01 mycontainer mydata d91231 --envs d --envs t
    """
    environments = require_config("azure_environments")


    storage_account_suffix = _strip_environment_prefix(storage_account)
    job_name = "Job_Mount_Blob_Serverless"

    click.secho(f"\n{'='*60}", fg="cyan", bold=True)
    click.secho(f"💡 Job name: {job_name} | Environments: {', '.join(envs)}", fg="cyan")
    click.secho(f"{'='*60}", fg="cyan", bold=True)

    runs = []
    for env_short in envs:

        profile = f"{env_short}{project_code}adb01"

        host, token = _resolve_connection(profile)

        if env_short not in environments:
            click.secho(f"❌ Unknown environment prefix '{env_short}' — skipping", fg="red", err=True)
            continue
        job_id = _find_job_id(host, token, job_name)
        storage_account_name = f"{env_short}{storage_account_suffix}"
        click.secho(f"\n🌍 {env_short.upper()}", fg="magenta", bold=True)
        click.secho(f"  Job: {job_name} ({job_id})", fg="cyan")
        click.secho(f"  Storage Account: {storage_account_name} | Container: {container} | Mount: {mount_path}", fg="cyan")
        params = {
            "config": {
                "secretScope_name": f"SecretScope-{env_short}{project_code}-databricks",
            },
            "list_mount": [
                {
                    "name": f"{mount_path}",
                    "source": f"wasbs://{container}@{storage_account_name}.blob.core.windows.net/",
                    "sas_key": f"{storage_account_name}-{container}-sas",
                    "force": force,
                    "container": container,
                    "storage": storage_account_name
                }
            ],
        }
        run_id = _run_mount_job(host, token, job_id, params, dry_run)
        if run_id is not None:
            runs.append((host, token, run_id, env_short))

    _report_mount_jobs(runs)


@databricks.command(name="mount-datalake")
@click.argument("datalake", type=str, metavar="DATALAKE")
@click.argument("container", type=str)
@click.argument("mount_path", type=str)
@click.argument("project_code", type=str)
@click.option("--envs", multiple=True, default=("d", "t", "r", "p"), show_default=True, help="Environment prefixes to process")
@click.option("--force", default="false", show_default=True, type=click.Choice(["true", "false"]), help="Force remount if already mounted")
@click.option("--profile", default=lambda: os.getenv("DATABRICKS_CONFIG_PROFILE", "DEFAULT"), show_default="env:DATABRICKS_CONFIG_PROFILE", help="Databricks CLI profile")
@click.option("--dry-run", is_flag=True, help="Print the job payload without triggering the job")
def mount_datalake(
    datalake: str,
    container: str,
    mount_path: str,
    project_code: str,
    envs: Tuple[str, ...],
    force: str,
    profile: str,
    dry_run: bool,
):
    """Trigger a Databricks job to mount an ADLS Gen2 container via service principal.

    DATALAKE may omit the environment prefix or include one that will be replaced.

    Example:
        mycli databricks mount-datalake 'Mount datalake {environment}' d90021adl01 myfs mydata myproj --envs d --envs t
    """
    environments = require_config("azure_environments")
    host, token = _resolve_connection(profile)
    datalake_suffix = _strip_environment_prefix(datalake)
    job_name = "Job_Mount_Datalake_Serverless"

    click.secho(f"\n{'='*60}", fg="cyan", bold=True)
    click.secho(f"💡 Job name: {job_name} | Environments: {', '.join(envs)}", fg="cyan")
    click.secho(f"{'='*60}", fg="cyan", bold=True)

    runs = []
    for env_short in envs:
        if env_short not in environments:
            click.secho(f"❌ Unknown environment prefix '{env_short}' — skipping", fg="red", err=True)
            continue
        env_display = environments[env_short]["name"]
        job_id = _find_job_id(host, token, job_name)
        datalake_name = f"{env_short}{datalake_suffix}"
        click.secho(f"\n🌍 {env_short.upper()} ({env_display})", fg="magenta", bold=True)
        click.secho(f"  Job: {job_name} ({job_id})", fg="cyan")
        click.secho(f"  Datalake: {datalake_name} | Container: {container} | Mount: {mount_path}", fg="cyan")
        params = {
            "config": {
                "clientId_key": f"{env_short}{project_code}spnadl-clientID",
                "clientSecret_key": f"{env_display}{project_code}spnadl-secret",
                "secretScope_key": f"SecretScope-{env_display}{project_code}akv01",
            },
            "list_mount": [
                {
                    "name": f"{mount_path}",
                    "source": f"abfss://{container}@{datalake_name}.dfs.core.windows.net/",
                    "force": force,
                }
            ],
        }
        run_id = _run_mount_job(host, token, job_id, params, dry_run)
        if run_id is not None:
            runs.append((host, token, run_id, env_short))

    _report_mount_jobs(runs)

