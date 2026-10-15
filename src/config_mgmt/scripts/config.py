import click
import os
from typing import List

from helpers.shell import run_cmd

DOTFILES = os.path.expanduser("~/.dotfiles/")


@click.group
def config():
    pass


def _run_steps(steps: List[List[str]]) -> None:
    """Run commands in order, attached to the terminal, aborting on the first failure."""
    for cmd in steps:
        run_cmd(cmd, error_msg=f"{' '.join(cmd)} failed", capture=False)


def _rebuild_cmd(base: List[str], flake_output: str, rollback: bool, update: bool, switch: bool) -> List[str]:
    return (
        [*base, "switch" if switch else "build", "--flake", f"{DOTFILES}#{flake_output}"]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else [])
    )


def _system_rebuild_cmd(rollback: bool, update: bool, switch: bool) -> List[str]:
    return _rebuild_cmd(["sudo", "nixos-rebuild"], "system", rollback, update, switch)


def _user_rebuild_cmd(rollback: bool, update: bool, switch: bool) -> List[str]:
    return _rebuild_cmd(["home-manager"], "user", rollback, update, switch)


def rebuild_options(update_help: str):
    """Shared --rollback/--update/--switch options of the rebuild commands."""
    def decorator(f):
        f = click.option("--switch/--no-switch", default=False, help="Switch to the newly built config")(f)
        f = click.option("--update/--no-update", default=False, help=update_help)(f)
        f = click.option("--rollback/--no-rollback", default=False, help="Rollback to previous version")(f)
        return f
    return decorator


@config.command()
def system_clean():
    """Clean up old nix generations and optimize the store."""
    _run_steps([
        ["sudo", "nix-collect-garbage", "-d"],
        ["home-manager", "expire-generations", "-d"],
        ["nix", "store", "gc"],
        ["sudo", "nix", "store", "optimize"],
        ["sudo", "nix", "profile", "wipe-history"],
        ["home-manager", "remove-generations", "old"],
    ])


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
    _run_steps([["nix", "flake", "update", flakeinput, "--flake", os.path.expanduser(flakepath)]])


@config.command()
@click.argument("flakeinput", nargs=1, type=str)
@click.option(
    "--packagename",
    help="Name of the package to build",
    type=str,
)
def reload(flakeinput: str, packagename: str):
    """Command that builds packageName package, updates the inputFlakeName flake input in the config and rebuilds the home config to reload the changes"""
    _run_steps([
        ["nix", "build", f"./#{packagename or flakeinput}"],
        ["nix", "flake", "update", flakeinput, "--flake", DOTFILES],
        ["home-manager", "switch", "--flake", f"{DOTFILES}#user"],
    ])


@config.command()
@rebuild_options("Update inputs before rebuild")
def rebuild_user(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the current user home-manager config"""
    _run_steps([_user_rebuild_cmd(rollback, update, switch)])


@config.command()
@rebuild_options("Update inputs before config")
def rebuild_system(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the system config"""
    _run_steps([_system_rebuild_cmd(rollback, update, switch)])


@config.command()
@rebuild_options("Update inputs before rebuild")
def rebuild_all(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the system and user configs"""
    _run_steps([
        _system_rebuild_cmd(rollback, update, switch),
        _user_rebuild_cmd(rollback, update, switch),
    ])
