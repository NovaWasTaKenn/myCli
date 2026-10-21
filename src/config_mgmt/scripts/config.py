import click
import os

from helpers.shell import run_cmd

DOTFILES = os.path.expanduser("~/.dotfiles/")


@click.group
def config():
    pass


@config.command()
def system_clean():
    """Clean up old nix generations and optimize the store."""
    run_cmd(["sudo", "nix-collect-garbage", "-d"], error_msg="nix-collect-garbage failed", capture=False)
    run_cmd(["home-manager", "expire-generations", "-d"], error_msg="home-manager expire-generations failed", capture=False)
    run_cmd(["nix", "store", "gc"], error_msg="nix store gc failed", capture=False)
    run_cmd(["sudo", "nix", "store", "optimize"], error_msg="nix store optimize failed", capture=False)
    run_cmd(["sudo", "nix", "profile", "wipe-history"], error_msg="nix profile wipe-history failed", capture=False)
    run_cmd(["home-manager", "remove-generations", "old"], error_msg="home-manager remove-generations failed", capture=False)


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
        ["nix", "flake", "update", flakeinput, "--flake", os.path.expanduser(flakepath)],
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
    run_cmd(["nix", "build", f"./#{packagename or flakeinput}"], error_msg="nix build failed", capture=False)
    run_cmd(["nix", "flake", "update", flakeinput, "--flake", DOTFILES], error_msg="nix flake update failed", capture=False)
    run_cmd(["home-manager", "switch", "--flake", f"{DOTFILES}#user"], error_msg="home-manager switch failed", capture=False)


@config.command()
@click.option("--rollback/--no-rollback", default=False, help="Rollback to previous version")
@click.option("--update/--no-update", default=False, help="Update inputs before rebuild")
@click.option("--switch/--no-switch", default=False, help="Switch to the newly built config")
def rebuild_user(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the current user home-manager config"""
    run_cmd(
        ["home-manager", "switch" if switch else "build", "--flake", f"{DOTFILES}#user"]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else []),
        error_msg="home-manager rebuild failed",
        capture=False,
    )


@config.command()
@click.option("--rollback/--no-rollback", default=False, help="Rollback to previous version")
@click.option("--update/--no-update", default=False, help="Update inputs before config")
@click.option("--switch/--no-switch", default=False, help="Switch to the newly built config")
def rebuild_system(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the system config"""
    run_cmd(
        ["sudo", "nixos-rebuild", "switch" if switch else "build", "--flake", f"{DOTFILES}#system"]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else []),
        error_msg="nixos-rebuild failed",
        capture=False,
    )


@config.command()
@click.option("--rollback/--no-rollback", default=False, help="Rollback to previous version")
@click.option("--update/--no-update", default=False, help="Update inputs before rebuild")
@click.option("--switch/--no-switch", default=False, help="Switch to the newly built config")
def rebuild_all(rollback: bool, update: bool, switch: bool):
    """Command that rebuilds the system and user configs"""
    run_cmd(
        ["sudo", "nixos-rebuild", "switch" if switch else "build", "--flake", f"{DOTFILES}#system"]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else []),
        error_msg="nixos-rebuild failed",
        capture=False,
    )
    run_cmd(
        ["home-manager", "switch" if switch else "build", "--flake", f"{DOTFILES}#user"]
        + (["--rollback"] if rollback else [])
        + (["--update"] if update else []),
        error_msg="home-manager rebuild failed",
        capture=False,
    )
