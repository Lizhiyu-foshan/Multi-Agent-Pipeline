# Multi-Agent-Pipeline Skill

## Version
v2.1

## Description
Multi-Agent-Pipeline is a three-layer multi-agent orchestration system that drives long-running (3-5 hour) development pipelines through prompt-passing protocol. It manages the full pipeline lifecycle: intent analysis, task decomposition, parallel execution, PDCA quality loops, and spec evolution.

## Architecture

### Three Layers
- **Layer 2 (PipelineOrchestrator)**: State machine with PDCA cycles, IntentGate, model routing, human-in-the-loop decisions, checkpoint/recovery
- **Layer 1 (ResourceSchedulerAPI)**: Task queue, role registry, lock management, atomic file persistence
- **Layer 0 (Workers / ParallelExecutor)**: ThreadPoolExecutor-based concurrent skill execution

### State Machine Flow
```
INIT (IntentGate) -> ANALYZE -> PLAN -> CONFIRM_PLAN (human) -> EXECUTE -> CHECK -> DECIDE (human)
  ^                                                                                    |
  |                                    -> EVOLVE -> VERIFY -> COMPLETED                |
  +------------------------------------------------------------------------------------+
```

### INIT Phase — IntentGate
Before entering ANALYZE, IntentGate classifies the pipeline description:
- **11 intent types**: build, fix, refactor, test, analyze, configure, document, deploy, review, migrate, optimize
- **5 complexity classes**: critical, complex, moderate, simple, trivial
- **Ambiguity detection**: triggers human clarification loop if description is vague
- **Entity extraction**: file paths, APIs, databases, infrastructure, auth patterns
- **Skill/role suggestions**: auto-selects appropriate skills and roles for downstream phases

### Model Routing (Category → Model)
Each LoopConfig carries a ModelRoute indicating which model category to use:

| Category | Priority | Use Case |
|----------|----------|----------|
| QUICK | 20 | Trivial tasks (format, rename) |
| STANDARD | 50 | General implementation |
| DEEP | 80 | Reviews, debugging, analysis |
| ULTRABRAIN | 99 | Architecture, critical decisions |

Custom routing via `register_model_route()` or context override.

### Key Design Decisions
- **Windows compatible**: No fcntl, uses atomic file writes (tempfile + os.replace)
- **Prompt-passing protocol**: No external AI calls, returns prompt requests to opencode agent
- **Dynamic roles**: Roles created from bmad-evo analysis results, not predefined
- **Context compression**: Prevents context explosion during long runs
- **Checkpoint/recovery**: Full snapshots at decision points, restore on failure
- **ParallelExecutor**: ThreadPoolExecutor for concurrent task execution with sequential fallback
- **CodeAnalyzer**: Pipeline-level AST engine shared across skills (12 rules, 3 modes)
- **HashlineEditTool**: Content-hash-verified file editing (OMO-inspired)

## Actions

| Action | Description |
|--------|-------------|
| `create_pipeline` | Create a new pipeline from description |
| `advance` | Advance pipeline with phase result |
| `resume_model_request` | Resume a multi-round prompt-passing session |
| `get_active_session` | Get active model request session for a pipeline |
| `human_decision` | Submit human decision (A/B/C/D) |
| `get_status` | Get pipeline status and progress |
| `list_pipelines` | List all pipelines |
| `resume` | Resume a paused pipeline |
| `cleanup` | Clean up expired locks and old checkpoints |
| `list_prompts` | List available prompt templates |
| `render_prompt` | Render a prompt template |
| `create_pipeline_via_adapter` | Create pipeline through skill adapter |
| `list_pipelines_via_adapter` | List pipelines through skill adapter |

## Context Parameters

| Parameter | Actions | Description |
|-----------|---------|-------------|
| `pipeline_id` | advance, resume_model_request, get_status, resume | Pipeline identifier |
| `phase_result` | advance | Result from previous phase execution |
| `decision` | human_decision | User choice (A/B/C/D) |
| `model_response` | resume_model_request | Model inference response text |
| `session_id` | resume_model_request | Prompt-passing session ID |
| `max_duration_hours` | create_pipeline | Maximum run time (default 5h) |

## Integration with Other Skills

- **bmad-evo**: Analysis phase calls bmad-evo for role discovery and task planning
- **spec-kit**: Evolution/verification phases call spec-kit for spec management
- **superpowers**: Execution phase delegates implementation tasks to superpowers
- **SpecGate**: Integrated via orchestrator's SpecGate middleware with LifecycleHookRegistry
- **CodeAnalyzer**: Shared AST service, used by superpowers for code_quality_review
- **IntentGate**: Runs in INIT phase to classify intent before ANALYZE

## Files

| File | Description |
|------|-------------|
| `src/pipeline/models.py` | Data models (Task, Role, Pipeline, Checkpoint, enums) |
| `src/pipeline/pipeline_orchestrator.py` | State machine + PDCA + IntentGate + model routing |
| `src/pipeline/intent_gate.py` | Pre-analysis intent classification |
| `src/pipeline/loop_policy.py` | LoopConfig + ModelRoute + custom routing API |
| `src/pipeline/parallel_executor.py` | Concurrent task execution with fallback |
| `src/pipeline/code_analyzer.py` | AST engine (12 rules, 3 modes, scoring) |
| `src/pipeline/hashline_edit.py` | Hash-verified file editing |
| `src/pipeline/agent_loop.py` | Execute-evaluate-refine cycle |
| `src/pipeline/execution_evaluator.py` | 6-dimension evaluation |
| `src/pipeline/prompt_manager.py` | Unified template system (14 templates) |
| `src/pipeline/prompt_session.py` | Multi-round session management |
| `src/pipeline/subagent_dispatcher.py` | Opencode Task tool bridge |
| `src/pipeline/worktree_manager.py` | Git worktree isolation |
| `src/pipeline/lock_manager.py` | Windows-compatible file locking |
| `src/pipeline/task_queue.py` | File-persisted task queue with atomic writes |
| `src/pipeline/role_registry.py` | Dynamic role registration |
| `src/pipeline/scheduler_api.py` | Layer 1 facade API |
| `src/pipeline/context_manager.py` | Context compression and long memory |
| `src/pipeline/checkpoint_manager.py` | Snapshot/restore for recovery |
| `src/pipeline/base_worker.py` | Worker pool + skill proxy |
| `.skills/multi-agent-pipeline/adapter.py` | Skill adapter (v2.1, 13 actions) |

## Tests

31 tests, 499 assertions — run with `python tests/test_e2e.py`
