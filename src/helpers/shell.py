"""Cross-module subprocess helpers."""

import subprocess
import click
from typing import List, Optional


def run_cmd(
    cmd: List[str],
    error_msg: str = "Command failed",
    success_msg: Optional[str] = None,
    capture: bool = True,
) -> Optional[subprocess.CompletedProcess]:
    """Run a command, logging and aborting on failure. Returns the completed process."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
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
) -> Optional[subprocess.CompletedProcess]:
    """Run a command, or log a dry-run message and skip if dry_run is True."""
    if dry_run:
        click.secho(f"⚠️  [DRY RUN] {dry_run_msg}", fg="yellow")
        return None
    return run_cmd(cmd, error_msg=error_msg, success_msg=success_msg, capture=capture)
