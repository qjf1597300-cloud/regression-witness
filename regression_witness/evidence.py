"""Conservative JUnit evidence parsing and observed test-transition evaluation."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET


MAX_REPORT_BYTES = 10 * 1024 * 1024
STATUSES = frozenset({"passed", "failure", "error", "skipped"})


class EvidenceError(ValueError):
    """A report cannot be interpreted without risking misleading evidence."""


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def parse_junit(path: Path) -> dict:
    """Read a bounded JUnit XML report; retain inconsistencies as blocking issues.

    The accepted structure has a testsuite or testsuites root. Test cases must
    be immediate children of a testsuite. Nested suites and XML namespaces are
    supported. Missing aggregate counts are allowed, but supplied counts must
    describe the parsed test cases exactly.
    """
    path = Path(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_REPORT_BYTES + 1)
    except OSError as exc:
        raise EvidenceError(f"Cannot read JUnit report: {exc}") from exc
    if len(raw) > MAX_REPORT_BYTES:
        raise EvidenceError("JUnit report exceeds the 10 MiB limit")
    # Removing NULs also exposes these ASCII declarations in UTF-16/UTF-32.
    declarations = raw.replace(b"\x00", b"").lower()
    if re.search(br"<!\s*(?:doctype|entity)\b", declarations):
        raise EvidenceError("DOCTYPE and ENTITY declarations are not supported")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as exc:
        raise EvidenceError(f"Invalid JUnit XML: {exc}") from exc
    if _tag(root) not in {"testsuite", "testsuites"}:
        raise EvidenceError("JUnit root must be testsuite or testsuites")

    cases: dict[str, dict[str, str]] = {}
    issues: list[str] = []
    visited: set[int] = set()

    def read_case(element: ET.Element) -> str:
        visited.add(id(element))
        name = element.get("name", "")
        if not name.strip():
            raise EvidenceError("JUnit testcase has an empty name")
        case_id = element.get("classname", "") + "::" + name
        if case_id in cases:
            raise EvidenceError(f"Duplicate JUnit testcase ID: {case_id}")
        outcomes = [child for child in element if _tag(child) in STATUSES - {"passed"}]
        for child in element:
            if _tag(child) not in {"failure", "error", "skipped", "properties", "system-out", "system-err"}:
                issues.append(f"Unsupported testcase element for {case_id}: {_tag(child)}")
        kinds = {_tag(child) for child in outcomes}
        if len(kinds) > 1:
            raise EvidenceError(f"Conflicting outcomes for testcase: {case_id}")
        status = next(iter(kinds), "passed")
        messages = []
        for outcome in outcomes:
            message = outcome.get("message", "")
            detail = "".join(outcome.itertext()).strip()
            if message:
                messages.append(message)
            if detail and detail != message:
                messages.append(detail)
        cases[case_id] = {"status": status, "message": "\n".join(messages)[:500]}
        # Some producers encode non-execution only in a status attribute. Do
        # not silently call such a testcase passed when there is no outcome.
        declared_status = element.get("status")
        if declared_status is not None and declared_status.lower() not in {"run", "passed"}:
            issues.append(f"Unsupported testcase status attribute for {case_id}: {declared_status}")
        return case_id

    def read_suite(element: ET.Element, depth: int = 0) -> list[str]:
        if depth > 128:
            raise EvidenceError("JUnit suite nesting exceeds the supported depth")
        visited.add(id(element))
        case_ids: list[str] = []
        for child in element:
            child_tag = _tag(child)
            if child_tag in {"testsuite", "testsuites"}:
                case_ids.extend(read_suite(child, depth + 1))
            elif child_tag == "testcase":
                if _tag(element) != "testsuite":
                    raise EvidenceError("A testcase must be inside a testsuite")
                case_ids.append(read_case(child))
            elif child_tag in {"error", "failure"}:
                issues.append(f"Suite-level {child_tag} is not testcase evidence")
        label = element.get("name") or _tag(element)
        actual = {
            "tests": len(case_ids),
            "failures": sum(cases[key]["status"] == "failure" for key in case_ids),
            "errors": sum(cases[key]["status"] == "error" for key in case_ids),
            "skipped": sum(cases[key]["status"] == "skipped" for key in case_ids),
        }
        for field, expected in actual.items():
            declared = element.get(field)
            if declared is None:
                continue
            if not re.fullmatch(r"[0-9]+", declared):
                issues.append(f"Invalid {field} count in suite {label}: {declared}")
                continue
            # Bound digit length before int() to avoid Python's conversion
            # limit and pathological numeric values in untrusted reports.
            if len(declared) > 15:
                issues.append(f"Unreasonable {field} count in suite {label}")
                continue
            count = int(declared)
            if count != expected:
                issues.append(f"Suite {label} declares {field}={count}, parsed {expected}")
            if field == "errors" and count:
                issues.append(f"Suite {label} declares execution errors")
        disabled = element.get("disabled")
        if disabled is not None and disabled != "0":
            issues.append(f"Suite {label} declares disabled tests: {disabled}")
        return case_ids

    read_suite(root)
    for element in root.iter():
        if _tag(element) in {"testcase", "testsuite", "testsuites"} and id(element) not in visited:
            raise EvidenceError("JUnit test structures occur outside the supported suite hierarchy")
    if not cases:
        raise EvidenceError("JUnit report contains no testcases")
    return {"cases": cases, "issues": issues, "sha256": hashlib.sha256(raw).hexdigest()}


def evaluate(
    before: dict,
    after: dict,
    tests: list[str],
    before_exit: int | None = None,
    after_exit: int | None = None,
) -> dict:
    """Classify observed transitions, without claiming a bug or fix is correct.

    Exit codes, when supplied, follow pytest's 0=passed and 1=test failure
    convention. Report-only comparisons are explicitly labelled in the reason.
    """
    selected = tests if isinstance(tests, list) else []
    before_cases = before.get("cases", {}) if isinstance(before, dict) else {}
    after_cases = after.get("cases", {}) if isinstance(after, dict) else {}
    before_cases = before_cases if isinstance(before_cases, dict) else {}
    after_cases = after_cases if isinstance(after_cases, dict) else {}

    def get_status(cases: dict, test: Any) -> str:
        if not isinstance(test, str):
            return "missing"
        case = cases.get(test)
        if not isinstance(case, dict):
            return "missing"
        status = case.get("status")
        return status if isinstance(status, str) else "missing"

    transitions = [
        {"test": test, "before": get_status(before_cases, test), "after": get_status(after_cases, test)}
        for test in selected
    ]

    def result(verdict: str, reason: str) -> dict:
        if before_exit is None or after_exit is None:
            reason += " Report-only or partial-exit comparison; execution was not fully verified."
        return {"verdict": verdict, "reason": reason, "transitions": transitions}

    def inconclusive(reason: str) -> dict:
        return result("INCONCLUSIVE", reason)

    if not selected or any(not isinstance(test, str) or not test.strip() for test in selected):
        return inconclusive("Select at least one non-empty testcase ID explicitly.")
    if len(set(selected)) != len(selected):
        return inconclusive("Selected testcase IDs must be unique.")
    for label, report, cases, exit_code in (
        ("before", before, before_cases, before_exit),
        ("after", after, after_cases, after_exit),
    ):
        if not isinstance(report, dict) or not cases:
            return inconclusive(f"The {label} report is absent or empty.")
        if not isinstance(report.get("issues"), list):
            return inconclusive(f"The {label} report lacks a valid issues list.")
        if report["issues"]:
            return inconclusive(f"The {label} report has blocking inconsistencies: {report['issues'][0]}")
        if any(
            not isinstance(case, dict)
            or not isinstance(case.get("status"), str)
            or case["status"] not in STATUSES
            for case in cases.values()
        ):
            return inconclusive(f"The {label} report has an invalid testcase outcome.")
        statuses = [case["status"] for case in cases.values()]
        if "error" in statuses:
            return inconclusive(f"The {label} report contains execution errors, not only test failures.")
        if exit_code is not None:
            if type(exit_code) is not int or exit_code not in {0, 1}:
                return inconclusive(f"The {label} pytest exit code {exit_code!r} does not describe a completed test run.")
            expected_exit = 1 if "failure" in statuses else 0
            if exit_code != expected_exit:
                return inconclusive(f"The {label} exit code {exit_code} conflicts with its testcase outcomes.")
    if any(row[phase] in {"missing", "skipped", "error"} for row in transitions for phase in ("before", "after")):
        return inconclusive("Every selected testcase must execute in both reports; missing, skipped, or error outcomes cannot establish a transition.")
    if any(case["status"] == "failure" for case in after_cases.values()):
        return result("STILL_FAILING", "The after report still contains failing tests; inspect the selected transitions and full report.")
    if all(row["before"] == "passed" and row["after"] == "passed" for row in transitions):
        return result("NOT_REPRODUCED", "Every selected testcase already passed before the change.")
    if all(row["before"] == "failure" and row["after"] == "passed" for row in transitions):
        return result("FAIL_TO_PASS_OBSERVED", "Every selected testcase failed before and passed after, with no after failures or execution errors. This observes a transition, not fix correctness.")
    return inconclusive("Selected testcases do not all share the required failure-to-pass transition.")
