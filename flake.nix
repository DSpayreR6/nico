{
  description = "NiCo – local NixOS configurator with web GUI";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          # Default python, not a pinned minor version: only the default is
          # fully built by Hydra, everything else compiles on the user's machine
          pythonEnv = pkgs.python3.withPackages (ps: [
            ps.flask
            ps.tree-sitter
          ]);
          grammar = pkgs.tree-sitter-grammars.tree-sitter-nix;
          desktopItem = pkgs.makeDesktopItem {
            name = "nico";
            exec = "nico";
            icon = "nico";
            desktopName = "NiCo";
            genericName = "NixOS Configurator";
            comment = "Local browser-based configurator for NixOS";
            categories = [ "System" ];
            keywords = [ "nixos" "nix" "config" "configurator" ];
            terminal = false;
            # German menu text; other locales fall back to the entries above
            extraConfig = {
              "GenericName[de]" = "NixOS-Konfigurator";
              "Comment[de]" = "Lokaler browserbasierter Konfigurator für NixOS";
            };
          };
        in {
          default = pkgs.stdenv.mkDerivation {
            pname = "nico";
            version = "0.12.0"; # update on each release

            src = ./.;

            nativeBuildInputs = [ pkgs.makeWrapper ];

            installPhase = ''
              runHook preInstall

              mkdir -p $out/lib/nico
              cp -r nico $out/lib/nico/

              # flake.nix is not part of the package, so stamp the version in:
              # it stays maintained in exactly one place (see nico/version.py)
              printf '%s' "$version" > $out/lib/nico/nico/VERSION

              mkdir -p $out/bin
              makeWrapper ${pythonEnv}/bin/python $out/bin/nico \
                --add-flags "-m nico.main" \
                --set PYTHONPATH "$out/lib/nico" \
                --set TREE_SITTER_NIX_GRAMMAR "${grammar}/parser"

              # Desktop entry and icons (freedesktop layout, picked up by any DE)
              mkdir -p $out/share/applications
              cp ${desktopItem}/share/applications/* $out/share/applications/

              for size in 16 24 32 48 64 128 256 512; do
                install -Dm644 assets/icons/nico-$size.png \
                  "$out/share/icons/hicolor/''${size}x''${size}/apps/nico.png"
              done

              runHook postInstall
            '';
          };
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/nico";
        };
      });

      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          grammar = pkgs.tree-sitter-grammars.tree-sitter-nix;
        in {
          default = pkgs.mkShell {
            buildInputs = [
              pkgs.python3
              pkgs.python3Packages.flask
              pkgs.python3Packages.tree-sitter
              grammar
            ];
            shellHook = ''
              export TREE_SITTER_NIX_GRAMMAR="${grammar}/parser"
            '';
          };
        }
      );
    };
}
