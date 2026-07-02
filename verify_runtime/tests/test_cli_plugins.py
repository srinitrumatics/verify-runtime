import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from verify_runtime import cli


class TestPlugins(unittest.TestCase):
    def test_plugins_lists_builtin_and_installed(self):
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
                    rc = cli.main(["plugins"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("build", out)
            self.assertIn("tests", out)
            # speckit is an optional plugin — the base package's CI installs no
            # plugins, so only assert its stages when it's actually installed.
            import importlib.metadata as im
            try:
                im.version("verify-plugin-speckit")
                speckit_installed = True
            except im.PackageNotFoundError:
                speckit_installed = False
            if speckit_installed:
                self.assertIn("spec", out)
                self.assertIn("verify-plugin-speckit", out)

    def test_plugins_no_config(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["plugins"])
            finally:
                os.chdir(cwd)
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("build", out)


if __name__ == "__main__":
    unittest.main()
