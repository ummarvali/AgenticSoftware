"""Tests for the tools layer: artifact sandboxing and code compilation."""

import tempfile
import unittest
from pathlib import Path

from agentic_sdlc.models import Artifact
from agentic_sdlc.tools import ArtifactStore, CodeRunner


class ArtifactStoreTests(unittest.TestCase):
    def test_writes_nested_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp)
            path = store.write(Artifact("pkg/mod.py", "x = 1", "code"))
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text(), "x = 1")

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(tmp)
            with self.assertRaises(ValueError):
                store.write(Artifact("../escape.py", "boom", "code"))


class CodeRunnerTests(unittest.TestCase):
    def test_compile_detects_syntax_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.py"
            bad = Path(tmp) / "bad.py"
            good.write_text("y = 2\n")
            bad.write_text("def broken(:\n")
            results = CodeRunner().compile_python([good, bad])
            by_ok = {r.ok for r in results}
            self.assertIn(True, by_ok)
            self.assertIn(False, by_ok)

    def test_run_unittests_no_tests_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = CodeRunner().run_unittests(Path(tmp))
            self.assertTrue(result.ok)



class StaticSafetyScanTests(unittest.TestCase):
    """The AST guardrail catches what a code reviewer would refuse on sight."""

    def _scan(self, source):
        import tempfile
        from pathlib import Path
        from agentic_sdlc.tools.static_check import scan_tree
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app").mkdir()
            (Path(tmp) / "app" / "__init__.py").write_text("")
            (Path(tmp) / "app" / "m.py").write_text(source)
            return scan_tree(Path(tmp))

    def test_clean_stdlib_code_has_no_high_findings(self):
        f = self._scan("import json\nfrom app import x\nfrom . import y\n\ndef ok():\n    return json.dumps({})\n")
        self.assertFalse([x for x in f if x.severity == "high"], f)

    def test_dangerous_calls_are_high(self):
        f = self._scan("import os, subprocess\n\ndef bad(c):\n    eval(c)\n    os.system(c)\n    subprocess.run(c, shell=True)\n")
        rules = sorted({(x.rule, x.detail) for x in f if x.severity == "high"})
        self.assertIn(("dangerous-call", "eval()"), rules)
        self.assertIn(("dangerous-call", "os.system()"), rules)
        self.assertIn(("dangerous-call", "subprocess.run(shell=True)"), rules)

    def test_non_stdlib_import_is_high(self):
        import sys
        if not hasattr(sys, "stdlib_module_names"):
            self.skipTest("needs Python 3.10+")
        f = self._scan("import requests\nimport json\n")
        self.assertTrue(any(x.rule == "non-stdlib-import" and "requests" in x.detail for x in f), f)
        self.assertFalse(any("json" in x.detail for x in f))

    def test_import_aliases_and_shell_true_are_resolved(self):
        f = self._scan("from os import system\nimport subprocess as sp\n"
                       "system('x')\nsp.Popen('x', shell=True)\n")
        details = {x.detail for x in f if x.rule == "dangerous-call"}
        self.assertIn("os.system()", details)
        self.assertIn("subprocess.Popen(shell=True)", details)

    def test_stdlib_shadowing_module_is_high(self):
        import sys
        import tempfile
        from pathlib import Path
        from agentic_sdlc.tools.static_check import scan_tree
        if sys.version_info < (3, 10):
            self.skipTest("needs Python 3.10+")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "unittest.py").write_text("import sys\nsys.exit(0)\n")
            Path(tmp, "pkg").mkdir()
            Path(tmp, "pkg", "json.py").write_text("x = 1\n")      # inside a package: fine
            f = scan_tree(Path(tmp))
        self.assertEqual([x.path for x in f if x.rule == "stdlib-shadowing"], ["unittest.py"])

    def test_hardcoded_secret_is_high(self):
        # Deliberately FAKE, key-shaped string (not a real credential): it exists only to
        # prove the scanner flags hard-coded secrets in generated code.
        f = self._scan('KEY = "sk-ant-api03-abcdefghijklmnop"\n')
        self.assertTrue(any(x.rule == "hardcoded-secret" for x in f), f)


if __name__ == "__main__":
    unittest.main()
