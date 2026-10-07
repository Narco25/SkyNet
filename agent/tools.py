import json
import re
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path("workspace").resolve()
PROJECTS_ROOT = (WORKSPACE / "projects").resolve()
LOG_DIR = Path("logs").resolve()


def validate_project_slug(project_slug: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project_slug):
        raise ValueError(
            "Project name must use lowercase letters, numbers, and hyphens; "
            "for example: todo-app"
        )

    return project_slug


def resolve_project(project_slug: str) -> Path:
    project_slug = validate_project_slug(project_slug)
    project_path = (PROJECTS_ROOT / project_slug).resolve()

    if project_path.parent != PROJECTS_ROOT:
        raise ValueError(f"Invalid project path: {project_slug}")

    return project_path


def resolve_project_file(project_slug: str, relative_path: str) -> Path:
    project_path = resolve_project(project_slug)
    file_path = (project_path / relative_path).resolve()

    if project_path not in file_path.parents and file_path != project_path:
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

    return sorted(
        str(path.relative_to(project_path))
        for path in project_path.rglob("*")
        if path.is_file()
    )


def read_project_file(project_slug: str, relative_path: str) -> str:
    file_path = resolve_project_file(project_slug, relative_path)

    if not file_path.exists():
        return ""

    return file_path.read_text(encoding="utf-8")


def write_project_file(
    project_slug: str,
    relative_path: str,
    content: str,
) -> Path:
    file_path = resolve_project_file(project_slug, relative_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def log_event(event_type: str, payload: dict) -> None:
    LOG_DIR.mkdir(exist_ok=True)

    log_file = LOG_DIR / "agent_events.jsonl"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        **payload,
    }

    with log_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")