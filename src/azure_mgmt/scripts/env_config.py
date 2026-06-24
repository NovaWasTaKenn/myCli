"""Environment-backed configuration for Azure management commands."""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv


def _load_dotenv() -> None:
    """Load .env from common locations, prioritizing module-local files."""
    module_dir = Path(__file__).resolve().parent
    candidates = [module_dir / ".env", Path.cwd() / ".env"]
    candidates.extend(parent / ".env" for parent in module_dir.parents)

    for env_path in candidates:
        if env_path.exists() and load_dotenv(dotenv_path=env_path):
            return

    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"No .env file found. Searched: {searched}")


def _parse_root_folders(value: Optional[str]) -> Tuple[str, ...]:
    if not value:
        raise Exception("Root folders not set in .env")
    folders = tuple(part.strip() for part in value.split(",") if part.strip())
    return folders


def _parse_environments(value: Optional[str]) -> Dict[str, Tuple[str, str]]:
    if not value:
        raise Exception("Environments dictionnary not set in .env")

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
MULTI_KV_CODE = os.getenv("MULTI_KV_CODE")
SOCLE_KV_CODE = os.getenv("SOCLE_KV_CODE")
AKVAKS_SETUP_CICD_SPN_SECRET = os.getenv("AKVAKS_SETUP_CICD_SPN_SECRET")
