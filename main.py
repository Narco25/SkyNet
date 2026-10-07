import argparse

from agent.orchestrator import run_puppetmaster


def main() -> None:
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
        help="Use Enter to approve each proposed file change.",
    )

    args = parser.parse_args()

    run_puppetmaster(
        goal=args.goal,
        project_slug=args.project,
        auto_mode=args.auto,
    )


if __name__ == "__main__":
    main()