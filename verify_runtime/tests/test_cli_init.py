import json, subprocess, sys, tempfile, unittest
from pathlib import Path
from verify_runtime import cli


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
