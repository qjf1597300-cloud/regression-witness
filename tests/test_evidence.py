"""Counterexamples that must never be presented as regression-fix evidence."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from regression_witness.__main__ import main
from regression_witness.evidence import EvidenceError, MAX_REPORT_BYTES, evaluate, parse_junit


class JUnitEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "report.xml"

    def parse(self, xml):
        self.path.write_bytes(xml if isinstance(xml, bytes) else xml.encode())
        return parse_junit(self.path)

    def test_namespace_parameterized_name_and_hash(self):
        report = self.parse('<testsuites xmlns="urn:junit" tests="1" failures="1"><testsuite tests="1" failures="1"><testcase classname="test_math.TestMath" name="test_divide[zero:1]"><failure message="division failed">details</failure></testcase></testsuite></testsuites>')
        self.assertEqual(report["cases"]["test_math.TestMath::test_divide[zero:1]"]["status"], "failure")
        self.assertEqual(report["issues"], [])
        self.assertEqual(len(report["sha256"]), 64)

    def test_missing_classname_is_supported(self):
        self.assertIn("::test_one", self.parse('<testsuite><testcase name="test_one"/></testsuite>')["cases"])

    def test_nested_suites_are_counted_once(self):
        report = self.parse('<testsuites tests="2"><testsuite tests="2"><testcase name="one"/><testsuite tests="1"><testcase name="two"/></testsuite></testsuite></testsuites>')
        self.assertEqual(len(report["cases"]), 2)
        self.assertEqual(report["issues"], [])

    def test_rejects_empty_report_even_if_declaring_tests(self):
        for xml in ('<testsuite/>', '<testsuite tests="1" errors="1"/>', '<testsuites/>'):
            with self.subTest(xml=xml), self.assertRaises(EvidenceError):
                self.parse(xml)

    def test_rejects_bad_root_and_malformed_xml(self):
        for xml in ('<report><testcase name="x"/></report>', '<testsuite>', ''):
            with self.subTest(xml=xml), self.assertRaises(EvidenceError):
                self.parse(xml)

    def test_rejects_duplicate_identity_across_suites(self):
        with self.assertRaisesRegex(EvidenceError, "Duplicate"):
            self.parse('<testsuites><testsuite><testcase classname="x" name="same"/></testsuite><testsuite><testcase classname="x" name="same"><failure/></testcase></testsuite></testsuites>')

    def test_rejects_empty_name(self):
        for name in ('', '   '):
            with self.subTest(name=name), self.assertRaises(EvidenceError):
                self.parse(f'<testsuite><testcase name="{name}"/></testsuite>')

    def test_rejects_conflicting_outcomes(self):
        for outcomes in ('<failure/><skipped/>', '<failure/><error/>', '<error/><skipped/>'):
            with self.subTest(outcomes=outcomes), self.assertRaisesRegex(EvidenceError, "Conflicting"):
                self.parse(f'<testsuite><testcase name="x">{outcomes}</testcase></testsuite>')

    def test_multiple_failures_are_one_failed_testcase(self):
        report = self.parse('<testsuite tests="1" failures="1"><testcase name="x"><failure message="first"/><failure message="second"/></testcase></testsuite>')
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["cases"]["::x"]["message"], "first\nsecond")

    def test_rejects_dtd_in_utf8_and_utf16(self):
        documents = (
            '<!DOCTYPE testsuite [<!ENTITY x "expanded">]><testsuite><testcase name="x"><failure>&x;</failure></testcase></testsuite>',
            '<!DOCTYPE testsuite><testsuite><testcase name="x"/></testsuite>',
            '<!DOCTYPE testsuite SYSTEM "unused.dtd"><testsuite><testcase name="x"/></testsuite>',
        )
        for xml in documents:
            for encoding in ("utf-8", "utf-16"):
                with self.subTest(xml=xml, encoding=encoding), self.assertRaisesRegex(EvidenceError, "DOCTYPE"):
                    self.parse(xml.encode(encoding))

    def test_declaration_text_in_cdata_and_comments_is_not_a_dtd(self):
        text = '<!DOCTYPE sample [<!ENTITY value "literal text">]>'
        fragments = (
            '<system-out><![CDATA[' + text + ']]></system-out>',
            '<!-- ' + text + ' -->',
        )
        for fragment in fragments:
            xml = '<testsuite tests="1"><testcase name="x"/>' + fragment + '</testsuite>'
            for encoding in ("utf-8", "utf-16"):
                with self.subTest(fragment=fragment, encoding=encoding):
                    parsed = self.parse(xml.encode(encoding))
                    self.assertEqual(parsed["cases"]["::x"]["status"], "passed")
                    self.assertEqual(parsed["issues"], [])

    def test_unknown_xml_encoding_is_an_evidence_error(self):
        xml = b'<?xml version="1.0" encoding="definitely-unknown-codec"?><testsuite><testcase name="x"/></testsuite>'
        with self.assertRaisesRegex(EvidenceError, "unknown encoding"):
            self.parse(xml)

    def test_compare_unknown_xml_encoding_returns_two_without_traceback(self):
        self.parse('<testsuite><testcase name="x"><failure/></testcase></testsuite>')
        after = Path(self.directory.name) / "after.xml"
        after.write_bytes(b'<?xml version="1.0" encoding="definitely-unknown-codec"?><testsuite><testcase name="x"/></testsuite>')
        output = Path(self.directory.name) / "comparison"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main([
                "compare", "--before", str(self.path), "--after", str(after),
                "--test", "::x", "--output", str(output),
            ])
        self.assertEqual(status, 2)
        self.assertIn("unknown encoding", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(output.exists())

    def test_rejects_oversized_report(self):
        with self.assertRaisesRegex(EvidenceError, "10 MiB"):
            self.parse(b" " * (MAX_REPORT_BYTES + 1))

    def test_rejects_missing_file(self):
        with self.assertRaises(EvidenceError):
            parse_junit(self.path)

    def test_failure_message_is_bounded(self):
        report = self.parse('<testsuite><testcase name="x"><failure message="' + 'x' * 2000 + '"/></testcase></testsuite>')
        self.assertEqual(len(report["cases"]["::x"]["message"]), 500)

    def test_counts_cannot_hide_missing_execution(self):
        for attrs in ('tests="2"', 'errors="1"', 'failures="1"', 'skipped="1"', 'tests="-1"', 'tests="garbage"', 'tests="' + '9' * 5000 + '"'):
            with self.subTest(attrs=attrs):
                report = self.parse(f'<testsuite {attrs}><testcase name="x"/></testsuite>')
                self.assertTrue(report["issues"])
                self.assertEqual(evaluate(report, report, ["::x"])["verdict"], "INCONCLUSIVE")

    def test_empty_error_suite_blocks_otherwise_valid_report(self):
        report = self.parse('<testsuites><testsuite errors="1"/><testsuite><testcase name="x"/></testsuite></testsuites>')
        self.assertTrue(report["issues"])

    def test_suite_level_error_blocks_success(self):
        report = self.parse('<testsuite><error message="collection failed"/><testcase name="x"/></testsuite>')
        self.assertTrue(report["issues"])

    def test_rejects_testcase_hidden_in_output(self):
        with self.assertRaises(EvidenceError):
            self.parse('<testsuite><system-out><testcase name="fake"/></system-out></testsuite>')

    def test_notrun_attribute_cannot_count_as_pass(self):
        report = self.parse('<testsuite><testcase name="x" status="notrun"/></testsuite>')
        self.assertTrue(report["issues"])

    def test_flaky_retry_extensions_cannot_count_as_plain_pass(self):
        report = self.parse('<testsuite><testcase name="x"><flakyFailure message="first attempt failed"/></testcase></testsuite>')
        self.assertTrue(report["issues"])

    def test_excessive_nested_suites_fail_with_evidence_error(self):
        with self.assertRaises(EvidenceError):
            self.parse('<testsuite>' * 150 + '<testcase name="x"/>' + '</testsuite>' * 150)


def report(**statuses):
    return {"cases": {name: {"status": value, "message": ""} for name, value in statuses.items()}, "issues": [], "sha256": "test-fixture"}


class TransitionEvidenceTests(unittest.TestCase):
    def test_observes_executed_transition(self):
        result = evaluate(report(target="failure"), report(target="passed"), ["target"], 1, 0)
        self.assertEqual(result["verdict"], "FAIL_TO_PASS_OBSERVED")
        self.assertEqual(result["transitions"], [{"test": "target", "before": "failure", "after": "passed"}])
        self.assertIn("not fix correctness", result["reason"])

    def test_report_only_mode_labels_unverified_execution(self):
        result = evaluate(report(target="failure"), report(target="passed"), ["target"])
        self.assertEqual(result["verdict"], "FAIL_TO_PASS_OBSERVED")
        self.assertIn("execution was not fully verified", result["reason"])

    def test_already_green_is_not_reproduction(self):
        self.assertEqual(evaluate(report(target="passed"), report(target="passed"), ["target"], 0, 0)["verdict"], "NOT_REPRODUCED")

    def test_after_failure_is_still_failing(self):
        self.assertEqual(evaluate(report(target="failure"), report(target="failure"), ["target"], 1, 1)["verdict"], "STILL_FAILING")

    def test_failure_outside_selection_still_blocks_after_success(self):
        self.assertEqual(evaluate(report(target="failure"), report(target="passed", unrelated="failure"), ["target"], 1, 1)["verdict"], "STILL_FAILING")

    def test_unrelated_skipped_case_does_not_fabricate_selected_skip(self):
        self.assertEqual(evaluate(report(target="failure", optional="skipped"), report(target="passed", optional="skipped"), ["target"], 1, 0)["verdict"], "FAIL_TO_PASS_OBSERVED")

    def test_mixed_selected_transitions_are_inconclusive(self):
        self.assertEqual(evaluate(report(a="failure", b="passed"), report(a="passed", b="passed"), ["a", "b"], 1, 0)["verdict"], "INCONCLUSIVE")

    def test_error_anywhere_cannot_count_as_reproduced(self):
        scenarios = [
            (report(target="error"), report(target="passed")),
            (report(target="failure"), report(target="error")),
            (report(target="failure", setup="error"), report(target="passed")),
            (report(target="failure"), report(target="passed", teardown="error")),
        ]
        for before, after in scenarios:
            with self.subTest(before=before, after=after):
                self.assertEqual(evaluate(before, after, ["target"])["verdict"], "INCONCLUSIVE")

    def test_selected_skips_or_missing_cases_are_inconclusive(self):
        scenarios = [
            (report(target="skipped"), report(target="passed")),
            (report(target="failure"), report(target="skipped")),
            (report(other="failure"), report(target="passed")),
            (report(target="failure"), report(other="passed")),
        ]
        for before, after in scenarios:
            with self.subTest(before=before, after=after):
                self.assertEqual(evaluate(before, after, ["target"])["verdict"], "INCONCLUSIVE")

    def test_setup_interrupt_collection_no_tests_and_signal_exits_are_inconclusive(self):
        for exit_code in (2, 3, 4, 5, 6, 127, -9, True):
            with self.subTest(exit_code=exit_code):
                self.assertEqual(evaluate(report(target="failure"), report(target="passed"), ["target"], exit_code, 0)["verdict"], "INCONCLUSIVE")

    def test_exit_codes_must_agree_with_structured_evidence(self):
        for before_exit, after_exit in ((0, 0), (1, 1)):
            with self.subTest(before_exit=before_exit, after_exit=after_exit):
                self.assertEqual(evaluate(report(target="failure"), report(target="passed"), ["target"], before_exit, after_exit)["verdict"], "INCONCLUSIVE")
        self.assertEqual(evaluate(report(target="passed"), report(target="passed"), ["target"], 1, 0)["verdict"], "INCONCLUSIVE")

    def test_missing_and_invalid_report_shapes_are_inconclusive(self):
        for broken in (None, {}, {"cases": {}}, {"cases": {"target": {"status": "passed"}}}, {"cases": {"target": {"status": "unknown"}}, "issues": []}, {"cases": {"target": {"status": []}}, "issues": []}, {"cases": []}):
            with self.subTest(broken=broken):
                self.assertEqual(evaluate(broken, report(target="passed"), ["target"])["verdict"], "INCONCLUSIVE")

    def test_explicit_unique_selection_required(self):
        for selected in ([], ["target", "target"], [""], [None], "target"):
            with self.subTest(selected=selected):
                self.assertEqual(evaluate(report(target="failure"), report(target="passed"), selected)["verdict"], "INCONCLUSIVE")

    def test_report_issues_always_block_positive_verdict(self):
        after = report(target="passed")
        after["issues"] = ["suite says errors=1 but no error case exists"]
        self.assertEqual(evaluate(report(target="failure"), after, ["target"], 1, 0)["verdict"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
