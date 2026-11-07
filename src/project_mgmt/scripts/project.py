"""`mycli project` commands - project/epic/task tracking root group."""

from typing import Optional

import click

from project_mgmt.scripts import notes as note_helpers
from project_mgmt.scripts.epic import epic
from project_mgmt.scripts.task import task
from project_mgmt.scripts.vault import project_dir, project_note_path, slugify, require_current_project



@click.group()
def project():
    """Project / epic / task tracking commands."""
    pass


@project.command()
@click.option("--name", default=None, help="The name of the project")
def init(name: Optional[str]):
    """Initialize a new project's folder structure and main note."""
    name = name if name is not None else require_current_project()
    project_slug  = slugify(name)
    directory = project_dir(project_slug)
    note_path = project_note_path(project_slug)
    if note_path.exists():
        raise click.ClickException(f"Project '{project_slug}' already exists at {note_path}")

    for sub in ("epics", "tasks"):
        (directory / sub).mkdir(parents=True, exist_ok=True)

    frontmatter = note_helpers.build_frontmatter(slug=project_slug, tags=["project"], project=project_slug)
    body = note_helpers.render_template("project.md", title=name)
    note_helpers.write_note(note_path, frontmatter, body)

    click.secho(f"✅ Created project '{project_slug}'", fg="green")
    click.echo(str(note_path))

project.add_command(epic)
project.add_command(task)
