"""Verification-Driven Development (VDD) engine: discovery, scoring, gate,
reporting, and remediation. Stack-agnostic; evaluators are resolved by name
via verify_runtime.resolver (local plugin_paths -> entry point -> built-in).
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

from verify_runtime.resolver import load_evaluator
from verify_runtime.yaml import load_yaml  # noqa: F401  (re-exported for callers)

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

IGNORE_DIRS = {
    "node_modules", "vendor", "dist", "build", ".git", ".svn", "storage",
    "coverage", "graphify-out", ".ruff_cache", "__pycache__", ".next",
    ".vite", ".turbo", ".cache", "bootstrap",
}


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
class Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def bold(self, s): return self._wrap("1", s)
    def dim(self, s): return self._wrap("2", s)
    def red(self, s): return self._wrap("31", s)
    def green(self, s): return self._wrap("32", s)
    def yellow(self, s): return self._wrap("33", s)
    def blue(self, s): return self._wrap("34", s)
    def magenta(self, s): return self._wrap("35", s)
    def cyan(self, s): return self._wrap("36", s)


SEVERITY_COLOR = {
    "critical": "red", "high": "red", "medium": "yellow",
    "low": "cyan", "info": "dim",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class RunResult:
    code: int
    out: str
    err: str
    duration: float
    timed_out: bool
    launched: bool  # False when the executable could not be found

    @property
    def ok(self) -> bool:
        return self.launched and not self.timed_out and self.code == 0

    @property
    def combined(self) -> str:
        return (self.out or "") + (("\n" + self.err) if self.err else "")


@dataclass
class Target:
    name: str
    path: Path
    kind: str
    language: str
    present: bool


@dataclass
class EvalResult:
    name: str
    weight: float
    passed: Optional[bool] = None      # None = skipped / inconclusive
    score: float = 0.0
    summary: str = ""
    findings: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""
    duration: float = 0.0
    error: Optional[str] = None
    phase: str = "Other"
    minimum: Optional[float] = None
    module: str = ""

    @property
    def counted(self) -> bool:
        """Whether this evaluator contributes to the composite score."""
        return self.passed is not None and not self.skipped and self.error is None



# ---------------------------------------------------------------------------
# Evaluator context (the only surface evaluators depend on)
# ---------------------------------------------------------------------------
class Context:
    def __init__(self, rules: dict, config: dict, root: Path,
                 targets: dict[str, Target], selected_targets: list[str],
                 log: Callable[[str], None]):
        self.rules = rules
        self.config = config or {}
        self.root = root
        self._targets = targets
        self._selected = selected_targets
        self._log = log
        self.env = os.environ

    # -- targets -----------------------------------------------------------
    @property
    def targets(self) -> list[Target]:
        return [t for name, t in self._targets.items()
                if t.present and name in self._selected]

    def target(self, name: str) -> Optional[Target]:
        t = self._targets.get(name)
        return t if (t and t.present and name in self._selected) else None

    # -- process execution -------------------------------------------------
    def which(self, name: str) -> Optional[str]:
        from shutil import which
        return which(name)

    def run(self, command: str, cwd: Optional[Path] = None,
            timeout: Optional[float] = None) -> RunResult:
        start = time.time()
        try:
            proc = subprocess.run(
                command, cwd=str(cwd) if cwd else None, shell=True,
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "",
                             time.time() - start, False, True)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            return RunResult(124, out, err, time.time() - start, True, True)
        except FileNotFoundError:
            return RunResult(127, "", "executable not found", time.time() - start, False, False)

    # -- source discovery --------------------------------------------------
    def iter_source_files(self, exts: Iterable[str],
                          targets: Optional[list[Target]] = None) -> Iterator[Path]:
        exts = {e if e.startswith(".") else "." + e for e in exts}
        for t in (targets or self.targets):
            for dirpath, dirnames, filenames in os.walk(t.path):
                dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
                for fn in filenames:
                    if Path(fn).suffix.lower() in exts:
                        yield Path(dirpath) / fn

    # -- helpers -----------------------------------------------------------
    def severity_weight(self, severity: str) -> int:
        return int((self.rules.get("severity_weights") or {}).get(severity, 0))

    def finding(self, severity: str, message: str, **extra) -> dict:
        severity = severity if severity in SEVERITY_ORDER else "info"
        f = {"severity": severity, "message": message}
        f.update({k: v for k, v in extra.items() if v is not None})
        return f

    def score_from_findings(self, findings: list[dict], base: float = 100.0) -> float:
        score = base
        for f in findings:
            score -= self.severity_weight(f.get("severity", "info"))
        return max(0.0, min(100.0, score))

    def log(self, msg: str) -> None:
        self._log(msg)

    def read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""



# ---------------------------------------------------------------------------
# Discovery + orchestration
# ---------------------------------------------------------------------------
def discover_targets(rules: dict, root: Path) -> dict[str, Target]:
    out: dict[str, Target] = {}
    for name, spec in (rules.get("targets") or {}).items():
        spec = spec or {}
        path = (root / spec.get("path", name)).resolve()
        detect = spec.get("detect")
        present = path.is_dir() and (detect is None or (path / detect).exists())
        out[name] = Target(name=name, path=path, kind=spec.get("kind", "unknown"),
                           language=spec.get("language", "unknown"), present=present)
    return out


def stages_config(rules: dict) -> dict:
    """The ordered stage map. Prefer the v2 `verification:` key, fall back to
    the v1 `evaluators:` key for backwards compatibility."""
    return rules.get("verification") or rules.get("evaluators") or {}




def normalise_result(name: str, weight: float, raw: Any) -> EvalResult:
    raw = raw or {}
    findings = list(raw.get("findings") or [])
    passed = raw.get("passed", None)
    skipped = bool(raw.get("skipped", False))
    if "score" in raw and raw["score"] is not None:
        score = float(raw["score"])
    elif skipped or passed is None:
        score = 0.0
    else:
        score = 100.0 if passed else 0.0
    return EvalResult(
        name=name, weight=float(weight), passed=passed, score=score,
        summary=raw.get("summary", ""), findings=findings,
        metrics=dict(raw.get("metrics") or {}),
        skipped=skipped, skip_reason=raw.get("skip_reason", ""),
    )


def run_evaluators(rules: dict, root: Path, targets: dict[str, Target],
                   selected_targets: list[str], only: Optional[set[str]],
                   skip: Optional[set[str]], fail_fast: bool,
                   log: Callable[[str], None]) -> list[EvalResult]:
    results: list[EvalResult] = []
    for name, cfg in stages_config(rules).items():
        cfg = cfg or {}
        if not cfg.get("enabled", True):
            continue
        if only and name not in only:
            continue
        if skip and name in skip:
            continue
        weight = cfg.get("weight", 1)
        module_name = cfg.get("module", name)
        phase = cfg.get("phase", "Other")
        minimum = cfg.get("minimum")
        ctx = Context(rules, cfg, root, targets, selected_targets, log)
        start = time.time()
        log(f"→ {name}")
        try:
            module = load_evaluator(module_name, rules=rules, root=root)
            raw = module.evaluate(ctx)
            res = normalise_result(name, weight, raw)
        except Exception:
            res = EvalResult(name=name, weight=float(weight), passed=None,
                             error=traceback.format_exc(limit=4))
            res.summary = "evaluator crashed"
        res.duration = time.time() - start
        res.phase = phase
        res.module = module_name
        res.minimum = float(minimum) if minimum is not None else None
        results.append(res)
        if fail_fast and res.passed is False:
            log(f"  fail-fast: stopping after '{name}'")
            break
    return results


# ---------------------------------------------------------------------------
# Scoring + gate
# ---------------------------------------------------------------------------
def compute_composite(results: list[EvalResult]) -> float:
    counted = [r for r in results if r.counted and r.weight > 0]
    total_w = sum(r.weight for r in counted)
    if total_w == 0:
        return 0.0
    return sum(r.weight * r.score for r in counted) / total_w


def collect_findings(results: list[EvalResult]) -> list[tuple[str, dict]]:
    out = []
    for r in results:
        for f in r.findings:
            out.append((r.name, f))
    return out


def evaluate_gate(rules: dict, results: list[EvalResult], composite: float) -> dict:
    gate = rules.get("gate") or {}
    min_score = float(gate.get("min_score", 0))
    block = set(gate.get("block_on_severity") or [])
    required = list(gate.get("required_pass") or [])
    by_name = {r.name: r for r in results}

    reasons: list[dict] = []
    passed = True

    if composite < min_score:
        passed = False
        reasons.append({"kind": "min_score", "level": "fail",
                        "message": f"composite {composite:.1f} < required {min_score:g}"})
    else:
        reasons.append({"kind": "min_score", "level": "ok",
                        "message": f"composite {composite:.1f} >= {min_score:g}"})

    blocking = [(n, f) for n, f in collect_findings(results) if f.get("severity") in block]
    if blocking:
        passed = False
        reasons.append({"kind": "severity", "level": "fail",
                        "message": f"{len(blocking)} blocking finding(s) at {sorted(block)}"})

    # Per-stage minimum thresholds.
    for r in results:
        if r.minimum is None:
            continue
        if not r.counted:
            reasons.append({"kind": "minimum", "level": "warn",
                            "message": f"'{r.name}' inconclusive — cannot check minimum {r.minimum:g}"})
        elif r.score < r.minimum:
            passed = False
            reasons.append({"kind": "minimum", "level": "fail",
                            "message": f"'{r.name}' score {r.score:.1f} < minimum {r.minimum:g}"})

    for name in required:
        r = by_name.get(name)
        if r is None:
            reasons.append({"kind": "required", "level": "warn",
                            "message": f"required evaluator '{name}' did not run"})
        elif r.passed is False:
            passed = False
            reasons.append({"kind": "required", "level": "fail",
                            "message": f"required evaluator '{name}' failed"})
        elif r.passed is None or r.skipped:
            reasons.append({"kind": "required", "level": "warn",
                            "message": f"required evaluator '{name}' inconclusive (skipped)"})
        else:
            reasons.append({"kind": "required", "level": "ok",
                            "message": f"required evaluator '{name}' passed"})

    return {"passed": passed, "min_score": min_score, "reasons": reasons}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def severity_totals(results: list[EvalResult]) -> dict[str, int]:
    totals = {s: 0 for s in SEVERITY_ORDER}
    for _, f in collect_findings(results):
        sev = f.get("severity", "info")
        totals[sev] = totals.get(sev, 0) + 1
    return totals


def status_badge(p: Palette, res: EvalResult) -> str:
    if res.error:
        return p.red("ERROR")
    if res.skipped or res.passed is None:
        return p.yellow("SKIP")
    return p.green("PASS") if res.passed else p.red("FAIL")


def render_human(p: Palette, project: str, targets: dict[str, Target],
                 selected: list[str], results: list[EvalResult],
                 composite: float, gate: dict, quiet: bool) -> str:
    buf = io.StringIO()
    w = buf.write
    bar = "─" * 66

    w("\n" + p.bold(p.cyan("Verification-Driven Development")) + p.dim(f"  ·  {project}") + "\n")
    w(p.dim(bar) + "\n")

    present = [f"{t.name} ({t.kind})" for n, t in targets.items() if t.present and n in selected]
    absent = [n for n, t in targets.items() if not t.present]
    w("targets  : " + (", ".join(present) if present else p.yellow("none detected")) + "\n")
    if absent:
        w(p.dim("absent   : " + ", ".join(absent)) + "\n")
    w(p.dim(bar) + "\n\n")

    # Group evaluators by phase, preserving first-seen order.
    phase_order: list[str] = []
    for r in results:
        if r.phase not in phase_order:
            phase_order.append(r.phase)

    for phase in phase_order:
        w(p.bold(p.blue(f"▸ {phase}")) + "\n")
        for r in [r for r in results if r.phase == phase]:
            _render_evaluator(w, p, r, quiet)
        w("\n")
    return _render_footer(buf, w, p, results, composite, gate)


def _render_evaluator(w, p, r, quiet):
    badge = status_badge(p, r)
    score = "  n/a " if not r.counted else f"{r.score:5.1f}"
    minim = f" · min {r.minimum:g}" if r.minimum is not None else ""
    head = f"  {badge:<5}  {p.bold(r.name):<16}  score {score}  " \
           f"{p.dim(f'w{r.weight:g}{minim} · {r.duration:.1f}s')}"
    w(head + "\n")
    if r.summary:
        w("         " + p.dim(r.summary) + "\n")
    if r.skip_reason and (r.skipped or r.passed is None):
        w("         " + p.yellow("skipped: " + r.skip_reason) + "\n")
    if r.error:
        first = r.error.strip().splitlines()[-1]
        w("         " + p.red("error: " + first) + "\n")
    if not quiet:
        for f in r.findings[:20]:
            sev = f.get("severity", "info")
            col = getattr(p, SEVERITY_COLOR.get(sev, "dim"))
            loc = f.get("file")
            if loc and f.get("line"):
                loc = f"{loc}:{f['line']}"
            tag = col(f"[{sev}]")
            w(f"           {tag} {f.get('message','')}")
            if loc:
                w(p.dim(f"  ({loc})"))
            w("\n")
        if len(r.findings) > 20:
            w(p.dim(f"           … {len(r.findings) - 20} more finding(s)\n"))


def _render_footer(buf, w, p, results, composite, gate) -> str:
    bar = "─" * 66
    totals = severity_totals(results)
    w(p.dim(bar) + "\n")
    tline = "  ".join(
        (getattr(p, SEVERITY_COLOR.get(s, "dim"))(f"{s}:{totals[s]}"))
        for s in SEVERITY_ORDER
    )
    w("findings : " + tline + "\n")

    score_str = f"{composite:5.1f} / 100"
    score_col = p.green if composite >= gate["min_score"] else p.red
    w("composite: " + score_col(p.bold(score_str)) + p.dim(f"  (gate min {gate['min_score']:g})") + "\n")

    w(p.dim(bar) + "\n")
    verdict = p.green(p.bold(" GATE: PASS ")) if gate["passed"] else p.red(p.bold(" GATE: FAIL "))
    w("verdict  : " + verdict + "\n")
    for reason in gate["reasons"]:
        icon = {"ok": p.green("✓"), "warn": p.yellow("!"), "fail": p.red("✗")}.get(reason["level"], "·")
        w(f"           {icon} {reason['message']}\n")
    w("\n")
    return buf.getvalue()


def build_json_report(project: str, targets: dict[str, Target], selected: list[str],
                      results: list[EvalResult], composite: float, gate: dict) -> dict:
    return {
        "schema": "vdd/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "targets": {
            n: {"kind": t.kind, "language": t.language, "present": t.present,
                "selected": n in selected}
            for n, t in targets.items()
        },
        "composite_score": round(composite, 2),
        "gate": gate,
        "findings_by_severity": severity_totals(results),
        "evaluators": [
            {
                "name": r.name, "phase": r.phase, "module": r.module,
                "weight": r.weight, "minimum": r.minimum, "passed": r.passed,
                "score": round(r.score, 2), "skipped": r.skipped,
                "skip_reason": r.skip_reason, "summary": r.summary,
                "duration_s": round(r.duration, 3), "error": r.error,
                "metrics": r.metrics, "findings": r.findings,
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def run_remediation(rules: dict, root: Path, targets: dict[str, Target],
                    selected: list[str], results: list[EvalResult],
                    ai: bool, apply: bool, log: Callable[[str], None]) -> dict:
    """Deterministic (and optionally AI-gated) auto-fixes.

    Deterministic fixers come from an evaluator module exposing
    ``remediate(ctx, findings, ai) -> {actions, changed}``. AI patches are
    generated (and, only with ``apply``, written) via _ai.
    """
    stages = stages_config(rules)
    actions: list[dict] = []
    changed = False
    by_name = {r.name: r for r in results}

    for name, cfg in stages.items():
        cfg = cfg or {}
        # Only remediate stages that actually ran this pass — `results` already
        # reflects --only / --skip / enabled, so honour that selection here too.
        if name not in by_name:
            continue
        if not cfg.get("enabled", True):
            continue
        module_name = cfg.get("module", name)
        try:
            module = load_evaluator(module_name, rules=rules, root=root)
        except Exception:
            continue
        if not hasattr(module, "remediate"):
            continue
        r = by_name.get(name)
        findings = r.findings if r else []
        ctx = Context(rules, cfg, root, targets, selected, log)
        log(f"⚙ remediate: {name}")
        try:
            out = module.remediate(ctx, findings, ai) or {}
        except Exception as e:
            out = {"actions": [{"title": f"{name} remediation failed",
                                "ran": True, "ok": False, "detail": str(e)}], "changed": False}
        for a in out.get("actions", []):
            a["stage"] = name
            actions.append(a)
        changed = changed or bool(out.get("changed"))

    ai_result = None
    if ai:
        ai_result = _ai_remediate(root, results, apply, log)
        actions.extend(ai_result.get("actions", []))
        changed = changed or bool(ai_result.get("changed"))

    return {"actions": actions, "changed": changed, "ai": ai_result}


def _ai_remediate(root: Path, results: list[EvalResult], apply: bool,
                  log: Callable[[str], None]) -> dict:
    """Generate Claude patch suggestions for code findings. Previews a unified
    diff; only writes when ``apply`` is set. No-ops gracefully without a key."""
    try:
        import _ai  # type: ignore
    except Exception:
        return {"actions": [], "changed": False}

    class _Env:
        env = os.environ

        def log(self, m):
            log(m)

    if not _ai.available(_Env()):
        return {"actions": [{"title": "AI remediation unavailable",
                             "ran": False, "ok": False,
                             "detail": "ANTHROPIC_API_KEY not set — skipped"}], "changed": False}

    # Collect actionable findings (need a file to patch).
    targets_f = []
    for r in results:
        for f in r.findings:
            if f.get("file") and f.get("severity") in ("critical", "high", "medium"):
                targets_f.append((r.name, f))
    targets_f = targets_f[:5]  # bound cost/blast radius
    if not targets_f:
        return {"actions": [{"title": "no auto-fixable findings", "ran": False, "ok": True,
                             "detail": ""}], "changed": False}

    actions, changed = [], False
    schema = {
        "type": "object",
        "properties": {"explanation": {"type": "string"},
                       "new_content": {"type": "string"}},
        "required": ["explanation", "new_content"],
        "additionalProperties": False,
    }
    for stage, f in targets_f:
        rel = f["file"]
        path = root / rel
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8", errors="ignore")
        system = ("You are a precise code-fixing agent. Given a file and one issue, return the "
                  "COMPLETE corrected file content and a one-line explanation. Make the minimal "
                  "change that resolves the issue; do not reformat unrelated code.")
        user = f"ISSUE ({f['severity']}): {f['message']}\nFILE: {rel}\n\n{original[:16000]}"
        parsed, err = _ai.call(_Env(), system, user, schema=schema, timeout=120)
        if parsed is None:
            actions.append({"stage": stage, "title": f"AI patch for {rel}",
                            "ran": True, "ok": False, "detail": err})
            continue
        new_content = parsed.get("new_content", "")
        diff = _unified_diff(original, new_content, rel)
        log("\n".join(diff.splitlines()[:40]))  # preview
        if apply and new_content and new_content != original:
            path.write_text(new_content, encoding="utf-8")
            changed = True
            actions.append({"stage": stage, "title": f"applied AI patch to {rel}",
                            "ran": True, "ok": True, "detail": parsed.get("explanation", "")})
        else:
            actions.append({"stage": stage, "title": f"AI patch previewed for {rel}",
                            "ran": True, "ok": True,
                            "detail": (parsed.get("explanation", "") + " (use --apply to write)")})
    return {"actions": actions, "changed": changed}


def _unified_diff(a: str, b: str, path: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))


def render_remediation(p: Palette, remediation: dict) -> str:
    if not remediation or not remediation.get("actions"):
        return ""
    buf = io.StringIO()
    buf.write(p.bold(p.magenta("▸ Remediation")) + "\n")
    for a in remediation["actions"]:
        icon = p.green("✓") if a.get("ok") else (p.dim("·") if not a.get("ran") else p.red("✗"))
        stage = a.get("stage", "")
        buf.write(f"  {icon} {p.dim(stage)} {a.get('title','')}")
        if a.get("detail"):
            buf.write(p.dim(f" — {a['detail'][:100]}"))
        buf.write("\n")
    buf.write("\n")
    return buf.getvalue()


def _force_utf8() -> None:
    """Windows consoles default to cp1252 and choke on the report glyphs."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

