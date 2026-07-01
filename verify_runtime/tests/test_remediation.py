"""Regression test: --fix --ai must actually invoke the AI client.

Guards against the packaging bug where `_ai_remediate` imported a top-level
`_ai` module that no longer exists, so the surrounding `except Exception`
silently swallowed the ImportError and the AI remediation path dead-no-op'd.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from verify_runtime import ai as ai_module
from verify_runtime import core


class TestAiRemediate(unittest.TestCase):
    def setUp(self):
        self._orig_available = ai_module.available
        self._orig_call = ai_module.call
        self.calls: list = []

    def tearDown(self):
        ai_module.available = self._orig_available
        ai_module.call = self._orig_call

    def test_ai_remediate_invokes_client_and_applies_patch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "src" / "bad.js"
            target.parent.mkdir(parents=True)
            target.write_text("const total = subtotal;\n", encoding="utf-8")

            # Stub the AI client with a canned "available + patch" pair.
            ai_module.available = lambda env: True

            def fake_call(env, system, user, schema=None, timeout=None):
                self.calls.append((system, user))
                return ({"explanation": "add tax", "new_content": "const total = subtotal + tax;\n"}, None)

            ai_module.call = fake_call

            r = core.EvalResult(name="lint", weight=1.0, passed=False, score=0.0)
            r.findings = [{"severity": "high", "message": "missing tax", "file": "src/bad.js"}]

            out = core._ai_remediate(root, [r], apply=True, log=lambda m: None)

            # The client was actually invoked (no silent no-op).
            self.assertEqual(len(self.calls), 1)
            self.assertTrue(out["changed"])
            self.assertTrue(any(a.get("ok") and "applied" in a["title"] for a in out["actions"]))
            # And the patch was written to disk.
            self.assertEqual(target.read_text(encoding="utf-8"), "const total = subtotal + tax;\n")

    def test_ai_remediate_unavailable_reports_skip(self):
        ai_module.available = lambda env: False
        with tempfile.TemporaryDirectory() as d:
            out = core._ai_remediate(Path(d), [], apply=False, log=lambda m: None)
        self.assertFalse(out["changed"])
        self.assertTrue(any("unavailable" in a["title"] for a in out["actions"]))


if __name__ == "__main__":
    unittest.main()
