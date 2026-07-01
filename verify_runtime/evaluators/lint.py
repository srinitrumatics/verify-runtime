"""lint evaluator — style/quality gate.

frontend : `npm run lint` (ESLint). Warnings are treated as low findings,
           errors as medium; a non-zero exit is a fail.
backend  : `php vendor/bin/pint --test` (Laravel Pint, check-only). Any file
           that would be reformatted is a low finding.

Missing toolchains -> skipped and excluded from the composite.
"""

from __future__ import annotations

import re


def _lint_frontend(ctx, cfg, findings, metrics):
    target = ctx.target("frontend")
    if target is None:
        return None
    command = (cfg.get("commands") or {}).get("frontend")
    if not command or ctx.which("npm") is None:
        metrics["frontend"] = "skipped: npm not found"
        return None
    if not (target.path / "node_modules").exists():
        metrics["frontend"] = "skipped: node_modules missing"
        return None

    ctx.log(f"  frontend: {command}")
    res = ctx.run(command, cwd=target.path, timeout=cfg.get("timeout", 300))
    err_count, warn_count = _count_eslint(res.combined)
    metrics["frontend"] = {"exit_code": res.code, "errors": err_count,
                           "warnings": warn_count, "timed_out": res.timed_out}
    if res.timed_out:
        findings.append(ctx.finding("medium", "frontend lint timed out"))
        return False
    for line in _eslint_problem_lines(res.combined):
        sev = "medium" if "error" in line.lower() else "low"
        findings.append(ctx.finding(sev, f"eslint: {line}", file="frontend"))
    if not res.ok and err_count == 0 and warn_count == 0:
        findings.append(ctx.finding("medium", f"frontend lint failed (exit {res.code})"))
    return res.ok


def _lint_backend(ctx, cfg, findings, metrics):
    target = ctx.target("backend")
    if target is None:
        return None
    command = (cfg.get("commands") or {}).get("backend")
    if not command or ctx.which("php") is None:
        metrics["backend"] = "skipped: php not found"
        return None
    if not (target.path / "vendor" / "bin").exists():
        metrics["backend"] = "skipped: composer vendor missing"
        return None

    ctx.log(f"  backend: {command}")
    res = ctx.run(command, cwd=target.path, timeout=cfg.get("timeout", 300))
    dirty = _count_pint(res.combined)
    metrics["backend"] = {"exit_code": res.code, "files_needing_format": dirty,
                          "timed_out": res.timed_out}
    if res.timed_out:
        findings.append(ctx.finding("low", "backend lint (pint) timed out"))
        return False
    if not res.ok:
        detail = f"{dirty} file(s) not formatted" if dirty else f"exit {res.code}"
        findings.append(ctx.finding("low", f"pint: code style issues — {detail}", file="backend"))
    return res.ok


def _count_eslint(output: str):
    m = re.search(r"(\d+)\s+problems?\s+\((\d+)\s+errors?,\s+(\d+)\s+warnings?\)", output)
    if m:
        return int(m.group(2)), int(m.group(3))
    errors = len(re.findall(r"\berror\b", output, re.IGNORECASE))
    warns = len(re.findall(r"\bwarning\b", output, re.IGNORECASE))
    return errors, warns


def _eslint_problem_lines(output: str, limit: int = 15):
    keep = []
    for raw in output.splitlines():
        line = raw.strip()
        if re.match(r"^\d+:\d+\s+(error|warning)", line):
            keep.append(line[:200])
        if len(keep) >= limit:
            break
    return keep


def _count_pint(output: str):
    m = re.search(r"(\d+)\s+files?", output)
    return int(m.group(1)) if m else 0


def remediate(ctx, findings, ai=False) -> dict:
    """Deterministic auto-fixes: run the formatter/linter in write mode."""
    cfg = ctx.config
    fixes = cfg.get("commands_fix") or {}
    actions = []
    changed = False

    fe = ctx.target("frontend")
    if fe and ctx.which("npm") and (fe.path / "node_modules").exists():
        cmd = fixes.get("frontend", "npx eslint . --fix")
        res = ctx.run(cmd, cwd=fe.path, timeout=cfg.get("timeout", 300))
        actions.append({"title": "eslint --fix", "ran": True, "ok": res.launched, "detail": cmd})
        changed = changed or res.launched

    be = ctx.target("backend")
    if be and ctx.which("php") and (be.path / "vendor" / "bin").exists():
        cmd = fixes.get("backend", "php vendor/bin/pint")
        res = ctx.run(cmd, cwd=be.path, timeout=cfg.get("timeout", 300))
        actions.append({"title": "pint (write)", "ran": True, "ok": res.launched, "detail": cmd})
        changed = changed or res.launched

    if not actions:
        actions.append({"title": "no formatter available", "ran": False, "ok": True, "detail": ""})
    return {"actions": actions, "changed": changed}


def evaluate(ctx) -> dict:
    cfg = ctx.config
    findings: list[dict] = []
    metrics: dict = {}

    fe = _lint_frontend(ctx, cfg, findings, metrics)
    be = _lint_backend(ctx, cfg, findings, metrics)

    outcomes = [x for x in (fe, be) if x is not None]
    if not outcomes:
        return {
            "passed": None, "skipped": True,
            "skip_reason": "no linter available (npm / pint not runnable)",
            "summary": "lint skipped", "findings": findings, "metrics": metrics,
        }

    passed = all(outcomes)
    score = ctx.score_from_findings(findings)
    parts = []
    if fe is not None:
        parts.append("frontend " + ("clean" if fe else "issues"))
    if be is not None:
        parts.append("backend " + ("clean" if be else "issues"))
    return {
        "passed": passed, "score": score,
        "summary": "; ".join(parts), "findings": findings, "metrics": metrics,
    }
