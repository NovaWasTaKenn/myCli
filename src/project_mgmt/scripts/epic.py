"""`mycli project epic` commands."""

from typing import Optional

import click

from project_mgmt.scripts import notes as note_helpers
from project_mgmt.scripts.vault import epic_dir, epic_note_path, project_note_path, slugify


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


@epic.command()
@click.argument("name")
@click.option("--project", "project_slug", required=True, help="Project slug the epic belongs to.")
@click.option("--deadline", help="Optional deadline, YYYY-MM-DD.")
def start(name: str, project_slug: str, deadline: Optional[str]):
    """Start a new epic under an existing project: folder structure, note, and a link from the project."""
    if not project_note_path(project_slug).exists():
        raise click.ClickException(
            f"Project '{project_slug}' does not exist. Run `mycli project init {project_slug}` first."
        )
    epic_slug = start_epic(name, project_slug, deadline)
    click.secho(f"✅ Created epic '{epic_slug}' under project '{project_slug}'", fg="green")
    click.echo(str(epic_note_path(project_slug, epic_slug)))
