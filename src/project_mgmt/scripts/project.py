"""`mycli project` commands - project/epic/task tracking root group."""

from typing import Optional

import click

from project_mgmt.scripts import notes as note_helpers
from project_mgmt.scripts.epic import epic
from project_mgmt.scripts.task import task
from project_mgmt.scripts.vault import project_dir, project_note_path, slugify


@click.group()
def project():
    """Project / epic / task tracking commands."""
    pass


@project.command()
@click.argument("name")
@click.option(
    "--vault", "workspace", type=click.Choice(["personal", "work"]),
    help="Vault workspace to create the project in; defaults to project_mgmt.vault_path.",
)
def init(name: str, workspace: Optional[str]):
    """Initialize a new project's folder structure and main note."""
    project_slug = slugify(name)
    directory = project_dir(project_slug, workspace)
    note_path = project_note_path(project_slug, workspace)
    if note_path.exists():
        raise click.ClickException(f"Project '{project_slug}' already exists at {note_path}")

    for sub in ("epics", "tasks"):
        (directory / sub).mkdir(parents=True, exist_ok=True)

    frontmatter = note_helpers.build_frontmatter(slug=project_slug, tags=["project"], project=project_slug)
    body = note_helpers.render_template("project.md", title=name)
    note_helpers.write_note(note_path, frontmatter, body)

    click.secho(f"✅ Created project '{project_slug}'", fg="green")
    click.echo(str(note_path))
    if workspace:
        click.secho(
            f"ℹ️  Created in the '{workspace}' vault. Other project/epic/task commands use "
            f"project_mgmt.vault_path from config - set it to this path to keep working here.",
            fg="cyan",
        )


project.add_command(epic)
project.add_command(task)
