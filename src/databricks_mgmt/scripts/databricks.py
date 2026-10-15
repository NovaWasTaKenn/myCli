"""Databricks management commands."""

import configparser
import json
import os
import time
from urllib import error, request
from urllib.parse import quote
from typing import Any, Dict, List, Optional, Tuple

import click

from helpers.azure import resolve_environments, strip_environment_prefix
from helpers.shell import run_json

DEFAULT_HOST = "https://accounts.azuredatabricks.net"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
TERMINAL_RUN_STATES = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR", "BLOCKED"}
RUN_POLL_INTERVAL_SECONDS = 10
RUN_TIMEOUT_SECONDS = 3600


def _api(
    host: str,
    token: str,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    content_type: str = "application/json",
) -> dict:
    """Call a Databricks REST endpoint and return its JSON response ({} if empty)."""
    req = request.Request(
        f"{host.rstrip('/')}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": content_type},
    )
    try:
        with request.urlopen(req) as response:
            payload = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise click.ClickException(f"Databricks API {method} {path} failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise click.ClickException(f"Failed to reach Databricks API: {exc.reason}") from exc
    return json.loads(payload) if payload.strip() else {}


def _resolve_connection(profile: str) -> Tuple[str, str]:
    """Resolve host and token from a Databricks CLI profile."""
    cfg_path = os.path.expanduser("~/.databrickscfg")
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)
    section = profile if cfg.has_section(profile) else "DEFAULT"
    host = cfg.get(section, "host", fallback=None)
    if not host:
        raise click.ClickException(f"No host found for profile '{profile}' in {cfg_path}. Configure the Databricks CLI.")

    token = cfg.get(section, "token", fallback=None)
    if token:
        return host, token

    token = (run_json(
        ["databricks", "auth", "token", "--host", host, "--profile", profile],
        error_msg="Failed to obtain token from Databricks CLI. Run 'databricks auth login' to configure your profile",
    ) or {}).get("access_token")
    if not token:
        raise click.ClickException("Failed to parse token from Databricks CLI output")
    return host, token


def _scim_lookup_id(host: str, account_id: str, token: str, resource: str, attribute: str, value: str) -> str:
    """Return the ID of the SCIM resource (Users/Groups) whose attribute equals value."""
    query = quote(f'{attribute} eq "{value}"')
    resources = _api(host, token, f"/api/2.0/accounts/{account_id}/scim/v2/{resource}?filter={query}").get("Resources", [])
    if not resources:
        raise click.ClickException(f"{resource[:-1]} '{value}' not found")
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
@click.option("--token", default=lambda: os.getenv("DATABRICKS_TOKEN"), show_default="env:DATABRICKS_TOKEN", help="Databricks OAuth/PAT token (resolved from --profile if not set)")
@click.option("--profile", default=lambda: os.getenv("DATABRICKS_CONFIG_PROFILE", "DEFAULT"), show_default="env:DATABRICKS_CONFIG_PROFILE", help="Databricks CLI profile")
def patch_group_member(account_id: str, group: str, user: str, action: str, host: Optional[str], token: Optional[str], profile: str):
    """Add or remove a user from a Databricks account group using SCIM PATCH."""
    host = host or DEFAULT_HOST
    token = token or _resolve_connection(profile)[1]

    click.secho(f"Looking up group '{group}'...", fg="cyan")
    group_id = _scim_lookup_id(host, account_id, token, "Groups", "displayName", group)
    click.secho(f"Looking up user '{user}'...", fg="cyan")
    user_id = _scim_lookup_id(host, account_id, token, "Users", "userName", user)

    if action == "add":
        operations = [{"op": "add", "path": "members", "value": [{"value": user_id}]}]
    else:
        operations = [{"op": "remove", "path": f'members[value eq "{user_id}"]'}]

    click.secho(f"Patching group '{group}' ({group_id}) for user '{user}' ({user_id})...", fg="cyan")
    response = _api(
        host, token, f"/api/2.0/accounts/{account_id}/scim/v2/Groups/{group_id}",
        method="PATCH",
        body={"schemas": [PATCH_SCHEMA], "Operations": operations},
        content_type="application/scim+json",
    )
    click.secho(f"✅ User {action}ed successfully", fg="green")
    if response:
        click.echo(json.dumps(response, indent=2))


def _find_job_id(host: str, token: str, job_name: str) -> int:
    """Return the ID of the uniquely named Databricks job."""
    jobs = _api(host, token, f"/api/2.1/jobs/list?name={quote(job_name)}").get("jobs", [])
    exact_matches = [job for job in jobs if job.get("settings", {}).get("name") == job_name]
    if not exact_matches:
        raise click.ClickException(f"Databricks job '{job_name}' not found")
    if len(exact_matches) > 1:
        raise click.ClickException(f"Multiple Databricks jobs named '{job_name}' found")
    return exact_matches[0]["job_id"]


def _run_mount_job(host: str, token: str, job_name: str, params: dict, dry_run: bool) -> Optional[int]:
    """Trigger the mount job with params as its `list_mount_point_string` notebook param."""
    job_id = _find_job_id(host, token, job_name)
    click.secho(f"  Job: {job_name} ({job_id})", fg="cyan")
    click.secho("\n📋 Job payload list_mount_point_string:", fg="cyan")
    click.echo(json.dumps(params, indent=2))
    if dry_run:
        click.secho("\n⚠️  [DRY RUN] Job not triggered.", fg="yellow")
        return None

    run_id = _api(
        host, token, "/api/2.1/jobs/run-now",
        method="POST",
        body={"job_id": job_id, "notebook_params": {"list_mount_point_string": json.dumps(params)}},
    )["run_id"]
    click.secho(f"\n✅ Job triggered successfully — run_id: {run_id}", fg="green")
    return run_id


def _wait_for_run(host: str, token: str, run_id: int) -> dict:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    previous_state = None

    while time.monotonic() < deadline:
        run = _api(host, token, f"/api/2.1/jobs/runs/get?run_id={run_id}")
        life_cycle_state = run.get("state", {}).get("life_cycle_state", "UNKNOWN")
        if life_cycle_state != previous_state:
            click.secho(f"  Run {run_id}: {life_cycle_state}", fg="cyan")
            previous_state = life_cycle_state
        if life_cycle_state in TERMINAL_RUN_STATES:
            return run
        time.sleep(RUN_POLL_INTERVAL_SECONDS)

    raise click.ClickException(f"Run {run_id} did not finish within {RUN_TIMEOUT_SECONDS // 60} minutes")


def _report_mount_jobs(runs: List[Tuple[str, str, int, str]]) -> None:
    """Wait for each triggered run and report its outcome; abort if any failed."""
    failed = False
    for host, token, run_id, environment in runs:
        click.secho(f"\n🌍 Checking {environment.upper()} run {run_id}", fg="magenta", bold=True)
        try:
            state = _wait_for_run(host, token, run_id).get("state", {})
            result_state = state.get("result_state")
            if result_state == "SUCCESS":
                click.secho(f"✅ Run {run_id} completed successfully", fg="green")
                continue

            failed = True
            state_message = state.get("state_message")
            click.secho(f"❌ Run {run_id} failed: {result_state or state.get('life_cycle_state', 'UNKNOWN')}", fg="red", err=True)
            if state_message:
                click.echo(state_message, err=True)
            output = _api(host, token, f"/api/2.1/jobs/runs/get-output?run_id={run_id}")
            if output.get("error") and output["error"] != state_message:
                click.echo(output["error"], err=True)
            if output.get("error_trace"):
                click.echo(output["error_trace"], err=True)
        except click.ClickException as exc:
            failed = True
            click.secho(f"❌ Could not get result for run {run_id}: {exc.message}", fg="red", err=True)

    if failed:
        raise click.Abort()


def _trigger_mounts(job_name: str, envs: Dict[str, Dict[str, Any]], connect, build_params, dry_run: bool) -> None:
    """Pipeline shared by the mount commands: per env, connect → trigger job; then wait for all runs.

    connect(env) returns (host, token); build_params(env, env_cfg) returns the job payload.
    """
    click.secho(f"\n{'='*60}", fg="cyan", bold=True)
    click.secho(f"💡 Job name: {job_name} | Environments: {', '.join(envs)}", fg="cyan")
    click.secho(f"{'='*60}", fg="cyan", bold=True)

    runs = []
    for env, env_cfg in envs.items():
        click.secho(f"\n🌍 {env.upper()} ({env_cfg['name']})", fg="magenta", bold=True)
        host, token = connect(env)
        run_id = _run_mount_job(host, token, job_name, build_params(env, env_cfg), dry_run)
        if run_id is not None:
            runs.append((host, token, run_id, env))

    _report_mount_jobs(runs)


@databricks.command(name="mount-blob")
@click.argument("storage_account", type=str, metavar="STORAGE_ACCOUNT")
@click.argument("container", type=str)
@click.argument("mount_path", type=str)
@click.argument("project_code", type=str)
@click.option("--envs", multiple=True, help="Environment prefixes to process (default: d, t, r, p)")
@click.option("--dry-run", is_flag=True, help="Print the job payload without triggering the job")
@click.option("--force", default="false", show_default=True, type=click.Choice(["true", "false"]), help="Force remount if already mounted")
def mount_blob(
    storage_account: str,
    container: str,
    mount_path: str,
    project_code: str,
    envs: Tuple[str, ...],
    dry_run: bool,
    force: str
):
    """Trigger a Databricks job to mount a Blob Storage container via SAS key.

    STORAGE_ACCOUNT may omit the environment prefix or include one that will be replaced.

    Authenticates with the Databricks CLI profile named after each env's workspace
    ({env}{PROJECT_CODE}adb01).

    Example:
        mycli databricks mount-blob d90021sa01 mycontainer mydata d91231 --envs d --envs t
    """
    storage_account_suffix = strip_environment_prefix(storage_account)

    def build_params(env: str, env_cfg: Dict[str, Any]) -> dict:
        storage_account_name = f"{env}{storage_account_suffix}"
        click.secho(f"  Storage Account: {storage_account_name} | Container: {container} | Mount: {mount_path}", fg="cyan")
        return {
            "config": {
                "secretScope_name": f"SecretScope-{env}{project_code}-databricks",
            },
            "list_mount": [
                {
                    "name": mount_path,
                    "source": f"wasbs://{container}@{storage_account_name}.blob.core.windows.net/",
                    "sas_key": f"{storage_account_name}-{container}-sas",
                    "force": force,
                    "container": container,
                    "storage": storage_account_name
                }
            ],
        }

    _trigger_mounts(
        "Job_Mount_Blob_Serverless",
        resolve_environments(envs),
        connect=lambda env: _resolve_connection(f"{env}{project_code}adb01"),
        build_params=build_params,
        dry_run=dry_run,
    )


@databricks.command(name="mount-datalake")
@click.argument("datalake", type=str, metavar="DATALAKE")
@click.argument("container", type=str)
@click.argument("mount_path", type=str)
@click.argument("project_code", type=str)
@click.option("--envs", multiple=True, help="Environment prefixes to process (default: d, t, r, p)")
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
        mycli databricks mount-datalake d90021adl01 myfs mydata myproj --envs d --envs t
    """
    connection = _resolve_connection(profile)
    datalake_suffix = strip_environment_prefix(datalake)

    def build_params(env: str, env_cfg: Dict[str, Any]) -> dict:
        datalake_name = f"{env}{datalake_suffix}"
        env_display = env_cfg["name"]
        click.secho(f"  Datalake: {datalake_name} | Container: {container} | Mount: {mount_path}", fg="cyan")
        return {
            "config": {
                "clientId_key": f"{env}{project_code}spnadl-clientID",
                "clientSecret_key": f"{env_display}{project_code}spnadl-secret",
                "secretScope_key": f"SecretScope-{env_display}{project_code}akv01",
            },
            "list_mount": [
                {
                    "name": mount_path,
                    "source": f"abfss://{container}@{datalake_name}.dfs.core.windows.net/",
                    "force": force,
                }
            ],
        }

    _trigger_mounts(
        "Job_Mount_Datalake_Serverless",
        resolve_environments(envs),
        connect=lambda env: connection,
        build_params=build_params,
        dry_run=dry_run,
    )
