import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from verify_runtime import cli


class TestList(unittest.TestCase):
    def test_list_shows_resolution_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text("{}")
            (root / "verification.yaml").write_text(
                "version: 2\n"
                "project: { name: t, root: . }\n"
                "targets: { app: { path: ., detect: package.json } }\n"
                "verification:\n"
                "  build: { module: build, weight: 1 }\n"
                "  bogus: { module: nonexistent_xyz, weight: 1 }\n")
            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["--list"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("source", out)         # header advertises the column
            self.assertIn("builtin", out)        # build resolves to a builtin
            self.assertIn("unresolved", out)     # a missing module degrades gracefully


class TestInit(unittest.TestCase):
    def test_init_creates_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "package.json").write_text("{}")
            rc = cli.main(["init", "--root", str(root)])
            self.assertEqual(rc, 0)
            cfg = root / "verification.yaml"
            self.assertTrue(cfg.exists())
            self.assertIn("verification:", cfg.read_text())
            self.assertTrue((root / "verification" / "plugins").is_dir())

    def test_init_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "verification.yaml").write_text("version: 2\n")
            rc = cli.main(["init", "--root", str(root)])
            self.assertEqual(rc, 2)
