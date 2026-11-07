"""Vault path resolution and project/epic/task path + slug helpers."""

import re
from pathlib import Path
from typing import Optional

from helpers.config import get_config

PROJECTS_SUBDIR = "20-Projects"

_WORKSPACES = {
    "personal": "~/Documents/personal",
    "work": "~/Documents/work",
}


def vault_root(workspace: Optional[str] = None) -> Path:
    """Resolve the vault root. `workspace` overrides project_mgmt.vault_path for one-off use."""
    if workspace:
        return Path(_WORKSPACES[workspace]).expanduser()
    return Path(get_config("project_mgmt.vault_path", default=_WORKSPACES["personal"])).expanduser()


def templates_root() -> Path:
    default = str(Path.home() / "Documents" / "templates" / "project-mgmt")
    return Path(get_config("project_mgmt.templates_path", default=default)).expanduser()


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"Could not derive a slug from {title!r}")
    return slug


def project_dir(project_slug: str, workspace: Optional[str] = None) -> Path:
    return vault_root(workspace) / PROJECTS_SUBDIR / project_slug


def project_note_path(project_slug: str, workspace: Optional[str] = None) -> Path:
    return project_dir(project_slug, workspace) / "_project.md"


def epic_dir(project_slug: str, epic_slug: str) -> Path:
    return project_dir(project_slug) / "epics" / epic_slug


def epic_note_path(project_slug: str, epic_slug: str) -> Path:
    return epic_dir(project_slug, epic_slug) / "_epic.md"


def find_task_path(task_slug: str) -> Optional[Path]:
    """Locate a task note by slug anywhere under the vault's projects tree."""
    root = vault_root() / PROJECTS_SUBDIR
    if not root.is_dir():
        return None
    matches = sorted(root.glob(f"*/tasks/{task_slug}.md")) + sorted(
        root.glob(f"*/epics/*/tasks/{task_slug}.md")
    )
    return matches[0] if matches else None
