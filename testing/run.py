#!/usr/bin/env python3
"""
Headless NiCo roundtrip runner.

Each test case lives in testing/configs/<name>/original. The runner copies that
directory to a temporary work tree, lets NiCo parse and write the files, then
copies the result to testing/configs/<name>/result and reports simple
regressions.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = Path(__file__).resolve().parent / "configs"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nico import config_manager, generator, hm_generator, importer  # noqa: E402
from nico.brix import brix_content_to_bricks, extract_brick_blocks, strip_brick_blocks  # noqa: E402


@dataclass
class CaseResult:
    name: str
    ok: bool = True
    info: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def fail(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    return (
        name == ".env"
        or name.endswith(".key")
        or "credential" in name
        or "secret" in name
    )


def _copy_tree(src: Path, dst: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory)
        return {name for name in names if _is_sensitive(base / name)}

    shutil.copytree(src, dst, ignore=ignore)


def _has_payload_files(path: Path) -> bool:
    return any(p.is_file() and p.name != ".gitkeep" for p in path.rglob("*"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return sorted((_normalize(v) for v in value), key=repr)
    return value


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, full_key))
        else:
            flat[full_key] = value
    return flat


def _managed_fields_from_original(config_dir: Path) -> dict[str, Any]:
    fields: dict[str, Any] = {}

    cfg_path = config_dir / "configuration.nix"
    if cfg_path.exists():
        content = strip_brick_blocks(_read_text(cfg_path))
        fields.update(importer.parse_config(content))

    home_path = config_dir / "home.nix"
    if home_path.exists():
        content = strip_brick_blocks(_read_text(home_path))
        hm_fields = importer.parse_home_config(content)
        if hm_fields:
            fields["home_manager"] = hm_fields

    flake_path = config_dir / "flake.nix"
    if flake_path.exists():
        fields.update(importer.parse_flake_nix(_read_text(flake_path)))
        fields["flakes"] = True

    return fields


def _write_nico_roundtrip(config_dir: Path) -> None:
    data = config_manager.load_config(str(config_dir))

    cfg_path = config_dir / "configuration.nix"
    if cfg_path.exists():
        content = _read_text(cfg_path)
        clean = strip_brick_blocks(content)
        recognized = importer.parse_config(clean)
        rest = importer.build_rest_brix(clean, recognized)
        blocks = extract_brick_blocks(content)
        if rest.strip():
            blocks.update(brix_content_to_bricks(rest, section="Start", existing_blocks=blocks))
        data.update(recognized)
        data["brick_blocks"] = blocks

    if (config_dir / "hardware-configuration.nix").exists():
        data["hardware_config"] = True

    flake_path = config_dir / "flake.nix"
    if flake_path.exists():
        data["flakes"] = True
        flake_content = _read_text(flake_path)
        data.update(importer.parse_flake_nix(flake_content))
        data["flake_brick_blocks"] = importer.build_flake_brix(flake_content)

    home_path = config_dir / "home.nix"
    if home_path.exists():
        home_content = _read_text(home_path)
        clean_home = strip_brick_blocks(home_content)
        hm = importer.parse_home_config(clean_home)
        rest_home = importer.build_home_rest_brix(clean_home, hm)
        hm_blocks = extract_brick_blocks(home_content)
        if rest_home.strip():
            hm_blocks.update(brix_content_to_bricks(rest_home, section="End", existing_blocks=hm_blocks))
        hm["hm_brick_blocks"] = hm_blocks
        data["home_manager"] = hm
        data["hm_brick_blocks"] = hm_blocks

    cfg_path.write_text(generator.generate_configuration_nix(data), encoding="utf-8")

    if data.get("flakes"):
        flake_path.write_text(
            generator.generate_flake_nix(data, nixos_dir=str(config_dir)),
            encoding="utf-8",
        )

    hm = dict(data.get("home_manager") or {})
    if hm.get("enabled") or home_path.exists():
        hm["hm_brick_blocks"] = data.get("hm_brick_blocks", {})
        home_path.write_text(hm_generator.generate_home_nix(hm), encoding="utf-8")


def _compare_managed_fields(original: Path, result: Path, case: CaseResult) -> None:
    expected = _flatten(_managed_fields_from_original(original))
    actual = _flatten(_managed_fields_from_original(result))

    ignored = {"brick_blocks", "flake_brick_blocks", "hm_brick_blocks"}
    for key, expected_value in sorted(expected.items()):
        if key in ignored or key.rsplit(".", 1)[-1] in ignored:
            continue
        if key not in actual:
            # An option that is empty in the original (e.g. a systemPackages
            # list with every entry commented out) is not written back at all –
            # NiCo omits empty options on purpose. Nothing is lost, so this is
            # not a regression.
            if expected_value in ([], {}, "", None):
                continue
            case.fail(f"missing managed field after roundtrip: {key}")
            continue
        if _normalize(actual[key]) != _normalize(expected_value):
            case.fail(
                "changed managed field after roundtrip: "
                f"{key}: {expected_value!r} -> {actual[key]!r}"
            )


def _check_unmanaged_preserved(original: Path, result: Path, case: CaseResult) -> None:
    original_cfg = original / "configuration.nix"
    result_cfg = result / "configuration.nix"
    if not original_cfg.exists() or not result_cfg.exists():
        return

    content = strip_brick_blocks(_read_text(original_cfg))
    recognized = importer.parse_config(content)
    rest = importer.build_rest_brix(content, recognized)
    if not rest.strip():
        return

    result_text = _read_text(result_cfg)
    missing_lines = []
    for line in rest.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("# <") or stripped.startswith("# </"):
            continue
        if stripped not in result_text:
            missing_lines.append(stripped)
            if len(missing_lines) >= 5:
                break

    if missing_lines:
        case.fail("unmanaged Nix content missing from result: " + "; ".join(missing_lines))


def _run_command(args: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output


def _dry_run_signals(output: str) -> dict[str, Any]:
    missing_paths = re.findall(r"error: path '([^']+)' does not exist", output)
    missing_attrs = re.findall(r"error: attribute '([^']+)' missing", output)
    return {
        "lockfile_updated": "updating lock file" in output or "creating lock file" in output,
        "missing_paths": missing_paths,
        "missing_attrs": missing_attrs,
    }


def _classify_dry_run_output(output: str) -> tuple[str, str]:
    signals = _dry_run_signals(output)
    if signals["missing_paths"]:
        path = signals["missing_paths"][0]
        return "MISSING_PATH", f"Fehlender Pfad: {path}"
    if signals["missing_attrs"]:
        attr = signals["missing_attrs"][0]
        return "MISSING_ATTR", f"Fehlendes Attribut/Argument: {attr}"
    if signals["lockfile_updated"]:
        return "LOCKFILE_MUTATION", "Lockfile wurde waehrend der Auswertung aktualisiert"
    if "not tracked by Git" in output:
        return "GIT_TRACKING", "Flake-Dateien sind fuer Nix im Git-Kontext nicht sichtbar"
    if "timed out" in output.lower():
        return "TIMEOUT", "Dry-Run lief in ein Timeout"
    return "EVAL_ERROR_OTHER", "Sonstiger Evaluationsfehler"


def _classify_severity(category: str, output: str = "") -> str:
    if category in {"NO_HOST_DETECTED", "NO_NIXOS_REBUILD", "NO_CONFIGURATION_NIX", "NO_NIX_INSTANTIATE", "TIMEOUT"}:
        return "yellow"
    if category == "MISSING_PATH":
        missing_paths = _dry_run_signals(output).get("missing_paths", [])
        if any(path.endswith("/hardware-configuration.nix") for path in missing_paths):
            return "yellow"
        return "red"
    if category == "EVAL_ERROR_OTHER" and "cannot find flake 'flake:" in output:
        return "yellow"
    if category in {"MISSING_ATTR", "GIT_TRACKING", "EVAL_ERROR_OTHER"}:
        return "red"
    if category == "LOCKFILE_MUTATION":
        return "green"
    return "red"


def _append_host_report(case: CaseResult, host: str, *, status: str, output: str = "", skipped_reason: str = "") -> None:
    dry_run = case.report.setdefault("dry_run", {})
    host_results = dry_run.setdefault("host_results", [])
    entry: dict[str, Any] = {"host": host, "status": status}
    if skipped_reason:
        entry["category"] = skipped_reason
        entry["summary"] = skipped_reason
        entry["severity"] = _classify_severity(skipped_reason)
    elif output:
        if status == "passed":
            entry["category"] = "PASSED"
            entry["summary"] = "Dry-Run erfolgreich"
            entry["severity"] = "green"
        else:
            category, summary = _classify_dry_run_output(output)
            entry["category"] = category
            entry["summary"] = summary
            entry["severity"] = _classify_severity(category, output)
        entry["signals"] = _dry_run_signals(output)
        entry["output_excerpt"] = output[:1200]
    host_results.append(entry)


def _case_severity(case: CaseResult) -> str:
    dry_run = case.report.get("dry_run", {})
    host_results = dry_run.get("host_results", [])
    severity = dry_run.get("severity")
    if host_results:
        severities = [str(host_result.get("severity") or "red") for host_result in host_results]
        if "red" in severities:
            return "red"
        if "yellow" in severities:
            return "yellow"
        return "green"
    if isinstance(severity, str) and severity:
        return severity
    if dry_run.get("status") == "passed":
        return "green"
    if dry_run.get("status") == "skipped":
        return _classify_severity(str(dry_run.get("reason") or ""))
    if dry_run.get("status") == "failed":
        return _classify_severity(str(dry_run.get("category") or ""), str(dry_run.get("output_excerpt") or ""))
    return "red" if not case.ok else "green"


def _syntax_check(result: Path, case: CaseResult) -> None:
    if shutil.which("nix-instantiate") is None:
        case.warn("nix-instantiate not found; syntax check skipped")
        return

    for nix_file in sorted(result.rglob("*.nix")):
        code, output = _run_command(["nix-instantiate", "--parse", str(nix_file)], result)
        if code != 0:
            rel = nix_file.relative_to(result)
            case.fail(f"syntax check failed for {rel}: {output}")


def _rebuild_dry_run(result: Path, case: CaseResult) -> None:
    def _infer_flake_hosts(flake_content: str) -> list[str]:
        fields = importer.parse_flake_nix(flake_content)
        hosts: list[str] = []

        default_host = fields.get("flake_default_host") or fields.get("hostname")
        if isinstance(default_host, str) and default_host.strip():
            hosts.append(default_host.strip())

        for entry in (fields.get("flake_hosts") or []):
            if isinstance(entry, dict):
                name = (entry.get("name") or "").strip()
            else:
                name = str(entry).strip()
            if name and name not in hosts:
                hosts.append(name)

        # Fallback for flakes that declare hosts as dotted assignments like
        # `nixosConfigurations.<name> = nixpkgs.lib.nixosSystem { ... };`
        for match in __import__("re").finditer(
            r'\bnixosConfigurations\.(?:"([^"]+)"|([\w.-]+))\s*=',
            flake_content,
        ):
            name = (match.group(1) or match.group(2) or "").strip()
            if name and name not in hosts:
                hosts.append(name)

        return hosts

    flake_path = result / "flake.nix"
    if flake_path.exists():
        case.report["mode"] = "flake"
        if shutil.which("nixos-rebuild") is None:
            case.warn("nixos-rebuild not found; rebuild dry-run skipped")
            case.report["dry_run"] = {"status": "skipped", "reason": "NO_NIXOS_REBUILD"}
            return
        hosts = _infer_flake_hosts(_read_text(flake_path))
        case.report["dry_run"] = {"status": "running", "hosts_detected": hosts}
        if not hosts:
            case.warn("flake.nix found, but no host detected; rebuild dry-run skipped")
            case.report["dry_run"] = {"status": "skipped", "reason": "NO_HOST_DETECTED", "hosts_detected": []}
            return
        flake_tmp = Path(tempfile.mkdtemp(prefix=f"nico-flake-dry-run-{case.name}-"))
        flake_work = flake_tmp / "config"
        try:
            _copy_tree(result, flake_work)
            for host in hosts:
                args = ["nixos-rebuild", "dry-run", "--flake", f"{flake_work}#{host}"]
                code, output = _run_command(args, flake_work, timeout=900)
                if code != 0:
                    _append_host_report(case, host, status="failed", output=output)
                    case.fail(f'rebuild dry-run failed for flake host "{host}": ' + output)
                else:
                    _append_host_report(case, host, status="passed", output=output)
        finally:
            shutil.rmtree(flake_tmp, ignore_errors=True)
        if case.report.get("dry_run", {}).get("status") == "running":
            case.report["dry_run"]["status"] = "completed"
        return

    cfg_path = result / "configuration.nix"
    case.report["mode"] = "non-flake"
    if not cfg_path.exists():
        case.warn("no configuration.nix found; rebuild dry-run skipped")
        case.report["dry_run"] = {"status": "skipped", "reason": "NO_CONFIGURATION_NIX"}
        return
    if shutil.which("nix-instantiate") is None:
        case.warn("nix-instantiate not found; rebuild dry-run skipped")
        case.report["dry_run"] = {"status": "skipped", "reason": "NO_NIX_INSTANTIATE"}
        return

    args = [
        "nix-instantiate",
        "--eval",
        "<nixpkgs/nixos>",
        "-A", "config.system.build.toplevel.drvPath",
        "-I", f"nixos-config={cfg_path}",
    ]
    code, output = _run_command(args, result, timeout=900)
    if code != 0:
        category, summary = _classify_dry_run_output(output)
        case.report["dry_run"] = {
            "status": "failed",
            "category": category,
            "summary": summary,
            "severity": _classify_severity(category, output),
            "signals": _dry_run_signals(output),
            "output_excerpt": output[:1200],
        }
        case.fail("rebuild dry-run failed: " + output)
    else:
        case.report["dry_run"] = {"status": "passed", "severity": "green"}


def _case_dirs(selected: str | None) -> list[Path]:
    if selected:
        return [CONFIGS_DIR / selected]
    if not CONFIGS_DIR.exists():
        return []
    return sorted(p for p in CONFIGS_DIR.iterdir() if p.is_dir())


def run_case(case_dir: Path, args: argparse.Namespace) -> CaseResult:
    case = CaseResult(case_dir.name)
    original = case_dir / "original"
    result = case_dir / "result"

    if not original.is_dir():
        case.fail("missing original directory")
        return case
    if not _has_payload_files(original):
        case.warn("original directory is empty; skipped")
        return case
    if not (original / "configuration.nix").exists() and not (original / "flake.nix").exists():
        case.fail("original needs configuration.nix or flake.nix")
        return case

    work_parent = Path(tempfile.mkdtemp(prefix=f"nico-test-{case.name}-"))
    work_dir = work_parent / "work"
    try:
        _copy_tree(original, work_dir)
        _write_nico_roundtrip(work_dir)

        if result.exists():
            shutil.rmtree(result)
        _copy_tree(work_dir, result)

        _compare_managed_fields(original, result, case)
        _check_unmanaged_preserved(original, result, case)

        if args.syntax:
            _syntax_check(result, case)
        if args.rebuild_dry_run:
            _rebuild_dry_run(result, case)
    except Exception as exc:  # noqa: BLE001 - test runner should report all case failures
        case.fail(f"runner error: {exc}")
    finally:
        if args.keep_work:
            case.info.append(f"work directory kept: {work_dir}")
        else:
            shutil.rmtree(work_parent, ignore_errors=True)

    return case


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run headless NiCo roundtrip tests.")
    parser.add_argument("--config", help="Only run one config from testing/configs/<name>.")
    parser.add_argument("--keep-work", action="store_true", help="Keep temporary work dirs.")
    parser.add_argument("--no-syntax", dest="syntax", action="store_false", help="Skip nix parse checks.")
    parser.add_argument("--rebuild-dry-run", action="store_true", help="Run nixos-rebuild dry-run.")
    parser.add_argument("--report-json", default="testing/report-dry-run.json",
                        help="Write structured dry-run report to this JSON path.")
    parser.set_defaults(syntax=True)
    return parser.parse_args()


def _write_report(results: list[CaseResult], args: argparse.Namespace) -> None:
    if not args.rebuild_dry_run:
        return

    category_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {"green": 0, "yellow": 0, "red": 0}
    evaluated_cases = 0
    report_cases = []
    for result in results:
        dry_run = result.report.get("dry_run", {})
        host_results = dry_run.get("host_results", [])
        severity = _case_severity(result)
        dry_run["severity"] = severity

        entry = {
            "name": result.name,
            "ok": result.ok,
            "warnings": result.warnings,
            "errors": result.errors,
            "report": result.report,
        }
        report_cases.append(entry)
        if host_results:
            evaluated_cases += 1
        for host_result in host_results:
            category = host_result.get("category")
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1
        category = dry_run.get("category")
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        if severity in severity_counts:
            severity_counts[severity] += 1

    payload = {
        "summary": {
            "cases_total": len(results),
            "cases_failed": sum(1 for r in results if not r.ok),
            "cases_with_host_results": evaluated_cases,
            "severity_counts": severity_counts,
            "category_counts": category_counts,
        },
        "cases": report_cases,
    }

    report_path = (ROOT / args.report_json).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    cases = _case_dirs(args.config)
    if not cases:
        print("No test configs found.")
        return 1

    results = [run_case(case_dir, args) for case_dir in cases]

    for result in results:
        status = _case_severity(result).upper()
        print(f"[{status}] {result.name}")
        for message in result.info:
            print(f"  info: {message}")
        for message in result.warnings:
            print(f"  warning: {message}")
        for message in result.errors:
            print(f"  error: {message}")

    _write_report(results, args)
    if args.rebuild_dry_run:
        print(f"\nReport written: {args.report_json}")

    severities = [_case_severity(result) for result in results]
    green = sum(1 for severity in severities if severity == "green")
    yellow = sum(1 for severity in severities if severity == "yellow")
    red = sum(1 for severity in severities if severity == "red")
    print(f"\n{green} green, {yellow} yellow, {red} red")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
