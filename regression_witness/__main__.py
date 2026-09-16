"""python -m regression_witness --help"""

import argparse
import json
from pathlib import Path
import shlex
import sys

from . import __version__
from .evidence import EvidenceError, evaluate, parse_junit
from .reporting import write_reports
from .runner import run_witness, validate_output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Observe the same regression test failing before a fix and passing after it.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="mode", required=True)
    compare = commands.add_parser("compare", help="Inspect existing JUnit reports without executing code.")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--before-exit", type=int)
    compare.add_argument("--after-exit", type=int)
    run = commands.add_parser("run", help="Execute a trusted test command in two fresh Git snapshots (NOT a sandbox).")
    run.add_argument("--repo", type=Path, required=True)
    run.add_argument("--base", required=True)
    run.add_argument("--head", default="HEAD")
    run.add_argument("--overlay", action="append", required=True, help="Exact head test/support file path; repeat to add files.")
    run.add_argument("--command", required=True, help="Quoted argv, without shell operators; include {junit}.")
    run.add_argument("--timeout", type=float, default=60)
    run.add_argument("--repeats", type=int, default=2)
    for sub in (compare, run):
        sub.add_argument("--test", action="append", required=True, help="Exact JUnit classname::name; repeat for each selected test.")
        sub.add_argument("--output", type=Path, required=True, help="A directory that does not yet exist.")
    args = parser.parse_args(argv)
    try:
        if args.mode == "run":
            report = run_witness(
                args.repo, args.base, args.head, args.overlay, args.test,
                shlex.split(args.command), args.output, args.timeout, args.repeats,
            )
        else:
            args.output = validate_output(args.output)
            before, after = parse_junit(args.before), parse_junit(args.after)
            result = evaluate(before, after, args.test, args.before_exit, args.after_exit)
            report = {
                "schema_version": 1, "tool_version": __version__, "mode": "report-only",
                **result, "selected_tests": args.test,
                "input_sha256": {"before": before["sha256"], "after": after["sha256"]},
                "exit_codes": {"before": args.before_exit, "after": args.after_exit},
            }
            args.output.mkdir(parents=True, exist_ok=False)
            write_reports(args.output, report)
        print(json.dumps({"verdict": report["verdict"], "reason": report["reason"], "report": str(args.output.absolute() / "report.md")}, ensure_ascii=False))
        return 0 if report["verdict"] == "FAIL_TO_PASS_OBSERVED" else 2 if report["verdict"] == "INCONCLUSIVE" else 1
    except (EvidenceError, OSError, ValueError) as exc:
        print("regression-witness: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
