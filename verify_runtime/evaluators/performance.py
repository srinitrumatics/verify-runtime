"""performance evaluator — static budgets, no server required.

Checks (all pure filesystem inspection, so this evaluator is never skipped when
a target is present):

  * frontend bundle size — total JS shipped in dist/ (or build/) vs bundle_kb;
    per-chunk ceiling vs max_chunk_kb. Only evaluated when a build output
    already exists (the build evaluator produces it).
  * source-file length — files over max_file_loc are flagged as a
    maintainability / render-cost risk.

Budgets come from rules: evaluators.performance.budgets.
"""

from __future__ import annotations

from pathlib import Path

_DIST_DIRS = ("dist", "build")
_JS_SUFFIXES = (".js", ".mjs", ".cjs")
_SRC_EXTS = [".ts", ".tsx", ".js", ".jsx", ".php", ".vue"]


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _check_bundle(ctx, budgets, findings, metrics):
    target = ctx.target("frontend")
    if target is None:
        return
    dist = None
    for d in _DIST_DIRS:
        candidate = target.path / d
        if candidate.is_dir():
            dist = candidate
            break
    if dist is None:
        metrics["bundle"] = "skipped: no dist/ build present (run build first)"
        return

    chunks = []
    total = 0
    max_chunk_kb = budgets.get("max_chunk_kb", 300)
    for f in dist.rglob("*"):
        if f.is_file() and f.suffix.lower() in _JS_SUFFIXES:
            size = f.stat().st_size
            total += size
            kb = size / 1024
            chunks.append((_rel(f, ctx.root), round(kb, 1)))
            if kb > max_chunk_kb:
                findings.append(ctx.finding(
                    "medium", f"JS chunk {kb:.0f}KB exceeds per-chunk budget {max_chunk_kb}KB",
                    file=_rel(f, ctx.root)))
    total_kb = round(total / 1024, 1)
    budget_kb = budgets.get("bundle_kb", 600)
    metrics["bundle"] = {"total_kb": total_kb, "chunks": len(chunks),
                         "budget_kb": budget_kb}
    if total_kb > budget_kb:
        over = total_kb - budget_kb
        findings.append(ctx.finding(
            "medium", f"frontend JS bundle {total_kb:.0f}KB exceeds budget "
                      f"{budget_kb}KB (+{over:.0f}KB)", file="frontend/dist"))


def _check_file_lengths(ctx, budgets, findings, metrics):
    max_loc = budgets.get("max_file_loc", 400)
    largest = []
    flagged = 0
    total_files = 0
    for path in ctx.iter_source_files(_SRC_EXTS):
        try:
            loc = sum(1 for _ in path.open("r", encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        total_files += 1
        largest.append((_rel(path, ctx.root), loc))
        if loc > max_loc:
            flagged += 1
            findings.append(ctx.finding(
                "low", f"file has {loc} LOC (> {max_loc}) — split for "
                       f"maintainability / smaller render units",
                file=_rel(path, ctx.root), line=loc))
    largest.sort(key=lambda x: x[1], reverse=True)
    metrics["source"] = {
        "files": total_files, "over_budget": flagged, "max_loc": max_loc,
        "largest": [{"file": f, "loc": n} for f, n in largest[:5]],
    }


def evaluate(ctx) -> dict:
    cfg = ctx.config
    budgets = cfg.get("budgets") or {}
    findings: list[dict] = []
    metrics: dict = {}

    if not ctx.targets:
        return {"passed": None, "skipped": True,
                "skip_reason": "no targets present",
                "summary": "performance skipped", "findings": findings, "metrics": metrics}

    _check_bundle(ctx, budgets, findings, metrics)
    _check_file_lengths(ctx, budgets, findings, metrics)

    score = ctx.score_from_findings(findings)
    # Performance findings are advisory: only a bundle overspend (medium) drags
    # the pass, individual long files (low) do not fail the evaluator.
    passed = not any(f["severity"] in ("critical", "high", "medium") for f in findings)
    bundle = metrics.get("bundle")
    bundle_str = (f"bundle {bundle['total_kb']:.0f}KB" if isinstance(bundle, dict) else "no bundle")
    src = metrics.get("source") or {}
    summary = f"{bundle_str}; {src.get('over_budget', 0)} oversized source file(s)"
    return {"passed": passed, "score": score, "summary": summary,
            "findings": findings, "metrics": metrics}
