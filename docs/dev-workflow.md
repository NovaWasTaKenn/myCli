# Dev workflow

`myCli` reaches the installed `mycli` binary only via the root dotfiles
flake's `myCli` GitHub input (`github:NovaWasTakenn/myCli/main`), not a local
path. Local edits here only take effect system-wide after they're pushed and
the flake input is updated. Iterate locally instead:

1. Fast inner loop, no build: from `src/`, run
   `PYTHONPATH=. python -m base.scripts.mycli ...` (needs Click/PyYAML/requests
   on the path — use the devShell, e.g. `nix develop`, or a venv with
   `pip install -e .`).
2. Before calling a change done, validate the real packaged build:
   `nix build .` then exercise commands via `./result/bin/mycli ...`. This
   catches packaging issues (missing `propagatedBuildInputs`, entry-point
   drift) the fast loop can't. Note: a flake's `src = ./.;` only includes
   **git-tracked** files — `git add` new files (no need to commit) before
   `nix build` picks them up.
3. Point `NOVA_CLI_CONFIG` at a scratch YAML file for both loops so the real
   `~/.config/nova-cli/config.yaml` is untouched; for `project_mgmt`, also set
   `project_mgmt.vault_path` to a scratch directory so test runs never touch
   a real Obsidian vault.
4. Once validated: push this repo, then `mycli config update myCli` /
   `mycli config rebuild-user --switch` from the parent dotfiles repo to reach
   the real system.
