# SkyNet

**A local-first AI agent network that plans, tests, builds, reviews, and repairs small Python projects, using LLMs running on your own machine.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

SkyNet takes a plain-English goal, breaks it into small tasks, and works through them with a team of specialised agents backed by a locally hosted model served by [Ollama](https://ollama.com/). Tests are written *before* the code, every change is shown to you for approval, and failures are fed back to a repair agent. Nothing leaves your machine.

It is built for modest hardware: it was developed on a 16 GB RAM laptop with no discrete GPU, running a 7B model on the CPU.

> **Status: early prototype.** Expect rough edges, and keep goals small. See [Known limitations](#known-limitations) and [Safety model](#safety-model) before running it on anything you care about.

## Table of contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Safety model](#safety-model)
- [Logs](#logs)
- [Project layout](#project-layout)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## How it works

### Design principles

- **Local first:** models and project files stay on your machine.
- **Human responsible:** you approve every file change, and you decide what to keep.
- **Test first:** tests define the expected behavior before any implementation exists.
- **Small tasks:** each task creates exactly one file and is verified on its own.
- **Isolated workspaces:** each generated project lives in its own directory.
- **Explicit tools:** agents only *propose* text. The Python orchestrator decides what is written and where.

### Agents

| Agent | Responsibility |
| --- | --- |
| **Puppetmaster** (planner) | Turns a goal into 2 to 5 ordered tasks, each with a target file and an exact interface |
| **Test writer** | Writes pytest tests from the interface, before the code exists, and rewrites them if they turn out to be broken |
| **Builder** | Implements one file so that the tests pass |
| **Reviewer** | Syntax-checks the file and runs that task's tests (plain Python, not an LLM) |
| **Repair agent** | Fixes the file when verification fails, using the pytest output |

### Workflow

```text
Human goal
   |
   v
Plan: 2-5 tasks, each with a file path and an interface
   |
   v
For each task:
   1. Test writer drafts tests              -> you approve
   2. Tests are run BEFORE any code exists, and must fail for the right reason
      (tests that already pass, don't compile, or import unavailable
      libraries are sent back to the test writer for a rewrite)
   3. Builder implements the file           -> you approve
   4. Reviewer syntax-checks it and runs this task's tests
   5. On failure, the repair agent gets the error and tries again (3 attempts total)
   |
   v
Full test suite runs once at the end as a regression check
   |
   v
Summary table, and a non-zero exit code if anything failed
```

Each agent sees only what it needs: the target file, the task's interface, signatures of the project's other files, and (for the builder and repairer) the tests. Model output is requested as schema-constrained JSON and validated; if it is invalid, the model is shown the exact error and asked to correct it.

## Requirements

- Windows 11, Linux, or macOS
- Python 3.10 or newer
- [Ollama](https://ollama.com/), ideally a recent release. Older versions that do not support schema-constrained output fall back to plain JSON mode automatically.
- At least one locally installed Ollama model

Recommended models:

```text
qwen2.5-coder:7b
llama3.1:8b-instruct-q5_K_M
```

Use one model at a time. On CPU-only machines, expect each model call to take from tens of seconds to a few minutes.

## Installation

Clone the repository:

```bash
git clone https://github.com/Narco25/SkyNet.git
cd SkyNet
```

Create and activate a virtual environment.

Linux and macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install the dependencies (`pydantic` and `pytest`):

```bash
pip install -r requirements.txt
```

Install Ollama from [ollama.com](https://ollama.com/), then pull a model:

```bash
ollama pull qwen2.5-coder:7b
```

Check that Ollama is running:

```bash
curl http://localhost:11434/api/tags
```

> **Why `pytest` is a dependency:** generated projects are tested with the same Python interpreter that runs SkyNet, so pytest must be installed in the same environment.

## Usage

Run SkyNet with a project name and a goal:

```bash
python main.py --project project-manager "Create a Python command-line project manager with functions to create a new project directory, list existing projects, and validate project names."
```

SkyNet shows each proposed test file and implementation, then asks whether to apply it. For files that already exist it shows a diff instead of the full content.

### Command-line options

| Option | Description |
| --- | --- |
| `goal` | The software goal for SkyNet to work toward |
| `--project` | **Required.** Project name: lowercase letters, numbers, and hyphens, up to 64 characters (for example `todo-app`) |
| `--auto` | Pressing Enter approves a proposed change instead of rejecting it |
| `--reuse` | Allow building in a project directory that already contains files |

By default SkyNet refuses to run in a project directory that already has files, because leftover tests from an earlier run can contradict the new plan. Pass `--reuse` if you really want to build on top of them. Old tests will then be included in the final full test run.

### Review modes

| Mode | Prompt | Pressing Enter |
| --- | --- | --- |
| Default | `[y/N]` | Rejects the change |
| `--auto` | `[Y/n]` | Applies the change |

Even with `--auto`, read the diffs: approval is the main safeguard you have (see [Safety model](#safety-model)).

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Every task verified and the full test suite passed |
| `1` | A task failed, was rejected, or was skipped; the full suite failed; or an error occurred (Ollama unreachable, model missing, project directory in use, invalid name) |
| `130` | Interrupted with Ctrl+C |

### What a run ends with

The output below only illustrates the format; your tasks and results will differ.

```text
======================================================================
RESULTS
======================================================================
[OK  ] 1. Validate project names (name_validation.py)
[OK  ] 2. Create and list projects (projects.py)
[FAIL] 3. Command-line interface (cli.py)
        Still failing after 3 attempts. The last attempt was left on disk for inspection.

Full test suite: FAILED
```

A task that fails all attempts leaves its last version on disk so you can inspect it. Later tasks that import it will fail as well.

Generated projects are written to:

```text
workspace/projects/<project-slug>/
```

For example:

```text
workspace/projects/project-manager/
├── name_validation.py
├── projects.py
└── tests/
    ├── test_01_validate_project_names.py
    └── test_02_create_and_list_projects.py
```

## Configuration

Settings are read from environment variables.

| Variable | Default | Description |
| --- | --- | --- |
| `SKYNET_MODEL` | `qwen2.5-coder:7b` | Ollama model to use |
| `SKYNET_OLLAMA_URL` | `http://localhost:11434` | Where Ollama is listening |
| `SKYNET_NUM_CTX` | `8192` | Context window in tokens. Lower it (for example `4096`) if you run short on memory |
| `SKYNET_TEMPERATURE` | `0.2` | Sampling temperature |
| `SKYNET_LLM_TIMEOUT` | `600` | Seconds to wait for each model response |

Example:

```bash
SKYNET_MODEL=llama3.1:8b-instruct-q5_K_M SKYNET_NUM_CTX=4096 python main.py --project todo-app "..."
```

On Windows PowerShell:

```powershell
$env:SKYNET_MODEL = "llama3.1:8b-instruct-q5_K_M"
python main.py --project todo-app "..."
```

## Safety model

SkyNet is constrained, but it is **not a sandbox**. Please read this section.

### What SkyNet enforces

- Agents only return text. Only the orchestrator writes files, and it chooses the destination: tests always go to a name it generates under `tests/`, and implementation files go to the path from the validated plan.
- Plan paths must be relative, importable `.py` files outside `tests/`. Every write is resolved (following symlinks) and checked to be inside `workspace/projects/<project-slug>/`, and files are capped at 200 KB.
- Project names are validated before any path is built.
- The only external program SkyNet runs is `python -m pytest`, using the interpreter that runs SkyNet, with no shell. Syntax checking is done in-process, which never executes the code.
- Test runs get a minimal environment (your API keys and other variables are not passed through), a throwaway home directory, no stdin, a timeout (60 seconds per task, 120 for the final suite), and, on Linux and macOS, CPU, memory, and file-size limits. On timeout the whole process group is killed.
- By default, nothing is written until you approve it.

### What SkyNet does not enforce

- **Generated code and generated tests run on your machine, as your user.** A test that deliberately reads or writes files outside its project, or opens a network connection, is not blocked. The measures above limit accidents and runaway code; they do not stop deliberate misbehaviour.
- On Windows, timeouts kill only the direct child process, and the resource limits do not apply.
- Passing verification does not mean code is correct, secure, well designed, or suitable for production.

If you are generating code from goals you do not fully control, or you simply want real isolation, run SkyNet inside a container or virtual machine with networking disabled. Docker-based test execution is on the [roadmap](#roadmap).

## Logs

Every run appends structured events to `logs/agent_events.jsonl`, one JSON object per line, tagged with a `run_id` and timestamp.

| Event | Meaning |
| --- | --- |
| `plan_created` | The goal and the plan that was produced |
| `proposal_reviewed` | A proposed file was approved or rejected (with a hash and size, not the full content) |
| `tests_checked` | Result of running new tests before implementation |
| `test_writer_failed`, `builder_failed` | The model could not produce valid output |
| `change_rejected` | You rejected an implementation |
| `verification_failed` | An attempt failed; includes the pytest output |
| `change_verified` | A task passed review |
| `run_finished` | Final per-task status and overall success |

## Project layout

```text
SkyNet/
├── agent/
│   ├── __init__.py
│   ├── context.py        # Prompt context: signature extraction, text clipping
│   ├── llm.py            # Ollama client, structured output, planner/builder/repair agents
│   ├── models.py         # Pydantic models and plan/path validation
│   ├── orchestrator.py   # The run loop: plan, tests, build, repair, summary
│   ├── reviewer.py       # Syntax check, pytest runner, test sanity check
│   ├── test_writer.py    # Test-writing agent
│   └── tools.py          # Project paths, safe file access, event logging
├── tests/
│   └── test_agent.py     # SkyNet's own tests (no Ollama needed)
├── logs/                 # Created at runtime
├── workspace/
│   └── projects/
│       └── <project-slug>/
├── main.py               # Command-line entry point
├── pytest.ini
├── requirements.txt
├── LICENSE
└── README.md
```

## Development

SkyNet's own tests replace the model with a fake, so they run without Ollama. Run them from the repository root:

```bash
pytest
```

They cover plan and path validation, the path guard (including symlink escapes), timeouts, the test sanity check, the retry-with-feedback logic, and a complete plan, test, build, repair, verify run.

When changing prompts or agent behavior, also try a few small real goals against your local model and check `logs/agent_events.jsonl`, since the fake model cannot tell you how a real one behaves.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| `Cannot reach Ollama at ...` | Start Ollama (`ollama serve`, or launch the desktop app) and check `SKYNET_OLLAMA_URL` |
| `Ollama could not find model ...` | Run `ollama pull <model>` or set `SKYNET_MODEL` to a model you have |
| `Ollama did not answer within ...s` | Raise `SKYNET_LLM_TIMEOUT`, or use a smaller model |
| Responses are slow or the machine runs out of memory | Lower `SKYNET_NUM_CTX`, close other applications, and use a single 7B model |
| `Project '...' already contains files` | Choose a new `--project` name, or pass `--reuse` |
| `No module named pytest` during test runs | Run `pip install -r requirements.txt` inside the virtual environment that runs SkyNet |
| The planner keeps returning invalid plans | Make the goal smaller and more specific; plans must have 2 to 5 tasks |

## Known limitations

- Output quality depends heavily on the local model. Small models struggle with large or vague goals, so keep goals small and concrete.
- Tests are written by the same kind of model as the code. They can be wrong or too weak, and the sanity check only catches obvious problems.
- Every task is a single Python file using only the standard library.
- Runs cannot be paused or resumed; the plan is logged but not reloaded.
- Test execution is not sandboxed (see [Safety model](#safety-model)).

## Roadmap

- Docker-based test execution with networking disabled
- Persist the plan and progress so interrupted runs can resume
- A tool registry with explicitly defined, safe tools
- A benchmark of fixed goals to measure pass rates across models and prompts
- Continuous integration for SkyNet's own tests
- Optional escalation of difficult tasks to external LLMs
- Human-approved integration of externally generated patches
- Project templates and dependency isolation

## Contributing

Contributions are welcome. Please keep changes small, add tests for new behavior (`pytest` must pass), and avoid introducing unrestricted shell, filesystem, or network access.

## License

Released under the [MIT License](LICENSE).
