{
  description = "Leo's personal assistant — Telegram front end over the Claude Agent SDK";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = ["x86_64-linux"];
    forAll = nixpkgs.lib.genAttrs systems;
    # Not legacyPackages: it cannot carry config, and the devShell wants the
    # unfree `claude-code`. Scope the grant to that one package.
    pkgsFor = system:
      import nixpkgs {
        inherit system;
        config.allowUnfreePredicate = pkg: nixpkgs.lib.getName pkg == "claude-code";
      };
  in {
    packages = forAll (system: let
      pkgs = pkgsFor system;
    in {
      default = pkgs.python3Packages.buildPythonApplication {
        pname = "assistant";
        version = "0.0.1";
        pyproject = true;
        src = ./.;
        build-system = [pkgs.python3Packages.hatchling];
        dependencies = with pkgs.python3Packages; [
          httpx
          claude-agent-sdk
          icalendar
          recurring-ical-events
          pypdf
        ];
        # Tests are stdlib-only (sqlite, env); the brain's `claude` CLI is not
        # needed to build or to run these.
        nativeCheckInputs = [pkgs.python3Packages.pytestCheckHook];
      };
    });

    devShells = forAll (system: let
      pkgs = pkgsFor system;
    in {
      default = pkgs.mkShell {
        packages = [
          (pkgs.python3.withPackages (ps:
            with ps; [
              httpx
              claude-agent-sdk
              icalendar
              recurring-ical-events
              pypdf
              pytest
            ]))
          pkgs.ruff
          # On PATH so `assistant` can actually answer during local dev; the
          # Agent SDK shells out to this.
          pkgs.claude-code
        ];
      };
    });

    # `nix flake check` builds the package (which runs pytest via the check hook).
    checks = forAll (system: {
      default = self.packages.${system}.default;
    });
  };
}
