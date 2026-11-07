"""Cross-module HTTP helpers.

Mirrors shell.run_cmd's philosophy: a thin wrapper that standardizes logging
and failure handling, so integrations like Jira (or a future Confluence sync)
don't hand-roll try/except per call site.
"""

from typing import Any, Optional

import click
import requests


def request(
    method: str,
    url: str,
    *,
    error_msg: str = "Request failed",
    success_msg: Optional[str] = None,
    fatal: bool = True,
    timeout: float = 15,
    **kwargs: Any,
) -> Optional[requests.Response]:
    """Make an HTTP request, logging and handling failure consistently.

    Mirrors run_cmd: fatal=True (default) logs red and raises click.Abort()
    on a request exception or non-2xx status. fatal=False logs yellow and
    returns None instead, for best-effort integrations that must not block
    the caller's other work.
    """
    try:
        response = requests.request(method, url, timeout=timeout, **kwargs)
        response.raise_for_status()
    except requests.RequestException as exc:
        detail = exc.response.text if getattr(exc, "response", None) is not None else str(exc)
        if fatal:
            click.secho(f"❌ {error_msg}: {detail}", fg="red", err=True)
            raise click.Abort()
        click.secho(f"⚠️  {error_msg}: {detail}", fg="yellow", err=True)
        return None
    else:
        if success_msg:
            click.secho(f"✅ {success_msg}", fg="green")
        return response


def request_json(*args: Any, **kwargs: Any) -> Optional[Any]:
    """request(), parsed as JSON. Returns None on failure or an empty body."""
    response = request(*args, **kwargs)
    if response is None:
        return None
    return response.json() if response.content else None
