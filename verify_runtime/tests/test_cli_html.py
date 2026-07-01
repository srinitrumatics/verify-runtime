import os
import tempfile
import unittest
from pathlib import Path

from verify_runtime import cli


class TestCliHtml(unittest.TestCase):
    def _write_config(self, root: Path) -> None:
        (root / "verification.yaml").write_text(
            "version: 2\n"
            "project: { name: t, root: . }\n"
            "targets: { app: { path: ., detect: null } }\n"
            "verification:\n"
            "  build: { module: build, weight: 1 }\n")

    def test_html_flag_writes_report_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)
            out_path = root / "report.html"

            cwd = os.getcwd()
            os.chdir(root)
            try:
                rc = cli.main(["--html", str(out_path)])
            finally:
                os.chdir(cwd)

            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            html = out_path.read_text(encoding="utf-8")
            self.assertTrue(html.startswith("<!"))
            self.assertIn("</html>", html)
            self.assertIn('id="verify-report"', html)

    def test_html_flag_default_path(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)

            cwd = os.getcwd()
            os.chdir(root)
            try:
                rc = cli.main(["--html"])
            finally:
                os.chdir(cwd)

            self.assertEqual(rc, 0)
            self.assertTrue((root / "verify-report.html").exists())

    def test_no_html_flag_does_not_write_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)

            cwd = os.getcwd()
            os.chdir(root)
            try:
                rc = cli.main([])
            finally:
                os.chdir(cwd)

            self.assertEqual(rc, 0)
            self.assertFalse((root / "verify-report.html").exists())


if __name__ == "__main__":
    unittest.main()
