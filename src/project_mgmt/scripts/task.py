"""`mycli project task` commands."""

import sys
from typing import List, Optional

import click

from helpers.config import get_config
from helpers.shell import run_cmd
from project_mgmt.scripts import jira, notes as note_helpers
from project_mgmt.scripts.epic import start_epic
from project_mgmt.scripts.vault import (
    epic_dir, epic_note_path, find_task_path, project_dir, require_current_project, slugify,
)


@click.group()
def task():
    """Task (ticket) commands."""
    pass


def _local_states() -> List[str]:
    return get_config("project_mgmt.states", default=["todo", "in-progress", "done"])


@task.command()
@click.argument("title")
@click.option(
    "--project", "project_slug",
    help="Project slug the task belongs to. Defaults to the current project "
    "(project_mgmt.project / $PROJECT_MGMT_PROJECT, else the nearest git repo's name).",
)
@click.option("--epic", "epic_name", help="Epic name or slug; created automatically if it doesn't exist.")
@click.option("--deadline", help="Optional deadline, YYYY-MM-DD.")
@click.option("--description", "edit_description", is_flag=True, help="Open $EDITOR to write a description.")
@click.option("--description-stdin", "stdin_description", is_flag=True, help="Read the description from stdin.")
@click.option(
    "--jira-sprint", type=click.Choice(["current", "backlog"]), default="backlog",
    help="Sprint placement; only meaningful when jira.enabled is true.",
)
@click.option("--story-points", type=int, help="Story points; only used when jira.enabled is true.")
def create(
    title: str,
    project_slug: Optional[str],
    epic_name: Optional[str],
    deadline: Optional[str],
    edit_description: bool,
    stdin_description: bool,
    jira_sprint: str,
    story_points: Optional[int],
):
    """Create a task, optionally under an epic (auto-created if missing), optionally synced to Jira.

    A future alternative, not implemented yet: opening a templated markdown
    file with comments and reading it back on save, instead of per-flag params.
    """
    if edit_description and stdin_description:
        raise click.UsageError("Use either --description or --description-stdin, not both.")
    project_slug = require_current_project(project_slug)
    if not project_dir(project_slug).exists():
        raise click.ClickException(
            f"Project '{project_slug}' does not exist. Run `mycli project init {project_slug}` first."
        )
    if (jira_sprint != "backlog" or story_points is not None) and not jira.enabled():
        raise click.UsageError("--jira-sprint/--story-points require jira.enabled=true in config.")

    description = ""
    if stdin_description:
        description = sys.stdin.read().strip()
    elif edit_description:
        description = (click.edit(text="") or "").strip()

    epic_slug = start_epic(epic_name, project_slug, exist_ok=True) if epic_name else None

    task_slug = slugify(title)
    task_dir = epic_dir(project_slug, epic_slug) / "tasks" if epic_slug else project_dir(project_slug) / "tasks"
    task_path = task_dir / f"{task_slug}.md"
    if task_path.exists():
        raise click.ClickException(f"Task '{task_slug}' already exists at {task_path}")

    jira_key = None
    if jira.enabled():
        jira_key = jira.create_issue(
            title=title, description=description, sprint=jira_sprint, story_points=story_points,
        )

    frontmatter = note_helpers.build_frontmatter(
        slug=task_slug,
        tags=["task", "status/todo"],
        project=project_slug,
        epic=epic_slug,
        deadline=deadline,
        story_points=story_points if jira.enabled() else None,
        jira_key=jira_key,
    )
    body = note_helpers.render_template("task.md", title=title, description=description)
    note_helpers.write_note(task_path, frontmatter, body)

    if epic_slug:
        note_helpers.append_link(epic_note_path(project_slug, epic_slug), "Tasks", f"- [[{task_slug}|{title}]]")

    suffix = f" (Jira {jira_key})" if jira_key else ""
    click.secho(f"✅ Created task '{task_slug}'{suffix}", fg="green")
    click.echo(str(task_path))


@task.command("status")
@click.argument("task_slug")
@click.argument("new_state")
@click.option(
    "--project", "project_slug",
    help="Project slug the task belongs to. Defaults to the current project "
    "(project_mgmt.project / $PROJECT_MGMT_PROJECT, else the nearest git repo's name).",
)
def set_status(task_slug: str, new_state: str, project_slug: Optional[str]):
    """Change a task's local completion state (and its Jira state, if synced)."""
    states = _local_states()
    if new_state not in states:
        raise click.UsageError(f"'{new_state}' is not a configured state. Choose from: {', '.join(states)}")

    project_slug = require_current_project(project_slug)
    task_path = find_task_path(project_slug, task_slug)
    if task_path is None:
        raise click.ClickException(f"No task found with slug '{task_slug}' in project '{project_slug}'")

    note_helpers.update_status_tag(task_path, new_state)
    click.secho(f"✅ {task_slug} -> {new_state}", fg="green")

    if jira.enabled():
        data, _ = note_helpers.read_frontmatter(task_path)
        jira_key = data.get("jira_key")
        if jira_key:
            jira.transition_issue(jira_key, new_state)


@task.command("open")
@click.argument("task_slug")
@click.option(
    "--project", "project_slug",
    help="Project slug the task belongs to. Defaults to the current project "
    "(project_mgmt.project / $PROJECT_MGMT_PROJECT, else the nearest git repo's name).",
)
def open_task(task_slug: str, project_slug: Optional[str]):
    """Open a task note in the editor."""
    project_slug = require_current_project(project_slug)
    task_path = find_task_path(project_slug, task_slug)
    if task_path is None:
        raise click.ClickException(f"No task found with slug '{task_slug}' in project '{project_slug}'")
    run_cmd(["tmux", "new-window", f'nvim "{task_path}"'], error_msg="Failed to open task note in tmux")
