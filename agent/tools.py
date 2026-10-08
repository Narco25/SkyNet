import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Anchor everything to the repository root, not the current working directory,
# so running SkyNet from another folder does not scatter workspaces and logs.
ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = (ROOT / "workspace").resolve()
PROJECTS_ROOT = (WORKSPACE / "projects").resolve()
LOG_DIR = (ROOT / "logs").resolve()

MAX_SLUG_LENGTH = 64
MAX_FILE_BYTES = 200_000

IGNORED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    ".git",
}

_RUN_ID: str | None = None


def start_run() -> str:
    """Begin a new run; every later log_event call is tagged with its id."""
    global _RUN_ID
    _RUN_ID = uuid.uuid4().hex[:8]
    return _RUN_ID


def validate_project_slug(project_slug: str) -> str:
    if (
        not isinstance(project_slug, str)
        or len(project_slug) > MAX_SLUG_LENGTH
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_slug)
    ):
        raise ValueError(
            "Project name must use lowercase letters, numbers, and hyphens "
            f"(at most {MAX_SLUG_LENGTH} characters); for example: todo-app"
        )

    return project_slug


def resolve_project(project_slug: str) -> Path:
    project_slug = validate_project_slug(project_slug)
    project_path = (PROJECTS_ROOT / project_slug).resolve()

    if project_path.parent != PROJECTS_ROOT:
        raise ValueError(f"Invalid project path: {project_slug}")

    return project_path


def resolve_project_file(project_slug: str, relative_path: str) -> Path:
    """Resolve a path inside a project, following symlinks, or raise.

    The resolved path must be strictly inside the project directory.
    """
    project_path = resolve_project(project_slug)

    try:
        file_path = (project_path / relative_path).resolve()
    except (ValueError, OSError) as error:
        raise ValueError(f"Invalid path: {relative_path!r}") from error

    if project_path not in file_path.parents:
        raise ValueError(
            f"Refusing to access path outside project: {relative_path}"
        )

    return file_path


def create_project(project_slug: str) -> Path:
    project_path = resolve_project(project_slug)
    project_path.mkdir(parents=True, exist_ok=True)
    return project_path


def list_projects() -> list[str]:
    if not PROJECTS_ROOT.exists():
        return []

    return sorted(
        path.name
        for path in PROJECTS_ROOT.iterdir()
        if path.is_dir()
    )


def list_project_files(project_slug: str) -> list[str]:
    project_path = resolve_project(project_slug)

    if not project_path.exists():
        return []

    files = []

    for path in project_path.rglob("*"):
        relative = path.relative_to(project_path)

        if any(part in IGNORED_DIRS for part in relative.parts):
            continue

        if path.is_file():
            files.append(relative.as_posix())

    return sorted(files)


def read_project_file(project_slug: str, relative_path: str) -> str:
    file_path = resolve_project_file(project_slug, relative_path)

    if not file_path.is_file():
        return ""

    return file_path.read_text(encoding="utf-8")


def write_project_file(
    project_slug: str,
    relative_path: str,
    content: str,
) -> Path:
    file_path = resolve_project_file(project_slug, relative_path)

    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(
            f"Refusing to write {relative_path}: larger than "
            f"{MAX_FILE_BYTES} bytes."
        )

    if file_path.is_dir():
        raise ValueError(f"Refusing to overwrite a directory: {relative_path}")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def log_event(event_type: str, payload: dict) -> None:
    LOG_DIR.mkdir(exist_ok=True)

    log_file = LOG_DIR / "agent_events.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": _RUN_ID,
        "event_type": event_type,
        **payload,
    }

    with log_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
