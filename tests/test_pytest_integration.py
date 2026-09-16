"""Optional compatibility tests using real pytest subprocesses and JUnit XML.

The application has no pytest dependency. These tests skip when pytest is not
available to this interpreter; they never install it or fabricate its reports.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from examples.demo import git
from regression_witness.runner import run_witness


PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None
TEST_ID = "test_bug::test_regression"


@unittest.skipUnless(PYTEST_AVAILABLE, "Optional integration dependency pytest is not installed")
class PytestIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="regression-witness-pytest-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        (self.repo / "bug.py").write_text(
            "def normalize(value):\n    return value.strip()\n", encoding="utf-8",
        )
        git(self.repo, "add", "bug.py")
        git(self.repo, "commit", "-m", "Synthetic pytest baseline with case-normalization bug")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def run_scenario(self, *, marker="", extra_import=""):
        (self.repo / "bug.py").write_text(
            "def normalize(value):\n    return value.strip().casefold()\n", encoding="utf-8",
        )
        (self.repo / "test_bug.py").write_text(
            "import pytest\nfrom bug import normalize\n"
            + extra_import + "\n" + marker
            + "def test_regression():\n"
            + "    assert normalize('  Hello  ') == 'hello'\n",
            encoding="utf-8",
        )
        git(self.repo, "add", "bug.py", "test_bug.py")
        git(self.repo, "commit", "-m", "Synthetic pytest fix and regression test")
        head = git(self.repo, "rev-parse", "HEAD")
        # An unrelated locally installed plugin or PYTEST_ADDOPTS must not alter
        # these controlled compatibility cases. This does not install anything.
        with patch.dict(os.environ, {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}):
            return run_witness(
                repo=self.repo, base=self.base, head=head,
                overlay=["test_bug.py"], tests=[TEST_ID],
                command=[
                    sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    "-o", "junit_family=xunit2", "--junitxml", "{junit}", "test_bug.py",
                ],
                output=self.root / "evidence", timeout=30,
            )

    def junit(self, side):
        return ET.parse(self.root / "evidence" / "runs" / (side + "-1") / "junit.xml").getroot()

    def selected_case(self, side):
        matches = [
            case for case in self.junit(side).iter("testcase")
            if case.get("classname", "") + "::" + case.get("name", "") == TEST_ID
        ]
        self.assertEqual(len(matches), 1, "Actual pytest testcase identity must match the selected ID")
        return matches[0]

    def test_actual_pytest_failure_then_pass_is_observed(self):
        result = self.run_scenario()
        self.assertEqual(result["verdict"], "FAIL_TO_PASS_OBSERVED")
        self.assertIsNotNone(self.selected_case("base").find("failure"))
        head = self.selected_case("head")
        for outcome in ("failure", "error", "skipped"):
            self.assertIsNone(head.find(outcome))

    def test_actual_pytest_collection_error_is_inconclusive(self):
        result = self.run_scenario(
            extra_import="import regression_witness_deliberately_missing_dependency\n",
        )
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertTrue(any(run["exit_code"] == 2 for run in result["runs"]))
        self.assertTrue(list(self.junit("base").iter("error")))

    def test_xfail_then_xpass_does_not_prove_failure_to_pass(self):
        result = self.run_scenario(
            marker="@pytest.mark.xfail(reason='synthetic expected failure', strict=False)\n",
        )
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        skipped = self.selected_case("base").find("skipped")
        self.assertIsNotNone(skipped)
        self.assertEqual(skipped.get("type"), "pytest.xfail")

    def test_actual_pytest_skip_cannot_count_as_a_pass(self):
        result = self.run_scenario(
            marker="@pytest.mark.skip(reason='synthetic unsupported environment')\n",
        )
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertIsNotNone(self.selected_case("base").find("skipped"))
        self.assertIsNotNone(self.selected_case("head").find("skipped"))


if __name__ == "__main__":
    unittest.main()
