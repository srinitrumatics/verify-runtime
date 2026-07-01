"""security evaluator — static secret/danger scan + dependency audit.

Runs with no toolchain required (the pattern scan always works), and adds a
dependency audit when `npm` / `composer` is available. Findings:

  * hardcoded secrets (API keys, tokens, passwords, private keys)
  * dangerous sinks (eval, dangerouslySetInnerHTML, `dd()` leftovers, etc.)
  * committed environment files (real-world secret leak vector)
  * vulnerable dependencies (npm audit / composer audit) at/above the
    configured minimum severity

Because this evaluator always has work to do, it is (almost) never skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SOURCE_EXTS = [".ts", ".tsx", ".js", ".jsx", ".php", ".vue", ".env"]

# --- secret patterns --------------------------------------------------------
# Each: (name, compiled regex, severity). Kept in code (not YAML) so the
# regexes never fight the config parser.
_SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), "critical"),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "critical"),
    ("aws-secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"), "critical"),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "high"),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}"), "high"),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}"), "high"),
    ("bearer-jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "high"),
    ("generic-secret", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
        r"\s*[:=]\s*['\"][A-Za-z0-9_\-./+=]{16,}['\"]"), "high"),
]

# --- dangerous sinks --------------------------------------------------------
_DANGER_PATTERNS = [
    ("js", re.compile(r"\bdangerouslySetInnerHTML\b"), "medium",
     "React dangerouslySetInnerHTML — XSS risk if content is untrusted"),
    ("js", re.compile(r"\beval\s*\("), "medium", "use of eval()"),
    ("js", re.compile(r"\bnew Function\s*\("), "medium", "dynamic new Function()"),
    ("js", re.compile(r"\.innerHTML\s*="), "low", "direct innerHTML assignment"),
    ("php", re.compile(r"\b(?:eval|assert)\s*\("), "high", "PHP eval()/assert() on runtime input"),
    ("php", re.compile(r"\b(?:exec|shell_exec|system|passthru|popen|proc_open)\s*\("), "medium",
     "shell execution sink"),
    ("php", re.compile(r"\b(?:dd|dump|var_dump|ray)\s*\("), "low", "debug helper left in source"),
    ("php", re.compile(r"\bunserialize\s*\("), "medium", "unserialize() — object injection risk"),
]

_JS_EXTS = {".ts", ".tsx", ".js", ".jsx", ".vue"}
_PHP_EXTS = {".php"}

# Directories/files where secrets in fixtures/examples are expected.
_ALLOW_SUBSTR = (".env.example", ".env.sample", "example", "fixture", "stub", ".test.", ".spec.")


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _is_allowlisted(rel: str) -> bool:
    low = rel.lower()
    return any(s in low for s in _ALLOW_SUBSTR)


def _scan_sources(ctx, findings, metrics):
    scanned = 0
    secret_hits = 0
    danger_hits = 0
    for path in ctx.iter_source_files(SOURCE_EXTS):
        rel = _rel(path, ctx.root)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        ext = path.suffix.lower()
        lines = text.splitlines()

        for name, rx, sev in _SECRET_PATTERNS:
            for m in rx.finditer(text):
                if _is_allowlisted(rel) and name == "generic-secret":
                    continue
                line_no = text.count("\n", 0, m.start()) + 1
                secret_hits += 1
                findings.append(ctx.finding(
                    sev, f"possible {name} committed to source",
                    file=rel, line=line_no))
                break  # one per pattern per file is enough signal

        for scope, rx, sev, label in _DANGER_PATTERNS:
            if scope == "js" and ext not in _JS_EXTS:
                continue
            if scope == "php" and ext not in _PHP_EXTS:
                continue
            for i, line in enumerate(lines, start=1):
                if rx.search(line):
                    danger_hits += 1
                    findings.append(ctx.finding(sev, label, file=rel, line=i))
                    break
    metrics["files_scanned"] = scanned
    metrics["secret_hits"] = secret_hits
    metrics["danger_hits"] = danger_hits


def _flag_env_files(ctx, findings, metrics):
    flagged = []
    for t in ctx.targets:
        for candidate in (".env", ".env.local", ".env.production"):
            p = t.path / candidate
            if p.exists():
                rel = _rel(p, ctx.root)
                flagged.append(rel)
                findings.append(ctx.finding(
                    "medium", "environment file present in project tree — "
                              "ensure it is git-ignored and holds no real secrets",
                    file=rel))
    metrics["env_files"] = flagged


def _audit_frontend(ctx, cfg, findings, metrics):
    target = ctx.target("frontend")
    if target is None or ctx.which("npm") is None:
        return
    if not (target.path / "node_modules").exists():
        metrics["npm_audit"] = "skipped: node_modules missing"
        return
    ctx.log("  npm audit --json")
    res = ctx.run("npm audit --json", cwd=target.path, timeout=cfg.get("timeout", 300))
    data = _safe_json(res.combined)
    if data is None:
        metrics["npm_audit"] = "unparseable"
        return
    counts = ((data.get("metadata") or {}).get("vulnerabilities")) or {}
    metrics["npm_audit"] = counts
    _emit_audit_findings(ctx, cfg, findings, counts, "npm")


def _audit_backend(ctx, cfg, findings, metrics):
    target = ctx.target("backend")
    if target is None or ctx.which("composer") is None:
        return
    ctx.log("  composer audit --format=json")
    res = ctx.run("composer audit --format=json --no-interaction",
                  cwd=target.path, timeout=cfg.get("timeout", 300))
    data = _safe_json(res.combined)
    if data is None:
        metrics["composer_audit"] = "no advisories or unparseable"
        return
    advisories = data.get("advisories") or {}
    total = sum(len(v) for v in advisories.values()) if isinstance(advisories, dict) else 0
    metrics["composer_audit"] = {"packages_affected": len(advisories), "advisories": total}
    if total:
        findings.append(ctx.finding(
            "high", f"composer audit: {total} advisory(ies) across "
                    f"{len(advisories)} package(s)", file="backend"))


_SEV_RANK = {"info": 0, "low": 1, "moderate": 2, "medium": 2, "high": 3, "critical": 4}


def _emit_audit_findings(ctx, cfg, findings, counts, tool):
    floor = _SEV_RANK.get(str(cfg.get("audit_min_severity", "high")).lower(), 3)
    for sev_name, n in counts.items():
        if not isinstance(n, int) or n <= 0:
            continue
        rank = _SEV_RANK.get(sev_name.lower())
        if rank is None or rank < floor:
            continue
        mapped = {"moderate": "medium"}.get(sev_name.lower(), sev_name.lower())
        if mapped not in ("critical", "high", "medium", "low", "info"):
            mapped = "medium"
        findings.append(ctx.finding(
            mapped, f"{tool} audit: {n} {sev_name} vulnerability(ies) in dependencies",
            file="frontend"))


def _safe_json(text: str):
    text = text.strip()
    start = text.find("{")
    if start == -1:
        return None
    try:
        return json.loads(text[start:])
    except (json.JSONDecodeError, ValueError):
        return None


def evaluate(ctx) -> dict:
    cfg = ctx.config
    findings: list[dict] = []
    metrics: dict = {}

    if not ctx.targets:
        return {"passed": None, "skipped": True,
                "skip_reason": "no targets to scan",
                "summary": "security skipped", "findings": findings, "metrics": metrics}

    _scan_sources(ctx, findings, metrics)
    if cfg.get("flag_env_files", True):
        _flag_env_files(ctx, findings, metrics)
    if cfg.get("dependency_audit", True):
        _audit_frontend(ctx, cfg, findings, metrics)
        _audit_backend(ctx, cfg, findings, metrics)

    score = ctx.score_from_findings(findings)
    # Pass unless a critical/high issue is present.
    has_blocking = any(f["severity"] in ("critical", "high") for f in findings)
    passed = not has_blocking
    summary = (f"{metrics.get('files_scanned', 0)} files scanned, "
               f"{len(findings)} finding(s)")
    return {"passed": passed, "score": score, "summary": summary,
            "findings": findings, "metrics": metrics}
