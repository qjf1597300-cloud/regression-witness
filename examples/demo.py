#!/usr/bin/env python3
"""Create a synthetic bug-fix repository and observe its real test results.

Run from any directory:
    python /path/to/regression-witness/examples/demo.py --output /new/demo/path

The generated repository, commits, and evidence are an intentionally artificial
demonstration. They are not evidence of adoption or a contribution to another
open-source project. No dependency installation or network access is needed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


TEST_ID = "test_normalize.NormalizeTests::test_casefold"
TEST_FILE = "test_normalize.py"

# This runner executes unittest assertions and derives JUnit from TestResult.
# Failures, errors, and skips are never replaced with canned XML outcomes.
UNITTEST_RUNNER = textwrap.dedent(
    r'''
    import argparse
    from pathlib import Path
    import sys
    import time
    import unittest
    import xml.etree.ElementTree as ET

    class RecordingResult(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.records = []
            self.started = {}

        def startTest(self, test):
            self.started[test.id()] = time.monotonic()
            super().startTest(test)

        def record(self, test, outcome, detail=""):
            elapsed = time.monotonic() - self.started.get(test.id(), time.monotonic())
            self.records.append((test.id(), outcome, detail, max(0.0, elapsed)))

        def addSuccess(self, test):
            super().addSuccess(test)
            self.record(test, "passed")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.record(test, "failure", self._exc_info_to_string(err, test))

        def addError(self, test, err):
            super().addError(test, err)
            self.record(test, "error", self._exc_info_to_string(err, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.record(test, "skipped", reason)

    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args()
    suite = unittest.TestLoader().discover(".", pattern="test_normalize.py")
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    root = ET.Element("testsuite", {
        "name": "synthetic-normalize-demo",
        "tests": str(result.testsRun),
        "failures": str(len(result.failures)),
        "errors": str(len(result.errors)),
        "skipped": str(len(result.skipped)),
    })
    for identity, outcome, detail, elapsed in result.records:
        classname, _, name = identity.rpartition(".")
        case = ET.SubElement(root, "testcase", {
            "classname": classname, "name": name, "time": "%.6f" % elapsed,
        })
        if outcome != "passed":
            child = ET.SubElement(case, outcome, {"message": detail.splitlines()[-1] if detail else outcome})
            child.text = detail
    args.junit.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(args.junit, encoding="utf-8", xml_declaration=True)
    sys.exit(2 if result.errors else (0 if result.wasSuccessful() else 1))
    '''
).lstrip()


@dataclass(frozen=True)
class DemoRepository:
    repo: Path
    base: str
    head: str
    test_id: str = TEST_ID
    test_file: str = TEST_FILE


def git(repo: Path, *arguments: str) -> str:
    """Run local Git without inheriting signing, hooks, or identity settings."""
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Regression Witness Synthetic Demo",
        "GIT_AUTHOR_EMAIL": "synthetic-demo@example.invalid",
        "GIT_COMMITTER_NAME": "Regression Witness Synthetic Demo",
        "GIT_COMMITTER_EMAIL": "synthetic-demo@example.invalid",
    })
    completed = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(repo), *arguments],
        check=True, capture_output=True, text=True, env=env,
    )
    return completed.stdout.strip()


def make_demo_repository(repo: Path, *, scenario: str = "redgreen") -> DemoRepository:
    """Make committed toy revisions; other scenarios exercise evidence limits."""
    allowed = {"redgreen", "allgreen", "still_failing", "collection_error", "test_error", "skipped"}
    if scenario not in allowed:
        raise ValueError("Unknown synthetic scenario: " + scenario)
    repo = repo.resolve()
    repo.mkdir(parents=True, exist_ok=False)
    git(repo, "init", "-b", "main")
    buggy = 'def normalize(value):\n    return value.strip()\n'
    fixed = 'def normalize(value):\n    return value.strip().casefold()\n'
    (repo / "normalize.py").write_text(fixed if scenario == "allgreen" else buggy, encoding="utf-8")
    (repo / "run_tests.py").write_text(UNITTEST_RUNNER, encoding="utf-8")
    (repo / "README.txt").write_text(
        "SYNTHETIC DEMO. This repository intentionally demonstrates a toy bug.\n"
        "It is not an external open-source contribution or adoption evidence.\n",
        encoding="utf-8",
    )
    git(repo, "add", "normalize.py", "run_tests.py", "README.txt")
    git(repo, "commit", "-m", "Synthetic baseline: normalize whitespace")
    base = git(repo, "rev-parse", "HEAD")

    header = "import unittest\nfrom normalize import normalize\n"
    decorator = "    @unittest.skip('synthetic unsupported scenario')\n" if scenario == "skipped" else ""
    body = "        self.assertEqual(normalize('  Hello  '), 'hello')\n"
    if scenario == "collection_error":
        header += "import regression_witness_deliberately_missing_dependency\n"
    elif scenario == "test_error":
        body = "        import regression_witness_deliberately_missing_dependency\n" + body
    test_source = header + "\nclass NormalizeTests(unittest.TestCase):\n" + decorator + "    def test_casefold(self):\n" + body
    (repo / TEST_FILE).write_text(test_source, encoding="utf-8")
    (repo / "normalize.py").write_text(buggy if scenario == "still_failing" else fixed, encoding="utf-8")
    git(repo, "add", "normalize.py", TEST_FILE)
    git(repo, "commit", "-m", "Synthetic fix and regression test: normalize case")
    return DemoRepository(repo=repo, base=base, head=git(repo, "rev-parse", "HEAD"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New directory for the synthetic repository and evidence")
    args = parser.parse_args(argv)
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error("--output must be a new directory: " + str(output))
    output.mkdir(parents=True, exist_ok=False)
    demo = make_demo_repository(output / "synthetic-repo")
    from regression_witness.runner import run_witness
    evidence = output / "evidence"
    result = run_witness(
        repo=demo.repo, base=demo.base, head=demo.head,
        overlay=[demo.test_file], tests=[demo.test_id],
        command=[sys.executable, "run_tests.py", "--junit", "{junit}"],
        output=evidence,
    )
    print("SYNTHETIC DEMO — real assertions in an intentionally artificial repository.")
    print(json.dumps({
        "verdict": result["verdict"], "base": demo.base, "head": demo.head,
        "repository": str(demo.repo), "report": str(evidence / "report.md"),
    }, indent=2))
    return 0 if result["verdict"] == "FAIL_TO_PASS_OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
