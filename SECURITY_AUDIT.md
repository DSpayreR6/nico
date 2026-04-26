# Security Audit – NiCo

**Date:** 2026-04-26  
**Auditor:** Claude Code (claude-sonnet-4-6)  
**Scope:** `nico/` and `testing/` directories (7818 LOC)

---

## Tools & Versions

| Tool | Version | Source |
|------|---------|--------|
| bandit | 1.9.4 | nixpkgs |
| pip-audit | 2.10.0 | nixpkgs |
| safety | — | not available in nixpkgs; omitted |
| Python | 3.13.12 | nixpkgs |

---

## 1. Bandit – Static Code Analysis

**Command:** `bandit -r nico/ testing/`  
**Total LOC:** 7818  
**Findings:** SEVERITY.HIGH: 0 · SEVERITY.MEDIUM: 3 · SEVERITY.LOW: 50

### MEDIUM Findings

| ID | File | Line | Severity | Description | Assessment |
|----|------|------|----------|-------------|------------|
| B310 | `nico/main.py` | 33 | Medium | `urllib.request.urlopen` – scheme not validated | **False positive.** URL is hardcoded to `http://127.0.0.1:{port}/api/status`. No user input flows in. |
| B310 | `nico/main.py` | 57 | Medium | `urllib.request.urlopen` – scheme not validated | **False positive.** Hardcoded `http://127.0.0.1:{port}/`. No user input. |
| B310 | `nico/main.py` | 77 | Medium | `urllib.request.urlopen` – scheme not validated | **False positive.** Hardcoded shutdown request to `http://127.0.0.1:{port}/api/shutdown`. No user input. |

### LOW Findings (summary)

| Test | Count | Description | Assessment |
|------|-------|-------------|------------|
| B404 | 9 | `import subprocess` flagged as risky | **Informational.** All uses reviewed below. |
| B603 | 15 | `subprocess` call without `shell=True` | **Not an issue.** Using list args instead of `shell=True` is the correct safe pattern. |
| B607 | 9 | Partial executable path (`nix`, `git`, `nixos-version`, `whoami`, `blkid`) | **Acceptable.** NixOS tools are system-provided; full paths would be nix-store hashes that change per generation. |
| B110 | 12 | `try: … except: pass` | **Acceptable.** Used for optional operations and graceful fallbacks. |
| B112 | 1 | `try: … except: continue` | **Acceptable.** Used to skip invalid optional validator entries. |
| B105 | 2 | Empty-string "hardcoded password" detections | **False positive.** Placeholders/defaults, not real credentials. |
| B101 | 2 | `assert` in vendored template test files under `testing/` | **Informational.** Test fixture code, not production runtime. |

---

## 2. pip-audit – Dependency Vulnerability Scan

**Command:** `pip-audit --skip-editable -r <(echo $'flask\ntree-sitter')`  
**Result:** ✅ No known vulnerabilities found

| Package | Version | CVEs |
|---------|---------|------|
| flask | 3.1.3 | none |
| tree-sitter | 0.25.2 | none |
| blinker | 1.9.0 | none |
| click | 8.3.3 | none |
| itsdangerous | 2.2.0 | none |
| jinja2 | 3.1.6 | none |
| markupsafe | 3.0.3 | none |
| werkzeug | 3.1.8 | none |

---

## 3. safety check

Requires account registration since v3.x. Omitted. Dependency CVE coverage provided by pip-audit above.

---

## 4. Manual Review – User Input → File Writes / Subprocess

### 4.1 Path Traversal in `_modify_brick_in_file` ✅ FIXED

**Current file:** `nico/server.py:327–341` (`_modify_brick_in_file` implementation)

```python
root  = Path(nixos_dir).resolve()
fpath = (root / fname).resolve()
try:
    fpath.relative_to(root)
except ValueError:
    return False, "ERR_SYSTEM_PATH"
```

**Result:** Re-checked on 2026-04-26. The earlier traversal issue is closed; brick file operations now reject paths outside the config root before any read or write.

---

### 4.2 Subprocess Calls – Command Injection Assessment ✅ OK

All subprocess calls use explicit argument lists (never `shell=True`). No user input is interpolated into command strings.

| File | Line | Command | User input in args? |
|------|------|---------|---------------------|
| `server.py` | 1747–1751 | `sudo nixos-rebuild` | `mode` (allowlist-checked), `hostname` (regex `[\w.-]+`), `conf_path` (from app settings) |
| `server.py` | 1894–1901 | `nix flake update` | none |
| `server.py` | 2066 | `nix flake update` | none |
| `server.py` | 2103 | `nix-build` / `nix build` | `flake_ref` (derived from resolved config path) |
| `server.py` | 2173, 2207 | `nix build` | same |
| `server.py` | 3070, 3108 | `sudo nixos-rebuild` | same as line 1747 |
| `validator.py` | 184 | `whoami` | none |
| `validator.py` | 294 | `nix-instantiate` / `nix build` | derived from resolved config path |
| `git_manager.py` | 20 | `git …` | none; all args are internal |
| `config_manager.py` | 183 | `nixos-version` | none |

**Hostname validation (server.py:1725):**
```python
if hostname_param and re.fullmatch(r'[\w.-]+', hostname_param):
    hostname = hostname_param
```
Correct allowlist pattern — blocks shell metacharacters.

---

### 4.3 File Writes – Path Traversal Assessment ✅ OK

All file-write endpoints were reviewed:

| File | Lines | Write target | Traversal check |
|------|-------|-------------|-----------------|
| `server.py` | 2401–2405 | `(root / rel).resolve()` | `relative_to(root.resolve())` ✅ |
| `server.py` | 2453–2458 | `(root / rel).resolve()` | `relative_to(root.resolve())` + `.nix`/`.lock` extension ✅ |
| `server.py` | 1076–1078 | `(nixos_dir / rel_path).resolve()` | `startswith(nixos_dir.resolve())` ✅ |
| `server.py` | 1097–1098 | same | same ✅ |
| `server.py` | 809–811 | `(nixos_path / rel_path).resolve()` | `startswith(nixos_path)` ✅ |
| `server.py` | 327–341 | `(root / fname).resolve()` | `relative_to(root)` ✅ |

---

### 4.4 CSRF Protection ✅ OK

- Token generated with `secrets.token_hex(32)` per session
- Compared with `secrets.compare_digest()` (constant-time, no timing attacks)
- All state-changing endpoints call `_check_csrf()` before processing
- SSE rebuild endpoint uses a separate `token` query-param with the same CSRF value

---

### 4.5 Network Exposure ✅ OK

- Server binds to `127.0.0.1` only (`HOST = "127.0.0.1"` hardcoded in `main.py`)
- No telemetry, no remote calls initiated by the server

---

## 5. Summary

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | Medium | B310: `urllib.urlopen` in `main.py` | ✅ False positive |
| 2 | Low | B603/B607: subprocess without shell / partial path | ✅ Safe by design |
| 3 | Low | B110/B112: exception swallowing in controlled fallback paths | ✅ Acceptable |
| 4 | Low | B105: empty-string "hardcoded password" detections | ✅ False positive |
| 5 | Low | B101: `assert` in test fixture code under `testing/` | ✅ Informational |
| 6 | Info | No CVEs in dependencies | ✅ Clean |
| 7 | Fixed | `_modify_brick_in_file` path traversal | ✅ Re-verified fixed |

**Highest real risk:** No currently confirmed medium/high exploitable issue in the audited scope. The previous brick path traversal was re-verified as fixed on 2026-04-26.

---

## 6. Recommendations

1. **Add `requirements.txt`:** Currently no `requirements.txt` or `pyproject.toml` — makes dependency tracking and automated auditing harder.
2. **Pin dependency versions:** Document exact versions in a lockfile to make `pip-audit` reproducible in CI.
3. **Optional audit hygiene:** Consider excluding fixture/template test code under `testing/` from Bandit if those informational `B101` findings are considered noise.
