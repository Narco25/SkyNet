import argparse
import sys

from agent.llm import LLMUnavailableError
from agent.orchestrator import ProjectExistsError, run_puppetmaster


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local Puppetmaster AI agent prototype."
    )

    parser.add_argument("goal", help="The project goal.")

    parser.add_argument(
        "--project",
        required=True,
        help="Project slug, for example: todo-app",
    )

    parser.add_argument(
        "--auto",
        action="store_true",
        help="Make pressing Enter approve each proposed file change.",
    )

    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Allow building in a project directory that already has files.",
    )

    args = parser.parse_args()

    try:
        summary = run_puppetmaster(
            goal=args.goal,
            project_slug=args.project,
            auto_mode=args.auto,
            reuse_existing=args.reuse,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except (LLMUnavailableError, ProjectExistsError, ValueError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1

    return 0 if summary.success else 1


if __name__ == "__main__":
    sys.exit(main())
