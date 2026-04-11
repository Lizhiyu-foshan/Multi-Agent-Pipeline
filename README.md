# Multi-Agent-Pipeline (MAP)

Multi-Agent Pipeline Framework — a three-layer orchestration system that runs continuously for 4-5 hours, integrating four core skills (bmad-evo, spec-kit, superpowers, multi-agent-pipeline) to automate system development, testing, and deployment via prompt-passing protocol.

## Quick Start

### 1. Install Dependencies

```bash
cd D:\Multi-Agent-Pipeline
pip install -r requirements.txt
```

### 2. Run Tests

```bash
python tests/test_e2e.py
```

### 3. Usage

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

### Pipeline Module (`src/pipeline/`, 21 files)

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
31 tests, 499 assertions, all passing
```

| # | Test | Assertions |
|---|------|-----------|
| 1-2 | Pipeline lifecycle + AgentLoop | ~25 |
| 3-4 | LoopPolicy + PromptManager | ~20 |
| 5-10 | Skill adapters + orchestrator integration | ~50 |
| 11-13 | Subagent dispatch + parallel execution | ~25 |
| 14-16 | Multi-round prompt passing + sessions | ~30 |
| 17-18 | Worktree + writing-skills | ~25 |
| 19-20 | HashlineEditTool + integration | ~35 |
| 21-22 | Lifecycle hooks + orchestrator triggering | ~25 |
| 23-24 | CodeAnalyzer AST + superpowers integration | ~40 |
| 25-26 | ParallelExecutor + orchestrator parallel | ~35 |
| 27 | Hierarchical init_deep | ~35 |
| 28-29 | Model routing + orchestrator propagation | ~35 |
| 30-31 | IntentGate + orchestrator INIT integration | ~40 |

## Project Structure

```
Multi-Agent-Pipeline/
├── README.md
├── SKILL.md
├── .skills/                        # Five skills
│   ├── bmad-evo/
│   │   ├── adapter.py              # v3.1: 6 actions, prompt-passing
│   │   ├── model_bridge.py
│   │   ├── prompt_pass.py
│   │   └── SKILL.md
│   ├── spec-kit/
│   │   ├── adapter.py              # v2.0: 9 actions
│   │   └── SKILL.md
│   ├── superpowers/
│   │   ├── adapter.py              # v2.3: 6 actions + hashline + CodeAnalyzer
│   │   ├── prompts/                # 4 markdown templates
│   │   └── SKILL.md
│   ├── multi-agent-pipeline/
│   │   ├── adapter.py              # v2.1: 13 actions
│   │   └── SKILL.md
│   ├── writing-skills/
│   │   ├── adapter.py              # v1.1: 6 actions + init_deep
│   │   └── SKILL.md
│   └── orchestrator/
│       └── SKILL.md
├── src/
│   ├── pipeline/                   # Core pipeline module (21 files)
│   │   ├── __init__.py             # Exports all public classes
│   │   ├── models.py               # Data models (Task, Role, Pipeline, enums)
│   │   ├── pipeline_orchestrator.py # State machine + PDCA + IntentGate
│   │   ├── intent_gate.py          # Intent classification (11 types)
│   │   ├── loop_policy.py          # Loop behavior + model routing
│   │   ├── parallel_executor.py    # Concurrent task execution
│   │   ├── code_analyzer.py        # AST engine (12 rules, 3 modes)
│   │   ├── hashline_edit.py        # Hash-verified file editing
│   │   ├── agent_loop.py           # Execute-evaluate-refine cycle
│   │   ├── execution_evaluator.py  # 6-dimension evaluation
│   │   ├── prompt_manager.py       # Unified template system
│   │   ├── prompt_session.py       # Multi-round session management
│   │   ├── subagent_dispatcher.py  # Opencode Task tool bridge
│   │   ├── worktree_manager.py     # Git worktree isolation
│   │   ├── base_worker.py          # Worker pool + skill proxy
│   │   ├── scheduler_api.py        # Layer 1 facade API
│   │   ├── task_queue.py           # File-persisted queue
│   │   ├── role_registry.py        # Dynamic role registration
│   │   ├── context_manager.py      # Context compression
│   │   ├── checkpoint_manager.py   # Snapshot/restore
│   │   └── lock_manager.py         # Windows-compatible locks
│   ├── specs/                      # Spec-Kit core module
│   │   ├── spec_gate.py            # Lifecycle hooks + SpecGate
│   │   ├── spec_evolution.py       # Two-stage analysis
│   │   ├── spec_manager.py
│   │   ├── reasoning_map.py
│   │   ├── constraint_validator.py
│   │   └── scenario_tracker.py
│   └── orchestrator/               # Top-level orchestrator
│       ├── core_orchestrator.py
│       ├── complexity_evaluator.py
│       ├── skill_loader.py
│       ├── path_selector.py
│       └── report_generator.py
└── tests/
    └── test_e2e.py                 # 31 tests, 499 assertions
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
