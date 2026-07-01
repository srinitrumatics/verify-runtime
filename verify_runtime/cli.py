"""Command-line entry point: config discovery, argument parsing, subcommands
(`init`, `selftest`), and the default flag-driven verification run."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from verify_runtime.core import (
    load_yaml, discover_targets, stages_config, run_evaluators, compute_composite,
    evaluate_gate, render_human, build_json_report,
    run_remediation, render_remediation, Palette, _force_utf8,
)
from verify_runtime.resolver import resolve_source


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

    if args.json is not None:
        report = build_json_report(project, targets, selected, results, composite, gate)
        if remediation:
            report["remediation"] = remediation
        payload = json.dumps(report, indent=2)
        if args.json == "-":
            print(payload)
        else:
            out_path = Path(args.json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(payload, encoding="utf-8")
            log(f"wrote JSON report → {out_path}")

    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(2)
