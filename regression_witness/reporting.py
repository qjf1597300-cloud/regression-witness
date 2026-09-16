"""Small reports with explicit limits on what was observed."""

import json
from pathlib import Path


def cell(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def markdown(report):
    lines = [
        "# Regression Witness",
        "",
        "**%s**" % report["verdict"],
        "",
        "Execution mode: **%s**." % cell(report.get("mode", "unspecified")),
        "",
        cell(report["reason"]),
        "",
        "This is an observation of test outcomes, not a proof that a fix is correct.",
        "",
    ]
    if report.get("mode") == "report-only":
        lines += [
            "**No tests were executed by this comparison.** XML files and any exit codes were supplied by the caller.",
            "",
            "Input SHA-256:",
            "",
        ]
        for side, digest in report.get("input_sha256", {}).items():
            lines.append("- %s: %s" % (cell(side), cell(digest)))
        lines.append("")
    if "commits" in report:
        lines += [
            "- Base: " + cell(report["commits"]["base"]),
            "- Head: " + cell(report["commits"]["head"]),
            "- Baseline tested: base with the explicitly listed head files overlaid.",
            "- Repetitions: " + str(report.get("repeats", 1)),
            "",
            "## Identical test inputs",
            "",
            "| File | SHA-256 |",
            "| --- | --- |",
        ]
        for path, digest in report.get("overlay_hashes", {}).items():
            lines.append("| %s | %s |" % (cell(path), digest))
        lines.append("")
    lines += ["## Selected tests", "", "| Test ID | Before | After |", "| --- | --- | --- |"]
    for row in report.get("transitions", []):
        lines.append("| %s | %s | %s |" % (cell(row["test"]), row["before"], row["after"]))
    lines.append("")
    if report.get("runs"):
        lines += ["## Execution", "", "| Run | Exit | Duration | Issues |", "| --- | --- | --- | --- |"]
        for run in report["runs"]:
            lines.append("| %s-%s | %s | %.3fs | %s |" % (
                run["side"], run["iteration"], run["exit_code"], run["duration_s"],
                cell("; ".join(run.get("issues", [])) or "none"),
            ))
        lines += ["", "Exact commands, outcomes and artifact paths are in report.json.", ""]
        failures = []
        for run in report["runs"]:
            for name, case in run.get("cases", {}).items():
                if case["status"] in ("failure", "error"):
                    failures.append((run["side"], name, case.get("message", "")))
        if failures:
            lines += ["## Failure summaries — review the cause", ""]
            for side, name, message in failures[:20]:
                lines.append("- %s / %s: %s" % (cell(side), cell(name), cell(message)))
            lines.append("")
    lines += [
        "## Limits",
        "",
        "- Failure may reflect a bad assertion or an environmental problem inside a test.",
        "- Repetition can reveal instability; it does not establish the absence of flakiness.",
        "- Reports and test code are not independently authenticated.",
        "- Local execution is not a security sandbox. Logs and XML may contain private data.",
        "",
    ]
    return "\n".join(lines)


def write_reports(output: Path, report):
    # The caller reserves a fresh directory; never replace a previous report.
    with (output / "report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with (output / "report.md").open("x", encoding="utf-8") as handle:
        handle.write(markdown(report))
