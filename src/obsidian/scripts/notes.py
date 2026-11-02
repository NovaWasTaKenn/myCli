import click
from pathlib import Path

from helpers.shell import run_cmd

TEMPLATES_DIR = Path.home() / ".dotfiles/modules/home-manager/cli/src/obsidian/pandoc/templates"
PERSONAL_VAULT = Path.home() / "Documents/personal"
WORK_VAULT = Path.home() / "Documents/work"


@click.group()
def notes():
    pass


@notes.command()
@click.argument("name")
@click.option("--pane", is_flag=True, help="Use a pane instead of a window.")
@click.option("--work", is_flag=True, help="Open the note in the work vault instead of personal.")
def quick(name, pane, work):
    vault = WORK_VAULT if work else PERSONAL_VAULT
    tmux_target = ["split-window", "-v"] if pane else ["new-window"]
    run_cmd(
        ["tmux", *tmux_target, "-c", str(vault), f'nvim "+Obsidian new {name}"'],
        error_msg="Failed to open note in tmux",
    )


@notes.command()
@click.argument("input_file", nargs=1, type=click.Path(exists=True, dir_okay=False))
@click.argument("output_file", nargs=1, type=click.Path(exists=False, dir_okay=False))
@click.option(
    "--doctype",
    default="simple",
    show_default=True,
    type=str,
    help="Sets the general document style",
)
@click.option(
    "--latex/--no-latex",
    default=False,
    type=bool,
    help="Outputs the intermediate latex instead",
)
@click.option(
    "--fontsize",
    default="10pt",
    show_default=True,
    type=str,
    help="Sets the pdf font size",
)
@click.option(
    "--lang",
    default="fr-FR",
    show_default=True,
    type=str,
    help="Sets the pdf's language",
)
def mdpdf_pandoc(
    input_file, output_file, fontsize: str, doctype: str, lang: str, latex: bool
) -> None:
    """Converts gf markdown to pdf using pandoc"""
    dest_format = "latex" if latex else "pdf"
    if not output_file.endswith(f".{dest_format}"):
        raise click.BadParameter(f"output file must be .{dest_format}", param_hint="OUTPUT_FILE")

    args: list[str] = [
        "pandoc",
        input_file,
        "-o",
        output_file,
        "--from=markdown+hard_line_breaks+raw_tex",
        f"--to={dest_format}",
        "--pdf-engine=xelatex",
        "--standalone",
        f"--template={TEMPLATES_DIR / 'eisvogel.latex'}",
        "--listings",
        "-V",
        f"lang={lang}",
        "-V",
        f"fontsize={fontsize}",
        "-V",
        "colorlinks=true",
    ]

    if doctype == "simple":
        args += ["-V", "pagestyle=empty", "-V", "disable-header-and-footer=true"]
    if doctype == "report":
        args += ["--toc", "-V", "toc-own-page", "-V", "titlepage"]

    run_cmd(args, error_msg="pandoc conversion failed", capture=False)


@notes.command()
@click.argument("src", nargs=1, type=click.Path(exists=True))
@click.argument("dest", nargs=1, type=click.Path(exists=False))
@click.option(
    "--start_at",
    type=click.Path(exists=False),
    help="export only from this folder, links to notes in src vault are preserved",
)
def export(src, dest, start_at) -> None:
    """obsidian-export command, exports obsidian markdown notes from src obsidian vault to commonMarknotes in dest destination"""
    args = ["obsidian-export", src, dest] + (["--start-at", start_at] if start_at else [])
    run_cmd(args, error_msg="obsidian-export failed", capture=False)
