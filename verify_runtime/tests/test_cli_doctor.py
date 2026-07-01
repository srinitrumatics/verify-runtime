import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from verify_runtime import cli


class TestDoctor(unittest.TestCase):
    def test_doctor_healthy_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "verification.yaml").write_text(
                "version: 2\n"
                "project: { name: t, root: . }\n"
                "targets: { app: { path: ., detect: null } }\n"
                "verification:\n"
                "  build: { module: build, weight: 1 }\n")
            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["doctor"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("Runtime", out)
            self.assertIn("Python", out)
            from verify_runtime import __version__
            self.assertIn(__version__, out)
            self.assertIn("builtin", out)

    def test_doctor_reports_unresolved_stage(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "verification.yaml").write_text(
                "version: 2\n"
                "project: { name: t, root: . }\n"
                "targets: { app: { path: ., detect: null } }\n"
                "verification:\n"
                "  bogus: { module: nonexistent_xyz, weight: 1 }\n")
            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["doctor"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 1)
            self.assertIn("unresolved", out)

    def test_doctor_handles_missing_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["doctor"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("no verification.yaml", out)


if __name__ == "__main__":
    unittest.main()
