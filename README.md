# Regression Witness

Observe the **same regression test failing before a change and passing after it**, and give a PR reviewer the commands, test identities, logs, and results behind that observation.

[简体中文](README.zh-CN.md)

A green test on a fixed revision does not show that the test detects the original problem. Regression Witness runs explicitly selected head test files against both committed revisions and produces a small Markdown report plus machine-readable JSON. It distinguishes an observed test failure from missing tests, skipped tests, execution errors, and inconsistent reports.

Built for local, trusted repositories. Capture reviewable evidence before merging a bug fix.

## Try the demo

Requirements: **Python 3.10+ and Git 2.31+**. The tool itself uses only the Python standard library. macOS and Linux are the intended platforms; Windows has not been verified.

From this repository's root directory:

```sh
python3 -B examples/demo.py --output /tmp/witness-demo
```

The output directory must not already exist. Choose a new path for another run.

The demo creates an intentionally synthetic Git repository, commits a small normalization bug and its fix, executes real `unittest` assertions, and derives JUnit XML from those results. It does not install dependencies or use the network. The expected verdict is `FAIL_TO_PASS_OBSERVED`.

Read `/tmp/witness-demo/evidence/report.md` and `report.json`.

## Run on a project

Prepare the project's test environment first. Regression Witness does not install the project, resolve dependencies, or create a virtual environment. Invoke the CLI from this repository's root directory:

```sh
python3 -m regression_witness run \
  --repo /path/to/project \
  --base BEFORE_FIX_COMMIT \
  --head AFTER_FIX_COMMIT \
  --overlay tests/test_bug.py \
  --test 'tests.test_bug::test_regression' \
  --command '/path/to/venv/bin/python -m pytest tests/test_bug.py --junitxml={junit}' \
  --output /tmp/witness-result \
  --repeats 2
```

Replace the revisions, paths, command, and test ID with values from your project. `/tmp/witness-result` must be a new directory.

**`--test` is an exact JUnit `classname::name`, not a pytest node ID.** For example:

```xml
<testcase classname="tests.test_bug" name="test_regression" />
```

That XML identifies `tests.test_bug::test_regression`. Its classname may differ from this example depending on the test layout and reporter configuration. Read the actual XML rather than converting a pytest node ID by guesswork. Parameterized names are preserved exactly.

- Repeat `--overlay` for each exact test or support file to copy from head into both snapshots. These must be committed regular files. Review the list: including production code can change what the baseline means.
- Repeat `--test` for each testcase whose failure-to-pass transition you intend to observe. Every selected case must meet the criterion.
- Include the literal `{junit}` placeholder in `--command`. The CLI replaces it with a fresh report path for each run.
- `--command` is parsed into an argument list and executed without automatically invoking a shell. Pipes, redirections, environment assignments, and `~` expansion are not interpreted.
- `--repeats` defaults to `2` in the CLI and accepts `1`–`10`. Every repetition uses fresh snapshots. `--timeout` defaults to 60 seconds per test command.

The integration suite runs actual pytest, covering failure-to-pass, collection errors, skipped tests, and xfail/xpass. Locally validated with Python 3.14.3 and pytest 9.1.1.

## What gets compared

The baseline is **base plus the explicitly selected head files**, not the untouched base tree. The other side is head with those same files. Regression Witness resolves revision names once, reads committed raw Git blobs into temporary directories, records SHA-256 hashes of the overlay files, and checks that their contents remain unchanged after execution.

Raw blob snapshots bypass checkout filters and `git archive` transformations: `export-ignore` and `export-subst` cannot silently remove or rewrite the tested files. The snapshots contain no `.git` metadata. The tool does not change the source repository's branch, index, or working-tree files; uncommitted changes are not included.

Each execution produces a new JUnit XML path and separate stdout/stderr logs. The report records resolved commits, overlay hashes, selected testcase IDs, command arguments, exit codes, durations, report hashes, and per-run outcomes. Repeated runs with different verdicts or selected transitions are marked `INCONCLUSIVE`.

The test command still executes code with your permissions. It can access or modify files outside its snapshot. File snapshots are **not a security sandbox**.

## Verdicts and exit codes

| Verdict | Meaning | CLI exit |
| --- | --- | --- |
| `FAIL_TO_PASS_OBSERVED` | Every selected testcase failed before and passed after, with no report integrity issues, execution errors, or remaining after failures. Supplied or captured exit codes must agree. | `0` |
| `NOT_REPRODUCED` | Every selected testcase already passed before and still passes after. | `1` |
| `STILL_FAILING` | The after report contains failures, including failures outside the selected cases. | `1` |
| `INCONCLUSIVE` | The available evidence cannot support the comparison. | `2` |

Invalid input and preparation errors also exit `2`; some errors occur before a report can be written. A completed `run` requires real captured exit codes, with pytest's `0` for a passing run and `1` for a run with test failures. Other codes, timeouts, and disagreement between exit codes and XML outcomes are inconclusive.

Missing or skipped selected tests, any execution error, duplicate testcase IDs, conflicting outcomes, empty reports, and inconsistent suite counts cannot establish success. A mixture of selected failure-to-pass and already-passing tests is inconclusive rather than an overall success. Unsupported retry/flaky outcome extensions also prevent a positive verdict.

Reports are written to:

```text
OUTPUT/
  report.md
  report.json
  runs/
    base-1/{junit.xml,stdout.log,stderr.log}
    head-1/{junit.xml,stdout.log,stderr.log}
    ...
```

Artifacts may be missing when execution or report generation fails. Logs are captured verbatim; review them before sharing a report bundle.

## Compare existing reports

```sh
python3 -m regression_witness compare \
  --before /path/to/before.xml \
  --after /path/to/after.xml \
  --test 'tests.test_bug::test_regression' \
  --output /tmp/witness-comparison
```

Optional `--before-exit` and `--after-exit` values enable consistency checks against caller-supplied pytest exit codes. **This mode remains report-only even when both codes are supplied.** It executes no tests and cannot verify the reports' origin, the command that produced them, or whether the same code or test inputs were used. Hashes identify the supplied bytes; they do not authenticate them.

## Scope and limits

- The current parser targets conventional JUnit XML with `testsuite`/`testsuites` roots and pytest-style exit semantics. It supports XML namespaces, but does not claim compatibility with every JUnit producer. DTD/entity declarations and reports over 10 MiB are rejected.
- Both complete Git trees must contain only regular files: symlinks, submodules, unsafe paths, and Git metadata paths are rejected. Snapshots are limited to 128 MiB and 100,000 files; each overlay file is limited to 10 MiB. There is no Git LFS hydration, submodule initialization, or dependency installation.
- Build steps and tools requiring `.git` may not work in these snapshots. A prepared environment must load the code under test from the snapshot. In particular, an editable install pointing at the original checkout can invalidate that assumption; import provenance is not automatically verified.
- Dependencies, environment variables, services, and credentials are not isolated or fully recorded. A failing assertion can still be wrong or caused by an environmental problem inside the test. Review the failure cause and the overlay list.
- Repetition can expose unstable outcomes, but cannot prove the absence of flaky tests. The selected command does not substitute for the project's broader test suite.
- Reports and executed test code are not independently authenticated. Treat a report as reviewable evidence, not an attestation from a trusted third party.

## Development and contributions

Run the standard-library test suite from the repository root:

```sh
python3 -B -m unittest discover -s tests -v
```

The four pytest integration tests are explicitly skipped when pytest is not installed. To include them, run the same command with a Python environment containing pytest. The CLI itself requires no third-party package.

Useful first contributions are small, verifiable examples and counterexamples:

1. Reproduce an existing merged Python bug fix with a pinned base/head, an exact overlay list, and an installed pytest environment. Record the real JUnit IDs and the setup needed to reproduce it.
2. Add a failing test for a report that would otherwise create a misleading positive verdict, such as a reporter-specific collection error or retry outcome.
3. Exercise an existing test environment on Linux and macOS, then document concrete compatibility limits.

Open an issue or pull request in [qjf1597300-cloud/regression-witness](https://github.com/qjf1597300-cloud/regression-witness). Include a minimal synthetic example or public commit references, expected versus observed behavior, Python/Git versions, and a sanitized report. Do not include credentials or private source code.

## Related work

Failure-to-pass verification is established practice. Regression Witness packages a narrow local workflow; it does not claim to invent it.

- [Apache DataFusion's PR review guide](https://datafusion.apache.org/contributor-guide/pr_review.html#review-the-test-coverage) asks reviewers to revert a fix and check that the new regression test fails without it.
- [SWE-bench's evaluation harness](https://www.swebench.com/SWE-bench/api/harness/) already uses fail-to-pass and pass-to-pass results and distinguishes infrastructure-related failures. It addresses benchmark evaluation with task and repository-specific setup.
- [GitHub's Awesome Copilot quality-playbook](https://github.com/github/awesome-copilot/blob/main/skills/quality-playbook/SKILL.md) specifies red/green execution, JUnit output, logs, and structured traceability. Regression Witness concentrates on a deterministic CLI for explicitly selected test evidence.
