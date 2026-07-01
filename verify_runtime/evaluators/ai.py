"""ai — Multi-reviewer AI code review (consensus panel).

Runs several specialised reviewers over a curated file sample and combines them
into a consensus score, in the spirit of an evals harness:

    security · architecture · performance · testing · refactoring · correctness

With ANTHROPIC_API_KEY set, each enabled reviewer is a Claude call
(claude-opus-4-8, structured output) with a lens-specific system prompt; scores
are averaged into a consensus. Offline, each reviewer runs as a deterministic
heuristic lens so the panel still produces a real signal.

Config (rules: verification.ai_review):
  reviewers        list of lenses to run (default: all)
  multi_call       when true + key present, one Claude call per lens; else a
                   single combined call (cheaper). Default false.
  max_files, max_bytes_per_file
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

from verify_runtime import ai as _ai

_REVIEW_EXTS = [".ts", ".tsx", ".php"]
_ALL_LENSES = ["security", "architecture", "performance", "testing", "refactoring", "correctness"]

_PRIORITY_HINTS = ("controller", "service", "checkout", "order", "payment", "auth",
                   "cart", "inventory", "statemachine", "gateway", "policy", "store", "api")

_LENS_SYSTEM = {
    "security": "You are an application security reviewer. Find auth bypass, injection, "
                "missing authorization, secret exposure, and unsafe input handling.",
    "architecture": "You are a software architect. Find layering violations, tight coupling, "
                    "business logic leaking into controllers/UI, and missing abstractions.",
    "performance": "You are a performance engineer. Find N+1 queries, unbounded loops, missing "
                   "pagination, redundant work, and blocking calls.",
    "testing": "You are a test engineer. Find untested branches, missing edge-case handling, and "
               "code that is hard to test.",
    "refactoring": "You are a senior engineer focused on maintainability. Find duplication, dead "
                   "code, overly long functions, and unclear naming.",
    "correctness": "You are a correctness reviewer. Find real bugs: wrong conditions, off-by-one, "
                   "unhandled errors, and broken invariants (server-authoritative pricing, the "
                   "order state machine, inventory reservation).",
}


def _rel(path, root):
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _collect_files(ctx, max_files, max_bytes):
    cands = []
    for path in ctx.iter_source_files(_REVIEW_EXTS):
        rel = _rel(path, ctx.root)
        low = rel.lower()
        if any(x in low for x in (".test.", ".spec.", ".d.ts")):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        prio = sum(1 for h in _PRIORITY_HINTS if h in low)
        cands.append((prio, -size, rel, path))
    cands.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for _, _, rel, path in cands[:max_files]:
        try:
            out.append((rel, path.read_text(encoding="utf-8", errors="ignore")[:max_bytes]))
        except OSError:
            continue
    return out


# --- heuristic lenses -------------------------------------------------------
def _lens_heuristic(ctx, lens, files):
    findings = []
    for rel, text in files:
        ext = Path(rel).suffix.lower()
        for sev, msg, rx in _HEURISTIC_RULES.get(lens, []):
            if rx.get("ext") and ext not in rx["ext"]:
                continue
            m = rx["re"].search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                findings.append(ctx.finding(sev, f"{lens}: {msg}", file=rel, line=line))
    return ctx.score_from_findings(findings), findings


_HEURISTIC_RULES = {
    "security": [
        ("medium", "non-HTTPS URL in source", {"re": re.compile(r"http://(?!localhost|127\.0\.0\.1)")}),
        ("medium", "permissive CORS (Allow-Origin: *)", {"re": re.compile(r"Allow-Origin['\"]?\s*[:=]\s*['\"]\*")}),
        ("low", "TODO/FIXME near security-sensitive code",
         {"re": re.compile(r"(?i)(TODO|FIXME).{0,40}(auth|token|password|secret)")}),
    ],
    "correctness": [
        ("medium", "empty catch block swallows errors", {"re": re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}")}),
        ("low", "loose equality (== / !=) in TS", {"re": re.compile(r"[^=!<>]==[^=]|[^!]!=[^=]"),
                                                    "ext": {".ts", ".tsx"}}),
        ("low", "debug statement left in source",
         {"re": re.compile(r"\b(console\.log|debugger|dd\(|var_dump\()")}),
    ],
    "performance": [
        ("low", "SELECT * / unbounded query", {"re": re.compile(r"(?i)select\s+\*|->all\(\)")}),
        ("low", "possible query inside a loop",
         {"re": re.compile(r"(?is)(for|foreach|while)[^{]{0,80}\{[^}]{0,200}->(get|first|find)\(")}),
    ],
    "refactoring": [
        ("low", "loose `any` type weakens type safety",
         {"re": re.compile(r":\s*any\b|<any>|as any\b"), "ext": {".ts", ".tsx"}}),
        ("info", "unresolved TODO/FIXME/HACK", {"re": re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")}),
    ],
    "architecture": [
        ("low", "raw DB query in a controller (should live in a service/model)",
         {"re": re.compile(r"(?i)class\s+\w*Controller[\s\S]{0,4000}?DB::"), "ext": {".php"}}),
    ],
    "testing": [
        ("info", "complex conditional with no nearby assertion",
         {"re": re.compile(r"if\s*\(.*&&.*\|\|.*\)")}),
    ],
}


# --- Claude lenses ----------------------------------------------------------
def _lens_ai(ctx, cfg, lens, files):
    system = _LENS_SYSTEM[lens] + (" Return an integer score 0-100 and concrete findings.")
    body = "".join(f"\n===== {rel} =====\n{txt}\n" for rel, txt in files)
    parsed, err = _ai.call(ctx, system, "Review these files:\n" + body,
                           schema=_ai.REVIEW_SCHEMA, timeout=cfg.get("timeout", 120))
    if parsed is None:
        return None, [], err
    findings = []
    for f in parsed.get("findings") or []:
        sev = str(f.get("severity", "info")).lower()
        findings.append(ctx.finding(sev if sev in
                        ("critical", "high", "medium", "low", "info") else "info",
                        f"{lens}: " + str(f.get("message", "")).strip(), file=f.get("file")))
    score = parsed.get("score")
    return (float(score) if isinstance(score, (int, float)) else ctx.score_from_findings(findings)), findings, None


def evaluate(ctx) -> dict:
    cfg = ctx.config
    if not ctx.targets:
        return {"passed": None, "skipped": True, "skip_reason": "no source targets",
                "summary": "ai review skipped", "findings": [], "metrics": {}}

    files = _collect_files(ctx, cfg.get("max_files", 12), cfg.get("max_bytes_per_file", 16000))
    if not files:
        return {"passed": None, "skipped": True, "skip_reason": "no reviewable files",
                "summary": "ai review skipped", "findings": [], "metrics": {}}

    lenses = [x for x in (cfg.get("reviewers") or _ALL_LENSES) if x in _ALL_LENSES]
    use_ai = _ai.available(ctx)
    multi_call = use_ai and bool(cfg.get("multi_call", False))

    findings: list[dict] = []
    lens_scores: dict[str, float] = {}
    provider = "heuristic"

    if use_ai and not multi_call:
        # Single combined Claude call plays every lens at once.
        provider = "anthropic"
        combined_system = ("You are a panel of reviewers covering: "
                           + ", ".join(lenses) + ". " + _LENS_SYSTEM["correctness"]
                           + " Return an integer score 0-100 and findings tagged by concern.")
        body = "".join(f"\n===== {rel} =====\n{txt}\n" for rel, txt in files)
        parsed, err = _ai.call(ctx, combined_system, "Review:\n" + body,
                               schema=_ai.REVIEW_SCHEMA, timeout=cfg.get("timeout", 120))
        if parsed is None:
            ctx.log(f"  ai_review: falling back to heuristics ({err})")
            use_ai = False
        else:
            for f in parsed.get("findings") or []:
                sev = str(f.get("severity", "info")).lower()
                findings.append(ctx.finding(sev if sev in
                                ("critical", "high", "medium", "low", "info") else "info",
                                str(f.get("message", "")).strip(), file=f.get("file")))
            sc = parsed.get("score")
            lens_scores["panel"] = float(sc) if isinstance(sc, (int, float)) else ctx.score_from_findings(findings)

    if not use_ai or multi_call:
        for lens in lenses:
            if multi_call:
                score, fnd, err = _lens_ai(ctx, cfg, lens, files)
                if score is None:
                    ctx.log(f"  ai_review[{lens}]: heuristic fallback ({err})")
                    score, fnd = _lens_heuristic(ctx, lens, files)
                else:
                    provider = "anthropic"
            else:
                score, fnd = _lens_heuristic(ctx, lens, files)
            lens_scores[lens] = round(score, 1)
            findings.extend(fnd)

    consensus = round(statistics.mean(lens_scores.values()), 1) if lens_scores else 100.0
    passed = not any(f["severity"] in ("critical", "high") for f in findings)
    return {
        "passed": passed, "score": consensus,
        "summary": f"{provider} consensus {consensus:.0f}/100 across {len(lens_scores)} reviewer(s), "
                   f"{len(findings)} finding(s)",
        "findings": findings,
        "metrics": {"provider": provider, "files_reviewed": len(files),
                    "lens_scores": lens_scores},
    }
