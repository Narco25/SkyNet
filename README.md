# SkyNet

SkyNet is a local-first Python AI agent network for building small software projects.

It uses locally hosted large language models through [Ollama](https://ollama.com/) to plan work, write tests, implement code, review changes, and repair verification failures. SkyNet is designed for constrained local hardware and keeps generated projects isolated from the agent system itself.

## Status

SkyNet is an early prototype.

It can currently:

- Accept a human-defined project goal.
- Plan a small set of implementation tasks.
- Create an isolated project directory.
- Generate pytest tests before implementation.
- Propose Python implementation files.
- Run `python -m py_compile` on changed files.
- Run tests with `python -m pytest`.
- Ask a repair agent to fix failed verification, with a limited number of retries.
- Log plans, proposals, approvals, rejections, and verification results.

It does not yet provide a polished user interface, persistent multi-session project management, autonomous long-running operation, or unrestricted access to the operating system.

## Design principles

SkyNet follows these principles:

- **Local first:** Models and project files remain on the local machine.
- **Human responsible:** The human approves risky operations and integrates accepted changes.
- **Test first:** Tests define the expected behavior before implementation.
- **Small tasks:** Agents work on narrowly scoped, individually verifiable tasks.
- **Sandboxed workspaces:** Each generated project lives in its own directory.
- **Explicit tools:** Agents may propose actions, but the Python orchestrator controls what is actually allowed.
- **Limited autonomy:** File writes may be convenient, but shell commands, networking, package installation, and paths outside a project require explicit restrictions or approval.

## Architecture

```text
Human
  |
  v
Puppetmaster / Orchestrator
  |-- Planner agent
  |-- Test-writing agent
  |-- Builder agent
  |-- Reviewer agent
  |-- Repair agent
  |
  v
Sandboxed project workspace
  |-- Project source files
  |-- tests/
  `-- Verification results
```

### Agents

| Agent | Responsibility |
|---|---|
| Puppetmaster | Turns a goal into a small, structured task plan |
| Test writer | Creates pytest tests before implementation |
| Builder | Implements one Python file or task at a time |
| Reviewer | Runs compilation and tests |
| Repair agent | Attempts to fix verification failures |

### Workflow

```text
1. Human provides a goal.
2. Planner creates a task plan.
3. Test writer creates tests for a task.
4. Reviewer runs the tests.
5. Builder implements the task.
6. Reviewer compiles the changed file and runs tests.
7. Repair agent fixes failures, if allowed.
8. Human reviews the final result.
```

## Requirements

- Windows 11, Linux, or macOS
- Python 3.10 or newer
- [Ollama](https://ollama.com/)
- A locally installed Ollama model

SkyNet was developed with a 16 GB RAM laptop using an Intel integrated GPU. Models therefore run primarily on the CPU and system RAM. Use one model at a time.

Recommended local models:

```text
qwen2.5-coder:7b
llama3.1:8b-instruct-q5_K_M
```

## Installation

Clone the repository:

```bash
git clone [https://github.com/Narco25/SkyNet.git](https://github.com/Narco25/SkyNet.git)
cd SkyNet
```

Create and activate a Python virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Install Ollama from the official website, then pull a local model:

```bash
ollama pull qwen2.5-coder:7b
```

Verify that Ollama is running:

```bash
curl http://localhost:11434/api/tags
```

## Usage

Run SkyNet with a project name and a goal:

```bash
python main.py \
  --project project-manager \
  --auto \
  "Create a Python command-line project manager with functions to create a new project directory, list existing projects, and validate project names. Include tests."
```

### Command-line options

| Option | Description |
|---|---|
| `goal` | The software goal for SkyNet to work toward |
| `--project` | The slug for the generated project, such as `todo-app` |
| `--auto` | Makes pressing Enter approve a proposed file change |

### Review modes

| Mode | Prompt | Enter behavior |
|---|---|---|
| Default | `[y/N]` | Rejects the proposed change |
| `--auto` | `[Y/n]` | Applies the proposed change |

Even in `--auto` mode, SkyNet remains sandboxed. It cannot freely execute commands, access the internet, install packages, or modify files outside its assigned project.

## Project layout

```text
SkyNet/
├── agent/
│   ├── llm.py
│   ├── models.py
│   ├── orchestrator.py
│   ├── reviewer.py
│   ├── test_writer.py
│   └── tools.py
├── logs/
├── workspace/
│   └── projects/
│       └── <project-slug>/
├── main.py
├── requirements.txt
└── README.md
```

Generated projects are stored under:

```text
workspace/projects/<project-slug>/
```

For example:

```text
workspace/projects/project-manager/
├── project_manager.py
└── tests/
    └── test_project_manager.py
```

## Safety boundaries

SkyNet is intentionally constrained.

Agents cannot:

- Freely execute shell commands.
- Install packages.
- Access the internet.
- Modify files outside `workspace/projects/<project-slug>/`.
- Rewrite SkyNet’s orchestrator or grant themselves permissions.
- Operate without a human-defined goal.

The reviewer checks whether code compiles and whether tests pass. Passing verification does not guarantee that code is secure, well designed, complete, or appropriate for production use.

## Roadmap

Planned improvements include:

- A tool registry with explicitly defined, safe tools.
- A task queue for resuming incomplete projects.
- Better structured outputs using JSON schemas.
- A test-first workflow with stricter coverage requirements.
- Optional escalation of difficult tasks to external LLMs.
- Human-approved integration of externally generated patches.
- Better project templates and dependency isolation.

## Contributing

Contributions are welcome. Please keep changes small, add tests for new behavior, and avoid introducing unrestricted shell, filesystem, or network access.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
