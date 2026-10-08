"""Tests for SkyNet itself. No Ollama needed: the model is replaced by a fake."""

import json
import re
import sys

import pytest
from pydantic import ValidationError

from agent import llm, orchestrator, reviewer, tools
from agent.context import extract_signatures, import_name_for, project_overview
from agent.models import FileChangeProposal, Task, TaskPlan, validate_source_path


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    projects = (tmp_path / "projects").resolve()
    projects.mkdir()
    monkeypatch.setattr(tools, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(tools, "LOG_DIR", (tmp_path / "logs").resolve())
    return projects


def make_task(task_id=1, path="mathops.py", title="Math ops"):
    return {
        "id": task_id,
        "title": title,
        "description": "d",
        "file_path": path,
        "interface": "def add(a, b) -> int  # sum",
        "acceptance_criteria": "adds",
    }


# ---------------------------------------------------------------- models

@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd", "../x.py", "a/../../x.py", "C:/x.py", "a\\b.py",
        "notes.txt", "tests/helper.py", "conftest.py", "test_thing.py",
        "thing_test.py", "my-module.py", "class.py", "a/b/c/d.py", "",
    ],
)
def test_validate_source_path_rejects(bad):
    with pytest.raises(ValueError):
        validate_source_path(bad)


def test_validate_source_path_accepts_and_normalises():
    assert validate_source_path("./pkg/mod.py") == "pkg/mod.py"


def test_plan_requires_between_two_and_five_unique_tasks():
    base = {"project_name": "p", "summary": "s"}

    with pytest.raises(ValidationError):
        TaskPlan.model_validate({**base, "tasks": [make_task()]})

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(
            {**base, "tasks": [make_task(1, "a.py"), make_task(2, "a.py")]}
        )

    with pytest.raises(ValidationError):
        TaskPlan.model_validate(
            {**base, "tasks": [make_task(1, "a.py"), make_task(1, "b.py")]}
        )

    plan = TaskPlan.model_validate(
        {**base, "tasks": [make_task(1, "a.py"), make_task(2, "b.py")]}
    )
    assert len(plan.tasks) == 2


def test_proposal_strips_code_fences_and_adds_newline():
    proposal = FileChangeProposal(full_file_content="```python\nx = 1\n```")
    assert proposal.full_file_content == "x = 1\n"

    with pytest.raises(ValidationError):
        FileChangeProposal(full_file_content="   \n")


# ----------------------------------------------------------------- tools

def test_path_guard(workspace):
    tools.create_project("demo")

    for bad in ["../other.py", "/etc/passwd", "a/../../x.py", "."]:
        with pytest.raises(ValueError):
            tools.resolve_project_file("demo", bad)

    with pytest.raises(ValueError):
        tools.validate_project_slug("Bad_Name")

    with pytest.raises(ValueError):
        tools.validate_project_slug("../escape")


def test_symlink_escape_blocked(workspace, tmp_path):
    project = tools.create_project("demo")
    outside = tmp_path / "outside"
    outside.mkdir()

    try:
        (project / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not available")

    with pytest.raises(ValueError):
        tools.resolve_project_file("demo", "link/secret.py")


def test_write_size_cap_and_listing_ignores_caches(workspace):
    tools.create_project("demo")

    with pytest.raises(ValueError):
        tools.write_project_file("demo", "big.py", "x" * (tools.MAX_FILE_BYTES + 1))

    tools.write_project_file("demo", "a.py", "x = 1\n")
    tools.write_project_file("demo", "__pycache__/a.pyc", "junk")
    assert tools.list_project_files("demo") == ["a.py"]


# --------------------------------------------------------------- context

def test_extract_signatures_and_overview(workspace):
    source = (
        "LIMIT = 3\n"
        "def add(a: int, b: int = 0) -> int:\n    '''Sum.'''\n    return a + b\n"
        "def _private(): pass\n"
        "class Box(Base):\n"
        "    def __init__(self, v): self.v = v\n"
        "    def get(self) -> int: return self.v\n"
        "    def _hidden(self): pass\n"
    )
    sigs = extract_signatures(source)
    assert "def add(a: int, b: int=0) -> int  # Sum." in sigs
    assert "_private" not in sigs and "_hidden" not in sigs
    assert "class Box(Base):" in sigs and "def __init__(self, v)" in sigs
    assert "LIMIT = ..." in sigs

    tools.create_project("demo")
    tools.write_project_file("demo", "pkg/mod.py", source)
    tools.write_project_file("demo", "tests/test_x.py", "def test_x(): pass\n")
    overview = project_overview("demo")
    assert "pkg.mod" in overview and "test_x" not in overview
    assert import_name_for("pkg/mod.py") == "pkg.mod"


# -------------------------------------------------------------- reviewer

def test_compile_review_reports_syntax_errors(workspace):
    tools.create_project("demo")
    tools.write_project_file("demo", "bad.py", "def f(:\n")
    result = reviewer.compile_review("demo", "bad.py")
    assert not result.passed and "SyntaxError" in result.stderr

    assert not reviewer.compile_review("demo", "missing.py").passed


def test_run_command_only_allows_current_interpreter(workspace):
    tools.create_project("demo")

    with pytest.raises(ValueError):
        reviewer.run_command("demo", ["rm", "-rf", "/"])


def test_timeout_is_reported_not_raised(workspace):
    tools.create_project("demo")
    tools.write_project_file(
        "demo", "tests/test_loop.py", "def test_loop():\n    while True:\n        pass\n"
    )
    result = reviewer.run_tests("demo", "tests/test_loop.py", timeout=3)
    assert result.timed_out and not result.passed
    assert "Timed out" in result.stderr


def test_environment_is_minimal(workspace, monkeypatch):
    monkeypatch.setenv("SECRET_API_KEY", "hunter2")
    tools.create_project("demo")
    tools.write_project_file(
        "demo",
        "tests/test_env.py",
        "import os\n\ndef test_env():\n    assert 'SECRET_API_KEY' not in os.environ\n",
    )
    assert reviewer.run_tests("demo", "tests/test_env.py").passed


@pytest.mark.parametrize(
    "test_source, source_existed, expected_ok",
    [
        ("from mathops import add\ndef test_a():\n    assert add(1, 2) == 3\n", False, True),
        ("def test_a():\n    assert True\n", False, False),          # vacuous
        ("def test_a(:\n", False, False),                            # syntax error
        ("import numpy\ndef test_a(): pass\n", False, False),        # missing 3rd-party
        ("x = 1\n", False, False),                                   # nothing collected
        ("def test_a():\n    assert True\n", True, True),            # existing code
    ],
)
def test_assess_red_state(workspace, test_source, source_existed, expected_ok):
    tools.create_project("demo")
    tools.write_project_file("demo", "tests/test_t.py", test_source)
    result = reviewer.run_tests("demo", "tests/test_t.py")
    ok, message = reviewer.assess_red_state(result, "mathops", source_existed)
    assert ok is expected_ok, message


# ------------------------------------------------------------ llm layer

def test_structured_call_retries_with_feedback(monkeypatch):
    replies = iter(
        [
            "not json at all",
            '```json\n{"explanation": "x", "full_file_content": ""}\n```',
            '{"explanation": "x", "full_file_content": "y = 1"}',
        ]
    )
    seen = []

    def fake_chat(messages, response_format=None):
        seen.append(len(messages))
        return next(replies)

    monkeypatch.setattr(llm, "chat_messages", fake_chat)

    proposal = llm.structured_call("sys", "usr", FileChangeProposal, "builder")
    assert proposal.full_file_content == "y = 1\n"
    assert seen == [2, 4, 6]  # the conversation grows with corrections


def test_structured_call_gives_up(monkeypatch):
    monkeypatch.setattr(llm, "chat_messages", lambda m, response_format=None: "nope")

    with pytest.raises(llm.LLMOutputError):
        llm.structured_call("s", "u", FileChangeProposal, "builder", max_retries=1)


# ------------------------------------------------------- end to end run

class FakeModel:
    """Plays every agent. The first mathops implementation is deliberately wrong."""

    PLAN = {
        "project_name": "demo",
        "summary": "two small modules",
        "tasks": [
            make_task(1, "mathops.py", "Math ops"),
            {**make_task(2, "stats.py", "Stats"), "interface": "def mean(xs) -> float"},
        ],
    }

    TESTS = {
        "mathops.py": "from mathops import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        "stats.py": "from stats import mean\n\ndef test_mean():\n    assert mean([1, 2, 3]) == 2\n",
    }

    CODE = {
        "mathops.py": "def add(a, b):\n    return a + b\n",
        "stats.py": (
            "from mathops import add\n\n"
            "def mean(xs):\n    total = 0\n    for x in xs:\n        total = add(total, x)\n"
            "    return total / len(xs)\n"
        ),
    }

    def __init__(self):
        self.calls = []

    def __call__(self, messages, response_format=None):
        system, user = messages[0]["content"], messages[-1]["content"]
        target = re.search(r"Target file: (\S+)", messages[1]["content"])
        target = target.group(1) if target else None

        if "Puppetmaster" in system:
            self.calls.append("plan")
            return json.dumps(self.PLAN)

        if "test-writing" in system:
            self.calls.append(f"tests:{target}")
            return json.dumps({"explanation": "t", "full_file_content": self.TESTS[target]})

        if "software builder" in system:
            self.calls.append(f"build:{target}")
            code = "def add(a, b):\n    return a - b\n" if target == "mathops.py" else self.CODE[target]
            return json.dumps({"explanation": "b", "full_file_content": code})

        if "repair agent" in system:
            self.calls.append(f"repair:{target}")
            assert "assert" in user  # the pytest failure was passed along
            return json.dumps({"explanation": "r", "full_file_content": self.CODE[target]})

        raise AssertionError("unexpected prompt")


def test_full_run_repairs_and_verifies(workspace, monkeypatch, capsys):
    fake = FakeModel()
    monkeypatch.setattr(llm, "chat_messages", fake)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    summary = orchestrator.run_puppetmaster("goal", "demo", auto_mode=True)

    assert summary.success
    assert [r.status for r in summary.results] == ["verified", "verified"]
    assert fake.calls == [
        "plan",
        "tests:mathops.py", "build:mathops.py", "repair:mathops.py",
        "tests:stats.py", "build:stats.py",
    ]
    assert summary.full_suite.passed

    events = [
        json.loads(line)["event_type"]
        for line in (tools.LOG_DIR / "agent_events.jsonl").read_text().splitlines()
    ]
    assert "plan_created" in events and "proposal_reviewed" in events
    assert events[-1] == "run_finished"


def test_rejection_stops_the_task(workspace, monkeypatch):
    monkeypatch.setattr(llm, "chat_messages", FakeModel())
    answers = iter(["y", "n", "y", "n"])  # accept tests, reject code, x2 tasks
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    summary = orchestrator.run_puppetmaster("goal", "demo", auto_mode=False)

    assert [r.status for r in summary.results] == ["rejected", "rejected"]
    assert not summary.success


def test_existing_project_requires_reuse(workspace, monkeypatch):
    tools.create_project("demo")
    tools.write_project_file("demo", "old.py", "x = 1\n")

    with pytest.raises(orchestrator.ProjectExistsError):
        orchestrator.run_puppetmaster("goal", "demo", auto_mode=True)


def test_eof_on_input_rejects(monkeypatch):
    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    proposal = FileChangeProposal(full_file_content="x = 1")
    assert orchestrator.ask_to_apply("a.py", proposal, auto_mode=True) is False
