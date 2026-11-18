# SPDX-License-Identifier: copyleft-next-0.3.1
{
  description = "kresults: a format for what tools report on a source tree";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs, ... }:
    let
      # The revision of krender a site names in its status bar.
      revision = self.rev or self.dirtyRev or "unknown";
      # validate needs only Python, so macOS checks records too.
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
      # The Python of validate, whose schemas jsonschema checks.
      python = pkgs: pkgs.python3.withPackages (ps: [ ps.jsonschema ]);
      # Sphinx with what docs/conf.py loads.
      docsPython =
        pkgs:
        pkgs.python3.withPackages (ps: [
          ps.sphinx
          ps.sphinx-autobuild
          ps.sphinx-copybutton
          ps.pydata-sphinx-theme
          ps.myst-parser
        ]);
    in
    {
      packages = forAllSystems (pkgs: rec {
        validate = pkgs.writeShellApplication {
          name = "validate";
          runtimeInputs = [ (python pkgs) ];
          text = ''exec python3 ${./format}/validate.py "$@"'';
        };
        # Its record names the tools it finds on the caller's PATH.
        krecord = pkgs.writeShellApplication {
          name = "krecord";
          runtimeInputs = [
            pkgs.python3
            pkgs.git
          ];
          text = ''exec python3 ${./format}/krecord.py "$@"'';
        };
        kseries = pkgs.writeShellApplication {
          name = "kseries";
          runtimeInputs = [
            pkgs.python3
            pkgs.git
          ];
          text = ''exec python3 ${./format}/series.py "$@"'';
        };
        kobjects = pkgs.writeShellApplication {
          name = "kobjects";
          runtimeInputs = [
            pkgs.python3
            pkgs.git
          ];
          text = ''exec python3 ${./format}/objects.py "$@"'';
        };
        kblktests = pkgs.writeShellApplication {
          name = "kblktests";
          runtimeInputs = [
            pkgs.python3
            pkgs.git
          ];
          text = ''exec python3 ${./format}/blktests.py "$@"'';
        };
        render = pkgs.writeShellApplication {
          name = "krender";
          runtimeInputs = [
            pkgs.python3
            pkgs.git
          ];
          text = ''exec python3 ${./format}/render.py --revision ${nixpkgs.lib.escapeShellArg revision} "$@"'';
        };
        # The six programs and the units, which find them by store path
        # where a clone's units search a developer's own directories.
        kresults = pkgs.symlinkJoin {
          name = "kresults";
          paths = [
            validate
            krecord
            kseries
            kobjects
            kblktests
            render
          ];
          postBuild = ''
            mkdir --parents --verbose $out/lib/systemd/user
            for unit in ${./units/user}/*; do
              substitute $unit $out/lib/systemd/user/$(basename $unit) \
                --replace-quiet \
                  "ExecSearchPath=%h/.local/bin:%h/.nix-profile/bin:/usr/local/bin:/usr/bin" \
                  "ExecSearchPath=$out/bin:${pkgs.python3}/bin"
            done
          '';
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
            (docsPython pkgs)
            pkgs.python3Packages.doc8
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
              black --check format scripts tests
              ruff check --no-cache format scripts tests
              HOME=$TMPDIR pyright
              touch $out
            '';

        tests =
          pkgs.runCommand "kresults-tests"
            {
              nativeBuildInputs = [
                (python pkgs)
                pkgs.git
              ];
            }
            ''
              cd ${./.}
              python3 -m unittest discover --start-directory tests
              touch $out
            '';

        # designs/writing.md, "The checks".
        prose = pkgs.runCommand "kresults-prose" { nativeBuildInputs = [ pkgs.python3 ]; } ''
          cd ${./.}
          python3 scripts/check-prose.py --file *.md designs/*.md docs/*.rst \
            format/*.md format/*.py scripts/*.py tests/*.py units/user/* \
            flake.nix ruff.toml
          touch $out
        '';

        # Sphinx with its warnings as errors, and the pages at 80 columns.
        docs =
          pkgs.runCommand "kresults-docs"
            {
              nativeBuildInputs = [
                (docsPython pkgs)
                pkgs.python3Packages.doc8
              ];
            }
            ''
              cd ${./.}
              doc8 docs
              sphinx-build --fail-on-warning --builder html docs $out
            '';
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
