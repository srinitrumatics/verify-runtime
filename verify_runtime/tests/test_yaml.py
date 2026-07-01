"""Unit tests for the bundled minimal YAML loader."""

from __future__ import annotations

import unittest

from verify_runtime.yaml import _mini_yaml


class TestMiniYaml(unittest.TestCase):
    def test_nested_maps_lists_scalars(self):
        text = """
# a comment
a: 1
b:
  c: two
  d: [x, y]
list:
  - name: foo
    v: 1
  - name: bar
    v: 2
flag: true
nothing: null
s: "hello: world"
neg: -3
pi: 3.14
"""
        d = _mini_yaml(text)
        self.assertEqual(d["a"], 1)
        self.assertEqual(d["b"], {"c": "two", "d": ["x", "y"]})
        self.assertEqual(d["list"], [{"name": "foo", "v": 1}, {"name": "bar", "v": 2}])
        self.assertIs(d["flag"], True)
        self.assertIsNone(d["nothing"])
        self.assertEqual(d["s"], "hello: world")   # colon inside quotes preserved
        self.assertEqual(d["neg"], -3)
        self.assertAlmostEqual(d["pi"], 3.14)

    def test_comment_stripping_respects_quotes(self):
        # A '#' inside quotes is not a comment.
        d = _mini_yaml('key: "a # b"\nother: c  # trailing\n')
        self.assertEqual(d["key"], "a # b")
        self.assertEqual(d["other"], "c")

    def test_value_with_ampersand_and_special_chars(self):
        d = _mini_yaml("phase: Build & Test\n")
        self.assertEqual(d["phase"], "Build & Test")


if __name__ == "__main__":
    unittest.main()
