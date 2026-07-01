"""Unit tests for composite scoring and the release gate."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Optional

from verify_runtime import core

RULES = {
    "gate": {"min_score": 80, "block_on_severity": ["critical"], "required_pass": ["build"]},
    "severity_weights": {"critical": 40, "high": 20, "medium": 8, "low": 2, "info": 0},
}


def _res(name, weight, score, passed: Optional[bool] = True, **kw):
    return core.EvalResult(name=name, weight=weight, score=score, passed=passed, **kw)


class TestComposite(unittest.TestCase):
    def test_weighted_mean(self):
        results = [_res("a", 10, 90), _res("b", 30, 100)]
        self.assertAlmostEqual(core.compute_composite(results), 97.5)

    def test_skipped_excluded(self):
        # A skipped stage must NOT drag the composite to 0 — it is excluded.
        results = [_res("a", 10, 90), _res("skip", 90, 0, passed=None, skipped=True)]
        self.assertAlmostEqual(core.compute_composite(results), 90.0)

    def test_no_counted_is_zero(self):
        results = [_res("skip", 10, 0, passed=None, skipped=True)]
        self.assertEqual(core.compute_composite(results), 0.0)


class TestScoreFromFindings(unittest.TestCase):
    def setUp(self):
        self.ctx = core.Context(RULES, {}, Path("."), {}, [], lambda m: None)

    def test_penalties(self):
        findings = [self.ctx.finding("medium", "x"), self.ctx.finding("low", "y")]
        self.assertEqual(self.ctx.score_from_findings(findings), 90.0)  # 100-8-2

    def test_floor_at_zero(self):
        findings = [self.ctx.finding("critical", "a")] * 5
        self.assertEqual(self.ctx.score_from_findings(findings), 0.0)


class TestGate(unittest.TestCase):
    def _gate(self, results, composite):
        return core.evaluate_gate(RULES, results, composite)

    def test_all_pass(self):
        results = [_res("build", 10, 100)]
        g = self._gate(results, 95.0)
        self.assertTrue(g["passed"])

    def test_below_min_score_fails(self):
        results = [_res("build", 10, 100)]
        g = self._gate(results, 70.0)
        self.assertFalse(g["passed"])
        self.assertTrue(any(r["kind"] == "min_score" and r["level"] == "fail" for r in g["reasons"]))

    def test_blocking_severity_fails(self):
        r = _res("build", 10, 100)
        r.findings = [{"severity": "critical", "message": "boom"}]
        g = self._gate([r], 95.0)
        self.assertFalse(g["passed"])
        self.assertTrue(any(r_["kind"] == "severity" for r_ in g["reasons"]))

    def test_stage_minimum_fails(self):
        r = _res("graph", 10, 84)
        r.minimum = 90.0
        # build present so required_pass is satisfied; graph below its minimum.
        g = self._gate([_res("build", 10, 100), r], 95.0)
        self.assertFalse(g["passed"])
        self.assertTrue(any(x["kind"] == "minimum" and x["level"] == "fail" for x in g["reasons"]))

    def test_required_pass_failure(self):
        g = self._gate([_res("build", 10, 100, passed=False)], 95.0)
        self.assertFalse(g["passed"])

    def test_required_skipped_only_warns(self):
        # A skipped required stage warns but does not by itself fail the gate.
        skipped_build = _res("build", 10, 0, passed=None, skipped=True)
        g = self._gate([skipped_build, _res("other", 10, 100)], 95.0)
        self.assertTrue(any(x["kind"] == "required" and x["level"] == "warn" for x in g["reasons"]))


if __name__ == "__main__":
    unittest.main()
