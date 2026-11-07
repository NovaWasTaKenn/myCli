"""`mycli project epic` commands."""

from typing import Optional

import click

from project_mgmt.scripts import notes as note_helpers
from project_mgmt.scripts.vault import (
    epic_dir, epic_note_path, list_epics, project_note_path, require_current_project, require_epic, slugify,
)


@click.group()
def epic():
    """Epic (feature set) commands."""
    pass


def start_epic(name: str, project_slug: str, deadline: Optional[str] = None, *, exist_ok: bool = False) -> str:
    """Create the epic's folder structure + note, link it from the project note.

    Returns the epic slug. With exist_ok=True an existing epic is left
    untouched (used by `task create --epic` to auto-create epics on demand).
    """
    epic_slug = slugify(name)
    note_path = epic_note_path(project_slug, epic_slug)
    if note_path.exists():
        if exist_ok:
            return epic_slug
        raise click.ClickException(f"Epic '{epic_slug}' already exists under project '{project_slug}'.")

    directory = epic_dir(project_slug, epic_slug)
    for sub in ("tasks", "docs/planning", "docs/realisation", "docs/finished"):
        (directory / sub).mkdir(parents=True, exist_ok=True)

    frontmatter = note_helpers.build_frontmatter(
        slug=epic_slug, tags=["epic", "status/todo"], project=project_slug, epic=epic_slug, deadline=deadline,
    )
    body = note_helpers.render_template("epic.md", title=name)
    note_helpers.write_note(note_path, frontmatter, body)

    project_note = project_note_path(project_slug)
    if project_note.exists():
        note_helpers.append_link(project_note, "Epics", f"- [[{epic_slug}|{name}]]")

    return epic_slug


_PROJECT_OPTION_HELP = (
    "Project slug the epic belongs to. Defaults to the current project "
    "(project_mgmt.project / $PROJECT_MGMT_PROJECT, else the nearest git repo's name)."
)


@epic.command()
@click.argument("epic_slug")
@click.option("--project", "project_slug", help=_PROJECT_OPTION_HELP)
def prd(epic_slug: str, project_slug: Optional[str]):
    """Open the epic's PRD/plan note, creating it from the planning template if missing."""
    project_slug = require_current_project(project_slug)
    require_epic(project_slug, epic_slug)
    path = epic_dir(project_slug, epic_slug) / "docs" / "planning" / "_prd.md"
    note_helpers.open_or_create(
        path,
        slug=f"{epic_slug}-prd",
        tags=["doc", "doc/planning"],
        project=project_slug,
        epic=epic_slug,
        template="planning.md",
        title=epic_slug.replace("-", " ").title(),
    )


@epic.command()
@click.argument("epic_slug")
@click.option("--project", "project_slug", help=_PROJECT_OPTION_HELP)
def scratchpad(epic_slug: str, project_slug: Optional[str]):
    """Open the epic's scratchpad note (one per epic), creating it from the scratchpad template if missing."""
    project_slug = require_current_project(project_slug)
    require_epic(project_slug, epic_slug)
    path = epic_dir(project_slug, epic_slug) / "docs" / "realisation" / "_scratchpad.md"
    note_helpers.open_or_create(
        path,
        slug=f"{epic_slug}-scratchpad",
        tags=["doc", "doc/realisation"],
        project=project_slug,
        epic=epic_slug,
        template="scratchpad.md",
        title=epic_slug.replace("-", " ").title(),
    )


@epic.command("list")
@click.option("--project", "project_slug", help=_PROJECT_OPTION_HELP)
def list_(project_slug: Optional[str]):
    """List epics in a project."""
    project_slug = require_current_project(project_slug)
    paths = list_epics(project_slug)
    if not paths:
        click.echo(f"No epics in project '{project_slug}'.")
        return
    for path in paths:
        data, body = note_helpers.read_frontmatter(path)
        slug = path.parent.name
        title = note_helpers.extract_title(body) or slug
        status = note_helpers.status_from_tags(data.get("tags", []))
        line = f"{slug:<24} [{status:<12}] {title}"
        if data.get("deadline"):
            line += f"  (due {data['deadline']})"
        click.echo(line)


@epic.command()
@click.argument("name")
@click.option("--project", "project_slug", help=_PROJECT_OPTION_HELP)
@click.option("--deadline", help="Optional deadline, YYYY-MM-DD.")
def start(name: str, project_slug: Optional[str], deadline: Optional[str]):
    """Start a new epic under an existing project: folder structure, note, and a link from the project."""
    project_slug = require_current_project(project_slug)
    if not project_note_path(project_slug).exists():
        raise click.ClickException(
            f"Project '{project_slug}' does not exist. Run `mycli project init {project_slug}` first."
        )
    epic_slug = start_epic(name, project_slug, deadline)
    click.secho(f"✅ Created epic '{epic_slug}' under project '{project_slug}'", fg="green")
    click.echo(str(epic_note_path(project_slug, epic_slug)))
