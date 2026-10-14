import click
import os

from helpers.shell import run_cmd


@click.group
def config():
    pass


@config.command()
def system_clean():
    """Clean up old nix generations and optimize the store."""
    run_cmd("sudo nix-collect-garbage -d".split(), error_msg="nix-collect-garbage failed", capture=False)
    run_cmd("home-manager expire-generations -d".split(), error_msg="expire-generations failed", capture=False)
    run_cmd("nix store gc".split(), error_msg="nix store gc failed", capture=False)
    run_cmd("sudo nix store optimize".split(), error_msg="nix store optimize failed", capture=False)
    run_cmd("sudo nix profile wipe-history".split(), error_msg="nix profile wipe-history failed", capture=False)
    run_cmd("home-manager remove-generations old".split(), error_msg="remove-generations failed", capture=False)


@config.command()
@click.argument("flakeinput", nargs=1, type=str)
@click.option(
    "--flakepath",
    default="~/.dotfiles/",
    help="path to the flake to update",
    type=str,
)
def update(flakeinput: str, flakepath: str):
    """Command that update a flake's input. By default updates the config's main flake"""

    run_cmd(
        [
            "nix",
            "flake",
            "update",
            flakeinput,
            "--flake",
            os.path.expanduser(flakepath),
        ],
        error_msg="nix flake update failed",
        capture=False,
    )


@config.command()
@click.argument("flakeinput", nargs=1, type=str)
@click.option(
    "--packagename",
    help="Name of the package to build",
    type=str,
)
def reload(flakeinput: str, packagename: str):
    """Command that builds packageName package, updates the inputFlakeName flake input in the config and rebuilds the home config to reload the changes"""

    packagename = (
        packagename if (packagename != "" and packagename is not None) else flakeinput
    )

    run_cmd(["nix", "build", f"./#{packagename}"], error_msg="nix build failed", capture=False)

    run_cmd(
        [
            "nix",
            "flake",
            "update",
            flakeinput,
            "--flake",
            os.path.expanduser("~/.dotfiles/"),
        ],
        error_msg="nix flake update failed",
        capture=False,
    )

    run_cmd(
        [
            "home-manager",
            "switch",
            "--flake",
            os.path.expanduser("~/.dotfiles/#user"),
        ],
        error_msg="home-manager switch failed",
        capture=False,
    )


@config.command()
@click.option(
    "--rollback/--no-rollback",
    default=False,
    help="Rollback to previous version",
    type=bool,
)
@click.option(
    "--update/--no-update",
    default=False,
    help="Update inputs before rebuild",
    type=bool,
)
@click.option(
    "--switch/--no-switch",
    default=False,
    help="Do not switch to newly built config",
    type=bool,
)
def rebuild_user(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the current user home-manager config"""

    args: list[str] = (
        [
            "home-manager",
            "switch" if switch else "build",
            "--flake",
            os.path.expanduser("~/.dotfiles/#user"),
        ]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else [])
    )

    run_cmd(args, error_msg="home-manager rebuild failed", capture=False)


@config.command()
@click.option(
    "--rollback/--no-update",
    default=False,
    help="Rollback to previous version",
    type=bool,
)
@click.option(
    "--update/--no-update",
    default=False,
    help="Update inputs before config",
    type=bool,
)
@click.option(
    "--switch/--no-switch",
    default=False,
    help="Do not switch to newly built config",
    type=bool,
)
def rebuild_system(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the system config"""

    args: list[str] = (
        [
            "sudo",
            "nixos-rebuild",
            "switch" if switch else "build",
            "--flake",
            os.path.expanduser("~/.dotfiles/#system"),
        ]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else [])
    )
    run_cmd(args, error_msg="nixos-rebuild failed", capture=False)


@config.command()
@click.option(
    "--rollback/--no-update",
    default=False,
    help="Rollback to previous version",
    type=bool,
)
@click.option(
    "--update/--no-update",
    default=False,
    help="Update inputs before rebuild",
    type=bool,
)
@click.option(
    "--switch/--no-switch",
    default=False,
    help="Do not switch to newly built config",
    type=bool,
)
def rebuild_all(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the system and user configs"""

    system_args: list[str] = (
        [
            "sudo",
            "nixos-rebuild",
            "switch" if switch else "build",
            "--flake",
            os.path.expanduser("~/.dotfiles/#system"),
        ]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else [])
    )

    user_args: list[str] = (
        [
            "home-manager",
            "switch" if switch else "build",
            "--flake",
            os.path.expanduser("~/.dotfiles/#user"),
        ]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else [])
    )

    run_cmd(system_args, error_msg="nixos-rebuild failed", capture=False)

    run_cmd(user_args, error_msg="home-manager rebuild failed", capture=False)
