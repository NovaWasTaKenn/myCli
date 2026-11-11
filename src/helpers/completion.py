"""Generic scaffolding for Click `shell_complete` callbacks.

Static completion (subcommand/flag names, fixed `click.Choice` values) is
handled by Click itself - nothing to write for that. This module is only for
*dynamic* values a command can't know about ahead of time (e.g. slugs read
from disk, choices read from config). Any command group can reuse it:

    from helpers.completion import complete_from

    def _project_slugs(ctx, param, incomplete):
        return [...]

    @click.option("--project", shell_complete=complete_from(_project_slugs))
"""

from typing import Callable, List

import click


def complete_from(values_fn: Callable[[click.Context, click.Parameter, str], List[str]]):
    """Wrap a (ctx, param, incomplete) -> list[str] lookup as a shell_complete callback.

    Errors from values_fn (missing config, unresolved vault, ...) are swallowed
    so a broken environment never breaks tab-completion - it just completes to
    nothing instead of raising in the user's shell.
    """
    def _complete(ctx: click.Context, param: click.Parameter, incomplete: str):
        try:
            values = values_fn(ctx, param, incomplete)
        except Exception:
            return []
        return [click.shell_completion.CompletionItem(v) for v in values if v.startswith(incomplete)]
    return _complete
