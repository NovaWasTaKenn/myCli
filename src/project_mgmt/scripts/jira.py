"""Thin, best-effort Jira Cloud sync for project_mgmt tasks.

Every call here is non-fatal by design: local Obsidian tracking must never be
blocked or rolled back by a Jira outage or misconfiguration. Failing calls
return None/False; callers just skip setting jira_key and carry on.
"""

import os
from typing import Optional, Tuple

import click

from helpers.config import get_config, require_config
from helpers.http import request, request_json


def enabled() -> bool:
    return bool(get_config("jira.enabled", default=False))


def _base_url() -> str:
    return require_config("jira.base_url").rstrip("/")


def _auth() -> Tuple[str, str]:
    email = require_config("jira.email")
    token_env = get_config("jira.api_token_env", default="JIRA_API_TOKEN")
    token = os.environ.get(token_env)
    if not token:
        raise click.ClickException(
            f"Jira sync is enabled but ${token_env} is not set. Export your Jira API token in that variable."
        )
    return email, token


def _active_sprint_id() -> Optional[int]:
    base, auth = _base_url(), _auth()
    board_id = require_config("jira.board_id")

    sprints = request_json(
        "GET", f"{base}/rest/agile/1.0/board/{board_id}/sprint", params={"state": "active"},
        auth=auth, fatal=False, error_msg="Failed to look up the active Jira sprint",
    )
    values = (sprints or {}).get("values") or []
    return values[0]["id"] if values else None


def create_issue(
    *, title: str, description: str = "", sprint: str = "backlog", story_points: Optional[int] = None,
) -> Optional[str]:
    """Create a Jira issue. Returns its key, or None if creation failed."""
    project_key = require_config("jira.project_key")
    base, auth = _base_url(), _auth()

    fields: dict = {
        "project": {"key": project_key},
        "summary": title,
        "issuetype": {"name": "Task"},
    }
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
        }
    story_points_field = get_config("jira.story_points_field")
    if story_points is not None and story_points_field:
        fields[story_points_field] = story_points

    created = request_json(
        "POST", f"{base}/rest/api/3/issue", json={"fields": fields}, auth=auth, fatal=False,
        error_msg="Failed to create Jira issue",
    )
    if not created:
        return None
    issue_key = created["key"]
    click.secho(f"✅ Created Jira issue {issue_key}", fg="green")

    if sprint == "current":
        sprint_id = _active_sprint_id(project_key)
        if sprint_id is None:
            click.secho(f"⚠️  No active Jira sprint found; {issue_key} left in the backlog", fg="yellow")
        else:
            request(
                "POST", f"{base}/rest/agile/1.0/sprint/{sprint_id}/issue",
                json={"issues": [issue_key]}, auth=auth, fatal=False,
                error_msg=f"Failed to move {issue_key} into the current sprint",
            )

    return issue_key


def transition_issue(issue_key: str, local_state: str) -> bool:
    """Apply the configured Jira transition for local_state. Returns whether it succeeded."""
    base, auth = _base_url(), _auth()
    target = get_config(f"jira.transitions.{local_state}")
    if not target:
        click.secho(
            f"⚠️  No Jira transition configured for state '{local_state}' (set jira.transitions.{local_state})",
            fg="yellow",
        )
        return False

    transition_id = str(target) if str(target).isdigit() else None
    if transition_id is None:
        available = request_json(
            "GET", f"{base}/rest/api/3/issue/{issue_key}/transitions", auth=auth, fatal=False,
            error_msg=f"Failed to list Jira transitions for {issue_key}",
        )
        for candidate in (available or {}).get("transitions", []):
            if candidate["name"].lower() == str(target).lower():
                transition_id = candidate["id"]
                break
        if transition_id is None:
            click.secho(f"⚠️  Jira transition '{target}' is not available for {issue_key}", fg="yellow")
            return False

    response = request(
        "POST", f"{base}/rest/api/3/issue/{issue_key}/transitions",
        json={"transition": {"id": transition_id}}, auth=auth, fatal=False,
        error_msg=f"Failed to transition {issue_key}",
    )
    if response is None:
        return False
    click.secho(f"✅ Transitioned {issue_key} -> {target}", fg="green")
    return True
