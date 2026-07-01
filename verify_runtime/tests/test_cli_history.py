import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from verify_runtime import cli, history


class TestCliHistory(unittest.TestCase):
    def _write_config(self, root: Path) -> None:
        (root / "verification.yaml").write_text(
            "version: 2\n"
            "project: { name: t, root: . }\n"
            "targets: { app: { path: ., detect: null } }\n"
            "verification:\n"
            "  build: { module: build, weight: 1 }\n")

    def test_history_no_db_prints_friendly_note(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)
            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["history"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("no history recorded yet", out)

    def test_history_with_rows_shows_composite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)
            report = {
                "composite_score": 91.25,
                "gate": {"passed": True},
                "findings_by_severity": {"critical": 0, "high": 0, "medium": 1, "low": 0, "info": 0},
                "evaluators": [{"name": "build", "score": 91.25, "skipped": False}],
            }
            history.record(root, {}, report)

            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["history"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("91.25", out)
            self.assertIn("PASS", out)


if __name__ == "__main__":
    unittest.main()
