from agent.context import clip, import_name_for
from agent.llm import structured_call
from agent.models import FileChangeProposal, Task

TEST_WRITER_SYSTEM = """You are a Python test-writing agent.

Write pytest tests for exactly one implementation file.

Rules:
- Test observable behavior through the public interface, not implementation details.
- Use ONLY the names listed in the interface; do not invent extra functions.
- Cover valid inputs, invalid inputs, and edge cases, in at most about 15 tests.
- Import the module under test exactly as instructed. Use only pytest, the
  Python standard library, and the module under test.
- Use the tmp_path fixture for any files. Never touch paths outside it.
- Do not execute shell commands, use subprocess, call input(), sleep, or
  access the internet.
- If modifying an existing test file, return the entire updated file.
- Do not include Markdown code fences inside the file content.
- Return only valid JSON."""

TEST_SHAPE = """{
  "explanation": "Brief explanation of the tests",
  "full_file_content": "The complete pytest test file"
}"""


def _task_block(task: Task, test_path: str) -> str:
    import_name = import_name_for(task.file_path)

    return (
        f"Target file: {task.file_path}\n"
        f"Module under test: import it as `{import_name}`. Tests run from the "
        f"project root, so `from {import_name} import <name>` works once the "
        "module exists.\n"
        f"These tests will be saved as: {test_path}\n\n"
        f"Task: {task.title}\n\n"
        f"Description:\n{task.description}\n\n"
        f"Interface:\n{task.interface}\n\n"
        f"Acceptance criteria:\n{task.acceptance_criteria}"
    )


def generate_test_file(
    goal: str,
    plan_summary: str,
    task: Task,
    test_path: str,
    overview: str,
    existing_test_content: str,
) -> FileChangeProposal:
    user = (
        f"Overall goal:\n{goal}\n\n"
        f"Project plan:\n{plan_summary}\n\n"
        f"{_task_block(task, test_path)}\n\n"
        f"Other project files (signatures only):\n{overview}\n\n"
        f"Existing test content, which may be empty:\n"
        f"```python\n{existing_test_content}\n```\n\n"
        f"Return JSON exactly in this shape:\n{TEST_SHAPE}"
    )

    return structured_call(
        TEST_WRITER_SYSTEM, user, FileChangeProposal, "test writer"
    )


def repair_test_file(
    goal: str,
    plan_summary: str,
    task: Task,
    test_path: str,
    overview: str,
    current_tests: str,
    problem: str,
) -> FileChangeProposal:
    """Rewrite tests that were found to be broken before implementation."""
    user = (
        f"Overall goal:\n{goal}\n\n"
        f"Project plan:\n{plan_summary}\n\n"
        f"{_task_block(task, test_path)}\n\n"
        f"Other project files (signatures only):\n{overview}\n\n"
        f"Your previous test file:\n```python\n{current_tests}\n```\n\n"
        f"Problem found when running it before implementation:\n"
        f"{clip(problem, 3000)}\n\n"
        "Fix the test file and return the complete updated file.\n\n"
        f"Return JSON exactly in this shape:\n{TEST_SHAPE}"
    )

    return structured_call(
        TEST_WRITER_SYSTEM, user, FileChangeProposal, "test writer"
    )