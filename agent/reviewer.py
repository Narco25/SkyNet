"""Verification of generated code.

IMPORTANT: this is defense in depth, not a real sandbox. Test runs execute
LLM-written code as your user. The measures below (minimal environment,
throwaway HOME, no stdin, timeouts, POSIX resource limits, killing the whole
process group) limit accidents and runaway code. They do NOT stop deliberate
file or network access. For real isolation, run the pytest command inside a
container with networking disabled.
"""

import os
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from agent.context import clip
from agent.tools import resolve_project, resolve_project_file

DEFAULT_TIMEOUT = 60
MAX_OUTPUT_CHARS = 6000
MAX_ERROR_CHARS = 4000

MAX_MEMORY_BYTES = 2 * 1024**3
MAX_FILE_WRITE_BYTES = 50 * 1024**2

# Environment variables that are safe and sometimes necessary to pass through.
_PASSTHROUGH_ENV = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "LANG",
    "LC_ALL",
)


@dataclass
class CommandResult:
    command: list[str]
    return_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.return_code == 0 and not self.timed_out

    def combined_output(self) -> str:
        parts = [self.stdout.strip(), self.stderr.strip()]
        return "\n".join(part for part in parts if part)


@dataclass
class ReviewResult:
    compile: CommandResult
    tests: CommandResult | None

    @property
    def passed(self) -> bool:
        if not self.compile.passed:
            return False

        return self.tests is not None and self.tests.passed

    def error_text(self) -> str:
        """The failure details to hand to the repair agent."""
        if not self.compile.passed:
            return clip(self.compile.combined_output(), MAX_ERROR_CHARS)

        if self.tests is not None and not self.tests.passed:
            return clip(self.tests.combined_output(), MAX_ERROR_CHARS)

        return ""


def _minimal_env(home: str) -> dict[str, str]:
    env = {
        key: os.environ[key] for key in _PASSTHROUGH_ENV if key in os.environ
    }

    env.update(
        {
            "HOME": home,
            "USERPROFILE": home,
            "TMPDIR": home,
            "TEMP": home,
            "TMP": home,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONHASHSEED": "0",
        }
    )

    return env


def _limit_resources(cpu_seconds: int):
    """Return a preexec_fn applying resource limits (POSIX only)."""

    def apply() -> None:
        import resource

        limits = (
            ("RLIMIT_CPU", cpu_seconds),
            ("RLIMIT_FSIZE", MAX_FILE_WRITE_BYTES),
            ("RLIMIT_AS", MAX_MEMORY_BYTES),
        )

        for name, value in limits:
            try:
                resource.setrlimit(getattr(resource, name), (value, value))
            except (ValueError, OSError, AttributeError):
                # Not every platform supports every limit (e.g. macOS RLIMIT_AS).
                pass

    return apply


def _kill(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_command(
    project_slug: str,
    command: list[str],
    timeout: int = DEFAULT_TIMEOUT,
) -> CommandResult:
    """Run a Python command inside the project directory.

    Only commands that start with the current interpreter are allowed.
    """
    if not command or command[0] != sys.executable:
        raise ValueError("Only commands run with the current Python interpreter are allowed.")

    project_path = resolve_project(project_slug)

    with tempfile.TemporaryDirectory(prefix="skynet-home-") as home:
        extra = {}

        if os.name == "posix":
            extra["start_new_session"] = True
            extra["preexec_fn"] = _limit_resources(timeout + 5)

        process = subprocess.Popen(
            command,
            cwd=project_path,
            env=_minimal_env(home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **extra,
        )

        timed_out = False

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(process)

            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""

        if timed_out:
            stderr = f"{stderr}\nTimed out after {timeout}s and was killed. " \
                     "The code may contain an infinite loop or be waiting for input."

        return CommandResult(
            command=command,
            return_code=-1 if timed_out else process.returncode,
            stdout=clip(stdout or "", MAX_OUTPUT_CHARS),
            stderr=clip(stderr or "", MAX_OUTPUT_CHARS),
            timed_out=timed_out,
        )


def compile_review(project_slug: str, relative_path: str) -> CommandResult:
    """Syntax-check a file in-process. Compiling never executes the code."""
    command = ["compile", relative_path]
    file_path = resolve_project_file(project_slug, relative_path)

    if not file_path.is_file():
        return CommandResult(command, 1, "", f"File does not exist: {relative_path}")

    try:
        compile(file_path.read_text(encoding="utf-8"), relative_path, "exec")
    except SyntaxError as error:
        detail = (error.text or "").rstrip()
        message = f"SyntaxError in {relative_path}, line {error.lineno}: {error.msg}"
        return CommandResult(command, 1, "", f"{message}\n{detail}".rstrip())
    except (ValueError, RecursionError) as error:
        return CommandResult(command, 1, "", f"Could not compile {relative_path}: {error}")

    return CommandResult(command, 0)


def run_tests(
    project_slug: str,
    target: str = "tests",
    stop_on_first_failure: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
) -> CommandResult:
    """Run pytest on one test file, or on the whole tests/ directory."""
    project_path = resolve_project(project_slug)
    target_path = resolve_project_file(project_slug, target)

    if not target_path.exists():
        return CommandResult(
            ["pytest", target],
            1,
            "",
            f"Verification failed: {target} not found.",
        )

    relative = target_path.relative_to(project_path).as_posix()

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-p",
        "no:cacheprovider",
    ]

    if stop_on_first_failure:
        command.append("-x")

    command.append(relative)

    return run_command(project_slug, command, timeout=timeout)


def run_full_suite(project_slug: str) -> CommandResult:
    """Run every test, without stopping at the first failure."""
    return run_tests(project_slug, "tests", stop_on_first_failure=False, timeout=120)


def review_change(
    project_slug: str,
    relative_path: str,
    test_path: str,
) -> ReviewResult:
    """Compile the changed file, then run only the tests for this task."""
    compile_result = compile_review(project_slug, relative_path)

    if not compile_result.passed:
        return ReviewResult(compile=compile_result, tests=None)

    return ReviewResult(
        compile=compile_result,
        tests=run_tests(project_slug, test_path),
    )


def assess_red_state(
    result: CommandResult,
    import_name: str,
    source_existed: bool,
) -> tuple[bool, str]:
    """Decide whether freshly written tests are in a healthy 'red' state.

    Before implementation, good tests should fail by assertion or by being
    unable to import the module under test. Tests that pass without an
    implementation, fail to compile, collect nothing, or need unavailable
    third-party modules are broken and should be rewritten.

    Returns (ok, message). The message is addressed to the test writer.
    """
    output = result.combined_output()

    if result.timed_out:
        return False, "The tests timed out. Remove any infinite loops or blocking calls."

    code = result.return_code

    if code == 0:
        if source_existed:
            return True, "Tests already pass against the existing file."

        return False, (
            f"The tests passed even though `{import_name}` does not exist yet, "
            "so they do not exercise the module. Import the module under test "
            "and assert on its behavior."
        )

    if code == 5:
        return False, (
            "pytest collected no tests. Write functions named test_* "
            "at module level."
        )

    if code == 1:
        return True, "Tests fail as expected before implementation."

    if code == 2:
        if "SyntaxError" in output:
            return False, f"The test file has a syntax error:\n{clip(output, 1500)}"

        missing = re.search(r"No module named '([^']+)'", output)

        if missing:
            name = missing.group(1)

            if name == import_name or import_name.startswith(name + "."):
                return True, "Tests fail as expected: the module does not exist yet."

            return False, (
                f"The tests import `{name}`, which is not available. Use only "
                f"pytest, the standard library, and the module under test "
                f"`{import_name}`."
            )

        if "ImportError" in output:
            return True, "Tests fail as expected: imported names do not exist yet."

        return False, f"pytest could not collect the tests:\n{clip(output, 1500)}"

    return False, f"pytest exited with unexpected code {code}:\n{clip(output, 1500)}"