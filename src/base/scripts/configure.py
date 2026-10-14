"""Root-level `configure` command: write keys to the CLI YAML config file."""

from typing import Any, Dict

import click
import yaml

from helpers.config import write_config


def _parse_value(raw: str) -> Any:
    """Parse a CLI value as a YAML scalar, falling back to the raw string."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _set_nested(tree: Dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    current = tree
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


@click.command()
@click.argument("settings", nargs=-1, required=True, metavar="KEY=VALUE...")
def configure(settings: tuple[str, ...]) -> None:
    """Store SETTINGS in the CLI YAML configuration file.

    Values are parsed as YAML scalars (e.g. `count=3` stores an int,
    `flag=true` a bool). Dotted keys create nested mappings, e.g.:

        mycli configure list.a=1 list.b=2
    """
    updates: Dict[str, Any] = {}
    for setting in settings:
        if "=" not in setting:
            raise click.BadParameter(f"Expected KEY=VALUE, got '{setting}'", param_hint="settings")
        key, raw_value = setting.split("=", 1)
        key = key.strip()
        if not key or any(not part.strip() for part in key.split(".")):
            raise click.BadParameter(f"Invalid configuration key '{key}'", param_hint="settings")
        _set_nested(updates, key, _parse_value(raw_value))

    config_path = write_config(updates)

    keys = ", ".join(setting.split("=", 1)[0].strip() for setting in settings)
    click.secho(f"Configured {keys} in {config_path}", fg="green")
