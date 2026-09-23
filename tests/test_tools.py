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


if __name__ == "__main__":
    unittest.main()
