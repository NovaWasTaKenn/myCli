import click
import shutil
import subprocess
import re


def _get_notify_command(message: str) -> str:
    """Return the appropriate notification command for the current environment."""
    # WSL: use wsl-notify-send.exe for Windows toast notifications
    if shutil.which("wsl-notify-send.exe"):
        return f'wsl-notify-send.exe --category "Alarm" "{message}"'
    # Native Linux fallback
    return f'notify-send --urgency=critical "⏰ Alarm" "{message}"'


def _duration_to_seconds(duration: str) -> int | None:
    """Parse a human duration string into total seconds.

    Accepts: 5m, 10min, 1h, 1h30m, 90s, 2h15m30s, or plain number (minutes).
    """
    normalized = duration.strip()

    # Plain number = minutes
    if normalized.isdigit():
        return int(normalized) * 60

    pattern = r"^(?:(\d+)h)?(?:(\d+)(?:m(?:in)?)?)?(?:(\d+)s)?$"
    match = re.match(pattern, normalized)

    if match and any(match.groups()):
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds

    return None


@click.group()
def utils():
    """Utility commands."""
    pass


@utils.command()
@click.argument("duration")
@click.option("--message", "-m", default="Timer finished!", help="Notification message.")
def alarm(duration, message):
    """Set an alarm that triggers a desktop notification after DURATION.

    DURATION can be expressed as: 5m, 10min, 1h, 1h30m, 90s, etc.
    Spawns a background process that sleeps then sends a notification.
    """
    seconds = _duration_to_seconds(duration)
    if seconds is None:
        click.echo(f"Invalid duration format: {duration}")
        click.echo("Examples: 5m, 10min, 1h, 1h30m, 90s")
        raise SystemExit(1)

    notify_cmd = _get_notify_command(message)

    # Spawn a fully detached background process: sleep then notify
    subprocess.Popen(
        f"sleep {seconds} && {notify_cmd}",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    click.echo(f"Alarm set for {duration} from now. Message: {message}")
