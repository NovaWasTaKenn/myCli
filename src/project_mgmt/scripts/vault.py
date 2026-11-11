"""Vault path resolution and project/epic/task path + slug helpers."""

import re
from pathlib import Path
from typing import List, Optional

import click

from helpers.config import require_config, get_config

PROJECTS_SUBDIR = "20-Projects"



def vault_root() -> Path:
    """Resolve the vault root. `workspace` overrides project_mgmt.vault_path for one-off use."""
    return Path(require_config("project_mgmt.vault_path")).expanduser()


def templates_root() -> Path:
    default = str(Path.home() / "Documents" / "templates" / "project-mgmt")
    return Path(get_config("project_mgmt.templates_path", default=default)).expanduser()


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"Could not derive a slug from {title!r}")
    return slug


def local_states() -> List[str]:
    """List the configured local task states (project_mgmt.states, defaulting to todo/in-progress/done)."""
    return get_config("project_mgmt.states", default=["todo", "in-progress", "done"])


def _git_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from `start` (default cwd) to the nearest directory holding a
    `.git` entry - a repo's `.git` dir, or a submodule's `.git` file."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / ".git").exists():
            return directory
    return None


def current_project() -> Optional[str]:
    """Resolve the current project slug, or None if it can't be determined.

    Precedence: `project_mgmt.project` - which, via the existing config
    lookup, is overridable by the $PROJECT_MGMT_PROJECT env var - then the
    name of the nearest enclosing git repo or submodule (walking up from cwd).
    """
    configured = get_config("project_mgmt.project")
    if configured:
        return slugify(str(configured))
    root = _git_root()
    return slugify(root.name) if root else None


def require_current_project(project_slug: Optional[str] = None) -> str:
    """Return project_slug, defaulting to current_project(); raises click.UsageError if neither resolves."""
    project_slug = project_slug or current_project()
    if not project_slug:
        raise click.UsageError(
            "Could not determine the current project. Pass --project, set project_mgmt.project, "
            "or run this from inside the project's git repo."
        )
    return project_slug


def project_dir(project_slug: str) -> Path:
    return vault_root() / PROJECTS_SUBDIR / project_slug


def project_note_path(project_slug: str) -> Path:
    return project_dir(project_slug) / "_project.md"


def epic_dir(project_slug: str, epic_slug: str) -> Path:
    return project_dir(project_slug) / "epics" / epic_slug


def epic_note_path(project_slug: str, epic_slug: str) -> Path:
    return epic_dir(project_slug, epic_slug) / "_epic.md"


def require_epic(project_slug: str, epic_slug: str) -> None:
    """Raise a clear click.ClickException if the epic doesn't exist yet."""
    if not epic_note_path(project_slug, epic_slug).exists():
        raise click.ClickException(
            f"Epic '{epic_slug}' does not exist under project '{project_slug}'. "
            f"Run `mycli project epic start {epic_slug} --project={project_slug}` first."
        )


def list_epics(project_slug: str) -> List[Path]:
    """List epic note paths under a project, sorted by slug."""
    base = project_dir(project_slug) / "epics"
    return sorted(base.glob("*/_epic.md")) if base.is_dir() else []


def project_slugs() -> List[str]:
    """List known project slugs (directory names under the vault's projects subdir)."""
    base = vault_root() / PROJECTS_SUBDIR
    return sorted(p.name for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


def epic_slugs(ctx: click.Context) -> List[str]:
    """List epic slugs for the project resolved from `ctx` (--project, else current_project())."""
    project_slug = ctx.params.get("project_slug") or current_project()
    if not project_slug:
        return []
    return [p.parent.name for p in list_epics(project_slug)]


def task_slugs(ctx: click.Context) -> List[str]:
    """List task slugs for the project/epic resolved from `ctx`."""
    project_slug = ctx.params.get("project_slug") or current_project()
    if not project_slug:
        return []
    epic_slug = ctx.params.get("epic_slug") or ctx.params.get("epic_name")
    return [p.stem for p in list_tasks(project_slug, epic_slug)]


def list_tasks(project_slug: str, epic_slug: Optional[str] = None) -> List[Path]:
    """List task note paths under a project, optionally scoped to one epic."""
    if epic_slug:
        tasks_dir = epic_dir(project_slug, epic_slug) / "tasks"
        return sorted(tasks_dir.glob("*.md")) if tasks_dir.is_dir() else []

    base = project_dir(project_slug)
    tasks = sorted((base / "tasks").glob("*.md")) if (base / "tasks").is_dir() else []
    epics_dir = base / "epics"
    if epics_dir.is_dir():
        tasks += sorted(epics_dir.glob("*/tasks/*.md"))
    return tasks


def find_task_path(project_slug: str, task_slug: str) -> Optional[Path]:
    """Locate a task note by slug within a project (standalone, or under any of its epics)."""
    base = project_dir(project_slug)
    if not base.is_dir():
        return None
    matches = sorted(base.glob(f"tasks/{task_slug}.md")) + sorted(base.glob(f"epics/*/tasks/{task_slug}.md"))
    return matches[0] if matches else None

def find_scratchpad_path(scratchpad_slug: str) -> Optional[Path]:
    """Locate a scratchpad note by slug anywhere under the vault's projects tree."""
    root = vault_root() / PROJECTS_SUBDIR
    if not root.is_dir():
        return None
    matches = sorted(root.glob(f"*/tasks/{task_slug}.md")) + sorted(
        root.glob(f"*/epics/*/tasks/{task_slug}.md")
    )
    return matches[0] if matches else None


