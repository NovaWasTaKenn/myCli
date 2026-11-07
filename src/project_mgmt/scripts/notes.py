"""Note creation/templating helpers for project_mgmt.

Python equivalent of the Neovim obsidian.lua frontmatter functions, used for
notes the CLI writes directly (bulk folder/note scaffolding) rather than
driving Neovim's ObsidianNew the way `mycli notes quick` does.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import click
import yaml

from helpers.shell import run_cmd
from project_mgmt.scripts.vault import templates_root


def build_frontmatter(*, slug: str, tags: Iterable[str], quality: str = "raw", **extra_fields: Any) -> str:
    """Build a YAML frontmatter block matching the vault's existing note convention."""
    fields: Dict[str, Any] = {
        "id": f"{slug}-{int(time.time())}",
        "aliases": [slug],
        "tags": list(tags),
        "creation_date": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    fields.update({key: value for key, value in extra_fields.items() if value is not None})
    fields["quality"] = quality
    dumped = yaml.safe_dump(fields, default_flow_style=False, sort_keys=False)
    return f"---\n{dumped}---\n"


def render_template(name: str, **placeholders: str) -> str:
    """Load a body template under templates_root() and substitute {{placeholder}} values."""
    path = templates_root() / name
    if not path.is_file():
        raise FileNotFoundError(
            f"Template not found: {path}. Expected project-mgmt templates under {templates_root()}."
        )
    text = path.read_text()
    for key, value in placeholders.items():
        text = text.replace(f"{{{{{key}}}}}", value or "")
    return text


def write_note(path: Path, frontmatter: str, body: str) -> None:
    if path.exists():
        raise FileExistsError(f"Note already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter + "\n" + body.rstrip() + "\n")


def open_in_editor(path: Path) -> None:
    """Open path in nvim via a new tmux window (mirrors `mycli notes quick`)."""
    run_cmd(["tmux", "new-window", f'nvim "{path}"'], error_msg="Failed to open note in tmux")


def open_or_create(
    path: Path, *, slug: str, tags: Iterable[str], template: str, title: str, **extra_fields: Any,
) -> None:
    """Open path in the editor, creating it from `template` first if it doesn't exist yet."""
    if not path.exists():
        frontmatter = build_frontmatter(slug=slug, tags=tags, **extra_fields)
        body = render_template(template, title=title)
        write_note(path, frontmatter, body)
        click.secho(f"✅ Created {path}", fg="green")
    open_in_editor(path)


def read_frontmatter(path: Path) -> Tuple[Dict[str, Any], str]:
    text = path.read_text()
    if not text.startswith("---\n"):
        raise ValueError(f"No frontmatter found in {path}")
    _, fm_text, body = text.split("---\n", 2)
    return yaml.safe_load(fm_text) or {}, body


def write_frontmatter(path: Path, data: Dict[str, Any], body: str) -> None:
    dumped = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
    path.write_text(f"---\n{dumped}---\n{body}")


def update_status_tag(path: Path, new_state: str) -> None:
    """Replace any existing status/<x> tag in path's frontmatter with status/<new_state>."""
    data, body = read_frontmatter(path)
    tags = [tag for tag in data.get("tags", []) if not str(tag).startswith("status/")]
    tags.append(f"status/{new_state}")
    data["tags"] = tags
    write_frontmatter(path, data, body)


def extract_title(body: str) -> str:
    """Return the first `# heading` found in a note body, or '' if none."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def status_from_tags(tags: Iterable[str]) -> str:
    """Return the value of the first `status/<x>` tag, or '-' if none."""
    for tag in tags:
        if str(tag).startswith("status/"):
            return str(tag).split("/", 1)[1]
    return "-"


def append_link(note_path: Path, heading: str, link_line: str) -> None:
    """Append link_line as a bullet under `## heading` in note_path, creating the heading if absent."""
    text = note_path.read_text()
    marker = f"## {heading}"
    if marker not in text:
        note_path.write_text(text.rstrip() + f"\n\n{marker}\n{link_line}\n")
        return

    lines = text.splitlines()
    insert_at = lines.index(marker) + 1
    while insert_at < len(lines) and lines[insert_at].strip() and not lines[insert_at].startswith("## "):
        insert_at += 1
    lines.insert(insert_at, link_line)
    note_path.write_text("\n".join(lines) + "\n")
