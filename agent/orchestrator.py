import difflib
import hashlib
import re
from dataclasses import dataclass, field

from agent.context import clip, import_name_for, project_overview
from agent.llm import (
    LLMOutputError,
    plan_tasks,
    propose_file_change,
    repair_file_change,
)
from agent.models import FileChangeProposal, Task, TaskPlan
from agent.reviewer import (
    CommandResult,
    assess_red_state,
    review_change,
    run_full_suite,
    run_tests,
)
from agent.test_writer import generate_test_file, repair_test_file
from agent.tools import (
    create_project,
    list_project_files,
    log_event,
    read_project_file,
    resolve_project,
    start_run,
    write_project_file,
)

MAX_BUILD_ATTEMPTS = 3
MAX_TEST_ATTEMPTS = 3

VERIFIED = "verified"
FAILED = "failed"
REJECTED = "rejected"
SKIPPED = "skipped"


class ProjectExistsError(RuntimeError):
    """The project directory already has files and reuse was not requested."""


@dataclass
class TaskResult:
    task: Task
    status: str
    detail: str = ""


@dataclass
class RunSummary:
    results: list[TaskResult] = field(default_factory=list)
    full_suite: CommandResult | None = None

    @property
    def success(self) -> bool:
        return (
            bool(self.results)
            and all(result.status == VERIFIED for result in self.results)
            and self.full_suite is not None
            and self.full_suite.passed
        )


# --------------------------------------------------------------------------
# Human approval
# --------------------------------------------------------------------------

def ask_to_apply(
    path: str,
    proposal: FileChangeProposal,
    auto_mode: bool,
    existing: str = "",
) -> bool:
    print("\n" + "=" * 70)
    print(f"PROPOSED CHANGE: {path}")
    print("=" * 70)

    if proposal.explanation:
        print(proposal.explanation)

    new = proposal.full_file_content

    if existing and existing == new:
        print("\n(No changes: proposal is identical to the current file.)\n")
    elif existing:
        diff = difflib.unified_diff(
            existing.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
        print("\n--- DIFF ---\n")
        print("\n".join(diff))
        print("\n--- END DIFF ---\n")
    else:
        print("\n--- FILE CONTENT ---\n")
        print(new)
        print("\n--- END FILE CONTENT ---\n")

    prompt = "Apply this change? [Y/n]: " if auto_mode else "Apply this change? [y/N]: "

    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            print("\nNo input available; rejecting the change.")
            return False

        if answer == "":
            return auto_mode

        if answer in {"y", "yes"}:
            return True

        if answer in {"n", "no"}:
            return False

        print("Enter Y to apply, N to reject, or press Enter for the default.")


def review_proposal(
    kind: str,
    path: str,
    proposal: FileChangeProposal,
    auto_mode: bool,
    project_slug: str,
    existing: str = "",
) -> bool:
    """Ask the human, and log the decision either way."""
    approved = ask_to_apply(path, proposal, auto_mode, existing)

    log_event(
        "proposal_reviewed",
        {
            "project_slug": project_slug,
            "kind": kind,
            "file_path": path,
            "approved": approved,
            "explanation": proposal.explanation,
            "bytes": len(proposal.full_file_content.encode("utf-8")),
            "sha256": hashlib.sha256(
                proposal.full_file_content.encode("utf-8")
            ).hexdigest(),
        },
    )

    return approved


# --------------------------------------------------------------------------
# Tests first
# --------------------------------------------------------------------------

def make_test_filename(task_id: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug = slug[:40].strip("_") or "task"
    return f"tests/test_{task_id:02d}_{slug}.py"


def prepare_tests(
    goal: str,
    plan: TaskPlan,
    task: Task,
    project_slug: str,
    auto_mode: bool,
) -> str | None:
    """Write tests for a task and make sure they fail for the right reason.

    Returns the test file path, or None if usable tests could not be produced.
    """
    test_path = make_test_filename(task.id, task.title)
    import_name = import_name_for(task.file_path)
    source_existed = bool(read_project_file(project_slug, task.file_path))
    current_tests = read_project_file(project_slug, test_path)
    overview = project_overview(project_slug, exclude=[task.file_path])

    print("\nTEST WRITER: creating tests")
    print(f"Test file: {test_path}\n")

    problem = ""

    for attempt in range(1, MAX_TEST_ATTEMPTS + 1):
        try:
            if attempt == 1:
                proposal = generate_test_file(
                    goal=goal,
                    plan_summary=plan.summary,
                    task=task,
                    test_path=test_path,
                    overview=overview,
                    existing_test_content=current_tests,
                )
            else:
                print(f"Tests are not usable yet; asking for a rewrite ({attempt}/{MAX_TEST_ATTEMPTS})...")
                proposal = repair_test_file(
                    goal=goal,
                    plan_summary=plan.summary,
                    task=task,
                    test_path=test_path,
                    overview=overview,
                    current_tests=current_tests,
                    problem=problem,
                )
        except LLMOutputError as error:
            print(f"Test writer failed:\n{error}")
            log_event(
                "test_writer_failed",
                {
                    "project_slug": project_slug,
                    "task": task.model_dump(),
                    "attempt": attempt,
                    "error": str(error),
                },
            )
            return None

        # The orchestrator decides where tests are written, not the model.
        if not review_proposal(
            "tests", test_path, proposal, auto_mode, project_slug, current_tests
        ):
            print("Tests rejected. Skipping this task.\n")
            return None

        write_project_file(project_slug, test_path, proposal.full_file_content)
        current_tests = proposal.full_file_content
        print(f"Tests written: {test_path}")

        result = run_tests(project_slug, test_path)
        ok, message = assess_red_state(result, import_name, source_existed)

        log_event(
            "tests_checked",
            {
                "project_slug": project_slug,
                "task_id": task.id,
                "test_path": test_path,
                "attempt": attempt,
                "ok": ok,
                "message": message,
                "return_code": result.return_code,
            },
        )

        if ok:
            print(f"Test check: {message}\n")
            return test_path

        print(f"Test check failed: {message}")
        problem = message

    print("Could not produce usable tests. Skipping this task.\n")
    return None


# --------------------------------------------------------------------------
# Build and repair
# --------------------------------------------------------------------------

def build_task(
    goal: str,
    plan: TaskPlan,
    task: Task,
    test_path: str,
    project_slug: str,
    auto_mode: bool,
) -> TaskResult:
    """Implement one task, repairing until its tests pass or attempts run out."""
    test_content = read_project_file(project_slug, test_path)
    overview = project_overview(project_slug, exclude=[task.file_path, test_path])
    last_error = ""

    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        current = read_project_file(project_slug, task.file_path)

        try:
            if attempt == 1:
                proposal = propose_file_change(
                    goal=goal,
                    plan_summary=plan.summary,
                    task=task,
                    test_content=test_content,
                    overview=overview,
                    existing_file_content=current,
                )
            else:
                proposal = repair_file_change(
                    goal=goal,
                    plan_summary=plan.summary,
                    task=task,
                    test_content=test_content,
                    overview=overview,
                    existing_file_content=current,
                    verification_error=last_error,
                )
        except LLMOutputError as error:
            print(f"Builder failed:\n{error}")
            log_event(
                "builder_failed",
                {
                    "project_slug": project_slug,
                    "task": task.model_dump(),
                    "attempt": attempt,
                    "error": str(error),
                },
            )
            return TaskResult(task, FAILED, "The model returned unusable output.")

        if not review_proposal(
            "implementation", task.file_path, proposal, auto_mode, project_slug, current
        ):
            print("Implementation rejected.\n")
            log_event(
                "change_rejected",
                {
                    "project_slug": project_slug,
                    "task": task.model_dump(),
                    "file_path": task.file_path,
                },
            )
            return TaskResult(task, REJECTED, "Rejected by the human reviewer.")

        try:
            write_project_file(project_slug, task.file_path, proposal.full_file_content)
        except ValueError as error:
            last_error = str(error)
            print(f"Could not write file: {last_error}")
            continue

        print(f"Written: {task.file_path}")
        print("Running reviewer...\n")

        result = review_change(project_slug, task.file_path, test_path)

        if result.passed:
            print("Reviewer passed.\n")
            log_event(
                "change_verified",
                {
                    "project_slug": project_slug,
                    "task": task.model_dump(),
                    "file_path": task.file_path,
                    "attempt": attempt,
                },
            )
            return TaskResult(task, VERIFIED)

        last_error = result.error_text()
        print("Reviewer failed:\n" + last_error + "\n")

        log_event(
            "verification_failed",
            {
                "project_slug": project_slug,
                "task": task.model_dump(),
                "file_path": task.file_path,
                "attempt": attempt,
                "error": last_error,
            },
        )

        if attempt < MAX_BUILD_ATTEMPTS:
            print("Asking repair agent to fix the failure...\n")

    return TaskResult(
        task,
        FAILED,
        f"Still failing after {MAX_BUILD_ATTEMPTS} attempts. "
        "The last attempt was left on disk for inspection.",
    )


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def print_summary(summary: RunSummary) -> None:
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)

    for result in summary.results:
        label = {
            VERIFIED: "OK  ",
            FAILED: "FAIL",
            REJECTED: "REJ ",
            SKIPPED: "SKIP",
        }[result.status]

        line = f"[{label}] {result.task.id}. {result.task.title} ({result.task.file_path})"
        print(line)

        if result.detail:
            print(f"        {result.detail}")

    suite = summary.full_suite

    if suite is None:
        print("\nFull test suite: not run")
    elif suite.passed:
        print("\nFull test suite: PASSED")
    else:
        print("\nFull test suite: FAILED")
        print(clip(suite.combined_output(), 3000))


def run_puppetmaster(
    goal: str,
    project_slug: str,
    auto_mode: bool = False,
    reuse_existing: bool = False,
) -> RunSummary:
    run_id = start_run()

    print(f"\nProject: {project_slug}  (run {run_id})")
    print(f"Goal: {goal}\n")

    resolve_project(project_slug)  # validates the slug before anything else

    if list_project_files(project_slug) and not reuse_existing:
        raise ProjectExistsError(
            f"Project '{project_slug}' already contains files. Choose a new "
            "--project name, or pass --reuse to build on the existing files "
            "(old tests will then be included in the final full test run)."
        )

    create_project(project_slug)
    print("Planning...\n")

    plan: TaskPlan = plan_tasks(goal)

    print(f"Project name: {plan.project_name}")
    print(f"Summary: {plan.summary}\n")

    for number, task in enumerate(plan.tasks, start=1):
        print(f"Task {number}/{len(plan.tasks)}: {task.title}")
        print(f"Target file: {task.file_path}\n")

    log_event(
        "plan_created",
        {
            "goal": goal,
            "project_slug": project_slug,
            "project_name": plan.project_name,
            "summary": plan.summary,
            "tasks": [task.model_dump() for task in plan.tasks],
        },
    )

    summary = RunSummary()

    for task in plan.tasks:
        print("\n" + "=" * 70)
        print(f"TASK {task.id}: {task.title}")
        print("=" * 70)

        test_path = prepare_tests(goal, plan, task, project_slug, auto_mode)

        if test_path is None:
            summary.results.append(
                TaskResult(task, SKIPPED, "No usable tests were produced.")
            )
            continue

        result = build_task(goal, plan, task, test_path, project_slug, auto_mode)
        summary.results.append(result)

        if result.status == VERIFIED:
            print(f"Task complete: {task.title}\n")
        else:
            print(f"Task not verified: {task.title}\n")

    if any(result.status == VERIFIED for result in summary.results):
        print("\nRunning the full test suite...")
        summary.full_suite = run_full_suite(project_slug)

    log_event(
        "run_finished",
        {
            "project_slug": project_slug,
            "success": summary.success,
            "results": {str(r.task.id): r.status for r in summary.results},
        },
    )

    print_summary(summary)
    print(f"\nProject files are in workspace/projects/{project_slug}/")
    print("Puppetmaster run complete.")

    return summary
