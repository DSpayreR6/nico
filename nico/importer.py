"""
Import: liest eine bestehende configuration.nix und überträgt bekannte Felder
in nico.json.  Unbekannter Inhalt landet als Brix damit nichts verloren geht.

Strategie:
  1. Bekannte Einfach-Optionen per Regex extrahieren.
  2. Bekannte Block-Optionen (z.B. services.avahi = { ... }) auflösen.
  3. Extrahierten Inhalt + Boilerplate aus dem Original entfernen.
  4. Rest als Brix sichern.
"""

import re
import shutil
from pathlib import Path
from .brix import strip_brick_blocks

ETC_NIXOS = Path("/etc/nixos")


# ── helpers ───────────────────────────────────────────────────────────────────

def _strip_nix_comments(text: str) -> str:
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'#(?!\s*</?bri(?:x|ck):)[^\n]*', '', text)
    return text


def _split_top_level_stmts(content: str) -> list[str]:
    statements, depth, start = [], 0, 0
    for i, ch in enumerate(content):
        if ch in ('{', '[', '('):
            depth += 1
        elif ch in ('}', ']', ')'):
            depth -= 1
        elif ch == ';' and depth == 0:
            stmt = content[start:i + 1].strip()
            if stmt:
                statements.append(stmt)
            start = i + 1
    leftover = content[start:].strip()
    if leftover:
        statements.append(leftover)
    return statements


def _brix_name_from_stmt(stmt: str) -> str:
    m = re.match(r'\s*([\w.\-]+)', stmt)
    return m.group(1).replace('.', '-') if m else 'imported'

def _str(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else None


def _bool(text: str, pattern: str) -> bool | None:
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if not m:
        return None
    return m.group(1).strip() == 'true'


def _block(text: str, key: str) -> str | None:
    """Inhalt von 'key = { ... }' zurückgeben (keine verschachtelten Blöcke)."""
    m = re.search(
        rf'(?m)^\s*{re.escape(key)}\s*=\s*\{{([^}}]*)\}}',
        text, re.DOTALL
    )
    return m.group(1) if m else None


def _remove_deep_block(content: str, key: str) -> str:
    """Entfernt 'key = { ... }' auch mit beliebig verschachtelten Blöcken."""
    m = re.search(rf'\n?[ \t]*{re.escape(key)}\s*=\s*\{{', content)
    if not m:
        return content
    brace_pos = content.index('{', m.start())
    depth, i = 0, brace_pos
    while i < len(content):
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                while end < len(content) and content[end] in ' \t':
                    end += 1
                if end < len(content) and content[end] == ';':
                    end += 1
                return content[:m.start()] + content[end:]
        i += 1
    return content


def _str_multi(text: str, *patterns: str) -> str | None:
    for p in patterns:
        v = _str(text, p)
        if v is not None:
            return v
    return None


def _bool_multi(text: str, *patterns: str) -> bool | None:
    for p in patterns:
        v = _bool(text, p)
        if v is not None:
            return v
    return None


# ── tree-sitter based parse/brix (with regex fallback) ───────────────────────

def _ts_parse_config(nix_content: str) -> 'tuple[dict, set[str]] | None':
    """
    Tree-sitter based parse_config.
    Returns (recognized_dict, consumed_binding_keys) or None when unavailable.
    consumed_binding_keys: set of top-level binding keys that were mapped.
    """
    from . import nix_parser as _np
    from .brix import strip_brick_blocks

    clean   = strip_brick_blocks(nix_content)
    result  = _np.parse(clean)
    if not result.available:
        return None

    kv: dict[str, str] = _np.make_kv(result)
    consumed: set[str] = set()
    r: dict = {}

    def _vt(nix_key: str) -> 'str | None':
        """Flat lookup, then block fallback trying all possible parent prefixes."""
        if nix_key in kv:
            return kv[nix_key]
        # Try every split point from longest parent to shortest.
        # e.g. services.pipewire.alsa.support32Bit:
        #   parent=services.pipewire.alsa / child=support32Bit       → not in kv
        #   parent=services.pipewire     / child=alsa.support32Bit   → in kv → search inner
        key_parts = nix_key.split('.')
        for i in range(len(key_parts) - 1, 0, -1):
            parent = '.'.join(key_parts[:i])
            child  = '.'.join(key_parts[i:])
            if parent in kv:
                inner = _np.extract_inner_block(kv[parent])
                if inner:
                    m = re.search(
                        rf'(?<![.\w]){re.escape(child)}'
                        r'\s*=\s*([^;{]+|\{[^{}]*\})\s*;',
                        inner,
                    )
                    if m:
                        return m.group(1).strip()
        return None

    def _mark(nix_key: str):
        if nix_key in kv:
            consumed.add(nix_key)
        else:
            parts = nix_key.rsplit('.', 1)
            if len(parts) == 2 and parts[0] in kv:
                consumed.add(parts[0])

    def s(nico_key, *nix_keys):
        for nk in nix_keys:
            vt = _vt(nk)
            if vt is not None:
                v = _np.extract_string(vt)
                if v is not None:
                    r[nico_key] = v; _mark(nk); return

    def b(nico_key, *nix_keys):
        for nk in nix_keys:
            vt = _vt(nk)
            if vt is not None:
                v = _np.extract_bool(vt)
                if v is not None:
                    r[nico_key] = v; _mark(nk); return

    # ── Module args (regex – not a standard binding) ──────────────────────────
    m_args = re.search(r'^\s*\{\s*([^}]+)\s*\}\s*:', clean, re.MULTILINE)
    if m_args:
        args_clean = ', '.join(a.strip() for a in m_args.group(1).split(',') if a.strip())
        if args_clean:
            r['nix_args'] = args_clean

    # ── System ────────────────────────────────────────────────────────────────
    s('hostname',      'networking.hostName')
    s('state_version', 'system.stateVersion')
    b('allowUnfree',   'nixpkgs.config.allowUnfree')

    # ── Lokalisierung ─────────────────────────────────────────────────────────
    s('timezone', 'time.timeZone')
    s('locale',   'i18n.defaultLocale')

    vt = _vt('i18n.extraLocaleSettings')
    if vt is not None:
        inner = _np.extract_inner_block(vt) or vt
        m = re.search(r'LC_\w+\s*=\s*"([^"]*)"', inner)
        if m:
            r['extra_locale'] = m.group(1); _mark('i18n.extraLocaleSettings')

    xkb_vt = _vt('services.xserver.xkb')
    if xkb_vt:
        inner = _np.extract_inner_block(xkb_vt) or ''
        for attr, nico_k in [('layout', 'keyboard_layout'), ('variant', 'keyboard_variant')]:
            m = re.search(rf'{attr}\s*=\s*"([^"]*)"', inner)
            if m:
                r[nico_k] = m.group(1); _mark('services.xserver.xkb')
    else:
        s('keyboard_layout',  'services.xserver.xkb.layout')
        s('keyboard_variant', 'services.xserver.xkb.variant')
    s('keyboard_console', 'console.keyMap')

    # ── Boot ──────────────────────────────────────────────────────────────────
    for loader_key, loader_name in [
        ('boot.loader.systemd-boot.enable', 'systemd-boot'),
        ('boot.loader.grub.enable',         'grub'),
    ]:
        vt = _vt(loader_key)
        if vt and _np.extract_bool(vt):
            r['boot_loader'] = loader_name; _mark(loader_key); break

    b('boot_efi_can_touch',   'boot.loader.efi.canTouchEfiVariables')
    s('boot_efi_mount_point', 'boot.loader.efi.efiSysMountPoint')
    vt = _vt('boot.loader.systemd-boot.configurationLimit')
    if vt:
        v = _np.extract_int(vt)
        if v is not None:
            r['boot_config_limit'] = v; _mark('boot.loader.systemd-boot.configurationLimit')

    # ── Netzwerk ──────────────────────────────────────────────────────────────
    b('networkmanager', 'networking.networkmanager.enable')
    b('ssh',            'services.openssh.enable')

    vt = _vt('networking.firewall.enable')
    if vt and _np.extract_bool(vt) is False:
        r['firewall_disable'] = True; _mark('networking.firewall.enable')

    for nico_tcp_udp, fw_key in [
        (('firewall_tcp_ports', 'firewall_tcp_enable'), 'networking.firewall.allowedTCPPorts'),
        (('firewall_udp_ports', 'firewall_udp_enable'), 'networking.firewall.allowedUDPPorts'),
    ]:
        ports_nico, enable_nico = nico_tcp_udp
        vt = kv.get(fw_key)
        if vt is not None:
            nums = re.findall(r'\d+', re.sub(r'#[^\n]*', '', vt))
            ports = ' '.join(nums)
            if ports:
                r[ports_nico] = ports; r[enable_nico] = True
                consumed.add(fw_key)

    # ── Services ──────────────────────────────────────────────────────────────
    b('printing', 'services.printing.enable')
    b('avahi',    'services.avahi.enable')
    b('bluetooth','hardware.bluetooth.enable')
    b('blueman',  'services.blueman.enable')
    b('libinput', 'services.libinput.enable')
    b('fprintd',  'services.fprintd.enable')
    b('pcscd',    'services.pcscd.enable')
    b('sunshine', 'services.sunshine.enable')

    # ── Audio ─────────────────────────────────────────────────────────────────
    b('pipewire',       'services.pipewire.enable')
    b('pipewire_32bit', 'services.pipewire.alsa.support32Bit')

    # ── Desktop ───────────────────────────────────────────────────────────────
    for de_name, de_key in [
        ('gnome',    'services.xserver.desktopManager.gnome.enable'),
        ('kde',      'services.desktopManager.plasma6.enable'),
        ('plasma5',  'services.xserver.desktopManager.plasma5.enable'),
        ('xfce',     'services.xserver.desktopManager.xfce.enable'),
        ('mate',     'services.xserver.desktopManager.mate.enable'),
        ('lxqt',     'services.xserver.desktopManager.lxqt.enable'),
        ('i3',       'services.xserver.windowManager.i3.enable'),
        ('sway',     'programs.sway.enable'),
        ('hyprland', 'programs.hyprland.enable'),
    ]:
        vt = _vt(de_key)
        if vt and _np.extract_bool(vt):
            r['desktop_environment'] = de_name; _mark(de_key); break

    vt = _vt('services.displayManager.autoLogin')
    if vt:
        inner = _np.extract_inner_block(vt) or ''
        m = re.search(r'user\s*=\s*"([^"]*)"', inner)
        if m:
            r['autologin_user'] = m.group(1); _mark('services.displayManager.autoLogin')
    else:
        s('autologin_user',
          'services.displayManager.autoLogin.user',
          'services.xserver.displayManager.autoLogin.user')

    # ── System-Programme ──────────────────────────────────────────────────────
    b('steam',    'programs.steam.enable')
    b('appimage', 'programs.appimage.enable')

    ff_vt = _vt('programs.firefox')
    if ff_vt:
        inner = _np.extract_inner_block(ff_vt) or ''
        m = re.search(r'enable\s*=\s*(true|false)', inner)
        if m:
            r['firefox'] = (m.group(1) == 'true'); _mark('programs.firefox')
        m2 = re.search(r'languagePacks\s*=\s*\[([^\]]*)\]', inner)
        if m2:
            packs = re.findall(r'"([^"]+)"', m2.group(1))
            if packs: r['firefox_lang_packs'] = ', '.join(packs)
        m2 = re.search(r'preferences\s*=\s*\{([^}]*)\}', inner, re.DOTALL)
        if m2:
            lines = [l.strip() for l in m2.group(1).splitlines() if l.strip()]
            if lines: r['firefox_prefs'] = '\n'.join(lines)
    else:
        b('firefox', 'programs.firefox.enable')

    # ── Pakete ────────────────────────────────────────────────────────────────
    if 'environment.systemPackages' in kv:
        pkgs_list = _np.extract_identifier_list(kv['environment.systemPackages']) or []
        r['packages'] = [{"attr": p, "enabled": True} for p in sorted(set(pkgs_list))]
        consumed.add('environment.systemPackages')

    # ── Schriftarten ──────────────────────────────────────────────────────────
    if 'fonts.packages' in kv:
        fonts_list = _np.extract_identifier_list(kv['fonts.packages']) or []
        if fonts_list:
            r['fonts'] = sorted(set(fonts_list))
        consumed.add('fonts.packages')

    # ── Nix & System ──────────────────────────────────────────────────────────
    b('nix_optimize_store', 'nix.settings.auto-optimise-store')

    vt = _vt('nix.settings.experimental-features')
    if vt and 'flakes' in vt:
        r['flakes'] = True; _mark('nix.settings.experimental-features')

    gc_vt = _vt('nix.gc')
    if gc_vt:
        inner = _np.extract_inner_block(gc_vt) or ''
        m = re.search(r'automatic\s*=\s*(true|false)', inner)
        if m: r['nix_gc'] = (m.group(1) == 'true')
        m = re.search(r'dates\s*=\s*"([^"]*)"', inner)
        if m: r['nix_gc_frequency'] = m.group(1)
        m = re.search(r'options\s*=\s*"--delete-older-than\s+([^"]+)"', inner)
        if m: r['nix_gc_age'] = m.group(1).strip()
        _mark('nix.gc')
    else:
        b('nix_gc', 'nix.gc.automatic')
        s('nix_gc_frequency', 'nix.gc.dates')
        vt = _vt('nix.gc.options')
        if vt:
            m = re.search(r'--delete-older-than\s+(\S+)', vt)
            if m: r['nix_gc_age'] = m.group(1); _mark('nix.gc.options')

    # ── Hardware ──────────────────────────────────────────────────────────────
    b('enable_all_firmware', 'hardware.enableAllFirmware')
    b('opengl',     'hardware.opengl.enable',        'hardware.graphics.enable')
    b('opengl_32bit','hardware.opengl.driSupport32Bit','hardware.graphics.enable32Bit')
    b('zram_swap',  'zramSwap.enable')
    b('openrgb',    'services.hardware.openrgb.enable')
    b('ledger',     'hardware.ledger.enable')
    b('ratbagd',    'services.ratbagd.enable')

    for cpu_type in ('intel', 'amd'):
        vt = _vt(f'hardware.cpu.{cpu_type}.updateMicrocode')
        if vt and _np.extract_bool(vt):
            r['cpu_microcode'] = cpu_type
            _mark(f'hardware.cpu.{cpu_type}.updateMicrocode')
            break

    # ── Virtualisierung ───────────────────────────────────────────────────────
    b('docker',               'virtualisation.docker.enable')
    b('docker_rootless',      'virtualisation.docker.rootless.enable')
    b('podman',               'virtualisation.podman.enable')
    b('podman_docker_compat', 'virtualisation.podman.dockerCompat')
    b('virtualbox_host',      'virtualisation.virtualbox.host.enable')
    b('virtualbox_guest',     'virtualisation.virtualbox.guest.enable')
    b('virtualbox_guest_drag_drop', 'virtualisation.virtualbox.guest.dragAndDrop')
    b('libvirtd',             'virtualisation.libvirtd.enable')
    b('virt_manager',         'programs.virt-manager.enable')

    # ── Dateisystem & Backup ──────────────────────────────────────────────────
    b('btrfs_scrub', 'services.btrfs.autoScrub.enable')

    for cfg_name, nico_key in [('home', 'snapper_home'), ('root', 'snapper_root')]:
        sn_key = f'services.snapper.configs.{cfg_name}'
        if sn_key in kv:
            r[nico_key] = True
            consumed.add(sn_key)
            inner = _np.extract_inner_block(kv[sn_key]) or kv[sn_key]
            for fld, limit_key in [
                ('snapper_timeline_hourly',  'TIMELINE_LIMIT_HOURLY'),
                ('snapper_timeline_daily',   'TIMELINE_LIMIT_DAILY'),
                ('snapper_timeline_weekly',  'TIMELINE_LIMIT_WEEKLY'),
                ('snapper_timeline_monthly', 'TIMELINE_LIMIT_MONTHLY'),
                ('snapper_timeline_yearly',  'TIMELINE_LIMIT_YEARLY'),
            ]:
                m2 = re.search(rf'{limit_key}\s*=\s*(\d+)', inner)
                if m2: r[fld] = int(m2.group(1))

    # ── Benutzer ──────────────────────────────────────────────────────────────
    _EXCL   = {'root', 'nobody', 'guest', 'gast'}
    _SHELLS = {'pkgs.zsh': 'zsh', 'pkgs.fish': 'fish',
               'pkgs.bash': 'bash', 'pkgs.nushell': 'nushell'}

    user_bindings = sorted(
        [(k, v) for k, v in kv.items()
         if k.startswith('users.users.') and k.count('.') == 2],
        key=lambda x: x[0]
    )
    primary_done = False
    extra_users: list[dict] = []

    for user_key, user_vt in user_bindings:
        uname = user_key.split('.')[-1]
        if uname in _EXCL:
            continue
        inner = _np.extract_inner_block(user_vt) or user_vt
        consumed.add(user_key)

        def _ue(pattern, text=inner):
            m = re.search(pattern, text)
            return m.group(1) if m else None

        if not primary_done:
            primary_done = True
            r['username'] = uname
            v = _ue(r'description\s*=\s*"([^"]*)"')
            if v: r['user_description'] = v
            v = _ue(r'initialPassword\s*=\s*"([^"]*)"')
            if v: r['user_initial_password'] = v
            v = _ue(r'\buid\s*=\s*(\d+)')
            if v: r['user_uid'] = v
            v = _ue(r'\bshell\s*=\s*(pkgs\.\w+)')
            if v: r['user_shell'] = _SHELLS.get(v, 'bash')
            m = re.search(r'extraGroups\s*=\s*\[([^\]]*)\]', inner)
            if m:
                groups = re.findall(r'"(\w+)"', m.group(1))
                if groups: r['user_groups'] = groups
        else:
            eu: dict = {'username': uname}
            v = _ue(r'description\s*=\s*"([^"]*)"')
            if v: eu['description'] = v
            v = _ue(r'initialPassword\s*=\s*"([^"]*)"')
            if v: eu['initial_password'] = v
            v = _ue(r'\buid\s*=\s*(\d+)')
            if v: eu['uid'] = int(v)
            v = _ue(r'\bshell\s*=\s*(pkgs\.\w+)')
            if v: eu['shell'] = _SHELLS.get(v, 'bash')
            m = re.search(r'extraGroups\s*=\s*\[([^\]]*)\]', inner)
            if m:
                groups = re.findall(r'"(\w+)"', m.group(1))
                if groups: eu['groups'] = groups
            extra_users.append(eu)

    if extra_users:
        r['extra_users'] = extra_users

    if 'users.users.gast' in kv:
        r['guest_user'] = True; consumed.add('users.users.gast')

    # ── Home Manager ──────────────────────────────────────────────────────────
    b('hm_use_global_pkgs',   'home-manager.useGlobalPkgs')
    b('hm_use_user_packages', 'home-manager.useUserPackages')

    if 'home-manager.sharedModules' in kv:
        vt = kv['home-manager.sharedModules']
        entries = re.findall(r'(\S+)', re.sub(r'[\[\]]', '', vt))
        if entries:
            plasma = 'plasma-manager.homeModules.plasma-manager'
            if plasma in entries:
                r['hm_plasma_manager'] = True
                extras = [e for e in entries if e != plasma]
            else:
                extras = entries
            if extras:
                r['hm_shared_modules_extra'] = '\n'.join(extras)
        consumed.add('home-manager.sharedModules')

    return r, consumed


def _ts_build_rest_brix(nix_content: str, consumed: set[str]) -> 'str | None':
    """
    Tree-sitter based build_rest_brix.
    Wraps all unclaimed top-level bindings as brix blocks.
    Returns formatted string or None when unavailable.
    """
    from . import nix_parser as _np
    from .brix import strip_brick_blocks

    clean  = strip_brick_blocks(nix_content).strip()
    result = _np.parse(clean)
    if not result.available:
        return None

    all_bindings = result.known + result.unknown
    unclaimed    = [bi for bi in all_bindings if bi.key not in consumed]

    if not unclaimed:
        return ''

    used: dict[str, int] = {}
    parts = []
    for bi in unclaimed:
        base = _brix_name_from_stmt(bi.full_text)
        if base in used:
            used[base] += 1
            name = f'{base}-{used[base]}'
        else:
            used[base] = 1
            name = base
        parts.append(f'# <brix: {name}>\n{bi.full_text}\n# </brix: {name}>\n')

    return '\n'.join(parts)


# ── public API ────────────────────────────────────────────────────────────────

def check_import_available() -> bool:
    f = ETC_NIXOS / "configuration.nix"
    try:
        return f.is_file() and f.stat().st_size > 0
    except OSError:
        return False


def has_hardware_config() -> bool:
    return (ETC_NIXOS / "hardware-configuration.nix").is_file()


def parse_config(nix_content: str) -> dict:
    """
    Extrahiert alle bekannten Felder aus nix_content.
    Gibt ein flaches Dict zurück das direkt in nico.json gemergt werden kann.
    Nur gefundene Schlüssel sind enthalten (keine Defaults).
    Tree-sitter wird bevorzugt; Regex-Fallback wenn nicht verfügbar.
    """
    ts = _ts_parse_config(nix_content)
    if ts is not None:
        return ts[0]

    r: dict = {}
    c = nix_content  # Kurzname

    def s(key, *patterns):
        v = _str_multi(c, *patterns)
        if v is not None:
            r[key] = v

    def b(key, *patterns):
        v = _bool_multi(c, *patterns)
        if v is not None:
            r[key] = v

    # ── Modulargumente (erste Zeile: { ... }: { )  ────────────────────────────
    m_args = re.search(r'^\s*\{\s*([^}]+)\s*\}\s*:', c, re.MULTILINE)
    if m_args:
        args_clean = ', '.join(a.strip() for a in m_args.group(1).split(',') if a.strip())
        if args_clean:
            r['nix_args'] = args_clean

    # ── System ────────────────────────────────────────────────────────────────
    s('hostname',      r'networking\.hostName\s*=\s*"([^"]*)"')
    s('state_version', r'^\s*system\.stateVersion\s*=\s*"([^"]*)"')
    b('allowUnfree',   r'nixpkgs\.config\.allowUnfree\s*=\s*(true|false)')

    # ── Lokalisierung ─────────────────────────────────────────────────────────
    s('timezone', r'time\.timeZone\s*=\s*"([^"]*)"')
    s('locale',   r'i18n\.defaultLocale\s*=\s*"([^"]*)"')

    # extraLocaleSettings: ersten LC_*-Wert als gemeinsamen Wert nehmen
    m_el = re.search(r'i18n\.extraLocaleSettings\s*=\s*\{([^}]*)\}', c, re.DOTALL)
    if m_el:
        v = re.search(r'LC_\w+\s*=\s*"([^"]*)"', m_el.group(1))
        if v:
            r['extra_locale'] = v.group(1)

    # xkb: Block-Form (NiCo-Output) ODER Einzel-Zeilen-Form
    xkb_block = _block(c, 'services.xserver.xkb')
    if xkb_block:
        v = _str(xkb_block, r'layout\s*=\s*"([^"]*)"')
        if v is not None:
            r['keyboard_layout'] = v
        v = _str(xkb_block, r'variant\s*=\s*"([^"]*)"')
        if v is not None:
            r['keyboard_variant'] = v
    else:
        s('keyboard_layout',  r'services\.xserver\.xkb\.layout\s*=\s*"([^"]*)"')
        s('keyboard_variant', r'services\.xserver\.xkb\.variant\s*=\s*"([^"]*)"')
    s('keyboard_console', r'console\.keyMap\s*=\s*"([^"]*)"')

    # ── Boot ──────────────────────────────────────────────────────────────────
    sdb  = _bool(c, r'boot\.loader\.systemd-boot\.enable\s*=\s*(true|false)')
    grub = _bool(c, r'boot\.loader\.grub\.enable\s*=\s*(true|false)')
    if sdb:
        r['boot_loader'] = 'systemd-boot'
    elif grub:
        r['boot_loader'] = 'grub'

    b('boot_efi_can_touch',   r'boot\.loader\.efi\.canTouchEfiVariables\s*=\s*(true|false)')
    s('boot_efi_mount_point', r'boot\.loader\.efi\.efiSysMountPoint\s*=\s*"([^"]*)"')
    m = re.search(r'boot\.loader\.systemd-boot\.configurationLimit\s*=\s*(\d+)', c)
    if m:
        r['boot_config_limit'] = int(m.group(1))

    # ── Netzwerk ──────────────────────────────────────────────────────────────
    b('networkmanager', r'networking\.networkmanager\.enable\s*=\s*(true|false)')
    b('ssh',            r'services\.openssh\.enable\s*=\s*(true|false)')
    # firewall: enable=false → firewall_disable=True; ports → tcp/udp_enable + ports
    fw_m = re.search(r'networking\.firewall\.enable\s*=\s*(true|false)', c)
    if fw_m and fw_m.group(1) == 'false':
        r['firewall_disable'] = True
    m = re.search(r'networking\.firewall\.allowedTCPPorts\s*=\s*\[([^\]]*)\]', c)
    if m:
        ports = ' '.join(m.group(1).split())
        if ports:
            r['firewall_tcp_ports'] = ports
            r['firewall_tcp_enable'] = True
    m = re.search(r'networking\.firewall\.allowedUDPPorts\s*=\s*\[([^\]]*)\]', c)
    if m:
        ports = ' '.join(m.group(1).split())
        if ports:
            r['firewall_udp_ports'] = ports
            r['firewall_udp_enable'] = True

    # ── Services ──────────────────────────────────────────────────────────────
    b('printing', r'services\.printing\.enable\s*=\s*(true|false)')

    # avahi: Block-Form oder Einzel-Zeile
    avahi_block = _block(c, 'services.avahi')
    if avahi_block:
        v = _bool(avahi_block, r'enable\s*=\s*(true|false)')
        if v is not None:
            r['avahi'] = v
    else:
        b('avahi', r'services\.avahi\.enable\s*=\s*(true|false)')

    b('bluetooth', r'hardware\.bluetooth\.enable\s*=\s*(true|false)')
    b('blueman',   r'services\.blueman\.enable\s*=\s*(true|false)')
    b('libinput',  r'services\.libinput\.enable\s*=\s*(true|false)')
    b('fprintd',   r'services\.fprintd\.enable\s*=\s*(true|false)')
    b('pcscd',     r'services\.pcscd\.enable\s*=\s*(true|false)')
    b('sunshine',  r'services\.sunshine\.enable\s*=\s*(true|false)')

    # ── Audio ─────────────────────────────────────────────────────────────────
    # pipewire: Block-Form oder Einzel-Zeile
    pw_block = _block(c, 'services.pipewire')
    if pw_block:
        v = _bool(pw_block, r'enable\s*=\s*(true|false)')
        if v is not None:
            r['pipewire'] = v
    else:
        b('pipewire', r'services\.pipewire\.enable\s*=\s*(true|false)')
    b('pipewire_32bit', r'services\.pipewire\.alsa\.support32Bit\s*=\s*(true|false)')

    # ── Desktop ───────────────────────────────────────────────────────────────
    desktop_patterns = [
        ('gnome',    r'services\.xserver\.desktopManager\.gnome\.enable\s*=\s*true'),
        ('kde',      r'services\.desktopManager\.plasma6\.enable\s*=\s*true'),
        ('plasma5',  r'services\.xserver\.desktopManager\.plasma5\.enable\s*=\s*true'),
        ('xfce',     r'services\.xserver\.desktopManager\.xfce\.enable\s*=\s*true'),
        ('mate',     r'services\.xserver\.desktopManager\.mate\.enable\s*=\s*true'),
        ('lxqt',     r'services\.xserver\.desktopManager\.lxqt\.enable\s*=\s*true'),
        ('i3',       r'services\.xserver\.windowManager\.i3\.enable\s*=\s*true'),
        ('sway',     r'programs\.sway\.enable\s*=\s*true'),
        ('hyprland', r'programs\.hyprland\.enable\s*=\s*true'),
    ]
    for de_name, de_pat in desktop_patterns:
        if re.search(de_pat, c):
            r['desktop_environment'] = de_name
            break

    # autoLogin: Block-Form oder Einzel-Zeile
    al_block = _block(c, 'services.displayManager.autoLogin')
    if al_block:
        v = _str(al_block, r'user\s*=\s*"([^"]*)"')
        if v:
            r['autologin_user'] = v
    else:
        v = _str_multi(c,
            r'services\.displayManager\.autoLogin\.user\s*=\s*"([^"]*)"',
            r'services\.xserver\.displayManager\.autoLogin\.user\s*=\s*"([^"]*)"',
        )
        if v:
            r['autologin_user'] = v

    # ── System-Programme ──────────────────────────────────────────────────────
    b('steam',    r'programs\.steam\.enable\s*=\s*(true|false)')
    b('appimage', r'programs\.appimage\.enable\s*=\s*(true|false)')
    # firefox: Block-Form oder Einzel-Zeile
    ff_block = _block(c, 'programs.firefox')
    if ff_block:
        v = _bool(ff_block, r'enable\s*=\s*(true|false)')
        if v is not None:
            r['firefox'] = v
        # languagePacks = [ "de" "en-US" ]
        m2 = re.search(r'languagePacks\s*=\s*\[([^\]]*)\]', ff_block)
        if m2:
            packs = re.findall(r'"([^"]+)"', m2.group(1))
            if packs:
                r['firefox_lang_packs'] = ', '.join(packs)
        # preferences = { "key" = "val"; ... }
        m2 = re.search(r'preferences\s*=\s*\{([^}]*)\}', ff_block, re.DOTALL)
        if m2:
            lines = [l.strip() for l in m2.group(1).splitlines() if l.strip()]
            if lines:
                r['firefox_prefs'] = '\n'.join(lines)
    else:
        b('firefox',  r'programs\.firefox\.enable\s*=\s*(true|false)')

    # ── Pakete ────────────────────────────────────────────────────────────────
    m = re.search(
        r'environment\.systemPackages\s*=\s*with\s+pkgs\s*;\s*\[(.*?)\]',
        c, re.DOTALL
    )
    if m:
        raw = re.sub(r'#[^\n]*', '', m.group(1))   # Kommentare entfernen
        pkgs = re.findall(r'\b([a-zA-Z][a-zA-Z0-9_.\-]*)\b', raw)
        # Immer als erkannt markieren (auch leere Liste), damit build_rest_brix()
        # die Zeile entfernt und kein Brick aus "environment.systemPackages = []" entsteht.
        r['packages'] = [{"attr": p, "enabled": True} for p in sorted(set(pkgs))]

    # ── Schriftarten ──────────────────────────────────────────────────────────
    m = re.search(
        r'fonts\.packages\s*=\s*with\s+pkgs\s*;\s*\[(.*?)\]',
        c, re.DOTALL
    )
    if m:
        raw = re.sub(r'#[^\n]*', '', m.group(1))
        fonts = re.findall(r'\b([a-zA-Z][a-zA-Z0-9_.\-]*)\b', raw)
        if fonts:
            r['fonts'] = sorted(set(fonts))

    # ── Nix & System ──────────────────────────────────────────────────────────
    b('nix_optimize_store', r'nix\.settings\.auto-optimise-store\s*=\s*(true|false)')
    if re.search(r'experimental-features[^;]*flakes', c):
        r['flakes'] = True

    # nix.gc: Block-Form oder Einzel-Zeilen
    gc_block = _block(c, 'nix.gc')
    if gc_block:
        v = _bool(gc_block, r'automatic\s*=\s*(true|false)')
        if v is not None:
            r['nix_gc'] = v
        v = _str(gc_block, r'dates\s*=\s*"([^"]*)"')
        if v:
            r['nix_gc_frequency'] = v
        v = _str(gc_block, r'options\s*=\s*"--delete-older-than\s+([^"]+)"')
        if v:
            r['nix_gc_age'] = v.strip()
    else:
        b('nix_gc',           r'nix\.gc\.automatic\s*=\s*(true|false)')
        s('nix_gc_frequency', r'nix\.gc\.dates\s*=\s*"([^"]*)"')
        m2 = re.search(r'nix\.gc\.options\s*=\s*"--delete-older-than\s+([^"]+)"', c)
        if m2:
            r['nix_gc_age'] = m2.group(1).strip()

    # ── Hardware ──────────────────────────────────────────────────────────────
    b('enable_all_firmware', r'hardware\.enableAllFirmware\s*=\s*(true|false)')
    # opengl: NixOS ≤ 23.11 = hardware.opengl, ≥ 24.05 = hardware.graphics
    b('opengl',
      r'hardware\.opengl\.enable\s*=\s*(true|false)',
      r'hardware\.graphics\.enable\s*=\s*(true|false)')
    b('opengl_32bit',
      r'hardware\.opengl\.driSupport32Bit\s*=\s*(true|false)',
      r'hardware\.graphics\.enable32Bit\s*=\s*(true|false)')
    b('zram_swap', r'zramSwap\.enable\s*=\s*(true|false)')
    b('openrgb',   r'services\.hardware\.openrgb\.enable\s*=\s*(true|false)')
    b('ledger',    r'hardware\.ledger\.enable\s*=\s*(true|false)')
    b('ratbagd',   r'services\.ratbagd\.enable\s*=\s*(true|false)')

    if re.search(r'hardware\.cpu\.intel\.updateMicrocode\s*=\s*true', c):
        r['cpu_microcode'] = 'intel'
    elif re.search(r'hardware\.cpu\.amd\.updateMicrocode\s*=\s*true', c):
        r['cpu_microcode'] = 'amd'

    # ── Virtualisierung ───────────────────────────────────────────────────────
    b('docker',               r'virtualisation\.docker\.enable\s*=\s*(true|false)')
    # docker rootless: Block-Form oder Einzel-Zeile
    dr_block = _block(c, 'virtualisation.docker.rootless')
    if dr_block:
        v = _bool(dr_block, r'enable\s*=\s*(true|false)')
        if v is not None:
            r['docker_rootless'] = v
    else:
        b('docker_rootless', r'virtualisation\.docker\.rootless\.enable\s*=\s*(true|false)')

    b('podman',               r'virtualisation\.podman\.enable\s*=\s*(true|false)')
    b('podman_docker_compat', r'virtualisation\.podman\.dockerCompat\s*=\s*(true|false)')
    b('virtualbox_host',      r'virtualisation\.virtualbox\.host\.enable\s*=\s*(true|false)')
    b('virtualbox_guest',     r'virtualisation\.virtualbox\.guest\.enable\s*=\s*(true|false)')
    b('virtualbox_guest_drag_drop',
                              r'virtualisation\.virtualbox\.guest\.dragAndDrop\s*=\s*(true|false)')
    b('libvirtd',             r'virtualisation\.libvirtd\.enable\s*=\s*(true|false)')
    b('virt_manager',         r'programs\.virt-manager\.enable\s*=\s*(true|false)')

    # ── Dateisystem & Backup ──────────────────────────────────────────────────
    b('btrfs_scrub', r'services\.btrfs\.autoScrub\.enable\s*=\s*(true|false)')

    # snapper: flache Form (Generator-Output) ODER verschachtelter Block (User-Config)
    if re.search(r'services\.snapper\.configs\.home', c) or \
       re.search(r'SUBVOLUME\s*=\s*"/home"', c):
        r['snapper_home'] = True
    if re.search(r'services\.snapper\.configs\.root', c) or \
       re.search(r'SUBVOLUME\s*=\s*"/"', c):
        r['snapper_root'] = True
    for field, key in [
        ('snapper_timeline_hourly',  'TIMELINE_LIMIT_HOURLY'),
        ('snapper_timeline_daily',   'TIMELINE_LIMIT_DAILY'),
        ('snapper_timeline_weekly',  'TIMELINE_LIMIT_WEEKLY'),
        ('snapper_timeline_monthly', 'TIMELINE_LIMIT_MONTHLY'),
        ('snapper_timeline_yearly',  'TIMELINE_LIMIT_YEARLY'),
    ]:
        m2 = re.search(rf'{key}\s*=\s*(\d+)', c)
        if m2:
            r[field] = int(m2.group(1))

    # ── Benutzer ──────────────────────────────────────────────────────────────
    _EXCLUDED_USERS = {'root', 'nobody', 'guest', 'gast'}
    _SHELL_MAP = {'pkgs.zsh': 'zsh', 'pkgs.fish': 'fish',
                  'pkgs.bash': 'bash', 'pkgs.nushell': 'nushell'}

    def _extract_user_block(text: str, start: int) -> tuple[str, int]:
        """Gibt den Inhalt des { }-Blocks und die End-Position zurück."""
        brace = text.index('{', start)
        depth, i = 0, brace
        while i < len(text):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[brace + 1:i], i + 1
            i += 1
        return text[brace + 1:], len(text)

    primary_done = False
    extra_users: list[dict] = []

    for m_user in re.finditer(r'users\.users\.(\w+)\s*=\s*\{', c):
        uname = m_user.group(1)
        if uname in _EXCLUDED_USERS:
            continue
        blk, _ = _extract_user_block(c, m_user.start())

        if not primary_done:
            primary_done = True
            r['username'] = uname
            v = _str(blk, r'description\s*=\s*"([^"]*)"')
            if v:
                r['user_description'] = v
            v = _str(blk, r'initialPassword\s*=\s*"([^"]*)"')
            if v:
                r['user_initial_password'] = v
            m2 = re.search(r'\buid\s*=\s*(\d+)', blk)
            if m2:
                r['user_uid'] = m2.group(1)
            m2 = re.search(r'\bshell\s*=\s*(pkgs\.\w+)', blk)
            if m2:
                r['user_shell'] = _SHELL_MAP.get(m2.group(1), 'bash')
            m2 = re.search(r'extraGroups\s*=\s*\[([^\]]*)\]', blk)
            if m2:
                groups = re.findall(r'"(\w+)"', m2.group(1))
                if groups:
                    r['user_groups'] = groups
        else:
            eu: dict = {'username': uname}
            v = _str(blk, r'description\s*=\s*"([^"]*)"')
            if v:
                eu['description'] = v
            v = _str(blk, r'initialPassword\s*=\s*"([^"]*)"')
            if v:
                eu['initial_password'] = v
            m2 = re.search(r'\buid\s*=\s*(\d+)', blk)
            if m2:
                eu['uid'] = int(m2.group(1))
            m2 = re.search(r'\bshell\s*=\s*(pkgs\.\w+)', blk)
            if m2:
                eu['shell'] = _SHELL_MAP.get(m2.group(1), 'bash')
            m2 = re.search(r'extraGroups\s*=\s*\[([^\]]*)\]', blk)
            if m2:
                groups = re.findall(r'"(\w+)"', m2.group(1))
                if groups:
                    eu['groups'] = groups
            extra_users.append(eu)

    if extra_users:
        r['extra_users'] = extra_users

    if re.search(r'users\.users\.gast\s*=\s*\{', c):
        r['guest_user'] = True

    # ── Home Manager NixOS-Modul ──────────────────────────────────────────────
    b('hm_use_global_pkgs',   r'home-manager\.useGlobalPkgs\s*=\s*(true|false)')
    b('hm_use_user_packages', r'home-manager\.useUserPackages\s*=\s*(true|false)')
    # sharedModules: plasma-manager Checkbox + Rest als Extra
    m_sm = re.search(r'home-manager\.sharedModules\s*=\s*\[([^\]]*)\]', c, re.DOTALL)
    if m_sm:
        entries = re.findall(r'(\S+)', m_sm.group(1))
        if entries:
            plasma = 'plasma-manager.homeModules.plasma-manager'
            if plasma in entries:
                r['hm_plasma_manager'] = True
                extras = [e for e in entries if e != plasma]
            else:
                extras = entries
            if extras:
                r['hm_shared_modules_extra'] = '\n'.join(extras)

    return r


def build_rest_brix(nix_content: str, recognized: dict) -> str:
    """
    Gibt den Konfigurationsinhalt zurück nachdem alle erkannten Optionen entfernt wurden.
    Das Ergebnis wird in Brix-Marker eingewickelt.
    Gibt leeren String zurück wenn nichts Sinnvolles übrig bleibt.
    Tree-sitter wird bevorzugt; Regex-Fallback wenn nicht verfügbar.
    """
    ts = _ts_parse_config(nix_content)
    if ts is not None:
        result = _ts_build_rest_brix(nix_content, ts[1])
        if result is not None:
            return result

    # Brick/Brix-Marker immer zuerst entfernen – egal ob der Aufrufer das bereits
    # getan hat oder nicht.  So landen keine alten Marker im Body des neuen Bricks.
    content = strip_brick_blocks(nix_content).strip()

    # Führende Kommentarblöcke entfernen
    content = re.sub(r'^(\s*#[^\n]*\n)+', '', content).strip()
    # NiCo-Sektionsheader entfernen (# ── Boot ──────…)
    content = re.sub(r'\n[ \t]*#[ \t]*──+[^\n]*', '', content)
    # Funktions-Header  { config, pkgs, ... }:
    content = re.sub(r'^\{[^}]*\}\s*:\s*', '', content, flags=re.DOTALL).strip()
    # Äußere geschweifte Klammern des Top-Level-Attribut-Sets
    if content.startswith('{') and content.endswith('}'):
        content = content[1:-1]
    # imports-Block (hardware-configuration.nix wird separat behandelt)
    content = re.sub(r'\s*imports\s*=\s*\[[^\]]*\]\s*;', '', content)

    def rm(key, *patterns):
        """Erkannte Einzel-Zeilen-Optionen entfernen."""
        if key not in recognized:
            return
        nonlocal content
        for pat in patterns:
            content = re.sub(pat, '', content, flags=re.MULTILINE)

    def rm_block(key, *patterns):
        """Erkannte Block-Optionen entfernen (DOTALL)."""
        if key not in recognized:
            return
        nonlocal content
        for pat in patterns:
            content = re.sub(pat, '', content, flags=re.DOTALL | re.MULTILINE)

    # ── Modulargumente (komplette erste Nicht-Kommentar-Zeile)  ──────────────
    if 'nix_args' in recognized:
        content = re.sub(r'^\s*\{[^}]+\}\s*:\s*\{?\s*\n?', '', content, count=1, flags=re.MULTILINE)

    # ── System ────────────────────────────────────────────────────────────────
    rm('hostname',      r'\s*networking\.hostName\s*=\s*"[^"]*"\s*;')
    rm('state_version', r'\s*system\.stateVersion\s*=\s*"[^"]*"\s*;',
                        r'[ \t]*#[ \t]*system\.stateVersion\s*=.*\n?')
    rm('allowUnfree',   r'\s*nixpkgs\.config\.allowUnfree\s*=\s*(?:true|false)\s*;')

    # ── Lokalisierung ─────────────────────────────────────────────────────────
    rm('timezone', r'\s*time\.timeZone\s*=\s*"[^"]*"\s*;')
    rm('locale',   r'\s*i18n\.defaultLocale\s*=\s*"[^"]*"\s*;')
    rm_block('extra_locale', r'\s*i18n\.extraLocaleSettings\s*=\s*\{[^}]*\}\s*;')
    rm_block('keyboard_layout',
             r'\s*services\.xserver\.xkb\s*=\s*\{[^}]*\}\s*;')
    rm('keyboard_layout',  r'\s*services\.xserver\.xkb\.layout\s*=\s*"[^"]*"\s*;')
    rm('keyboard_variant', r'\s*services\.xserver\.xkb\.variant\s*=\s*"[^"]*"\s*;')
    rm('keyboard_console', r'\s*console\.keyMap\s*=\s*"[^"]*"\s*;')

    # ── Boot ──────────────────────────────────────────────────────────────────
    rm('boot_loader',
       r'\s*boot\.loader\.systemd-boot\.enable\s*=\s*(?:true|false)\s*;',
       r'\s*boot\.loader\.grub\.enable\s*=\s*(?:true|false)\s*;')
    rm('boot_efi_can_touch',   r'\s*boot\.loader\.efi\.canTouchEfiVariables\s*=\s*(?:true|false)\s*;')
    rm('boot_efi_mount_point', r'\s*boot\.loader\.efi\.efiSysMountPoint\s*=\s*"[^"]*"\s*;')
    rm('boot_config_limit',    r'\s*boot\.loader\.systemd-boot\.configurationLimit\s*=\s*\d+\s*;')

    # ── Netzwerk ──────────────────────────────────────────────────────────────
    rm('networkmanager', r'\s*networking\.networkmanager\.enable\s*=\s*(?:true|false)\s*;')
    rm('ssh',            r'\s*services\.openssh\.enable\s*=\s*(?:true|false)\s*;')
    rm('firewall_disable',   r'\s*networking\.firewall\.enable\s*=\s*(?:true|false)\s*;')
    rm('firewall_tcp_ports', r'\s*networking\.firewall\.allowedTCPPorts\s*=\s*\[[^\]]*\]\s*;')
    rm('firewall_udp_ports', r'\s*networking\.firewall\.allowedUDPPorts\s*=\s*\[[^\]]*\]\s*;')

    # ── Services ──────────────────────────────────────────────────────────────
    rm('printing', r'\s*services\.printing\.enable\s*=\s*(?:true|false)\s*;')
    rm_block('avahi', r'\s*services\.avahi\s*=\s*\{[^}]*\}\s*;')
    rm('avahi',        r'\s*services\.avahi\.enable\s*=\s*(?:true|false)\s*;')
    rm('bluetooth',    r'\s*hardware\.bluetooth\.enable\s*=\s*(?:true|false)\s*;')
    rm('blueman',      r'\s*services\.blueman\.enable\s*=\s*(?:true|false)\s*;')
    rm('libinput',     r'\s*services\.libinput\.enable\s*=\s*(?:true|false)\s*;')
    rm('fprintd',      r'\s*services\.fprintd\.enable\s*=\s*(?:true|false)\s*;')
    rm('pcscd',        r'\s*services\.pcscd\.enable\s*=\s*(?:true|false)\s*;')
    rm('sunshine',     r'\s*services\.sunshine\.enable\s*=\s*(?:true|false)\s*;')

    # ── Audio ─────────────────────────────────────────────────────────────────
    rm_block('pipewire', r'\s*services\.pipewire\s*=\s*\{[^}]*\}\s*;')
    rm('pipewire',
       r'\s*services\.pipewire\.enable\s*=\s*(?:true|false)\s*;',
       r'\s*services\.pulseaudio\.enable\s*=\s*false\s*;',
       r'\s*security\.rtkit\.enable\s*=\s*true\s*;')
    rm('pipewire_32bit', r'\s*services\.pipewire\.alsa\.support32Bit\s*=\s*(?:true|false)\s*;')

    # ── Desktop ───────────────────────────────────────────────────────────────
    if 'desktop_environment' in recognized:
        content = re.sub(r'\s*services\.xserver\.desktopManager\.\w+\.enable\s*=\s*(?:true|false)\s*;', '', content)
        content = re.sub(r'\s*services\.desktopManager\.\w+\.enable\s*=\s*(?:true|false)\s*;', '', content)
        content = re.sub(r'\s*services\.xserver\.windowManager\.\w+\.enable\s*=\s*(?:true|false)\s*;', '', content)
        content = re.sub(r'\s*services\.xserver\.displayManager\.\w+\.enable\s*=\s*(?:true|false)\s*;', '', content)
        content = re.sub(r'\s*services\.displayManager\.\w+\.enable\s*=\s*(?:true|false)\s*;', '', content)
        content = re.sub(r'\s*services\.xserver\.enable\s*=\s*(?:true|false)\s*;', '', content)
        content = re.sub(r'\s*programs\.sway\.enable\s*=\s*(?:true|false)\s*;', '', content)
        content = re.sub(r'\s*programs\.hyprland\.enable\s*=\s*(?:true|false)\s*;', '', content)

    rm_block('autologin_user', r'\s*services\.displayManager\.autoLogin\s*=\s*\{[^}]*\}\s*;')
    if 'autologin_user' in recognized:
        content = re.sub(r'\s*services\.(?:xserver\.)?displayManager\.autoLogin\.\w+\s*=\s*[^;]+\s*;', '', content)

    # ── System-Programme ──────────────────────────────────────────────────────
    rm('steam',    r'\s*programs\.steam\.enable\s*=\s*(?:true|false)\s*;')
    rm_block('appimage', r'\s*programs\.appimage\s*=\s*\{[^}]*\}\s*;')
    rm('appimage', r'\s*programs\.appimage\.enable\s*=\s*(?:true|false)\s*;',
                   r'\s*programs\.appimage\.binfmt\s*=\s*(?:true|false)\s*;')
    if 'firefox' in recognized:
        content = _remove_deep_block(content, 'programs.firefox')
        rm('firefox', r'\s*programs\.firefox\.enable\s*=\s*(?:true|false)\s*;')

    # ── Pakete ────────────────────────────────────────────────────────────────
    rm_block('packages', r'\s*environment\.systemPackages\s*=\s*with\s+pkgs\s*;\s*\[.*?\]\s*;')

    # ── Schriftarten ──────────────────────────────────────────────────────────
    rm_block('fonts', r'\s*fonts\.packages\s*=\s*with\s+pkgs\s*;\s*\[.*?\]\s*;')

    # ── Nix & System ──────────────────────────────────────────────────────────
    rm('nix_optimize_store', r'\s*nix\.settings\.auto-optimise-store\s*=\s*(?:true|false)\s*;')
    if recognized.get('flakes'):
        content = re.sub(r'\s*nix\.settings\.experimental-features\s*=\s*\[[^\]]*\]\s*;', '', content)
        content = re.sub(r'\s*nix\.settings\.experimental-features\s*=\s*"[^"]*"\s*;', '', content)
    if recognized.get('user_shell') in ('zsh', 'fish'):
        shell = recognized['user_shell']
        content = re.sub(rf'\s*programs\.{shell}\.enable\s*=\s*(?:true|false)\s*;', '', content)
    rm_block('nix_gc', r'\s*nix\.gc\s*=\s*\{[^}]*\}\s*;')
    rm('nix_gc',          r'\s*nix\.gc\.automatic\s*=\s*(?:true|false)\s*;')
    rm('nix_gc_frequency',r'\s*nix\.gc\.dates\s*=\s*"[^"]*"\s*;')
    rm('nix_gc_age',      r'\s*nix\.gc\.options\s*=\s*"[^"]*"\s*;')

    # ── Hardware ──────────────────────────────────────────────────────────────
    rm('enable_all_firmware', r'\s*hardware\.enableAllFirmware\s*=\s*(?:true|false)\s*;')
    rm('opengl',
       r'\s*hardware\.opengl\.enable\s*=\s*(?:true|false)\s*;',
       r'\s*hardware\.graphics\.enable\s*=\s*(?:true|false)\s*;')
    rm('opengl_32bit',
       r'\s*hardware\.opengl\.driSupport32Bit\s*=\s*(?:true|false)\s*;',
       r'\s*hardware\.graphics\.enable32Bit\s*=\s*(?:true|false)\s*;')
    rm('zram_swap', r'\s*zramSwap\.enable\s*=\s*(?:true|false)\s*;')
    rm('openrgb',   r'\s*services\.hardware\.openrgb\.enable\s*=\s*(?:true|false)\s*;')
    rm('ledger',    r'\s*hardware\.ledger\.enable\s*=\s*(?:true|false)\s*;')
    rm('ratbagd',   r'\s*services\.ratbagd\.enable\s*=\s*(?:true|false)\s*;')
    if 'cpu_microcode' in recognized:
        content = re.sub(r'\s*hardware\.cpu\.\w+\.updateMicrocode\s*=\s*(?:true|false)\s*;', '', content)

    # ── Virtualisierung ───────────────────────────────────────────────────────
    rm('docker',               r'\s*virtualisation\.docker\.enable\s*=\s*(?:true|false)\s*;')
    rm_block('docker_rootless',r'\s*virtualisation\.docker\.rootless\s*=\s*\{[^}]*\}\s*;')
    rm('docker_rootless',      r'\s*virtualisation\.docker\.rootless\.enable\s*=\s*(?:true|false)\s*;',
                               r'\s*virtualisation\.docker\.rootless\.setSocketVariable\s*=\s*(?:true|false)\s*;')
    rm('podman',               r'\s*virtualisation\.podman\.enable\s*=\s*(?:true|false)\s*;')
    rm('podman_docker_compat', r'\s*virtualisation\.podman\.dockerCompat\s*=\s*(?:true|false)\s*;')
    rm('virtualbox_host',      r'\s*virtualisation\.virtualbox\.host\.enable\s*=\s*(?:true|false)\s*;')
    rm('virtualbox_guest',     r'\s*virtualisation\.virtualbox\.guest\.enable\s*=\s*(?:true|false)\s*;')
    rm('virtualbox_guest_drag_drop',
                               r'\s*virtualisation\.virtualbox\.guest\.dragAndDrop\s*=\s*(?:true|false)\s*;')
    rm('libvirtd',             r'\s*virtualisation\.libvirtd\.enable\s*=\s*(?:true|false)\s*;')
    rm('virt_manager',         r'\s*programs\.virt-manager\.enable\s*=\s*(?:true|false)\s*;')

    # ── Dateisystem & Backup ──────────────────────────────────────────────────
    rm('btrfs_scrub', r'\s*services\.btrfs\.autoScrub\.enable\s*=\s*(?:true|false)\s*;')
    # snapper: flache Form (Generator) oder tief verschachtelter Block (User-Config)
    if 'snapper_home' in recognized or 'snapper_root' in recognized:
        content = _remove_deep_block(content, 'services.snapper')
        rm('snapper_home', r'\s*services\.snapper\.configs\.home\s*=\s*\{[^}]*\}\s*;')
        rm('snapper_root', r'\s*services\.snapper\.configs\.root\s*=\s*\{[^}]*\}\s*;')

    # ── Benutzer ──────────────────────────────────────────────────────────────
    all_recognized_users = []
    if 'username' in recognized:
        all_recognized_users.append(recognized['username'])
    for eu in recognized.get('extra_users', []):
        eu_name = eu.get('username', '')
        if eu_name:
            all_recognized_users.append(eu_name)
    for uname in all_recognized_users:
        content = _remove_deep_block(content, f'users.users.{uname}')
    if 'guest_user' in recognized:
        content = _remove_deep_block(content, 'users.users.gast')

    # ── Home Manager NixOS-Modul ──────────────────────────────────────────────
    rm('hm_use_global_pkgs',   r'\s*home-manager\.useGlobalPkgs\s*=\s*(?:true|false)\s*;')
    rm('hm_use_user_packages', r'\s*home-manager\.useUserPackages\s*=\s*(?:true|false)\s*;')
    if 'hm_plasma_manager' in recognized or 'hm_shared_modules_extra' in recognized:
        content = re.sub(r'\s*home-manager\.sharedModules\s*=\s*\[[^\]]*\]\s*;', '', content, flags=re.DOTALL)

    content = _strip_nix_comments(content)
    content = re.sub(r'\n{3,}', '\n\n', content).strip()

    if not content:
        return ''

    used: dict[str, int] = {}
    parts = []
    for stmt in _split_top_level_stmts(content):
        base = _brix_name_from_stmt(stmt)
        if base in used:
            used[base] += 1
            name = f'{base}-{used[base]}'
        else:
            used[base] = 1
            name = base
        parts.append(f'# <brix: {name}>\n{stmt}\n# </brix: {name}>\n')

    return '\n'.join(parts)


def parse_home_config(nix_content: str) -> dict:
    """Parse a NiCo-generated home.nix and return a home_manager dict.
    Only handles the fields that hm_generator.generate_home_nix writes."""
    from .hm_generator import HM_DEFAULTS

    r: dict = dict(HM_DEFAULTS)
    r["enabled"] = True

    def _s(pat: str) -> str | None:
        m = re.search(pat, nix_content, re.DOTALL | re.MULTILINE)
        return m.group(1).strip() if m else None

    v = _s(r'home\.username\s*=\s*"([^"]*)"')
    if v: r["username"] = v

    v = _s(r'home\.homeDirectory\s*=\s*"([^"]*)"')
    if v: r["home_dir"] = v

    v = _s(r'home\.stateVersion\s*=\s*"([^"]*)"')
    if v: r["state_version"] = v

    # Git
    if re.search(r'programs\.git\s*=\s*\{', nix_content):
        r["git_enable"] = True
        v = _s(r'userName\s*=\s*"([^"]*)"');        r["git_name"]           = v or r["git_name"]
        v = _s(r'userEmail\s*=\s*"([^"]*)"');       r["git_email"]          = v or r["git_email"]
        v = _s(r'defaultBranch\s*=\s*"([^"]*)"');   r["git_default_branch"] = v or r["git_default_branch"]

    # Shell (first match wins)
    for shell in ("bash", "zsh", "fish"):
        if re.search(rf'programs\.{shell}\s*=\s*\{{', nix_content):
            r["shell"] = shell
            key = {"bash": "initExtra", "zsh": "initExtra", "fish": "shellInit"}[shell]
            m2 = re.search(rf"{key}\s*=\s*''(.*?)''", nix_content, re.DOTALL)
            if m2:
                raw = m2.group(1).lstrip("\n").rstrip()
                if raw:
                    lines = raw.splitlines()
                    indent = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
                    r["shell_init_extra"] = "\n".join(l[indent:] for l in lines)
            break

    if re.search(r'programs\.firefox\.enable\s*=\s*true', nix_content):
        r["firefox"] = True

    # XDG user dirs
    if re.search(r'xdg\.userDirs\s*=\s*\{', nix_content):
        r["xdg_user_dirs"] = True
        for key, nix_key in [
            ("xdg_download",    "download"),
            ("xdg_documents",   "documents"),
            ("xdg_pictures",    "pictures"),
            ("xdg_music",       "music"),
            ("xdg_videos",      "videos"),
            ("xdg_desktop",     "desktop"),
            ("xdg_templates",   "templates"),
            ("xdg_publicshare", "publicShare"),
        ]:
            m2 = re.search(rf'{nix_key}\s*=\s*"\${{[^}}]*}}/([^"]+)"', nix_content)
            if m2: r[key] = m2.group(1)

    # Packages (pkgs.attrname)
    m_pkg = re.search(r'home\.packages\s*=\s*\[(.*?)\]', nix_content, re.DOTALL)
    if m_pkg:
        r["packages"] = re.findall(r'pkgs\.([\w_\-]+)', m_pkg.group(1))

    return r


def build_home_rest_brix(nix_content: str, recognized: dict) -> str:
    """Return unsupported Home Manager statements as legacy brix blocks."""
    content = strip_brick_blocks(nix_content)

    # Remove NiCo header comments, version line, section markers and wrapper.
    content = re.sub(r'^\s*# nico-version: .*$\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*# Generated by NiCo.*$\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*# Do not edit manually\..*$\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*#\s*──.*$\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*\{[^}]*\}\s*:\s*', '', content, count=1, flags=re.MULTILINE).lstrip()
    if content.startswith('{'):
        content = content[1:]
    content = content.rstrip()
    if content.endswith('}'):
        content = content[:-1]

    def rm(pat: str):
        nonlocal content
        content = re.sub(pat, '', content, flags=re.DOTALL | re.MULTILINE)

    rm(r'\s*home\.username\s*=\s*"[^"]*"\s*;')
    rm(r'\s*home\.homeDirectory\s*=\s*"[^"]*"\s*;')
    rm(r'\s*home\.stateVersion\s*=\s*"[^"]*"\s*;')

    if recognized.get("packages"):
        rm(r'\s*home\.packages\s*=\s*\[(?:[^\]]|\](?=\s*;))*\]\s*;')
    if recognized.get("git_enable"):
        rm(r'\s*programs\.git\s*=\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*;')
    shell = recognized.get("shell")
    if shell in ("bash", "zsh", "fish"):
        rm(rf'\s*programs\.{re.escape(shell)}\s*=\s*\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}}\s*;')
    if recognized.get("firefox"):
        rm(r'\s*programs\.firefox\.enable\s*=\s*true\s*;')
    if recognized.get("xdg_user_dirs"):
        rm(r'\s*xdg\.userDirs\s*=\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*;')

    content = _strip_nix_comments(content)
    content = re.sub(r'\n{3,}', '\n\n', content).strip()
    if not content:
        return ''

    used: dict[str, int] = {}
    parts = []
    for stmt in _split_top_level_stmts(content):
        base = _brix_name_from_stmt(stmt)
        if base in used:
            used[base] += 1
            name = f'{base}-{used[base]}'
        else:
            used[base] = 1
            name = base
        parts.append(f'# <brix: {name}>\n{stmt}\n# </brix: {name}>\n')

    return '\n'.join(parts)


def dir_has_non_zip_files(nixos_dir: str | Path) -> bool:
    """True wenn nixos_dir mindestens eine relevante Nicht-ZIP-Datei enthält.
    Ausgenommen: nico.json (von NiCo angelegt), ZIP-Dateien, versteckte Dirs.
    """
    root = Path(nixos_dir)
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if f.suffix.lower() == ".zip":
            continue
        if any(part.startswith('.') for part in rel.parts):
            continue
        if str(rel) == "nico.json":
            continue
        return True
    return False


def backup_to_zip(nixos_dir: str | Path) -> str:
    """Packt alle relevanten Nicht-ZIP-Dateien in nixos_dir in eine ZIP.
    ZIP-Dateien und versteckte Verzeichnisse werden ausgelassen.
    Gibt den Dateinamen (relativ) zurück.
    """
    import zipfile as _zipfile
    from datetime import datetime as _dt
    root = Path(nixos_dir)
    ts = _dt.now().strftime("%Y-%m-%d-%H%M%S")
    zip_name = f"nixos-config-{ts}.zip"
    zip_path = root / zip_name
    with _zipfile.ZipFile(zip_path, "w", _zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(root)
            if f.suffix.lower() == ".zip":
                continue
            if any(part.startswith('.') for part in rel.parts):
                continue
            if str(rel) == "nico.json":
                continue  # add separately below
            zf.write(f, rel)
        # nico.json als Kopie beifügen (Referenz, wird beim Import nicht angewendet)
        nico_json = root / "nico.json"
        if nico_json.exists():
            zf.write(nico_json, "nico.json")
    return zip_name


def parse_flake_nix(content: str) -> dict:
    """
    Liest eine flake.nix und extrahiert bekannte Felder als flake_*-Dict.
    Analog zu parse_config() für configuration.nix.
    """
    r: dict = {}

    # description
    m = re.search(r'description\s*=\s*"([^"]*)"', content)
    if m:
        r['flake_description'] = m.group(1)

    # nixpkgs channel (z.B. github:NixOS/nixpkgs/nixos-unstable)
    m = re.search(r'nixpkgs\.url\s*=\s*"github:NixOS/nixpkgs/([^"]+)"', content)
    if m:
        r['flake_nixpkgs_channel'] = m.group(1)

    # system architecture
    m = re.search(r'\bsystem\s*=\s*"([^"]+)"', content)
    if m:
        r['flake_arch'] = m.group(1)

    # home-manager input
    hm_url = bool(re.search(r'home-manager\s*[=.]', content))
    r['flake_hm_input'] = hm_url
    if hm_url:
        r['flake_hm_follows'] = bool(re.search(r'inputs\.nixpkgs\.follows', content))
        r['flake_hm_module']  = bool(re.search(r'home-manager\.nixosModules', content))

    # nixos-hardware input
    r['flake_nixos_hardware'] = bool(re.search(r'nixos-hardware\.url\s*=', content))

    # plasma-manager input
    r['flake_plasma_manager'] = bool(re.search(r'plasma-manager\s*[=.]', content))

    # Per-host data: extract flake_hosts list from nixosConfigurations
    flake_hosts: list[dict] = []
    outputs_m = re.search(r'\boutputs\s*=\s*\{[^}]*\}\s*:\s*\{', content)
    if outputs_m:
        colon_pos = content.index(':', outputs_m.start())
        body_start = content.index('{', colon_pos)
        depth, i = 0, body_start
        while i < len(content):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
            i += 1
        else:
            body_end = len(content)
        outputs_body = content[body_start + 1:body_end]
        nixos_cfg = _flake_extract_block(outputs_body, "nixosConfigurations")
        if nixos_cfg:
            cfg_open  = nixos_cfg.index('{') + 1
            cfg_close = nixos_cfg.rindex('}')
            for host_entry in _flake_split_attrs(nixos_cfg[cfg_open:cfg_close]):
                host_name = _flake_attr_name(host_entry)
                if not host_name:
                    continue
                sys_m2 = re.search(r'nixosSystem\s*\{', host_entry)
                if not sys_m2:
                    continue
                sys_start = host_entry.index('{', sys_m2.start())
                depth2, j = 0, sys_start
                while j < len(host_entry):
                    if host_entry[j] == '{':
                        depth2 += 1
                    elif host_entry[j] == '}':
                        depth2 -= 1
                        if depth2 == 0:
                            sys_block = host_entry[sys_start:j + 1]
                            if not _is_exotic_nixos_system(sys_block):
                                h: dict = {'name': host_name}
                                arch_m = re.search(r'\bsystem\s*=\s*"([^"]+)"', sys_block)
                                h['arch'] = arch_m.group(1) if arch_m else 'x86_64-linux'
                                sa_m = re.search(r'specialArgs\s*=\s*\{([^}]*)\}', sys_block)
                                h['specialArgs'] = sa_m.group(1).strip() if sa_m else ''
                                mod_m = re.search(r'\bmodules\s*=\s*\[', sys_block)
                                if mod_m:
                                    b_start = sys_block.index('[', mod_m.start())
                                    depth3, k = 0, b_start
                                    while k < len(sys_block):
                                        if sys_block[k] == '[':
                                            depth3 += 1
                                        elif sys_block[k] == ']':
                                            depth3 -= 1
                                            if depth3 == 0:
                                                raw = sys_block[b_start + 1:k]
                                                clean = re.sub(r'#[^\n]*', '', raw)
                                                h['modules'] = '\n'.join(
                                                    l.strip() for l in clean.splitlines() if l.strip()
                                                )
                                                break
                                        k += 1
                                    else:
                                        h['modules'] = ''
                                else:
                                    h['modules'] = ''
                                flake_hosts.append(h)
                            break
                    j += 1
    if flake_hosts:
        r['flake_hosts'] = flake_hosts
        r['flake_arch']  = flake_hosts[0]['arch']

    return r


# ── flake.nix Brix-Extraktion ─────────────────────────────────────────────────

def _flake_extract_block(content: str, keyword: str) -> str | None:
    """Find 'keyword = {' in content and return the full assignment including
    the closing '};', or None if not found.  Handles nested braces."""
    pat = re.compile(r'\b' + re.escape(keyword) + r'\s*=\s*\{')
    m = pat.search(content)
    if not m:
        return None
    start = m.start()
    depth = 0
    i = m.start(0)
    # walk from the opening '{' of the value
    brace_start = content.index('{', m.start())
    i = brace_start
    while i < len(content):
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth == 0:
                # include trailing ';' if present
                end = i + 1
                while end < len(content) and content[end] in ' \t':
                    end += 1
                if end < len(content) and content[end] == ';':
                    end += 1
                return content[start:end]
        i += 1
    return None


def _flake_split_attrs(block_content: str) -> list[str]:
    """Split the BODY of a Nix attribute set (without outer braces) into
    individual top-level attribute assignments.  Returns list of raw strings."""
    entries: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in block_content:
        if ch in '{[(':
            depth += 1
        elif ch in '}])':
            depth -= 1
        current.append(ch)
        if depth == 0 and ch == ';':
            text = ''.join(current).strip()
            if text:
                entries.append(text)
            current = []
    leftover = ''.join(current).strip()
    if leftover:
        entries.append(leftover)
    return entries


def _flake_attr_name(assignment: str) -> str:
    """Extract the attribute name (first identifier) from an assignment string.
    Skips leading comment lines (e.g. section markers injected by _section())."""
    for line in assignment.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            m = re.match(r'([\w-]+)', stripped)
            return m.group(1) if m else ''
    return ''


def _is_exotic_nixos_system(host_block: str) -> bool:
    """Return True if a nixosSystem { ... } block has parameters NiCo can't
    represent in the panel (anything beyond system, modules, specialArgs).
    specialArgs is always panel-safe (stored as textarea regardless of content)."""
    return bool(re.search(r'\b(pkgs|lib|config|options)\s*=', host_block))


# Known input names that NiCo manages via panel fields
_KNOWN_INPUTS = frozenset({"nixpkgs", "home-manager", "nixos-hardware", "plasma-manager"})

# Known output keys that NiCo manages (nixosConfigurations is generated from hosts)
_KNOWN_OUTPUTS = frozenset({"nixosConfigurations"})


def build_flake_brix(content: str) -> dict:
    """
    Parse a flake.nix and return a Brix-blocks dict for everything that cannot
    be represented in the NiCo flake panel.

    Logic:
    - Unknown inputs      → Brix in "Inputs-Extra"
    - Exotic hosts        → Brix in "Outputs-Hosts" (per-host decision)
    - Non-exotic hosts    → panel handles (no Brix created)
    - Other output keys   → Brix in "Outputs-Extra"
    """
    from .brix import format_brick
    brix: dict = {}
    order_inputs       = 1
    order_outputs_host = 1
    order_outputs_extra = 1

    # ── inputs ────────────────────────────────────────────────────────────────
    inputs_block = _flake_extract_block(content, "inputs")
    if inputs_block:
        brace_open  = inputs_block.index('{') + 1
        brace_close = inputs_block.rindex('}')
        for entry in _flake_split_attrs(inputs_block[brace_open:brace_close]):
            name = _flake_attr_name(entry)
            if name and name not in _KNOWN_INPUTS:
                brix[name] = {
                    "section": "Inputs-Extra",
                    "order":   order_inputs,
                    "text":    format_brick("Inputs-Extra", order_inputs, name, entry),
                }
                order_inputs += 1

    # ── outputs ───────────────────────────────────────────────────────────────
    outputs_match = re.search(r'\boutputs\s*=\s*\{[^}]*\}\s*:\s*\{', content)
    if outputs_match:
        colon_pos  = content.index(':', outputs_match.start())
        body_start = content.index('{', colon_pos)
        depth, i   = 0, body_start
        while i < len(content):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
            i += 1
        else:
            body_end = len(content)
        outputs_body = content[body_start + 1:body_end]

        # Per-host decision: exotic → Brix, non-exotic → panel
        nixos_cfg = _flake_extract_block(outputs_body, "nixosConfigurations")
        if nixos_cfg:
            cfg_open  = nixos_cfg.index('{') + 1
            cfg_close = nixos_cfg.rindex('}')
            for host_entry in _flake_split_attrs(nixos_cfg[cfg_open:cfg_close]):
                host_name = _flake_attr_name(host_entry)
                if not host_name:
                    continue
                sys_m = re.search(r'nixosSystem\s*\{', host_entry)
                if not sys_m:
                    # Unknown host structure → Brix
                    brix[host_name] = {
                        "section": "Outputs-Hosts",
                        "order":   order_outputs_host,
                        "text":    format_brick("Outputs-Hosts", order_outputs_host, host_name, host_entry),
                    }
                    order_outputs_host += 1
                    continue
                sys_start = host_entry.index('{', sys_m.start())
                depth2, j = 0, sys_start
                while j < len(host_entry):
                    if host_entry[j] == '{':
                        depth2 += 1
                    elif host_entry[j] == '}':
                        depth2 -= 1
                        if depth2 == 0:
                            sys_block = host_entry[sys_start:j + 1]
                            if _is_exotic_nixos_system(sys_block):
                                brix[host_name] = {
                                    "section": "Outputs-Hosts",
                                    "order":   order_outputs_host,
                                    "text":    format_brick("Outputs-Hosts", order_outputs_host, host_name, host_entry),
                                }
                                order_outputs_host += 1
                            break
                    j += 1

        # Other output keys (formatter etc.) → "Outputs-Extra"
        for entry in _flake_split_attrs(outputs_body):
            name = _flake_attr_name(entry)
            if name and name not in _KNOWN_OUTPUTS:
                brix[name] = {
                    "section": "Outputs-Extra",
                    "order":   order_outputs_extra,
                    "text":    format_brick("Outputs-Extra", order_outputs_extra, name, entry),
                }
                order_outputs_extra += 1

    return brix


def copy_nix_tree(source_dir: str | Path, nixos_dir: str | Path) -> list[str]:
    """
    1:1-Kopie aller *.nix- und *.lock-Dateien von source_dir nach nixos_dir.
    Bestehende *.nix- und *.lock-Dateien in nixos_dir werden zuerst gelöscht.

    Sonderfall: src == dst oder src liegt innerhalb von dst → kein Löschen,
    Dateien sind schon am richtigen Ort.
    """
    src = Path(source_dir).resolve()
    dst = Path(nixos_dir).resolve()

    # Selbstreferenz-Schutz: würde zuerst alle Dateien löschen, dann src leer vorfinden
    src_inside_dst = False
    try:
        src.relative_to(dst)
        src_inside_dst = True
    except ValueError:
        pass

    if src == dst or src_inside_dst:
        # Dateien sind bereits am richtigen Ort – nur Liste zurückgeben, nichts löschen/kopieren
        copied = []
        for f in sorted(src.rglob("*")):
            rel = f.relative_to(src)
            if f.is_file() and f.suffix in (".nix", ".lock"):
                if not any(p.startswith('.') for p in rel.parts):
                    copied.append(str(rel))
        return copied

    for existing in dst.rglob("*"):
        if existing.is_file() and existing.suffix in (".nix", ".lock"):
            existing.unlink()

    copied = []
    for f in sorted(src.rglob("*")):
        if f.is_file() and f.suffix in (".nix", ".lock"):
            rel = f.relative_to(src)
            dest_file = dst / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest_file)
            copied.append(str(rel))
    return copied


def copy_hardware_config(nixos_dir: str, src_path: str | None = None) -> bool:
    src = Path(src_path) if src_path else (ETC_NIXOS / "hardware-configuration.nix")
    dst = Path(nixos_dir) / "hardware-configuration.nix"
    shutil.copy2(src, dst)
    return True
