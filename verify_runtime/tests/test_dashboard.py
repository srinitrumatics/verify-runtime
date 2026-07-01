import unittest

from verify_runtime import dashboard


def _sample_report() -> dict:
    return {
        "schema": "vdd/1",
        "generated_at": "2026-07-01T12:00:00+00:00",
        "project": "ai_dashboard",
        "targets": {
            "frontend": {"kind": "node", "language": "javascript", "present": True, "selected": True},
        },
        "composite_score": 92.5,
        "gate": {"passed": True, "min_score": 80, "reasons": [
            {"kind": "required", "level": "ok", "message": "required evaluator 'build' passed"},
        ]},
        "findings_by_severity": {"critical": 1, "high": 0, "medium": 2, "low": 0, "info": 0},
        "evaluators": [
            {
                "name": "build", "phase": "Build & Test", "module": "build",
                "weight": 12, "minimum": None, "passed": True, "score": 100.0,
                "skipped": False, "skip_reason": "", "summary": "frontend ok",
                "duration_s": 1.234, "error": None, "metrics": {},
                "findings": [],
            },
            {
                "name": "tests", "phase": "Build & Test", "module": "tests",
                "weight": 14, "minimum": 75, "passed": False, "score": 60.0,
                "skipped": False, "skip_reason": "", "summary": "2 tests failed",
                "duration_s": 3.5, "error": None, "metrics": {},
                "findings": [
                    {"severity": "critical", "message": "boom <script>alert(1)</script>",
                     "file": "src/app.ts", "line": 42},
                    {"severity": "medium", "message": "minor nit"},
                ],
            },
            {
                "name": "security", "phase": "Security", "module": "security",
                "weight": 10, "minimum": None, "passed": None, "score": 0.0,
                "skipped": True, "skip_reason": "no target", "summary": "skipped",
                "duration_s": 0.0, "error": None, "metrics": {},
                "findings": [],
            },
        ],
    }


class TestRenderHtml(unittest.TestCase):
    def test_returns_full_html_document(self):
        html = dashboard.render_html(_sample_report())
        self.assertIsInstance(html, str)
        self.assertTrue(html.startswith("<!"))
        self.assertIn("</html>", html)

    def test_contains_project_composite_and_gate(self):
        html = dashboard.render_html(_sample_report())
        self.assertIn("ai_dashboard", html)
        self.assertIn("92.5", html)
        self.assertIn("PASS", html)

    def test_contains_evaluator_names_and_finding_message(self):
        html = dashboard.render_html(_sample_report())
        self.assertIn("build", html)
        self.assertIn("tests", html)
        self.assertIn("security", html)
        self.assertIn("minor nit", html)

    def test_escapes_dangerous_finding_message(self):
        html = dashboard.render_html(_sample_report())
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_embeds_raw_report_json(self):
        html = dashboard.render_html(_sample_report())
        self.assertIn('id="verify-report"', html)
        self.assertIn('application/json', html)

    def test_trend_svg_present_with_series(self):
        html = dashboard.render_html(_sample_report(), trend=[90.0, 92.5])
        self.assertIn("<svg", html)

    def test_trend_section_omitted_without_series(self):
        html = dashboard.render_html(_sample_report(), trend=None)
        self.assertNotIn("<svg", html)

    def test_trend_section_omitted_with_single_point(self):
        html = dashboard.render_html(_sample_report(), trend=[92.5])
        self.assertNotIn("<svg", html)


if __name__ == "__main__":
    unittest.main()
