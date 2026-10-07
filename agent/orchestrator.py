import re
from pathlib import Path

from agent.llm import (
    plan_tasks,
    propose_file_change,
    repair_file_change,
)
from agent.models import TaskPlan
from agent.reviewer import review_change
from agent.test_writer import generate_test_file
from agent.tools import (
    create_project,
    list_project_files,
    list_projects,
    log_event,
    read_project_file,
    write_project_file,
)


def ask_to_apply(proposal, auto_mode: bool) -> bool:
    print("\n" + "=" * 70)
    print(f"PROPOSED CHANGE: {proposal.file_path}")
    print("=" * 70)
    print(proposal.explanation)
    print("\n--- FILE CONTENT ---\n")
    print(proposal.full_file_content)
    print("\n--- END FILE CONTENT ---\n")

    if auto_mode:
        prompt = "Apply this change? [Y/n]: "
    else:
        prompt = "Apply this change? [y/N]: "

    while True:
        answer = input(prompt).strip().lower()

        if answer == "":
            return auto_mode

        if answer in {"y", "yes"}:
            return True

        if answer in {"n", "no"}:
            return False

        print("Enter Y to apply, N to reject, or press Enter for the default.")


def make_test_filename(task_id: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:40].strip("_")
    return f"tests/test_{task_id:02d}_{slug}.py"


def run_test_writer(
    goal: str,
    plan: TaskPlan,
    task,
    project_slug: str,
    auto_mode: bool,
) -> bool:
    test_path = make_test_filename(task.id, task.title)
    existing_tests = read_project_file(project_slug, test_path)

    print("\nTEST WRITER: creating tests")
    print(f"Test file: {test_path}\n")

    try:
        test_proposal = generate_test_file(
            goal=goal,
            plan_summary=plan.summary,
            task_title=task.title,
            task_description=task.description,
            acceptance_criteria=task.acceptance_criteria,
            existing_test_content=existing_tests,
        )
    except ValueError as error:
        print(f"Test writer failed:\n{error}")
        log_event(
            "test_writer_failed",
            {
                "project_slug": project_slug,
                "task": task.model_dump(),
                "error": str(error),
            },
        )
        return False

    if not test_proposal.file_path.startswith("tests/"):
        print("Rejecting test proposal: tests must be under tests/.")
        return False

    if not ask_to_apply(test_proposal, auto_mode):
        print("Tests rejected. Skipping this task.\n")
        return False

    write_project_file(
        project_slug,
        test_proposal.file_path,
        test_proposal.full_file_content,
    )

    print(f"Tests written: {test_proposal.file_path}")

    from agent.reviewer import run_tests

    initial_test_result = run_tests(project_slug)

    if initial_test_result["passed"]:
        print(
            "Warning: tests passed before implementation. "
            "They may not actually test the requested behavior."
        )

    return True


def run_puppetmaster(
    goal: str,
    project_slug: str,
    auto_mode: bool = False,
) -> None:
    print(f"\nProject: {project_slug}")
    print(f"Goal: {goal}\n")

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

    for task in plan.tasks:
        print("\n" + "=" * 70)
        print(f"TASK: {task.title}")
        print("=" * 70)

        tests_ready = run_test_writer(
            goal=goal,
            plan=plan,
            task=task,
            project_slug=project_slug,
            auto_mode=auto_mode,
        )

        if not tests_ready:
            continue

        existing_content = read_project_file(project_slug, task.file_path)
        last_error = ""
        verified = False

        for attempt in range(1, 4):
            try:
                if attempt == 1:
                    proposal = propose_file_change(
                        goal=goal,
                        plan_summary=plan.summary,
                        task_title=task.title,
                        task_description=task.description,
                        acceptance_criteria=task.acceptance_criteria,
                        existing_file_content=existing_content,
                    )
                else:
                    proposal = repair_file_change(
                        goal=goal,
                        plan_summary=plan.summary,
                        task_title=task.title,
                        task_description=task.description,
                        acceptance_criteria=task.acceptance_criteria,
                        existing_file_content=existing_content,
                        verification_error=last_error,
                    )
            except ValueError as error:
                last_error = str(error)
                print(f"Builder failed:\n{last_error}")
                log_event(
                    "builder_failed",
                    {
                        "project_slug": project_slug,
                        "task": task.model_dump(),
                        "attempt": attempt,
                        "error": last_error,
                    },
                )
                break

            if proposal.file_path != task.file_path:
                last_error = (
                    f"The builder proposed '{proposal.file_path}', "
                    f"but the task requires '{task.file_path}'."
                )
                print(last_error)

                if attempt < 3:
                    print("Retrying...\n")
                    continue

                log_event(
                    "builder_path_mismatch",
                    {
                        "project_slug": project_slug,
                        "expected": task.file_path,
                        "proposed": proposal.file_path,
                    },
                )
                break

            if not ask_to_apply(proposal, auto_mode):
                print("Implementation rejected.\n")
                log_event(
                    "change_rejected",
                    {
                        "project_slug": project_slug,
                        "task": task.model_dump(),
                        "file_path": proposal.file_path,
                    },
                )
                break

            write_project_file(
                project_slug,
                proposal.file_path,
                proposal.full_file_content,
            )

            print(f"Written: {proposal.file_path}")
            print("Running reviewer...\n")

            from agent.reviewer import review_change

            result = review_change(project_slug, proposal.file_path)

            if result["passed"]:
                print("Reviewer passed.\n")
                verified = True

                log_event(
                    "change_verified",
                    {
                        "project_slug": project_slug,
                        "task": task.model_dump(),
                        "file_path": proposal.file_path,
                    },
                )
                break

            compile_result = result["compile"]
            test_result = result["tests"]

            errors = []

            if not compile_result["passed"]:
                errors.append(compile_result["stderr"])

            if test_result and not test_result["passed"]:
                errors.append(test_result["stdout"])
                errors.append(test_result["stderr"])

            last_error = "\n".join(error for error in errors if error)

            print("Reviewer failed:\n" + last_error)

            if attempt < 3:
                print("Asking repair agent to fix the failure...\n")
                existing_content = read_project_file(
                    project_slug,
                    task.file_path,
                )
                continue

            log_event(
                "verification_failed",
                {
                    "project_slug": project_slug,
                    "task": task.model_dump(),
                    "file_path": proposal.file_path,
                    "error": last_error,
                },
            )

        if verified:
            print(f"Task complete: {task.title}\n")
        else:
            print(f"Task not verified: {task.title}\n")

    print("\nProjects:")
    for slug in list_projects():
        print(f"- {slug}")

    print("\nPuppetmaster run complete.")