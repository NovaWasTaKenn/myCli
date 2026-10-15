"""Azure environment helpers shared across command groups."""

from typing import Any, Dict, Iterable

import click

from helpers.config import require_config
from helpers.shell import run_cmd_or_dry

DEFAULT_ENVS = ("d", "t", "r", "p")


def resolve_environments(envs: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Validate environment prefixes (all of them if empty) and return {prefix: config}.

    Each config holds at least `name` and `subscription_id`.
    """
    environments = require_config("azure_environments")
    envs = tuple(envs) or DEFAULT_ENVS
    unknown = [env for env in envs if env not in environments]
    if unknown:
        raise click.ClickException(
            f"Unsupported environment prefix(es): {', '.join(unknown)}. "
            f"Available: {', '.join(environments)}"
        )
    return {env: environments[env] for env in envs}


def set_subscription(env_cfg: Dict[str, Any], dry_run: bool = False) -> None:
    """Select the environment's subscription for subsequent `az` commands."""
    subscription_id = env_cfg["subscription_id"]
    run_cmd_or_dry(
        ["az", "account", "set", "--subscription", subscription_id],
        dry_run=dry_run,
        dry_run_msg=f"Would set subscription to {env_cfg['name']} ({subscription_id})",
        error_msg=f"Error setting subscription to {env_cfg['name']}",
        success_msg=f"Subscription set to {env_cfg['name']} ({subscription_id})",
    )


def strip_environment_prefix(resource_name: str) -> str:
    """Drop a leading environment prefix from names like `dd90021adl01`."""
    if resource_name[:1].lower() in DEFAULT_ENVS and resource_name[1:3].lower() == "d9":
        return resource_name[1:]
    return resource_name
