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
          pythonEnv = pkgs.python312.withPackages (ps: [
            ps.flask
            ps.tree-sitter
          ]);
          grammar = pkgs.tree-sitter-grammars.tree-sitter-nix;
        in {
          default = pkgs.stdenv.mkDerivation {
            pname = "nico";
            version = "0.9.4"; # update on each release

            src = ./.;

            nativeBuildInputs = [ pkgs.makeWrapper ];

            installPhase = ''
              runHook preInstall

              mkdir -p $out/lib/nico
              cp -r nico $out/lib/nico/

              mkdir -p $out/bin
              makeWrapper ${pythonEnv}/bin/python $out/bin/nico \
                --add-flags "-m nico.main" \
                --set PYTHONPATH "$out/lib/nico" \
                --set TREE_SITTER_NIX_GRAMMAR "${grammar}/parser"

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
              pkgs.python312
              pkgs.python312Packages.flask
              pkgs.python312Packages.tree-sitter
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
