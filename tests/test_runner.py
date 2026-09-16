"""End-to-end evidence checks using real Git commits and unittest assertions."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from examples.demo import git, make_demo_repository
from regression_witness.runner import run_witness


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="regression-witness-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def witness(self, demo, *, output_name="evidence", **overrides):
        options = {
            "repo": demo.repo,
            "base": demo.base,
            "head": demo.head,
            "overlay": [demo.test_file],
            "tests": [demo.test_id],
            "command": [sys.executable, "run_tests.py", "--junit", "{junit}"],
            "output": self.root / output_name,
        }
        options.update(overrides)
        return run_witness(**options)

    def test_real_failure_then_pass_with_repeat_and_saved_evidence(self):
        demo = make_demo_repository(self.root / "repo")
        result = self.witness(demo, repeats=2)
        self.assertEqual(result["verdict"], "FAIL_TO_PASS_OBSERVED")
        evidence = self.root / "evidence"
        persisted = json.loads((evidence / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["verdict"], result["verdict"])
        self.assertIn("FAIL_TO_PASS_OBSERVED", (evidence / "report.md").read_text(encoding="utf-8"))
        # Inspect actual subprocess reports, not just the summarized verdict.
        reports = list((evidence / "runs").glob("*/**/*.xml"))
        self.assertGreaterEqual(len(reports), 4)
        failures = sum(len(ET.parse(path).getroot().findall(".//failure")) for path in reports)
        self.assertEqual(failures, 2)

    def test_test_that_already_passed_is_not_a_reproduction(self):
        demo = make_demo_repository(self.root / "repo", scenario="allgreen")
        self.assertEqual(self.witness(demo)["verdict"], "NOT_REPRODUCED")

    def test_unfixed_assertion_is_still_failing(self):
        demo = make_demo_repository(self.root / "repo", scenario="still_failing")
        self.assertEqual(self.witness(demo)["verdict"], "STILL_FAILING")

    def test_collection_error_cannot_be_evidence_of_reproduction(self):
        demo = make_demo_repository(self.root / "repo", scenario="collection_error")
        self.assertEqual(self.witness(demo)["verdict"], "INCONCLUSIVE")

    def test_dependency_error_in_selected_test_cannot_be_a_failure_witness(self):
        demo = make_demo_repository(self.root / "repo", scenario="test_error")
        self.assertEqual(self.witness(demo)["verdict"], "INCONCLUSIVE")

    def test_skipped_test_cannot_be_evidence_of_pass(self):
        demo = make_demo_repository(self.root / "repo", scenario="skipped")
        self.assertEqual(self.witness(demo)["verdict"], "INCONCLUSIVE")

    def test_dirty_source_is_ignored_and_preserved_without_switching_branch(self):
        demo = make_demo_repository(self.root / "repo")
        dirty_content = "def normalize(value):\n    return 'DIRTY WORKTREE MUST NOT RUN'\n"
        (demo.repo / "normalize.py").write_text(dirty_content, encoding="utf-8")
        (demo.repo / "untracked.txt").write_text("keep this file\n", encoding="utf-8")
        # Stage a different existing file as well, to guard the source index.
        (demo.repo / "README.txt").write_text("staged user changes\n", encoding="utf-8")
        git(demo.repo, "add", "README.txt")
        before_status = git(demo.repo, "status", "--porcelain")
        before_diff = git(demo.repo, "diff", "--cached")
        before_branch = git(demo.repo, "symbolic-ref", "HEAD")
        before_head = git(demo.repo, "rev-parse", "HEAD")
        self.assertEqual(self.witness(demo)["verdict"], "FAIL_TO_PASS_OBSERVED")
        self.assertEqual(git(demo.repo, "status", "--porcelain"), before_status)
        self.assertEqual(git(demo.repo, "diff", "--cached"), before_diff)
        self.assertEqual(git(demo.repo, "symbolic-ref", "HEAD"), before_branch)
        self.assertEqual(git(demo.repo, "rev-parse", "HEAD"), before_head)
        self.assertEqual((demo.repo / "normalize.py").read_text(encoding="utf-8"), dirty_content)
        self.assertEqual((demo.repo / "untracked.txt").read_text(encoding="utf-8"), "keep this file\n")

    def test_invalid_overlay_paths_are_rejected_before_execution(self):
        demo = make_demo_repository(self.root / "repo")
        paths = ["../outside.py", str(demo.repo / demo.test_file), "not-tracked.py", "."]
        for index, path in enumerate(paths):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.witness(demo, output_name="invalid-%d" % index, overlay=[path])

    def test_timeout_is_inconclusive(self):
        demo = make_demo_repository(self.root / "repo")
        result = self.witness(
            demo,
            command=[sys.executable, "-c", "import time; time.sleep(5)", "{junit}"],
            timeout=0.1,
        )
        self.assertEqual(result["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
