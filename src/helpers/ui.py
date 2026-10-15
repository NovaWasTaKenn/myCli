"""Console output helpers shared across command groups."""

from typing import Optional

import click


def banner(title: str, fg: Optional[str] = None, width: int = 60) -> None:
    """Print a section title framed by separator lines."""
    line = "=" * width
    click.secho(f"\n{line}\n{title}\n{line}\n", fg=fg, bold=fg is not None)
