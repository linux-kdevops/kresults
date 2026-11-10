# SPDX-License-Identifier: copyleft-next-0.3.1
{
  description = "kresults: a format for what tools report on a source tree";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      # validate needs only Python, so macOS checks records too.
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      # The Python of validate, whose schemas jsonschema checks.
      python = pkgs: pkgs.python3.withPackages (ps: [ ps.jsonschema ]);
    in
    {
      packages = forAllSystems (pkgs: rec {
        validate = pkgs.writeShellApplication {
          name = "validate";
          runtimeInputs = [ (python pkgs) ];
          text = ''exec python3 ${./format}/validate.py "$@"'';
        };
        default = validate;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (python pkgs)
            pkgs.black
            pkgs.ruff
            pkgs.pyright
          ];
        };
      });

      checks = forAllSystems (pkgs: {
        lint =
          pkgs.runCommand "kresults-lint"
            {
              nativeBuildInputs = [
                (python pkgs)
                pkgs.black
                pkgs.ruff
                pkgs.pyright
              ];
            }
            ''
              cd ${./.}
              black --check format tests
              ruff check --no-cache format tests
              HOME=$TMPDIR pyright
              touch $out
            '';

        tests =
          pkgs.runCommand "kresults-tests"
            {
              nativeBuildInputs = [ (python pkgs) ];
            }
            ''
              cd ${./.}
              python3 -m unittest discover --start-directory tests
              touch $out
            '';
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
