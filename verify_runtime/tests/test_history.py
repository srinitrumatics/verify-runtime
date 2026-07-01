import tempfile
import unittest
from pathlib import Path

from verify_runtime import history


def _report(composite=88.5, gate_passed=True):
    return {
        "schema": "vdd/1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "project": "t",
        "composite_score": composite,
        "gate": {"passed": gate_passed, "min_score": 80, "reasons": []},
        "findings_by_severity": {"critical": 0, "high": 1, "medium": 2, "low": 0, "info": 0},
        "evaluators": [
            {"name": "build", "score": composite, "skipped": False},
        ],
    }


class TestEnabled(unittest.TestCase):
    def test_enabled_via_flag(self):
        self.assertTrue(history.enabled({}, True))

    def test_enabled_via_config(self):
        self.assertTrue(history.enabled({"history": {"enabled": True}}, False))

    def test_disabled_by_default(self):
        self.assertFalse(history.enabled({}, False))


class TestRecordAndQuery(unittest.TestCase):
    def test_record_creates_db_and_recent_returns_rows_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rules: dict = {}

            db1 = history.record(root, rules, _report(composite=70.0))
            db2 = history.record(root, rules, _report(composite=90.0))

            self.assertEqual(db1, db2)
            self.assertTrue(db1.exists())

            rows = history.recent(root, rules, limit=20)
            self.assertEqual(len(rows), 2)
            # newest first
            self.assertEqual(rows[0]["composite"], 90.0)
            self.assertEqual(rows[1]["composite"], 70.0)
            for row in rows:
                self.assertIn("ts", row)
                self.assertIn("git_sha", row)
                self.assertIn("gate_passed", row)
                self.assertIn("findings_by_severity", row)
                self.assertIsInstance(row["findings_by_severity"], dict)

    def test_trend_returns_oldest_to_newest_composites(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rules: dict = {}
            history.record(root, rules, _report(composite=70.0))
            history.record(root, rules, _report(composite=90.0))

            series = history.trend(root, rules)
            self.assertEqual(series, [70.0, 90.0])

    def test_recent_on_missing_db_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rules: dict = {}
            self.assertEqual(history.recent(root, rules), [])
            self.assertEqual(history.trend(root, rules), [])

    def test_db_path_respects_config_override(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            custom = root / "custom" / "hist.db"
            rules = {"history": {"path": str(custom)}}
            p = history.db_path(root, rules)
            self.assertEqual(p, custom)


if __name__ == "__main__":
    unittest.main()
