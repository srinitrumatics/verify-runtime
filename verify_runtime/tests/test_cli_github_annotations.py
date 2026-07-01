import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from verify_runtime import cli, core


class TestCliGithubAnnotations(unittest.TestCase):
    def _write_config(self, root: Path) -> None:
        (root / "verification.yaml").write_text(
            "version: 2\n"
            "project: { name: t, root: . }\n"
            "targets: { app: { path: ., detect: null } }\n"
            "verification:\n"
            "  build: { module: build, weight: 1 }\n")

    def _fake_results(self):
        return [core.EvalResult(
            name="security", weight=1, passed=False, score=0.0,
            findings=[{"severity": "high", "message": "found a secret",
                       "file": "app/.env", "line": 3}],
        )]

    def test_github_flag_emits_annotations_to_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)

            cwd = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with mock.patch.object(cli, "run_evaluators", return_value=self._fake_results()):
                    with contextlib.redirect_stdout(buf):
                        rc = cli.main(["--github"])
            finally:
                os.chdir(cwd)

            out = buf.getvalue()
            self.assertIn(
                "::error file=app/.env,line=3::[security] found a secret", out)
            self.assertIsInstance(rc, int)

    def test_github_actions_env_emits_annotations_without_flag(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)

            cwd = os.getcwd()
            os.chdir(root)
            had_env = "GITHUB_ACTIONS" in os.environ
            old_value = os.environ.get("GITHUB_ACTIONS")
            os.environ["GITHUB_ACTIONS"] = "true"
            try:
                buf = io.StringIO()
                with mock.patch.object(cli, "run_evaluators", return_value=self._fake_results()):
                    with contextlib.redirect_stdout(buf):
                        cli.main([])
            finally:
                os.chdir(cwd)
                if had_env:
                    os.environ["GITHUB_ACTIONS"] = old_value
                else:
                    del os.environ["GITHUB_ACTIONS"]

            out = buf.getvalue()
            self.assertIn(
                "::error file=app/.env,line=3::[security] found a secret", out)

    def test_no_flag_and_no_env_emits_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._write_config(root)

            cwd = os.getcwd()
            os.chdir(root)
            had_env = "GITHUB_ACTIONS" in os.environ
            old_value = os.environ.pop("GITHUB_ACTIONS", None)
            try:
                buf = io.StringIO()
                with mock.patch.object(cli, "run_evaluators", return_value=self._fake_results()):
                    with contextlib.redirect_stdout(buf):
                        cli.main([])
            finally:
                os.chdir(cwd)
                if had_env:
                    os.environ["GITHUB_ACTIONS"] = old_value

            out = buf.getvalue()
            self.assertNotIn("::error", out)
            self.assertNotIn("::warning", out)
            self.assertNotIn("::notice", out)


if __name__ == "__main__":
    unittest.main()
