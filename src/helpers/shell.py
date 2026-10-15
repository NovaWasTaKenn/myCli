"""Cross-module subprocess helpers.

Commands are written as a pipeline of steps, each step being one of:
- run_text / run_json: read steps, always executed (even in dry-run), abort on failure
- run_cmd_or_dry: write steps, skipped in dry-run
- run_idempotent: create steps that tolerate the resource already existing
"""

import json
import subprocess
import click
from typing import Any, Iterable, List, Optional


def run_cmd(
    cmd: List[str],
    error_msg: str = "Command failed",
    success_msg: Optional[str] = None,
    capture: bool = True,
    check: bool = True,
) -> Optional[subprocess.CompletedProcess]:
    """Run a command, logging and aborting on failure. Returns the completed process.

    With capture=False the command is attached to the terminal (stdin/stdout/stderr
    inherited) instead of captured. With check=False the completed process is
    returned even on non-zero exit instead of aborting.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            check=check,
            stdin=subprocess.DEVNULL if capture else None,
        )
    except subprocess.CalledProcessError as e:
        click.secho(f"❌ {error_msg}: {e.stderr or e}", fg="red", err=True)
        raise click.Abort()
    else:
        if success_msg:
            click.secho(f"✅ {success_msg}", fg="green")
        return result


def run_cmd_or_dry(
    cmd: List[str],
    dry_run: bool,
    dry_run_msg: str,
    error_msg: str = "Command failed",
    success_msg: Optional[str] = None,
    capture: bool = True,
    check: bool = True,
) -> Optional[subprocess.CompletedProcess]:
    """Run a command, or log a dry-run message and skip if dry_run is True."""
    if dry_run:
        click.secho(f"⚠️  [DRY RUN] {dry_run_msg}", fg="yellow")
        return None
    return run_cmd(cmd, error_msg=error_msg, success_msg=success_msg, capture=capture, check=check)


def run_text(cmd: List[str], error_msg: str = "Command failed") -> str:
    """Run a command and return its stripped stdout (e.g. `-o tsv` queries)."""
    return run_cmd(cmd, error_msg=error_msg).stdout.strip()


def run_json(cmd: List[str], error_msg: str = "Command failed") -> Any:
    """Run a command and return its stdout parsed as JSON (None if empty)."""
    stdout = run_cmd(cmd, error_msg=error_msg).stdout
    return json.loads(stdout) if stdout.strip() else None


def run_idempotent(
    cmd: List[str],
    exists_markers: Iterable[str],
    dry_run: bool,
    dry_run_msg: str,
    error_msg: str = "Command failed",
    success_msg: Optional[str] = None,
    exists_msg: str = "Already exists (skipping)",
) -> None:
    """Run a create command, treating an "already exists" error as success.

    exists_markers are matched case-insensitively against stderr. Any other
    failure is logged and aborts.
    """
    result = run_cmd_or_dry(cmd, dry_run=dry_run, dry_run_msg=dry_run_msg, check=False)
    if result is None:
        return
    if result.returncode == 0:
        if success_msg:
            click.secho(f"✅ {success_msg}", fg="green")
        return
    stderr = result.stderr.lower()
    if any(marker.lower() in stderr for marker in exists_markers):
        click.secho(f"ℹ️  {exists_msg}", fg="blue")
        return
    click.secho(f"❌ {error_msg}: {result.stderr}", fg="red", err=True)
    raise click.Abort()
