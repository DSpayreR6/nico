{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  buildInputs = [
    pkgs.python3
    pkgs.python3Packages.flask
    pkgs.python3Packages.tree-sitter
    pkgs.tree-sitter-grammars.tree-sitter-nix
  ];

  shellHook = ''
    export TREE_SITTER_NIX_GRAMMAR="${pkgs.tree-sitter-grammars.tree-sitter-nix}/parser"
  '';
}
