"""Environment-backed configuration shared across command groups."""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv


def _load_dotenv() -> Optional[Path]:
    """Load .env from common locations, prioritizing explicit and cwd locations."""
    module_dir = Path(__file__).resolve().parent
    candidates = []

    # Optional override for packaged/installed CLI usage.
    env_override = os.getenv("NOVA_CLI_ENV_FILE")
    if env_override:
        candidates.append(Path(env_override).expanduser())

    candidates.extend([
        Path.cwd() / ".env",
        Path.home() / ".config" / "nova-cli" / ".env",
        module_dir / ".env",
    ])
    candidates.extend(parent / ".env" for parent in module_dir.parents[1:3])

    # Preserve order while removing duplicates.
    deduped_candidates = []
    seen = set()
    for env_path in candidates:
        resolved = str(env_path.resolve(strict=False))
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped_candidates.append(env_path)

    for env_path in deduped_candidates:
        if env_path.exists() and load_dotenv(dotenv_path=env_path):
            return env_path

    return None


def _parse_root_folders(value: Optional[str]) -> Tuple[str, ...]:
    if not value:
        return tuple()
    folders = tuple(part.strip() for part in value.split(",") if part.strip())
    return folders


def _parse_environments(value: Optional[str]) -> Dict[str, Tuple[str, str]]:
    if not value:
        return {}

    parsed: Dict[str, Tuple[str, str]] = {}
    entries = [entry.strip() for entry in value.split(";") if entry.strip()]
    for entry in entries:
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) != 3:
            continue
        short_name, display_name, subscription_id = parts
        if short_name and display_name and subscription_id:
            parsed[short_name] = (display_name, subscription_id)

    return parsed


_load_dotenv()



INGESTION_ROOT_FOLDERS = _parse_root_folders(os.getenv("INGESTION_ROOT_FOLDERS"))
ENVIRONMENTS = _parse_environments(os.getenv("AZURE_ENVIRONMENTS"))
MULTI_KV_CODE = os.getenv("MULTI_KV_CODE", "")
SOCLE_KV_CODE = os.getenv("SOCLE_KV_CODE", "")
AKVAKS_SETUP_CICD_SPN_SECRET = os.getenv("AKVAKS_SETUP_CICD_SPN_SECRET", "")


def require_environments() -> Dict[str, Tuple[str, str]]:
    """Return environment mapping or raise a clear configuration error."""
    if not ENVIRONMENTS:
        import click

        raise click.ClickException(
            "Missing AZURE_ENVIRONMENTS configuration. "
            "Set it in a .env file or as an environment variable."
        )
    return ENVIRONMENTS


def require_ingestion_root_folders() -> Tuple[str, ...]:
    """Return ingestion root folders or raise a clear configuration error."""
    if not INGESTION_ROOT_FOLDERS:
        import click

        raise click.ClickException(
            "Missing INGESTION_ROOT_FOLDERS configuration. "
            "Set it in a .env file or as an environment variable."
        )
    return INGESTION_ROOT_FOLDERS
