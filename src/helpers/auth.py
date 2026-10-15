"""Azure privilege elevation helpers."""

from contextlib import contextmanager
from typing import Iterator

import click

from helpers.config import require_config
from helpers.shell import run_cmd, run_text


@contextmanager
def elevated_spn(
    client_id: str,
    tenant_id: str,
    secret_name: str,
    keyvault_name: str,
    dry_run: bool = False,
) -> Iterator[None]:
    """Run the wrapped block elevated as the CICD service principal.

    Enter fetches the SPN secret from Key Vault and logs in with it; exit lowers
    permissions via an interactive `az login`. In dry-run mode nothing is logged
    in and the block runs as-is.
    """
    if dry_run:
        click.secho(
            f"⚠️  [DRY RUN] Would retrieve {secret_name} from {keyvault_name}, elevate as SPN "
            f"{client_id} (tenant {tenant_id}) then lower permissions via interactive 'az login'",
            fg="yellow",
        )
        yield
        return

    secret = run_text(
        ["az", "keyvault", "secret", "show",
         "--id", f"https://{keyvault_name}.vault.azure.net/secrets/{secret_name}",
         "--query", "value", "-o", "tsv"],
        error_msg=f"Error getting {secret_name} secret from {keyvault_name}",
    )

    click.secho(f"🔐 Elevating: logging in as SPN {client_id}...", fg="yellow")
    run_cmd(
        ["az", "login", "--service-principal", "--username", client_id, "--tenant", tenant_id, "-p", secret],
        error_msg="SPN elevation failed",
        success_msg="Elevated to CICD service principal",
    )

    try:
        yield
    finally:
        click.secho("🔓 Lowering permissions: interactive 'az login'...", fg="yellow")
        run_cmd(["az", "login"], error_msg="Interactive login failed", capture=False, check=False)


@contextmanager
def elevated_cicd_spn(dry_run: bool = False) -> Iterator[None]:
    """elevated_spn with the CICD SPN coordinates read from the CLI config."""
    with elevated_spn(
        client_id=require_config("cicd_spn_client_id"),
        tenant_id=require_config("tenant_id"),
        secret_name=require_config("cicd_spn_secret_name"),
        keyvault_name=require_config("cicd_spn_keyvault"),
        dry_run=dry_run,
    ):
        yield
