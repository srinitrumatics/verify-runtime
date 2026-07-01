"""tests evaluator — the heart of verification-driven development.

frontend : `npm run test` (Vitest).
backend  : `php artisan test` (Pest/PHPUnit).

Parses the runner summary to report failing test counts as findings. Optional
coverage floor (rules: evaluators.tests.min_coverage) is best-effort — it only
fails when coverage is both reported and below the floor.

The constitution requires passing tests for auth, checkout, order-ownership,
the order state machine, and inventory restore before release, so a failing or
absent test suite is treated as a hard fail for its target.
"""

from __future__ import annotations

import re

# Runners like Pest 3 wrap even the summary label in color codes
# ("\x1b[90mTests:\x1b[39m 108 passed"), which defeats plain-text regexes.
# Strip ANSI SGR/escape sequences before parsing so counts are color-agnostic.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _run_target(ctx, cfg, name, findings, metrics):
    target = ctx.target(name)
    if target is None:
        return None
    command = (cfg.get("commands") or {}).get(name)
    if not command:
        return None

    tool = "npm" if name == "frontend" else "php"
    if ctx.which(tool) is None:
        metrics[name] = f"skipped: {tool} not found"
        return None
    if name == "frontend" and not (target.path / "node_modules").exists():
        metrics[name] = "skipped: node_modules missing"
        return None
    if name == "backend" and not (target.path / "vendor").exists():
        metrics[name] = "skipped: composer vendor missing"
        return None

    ctx.log(f"  {name}: {command}")
    res = ctx.run(command, cwd=target.path, timeout=cfg.get("timeout", 900))
    passed_n, failed_n, total_n = _parse_counts(res.combined)
    entry = {"exit_code": res.code, "passed": passed_n, "failed": failed_n,
             "total": total_n, "timed_out": res.timed_out}

    if res.timed_out:
        findings.append(ctx.finding("high", f"{name} test suite timed out"))
        metrics[name] = entry
        return False

    if failed_n:
        for line in _failed_test_names(res.combined):
            findings.append(ctx.finding("high", f"failing test: {line}", file=name))
        if not any(f["severity"] == "high" for f in findings):
            findings.append(ctx.finding("high", f"{failed_n} {name} test(s) failing"))

    # No tests discovered at all is itself a problem for a target under a
    # test-coverage constitution.
    if res.ok and total_n == 0:
        findings.append(ctx.finding(
            "medium", f"{name} test command ran but reported zero tests"))

    # Optional coverage floor.
    floor = cfg.get("min_coverage", 0) or 0
    if floor:
        cov = _parse_coverage(res.combined)
        if cov is not None:
            entry["coverage_pct"] = cov
            if cov < floor:
                findings.append(ctx.finding(
                    "medium", f"{name} coverage {cov:.1f}% below floor {floor}%"))

    metrics[name] = entry
    return res.ok and failed_n == 0


def _parse_counts(output: str):
    output = _strip_ansi(output)
    # Vitest: "Tests  12 passed (12)" / "Tests  2 failed | 10 passed (12)"
    passed = failed = total = 0
    mv = re.search(r"Tests\s+(?:(\d+)\s+failed\s*\|\s*)?(\d+)\s+passed\s*\((\d+)\)", output)
    if mv:
        failed = int(mv.group(1) or 0)
        passed = int(mv.group(2))
        total = int(mv.group(3))
        return passed, failed, total
    # Pest / PHPUnit: "Tests:  34 passed (89 assertions)" / "Tests:  2 failed, 32 passed"
    mp = re.search(r"Tests:\s+([^\n]+)", output)
    if mp:
        seg = mp.group(1)
        fp = re.search(r"(\d+)\s+passed", seg)
        ff = re.search(r"(\d+)\s+failed", seg)
        passed = int(fp.group(1)) if fp else 0
        failed = int(ff.group(1)) if ff else 0
        return passed, failed, passed + failed
    # PHPUnit classic: "OK (34 tests, 89 assertions)"
    mo = re.search(r"OK\s+\((\d+)\s+tests?", output)
    if mo:
        return int(mo.group(1)), 0, int(mo.group(1))
    mf = re.search(r"FAILURES!.*?Tests:\s+(\d+).*?Failures:\s+(\d+)", output, re.DOTALL)
    if mf:
        total = int(mf.group(1))
        failed = int(mf.group(2))
        return total - failed, failed, total
    return passed, failed, total


def _failed_test_names(output: str, limit: int = 12):
    output = _strip_ansi(output)
    keep = []
    for raw in output.splitlines():
        line = raw.strip()
        if re.match(r"^(FAIL|✗|×|⨯)\b", line) or line.startswith("FAILED"):
            keep.append(line[:200])
        if len(keep) >= limit:
            break
    return keep


def _parse_coverage(output: str):
    output = _strip_ansi(output)
    m = re.search(r"All files\s*\|\s*([\d.]+)", output)      # vitest/istanbul table
    if m:
        return float(m.group(1))
    m = re.search(r"(?:Lines|Total Coverage)[:\s|]+([\d.]+)\s*%", output)
    if m:
        return float(m.group(1))
    return None


def evaluate(ctx) -> dict:
    cfg = ctx.config
    findings: list[dict] = []
    metrics: dict = {}

    fe = _run_target(ctx, cfg, "frontend", findings, metrics)
    be = _run_target(ctx, cfg, "backend", findings, metrics)

    outcomes = [x for x in (fe, be) if x is not None]
    if not outcomes:
        return {
            "passed": None, "skipped": True,
            "skip_reason": "no test runner available (npm / php not runnable)",
            "summary": "tests skipped", "findings": findings, "metrics": metrics,
        }

    passed = all(outcomes)
    score = ctx.score_from_findings(findings)
    parts = []
    for name, res in (("frontend", fe), ("backend", be)):
        if res is None:
            continue
        m = metrics.get(name, {})
        if isinstance(m, dict):
            parts.append(f"{name} {m.get('passed', 0)}✓/{m.get('failed', 0)}✗")
        else:
            parts.append(f"{name} {'ok' if res else 'fail'}")
    return {
        "passed": passed, "score": score,
        "summary": "; ".join(parts), "findings": findings, "metrics": metrics,
    }
