"""Helpers that build compact context for the agents' prompts."""

import ast
from typing import Iterable

from agent.tools import list_project_files, read_project_file


def clip(text: str, limit: int, head: int | None = None) -> str:
    """Shorten text to roughly `limit` characters, keeping start and end.

    Tracebacks and pytest summaries put the useful part at the end, so most of
    the budget goes to the tail.
    """
    if len(text) <= limit:
        return text

    head = limit // 4 if head is None else head
    tail = max(limit - head, 0)
    omitted = len(text) - head - tail
    tail_text = text[-tail:] if tail else ""

    return f"{text[:head]}\n...[{omitted} characters omitted]...\n{tail_text}"


def import_name_for(file_path: str) -> str:
    """'pkg/mod.py' -> 'pkg.mod' (how tests run from the project root import it)."""
    return file_path[: -len(".py")].replace("/", ".")


def _function_signature(node: ast.AST, indent: str = "") -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    line = f"{indent}{prefix} {node.name}({ast.unparse(node.args)}){returns}"

    doc = ast.get_docstring(node)
    if doc:
        line += f"  # {doc.strip().splitlines()[0]}"

    return line


def extract_signatures(source: str) -> str:
    """Return the public signatures of a Python source file, without bodies."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "(file has syntax errors)"

    lines: list[str] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                lines.append(_function_signature(node))

        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue

            bases = ", ".join(ast.unparse(base) for base in node.bases)
            lines.append(f"class {node.name}({bases}):" if bases else f"class {node.name}:")

            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name == "__init__" or not member.name.startswith("_"):
                        lines.append(_function_signature(member, indent="    "))

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    lines.append(f"{target.id} = ...")

    return "\n".join(lines) if lines else "(no public names)"


def project_overview(
    project_slug: str,
    exclude: Iterable[str] = (),
    max_chars: int = 3000,
) -> str:
    """Summarise the project's other source files by signature only."""
    excluded = set(exclude)
    sections: list[str] = []

    for relative in list_project_files(project_slug):
        if relative in excluded or relative.startswith("tests/"):
            continue

        if not relative.endswith(".py"):
            sections.append(f"# {relative}")
            continue

        source = read_project_file(project_slug, relative)
        sections.append(
            f"# {relative}  (import as `{import_name_for(relative)}`)\n"
            f"{extract_signatures(source)}"
        )

    if not sections:
        return "(no other source files yet)"

    return clip("\n\n".join(sections), max_chars, head=max_chars)
