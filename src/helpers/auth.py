"""Azure privilege elevation helpers."""

from contextlib import contextmanager
from typing import Iterator, Optional

import click

from helpers.shell import run_cmd, run_cmd_or_dry


@contextmanager
def elevated_spn(
    client_id: str,
    tenant_id: str,
    secret_name: str,
    keyvault_name: str,
    dry_run: bool = False,
) -> Iterator[None]:
    """Run the wrapped block elevated as the CICD service principal.

    Enter logs in with the SPN; exit lowers permissions via an interactive
    `az login`. In dry-run mode nothing is logged in and the block runs as-is.
    """
    
    secret = run_cmd_or_dry(
                ['az', 'keyvault','secrets','show','--id', f"https://{keyvault_name}.vault.azure.net/secrets/{secret_name}"],
                dry_run=dry_run,
                dry_run_msg=(
                    f"⚠️  [DRY RUN] Would retrieve {secret_name} from {keyvault_name}"
                ),
                error_msg=f"Error getting {secret_name} secret from {keyvault_name}",
                success_msg=f"Retrieved {secret_name} secret from {keyvault_name}",
            )
    secret =  secret.stdout.strip('"') if secret else secret

    if dry_run:
        click.secho(
            f"⚠️  [DRY RUN] Would elevate as SPN {client_id} (tenant {tenant_id}) "
            "then lower permissions via interactive 'az login'",
            fg="yellow",
        )
        yield
        return

    click.secho(f"🔐 Elevating: logging in as SPN {client_id}...", fg="yellow")
    run_cmd(
        ["az", "login", "--service-principal", "--username", client_id, "--tenant", tenant_id, "-p", secret], 
        error_msg=f"❌ SPN elevation failed",
        success_msg="✅ Elevated to CICD service principal"
    )

    try:
        yield
    finally:
        click.secho("🔓 Lowering permissions: interactive 'az login'...", fg="yellow")
        run_cmd(["az", "login"], error_msg="Interactive login failed", capture=False, check=False)
