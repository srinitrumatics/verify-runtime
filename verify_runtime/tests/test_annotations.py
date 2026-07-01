"""Unit tests for GitHub Actions workflow-command annotation rendering."""

from __future__ import annotations

import unittest

from verify_runtime import annotations


def _report(evaluators):
    return {"evaluators": evaluators}


def _ev(name, findings):
    return {"name": name, "findings": findings}


class TestRenderGithubAnnotations(unittest.TestCase):
    def test_high_finding_with_file_and_line(self):
        report = _report([_ev("security", [
            {"severity": "high", "message": "found a secret", "file": "app/.env", "line": 3},
        ])])
        lines = annotations.render_github_annotations(report)
        self.assertEqual(lines, [
            "::error file=app/.env,line=3::[security] found a secret",
        ])

    def test_medium_finding_with_file_no_line(self):
        report = _report([_ev("company_rules", [
            {"severity": "medium", "message": "missing LICENSE", "file": "LICENSE"},
        ])])
        lines = annotations.render_github_annotations(report)
        self.assertEqual(lines, [
            "::warning file=LICENSE::[company_rules] missing LICENSE",
        ])

    def test_info_finding_with_no_file(self):
        report = _report([_ev("meta", [
            {"severity": "info", "message": "no significant issues"},
        ])])
        lines = annotations.render_github_annotations(report)
        self.assertEqual(lines, [
            "::notice::[meta] no significant issues",
        ])

    def test_message_escaping(self):
        report = _report([_ev("security", [
            {"severity": "high", "message": "100% bad :: has\nnewline"},
        ])])
        line = annotations.render_github_annotations(report)[0]
        self.assertIn("%25", line)
        self.assertIn("%0A", line)
        self.assertNotIn("\n", line)

    def test_file_property_escaping(self):
        report = _report([_ev("security", [
            {"severity": "high", "message": "boom", "file": "C:\\a,b:c", "line": 1},
        ])])
        line = annotations.render_github_annotations(report)[0]
        self.assertIn("%2C", line)
        self.assertIn("%3A", line)

    def test_cap_truncates_and_appends_notice(self):
        findings = [
            {"severity": "low", "message": f"issue {i}"} for i in range(60)
        ]
        report = _report([_ev("lint", findings)])
        lines = annotations.render_github_annotations(report, cap=50)
        self.assertEqual(len(lines), 51)
        self.assertTrue(lines[-1].startswith("::notice::"))
        self.assertIn("10 more finding(s) not annotated (showing first 50)", lines[-1])

    def test_severity_ordering(self):
        report = _report([_ev("mixed", [
            {"severity": "info", "message": "info-msg"},
            {"severity": "critical", "message": "crit-msg"},
            {"severity": "low", "message": "low-msg"},
            {"severity": "medium", "message": "medium-msg"},
            {"severity": "high", "message": "high-msg"},
        ])])
        lines = annotations.render_github_annotations(report)
        levels = [line.split(" ", 1)[0].split("::")[1] for line in lines]
        # errors (critical/high) before warnings (medium/low) before notices (info)
        error_idx = [i for i, lvl in enumerate(levels) if lvl == "error"]
        warning_idx = [i for i, lvl in enumerate(levels) if lvl == "warning"]
        notice_idx = [i for i, lvl in enumerate(levels) if lvl == "notice"]
        self.assertTrue(max(error_idx) < min(warning_idx) < max(warning_idx) < min(notice_idx))


class TestShouldEmit(unittest.TestCase):
    def test_flag_true(self):
        self.assertTrue(annotations.should_emit({}, True))

    def test_env_true(self):
        self.assertTrue(annotations.should_emit({"GITHUB_ACTIONS": "true"}, False))

    def test_neither(self):
        self.assertFalse(annotations.should_emit({}, False))

    def test_env_other_value_false(self):
        self.assertFalse(annotations.should_emit({"GITHUB_ACTIONS": "false"}, False))


if __name__ == "__main__":
    unittest.main()
