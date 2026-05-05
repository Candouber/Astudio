<p align="center">
  <img src="./static/astudio-logo.png" alt="AStudio" width="720">
</p>

# AStudio

<p align="center">
  <strong>A local-first multi-agent workspace for structured task execution</strong>
</p>

<p align="center">
  <a href="./README.md">中文</a>
  ·
  English
</p>

<p align="center">
  <a href="./LICENSE">MIT License</a>
  ·
  <a href="./docs/architecture.md">Architecture</a>
  ·
  <a href="./docs/studio-system.md">Studio System</a>
  ·
  <a href="./CONTRIBUTING.md">Contributing</a>
</p>

AStudio turns a complex request into a task pipeline that can be routed, reviewed, executed, tracked, and reused. After a user submits a request, Agent Zero classifies the task: simple questions can be answered directly, platform-management requests go to Studio 0, and business tasks are routed to an existing Studio or a newly created specialist Studio. The Studio Leader clarifies requirements, creates a DAG execution plan, assigns Sub-agents, and reviews deliverables. Sub-agents use Skills, attachment tools, search tools, and a task sandbox to complete concrete steps. After execution, the system synthesizes the final result, records cost, and consolidates memory.

AStudio runs locally by default. Model keys, task databases, attachments, sandbox artifacts, and runtime logs stay on the local machine and are ignored by default.

## Table of Contents

- [Core Capabilities](#core-capabilities)
- [Workflow](#workflow)
- [System Architecture](#system-architecture)
- [Documentation](#documentation)
- [Tech Stack](#tech-stack)
- [Installation](#installation)
- [Model and Search Configuration](#model-and-search-configuration)
- [Local Data](#local-data)
- [Development Commands](#development-commands)
- [Contributing](#contributing)
- [License](#license)

## Core Capabilities

- **Task orchestration engine**: `/api/tasks/ask` creates a task; a background worker process handles routing, planning, and execution while the FastAPI process stays responsive.
- **Agent Zero routing layer**: routes tasks through Studio Cards, the system-management classifier, and deterministic fallback rules.
- **Studio workspace model**: each Studio has a scenario, capability tags, recent topics, user facts, a Leader, and multiple Sub-agents.
- **DAG execution plans**: the Leader outputs steps with `id`, `depends_on`, and `assign_to_role`; the backend validates dependencies, trims cycles, and runs parallel-ready nodes concurrently.
- **Clarification and approval**: incomplete requests enter `need_clarification`; planned tasks pause at `await_leader_plan_approval` before execution.
- **Leader review loop**: Sub-agent deliverables move through `pending_review`; the Leader can accept them or request revisions.
- **Skill pool and dynamic hiring**: built-in file, search, code, sandbox, and scheduling tools; bundle Skills can be imported from SkillHub / ClawHub or generated locally by AI.
- **Task sandbox**: each task can own an isolated directory and dev port, with file writes, command runs, logs, previews, and reusable artifacts.
- **Attachment analysis**: uploaded Excel/CSV, PDF, image, and text files automatically expose attachment-reading, table-preview, PDF text, and image metadata tools.
- **Human intervention and cascade reruns**: users can retry blocked nodes or manually edit a step output; downstream steps rerun according to dependency links.
- **Long-term memory**: completed tasks update participating agents' `soul`, Studio Card topics, capability labels, and user facts.
- **Observability and recovery**: SSE streams task status, node updates, heartbeats, and pauses; a watchdog handles stale tasks and preserves plans for reruns.
- **Desktop app**: the Electron package embeds the frontend and local backend sidecar, then starts with a free local port and user-data directory.

## Workflow

### 1. Configure Models

AStudio exposes model configuration in the UI so users do not need to manage everything from the command line. On first launch, open Settings and add Providers, API keys, model lists, and model assignments for each role.

![Configure models](./static/Pasted%20image%2020260428161644.png)

### 2. Submit a Task and Add Missing Context

When a request is too broad, the Leader asks targeted clarification questions. The answers are merged with the original request before a new execution plan is generated.

![Clarification form](./static/Pasted%20image%2020260428163324.png)

### 3. Review the Execution Plan

The Leader breaks the task into steps, assigns roles, and records dependencies. Users can approve the plan or send feedback for replanning.

![Add clarification](./static/Pasted%20image%2020260428163935.png)

![Plan approval](./static/Pasted%20image%2020260428163755.png)

### 4. Let a Studio Take Over

Studios preserve experience for concrete task scenarios. Agent Zero routes by Studio Card, reusing a matching Studio when possible and creating a new specialist Studio when needed.

![Studio](./static/Pasted%20image%2020260428162406.png)

### 5. Execute the DAG with Sub-agents

Sub-agents do the actual work. The Leader assigns each step to a role, and the backend starts nodes according to DAG dependencies. If an upstream node is blocked, downstream nodes are skipped until the user provides enough context to resume.

![Agents and DAG](./static/Pasted%20image%2020260428164110.png)

Each Sub-agent has editable `agent.md`, `soul`, and Skills. Stable workflows can become fixed roles, while missing capabilities can be added through the Leader's hiring flow.

![Edit agent and skills](./static/Pasted%20image%2020260428171534.png)

### 6. Inspect Results, Annotations, and Sandbox Artifacts

Text results support annotation-style follow-up questions, which makes it easier to continue from a specific part of a long answer.

![Result annotations](./static/astudio-demo-result.gif)

When a task produces code, pages, scripts, or small tools, the artifacts are kept in the task sandbox. Users can inspect files, run commands, open previews, and reuse the output in later work.

![Sandbox tool](./static/astudio-demo-sandbox.gif)

## System Architecture

AStudio has four main layers:

| Layer | Responsibilities | Modules |
| --- | --- | --- |
| Desktop and frontend | Electron shell, React UI, task board, Studio management, sandbox files, Skill pool, SSE subscriptions | `web/`, `web/electron/main.cjs` |
| API and state | FastAPI routes, SQLite/WAL, local config, attachment storage, Task/Studio/Sandbox/Schedule stores | `server/main.py`, `server/routers/`, `server/storage/` |
| Orchestration | Agent Zero routing, Leader planning, DAG scheduling, Sub-agent ReAct execution, Leader review, final synthesis | `server/agents/`, `server/routers/task.py` |
| Tools and memory | Skill Registry, built-in tools, bundle Skills, sandbox commands, search, attachment analysis, scheduled jobs, soul and Studio Card updates | `server/tools/`, `server/core/`, `server/services/` |

### Task Lifecycle

1. The user creates a task; the backend writes `tasks` and an initial iteration.
2. An isolated worker process starts so long-running tasks do not block the API process.
3. Agent Zero routes the task: system management, direct answer, existing Studio, or new Studio.
4. The Studio Leader creates a plan; it may request clarification or wait for user approval.
5. After approval, the backend creates UI nodes and edges, then runs dependency-ready DAG steps concurrently.
6. Sub-agents use tools and report via `submit_task_deliverable` or `report_system_blocker`.
7. The Leader reviews every step and can request revisions.
8. When all steps end, the Leader compiles findings and Agent Zero writes the final answer.
9. Task Monitor finalizes the task and updates agent `soul`, Studio Card metadata, and user facts.

### Reliability Design

- **Process isolation**: each task pipeline runs through `workers.task_worker` by default; worker crashes are reflected in task state.
- **Task locking and termination**: duplicate orchestration is avoided, and user termination cancels active coroutines and workers.
- **SSE updates**: `/api/tasks/{task_id}/stream` sends statuses, nodes, heartbeats, and pause events to the frontend.
- **Watchdog**: stale planning or executing tasks are marked failed or terminated, with saved plans available for reruns.
- **Cost and timing metrics**: every SubTask records tokens, duration, cost, and model name.
- **Execution boundary**: sandbox commands pass local safety checks, and file operations are constrained to the task sandbox.

## Documentation

The README covers the main path. Detailed notes live under `docs/`:

| Document | Content |
| --- | --- |
| [Architecture](./docs/architecture.md) | Agent Zero, Studio, and Canvas layering |
| [Studio System](./docs/studio-system.md) | Studio Card, Soul, Agent.md, and instantiation |
| [Agent Zero](./docs/agent-zero.md) | Routing, decomposition, synthesis, and Studio promotion |
| [Canvas Engine](./docs/canvas-engine.md) | React Flow, SSE, node state, and canvas interaction |
| [Context Distillation](./docs/context-distillation.md) | Node summaries, long-term memory, and context compression |
| [LLM Integration](./docs/llm-integration.md) | LiteLLM, role-based model routing, and hot reload |

## Tech Stack

- **Frontend**: React 19, TypeScript, Vite, React Router, Zustand, React Flow, Lucide Icons
- **Backend**: FastAPI, Pydantic, SQLite WAL, SSE, LiteLLM, Playwright, uv
- **Orchestration**: Agent Zero, Studio Leader, Sub-agent ReAct, Skill Registry, task workers, watchdog
- **Desktop**: Electron, electron-builder, PyInstaller sidecar
- **Package management**: pnpm workspace, uv

## Installation

### Option 1: Download the Desktop App

Regular users should install the desktop app. It does not require Node.js, Python, pnpm, or uv.

1. Open [GitHub Releases](https://github.com/Candouber/Astudio/releases).
2. Download the package for your operating system.
3. Launch AStudio, then configure model Providers, API keys, and role-based model routing in Settings.

| System | File | Notes |
| --- | --- | --- |
| Windows x64 | `AStudio-*-win-x64.exe` | Install or launch directly |
| macOS Apple Silicon | `AStudio-*-mac-arm64.dmg` | For M-series Macs |
| macOS Intel | `AStudio-*-mac-x64.dmg` | For Intel Macs |
| Linux x64 | `AStudio-*-linux-x86_64.AppImage` | Make it executable, then launch |

The desktop package starts the local backend automatically. App data, model config, the database, logs, and sandbox files are written to the system user-data directory instead of the repository directory.

### Option 2: Run from Source

Developers can run AStudio from source.

#### Requirements

- Node.js 20+
- pnpm 8+, preferably enabled through Corepack
- Python 3.11+
- uv

On macOS, uv can be installed with Homebrew:

```bash
brew install uv
```

#### Clone and Install Dependencies

```bash
git clone <your-repo-url>
cd <repo-dir>
corepack enable
pnpm setup
```

`pnpm setup` will:

- install root and frontend Node dependencies;
- run `uv sync` inside `server/`.

Browser-search enhancement is optional. By default, `web_search` uses the configured search provider first, then tries a system Chrome / Edge / Chromium headless CLI fallback when normal search fails. Install the bundled Playwright Chromium only when you need it:

```bash
pnpm setup:browser
```

#### Start the Development Environment

```bash
pnpm dev
```

Open:

- Web UI: http://127.0.0.1:5173
- Backend health check: http://127.0.0.1:8000/api/health

#### Start the Local Stable Mode

```bash
pnpm start
```

This builds the frontend and serves `web/dist` through FastAPI. Open:

- AStudio: http://127.0.0.1:8000

#### Start Electron

Development:

```bash
pnpm electron:dev
```

Local stable entry:

```bash
pnpm electron:start
```

Electron reuses a healthy local backend when available. If no backend is available, it starts FastAPI. Packaged desktop builds use a free local port by default, store data in Electron's user-data directory, and write backend logs to `logs/backend.log`.

## Model and Search Configuration

After first launch, configure models in Settings. You can also start from the example config:

```bash
mkdir -p data
cp config.example.yaml data/config.yaml
```

Model configuration has three main parts:

- `llm_providers`: provider name, API key, endpoint, model list, display names, and OAuth flag.
- `model_aliases`: maps AStudio-local model names to real provider model IDs, useful for OpenAI-compatible gateways.
- `model_assignment`: assigns models, reasoning effort, and thinking mode to `agent_zero`, `sub_agents`, and `distillation`; Studio Leader currently reuses `agent_zero`.

### Model Configuration Rules

- `name` is the Provider name used by AStudio. It also becomes the prefix used in role routing, for example `deepseek/deepseek-chat`.
- `litellm_provider` is the actual LiteLLM routing prefix. For OpenAI-compatible gateways, `name` can be a custom identifier such as `siliconflow` or `oneapi`, while `litellm_provider` is usually `openai`.
- `models` are the local model names shown in the UI. Short names such as `qwen-max` are valid; AStudio stores them as `Provider Name/model`.
- `model_aliases` maps local model names to the real model IDs sent to LiteLLM.
- `model_display_names` only affects UI and task-record display. It does not affect the actual API call.
- `model_assignment.*.model` should preferably use `Provider Name/model` to avoid ambiguity when multiple Providers expose the same short model name.

Simple API-key Provider example:

```yaml
llm_providers:
  - name: deepseek
    api_key: "sk-..."
    endpoint: null
    models:
      - deepseek-chat
      - deepseek-coder
    model_aliases: {}
    model_display_names: {}
    is_oauth: false

model_assignment:
  agent_zero:
    model: deepseek/deepseek-chat
    reasoning_effort: high
    thinking_type: default
    thinking_budget_tokens: null
  sub_agents:
    model: deepseek/deepseek-chat
    reasoning_effort: low
    thinking_type: default
    thinking_budget_tokens: null
  distillation:
    model: deepseek/deepseek-chat
    reasoning_effort: default
    thinking_type: default
    thinking_budget_tokens: null
```

OpenAI-compatible gateway example:

```yaml
llm_providers:
  - name: siliconflow
    litellm_provider: openai
    api_key: "sk-..."
    endpoint: "https://api.siliconflow.cn/v1"
    models:
      - qwen-max
    model_aliases:
      qwen-max: Qwen/Qwen3-235B-A22B-Instruct-2507
    model_display_names:
      qwen-max: "Qwen Max (SiliconFlow)"

model_assignment:
  agent_zero:
    model: siliconflow/qwen-max
    reasoning_effort: high
    thinking_type: default
    thinking_budget_tokens: null
  sub_agents:
    model: siliconflow/qwen-max
    reasoning_effort: low
    thinking_type: default
    thinking_budget_tokens: null
  distillation:
    model: siliconflow/qwen-max
    reasoning_effort: default
    thinking_type: default
    thinking_budget_tokens: null
```

Reasoning fields:

- `reasoning_effort`: `default`, `none`, `minimal`, `low`, `medium`, `high`, or `xhigh`. Support depends on the model and Provider.
- `thinking_type`: `default`, `enabled`, or `adaptive`. This mainly applies to models that expose thinking controls.
- `thinking_budget_tokens`: thinking-token budget. Leave it empty to avoid setting one explicitly; Anthropic thinking models use this field.

Settings saved from the UI are hot-reloaded in normal use. If you edit `data/config.yaml` manually, reopen and save the Settings page or restart the backend so active workers pick up the new config.

Web Search supports DuckDuckGo, Brave, Tavily, SearXNG, and Jina. When normal search fails, the system can use the optional Playwright browser search; without Playwright, it tries a system Chrome / Edge / Chromium headless CLI fallback.

## Local Data

Common local paths:

- `data/`: tasks, config, database, sandboxes, attachments, and Skill bundles.
- `data/config.yaml`: model and search config.
- `data/studios/`: Studio and Sub-agent memory and role files.
- `data/sandboxes/`: task sandbox directories, run logs, and artifacts.
- `server/.venv/`: backend virtual environment.
- `web/dist/`: frontend build output.
- Electron user-data directory: config, database, logs, and sandboxes for packaged desktop builds.

These paths are covered by `.gitignore`. Do not commit API keys, databases, logs, sandbox artifacts, or build outputs.

## Development Commands

| Command | Description |
| --- | --- |
| `pnpm setup` | Install Node and Python dependencies |
| `pnpm setup:browser` | Optionally install Playwright Chromium browser-search support |
| `pnpm dev` | Start FastAPI and Vite together |
| `pnpm start` | Build frontend and serve it from the backend |
| `pnpm build:web` | Build the frontend |
| `pnpm electron:dev` | Electron + Vite development mode |
| `pnpm electron:start` | Build frontend, then start Electron |
| `pnpm electron:pack` | Create a local Electron package |
| `pnpm electron:pack:sidecar` | Build backend sidecar and package the desktop app |

## Contributing

Contributions are welcome around agent orchestration, Studio memory, task sandboxes, the Skill pool, model routing, the desktop app, and documentation. Please read [CONTRIBUTING.md](./CONTRIBUTING.md) before starting.

## License

AStudio is released under the [MIT License](./LICENSE).
