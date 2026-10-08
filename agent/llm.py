"""LLM access and the planner, builder, and repair agents.

Talks to Ollama's native /api/chat endpoint (standard library only) because,
unlike the OpenAI-compatible endpoint, it supports JSON-schema constrained
output and a configurable context window (num_ctx).

Configuration (environment variables):
    SKYNET_OLLAMA_URL     default http://localhost:11434
    SKYNET_MODEL          default qwen2.5-coder:7b
    SKYNET_TEMPERATURE    default 0.2
    SKYNET_NUM_CTX        default 8192 (lower to 4096 if memory is tight)
    SKYNET_LLM_TIMEOUT    seconds per request, default 600
"""

import json
import os
import socket
import urllib.error
import urllib.request

from pydantic import BaseModel, ValidationError

from agent.context import clip
from agent.models import FileChangeProposal, Task, TaskPlan

BASE_URL = os.environ.get("SKYNET_OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("SKYNET_MODEL", "qwen2.5-coder:7b")
TEMPERATURE = float(os.environ.get("SKYNET_TEMPERATURE", "0.2"))
NUM_CTX = int(os.environ.get("SKYNET_NUM_CTX", "8192"))
TIMEOUT = float(os.environ.get("SKYNET_LLM_TIMEOUT", "600"))

MAX_RETRIES = 2
MAX_ERROR_IN_PROMPT = 4000

# Bypass any HTTP(S)_PROXY settings: Ollama is a local service.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_schema_supported = True


class LLMUnavailableError(RuntimeError):
    """Ollama is unreachable, timed out, or does not have the model."""


class LLMOutputError(ValueError):
    """The model kept returning output that failed validation."""


class _SchemaRejected(Exception):
    """This Ollama version rejected the JSON schema in `format`."""


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", errors="replace")
        return json.loads(body).get("error", body)
    except (ValueError, AttributeError):
        return str(error)


def _post_chat(messages: list[dict], response_format) -> str:
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": TEMPERATURE, "num_ctx": NUM_CTX},
    }

    if response_format is not None:
        payload["format"] = response_format

    request = urllib.request.Request(
        f"{BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with _opener.open(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = _error_detail(error)

        if isinstance(response_format, dict) and error.code in (400, 500):
            raise _SchemaRejected(detail) from error

        if error.code == 404:
            raise LLMUnavailableError(
                f"Ollama could not find model '{MODEL}': {detail}\n"
                f"Pull it with: ollama pull {MODEL}"
            ) from error

        raise LLMUnavailableError(
            f"Ollama returned HTTP {error.code}: {detail}"
        ) from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise LLMUnavailableError(
                f"Ollama did not answer within {TIMEOUT:.0f}s. "
                "Raise SKYNET_LLM_TIMEOUT or use a smaller model."
            ) from error

        raise LLMUnavailableError(
            f"Cannot reach Ollama at {BASE_URL} ({error.reason}). "
            "Start it with `ollama serve`."
        ) from error
    except (TimeoutError, socket.timeout) as error:
        raise LLMUnavailableError(
            f"Ollama did not answer within {TIMEOUT:.0f}s. "
            "Raise SKYNET_LLM_TIMEOUT or use a smaller model."
        ) from error
    except json.JSONDecodeError as error:
        raise LLMUnavailableError("Ollama returned a response that is not JSON.") from error

    if "error" in data:
        raise LLMUnavailableError(f"Ollama error: {data['error']}")

    return data.get("message", {}).get("content", "") or ""


def chat_messages(messages: list[dict], response_format=None) -> str:
    """Send a conversation to the model and return the reply text.

    `response_format` may be None, "json", or a JSON-schema dict. If this
    Ollama version rejects schemas, falls back to plain "json" mode once
    and remembers it.
    """
    global _schema_supported

    fmt = response_format

    if isinstance(fmt, dict) and not _schema_supported:
        fmt = "json"

    try:
        return _post_chat(messages, fmt)
    except _SchemaRejected as rejected:
        _schema_supported = False
        print(
            "Note: this Ollama version rejected schema-constrained output "
            f"({clip(str(rejected), 200)}); falling back to plain JSON mode."
        )
        return _post_chat(messages, "json")


def chat(system: str, user: str, json_mode: bool = False) -> str:
    """Single-turn convenience wrapper."""
    return chat_messages(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format="json" if json_mode else None,
    )


def _extract_json(raw: str) -> str:
    """Strip Markdown fences and chatter around a JSON object."""
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        return text[start : end + 1]

    return text


def structured_call(
    system: str,
    user: str,
    model_class: type[BaseModel],
    what: str,
    max_retries: int = MAX_RETRIES,
):
    """Ask the model for JSON matching `model_class`, retrying with feedback.

    On a validation failure the model is shown its own reply and the exact
    error, and asked to correct it. Raises LLMOutputError (a ValueError) if
    all attempts fail.
    """
    schema = model_class.model_json_schema()

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    last_error = ""
    last_raw = ""

    for attempt in range(max_retries + 1):
        raw = chat_messages(messages, response_format=schema)
        last_raw = raw

        try:
            return model_class.model_validate_json(_extract_json(raw))
        except ValidationError as error:
            last_error = str(error)

        if attempt < max_retries:
            print(f"  {what}: invalid output, asking for a correction "
                  f"({attempt + 1}/{max_retries})...")

            messages.append({"role": "assistant", "content": clip(raw, 3000)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid. Problems:\n"
                        f"{clip(last_error, 1500)}\n\n"
                        "Return the complete corrected JSON object only."
                    ),
                }
            )

    raise LLMOutputError(
        f"The {what} returned invalid output after {max_retries + 1} attempts.\n"
        f"Last validation error:\n{clip(last_error, 1500)}\n\n"
        f"Last raw response:\n{clip(last_raw, 1500)}"
    )


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

PLANNER_SYSTEM = """You are the Puppetmaster, a careful software project planner.

Break the user's goal into a small number of concrete implementation tasks.

Rules:
- Create between 2 and 5 tasks.
- Each task creates exactly one Python source file: a relative path using
  forward slashes, ending in .py, with every folder and the file name made of
  letters, digits, and underscores (for example: project_manager.py).
- Never plan files inside tests/ and never plan conftest.py. Tests are written
  separately.
- Order the tasks so that each file only imports from files created by EARLIER tasks.
- For every task, write an "interface": the public function and class
  signatures the file must provide, each with a one-line description of its
  behavior, including which exceptions it raises. The tests and the
  implementation are written independently from this interface, so be exact
  about names, parameters, and return values.
- Use only the Python standard library.
- Do not include shell commands, package installation, or test execution as tasks.
- Return only valid JSON."""

PLAN_SHAPE = """{
  "project_name": "short_project_name",
  "summary": "one paragraph explaining the approach",
  "tasks": [
    {
      "id": 1,
      "title": "Short task title",
      "description": "What must be implemented",
      "file_path": "relative/path/file.py",
      "interface": "def name(arg: type) -> type  # behavior, exceptions raised",
      "acceptance_criteria": "How to tell the task is complete"
    }
  ]
}"""

BUILDER_SYSTEM = """You are a Python software builder.

Implement exactly one requested file.

Rules:
- Write complete, runnable Python code for the whole file.
- Follow the interface exactly: the same names, parameters, return values,
  and exceptions.
- The provided tests already exist and are the specification. Make them pass.
  Never include, modify, or redefine the tests.
- Import from other project files only the names listed in the project overview.
- Use only the Python standard library.
- If modifying an existing file, return the entire updated file.
- Do not invent unrelated features.
- Do not include Markdown code fences inside the file content.
- Return only valid JSON."""

REPAIR_SYSTEM = """You are a Python software repair agent.

Fix one verification failure in one Python file.

Rules:
- Return the entire updated file.
- The provided tests are the specification. Fix the implementation, not the tests.
- Change only what is necessary to fix the reported error.
- Keep the interface (names, parameters, return values, exceptions) unchanged.
- Do not include Markdown code fences inside the file content.
- Return only valid JSON."""

PROPOSAL_SHAPE = """{
  "explanation": "Brief explanation of the change",
  "full_file_content": "The complete file content"
}"""


def _task_block(task: Task) -> str:
    return (
        f"Target file: {task.file_path}\n"
        f"Task: {task.title}\n\n"
        f"Description:\n{task.description}\n\n"
        f"Interface:\n{task.interface}\n\n"
        f"Acceptance criteria:\n{task.acceptance_criteria}"
    )


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

def plan_tasks(goal: str) -> TaskPlan:
    user = (
        f"Project goal:\n{goal}\n\n"
        f"Return JSON exactly in this shape:\n{PLAN_SHAPE}"
    )

    return structured_call(PLANNER_SYSTEM, user, TaskPlan, "planner")


def propose_file_change(
    goal: str,
    plan_summary: str,
    task: Task,
    test_content: str,
    overview: str,
    existing_file_content: str,
) -> FileChangeProposal:
    user = (
        f"Overall goal:\n{goal}\n\n"
        f"Project plan:\n{plan_summary}\n\n"
        f"{_task_block(task)}\n\n"
        f"Other project files (signatures only):\n{overview}\n\n"
        f"Tests that must pass (do not modify):\n```python\n{test_content}\n```\n\n"
        f"Existing content of {task.file_path}, which may be empty:\n"
        f"```python\n{existing_file_content}\n```\n\n"
        f"Return JSON exactly in this shape:\n{PROPOSAL_SHAPE}"
    )

    return structured_call(BUILDER_SYSTEM, user, FileChangeProposal, "builder")


def repair_file_change(
    goal: str,
    plan_summary: str,
    task: Task,
    test_content: str,
    overview: str,
    existing_file_content: str,
    verification_error: str,
) -> FileChangeProposal:
    user = (
        f"Overall goal:\n{goal}\n\n"
        f"Project plan:\n{plan_summary}\n\n"
        f"{_task_block(task)}\n\n"
        f"Other project files (signatures only):\n{overview}\n\n"
        f"Tests that must pass (do not modify):\n```python\n{test_content}\n```\n\n"
        f"Current content of {task.file_path}:\n"
        f"```python\n{existing_file_content}\n```\n\n"
        f"Verification error:\n{clip(verification_error, MAX_ERROR_IN_PROMPT)}\n\n"
        f"Return JSON exactly in this shape:\n{PROPOSAL_SHAPE}"
    )

    return structured_call(REPAIR_SYSTEM, user, FileChangeProposal, "repair agent")
