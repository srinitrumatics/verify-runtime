import unittest
from verify_runtime import selftest


class TestSelftest(unittest.TestCase):
    def test_run_returns_suite(self):
        suite = selftest.run()
        self.assertTrue(suite.countTestCases() >= 1)

    def test_aggregate_includes_runtime(self):
        out = selftest.aggregate()
        self.assertIn("runtime", out)
        self.assertGreaterEqual(out["runtime"]["total"], 1)
        self.assertEqual(out["runtime"]["failed"], 0)
