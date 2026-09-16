"""Behavior regressions found by independent review of witness boundaries."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from examples.demo import git, make_demo_repository
from regression_witness.__main__ import main
from regression_witness.evidence import EvidenceError
from regression_witness.runner import run_witness


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="witness-review-regression-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def witness(self, demo, output, **overrides):
        options = {
            "repo": demo.repo,
            "base": demo.base,
            "head": demo.head,
            "overlay": [demo.test_file],
            "tests": [demo.test_id],
            "command": [sys.executable, "run_tests.py", "--junit", "{junit}"],
            "output": output,
        }
        options.update(overrides)
        return run_witness(**options)

    def test_archive_attributes_cannot_create_a_failure_to_pass_transition(self):
        # In either revision's checkout the fixture exists with identical bytes.
        # Only packaging attributes change. A git archive would omit/substitute
        # the base fixture, falsely making its otherwise passing test fail.
        for attribute in ("export-ignore", "export-subst"):
            with self.subTest(attribute=attribute):
                demo = make_demo_repository(self.root / attribute, scenario="allgreen")
                fixture = "$Format:%h$\n"
                (demo.repo / "marker.txt").write_text(fixture, encoding="utf-8")
                (demo.repo / ".gitattributes").write_text(
                    "marker.txt " + attribute + "\n", encoding="utf-8",
                )
                (demo.repo / demo.test_file).write_text(
                    "import unittest\nfrom pathlib import Path\n\n"
                    "class NormalizeTests(unittest.TestCase):\n"
                    "    def test_casefold(self):\n"
                    "        fixture = Path('marker.txt')\n"
                    "        self.assertTrue(fixture.is_file())\n"
                    "        self.assertEqual(fixture.read_text(), '$Format:%h$\\n')\n",
                    encoding="utf-8",
                )
                git(demo.repo, "add", "marker.txt", ".gitattributes", demo.test_file)
                git(demo.repo, "commit", "-m", "Synthetic passing checkout with archive attributes")
                base = git(demo.repo, "rev-parse", "HEAD")
                (demo.repo / ".gitattributes").write_text("", encoding="utf-8")
                git(demo.repo, "add", ".gitattributes")
                git(demo.repo, "commit", "-m", "Change packaging attributes only")
                head = git(demo.repo, "rev-parse", "HEAD")

                result = self.witness(
                    demo, self.root / (attribute + "-evidence"), base=base, head=head,
                )

                self.assertEqual(result["verdict"], "NOT_REPRODUCED")
                self.assertEqual([run["exit_code"] for run in result["runs"]], [0, 0])
                self.assertEqual(result["transitions"], [{
                    "test": demo.test_id, "before": "passed", "after": "passed",
                }])

    def test_output_symlink_alias_cannot_write_into_git_metadata(self):
        demo = make_demo_repository(self.root / "repo")
        alias = self.root / "metadata-alias"
        try:
            alias.symlink_to(demo.repo / ".git", target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Directory symlinks unavailable: %s" % exc)

        with self.assertRaises(EvidenceError):
            self.witness(demo, alias / "unexpected-evidence")

        self.assertFalse((demo.repo / ".git" / "unexpected-evidence").exists())

    def test_compare_discloses_report_only_mode_even_with_supplied_exit_codes(self):
        before = self.root / "before.xml"
        after = self.root / "after.xml"
        before.write_text(
            '<testsuite><testcase classname="sample" name="test">'
            '<failure message="synthetic"/></testcase></testsuite>',
            encoding="utf-8",
        )
        after.write_text(
            '<testsuite><testcase classname="sample" name="test"/></testsuite>',
            encoding="utf-8",
        )
        output = self.root / "comparison"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = main([
                "compare", "--before", str(before), "--after", str(after),
                "--before-exit", "1", "--after-exit", "0",
                "--test", "sample::test", "--output", str(output),
            ])

        self.assertEqual(status, 0)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        markdown = (output / "report.md").read_text(encoding="utf-8")
        self.assertEqual(report["mode"], "report-only")
        self.assertIn("execution mode", markdown.casefold())
        self.assertIn("report-only", markdown.casefold())
        for source in (before, after):
            self.assertIn(hashlib.sha256(source.read_bytes()).hexdigest(), markdown)


if __name__ == "__main__":
    unittest.main()
