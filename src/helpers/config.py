"""YAML-backed configuration shared across command groups."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
import yaml

CONFIG_ENV_VAR = "NOVA_CLI_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "nova-cli" / "config.yaml"


def _candidate_paths() -> List[Path]:
    """Return config file lookup paths, in priority order."""
    module_dir = Path(__file__).resolve().parent
    candidates = []

    # Optional override for packaged/installed CLI usage.
    env_override = os.getenv(CONFIG_ENV_VAR)
    if env_override:
        candidates.append(Path(env_override).expanduser())

    candidates.extend([
        Path.cwd() / "nova-cli.yaml",
        DEFAULT_CONFIG_PATH,
        module_dir / "config.yaml",
    ])
    candidates.extend(parent / "config.yaml" for parent in module_dir.parents[1:3])

    # Preserve order while removing duplicates.
    deduped_candidates = []
    seen = set()
    for config_path in candidates:
        resolved = str(config_path.resolve(strict=False))
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped_candidates.append(config_path)

    return deduped_candidates


def _load_config() -> Dict[str, Any]:
    """Load the YAML config from the first matching common path."""
    for config_path in _candidate_paths():
        if config_path.is_file():
            data = yaml.safe_load(config_path.read_text())
            if data is None:
                return {}
            if not isinstance(data, dict):
                raise click.ClickException(
                    f"Invalid config file {config_path}: expected a mapping at the top level."
                )
            return data
    return {}


_CONFIG = _load_config()


def _env_var_name(key: str) -> str:
    return key.upper().replace(".", "_").replace("-", "_")


def _lookup(key: str) -> Tuple[bool, Any]:
    """Resolve a (possibly dotted) key. Environment variables take precedence."""
    env_value = os.getenv(_env_var_name(key))
    if env_value is not None:
        return True, env_value

    current: Any = _CONFIG
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def get_config(key: str, default: Any = None, message: Optional[str] = None) -> Any:
    """Return the config value for key, or default if it is missing.

    If message is provided and the key is missing, raise a ClickException
    with that message instead of returning the default.
    """
    found, value = _lookup(key)
    if found:
        return value
    if message is not None:
        raise click.ClickException(message)
    return default


def require_config(key: str, message: Optional[str] = None) -> Any:
    """Return the config value for key, raising a ClickException if it is missing.

    The optional message overrides the default missing-key error message.
    """
    found, value = _lookup(key)
    if not found:
        raise click.ClickException(
            message
            or (
                f"Missing configuration key '{key}'. "
                f"Set it with `mycli configure {key}=<value>` or in {DEFAULT_CONFIG_PATH}."
            )
        )
    return value


def _deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def write_config(updates: Dict[str, Any]) -> Path:
    """Deep-merge updates into the YAML config file and return its path."""
    env_override = os.getenv(CONFIG_ENV_VAR)
    target = Path(env_override).expanduser() if env_override else DEFAULT_CONFIG_PATH

    existing: Dict[str, Any] = {}
    if target.is_file():
        existing = yaml.safe_load(target.read_text()) or {}
        if not isinstance(existing, dict):
            raise click.ClickException(
                f"Invalid config file {target}: expected a mapping at the top level."
            )

    merged = _deep_merge(existing, updates)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(merged, default_flow_style=False, sort_keys=False))
    target.chmod(0o600)
    return target
