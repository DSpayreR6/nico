{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  buildInputs = [
    pkgs.python312
    pkgs.python312Packages.flask
    pkgs.python312Packages.tree-sitter
    pkgs.tree-sitter-grammars.tree-sitter-nix
  ];

  shellHook = ''
    export TREE_SITTER_NIX_GRAMMAR="${pkgs.tree-sitter-grammars.tree-sitter-nix}/parser"
  '';
}
