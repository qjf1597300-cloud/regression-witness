"""Run one test command against two immutable Git revisions.

Raw Git blobs isolate checkout files without changing the source branch or index.
They DO NOT isolate processes, dependencies, credentials, or network access.
"""

import hashlib
import io
import math
import os
from pathlib import Path, PurePosixPath
import platform
import signal
import subprocess
import tempfile
import time

from .evidence import EvidenceError, evaluate, parse_junit
from .reporting import write_reports

MAX_ARCHIVE = 128 * 1024 * 1024
MAX_OVERLAY = 10 * 1024 * 1024


def _git(repo, *args, timeout=30, input_data=None):
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
            capture_output=True, timeout=timeout, env=env, input=input_data,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("Git command failed: %s" % exc) from exc
    if result.returncode:
        raise EvidenceError("Git command failed: " + result.stderr.decode("utf-8", "replace")[:1000])
    return result.stdout


def _revision(repo, ref):
    if not ref or "\0" in ref:
        raise EvidenceError("A Git revision is required.")
    return _git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def _safe_path(path):
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or "\\" in path or "\0" in path or ".." in candidate.parts:
        raise EvidenceError("Unsafe relative file path: %r" % path)
    if any(part.casefold() == ".git" for part in candidate.parts):
        raise EvidenceError("Git metadata is not a test input.")
    if str(candidate) in ("", "."):
        raise EvidenceError("Expected an exact file path, not a directory.")
    return str(candidate)


def _tree(repo, sha):
    entries = {}
    total = 0
    for record in _git(repo, "ls-tree", "-r", "-l", "-z", sha).split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, object_id, size = meta.decode("ascii").split()
        decoded = path.decode("utf-8", "strict")
        _safe_path(decoded)
        if mode not in ("100644", "100755") or kind != "blob":
            raise EvidenceError("Version 0.1 supports regular files only (no symlinks/submodules): " + decoded)
        total += int(size)
        if total > MAX_ARCHIVE or len(entries) >= 100_000:
            raise EvidenceError("Snapshot exceeds 128 MiB or 100,000 files.")
        entries[decoded] = (mode, object_id)
    return entries


def _snapshot(repo, sha, root):
    entries = _tree(repo, sha)
    # git archive applies export-ignore/export-subst; these can change test
    # outcomes. Read the committed blobs directly, with no checkout filters.
    request = b"".join(object_id.encode("ascii") + b"\n" for _, object_id in entries.values())
    raw = _git(repo, "cat-file", "--batch", input_data=request, timeout=60) if entries else b""
    root.mkdir(parents=True)
    stream = io.BytesIO(raw)
    try:
        for path, (mode, expected_id) in entries.items():
            object_id, kind, size_text = stream.readline().decode("ascii").split()
            size = int(size_text)
            if object_id != expected_id or kind != "blob" or not 0 <= size <= MAX_ARCHIVE:
                raise EvidenceError("Unexpected Git batch object.")
            content = stream.read(size)
            if len(content) != size or stream.read(1) != b"\n":
                raise EvidenceError("Incomplete Git batch object.")
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(content)
            destination.chmod(0o755 if mode == "100755" else 0o644)
        if stream.read(1):
            raise EvidenceError("Unexpected trailing Git batch data.")
    except (OSError, ValueError) as exc:
        raise EvidenceError("Cannot create snapshot: %s" % exc) from exc


def validate_output(output: Path, repo: Path | None = None) -> Path:
    """Resolve aliases before reserving a fresh non-metadata directory."""
    original = Path(output).absolute()
    if original.exists() or original.is_symlink():
        raise EvidenceError("Output must be a new directory.")
    output = original.resolve()
    if ".git" in (part.casefold() for part in output.parts):
        raise EvidenceError("Output cannot be inside Git metadata.")
    existing_parent = output.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    candidates = [existing_parent] + ([repo] if repo else [])
    for candidate in candidates:
        for option in ("--absolute-git-dir", "--git-common-dir"):
            try:
                git_dir = _git(candidate, "rev-parse", "--path-format=absolute", option).decode().strip()
            except EvidenceError:
                continue
            metadata = Path(git_dir).resolve()
            if output == metadata or metadata in output.parents:
                raise EvidenceError("Output cannot be inside Git metadata.")
    return output


def _execute(argv, cwd, destination, timeout):
    began = time.monotonic()
    issues, exit_code, timed_out = [], None, False
    with (destination / "stdout.log").open("xb") as stdout, (destination / "stderr.log").open("xb") as stderr:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=(os.name == "posix"))
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                issues.append("Test command timed out.")
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait()
                exit_code = proc.returncode
        except OSError as exc:
            issues.append("Cannot execute test command: %s" % exc)
    return exit_code, timed_out, time.monotonic() - began, issues


def _one_run(repo, sha, overlay_data, command, output, side, iteration, timeout):
    destination = output / "runs" / ("%s-%d" % (side, iteration))
    destination.mkdir(parents=True)
    junit = destination / "junit.xml"
    with tempfile.TemporaryDirectory(prefix="regression-witness-") as temporary:
        checkout = Path(temporary) / "checkout"
        _snapshot(repo, sha, checkout)
        for path, (mode, content) in overlay_data.items():
            target = checkout / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not target.is_file():
                raise EvidenceError("Overlay collides with a directory: " + path)
            target.write_bytes(content)
            target.chmod(0o755 if mode == "100755" else 0o644)
        argv = [arg.replace("{junit}", str(junit)) for arg in command]
        code, timed_out, elapsed, issues = _execute(argv, checkout, destination, timeout)
        for path, (_, content) in overlay_data.items():
            target = checkout / path
            if target.is_symlink() or not target.is_file() or target.read_bytes() != content:
                issues.append("Selected test input changed during execution: " + path)
        try:
            parsed = parse_junit(junit)
            issues.extend(parsed["issues"])
        except (EvidenceError, OSError) as exc:
            issues.append("JUnit evidence unavailable: %s" % exc)
            parsed = {"cases": {}, "issues": [], "sha256": None}
    return {
        "side": side, "iteration": iteration, "commit": sha, "command": argv,
        "exit_code": code, "timed_out": timed_out, "duration_s": round(elapsed, 6),
        "issues": issues, "cases": parsed["cases"], "sha256": parsed["sha256"],
        "junit": str(junit.relative_to(output)),
        "stdout": str((destination / "stdout.log").relative_to(output)),
        "stderr": str((destination / "stderr.log").relative_to(output)),
    }


def run_witness(repo: Path, base: str, head: str, overlay: list[str],
                tests: list[str], command: list[str], output: Path,
                timeout: float = 60, repeats: int = 1) -> dict:
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise EvidenceError("Repository directory does not exist.")
    output = validate_output(output, repo)
    if not command or not any("{junit}" in part for part in command):
        raise EvidenceError("Command must include the literal {junit} report-path placeholder.")
    if not tests or len(set(tests)) != len(tests):
        raise EvidenceError("Specify at least one unique exact testcase ID.")
    if not math.isfinite(timeout) or timeout <= 0 or not 1 <= repeats <= 10:
        raise EvidenceError("Timeout must be positive and repeats must be between 1 and 10.")
    if not overlay:
        raise EvidenceError("Explicitly select at least one head test file to overlay.")
    paths = [_safe_path(path) for path in overlay]
    if len(paths) != len(set(paths)):
        raise EvidenceError("Duplicate overlay paths are not allowed.")
    # Resolve each revision once; moving branch names cannot change later runs.
    base_sha, head_sha = _revision(repo, base), _revision(repo, head)
    _tree(repo, base_sha)
    head_tree = _tree(repo, head_sha)
    overlay_data = {}
    for path in paths:
        if path not in head_tree:
            raise EvidenceError("Overlay must name an exact tracked head file: " + path)
        mode, object_id = head_tree[path]
        content = _git(repo, "cat-file", "blob", object_id)
        if len(content) > MAX_OVERLAY:
            raise EvidenceError("Overlay file exceeds the 10 MiB limit: " + path)
        overlay_data[path] = (mode, content)
    output.mkdir(parents=True, exist_ok=False)
    runs, attempts = [], []
    for iteration in range(1, repeats + 1):
        pair = []
        for side, sha in (("base", base_sha), ("head", head_sha)):
            try:
                run = _one_run(repo, sha, overlay_data, command, output, side, iteration, timeout)
            except (EvidenceError, OSError, UnicodeError) as exc:
                run = {
                    "side": side, "iteration": iteration, "commit": sha, "command": command,
                    "exit_code": None, "timed_out": False, "duration_s": 0,
                    "issues": ["Snapshot preparation failed: %s" % exc], "cases": {}, "sha256": None,
                }
            pair.append(run)
            runs.append(run)
        before, after = pair
        result = evaluate(before, after, tests, before["exit_code"], after["exit_code"])
        # Missing exit codes can be legitimate for report-only comparison, never for execution.
        if any(run["exit_code"] is None or run["issues"] for run in pair):
            result["verdict"] = "INCONCLUSIVE"
            result["reason"] = "Execution or report integrity issues prevent a reliable observation."
        attempts.append(result)
    final = dict(attempts[0])
    if any(result["verdict"] != final["verdict"] or result["transitions"] != final["transitions"] for result in attempts[1:]):
        final["verdict"] = "INCONCLUSIVE"
        final["reason"] = "Repeated runs produced inconsistent outcomes; inspect individual runs."
    report = {
        "schema_version": 1, "tool_version": "0.1.0", "mode": "executed",
        **final, "commits": {"base": base_sha, "head": head_sha},
        "overlay_hashes": {path: hashlib.sha256(content).hexdigest() for path, (_, content) in overlay_data.items()},
        "command_template": command, "selected_tests": tests, "repeats": repeats, "timeout_s": timeout,
        "runtime": {"python": platform.python_version(), "platform": platform.system()},
        "runs": runs, "attempts": attempts,
    }
    write_reports(output, report)
    return report
