from agent.llm import chat
from agent.models import FileChangeProposal
from pydantic import ValidationError


def generate_test_file(
    goal: str,
    plan_summary: str,
    task_title: str,
    task_description: str,
    acceptance_criteria: str,
    existing_test_content: str,
) -> FileChangeProposal:
    system = """You are a Python test-writing agent.

Write pytest tests for exactly one implementation task.

Rules:
- Put tests in tests/test_<unique_name>.py.
- Test observable behavior, not implementation details.
- Cover valid inputs, invalid inputs, and edge cases.
- Use only pytest and the Python standard library.
- Do not execute shell commands or access the internet.
- If modifying an existing test file, return the entire updated file.
- Do not include Markdown code fences.
- Return only valid JSON."""

    user = f"""Overall goal:
{goal}

Project plan:
{plan_summary}

Implementation task:
{task_title}

Task description:
{task_description}

Acceptance criteria:
{acceptance_criteria}

Existing test content, which may be empty:
```python
{existing_test_content}
```

Return JSON exactly in this shape:
{{
  "explanation": "Brief explanation of the tests",
  "file_path": "tests/test_<unique_name>.py",
  "full_file_content": "The complete pytest test file"
}}"""

    raw = chat(system, user, json_mode=True)

    try:
        return FileChangeProposal.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError(
            "The test writer returned invalid JSON.\n"
            f"Raw response:\n{raw}\n\nValidation error:\n{error}"
        ) from error