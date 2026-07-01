import textwrap, unittest
from pathlib import Path
import tempfile
from verify_runtime import resolver


class TestResolver(unittest.TestCase):
    def test_builtin_resolves(self):
        mod = resolver.load_evaluator("build", rules={}, root=Path("."))
        self.assertTrue(hasattr(mod, "evaluate"))

    def test_local_overrides_builtin(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            plug = root / "plugins"
            plug.mkdir()
            (plug / "build.py").write_text(
                "MARKER='local'\ndef evaluate(ctx):\n    return {'passed': True, 'score': 1}\n")
            rules = {"plugin_paths": ["plugins"]}
            mod = resolver.load_evaluator("build", rules=rules, root=root)
            self.assertEqual(getattr(mod, "MARKER", None), "local")

    def test_unknown_raises(self):
        with self.assertRaises(FileNotFoundError):
            resolver.load_evaluator("does_not_exist", rules={}, root=Path("."))

    def test_source_label(self):
        self.assertEqual(resolver.resolve_source("build", {}, Path("."))[0], "builtin")
