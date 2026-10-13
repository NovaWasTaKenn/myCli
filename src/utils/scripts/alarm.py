import click
import shutil
import subprocess
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed


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


def _parse_rg_count_output(output: str) -> dict[str, int]:
    """Parse ripgrep --count-matches output into {file_path: count}."""
    per_file: dict[str, int] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            path, count_str = line.rsplit(":", 1)
            per_file[path] = int(count_str)
        except ValueError:
            # Ignore malformed lines defensively.
            continue
    return per_file


def _run_rg_count(
    folder: str,
    expression: str,
    globs: tuple[str, ...],
    ignore_case: bool,
    pcre2: bool,
    hidden: bool,
    follow: bool,
    rg_threads: int,
) -> tuple[int, dict[str, int]]:
    """Count regex matches using ripgrep and return total + per-file counts."""
    cmd: list[str] = [
        "rg",
        "--color",
        "never",
        "--no-messages",
        "--count-matches",
        "--with-filename",
        "--threads",
        str(rg_threads),
    ]
    if ignore_case:
        cmd.append("--ignore-case")
    if pcre2:
        cmd.append("--pcre2")
    if hidden:
        cmd.append("--hidden")
    if follow:
        cmd.append("--follow")

    for glob in globs:
        cmd.extend(["--glob", glob])

    cmd.extend(["-e", expression, folder])

    completed = subprocess.run(cmd, text=True, capture_output=True)

    # rg returns:
    # 0 -> at least one match
    # 1 -> no matches
    # 2 -> error
    if completed.returncode not in (0, 1):
        stderr = completed.stderr.strip() or "ripgrep failed"
        raise RuntimeError(stderr)

    per_file = _parse_rg_count_output(completed.stdout)
    total = sum(per_file.values())
    return total, per_file


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


@utils.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=str))
@click.option(
    "--expr",
    "-e",
    "expressions",
    multiple=True,
    required=True,
    help="Regex expression to count. Repeat -e for multiple expressions.",
)
@click.option(
    "--glob",
    "-g",
    "globs",
    multiple=True,
    help="Include only files matching this glob. Repeat for multiple globs.",
)
@click.option("--ignore-case", "-i", is_flag=True, help="Case-insensitive regex.")
@click.option("--pcre2", is_flag=True, help="Enable PCRE2 regex engine in ripgrep.")
@click.option("--hidden", is_flag=True, help="Search hidden files/directories too.")
@click.option("--follow", is_flag=True, help="Follow symlinks.")
@click.option(
    "--jobs",
    type=click.IntRange(min=1),
    default=None,
    help="Parallel jobs across expressions. Default: auto.",
)
@click.option(
    "--rg-threads",
    type=click.IntRange(min=1),
    default=None,
    help="Threads used by each ripgrep process. Default: auto-balanced.",
)
@click.option(
    "--per-file/--no-per-file",
    default=False,
    help="Also show per-file counts for each expression.",
)
@click.option(
    "--top-files",
    type=click.IntRange(min=1),
    default=20,
    show_default=True,
    help="When --per-file is enabled, show only the top N files per expression.",
)
@click.option(
    "--diff/--no-diff",
    default=False,
    help="Show difference between two expression totals (expr1 - expr2).",
)
def logcount(
    folder: str,
    expressions: tuple[str, ...],
    globs: tuple[str, ...],
    ignore_case: bool,
    pcre2: bool,
    hidden: bool,
    follow: bool,
    jobs: int | None,
    rg_threads: int | None,
    per_file: bool,
    top_files: int,
    diff: bool,
) -> None:
    """Count regex occurrences in a folder of logs using ripgrep.

    Designed for large log datasets by delegating matching to ripgrep,
    and optionally running one process per expression in parallel.
    """
    if not shutil.which("rg"):
        click.echo("Error: ripgrep (rg) is required but was not found in PATH.")
        click.echo("Install it and retry (https://github.com/BurntSushi/ripgrep).")
        raise SystemExit(1)

    folder = os.path.abspath(folder)
    cpu_count = os.cpu_count() or 1

    if jobs is None:
        jobs = min(len(expressions), cpu_count)
    else:
        jobs = min(jobs, len(expressions))

    if rg_threads is None:
        rg_threads = max(1, cpu_count // jobs)

    if diff and len(expressions) != 2:
        click.echo("Error: --diff requires exactly two -e/--expr options.")
        raise SystemExit(1)

    click.echo(
        f"Running {len(expressions)} expression(s) over {folder} with jobs={jobs}, rg_threads={rg_threads}"
    )

    results: dict[str, tuple[int, dict[str, int]]] = {}
    errors: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(
                _run_rg_count,
                folder,
                expression,
                globs,
                ignore_case,
                pcre2,
                hidden,
                follow,
                rg_threads,
            ): expression
            for expression in expressions
        }

        for future in as_completed(futures):
            expression = futures[future]
            try:
                results[expression] = future.result()
            except Exception as exc:
                errors.append((expression, str(exc)))

    if errors:
        for expression, message in errors:
            click.echo(f"[{expression}] error: {message}")
        raise SystemExit(2)

    grand_total = 0
    for expression in expressions:
        total, per_file_counts = results[expression]
        grand_total += total
        click.echo(f"[{expression}] {total}")

        if per_file:
            sorted_files = sorted(
                per_file_counts.items(), key=lambda item: item[1], reverse=True
            )
            for file_path, count in sorted_files[:top_files]:
                click.echo(f"  {count:>8}  {file_path}")

    if diff:
        first_total = results[expressions[0]][0]
        second_total = results[expressions[1]][0]
        click.echo(f"DIFF ({expressions[0]}) - ({expressions[1]}) = {first_total - second_total}")

    click.echo(f"TOTAL {grand_total}")
