"""build evaluator — does the project compile / bundle?

frontend : runs the configured build command (e.g. `npm run build`), which for
           this repo is `tsc -b && vite build` — so it doubles as a type check.
backend  : Laravel has no bundler, so we run a `php -l` syntax sweep over the
           application source as a compile-equivalent.

Contract (returned dict): passed, score, summary, findings[], metrics{}.
A missing toolchain yields skipped=True and is excluded from the composite.
"""

from __future__ import annotations

from pathlib import Path


def _build_frontend(ctx, cfg, findings, metrics):
    target = ctx.target("frontend")
    if target is None:
        return None
    command = (cfg.get("commands") or {}).get("frontend")
    if not command:
        return None
    if ctx.which("npm") is None:
        ctx.log("  npm not found — frontend build skipped")
        metrics["frontend"] = "skipped: npm not found"
        return None
    if not (target.path / "node_modules").exists():
        ctx.log("  node_modules missing — run `npm install` first")
        metrics["frontend"] = "skipped: node_modules missing"
        findings.append(ctx.finding(
            "low", "frontend dependencies not installed (node_modules missing); "
                   "build could not be verified"))
        return None

    ctx.log(f"  frontend: {command}")
    res = ctx.run(command, cwd=target.path, timeout=cfg.get("timeout", 600))
    metrics["frontend"] = {"command": command, "exit_code": res.code,
                           "duration_s": round(res.duration, 2),
                           "timed_out": res.timed_out}
    if res.timed_out:
        findings.append(ctx.finding("high", f"frontend build timed out after {cfg.get('timeout', 600)}s"))
        return False
    if not res.ok:
        for line in _error_lines(res.combined):
            findings.append(ctx.finding("critical", f"build error: {line}", file="frontend"))
        if not any(f["severity"] == "critical" for f in findings):
            findings.append(ctx.finding("critical", f"frontend build failed (exit {res.code})"))
        return False
    return True


def _build_backend(ctx, cfg, findings, metrics):
    target = ctx.target("backend")
    if target is None:
        return None
    if not cfg.get("php_lint", True):
        return None
    if ctx.which("php") is None:
        ctx.log("  php not found — backend compile sweep skipped")
        metrics["backend"] = "skipped: php not found"
        return None

    scan_dirs = [target.path / d for d in ("app", "routes", "database", "config")]
    files = []
    for d in scan_dirs:
        if d.is_dir():
            files.extend(sorted(d.rglob("*.php")))
    # Always include the entrypoint.
    artisan = target.path / "artisan"
    if artisan.exists():
        files.append(artisan)

    if not files:
        metrics["backend"] = "skipped: no php sources"
        return None

    ctx.log(f"  backend: php -l over {len(files)} file(s)")
    errors = 0
    checked = 0
    for f in files:
        res = ctx.run(f'php -l "{f}"', timeout=30)
        checked += 1
        if not res.ok:
            errors += 1
            msg = _first_php_error(res.combined) or f"syntax error (exit {res.code})"
            findings.append(ctx.finding(
                "critical", f"PHP syntax error: {msg}",
                file=_rel(f, ctx.root), line=_php_error_line(res.combined)))
    metrics["backend"] = {"files_checked": checked, "syntax_errors": errors}
    return errors == 0


def _error_lines(output: str, limit: int = 8):
    keep = []
    for raw in output.splitlines():
        line = raw.strip()
        low = line.lower()
        if not line:
            continue
        if ("error" in low or "error ts" in low or low.startswith("✘")
                or "failed to" in low or "cannot find" in low):
            keep.append(line[:200])
        if len(keep) >= limit:
            break
    return keep


def _first_php_error(output: str):
    for raw in output.splitlines():
        line = raw.strip()
        if line.lower().startswith("parse error") or line.lower().startswith("php parse error"):
            return line[:200]
    for raw in output.splitlines():
        if "error" in raw.lower():
            return raw.strip()[:200]
    return None


def _php_error_line(output: str):
    import re
    m = re.search(r"on line (\d+)", output)
    return int(m.group(1)) if m else None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def evaluate(ctx) -> dict:
    cfg = ctx.config
    findings: list[dict] = []
    metrics: dict = {}

    fe = _build_frontend(ctx, cfg, findings, metrics)
    be = _build_backend(ctx, cfg, findings, metrics)

    outcomes = [x for x in (fe, be) if x is not None]
    if not outcomes:
        return {
            "passed": None, "skipped": True,
            "skip_reason": "no buildable target (missing toolchains / dependencies)",
            "summary": "nothing to build", "findings": findings, "metrics": metrics,
        }

    passed = all(outcomes)
    score = ctx.score_from_findings(findings)
    parts = []
    if fe is not None:
        parts.append("frontend " + ("ok" if fe else "FAILED"))
    if be is not None:
        n = metrics.get("backend", {})
        detail = f" ({n.get('files_checked', '?')} files)" if isinstance(n, dict) else ""
        parts.append("backend " + ("ok" if be else "FAILED") + detail)
    return {
        "passed": passed, "score": score,
        "summary": "; ".join(parts), "findings": findings, "metrics": metrics,
    }
