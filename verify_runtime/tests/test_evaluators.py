"""Behavioral tests for the generic (stack-agnostic) evaluators.

Spec-Kit-specific evaluator tests (spec/plan/architecture/tasks/graph/coverage
and the meta self-check that used to parse subprocess output) moved to the
verify-plugin-speckit package or were retired — see the migration notes.
This module keeps the fast unit tests for the code-quality output parsers
(no npm/php required).
"""

from __future__ import annotations

import unittest

from verify_runtime.resolver import load_evaluator


class TestCodeOutputParsers(unittest.TestCase):
    """The code-quality evaluators parse tool output — verify without toolchains."""

    def test_vitest_and_pest_counts(self):
        parse = load_evaluator("tests")._parse_counts
        self.assertEqual(parse("Tests  2 failed | 10 passed (12)"), (10, 2, 12))
        self.assertEqual(parse("Tests  12 passed (12)"), (12, 0, 12))
        self.assertEqual(parse("Tests:  34 passed (89 assertions)"), (34, 0, 34))
        self.assertEqual(parse("Tests:  2 failed, 32 passed"), (32, 2, 34))
        self.assertEqual(parse("OK (34 tests, 89 assertions)"), (34, 0, 34))
        # Pest 3 colorizes the summary label itself; parsing must ignore ANSI.
        self.assertEqual(
            parse("\x1b[90mTests:\x1b[39m  \x1b[32;1m108 passed\x1b[39;22m"
                  "\x1b[90m (280 assertions)\x1b[39m"),
            (108, 0, 108))

    def test_eslint_count(self):
        count = load_evaluator("lint")._count_eslint
        self.assertEqual(count("✖ 3 problems (1 errors, 2 warnings)"), (1, 2))

    def test_php_error_line(self):
        line = load_evaluator("build")._php_error_line
        self.assertEqual(line("PHP Parse error: syntax error on line 42"), 42)

    def test_security_secret_patterns(self):
        sec = load_evaluator("security")
        secret = 'api_key = "ABCD1234ABCD1234ABCD1234"'
        self.assertTrue(any(rx.search(secret) for _, rx, _ in sec._SECRET_PATTERNS))
        self.assertFalse(any(rx.search("const total = subtotal + tax")
                             for _, rx, _ in sec._SECRET_PATTERNS))


if __name__ == "__main__":
    unittest.main()
