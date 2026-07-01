"""meta — self-verification stage.

Runs every registered self-test suite (verify.selftests entry points): the
runtime engine tests AND any installed plugin's tests. A failing suite is a
CRITICAL finding (blocks the gate); zero tests discovered is CRITICAL too, so
the verifier can never silently pass without proving itself first.
"""
from __future__ import annotations


def evaluate(ctx) -> dict:
    from verify_runtime.selftest import aggregate

    suites = aggregate()  # {name: {total, failed, passed, failures[]}}
    if not suites:
        return {"passed": None, "skipped": True,
                "skip_reason": "no self-test suites registered (verify.selftests)",
                "summary": "self-tests skipped", "findings": [], "metrics": {}}

    total = sum(s["total"] for s in suites.values())
    failed = sum(s["failed"] for s in suites.values())
    findings: list[dict] = []

    if total == 0:
        findings.append(ctx.finding("critical", "no self-tests discovered — cannot trust this run"))
    if failed:
        findings.append(ctx.finding(
            "critical", f"verifier self-tests FAILING — do not trust this run ({failed} problem(s))"))
        for name, s in suites.items():
            for f in s["failures"][:10]:
                findings.append(ctx.finding("high", f"failing self-test [{name}]: {f}"))

    passed = failed == 0 and total > 0
    summary = (f"{total} self-tests passed across {len(suites)} suite(s)"
               if passed else f"self-tests FAILED ({failed} problem(s))")
    metrics = {"suites": suites, "total": total, "failed": failed}
    return {"passed": passed, "score": 100.0 if passed else 0.0,
            "summary": summary, "findings": findings, "metrics": metrics}
