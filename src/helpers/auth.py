"""Azure privilege elevation helpers."""

import subprocess
from contextlib import contextmanager
from typing import Iterator, Optional

import click

from helpers.env_config import (
    AKVAKS_SETUP_CICD_SPN_CLIENT_ID,
    AKVAKS_SETUP_CICD_SPN_TENANT_ID,
    AKVAKS_SETUP_CICD_SPN_SECRET,
)


@contextmanager
def elevated_spn(
    client_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    secret: Optional[str] = None,
    dry_run: bool = False,
) -> Iterator[None]:
    """Run the wrapped block elevated as the CICD service principal.

    Enter logs in with the SPN; exit lowers permissions via an interactive
    `az login`. In dry-run mode nothing is logged in and the block runs as-is.
    """
    client_id = client_id or AKVAKS_SETUP_CICD_SPN_CLIENT_ID
    tenant_id = tenant_id or AKVAKS_SETUP_CICD_SPN_TENANT_ID
    secret = secret or AKVAKS_SETUP_CICD_SPN_SECRET

    if dry_run:
        click.secho(
            f"⚠️  [DRY RUN] Would elevate as SPN {client_id} (tenant {tenant_id}) "
            "then lower permissions via interactive 'az login'",
            fg="yellow",
        )
        yield
        return

    missing = [name for name, value in (
        ("client id", client_id), ("tenant id", tenant_id), ("secret", secret),
    ) if not value]
    if missing:
        raise click.ClickException(
            "Missing CICD SPN elevation config: " + ", ".join(missing) + ". Set "
            "AKVAKS_SETUP_CICD_SPN_CLIENT_ID / _TENANT_ID / _SECRET."
        )

    click.secho(f"🔐 Elevating: logging in as SPN {client_id}...", fg="yellow")
    try:
        subprocess.run(
            ["az", "login", "--service-principal",
             "--username", client_id, "--tenant", tenant_id, "-p", secret],
            check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        click.secho(f"❌ SPN elevation failed: {e.stderr or e}", fg="red", err=True)
        raise click.Abort()
    click.secho("✅ Elevated to CICD service principal", fg="green")

    try:
        yield
    finally:
        click.secho("🔓 Lowering permissions: interactive 'az login'...", fg="yellow")
        subprocess.run(["az", "login"], check=False)
