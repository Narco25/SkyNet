import keyword
from pathlib import PurePosixPath

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_TASKS = 2
MAX_TASKS = 5
MAX_PATH_DEPTH = 3


def validate_source_path(value: str) -> str:
    """Validate a planner-supplied path for an implementation file.

    Returns the normalised POSIX path or raises ValueError. The path must be
    relative, stay inside the project, end in .py, and be importable (every
    directory and the file stem must be a valid Python identifier). Names
    that pytest would collect as tests, and conftest.py, are rejected.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("file_path must be a non-empty string")

    if "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("file_path must use forward slashes and no drive letters")

    if value.startswith("/"):
        raise ValueError("file_path must be relative to the project root")

    path = PurePosixPath(value)

    if ".." in path.parts:
        raise ValueError("file_path must not contain '..'")

    if len(path.parts) > MAX_PATH_DEPTH:
        raise ValueError(f"file_path may be at most {MAX_PATH_DEPTH} levels deep")

    if path.suffix != ".py":
        raise ValueError("file_path must end in .py")

    for part in path.parts[:-1] + (path.stem,):
        if not part.isidentifier() or keyword.iskeyword(part):
            raise ValueError(
                f"'{part}' is not a valid Python module name; use letters, "
                "digits, and underscores"
            )

    if path.parts[0] == "tests":
        raise ValueError("implementation files must not live under tests/")

    name = path.name
    if name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py"):
        raise ValueError("implementation files must not look like test files")

    return path.as_posix()


class Task(BaseModel):
    id: int
    title: str
    description: str
    file_path: str
    interface: str
    acceptance_criteria: str

    @field_validator("title", "description", "interface", "acceptance_criteria")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("file_path")
    @classmethod
    def safe_source_path(cls, value: str) -> str:
        return validate_source_path(value)


class TaskPlan(BaseModel):
    project_name: str
    summary: str
    tasks: list[Task] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_tasks(self) -> "TaskPlan":
        count = len(self.tasks)

        if not MIN_TASKS <= count <= MAX_TASKS:
            raise ValueError(
                f"The plan must contain between {MIN_TASKS} and {MAX_TASKS} "
                f"tasks, but it has {count}."
            )

        ids = [task.id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("Task ids must be unique.")

        paths = [task.file_path for task in self.tasks]
        if len(set(paths)) != len(paths):
            raise ValueError("Each task must target a different file_path.")

        return self


class FileChangeProposal(BaseModel):
    """A proposed full-file replacement.

    The target path is deliberately NOT part of the model: the orchestrator
    decides where a proposal is written, so the model cannot redirect it.
    """

    explanation: str = ""
    full_file_content: str

    @field_validator("full_file_content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("full_file_content must not be empty")

        text = value.strip("\n")

        # Small models often wrap code in Markdown fences despite instructions.
        if text.lstrip().startswith("```"):
            lines = text.strip().splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)

        if not text.strip():
            raise ValueError("full_file_content must not be empty")

        return text if text.endswith("\n") else text + "\n"
