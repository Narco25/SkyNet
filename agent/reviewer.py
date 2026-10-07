import subprocess
import sys
from pathlib import Path

from agent.tools import resolve_project, resolve_project_file


def run_allowlisted_command(
    project_slug: str,
    command: list[str],
    timeout: int = 60,
) -> dict:
    project_path = resolve_project(project_slug)

    completed = subprocess.run(
        command,
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )

    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "passed": completed.returncode == 0,
    }


def compile_review(project_slug: str, relative_path: str) -> dict:
    file_path = resolve_project_file(project_slug, relative_path)

    if not file_path.exists():
        return {
            "passed": False,
            "return_code": 1,
            "stdout": "",
            "stderr": f"File does not exist: {relative_path}",
        }

    return run_allowlisted_command(
        project_slug,
        ["python", "-m", "py_compile", str(file_path)],
    )


def run_tests(project_slug: str) -> dict:
    project_path = resolve_project(project_slug)
    tests_dir = project_path / "tests"

    if not tests_dir.exists():
        return {
            "passed": False,
            "return_code": 1,
            "stdout": "",
            "stderr": "Verification failed: no tests directory found.",
        }

    return run_allowlisted_command(
        project_slug,
        ["python", "-m", "pytest", "-q", "tests"],
    )


def review_change(project_slug: str, relative_path: str) -> dict:
    compile_result = compile_review(project_slug, relative_path)

    if not compile_result["passed"]:
        return {
            "passed": False,
            "compile": compile_result,
            "tests": None,
        }

    test_result = run_tests(project_slug)

    return {
        "passed": test_result["passed"],
        "compile": compile_result,
        "tests": test_result,
    }

