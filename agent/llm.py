import json

from openai import OpenAI
from pydantic import ValidationError

from agent.models import FileChangeProposal, TaskPlan

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
)

MODEL = "qwen2.5-coder:7b"


def chat(system: str, user: str, json_mode: bool = False) -> str:
    kwargs = {}

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **kwargs,
    )

    return response.choices[0].message.content or ""


def plan_tasks(goal: str) -> TaskPlan:
    system = """You are the Puppetmaster, a careful software project planner.

Break the user's goal into a small number of concrete implementation tasks.

Rules:
- Create between 2 and 5 tasks.
- Each task must create or modify exactly one file.
- Use file paths relative to the project workspace.
- Do not include shell commands, package installation, or test execution as tasks.
- Keep each task small and independently understandable.
- Return only valid JSON."""

    user = f"""Project goal:
{goal}

Return JSON exactly in this shape:
{{
  "project_name": "short_project_name",
  "summary": "one paragraph explaining the approach",
  "tasks": [
    {{
      "id": 1,
      "title": "Short task title",
      "description": "What must be implemented",
      "file_path": "relative/path/file.py",
      "acceptance_criteria": "How to tell the task is complete"
    }}
  ]
}}"""

    raw = chat(system, user, json_mode=True)

    try:
        return TaskPlan.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError(
            "The planner returned invalid JSON.\n"
            f"Raw response:\n{raw}\n\nValidation error:\n{error}"
        ) from error


def propose_file_change(
    goal: str,
    plan_summary: str,
    task_title: str,
    task_description: str,
    acceptance_criteria: str,
    existing_file_content: str,
) -> FileChangeProposal:
    system = """You are a Python software builder.

Implement exactly one requested task.

Rules:
- Write complete, runnable Python code.
- Use only the Python standard library unless the task explicitly requires another dependency.
- If modifying an existing file, return the entire updated file.
- Do not invent unrelated features.
- Do not include Markdown code fences.
- Return only valid JSON."""

    user = f"""Overall goal:
{goal}

Project plan:
{plan_summary}

Task:
{task_title}

Task description:
{task_description}

Acceptance criteria:
{acceptance_criteria}

Existing file content, which may be empty:
```python
{existing_file_content}
```

Return JSON exactly in this shape:
{{
  "explanation": "Brief explanation of the change",
  "file_path": "relative/path/file.py",
  "full_file_content": "The complete new file content"
}}"""

    raw = chat(system, user, json_mode=True)

    try:
        proposal = FileChangeProposal.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError(
            "The builder returned invalid JSON.\n"
            f"Raw response:\n{raw}\n\nValidation error:\n{error}"
        ) from error

    return proposal

def repair_file_change(
    goal: str,
    plan_summary: str,
    task_title: str,
    task_description: str,
    acceptance_criteria: str,
    existing_file_content: str,
    verification_error: str,
) -> FileChangeProposal:
    system = """You are a Python software repair agent.

Fix exactly one verification failure in one Python file.

Rules:
- Return the entire updated file.
- Change only what is necessary to fix the reported error.
- Write complete, runnable Python code.
- Do not include Markdown code fences.
- Return only valid JSON."""

    user = f"""Goal:
{goal}

Project plan:
{plan_summary}

Task:
{task_title}

Task description:
{task_description}

Acceptance criteria:
{acceptance_criteria}

Current file content:
```python
{existing_file_content}
```

Verification error:
{verification_error}

Return JSON exactly in this shape:
{{
  "explanation": "Brief explanation of the repair",
  "file_path": "relative/path/file.py",
  "full_file_content": "The complete repaired file content"
}}"""

    raw = chat(system, user, json_mode=True)

    try:
        return FileChangeProposal.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError(
            "The repair agent returned invalid JSON.\n"
            f"Raw response:\n{raw}\n\nValidation error:\n{error}"
        ) from error