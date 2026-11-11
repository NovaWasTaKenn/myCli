{
  description = "Ephemeral scala dev env";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };

  outputs = inputs @ {...}: let
    system = "x86_64-linux";
    pkgs = import inputs.nixpkgs {
      system = system;
    };

    myCli = pkgs.python3Packages.buildPythonPackage rec {
      pname = "nova-cli";
      version = "0.1.0";
      format = "setuptools";
      src = ./.;

      propagatedBuildInputs = [
        pkgs.python313Packages.click
        pkgs.python313Packages.azure-servicebus
        pkgs.python313Packages.azure-search-documents
        pkgs.python313Packages.azure-identity
        pkgs.python313Packages.jmespath
        pkgs.python313Packages.pyyaml
        pkgs.python313Packages.requests
        pkgs.ripgrep
      ];

      nativeBuildInputs = [pkgs.installShellFiles];

      postInstall = ''
        installShellCompletion --cmd mycli \
          --zsh <(_MYCLI_COMPLETE=zsh_source $out/bin/mycli)
      '';

      meta = with pkgs.lib; {
        description = "My personal CLI containing all the custom commands i need";
        homepage = "https://github.com/NovaWassTakenn/nova-cli";
        license = licenses.mit;
        maintainers = with maintainers; [NovaWasTakenn];
      };
    };
  in {
    # importing package example
    # packages."x86_64-linux".default =
    #   pkgs.callPackage (import ./default.nix) {};

    devShells."x86_64-linux".default = pkgs.mkShell {
      packages = with pkgs; [
        python313
        python313Packages.click
        python313Packages.azure-servicebus
        python313Packages.azure-search-documents
        python313Packages.azure-identity
        python313Packages.cock
        python313Packages.jmespath
        python313Packages.pyyaml
        python313Packages.requests
      ];

      shellHook = "echo 'Welcome to a python dev env'";
            #PROJECT_MGMT_PROJECT = "mycli";
    };

    packages.${system} = {
      myCli = myCli;
      default = myCli;
    };
  };
}
