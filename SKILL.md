# Multi-Agent-Pipeline Skill

## Version
v2.1

## Description
Three-layer multi-agent orchestration system that drives long-running (3-5 hour) development pipelines through prompt-passing protocol. Integrates BMAD-EVO, Spec-Kit, Superpowers, and Multi-Agent-Pipeline skills with PDCA quality loops, IntentGate pre-analysis, model routing, and parallel execution.

## Capabilities
- Intent analysis (11 types, 5 complexity classes, ambiguity detection)
- Model routing (QUICK/STANDARD/DEEP/ULTRABRAIN)
- Parallel task execution via ThreadPoolExecutor
- AST-based code quality analysis (12 rules, 3 modes)
- Hash-verified file editing
- Lifecycle hooks with chained handlers (6 points)
- Multi-round prompt-passing sessions
- PDCA quality loop with checkpoint/recovery
- Dynamic role generation from analysis results
- Git worktree isolation for parallel subagents

## Architecture

### Three Layers
- **Layer 2 (PipelineOrchestrator)**: State machine with PDCA, IntentGate, model routing, ParallelExecutor
- **Layer 1 (ResourceSchedulerAPI)**: Task queue, role registry, lock management, atomic file persistence
- **Layer 0 (Workers)**: SkillProxyWorkers + ParallelExecutor threads

### State Machine Flow
```
INIT (IntentGate) -> ANALYZE -> PLAN -> CONFIRM_PLAN (human) -> EXECUTE -> CHECK -> DECIDE (human)
  ^                                                                                    |
  |                                    -> EVOLVE -> VERIFY -> COMPLETED                |
  +------------------------------------------------------------------------------------+
```

## Actions

| Action | Description |
|--------|-------------|
| `create_pipeline` | Create pipeline from description (IntentGate runs in INIT) |
| `advance` | Advance pipeline with phase result |
| `resume_model_request` | Resume multi-round prompt-passing session |
| `get_active_session` | Get active model request session for pipeline |
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
| `pipeline_id` | advance, resume_model_request, get_status | Pipeline identifier |
| `phase_result` | advance | Result from previous phase |
| `decision` | human_decision | User choice (A/B/C/D) |
| `model_response` | resume_model_request | Model inference response |
| `session_id` | resume_model_request | Prompt-passing session ID |
| `max_duration_hours` | create_pipeline | Maximum run time (default 5h) |

## Integration with Other Skills

- **bmad-evo**: Analysis phase — role discovery, task planning, constraint generation
- **spec-kit**: Evolution/verification — spec management, LifecycleHookRegistry, SpecGate
- **superpowers**: Execution — task implementation, two-stage review, TDD, debugging
- **CodeAnalyzer**: Pipeline-level shared service, used by superpowers for code quality
- **IntentGate**: INIT phase — intent classification before ANALYZE

## Files

| File | Description |
|------|-------------|
| `src/pipeline/pipeline_orchestrator.py` | State machine + PDCA + IntentGate + ParallelExecutor + model routing |
| `src/pipeline/intent_gate.py` | Pre-analysis intent classification (11 types, 5 complexity classes) |
| `src/pipeline/loop_policy.py` | LoopConfig + ModelRoute + ModelCategory + custom routing API |
| `src/pipeline/parallel_executor.py` | ThreadPoolExecutor concurrent execution with fallback |
| `src/pipeline/code_analyzer.py` | AST engine (12 rules, 3 modes, scoring) |
| `src/pipeline/hashline_edit.py` | Content-hash-verified file editing |
| `src/pipeline/agent_loop.py` | Execute-evaluate-refine cycle with escalation |
| `src/pipeline/prompt_manager.py` | 14 templates, 12 shared sections |
| `src/pipeline/prompt_session.py` | Multi-round prompt-passing session management |
| `src/pipeline/subagent_dispatcher.py` | Opencode Task tool bridge |
| `src/pipeline/worktree_manager.py` | Git worktree isolation |
| `src/pipeline/execution_evaluator.py` | 6-dimension evaluation |
| `src/pipeline/models.py` | Data models (Task, Role, Pipeline, enums) |
| `src/pipeline/scheduler_api.py` | Layer 1 facade API |
| `src/pipeline/task_queue.py` | File-persisted queue with atomic writes |
| `src/pipeline/role_registry.py` | Dynamic role registration |
| `src/pipeline/context_manager.py` | Context compression and long memory |
| `src/pipeline/checkpoint_manager.py` | Snapshot/restore for recovery |
| `src/pipeline/lock_manager.py` | Windows-compatible file locks |
| `src/pipeline/base_worker.py` | Worker pool + skill proxy |
| `.skills/multi-agent-pipeline/adapter.py` | Skill adapter (v2.1, 13 actions) |

## Tests

31 tests, 499 assertions, all passing.

```bash
python tests/test_e2e.py
```
