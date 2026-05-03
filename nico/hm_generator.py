"""
Home Manager configuration generator.

Generates a home.nix file from NiCo's Home Manager config data.
This module is wired into the GUI via the HM file browser panel.

Supported options (initial set, covering the most common 80/20 needs):
  - programs.git            (name, email, defaultBranch)
  - programs.bash/zsh/fish  (enable + init extras)
  - home.packages           (list of pkgs attr names)
  - programs.firefox        (enable)
  - xdg.userDirs            (standard XDG directories)
  - home.stateVersion       (required by Home Manager)
  - home.username / home.homeDirectory

Data shape (as stored in nico.json under the key "home_manager"):
{
  "enabled":          false,        # master switch
  "username":         "",           # must match the system user
  "home_dir":         "",           # e.g. /home/alice
  "state_version":    "24.11",

  "git_enable":       false,
  "git_name":         "",
  "git_email":        "",
  "git_default_branch": "main",

  "shell":            "bash",       # bash | zsh | fish
  "shell_init_extra": "",           # appended to the shell init file

  "packages":         [],           # list of pkgs attr names (strings)

  "firefox":          false,

  "xdg_user_dirs":    false,        # enable xdg.userDirs
  "xdg_download":     "Downloads",
  "xdg_documents":    "Documents",
  "xdg_pictures":     "Pictures",
  "xdg_music":        "Music",
  "xdg_videos":       "Videos",
  "xdg_desktop":      "Desktop",
  "xdg_templates":    "Templates",
  "xdg_publicshare":  "Public",
}

Usage:
  from nico.hm_generator import generate_home_nix
  nix_text = generate_home_nix(data["home_manager"])
"""

import hashlib
import re

from .brix import inject_brick_blocks

_MARKER_WIDTH = 78


def _hm_section_top(name: str) -> str:
    """Top-level section marker (no indent): # ── Name ─────────────"""
    dashes = "─" * max(1, _MARKER_WIDTH - len(name) - 6)
    return f"# ── {name} {dashes}"


def _hm_section(name: str) -> str:
    """Indented section marker for inside {}: # ── Name ─────────────"""
    dashes = "─" * max(1, _MARKER_WIDTH - len(name) - 6)
    return f"  # ── {name} {dashes}"


def _add_hm_version_hash(content: str, ftype: str = "hm") -> str:
    """Insert '# nico-version: [type#]<hash>' on line 2 of content."""
    h = hashlib.sha256(content.encode()).hexdigest()[:8]
    lines = content.split("\n")
    prefix = f"{ftype}#" if ftype else ""
    lines.insert(1, f"# nico-version: {prefix}{h}")
    return "\n".join(lines)


# Default values so callers can pass a partial dict
HM_DEFAULTS: dict = {
    "enabled":            False,
    "args":               [],
    "username":           "",
    "home_dir":           "",
    "state_version":      "",

    "git_enable":         False,
    "git_name":           "",
    "git_email":          "",
    "git_default_branch": "",

    "shell":              "",
    "shell_init_extra":   "",

    "packages":           [],

    "firefox":            False,

    "xdg_user_dirs":      False,
    "xdg_download":       "",
    "xdg_documents":      "",
    "xdg_pictures":       "",
    "xdg_music":          "",
    "xdg_videos":         "",
    "xdg_desktop":        "",
    "xdg_templates":      "",
    "xdg_publicshare":    "",
}


HM_SECTION_START = "Start"
HM_SECTION_MAIN = "Home Manager"
HM_SECTION_END = "End"


def _g(data: dict, key: str):
    """Get a value from data, falling back to HM_DEFAULTS."""
    return data.get(key, HM_DEFAULTS.get(key))


_HM_CORE_ARGS = ["config", "pkgs", "lib"]
_HM_ARG_NAME_RE = re.compile(r"^[A-Za-z_][\w'-]*")
_HM_INFER_PATTERNS = [
    re.compile(r"\bif\s+([A-Za-z_][\w'-]*)\b"),
    re.compile(r"\$\{\s*([A-Za-z_][\w'-]*)\b"),
]
_HM_SKIP_INFER_NAMES = {
    "config", "pkgs", "lib", "self", "super", "builtins",
    "true", "false", "null", "if", "then", "else", "let", "in",
    "with", "inherit", "assert", "rec",
}


def _hm_arg_base(arg: str) -> str | None:
    m = _HM_ARG_NAME_RE.match((arg or "").strip())
    return m.group(0) if m else None


def _infer_hm_brick_args(blocks: dict[str, dict], known_args: list[str]) -> list[str]:
    known_names = {name for name in (_hm_arg_base(arg) for arg in known_args) if name}
    inferred: list[str] = []
    for block in blocks.values():
        text = block.get("text", "")
        body_lines = text.splitlines()[1:-1]
        body = "\n".join(body_lines)
        for pattern in _HM_INFER_PATTERNS:
            for match in pattern.finditer(body):
                name = match.group(1)
                if name in _HM_SKIP_INFER_NAMES or name in known_names:
                    continue
                known_names.add(name)
                inferred.append(f"{name} ? false")
    return inferred


def _hm_render_args(data: dict, blocks: dict[str, dict]) -> str:
    args = []
    seen = set()
    for arg in (_g(data, "args") or []):
        cleaned = (arg or "").strip()
        base = _hm_arg_base(cleaned)
        if not cleaned or not base or base in seen:
            continue
        seen.add(base)
        args.append(cleaned)

    for core in _HM_CORE_ARGS:
        if core not in seen:
            seen.add(core)
            args.append(core)

    for inferred in _infer_hm_brick_args(blocks, args):
        base = _hm_arg_base(inferred)
        if base and base not in seen:
            seen.add(base)
            args.append(inferred)

    return "{ " + ", ".join(args + ["..."]) + " }:"


def generate_home_nix(data: dict) -> str:
    """
    Generate a home.nix string from the given Home Manager config dict.
    Missing keys fall back to HM_DEFAULTS for UI-controlled feature toggles.
    Identity and stateVersion are only written when explicitly present in data.
    Returns a complete, standalone home.nix that can be imported by
    Home Manager (standalone or as a NixOS module).
    """
    username      = (data.get("username") or "").strip()
    home_dir      = (data.get("home_dir") or "").strip()
    state_version = (data.get("state_version") or "").strip()

    git_enable    = _g(data, "git_enable")
    git_name      = _g(data, "git_name")
    git_email     = _g(data, "git_email")
    git_branch    = _g(data, "git_default_branch")

    shell         = _g(data, "shell")
    shell_extra   = (_g(data, "shell_init_extra") or "").strip()

    packages      = _g(data, "packages") or []
    firefox       = _g(data, "firefox")

    xdg_dirs      = _g(data, "xdg_user_dirs")
    brix          = data.get("hm_brick_blocks", {})
    args_header   = _hm_render_args(data, brix)

    lines = [
        "# Generated by NiCo – NixOS Configurator (Home Manager)",
        "# Do not edit manually. Use NiCo or add custom options via Nix-Brix.",
        _hm_section_top(HM_SECTION_START),
        args_header,
        "{",
        _hm_section(HM_SECTION_MAIN),
    ]

    # ── Identity ─────────────────────────────────────────────────────────
    if username:
        lines += [f'  home.username      = "{username}";']
    if home_dir:
        lines += [f'  home.homeDirectory = "{home_dir}";']

    # ── Packages ─────────────────────────────────────────────────────────
    if packages:
        pkg_lines = "\n".join(f"    pkgs.{attr}" for attr in sorted(packages))
        lines += [
            "",
            "  home.packages = [",
            pkg_lines,
            "  ];",
        ]

    # ── Git ───────────────────────────────────────────────────────────────
    if git_enable:
        lines += ["", "  programs.git = {", "    enable = true;"]
        if git_name:
            lines += [f'    userName  = "{git_name}";']
        if git_email:
            lines += [f'    userEmail = "{git_email}";']
        if git_branch:
            lines += [
                "    extraConfig = {",
                f'      init.defaultBranch = "{git_branch}";',
                "    };",
            ]
        lines += ["  };"]

    # ── Shell ─────────────────────────────────────────────────────────────
    if shell in ("bash", "zsh", "fish"):
        lines += ["", f"  programs.{shell} = {{", "    enable = true;"]
        if shell_extra:
            extra_key = {
                "bash": "initExtra",
                "zsh":  "initExtra",
                "fish": "shellInit",
            }[shell]
            # Indent each line of the extra content
            indented = "\n".join(f"      {ln}" for ln in shell_extra.splitlines())
            lines += [
                f"    {extra_key} = ''",
                indented,
                "    '';",
            ]
        lines += ["  };"]

    # ── Firefox ───────────────────────────────────────────────────────────
    if firefox:
        lines += ["", "  programs.firefox.enable = true;"]

    # ── XDG user directories ─────────────────────────────────────────────
    if xdg_dirs:
        xdg_values = [
            ("download", "xdg_download"),
            ("documents", "xdg_documents"),
            ("pictures", "xdg_pictures"),
            ("music", "xdg_music"),
            ("videos", "xdg_videos"),
            ("desktop", "xdg_desktop"),
            ("templates", "xdg_templates"),
            ("publicShare", "xdg_publicshare"),
        ]
        lines += [
            "",
            "  xdg.userDirs = {",
            "    enable     = true;",
            "    createDirectories = true;",
        ]
        for nix_key, data_key in xdg_values:
            value = _g(data, data_key)
            if value:
                lines += [f'    {nix_key} = "${{config.home.homeDirectory}}/{value}";']
        lines += ["  };"]

    # ── stateVersion ────────────────────────────────────────────────────
    if state_version:
        lines += ["", f'  home.stateVersion = "{state_version}";']

    lines += [
        _hm_section(HM_SECTION_END),
        "",
        "}",
        "",
    ]
    content = "\n".join(lines)
    if brix:
        content = inject_brick_blocks(content, brix)
    return _add_hm_version_hash(content, ftype="hm")
