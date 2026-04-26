# Multi-Agent-Pipeline (MAP)

Multi-Agent Pipeline Framework — a three-layer orchestration system that runs continuously for 4-5 hours, integrating four core skills (bmad-evo, spec-kit, superpowers, multi-agent-pipeline) to automate system development, testing, and deployment via prompt-passing protocol.

## MAP Command Reference (`/map`)

All commands run as: `python scripts/engine_hook.py <command> [args]`

### Enable `/map` In Chat UI (OpenCode)

If `/map /?` works in terminal but has no response in chat, it usually means the
chat host has not registered the slash command yet.

For OpenCode Desktop v1.14.x (including v1.14.25), the simplest way is project-level command files:

1. Create this file in your repo:

```text
.opencode/commands/map.md
```

2. Put this content in it:

```md
---
description: Run MAP command via engine_hook.py
---

Run this MAP command and show terminal output:
!`python scripts/engine_hook.py $ARGUMENTS`
```

3. Restart/reload OpenCode, then use:

```text
/map /?
/map p /?
/map doc /?
```

Alternative manual registration (if your host has a custom command UI):

- Name: `map`
- Run:

```bash
python scripts/map_command_proxy.py {args}
```

3. If your host uses JSON-based command config, use `config/slash_commands.template.json`
   as a template and map it to your host's actual config location/format.

Notes:
- The proxy normalizes full-width `？` to `?`, so `/map /？` also works.
- You can always use terminal fallback: `python scripts/engine_hook.py /?`.

### Engine Commands

| Command | Description |
|---------|-------------|
| `/map <task description>` | Start engine for a task (simple tasks auto-skip to direct execution) |
| `/map status` | Show engine state + current project info + health |
| `/map pause [--reason X]` | Pause engine, save context to current project |
| `/map resume` | Resume engine from pause, load saved context |
| `/map stop [--reason X]` | Gracefully stop engine |
| `/map report` | Show last completed engine run report (JSON) |
| `/map overview` | Show all projects dashboard |

### Project Commands (`/map p`)

| Command | Description |
|---------|-------------|
| `/map p init <name> [--path <dir>] [--url <git-url>]` | Create project (auto-detect: new/link/clone) + auto-generate docs |
| `/map p <name>` | Switch to project (auto-pause old / auto-resume target) |
| `/map p list` | List all projects with status, health score, tech stack |
| `/map p status [name]` | Show project detail: info, health (5 dimensions), doc versions, progress |
| `/map p archive <name>` | Archive (freeze) project |
| `/map p remove <name>` | Remove project registration (does NOT delete source code) |
| `/map p assess [name]` | Consolidated assessment (health + gates + drift + risk + contamination) |
| `/map p deliver [name]` | Deliver project (auto-detect local/github by init mode) |

### Document Commands (`/map doc`)

| Command | Description |
|---------|-------------|
| `/map doc show [type]` | Show document content (design_doc\|work_breakdown\|progress_report\|timeline_plan\|constraints\|acceptance_criteria\|test_manual) |
| `/map doc log [type]` | Show document change history (timestamp, trigger, version, summary, lines changed) |
| `/map doc diff <type> <v1> <v2>` | Compare two versions of a document (unified diff) |

### Help Commands

| Command | Description |
|---------|-------------|
| `/map /?` | Show full command reference |
| `/map p /?` | Show project command details |
| `/map doc /?` | Show document command details |

### Notes

- **Unified init**: `/map p init <name>` auto-detects mode — no flags = new, `--path` = link local dir, `--url` = clone from GitHub.
- **Quick switch**: `/map p <name>` switches project, auto-pausing the old and auto-resuming the target.
- **Documents are auto-maintained**: No manual update command. The engine updates docs automatically during conversation.
- **Every doc change is tracked**: changelog + unified diff saved to `history/`, with trigger, version, and BMAD assessment.
- **Project isolation**: Each project stores its own docs, context snapshots, and progress in `.pipeline/projects/<id>/`.
- **Switch safety**: If engine is running, switching prompts user to choose pause/wait/continue.
- **Health scoring**: 5 dimensions (doc completeness, buildability, task completion, constraint adherence, activity), each 0-20, total 100.
- **Consolidated assessment**: `/map p assess` runs health + gates + drift + risk + contamination in one command.

## Quick Start

### 1. Install Dependencies

```bash
cd D:\Multi-Agent-Pipeline
pip install -r requirements.txt
```

### 2. Run Regression Baseline

```bash
python scripts/run_regression_baseline.py
```

This is the fixed regression baseline to run after every change.
It executes the stable pytest suite and excludes script-style E2E files:

```bash
python -m pytest tests -q --ignore=tests/test_e2e.py --ignore=tests/test_real_adapter_e2e.py
```

### 3. Optional Full E2E (slower)

```bash
python tests/test_e2e.py
python tests/test_real_adapter_e2e.py
```

### 4. Usage

```python
from pipeline import PipelineOrchestrator
from specs.spec_gate import SpecGate

gate = SpecGate(".specs")
orchestrator = PipelineOrchestrator(
    state_dir=".pipeline",
    skills={
        "bmad-evo": Bmad_Evo_Adapter(),
        "superpowers": SuperpowersAdapter(),
        "spec-kit": SpecKitAdapter(),
    },
    spec_gate=gate,
)

# Create pipeline — IntentGate analyzes description before ANALYZE
pipeline, next_action = orchestrator.create_pipeline(
    "Build a REST API for user authentication with JWT"
)

# Advance through phases
result = orchestrator.advance(pipeline.id, {"success": True})
```

## Architecture

### Three Layers

| Layer | Component | Responsibility |
|-------|-----------|---------------|
| **Layer 2** | PipelineOrchestrator | State machine with PDCA, human-in-the-loop, IntentGate, model routing |
| **Layer 1** | ResourceSchedulerAPI | Task queue, role registry, lock management, atomic persistence |
| **Layer 0** | Workers / ParallelExecutor | ThreadPoolExecutor-based concurrent skill execution |

### State Machine Flow

```
INIT (IntentGate) -> ANALYZE -> PLAN -> CONFIRM_PLAN (human) -> EXECUTE -> CHECK -> DECIDE (human)
  ^                                                                                    |
  |                                    -> EVOLVE -> VERIFY -> COMPLETED                |
  +------------------------------------------------------------------------------------+
```

### INIT Phase — IntentGate

Before entering ANALYZE, the IntentGate classifies the user's description:
- **Intent type**: build / fix / refactor / test / analyze / configure / document / deploy / review / migrate / optimize
- **Complexity class**: critical / complex / moderate / simple / trivial
- **Ambiguity detection**: triggers human clarification if description is vague
- **Entity extraction**: file paths, APIs, databases, infrastructure, auth patterns
- **Skill/role suggestions**: auto-selects appropriate skills and roles

### Model Routing (Category → Model)

Inspired by OMO's Category → Model routing:

| Category | Priority | Use Case | Temperature | Max Tokens |
|----------|----------|----------|-------------|------------|
| QUICK | 20 | Trivial tasks (format, rename) | 0.3 | 2048 |
| STANDARD | 50 | General implementation | 0.7 | 4096 |
| DEEP | 80 | Reviews, debugging, analysis | 0.5 | 8192 |
| ULTRABRAIN | 99 | Architecture, critical decisions | 0.4 | 16384 |

Custom routing via `loop_policy.register_model_route()` or context override.

## Core Components

### Engine Control Layer

| Component | File | Description |
|-----------|------|-------------|
| **EngineController** | `engine_controller.py` | Unified entry: evaluate→ignite→advance→pause/resume/stop |
| **CoolingSystem** | `cooling_system.py` | Three-tier context compression (50K/100K/150K tokens) + 5h auto-shutdown |
| **BrakeSystem** | `brake_system.py` | Pause/stop/abort three-level control + external signal file |
| **TransmissionBridge** | `transmission.py` | Project identification (Flask/Django/Vue/React/FastAPI detection) |

### Project Manage Module (`src/project_manage/`, 15 files)

| Component | File | Description |
|-----------|------|-------------|
| **ProjectRegistry** | `registry.py` | CRUD + lifecycle + current project pointer + health scoring |
| **ProjectInitializer** | `project_init.py` | Three-mode init: new (scaffold) / clone (git) / local (scan) |
| **ProjectDocsManager** | `docs_manager.py` | Versioned document registry + changelog + diff tracking |
| **ConstraintPackManager** | `packs.py` | Constraint pack engine with Python executable rules |
| **ChangeIngester** | `ingest.py` | External change ingestion |
| **DriftDetector** | `drift.py` | Constraint drift detection |
| **GateEvaluator** | `gates.py` | Six-dimension gate evaluation |
| **DeliveryManager** | `delivery.py` | Local delivery pipeline (draft→staged→gate→approved→promoted→verified) |
| **GitHubDeliveryManager** | `github_delivery.py` | GitHub PR delivery |
| **ChangeControlManager** | `change_control.py` | Risk assessment + contamination check + compressed backups + merge |
| **MetricsAggregator** | `metrics.py` | Cross-project dashboard KPIs |
| **ApprovalManager** | `approval.py` | Approval workflow |
| **AuditLogger** | `audit.py` | Append-only JSONL audit trail |
| **models.py** | — | 8 dataclasses + 6 enums |

### Skill Adapter (`.skills/project-manage/`)

| Component | File | Description |
|-----------|------|-------------|
| **ProjectManage_Adapter** | `adapter.py` | 30 actions: init, lifecycle, docs, packs, drift, gates, delivery, metrics, health, overview, assess |

### Core Components

| Component | File | Description |
|-----------|------|-------------|
| **PipelineOrchestrator** | `pipeline_orchestrator.py` | State machine + PDCA + AgentLoop + IntentGate + ParallelExecutor |
| **IntentGate** | `intent_gate.py` | Pre-analysis intent classification (11 types, 5 complexity classes) |
| **LoopPolicy** | `loop_policy.py` | AgentLoop behavior + model routing per role/skill/level |
| **ParallelExecutor** | `parallel_executor.py` | ThreadPoolExecutor concurrent execution with fallback |
| **CodeAnalyzer** | `code_analyzer.py` | Pipeline-level AST engine (12 rules, 3 modes, scoring) |
| **HashlineEditTool** | `hashline_edit.py` | Content-hash-verified file editing (OMO-inspired) |
| **AgentLoop** | `agent_loop.py` | Execute-evaluate-refine cycle with escalation |
| **PromptManager** | `prompt_manager.py` | 14 templates, 12 shared sections, composable prompts |
| **PromptSession** | `prompt_session.py` | Multi-round prompt-passing session management |
| **SubagentDispatcher** | `subagent_dispatcher.py` | Bridges pipeline tasks to opencode Task tool |
| **WorktreeManager** | `worktree_manager.py` | Git worktree isolation for parallel subagents |
| **SpecGate** (in specs/) | `../specs/spec_gate.py` | Lifecycle hooks + progressive context injection + two-stage review |
| **ExecutionEvaluator** | `execution_evaluator.py` | 6-dimension evaluation with spec/TDD/review checks |
| **CheckpointManager** | `checkpoint_manager.py` | Snapshot/restore for failure recovery |
| **ContextManager** | `context_manager.py` | Context compression and long memory |

### Shared Services (Pipeline-Level)

Decoupled from individual skills, available to all:

- **CodeAnalyzer**: AST-based code quality engine (extracted from bmad-evo)
- **SpecGate**: Lifecycle hooks + spec-gated execution middleware
- **PromptManager**: Unified prompt template system across all skills
- **IntentGate**: Pre-analysis intent classification

## Four Core Skills

### BMAD-EVO (Analysis & Design)
- Deep requirement analysis via multi-agent Q&A
- Task decomposition with dependency graphs
- Constraint generation for spec-kit
- Referenced via `sys.path` — original code at `D:/bmad-evo` is never modified

### Spec-Kit (Specification Management)
- Agent Reasoning Maps (system/service level)
- Semantic constraints (format/contract/behavior)
- WHEN/THEN scenario tracking
- Spec evolution analysis
- **LifecycleHookRegistry** with 6 lifecycle points and chained handlers

### Superpowers (Engineering Execution)
- Two-stage review: spec compliance → code quality (using CodeAnalyzer AST engine)
- TDD enforcement: RED → GREEN → REFACTOR
- 4-phase systematic debugging
- HashlineEditTool integration for hash-verified file editing

### Multi-Agent-Pipeline (Pipeline Orchestration)
- PipelineOrchestrator state machine with PDCA cycles
- ParallelExecutor for concurrent task execution
- Model routing (QUICK/STANDARD/DEEP/ULTRABRAIN)
- IntentGate pre-analysis in INIT phase

### Writing-Skills (Meta-Skill)
- Scaffold, validate, and upgrade skill adapters
- Hierarchical `init_deep`: recursive AGENTS.md generation with AST symbol extraction
- Generate adapter code and SKILL.md from specifications

## Test Suite

```
519 tests passed (518 pytest + 1 e2e runner)
```

| Suite | File | Tests | Description |
|-------|------|-------|-------------|
| Core E2E | `test_e2e.py` | 17 | Engine control, worktree, adapter integration |
| Project Manage E2E | `test_project_manager_e2e.py` | 13 | Init, switch, health, docs, changelog, auto pause/resume, CLI |
| Adapter Contract | `test_multi_agent_adapter_contract.py` | 3 | Multi-agent-pipeline adapter actions |
| Regression | `tests/` (pytest) | ~505 | All pipeline modules, skills, specs |

## Project Structure

```
Multi-Agent-Pipeline/
├── README.md
├── SKILL.md
├── scripts/
│   └── engine_hook.py               # /map CLI entry point
├── .skills/                          # Skill adapters
│   ├── project-manage/
│   │   └── adapter.py                # 30 actions: projects, docs, packs, delivery, health
│   ├── bmad-evo/
│   │   ├── adapter.py                # v3.1: 6 actions, prompt-passing
│   │   └── SKILL.md
│   ├── spec-kit/
│   │   ├── adapter.py                # v2.0: 9 actions
│   │   └── SKILL.md
│   ├── superpowers/
│   │   ├── adapter.py                # v2.3: 6 actions + hashline + CodeAnalyzer
│   │   └── SKILL.md
│   ├── multi-agent-pipeline/
│   │   ├── adapter.py                # v2.1: 13 actions
│   │   └── SKILL.md
│   ├── writing-skills/
│   │   └── adapter.py                # v1.1: 6 actions + init_deep
│   ├── orchestrator/
│   │   └── SKILL.md
│   └── data-processor/
│       └── adapter.py
├── src/
│   ├── pipeline/                     # Core pipeline module
│   │   ├── __init__.py               # Exports all public classes
│   │   ├── models.py                 # Data models (Task, Role, Pipeline, enums)
│   │   ├── pipeline_orchestrator.py  # State machine + PDCA + IntentGate
│   │   ├── engine_controller.py      # Unified engine control (ignite/advance/pause/stop)
│   │   ├── cooling_system.py         # Three-tier context compression + 5h auto-shutdown
│   │   ├── brake_system.py           # Pause/stop/abort three-level control
│   │   ├── transmission.py           # Project identification + skill adapter scaffolding
│   │   ├── intent_gate.py            # Intent classification (11 types)
│   │   ├── loop_policy.py            # Loop behavior + model routing
│   │   ├── parallel_executor.py      # Concurrent task execution
│   │   ├── code_analyzer.py          # AST engine (12 rules, 3 modes)
│   │   ├── hashline_edit.py          # Hash-verified file editing
│   │   ├── agent_loop.py             # Execute-evaluate-refine cycle
│   │   ├── execution_evaluator.py    # 6-dimension evaluation
│   │   ├── prompt_manager.py         # Unified template system
│   │   ├── prompt_session.py         # Multi-round session management
│   │   ├── subagent_dispatcher.py    # Opencode Task tool bridge
│   │   ├── worktree_manager.py       # Git worktree isolation
│   │   ├── base_worker.py            # Worker pool + skill proxy
│   │   ├── scheduler_api.py          # Layer 1 facade API
│   │   ├── task_queue.py             # File-persisted queue
│   │   ├── role_registry.py          # Dynamic role registration
│   │   ├── context_manager.py        # Context compression
│   │   ├── checkpoint_manager.py     # Snapshot/restore
│   │   ├── lock_manager.py           # Windows-compatible locks
│   │   └── metrics.py                # Pipeline metrics
│   ├── project_manage/               # Multi-project governance module
│   │   ├── __init__.py
│   │   ├── models.py                 # 8 dataclasses + 6 enums
│   │   ├── registry.py               # Project CRUD + lifecycle + current pointer + health
│   │   ├── project_init.py           # Three-mode init (new/clone/local)
│   │   ├── docs_manager.py           # Versioned docs + changelog + diff
│   │   ├── packs.py                  # Constraint pack engine
│   │   ├── ingest.py                 # External change ingestion
│   │   ├── drift.py                  # Constraint drift detection
│   │   ├── gates.py                  # Six-dimension gate evaluation
│   │   ├── delivery.py               # Local delivery pipeline
│   │   ├── github_delivery.py        # GitHub PR delivery
│   │   ├── change_control.py         # Risk + contamination + backup + merge
│   │   ├── metrics.py                # Cross-project dashboard KPIs
│   │   ├── approval.py               # Approval workflow
│   │   └── audit.py                  # Append-only JSONL audit trail
│   ├── specs/                        # Spec-Kit core module
│   │   ├── spec_gate.py              # Lifecycle hooks + SpecGate
│   │   ├── spec_evolution.py         # Two-stage analysis
│   │   └── ...                       # spec_manager, reasoning_map, etc.
│   └── orchestrator/                 # Top-level orchestrator
│       └── ...                       # core_orchestrator, skill_loader, etc.
└── tests/
    ├── test_e2e.py                   # Core E2E (17 tests)
    ├── test_project_manager_e2e.py   # Project manage E2E (13 tests)
    ├── test_multi_agent_adapter_contract.py
    └── ...                           # Pytest regression suite
```

## Design Principles

1. **No external AI calls** — prompt-passing protocol; opencode agent IS the model
2. **Windows compatible** — no `fcntl`, atomic file writes via `tempfile + os.replace`
3. **Dynamic roles** — generated from bmad-evo analysis, not predefined
4. **Decoupled skills** — shared services live in the pipeline layer, not in individual skills
5. **Benchmark against OMO** — best ideas backported (HashlineEdit, model routing, AST analysis)

## OMO Comparison (Backported Features)

| OMO Feature | MAP Equivalent | Status |
|-------------|---------------|--------|
| Hashline Edit Tool | `HashlineEditTool` | Done |
| Category → Model routing | `ModelCategory` + `ModelRoute` + `LoopPolicy` | Done |
| 25+ Hooks | `LifecycleHookRegistry` (6 points) | Done |
| AST-Grep | `CodeAnalyzer` (12 rules, 3 modes) | Done |
| Ralph Loop | `AgentLoop` with pass_threshold + escalation | Done |

## Related Projects

- [BMAD-EVO](https://github.com/Lizhiyu-foshan/bmad-evo) — Constraint-driven analysis framework
- [Superpowers](https://github.com/obra/superpowers) — Engineering skills framework (145K+ stars)
- [Spec-Kit](https://github.com/fission-ai/openspec) — Spec-driven development toolkit
- [Oh-My-OpenAgent](https://github.com/code-yeongyu/oh-my-openagent) — Harness framework (50K+ stars, benchmarked)

## License

MIT License
