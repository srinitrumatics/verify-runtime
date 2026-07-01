"""Command-line entry point: config discovery, argument parsing, subcommands
(`init`, `selftest`), and the default flag-driven verification run."""
from __future__ import annotations

import argparse
import importlib.metadata as im
import json
import os
import pkgutil
import shutil
import sys
from pathlib import Path
from typing import Optional

from verify_runtime.core import (
    load_yaml, discover_targets, stages_config, run_evaluators, compute_composite,
    evaluate_gate, render_human, build_json_report,
    run_remediation, render_remediation, Palette, _force_utf8,
)
from verify_runtime.resolver import resolve_source
from verify_runtime import dashboard, history


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify", add_help=True,
        description="Verification-Driven Development runner")
    parser.add_argument("--json", nargs="?", const="-", default=None, metavar="PATH",
                        help="emit JSON report (to PATH, or stdout if no path)")
    parser.add_argument("--only", default=None, help="comma list of evaluators to run")
    parser.add_argument("--skip", default=None, help="comma list of evaluators to skip")
    parser.add_argument("--target", default=None, help="comma list of targets to include")
    parser.add_argument("--fail-fast", action="store_true", help="stop at first FAIL")
    parser.add_argument("--fix", action="store_true",
                        help="auto-remediate: apply safe deterministic fixes, then re-run")
    parser.add_argument("--ai", action="store_true",
                        help="with --fix, also generate Claude patches for findings (preview only)")
    parser.add_argument("--apply", action="store_true",
                        help="with --fix --ai, actually write the previewed AI patches")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    parser.add_argument("--quiet", action="store_true", help="hide per-finding detail")
    parser.add_argument("--list", action="store_true", help="list targets/evaluators and exit")
    parser.add_argument("--history", action="store_true",
                        help="record this run to the history db")
    parser.add_argument("--html", nargs="?", const="verify-report.html", default=None,
                        metavar="PATH", help="write a self-contained HTML report")
    return parser.parse_args(argv)


def _csv(value: Optional[str]) -> Optional[set[str]]:
    if not value:
        return None
    return {x.strip() for x in value.split(",") if x.strip()}


def _find_config(start: Path) -> Path:
    for d in [start, *start.parents]:
        c = d / "verification.yaml"
        if c.exists():
            return c
    raise SystemExit("verify: no verification.yaml found (run `verify init`)")


def _find_config_or_none(start: Path) -> Optional[Path]:
    for d in [start, *start.parents]:
        c = d / "verification.yaml"
        if c.exists():
            return c
    return None


def _parse_runtime_spec(spec: str):
    import re
    m = re.match(r"\s*(>=|<=|==|>|<)?\s*([0-9][0-9.]*)\s*$", spec)
    if not m:
        return None
    op = m.group(1) or ">="
    ver = tuple(int(x) for x in m.group(2).split("."))
    return op, ver


def _version_tuple(v: str):
    parts = []
    for x in v.split("."):
        digits = "".join(c for c in x if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _satisfies_runtime(installed: str, spec: str) -> Optional[bool]:
    parsed = _parse_runtime_spec(spec)
    if parsed is None:
        return None
    op, ver = parsed
    iv = _version_tuple(installed)
    n = max(len(iv), len(ver))
    iv = iv + (0,) * (n - len(iv))
    ver = ver + (0,) * (n - len(ver))
    if op == ">=":
        return iv >= ver
    if op == "<=":
        return iv <= ver
    if op == "==":
        return iv == ver
    if op == ">":
        return iv > ver
    if op == "<":
        return iv < ver
    return None  # pragma: no cover


_STARTER = """version: 2
runtime: ">=1.0"
plugin_paths: [verification/plugins]

project: {{ name: {name}, root: . }}

targets:
{targets}
gate:
  min_score: 80
  block_on_severity: [critical]
  required_pass: [build, tests]

severity_weights: {{ critical: 40, high: 20, medium: 8, low: 2, info: 0 }}

verification:
  build: {{ module: build, phase: "Build & Test", weight: 12, commands: {{ frontend: npm run build }} }}
  tests: {{ module: tests, phase: "Build & Test", weight: 14, commands: {{ frontend: npm run test }} }}
"""


def cmd_init(root: Path) -> int:
    cfg = root / "verification.yaml"
    if cfg.exists():
        print(f"verify init: refusing to overwrite existing {cfg}", flush=True)
        return 2
    targets = ""
    if (root / "package.json").exists():
        targets += "  frontend: { path: ., kind: node, detect: package.json, language: javascript }\n"
    if (root / "composer.json").exists():
        targets += "  backend: { path: ., kind: php, detect: composer.json, language: php }\n"
    if not targets:
        targets = "  app: { path: ., detect: null, language: unknown }\n"
    cfg.write_text(_STARTER.format(name=root.name, targets=targets), encoding="utf-8")
    (root / "verification" / "plugins").mkdir(parents=True, exist_ok=True)
    print(f"verify init: wrote {cfg} and verification/plugins/", flush=True)
    return 0


def cmd_selftest(only):
    from verify_runtime.selftest import aggregate
    suites = aggregate(only=only)
    ok = True
    for name, s in suites.items():
        status = "PASS" if s["failed"] == 0 and s["total"] else "FAIL"
        ok = ok and status == "PASS"
        print(f"{status}  {name}: {s['passed']}/{s['total']} passed")
    return 0 if ok and suites else 1


def cmd_doctor(argv: list[str]) -> int:
    from verify_runtime import __version__

    color_enabled = (
        "--no-color" not in argv
        and os.environ.get("NO_COLOR") is None
        and sys.stdout.isatty()
    )
    p = Palette(color_enabled)
    problems = 0
    lines = [p.bold("verify doctor"), ""]

    lines.append(f"Runtime:        verify-runtime {__version__}")
    lines.append(
        f"Python:         {sys.version.split()[0]} "
        f"({'.'.join(str(x) for x in sys.version_info[:3])})"
    )

    config_path = _find_config_or_none(Path.cwd())
    rules: dict = {}
    root = Path.cwd()
    config_ok = True

    if config_path is None:
        lines.append("Configuration:  no verification.yaml found (run `verify init`)")
    else:
        lines.append(f"Configuration:  found {config_path}")
        try:
            rules = load_yaml(config_path) or {}
            lines.append("                parses OK")
        except Exception as e:
            config_ok = False
            problems += 1
            lines.append(p.red(f"                FAILED to parse: {e}"))
        root = (config_path.parent / ((rules.get("project") or {}).get("root", "."))).resolve()

        runtime_spec = rules.get("runtime")
        if runtime_spec:
            sat = _satisfies_runtime(__version__, str(runtime_spec))
            if sat is None:
                lines.append(
                    p.yellow(f"                runtime floor '{runtime_spec}' could not be "
                             f"parsed (warning)"))
            elif sat:
                lines.append(f"                runtime floor '{runtime_spec}' satisfied")
            else:
                problems += 1
                lines.append(p.red(
                    f"                runtime floor '{runtime_spec}' NOT satisfied by "
                    f"installed {__version__}"))

    lines.append("")
    lines.append("Plugins (installed):")
    dist_evaluators: dict[str, list[str]] = {}
    for ep in im.entry_points(group="verify.evaluators"):
        dist_name = getattr(ep.dist, "name", None) or "?"
        dist_evaluators.setdefault(dist_name, []).append(ep.name)
    if dist_evaluators:
        for dist_name in sorted(dist_evaluators):
            lines.append(f"  - {dist_name}: {', '.join(sorted(dist_evaluators[dist_name]))}")
    else:
        lines.append("  (none found)")

    lines.append("")
    lines.append("Entry points:")
    selftest_names = sorted(ep.name for ep in im.entry_points(group="verify.selftests"))
    if selftest_names:
        lines.append(f"  PASS  verify.selftests resolves: {', '.join(selftest_names)}")
    else:
        problems += 1
        lines.append(p.red("  FAIL  verify.selftests group has no entries"))

    if config_path is not None and config_ok:
        stages = stages_config(rules)
        resolved: list[tuple[str, str]] = []
        unresolved: list[str] = []
        for name, cfg in stages.items():
            cfg = cfg or {}
            try:
                source, _ = resolve_source(cfg.get("module", name), rules, root)
                resolved.append((name, source))
            except FileNotFoundError:
                unresolved.append(name)
        if stages:
            for name, source in resolved:
                lines.append(f"    {name:<14} -> {source}")
            if unresolved:
                problems += len(unresolved)
                lines.append(p.red(f"  FAIL  unresolved stage module(s): {', '.join(unresolved)}"))
            else:
                lines.append(f"  PASS  all {len(stages)} configured stage module(s) resolve")

    lines.append("")
    lines.append("Environment:")
    python_path = shutil.which("python") or shutil.which("python3")
    lines.append(f"  python:  {'found' if python_path else 'MISSING'} ({python_path or '-'})")
    if config_path is not None and config_ok:
        needs_npm = False
        needs_php = False
        for cfg in stages_config(rules).values():
            cfg = cfg or {}
            for cmd in (cfg.get("commands") or {}).values():
                if isinstance(cmd, str):
                    stripped = cmd.strip()
                    if stripped.startswith("npm"):
                        needs_npm = True
                    if stripped.startswith("php"):
                        needs_php = True
        if needs_npm:
            npm_path = shutil.which("npm")
            lines.append(f"  npm:     {'found' if npm_path else 'MISSING (optional)'} ({npm_path or '-'})")
        if needs_php:
            php_path = shutil.which("php")
            lines.append(f"  php:     {'found' if php_path else 'MISSING (optional)'} ({php_path or '-'})")
    key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    lines.append(f"  ANTHROPIC_API_KEY: {'set' if key_set else 'not set (informational; affects AI stages)'}")

    lines.append("")
    if problems:
        lines.append(p.red(f"doctor: PROBLEMS FOUND ({problems})"))
    else:
        lines.append(p.green("doctor: PASS"))

    print("\n".join(lines))
    return 1 if problems else 0


def cmd_plugins(argv: list[str]) -> int:
    from verify_runtime import evaluators as evaluators_pkg

    color_enabled = (
        "--no-color" not in argv
        and os.environ.get("NO_COLOR") is None
        and sys.stdout.isatty()
    )
    p = Palette(color_enabled)
    lines = [p.bold("verify plugins"), ""]

    lines.append("Installed:")
    dist_evaluators: dict[str, list[str]] = {}
    for ep in im.entry_points(group="verify.evaluators"):
        dist_name = getattr(ep.dist, "name", None) or "?"
        dist_evaluators.setdefault(dist_name, []).append(ep.name)
    for dist_name in sorted(dist_evaluators):
        lines.append(f"  {dist_name}: {', '.join(sorted(dist_evaluators[dist_name]))}")

    builtin_names = sorted(
        m.name for m in pkgutil.iter_modules(evaluators_pkg.__path__)
        if not m.name.startswith("_")
    )
    lines.append(f"  verify-runtime (builtin): {', '.join(builtin_names)}")

    lines.append("")
    lines.append("Local:")
    config_path = _find_config_or_none(Path.cwd())
    rules: dict = {}
    root = Path.cwd()
    if config_path is None:
        lines.append("  no verification.yaml found; no plugin_paths to scan")
    else:
        try:
            rules = load_yaml(config_path) or {}
        except Exception as e:
            lines.append(p.red(f"  could not parse {config_path}: {e}"))
            rules = {}
        root = (config_path.parent / ((rules.get("project") or {}).get("root", "."))).resolve()
        plugin_paths = rules.get("plugin_paths") or []
        found_any = False
        for rel in plugin_paths:
            d = root / rel
            if d.is_dir():
                for f in sorted(d.glob("*.py")):
                    found_any = True
                    lines.append(f"  {f}  (module: {f.stem})")
        if not found_any:
            lines.append("  (none found)")

    if config_path is not None and rules:
        lines.append("")
        lines.append("Configured stages:")
        for name, cfg in stages_config(rules).items():
            cfg = cfg or {}
            try:
                source, _ = resolve_source(cfg.get("module", name), rules, root)
            except FileNotFoundError:
                source = "unresolved"
            lines.append(f"  {name:<14} -> {source}")

    print("\n".join(lines))
    return 0


def _fmt_gate(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _fmt_findings(findings: dict) -> str:
    order = ["critical", "high", "medium", "low", "info"]
    parts = [f"{k[0].upper()}{findings.get(k, 0)}" for k in order if findings.get(k)]
    return " ".join(parts) if parts else "none"


def _sparkline(series: list[float]) -> str:
    if not series:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(series), max(series)
    span = hi - lo
    if span == 0:
        return blocks[0] * len(series)
    out = []
    for v in series:
        idx = int((v - lo) / span * (len(blocks) - 1))
        out.append(blocks[idx])
    return "".join(out)


def cmd_history(argv: list[str]) -> int:
    _force_utf8()
    limit = 20
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    stage = None
    if "--stage" in argv:
        stage = argv[argv.index("--stage") + 1]

    config_path = _find_config_or_none(Path.cwd())
    rules: dict = {}
    root = Path.cwd()
    if config_path is not None:
        try:
            rules = load_yaml(config_path) or {}
        except Exception:
            rules = {}
        root = (config_path.parent / ((rules.get("project") or {}).get("root", "."))).resolve()

    rows = history.recent(root, rules, limit=limit)
    if not rows:
        print("verify history: no history recorded yet "
              "(run with --history or set history.enabled: true)")
        return 0

    print(f"{'ts':<26} {'sha':<9} {'composite':>9}  {'gate':<4}  findings")
    for row in rows:
        ts = str(row.get("ts") or "")[:25]
        sha = row.get("git_sha") or "-"
        composite = row.get("composite")
        composite_s = f"{composite:.2f}" if composite is not None else "-"
        gate_s = _fmt_gate(row.get("gate_passed"))
        findings_s = _fmt_findings(row.get("findings_by_severity") or {})
        print(f"{ts:<26} {sha:<9} {composite_s:>9}  {gate_s:<4}  {findings_s}")

    series = history.trend(root, rules, stage=stage, limit=limit)
    if series:
        label = f"trend ({stage})" if stage else "trend (composite)"
        values = " ".join(f"{v:.1f}" for v in series)
        print(f"{label}: {_sparkline(series)}  [{values}]")

    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "init":
        root = Path(".")
        if "--root" in argv:
            root = Path(argv[argv.index("--root") + 1])
        return cmd_init(root)
    if argv and argv[0] == "selftest":
        only = argv[argv.index("--suite") + 1] if "--suite" in argv else None
        return cmd_selftest(only)
    if argv and argv[0] == "doctor":
        return cmd_doctor(argv[1:])
    if argv and argv[0] == "plugins":
        return cmd_plugins(argv[1:])
    if argv and argv[0] == "history":
        return cmd_history(argv[1:])

    args = parse_args(argv)
    _force_utf8()

    color_enabled = (
        not args.no_color
        and os.environ.get("NO_COLOR") is None
        and sys.stdout.isatty()
    )
    p = Palette(color_enabled)

    def log(msg: str) -> None:
        if not args.list:
            print(p.dim(msg), file=sys.stderr)

    config_path = _find_config(Path.cwd())
    try:
        rules = load_yaml(config_path) or {}
    except Exception as e:  # pragma: no cover
        print(f"error: failed to parse {config_path}: {e}", file=sys.stderr)
        return 2

    project = ((rules.get("project") or {}).get("name")) or "project"
    root = (config_path.parent / ((rules.get("project") or {}).get("root", "."))).resolve()

    targets = discover_targets(rules, root)
    target_filter = _csv(args.target)
    selected = [n for n in targets if (target_filter is None or n in target_filter)]

    if args.list:
        print(f"project: {project}")
        print(f"root:    {root}")
        print("targets:")
        for n, t in targets.items():
            mark = "present" if t.present else "absent"
            print(f"  - {n:<10} {t.kind:<16} [{mark}]  {t.path}")
        print("stages (phase · weight · minimum · source):")
        for n, cfg in stages_config(rules).items():
            cfg = cfg or {}
            en = "on " if cfg.get("enabled", True) else "off"
            mn = cfg.get("minimum")
            mn_s = f"min {mn:g}" if mn is not None else "no-min"
            try:
                source, _ = resolve_source(cfg.get("module", n), rules, root)
            except FileNotFoundError:
                source = "unresolved"
            print(f"  - {n:<14} [{en}] {str(cfg.get('phase','Other')):<16} "
                  f"w{cfg.get('weight', 1):<3} {mn_s:<7} {source}")
        return 0

    only = _csv(args.only)
    skip = _csv(args.skip)

    results = run_evaluators(rules, root, targets, selected, only, skip,
                             args.fail_fast, log)

    remediation = None
    if args.fix:
        remediation = run_remediation(rules, root, targets, selected, results,
                                      args.ai, args.apply, log)
        if remediation.get("changed"):
            log("re-running verification after remediation…")
            results = run_evaluators(rules, root, targets, selected, only, skip,
                                     args.fail_fast, log)

    composite = compute_composite(results)
    gate = evaluate_gate(rules, results, composite)

    sys.stdout.write(render_human(p, project, targets, selected, results, composite, gate, args.quiet))
    if remediation:
        sys.stdout.write(render_remediation(p, remediation))

    report = None
    if args.json is not None or args.html is not None or history.enabled(rules, args.history):
        report = build_json_report(project, targets, selected, results, composite, gate)
        if remediation:
            report["remediation"] = remediation

    if args.json is not None:
        payload = json.dumps(report, indent=2)
        if args.json == "-":
            print(payload)
        else:
            out_path = Path(args.json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(payload, encoding="utf-8")
            log(f"wrote JSON report → {out_path}")

    if history.enabled(rules, args.history):
        try:
            db_path = history.record(root, rules, report)
            log(f"recorded run to history db → {db_path}")
        except Exception as e:
            log(f"warning: failed to record history: {e}")

    if args.html is not None:
        try:
            try:
                trend = history.trend(root, rules)
            except Exception as e:
                log(f"warning: failed to load history trend for HTML report: {e}")
                trend = []
            html_doc = dashboard.render_html(report, trend)
            html_out_path = Path(args.html)
            html_out_path.parent.mkdir(parents=True, exist_ok=True)
            html_out_path.write_text(html_doc, encoding="utf-8")
            log(f"wrote HTML report → {html_out_path}")
        except Exception as e:
            log(f"warning: failed to write HTML report: {e}")

    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(2)
