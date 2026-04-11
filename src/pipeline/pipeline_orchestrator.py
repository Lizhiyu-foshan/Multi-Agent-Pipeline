"""
Pipeline Orchestrator - State machine with PDCA and human-in-the-loop.

Key design differences from reference:
- Prompt-passing protocol instead of external AI calls
- Human decision points at critical phases
- Checkpoint/recovery integrated into state transitions
- Context compression for long-running pipelines
- Dynamic role creation from analysis results
- PromptManager for unified prompt template management
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import (
    Checkpoint,
    DateTimeEncoder,
    DecisionPoint,
    HumanDecision,
    PipelinePhase,
    PipelineRun,
    PipelineState,
    Task,
)
from .scheduler_api import ResourceSchedulerAPI
from .context_manager import ContextManager
from .checkpoint_manager import CheckpointManager
from .execution_evaluator import ExecutionEvaluator
from .agent_loop import AgentLoop, LoopOutcome
from .loop_policy import (
    LoopPolicy,
    LoopConfig,
    LoopMode,
    ExecutionLevel,
    ModelCategory,
    ModelRoute,
)
from .prompt_manager import PromptManager
from .subagent_dispatcher import SubagentDispatcher, find_parallel_ready_tasks
from .prompt_session import (
    PromptPassingSession,
    SessionManager,
    create_session_from_pending,
)
from .parallel_executor import ParallelExecutor, ParallelBatchResult
from .intent_gate import (
    IntentGate,
    IntentResult,
    IntentType,
    ComplexityClass,
    AmbiguityLevel,
)

try:
    from .worktree_manager import WorktreeManager
except ImportError:
    WorktreeManager = None

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Central orchestrator for multi-agent pipelines.

    State machine flow:
    INIT -> ANALYZE -> PLAN -> CONFIRM_PLAN (human) -> EXECUTE -> CHECK -> DECIDE (human)
      ^                                                                    |
      |                    -> EVOLVE -> VERIFY -> COMPLETED                |
      +--------------------------------------------------------------------+

    Lifecycle hooks (via SpecGate):
    - on_pipeline_start: emitted when pipeline enters ANALYZE
    - on_task_start: emitted before each task executes (chained with pre_inject)
    - on_task_complete: emitted after each task completes (chained with post_check)
    - on_pdca_cycle: emitted at PDCA CHECK phase
    - on_pipeline_complete: emitted when pipeline reaches COMPLETED
    - on_error: emitted on task failure or pipeline failure
    """

    def __init__(
        self,
        state_dir: str = None,
        skills: Dict[str, Any] = None,
        spec_gate: Any = None,
    ):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self.state_dir = state_dir

        state_path = Path(state_dir) / "state"
        lock_path = Path(state_dir) / "locks"
        context_path = Path(state_dir) / "context"
        checkpoint_path = Path(state_dir) / "checkpoints"

        self.scheduler = ResourceSchedulerAPI(
            state_dir=str(state_path), lock_dir=str(lock_path)
        )
        self.context = ContextManager(state_dir=str(context_path))
        self.checkpoint_mgr = CheckpointManager(state_dir=str(checkpoint_path))
        self.skills = skills or {}

        self.spec_gate = spec_gate

        self.evaluator = ExecutionEvaluator()
        self.parallel_executor = ParallelExecutor(max_workers=3, timeout_per_task=120.0)
        self.loop_policy = LoopPolicy()
        self.intent_gate = IntentGate(
            project_path=str(Path(state_dir).parent) if state_dir else None
        )
        self.prompt_manager = PromptManager(
            project_path=str(Path(state_dir).parent) if state_dir else None
        )
        self.session_manager = SessionManager(
            state_dir=str(Path(state_dir) / "sessions")
        )
        self.worktree_manager = (
            WorktreeManager(repo_root=str(Path(state_dir).parent))
            if WorktreeManager
            else None
        )
        self.subagent_dispatcher = SubagentDispatcher(
            prompt_manager=self.prompt_manager,
            worktree_manager=self.worktree_manager,
        )

        self.pipelines: Dict[str, PipelineRun] = {}
        self._load_pipelines()

    def _load_pipelines(self):
        pipe_file = Path(self.state_dir) / "state" / "pipelines.json"
        if pipe_file.exists():
            try:
                with open(pipe_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for pid, pdata in data.items():
                    self.pipelines[pid] = PipelineRun.from_dict(pdata)
                logger.info(f"Loaded {len(self.pipelines)} pipelines")
            except Exception as e:
                logger.error(f"Failed to load pipelines: {e}")

    def _save_pipelines(self):
        pipe_file = Path(self.state_dir) / "state" / "pipelines.json"
        try:
            os.makedirs(pipe_file.parent, exist_ok=True)
            data = {pid: p.to_dict() for pid, p in self.pipelines.items()}
            fd, tmp = os.path.join(str(pipe_file.parent), "_tmp_pipes.json"), None
            import tempfile

            fd, tmp = tempfile.mkstemp(dir=str(pipe_file.parent), suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(
                        data, f, indent=2, ensure_ascii=False, cls=DateTimeEncoder
                    )
                os.replace(tmp, str(pipe_file))
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except Exception as e:
            logger.error(f"Failed to save pipelines: {e}")

    # ===== Pipeline Lifecycle =====

    def create_pipeline(
        self,
        description: str,
        max_duration_hours: float = 5.0,
    ) -> Tuple[PipelineRun, Dict[str, Any]]:
        """
        Create a new pipeline run. Returns (pipeline, next_action).
        Next action should be 'analyze' to kick off the bmad-evo analysis.
        """
        pipeline = PipelineRun(
            description=description,
            state=PipelineState.IDLE,
            phase=PipelinePhase.INIT,
            max_duration_hours=max_duration_hours,
        )
        self.pipelines[pipeline.id] = pipeline
        self._save_pipelines()

        self.context.add_entry(
            pipeline.id, "", "orchestrator", "init", f"Pipeline created: {description}"
        )

        try:
            prompt = self.prompt_manager.render(
                "pipeline/analyze",
                description=description,
                spec_context="",
            )
        except Exception:
            prompt = (
                f"Analyze the following project description and provide:\n"
                f"1. Role definitions (type, name, capabilities)\n"
                f"2. Task breakdown with dependencies\n"
                f"3. Estimated complexity\n\n"
                f"Description: {description}"
            )

        return pipeline, {
            "action": "analyze",
            "pipeline_id": pipeline.id,
            "prompt": prompt,
        }

    def advance(self, pipeline_id: str, phase_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Advance pipeline to next phase based on current phase result.

        Returns a dict with:
        - 'action': what to do next ('execute_task', 'human_decision', 'analyze', etc.)
        - 'prompt': if action needs prompt-passing, the prompt to use
        - 'options': if human_decision, the available choices
        - 'phase': new phase
        - 'completed': if pipeline is done
        """
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline:
            return {"error": f"Pipeline {pipeline_id} not found"}

        if self._is_timed_out(pipeline):
            return self._handle_timeout(pipeline)

        phase = PipelinePhase(pipeline.phase)
        self.context.add_entry(
            pipeline.id,
            phase_result.get("task_id", ""),
            "orchestrator",
            phase.value,
            f"Phase result: {json.dumps(phase_result, ensure_ascii=False)[:500]}",
        )

        handler = self._phase_handlers.get(phase)
        if handler:
            return handler(self, pipeline, phase_result)
        return {"error": f"No handler for phase: {phase}"}

    # ===== Phase Handlers =====

    def _handle_init(self, pipeline: PipelineRun, result: Dict) -> Dict:
        # Handle response from clarification human_decision
        decision = result.get("decision")
        if decision and pipeline.phase == PipelinePhase.INIT:
            if decision.upper() == "B":
                clarified = result.get("clarified_description", "")
                if clarified:
                    pipeline.description = clarified
                    pipeline.artifacts["intent"] = {
                        "clarified": True,
                        "original_description": pipeline.description,
                    }
                    self._save_pipelines()
            # For "A" or after clarification update, fall through to ANALYZE

        intent_result: Optional[IntentResult] = None

        if hasattr(self, "intent_gate") and self.intent_gate:
            # Skip re-analysis if we already got a decision to proceed
            if not decision:
                intent_result = self.intent_gate.analyze(
                    pipeline.description,
                    context={"pipeline_id": pipeline.id},
                )
                pipeline.artifacts["intent"] = intent_result.to_dict()
                self._save_pipelines()

                if intent_result.needs_clarification:
                    pipeline.phase = PipelinePhase.INIT
                    self._save_pipelines()

                    questions = intent_result.clarification_questions
                    summary = self._build_intent_summary(intent_result)
                    options = ["A", "B"]
                    option_lines = [
                        summary,
                        "",
                        "The intent analysis found ambiguity or missing information:",
                    ]
                    for i, q in enumerate(questions, 1):
                        option_lines.append(f"  {i}. {q}")
                    option_lines.extend(
                        [
                            "",
                            "[A] Proceed anyway - use detected intent and continue to analysis",
                            "[B] Clarify - provide a more detailed description",
                        ]
                    )
                    if intent_result.prerequisite_issues:
                        option_lines.append("")
                        option_lines.append("Prerequisite issues:")
                        for iss in intent_result.prerequisite_issues:
                            option_lines.append(f"  ! {iss}")

                    return {
                        "action": "human_decision",
                        "phase": pipeline.phase,
                        "pipeline_id": pipeline.id,
                        "question": "\n".join(option_lines),
                        "options": options,
                        "intent_result": intent_result.to_dict(),
                        "context_summary": summary,
                    }
            else:
                # Decision was made, use stored intent if available
                stored_intent = pipeline.artifacts.get("intent")
                if isinstance(stored_intent, dict) and "intent_type" in stored_intent:
                    intent_result = IntentResult.from_dict(stored_intent)

        pipeline.phase = PipelinePhase.ANALYZE
        pipeline.state = PipelineState.RUNNING
        pipeline.started_at = datetime.now()
        self._save_pipelines()

        self._emit_lifecycle(
            "on_pipeline_start",
            {
                "pipeline_id": pipeline.id,
                "description": pipeline.description,
                "intent": intent_result.to_dict() if intent_result else None,
            },
        )

        try:
            prompt = self.prompt_manager.render(
                "pipeline/analyze",
                description=pipeline.description,
                spec_context="",
            )
        except Exception:
            prompt = pipeline.description

        enrich = {}
        if intent_result:
            enrich["intent_result"] = intent_result.to_dict()

        return {
            "action": "call_skill",
            "skill": "bmad-evo",
            "action_type": "analyze",
            "prompt": prompt,
            "pipeline_id": pipeline.id,
            "phase": pipeline.phase,
            **enrich,
        }

    def _build_intent_summary(self, intent_result: IntentResult) -> str:
        lines = [
            f"Intent: {intent_result.intent_type.value} "
            f"(confidence: {intent_result.confidence:.0%}, "
            f"complexity: {intent_result.complexity_class.value})",
        ]
        if intent_result.entities:
            lines.append(f"Entities: {', '.join(intent_result.entities[:5])}")
        if intent_result.suggested_skills:
            lines.append(
                f"Suggested skills: {', '.join(intent_result.suggested_skills)}"
            )
        if intent_result.suggested_roles:
            lines.append(f"Suggested roles: {', '.join(intent_result.suggested_roles)}")
        return "\n".join(lines)

    def _handle_analyze(self, pipeline: PipelineRun, result: Dict) -> Dict:
        if not result.get("success"):
            return self._handle_failure(pipeline, "Analysis failed", result)

        artifacts = result.get("artifacts", {})
        self.context.store_artifact(pipeline.id, "", "analysis", artifacts)

        roles_data = artifacts.get("roles", [])
        if roles_data:
            registered = self.scheduler.registry.register_from_analysis(
                {"roles": roles_data}
            )
            pipeline.roles = registered
            logger.info(f"Registered {len(registered)} roles from analysis")

        tasks_data = artifacts.get("tasks", [])
        if not tasks_data:
            return self._handle_failure(pipeline, "Analysis produced no tasks", result)

        pipeline.artifacts["analysis"] = artifacts
        pipeline.phase = PipelinePhase.PLAN
        self._save_pipelines()

        try:
            prompt = self.prompt_manager.render(
                "pipeline/plan",
                tasks_json=json.dumps(tasks_data, ensure_ascii=False)[:2000],
                roles_json=json.dumps(roles_data, ensure_ascii=False)[:1000],
                spec_context="",
            )
        except Exception:
            prompt = (
                f"Create detailed execution plan based on analysis.\n"
                f"Tasks: {json.dumps(tasks_data, ensure_ascii=False)[:2000]}\n"
                f"Roles: {json.dumps(roles_data, ensure_ascii=False)[:1000]}"
            )

        return {
            "action": "call_skill",
            "skill": "bmad-evo",
            "action_type": "plan",
            "prompt": prompt,
            "pipeline_id": pipeline.id,
            "phase": pipeline.phase,
        }

    def _handle_plan(self, pipeline: PipelineRun, result: Dict) -> Dict:
        if not result.get("success"):
            return self._handle_failure(pipeline, "Planning failed", result)

        artifacts = result.get("artifacts", {})
        self.context.store_artifact(pipeline.id, "", "plan", artifacts)

        task_graph = artifacts.get("task_graph", {})
        tasks = task_graph.get("tasks", [])
        execution_waves = task_graph.get("execution_waves", [])

        pipeline.artifacts["plan"] = artifacts
        pipeline.phase = PipelinePhase.CONFIRM_PLAN
        self._save_pipelines()

        self.checkpoint_mgr.create_full_snapshot(
            pipeline,
            self.scheduler.task_queue.get_statistics(),
            self.scheduler.registry.get_status(),
            label="pre_confirm",
        )

        summary_lines = [
            f"Pipeline: {pipeline.description[:100]}",
            f"Tasks: {len(tasks)}",
            f"Execution waves: {len(execution_waves)}",
            f"Roles: {', '.join(pipeline.roles)}",
            "",
            "Tasks:",
        ]
        for i, t in enumerate(tasks[:15], 1):
            name = t.get("name", t.get("title", f"Task {i}"))
            role = t.get("role_id", t.get("role", "unknown"))
            summary_lines.append(f"  {i}. [{role}] {name}")
        if len(tasks) > 15:
            summary_lines.append(f"  ... and {len(tasks) - 15} more")

        summary_lines.extend(
            [
                "",
                "Choose execution mode:",
                "  [A] Execute - proceed with plan",
                "  [B] Adjust - modify plan",
                "  [C] Cancel - abort pipeline",
            ]
        )

        return {
            "action": "human_decision",
            "phase": pipeline.phase,
            "pipeline_id": pipeline.id,
            "question": "\n".join(summary_lines),
            "options": ["A", "B", "C"],
            "context_summary": "\n".join(summary_lines),
        }

    def _handle_confirm_plan(self, pipeline: PipelineRun, result: Dict) -> Dict:
        decision = result.get("decision", "A").upper()
        pipeline.decision_history.append(
            {
                "phase": "confirm_plan",
                "decision": decision,
                "timestamp": datetime.now().isoformat(),
            }
        )

        if decision == "C":
            pipeline.state = PipelineState.FAILED
            pipeline.phase = PipelinePhase.FAILED
            self._save_pipelines()
            return {
                "action": "completed",
                "phase": "failed",
                "reason": "Cancelled by user",
            }

        if decision == "B":
            pipeline.phase = PipelinePhase.PLAN
            self._save_pipelines()
            return {
                "action": "call_skill",
                "skill": "bmad-evo",
                "action_type": "replan",
                "prompt": "Adjust the previous plan based on user feedback.",
                "pipeline_id": pipeline.id,
                "phase": pipeline.phase,
            }

        plan_artifacts = pipeline.artifacts.get("plan", {})
        task_graph = plan_artifacts.get(
            "task_graph", plan_artifacts.get("task_plan", {})
        )
        tasks = task_graph.get("tasks", [])

        submitted = self._submit_plan_tasks(pipeline, tasks)
        pipeline.tasks = submitted
        pipeline.phase = PipelinePhase.EXECUTE
        self._save_pipelines()

        return {
            "action": "execute_next_task",
            "pipeline_id": pipeline.id,
            "phase": pipeline.phase,
            "task_count": len(submitted),
        }

    def _handle_execute(self, pipeline: PipelineRun, result: Dict) -> Dict:
        """
        Execute phase with AgentLoop per task.

        Modes:
        1. Sequential: one task at a time via _execute_task_with_loop
        2. Subagent dispatch: multiple independent tasks dispatched to opencode Task tool
        3. Result collection: receiving subagent results from opencode agent

        When context.subagent_mode=True or multiple ready tasks exist,
        dispatches them as parallel subagent requests.
        """
        if result.get("action") == "receive_subagent_results":
            return self._receive_subagent_results(pipeline, result)

        task_id = result.get("task_id", "")
        skill_name = result.get("skill", "")
        task_result = result.get("task_result")

        if task_result and task_id:
            if task_result.get("success"):
                artifacts = task_result.get("artifacts", {})
                if artifacts:
                    for k, v in artifacts.items():
                        self.context.store_artifact(pipeline.id, task_id, k, v)

                task = self.scheduler.task_queue.get(task_id)
                if task:
                    self.scheduler.complete_task(task_id, True, task_result)

        task_stats = self.scheduler.task_queue.get_statistics()
        pending = task_stats.get("pending", 0)
        processing = task_stats.get("processing", 0)

        if pending == 0 and processing == 0:
            pipeline.phase = PipelinePhase.CHECK
            self._save_pipelines()
            self._emit_lifecycle(
                "on_pdca_cycle",
                {
                    "pipeline_id": pipeline.id,
                    "pdca_cycle": pipeline.pdca_cycle + 1,
                },
            )
            return {
                "action": "check",
                "pipeline_id": pipeline.id,
                "phase": pipeline.phase,
                "statistics": task_stats,
            }

        ready_tasks = find_parallel_ready_tasks(
            pipeline_tasks=pipeline.tasks,
            task_queue_get_fn=self.scheduler.task_queue.get,
            task_queue_get_stats_fn=self.scheduler.task_queue.get_statistics,
            max_parallel=5,
        )

        if len(ready_tasks) >= 2 and not result.get("sequential_mode"):
            dispatch_result = self._dispatch_parallel_subagents(pipeline, ready_tasks)
            if dispatch_result:
                return dispatch_result

        next_task_data = self._get_next_ready_task(pipeline)
        if next_task_data:
            return self._execute_task_with_loop(pipeline, next_task_data)

        return {
            "action": "wait",
            "pipeline_id": pipeline.id,
            "phase": pipeline.phase,
            "message": "Waiting for blocked tasks or all tasks done",
        }

    def _execute_task_with_loop(self, pipeline: PipelineRun, task_data: Dict) -> Dict:
        """
        Execute a single sub-task with LoopPolicy.

        Policy determines behavior:
        - analyst/architect roles at SUB_TASK level → 1-pass (no loop)
        - developer/tester roles at SUB_TASK level → AgentLoop with escalation
        - system-level phases → handled by core_orchestrator, not here
        """
        task_id = task_data.get("task_id", "")
        skill_name = task_data.get("skill", "")
        role_id = task_data.get("role_id", "")

        task = self.scheduler.task_queue.get(task_id)
        role_type = role_id
        if task:
            role_type = task.role_id

        loop_config = self.loop_policy.get_config(
            level=ExecutionLevel.SUB_TASK,
            role_type=role_type,
            skill_name=skill_name,
        )

        logger.info(
            f"Task {task_id} [{role_type}]: loop_mode={loop_config.mode.value}, "
            f"max_iter={loop_config.max_iterations}"
        )

        skill_adapter = self.skills.get(skill_name)
        if not skill_adapter:
            task_data["action"] = "call_skill"
            return task_data

        context = {
            "pipeline_id": pipeline.id,
            "task_id": task_id,
            "role_id": role_id,
            "project_path": str(Path(self.state_dir).parent),
            "spec_context": self.context.get_context_for_task(pipeline.id, task_id),
            "previous_artifacts_summary": self.context.get_previous_artifacts_summary(
                pipeline.id
            ),
            "model_route": loop_config.model_route.to_dict(),
        }

        self._emit_lifecycle(
            "on_task_start",
            {
                "pipeline_id": pipeline.id,
                "task_id": task_id,
                "skill_name": skill_name,
                "role_id": role_id,
            },
        )

        active_service = task_data.get("active_service")

        if not loop_config.needs_loop:
            result = skill_adapter.execute(task_data.get("prompt", ""), context)
            pending = result.get("pending_model_request")
            if pending:
                session = create_session_from_pending(
                    pending_request=pending,
                    pipeline_id=pipeline.id,
                    task_id=task_id,
                    skill_name=skill_name,
                    action="execute_task",
                    phase=pipeline.phase,
                    context=context,
                    max_rounds=loop_config.max_iterations,
                )
                sid = self.session_manager.save(session)
                return {
                    "action": "model_request",
                    "session_id": sid,
                    "pipeline_id": pipeline.id,
                    "task_id": task_id,
                    "prompt": pending.get("prompt", ""),
                    "model_request_type": pending.get("type", ""),
                    "model_route": loop_config.model_route.to_dict(),
                    "round": 1,
                    "rounds_remaining": session.rounds_remaining,
                }

            if result.get("success") and result.get("artifacts"):
                for k, v in result["artifacts"].items():
                    self.context.store_artifact(pipeline.id, task_id, k, v)
            self.scheduler.complete_task(task_id, result.get("success", True), result)
            self._emit_lifecycle(
                "on_task_complete",
                {
                    "pipeline_id": pipeline.id,
                    "task_id": task_id,
                    "skill_name": skill_name,
                    "success": result.get("success", True),
                    "artifacts": result.get("artifacts", {}),
                },
            )
            return {
                "action": "execute_next_task",
                "pipeline_id": pipeline.id,
                "task_id": task_id,
                "loop_mode": "one_pass",
                "success": result.get("success", True),
            }

        agent_loop = AgentLoop(
            evaluator=self.evaluator,
            max_iterations=loop_config.max_iterations,
            pass_threshold=loop_config.pass_threshold,
        )

        loop_state = agent_loop.start(
            task_description=task_data.get("prompt", ""),
            skill_name=skill_name,
            context=context,
        )

        first_result = skill_adapter.execute(
            loop_state.prompt, {**context, **loop_state.context}
        )

        loop_state = agent_loop.receive_result(loop_state, first_result)

        if loop_state.needs_model:
            session = create_session_from_pending(
                pending_request=first_result.get("pending_model_request", {}),
                pipeline_id=pipeline.id,
                task_id=task_id,
                skill_name=skill_name,
                action="execute_task",
                phase=pipeline.phase,
                context={**context, **loop_state.context},
                loop_state=loop_state.to_dict(),
                max_rounds=loop_config.max_iterations,
            )
            sid = self.session_manager.save(session)
            return {
                "action": "model_request",
                "session_id": sid,
                "pipeline_id": pipeline.id,
                "task_id": task_id,
                "prompt": loop_state.prompt,
                "model_request_type": first_result.get("pending_model_request", {}).get(
                    "type", ""
                ),
                "model_route": loop_config.model_route.to_dict(),
                "round": loop_state.iteration,
                "rounds_remaining": loop_state.max_iterations - loop_state.iteration,
            }

        outcome = loop_state.outcome

        if outcome.passed:
            if outcome.final_result.get("artifacts"):
                for k, v in outcome.final_result["artifacts"].items():
                    self.context.store_artifact(pipeline.id, task_id, k, v)
            self.scheduler.complete_task(task_id, True, outcome.final_result)
            self._emit_lifecycle(
                "on_task_complete",
                {
                    "pipeline_id": pipeline.id,
                    "task_id": task_id,
                    "skill_name": skill_name,
                    "success": True,
                    "iterations": outcome.total_iterations,
                    "artifacts": outcome.final_result.get("artifacts", {}),
                },
            )

            return {
                "action": "execute_next_task",
                "pipeline_id": pipeline.id,
                "task_id": task_id,
                "iterations": outcome.total_iterations,
                "score": outcome.final_evaluation.score
                if outcome.final_evaluation
                else 0.0,
            }

        if outcome.escalated:
            self._emit_lifecycle(
                "on_error",
                {
                    "pipeline_id": pipeline.id,
                    "task_id": task_id,
                    "reason": "agent_loop_escalated",
                    "iterations": outcome.total_iterations,
                },
            )
            escalation = agent_loop.build_escalation_message(outcome)
            escalation["pipeline_id"] = pipeline.id
            escalation["task_id"] = task_id

            self.checkpoint_mgr.create_full_snapshot(
                pipeline,
                self.scheduler.task_queue.get_statistics(),
                self.scheduler.registry.get_status(),
                label=f"escalation_{task_id[:12]}",
            )

            return {
                "action": "human_decision",
                "phase": PipelinePhase.EXECUTE,
                "pipeline_id": pipeline.id,
                "task_id": task_id,
                "question": escalation["question"],
                "options": escalation["options"],
                "escalation_context": escalation.get("escalation_context", {}),
                "loop_outcome": outcome.to_dict(),
            }

        return {
            "action": "execute_next_task",
            "pipeline_id": pipeline.id,
            "task_id": task_id,
            "failed": True,
        }

    def _dispatch_parallel_subagents(
        self, pipeline: PipelineRun, ready_tasks: List[Dict]
    ) -> Optional[Dict]:
        """Execute multiple independent tasks in parallel via ParallelExecutor."""
        logger.info(
            f"Parallel dispatching {len(ready_tasks)} tasks for pipeline {pipeline.id}"
        )

        self.checkpoint_mgr.create_full_snapshot(
            pipeline,
            self.scheduler.task_queue.get_statistics(),
            self.scheduler.registry.get_status(),
            label=f"pre_parallel_{len(ready_tasks)}",
        )

        for task_data in ready_tasks:
            self._emit_lifecycle(
                "on_task_start",
                {
                    "pipeline_id": pipeline.id,
                    "task_id": task_data.get("task_id", ""),
                    "skill_name": task_data.get("skill", ""),
                    "role_id": task_data.get("role_id", ""),
                    "parallel": True,
                },
            )

        batch = self.parallel_executor.execute_batch(
            tasks=ready_tasks,
            skill_execute_fn=lambda td: self._execute_skill_for_parallel(td, pipeline),
            on_complete_fn=lambda tid, res: self._on_parallel_task_complete(
                pipeline, tid, res
            ),
        )

        for pr in batch.results:
            if pr.success and pr.artifacts:
                for k, v in pr.artifacts.items():
                    self.context.store_artifact(pipeline.id, pr.task_id, k, v)
            self.scheduler.complete_task(
                pr.task_id,
                pr.success,
                {
                    "success": pr.success,
                    "artifacts": pr.artifacts,
                },
            )
            self._emit_lifecycle(
                "on_task_complete",
                {
                    "pipeline_id": pipeline.id,
                    "task_id": pr.task_id,
                    "success": pr.success,
                    "artifacts": pr.artifacts,
                    "parallel": True,
                },
            )

        task_stats = self.scheduler.task_queue.get_statistics()
        pending = task_stats.get("pending", 0)
        processing = task_stats.get("processing", 0)

        if pending == 0 and processing == 0:
            pipeline.phase = PipelinePhase.CHECK
            self._save_pipelines()
            self._emit_lifecycle(
                "on_pdca_cycle",
                {
                    "pipeline_id": pipeline.id,
                    "pdca_cycle": pipeline.pdca_cycle + 1,
                },
            )
            return {
                "action": "check",
                "pipeline_id": pipeline.id,
                "phase": pipeline.phase,
                "parallel_result": batch.to_dict(),
            }

        return {
            "action": "execute_next_task",
            "pipeline_id": pipeline.id,
            "parallel_result": batch.to_dict(),
        }

    def _execute_skill_for_parallel(
        self, task_data: Dict[str, Any], pipeline: PipelineRun
    ) -> Dict[str, Any]:
        """Execute a single skill for parallel task (called in thread)."""
        task_id = task_data.get("task_id", "")
        skill_name = task_data.get("skill", "superpowers")
        role_id = task_data.get("role_id", "developer")

        skill_adapter = self.skills.get(skill_name)
        if not skill_adapter:
            return {
                "success": False,
                "error": f"No skill adapter: {skill_name}",
                "artifacts": {},
            }

        task = self.scheduler.task_queue.get(task_id)
        role_type = task.role_id if task else role_id

        loop_config = self.loop_policy.get_config(
            level=ExecutionLevel.SUB_TASK,
            role_type=role_type,
            skill_name=skill_name,
        )

        context = {
            "pipeline_id": pipeline.id,
            "task_id": task_id,
            "role_id": role_id,
            "project_path": str(Path(self.state_dir).parent),
            "spec_context": self.context.get_context_for_task(pipeline.id, task_id),
            "previous_artifacts_summary": self.context.get_previous_artifacts_summary(
                pipeline.id
            ),
            "parallel": True,
            "model_route": loop_config.model_route.to_dict(),
        }

        if not loop_config.needs_loop:
            result = skill_adapter.execute(task_data.get("prompt", ""), context)
            if result.get("pending_model_request"):
                return {
                    "success": True,
                    "artifacts": result.get("artifacts", {}),
                    "pending_model_request": result["pending_model_request"],
                }
            return result

        agent_loop = AgentLoop(
            evaluator=self.evaluator,
            max_iterations=loop_config.max_iterations,
            pass_threshold=loop_config.pass_threshold,
        )
        loop_state = agent_loop.start(
            task_description=task_data.get("prompt", ""),
            skill_name=skill_name,
            context=context,
        )
        first_result = skill_adapter.execute(
            loop_state.prompt, {**context, **loop_state.context}
        )
        loop_state = agent_loop.receive_result(loop_state, first_result)

        if loop_state.needs_model:
            return {
                "success": True,
                "artifacts": first_result.get("artifacts", {}),
                "pending_model_request": first_result.get("pending_model_request"),
            }

        outcome = loop_state.outcome
        if outcome.passed:
            return {
                "success": True,
                "artifacts": outcome.final_result.get("artifacts", {}),
            }

        return {
            "success": False,
            "error": f"AgentLoop escalated after {outcome.total_iterations} iterations",
            "artifacts": outcome.final_result.get("artifacts", {}),
        }

    def _on_parallel_task_complete(
        self, pipeline: PipelineRun, task_id: str, result: Dict[str, Any]
    ):
        pass

    def _receive_subagent_results(self, pipeline: PipelineRun, result: Dict) -> Dict:
        """Process subagent results returned by the opencode agent."""
        results = result.get("results", [])
        summary = self.subagent_dispatcher.receive_results(
            pipeline_id=pipeline.id,
            results=results,
            scheduler=self.scheduler,
            context_mgr=self.context,
            pipeline=pipeline,
        )

        for proc in summary.get("processed", []):
            if proc["success"]:
                logger.info(f"Subagent task {proc['task_id']} succeeded")
            else:
                logger.warning(f"Subagent task {proc['task_id']} failed")

        task_stats = self.scheduler.task_queue.get_statistics()
        pending = task_stats.get("pending", 0)
        processing = task_stats.get("processing", 0)

        if pending == 0 and processing == 0:
            pipeline.phase = PipelinePhase.CHECK
            self._save_pipelines()
            self._emit_lifecycle(
                "on_pdca_cycle",
                {
                    "pipeline_id": pipeline.id,
                    "pdca_cycle": pipeline.pdca_cycle + 1,
                },
            )
            return {
                "action": "check",
                "pipeline_id": pipeline.id,
                "phase": pipeline.phase,
                "statistics": task_stats,
                "subagent_summary": summary,
            }

        return {
            "action": "execute_next_task",
            "pipeline_id": pipeline.id,
            "subagent_summary": summary,
        }

    def _handle_check(self, pipeline: PipelineRun, result: Dict) -> Dict:
        pipeline.pdca_cycle += 1

        self._emit_lifecycle(
            "on_pdca_cycle",
            {
                "pipeline_id": pipeline.id,
                "pdca_cycle": pipeline.pdca_cycle,
            },
        )

        task_stats = self.scheduler.task_queue.get_statistics()
        pipe_tasks = self.scheduler.task_queue.get_by_pipeline(pipeline.id)

        completed = sum(1 for t in pipe_tasks if t.status == "completed")
        failed = sum(1 for t in pipe_tasks if t.status == "failed")
        total = len(pipe_tasks)
        success_rate = (completed / total * 100) if total > 0 else 0

        issues = [
            {"task_id": t.id, "name": t.name, "error": t.result.get("error", "Unknown")}
            for t in pipe_tasks
            if t.status == "failed"
        ]

        root_causes = self._analyze_failure_root_causes(issues)

        self.checkpoint_mgr.create_full_snapshot(
            pipeline,
            task_stats,
            self.scheduler.registry.get_status(),
            label=f"check_cycle_{pipeline.pdca_cycle}",
        )

        pipeline.phase = PipelinePhase.DECIDE
        self._save_pipelines()

        check_msg = [
            f"PDCA Check - Cycle {pipeline.pdca_cycle}",
            f"Total: {total} | Completed: {completed} | Failed: {failed} | Success: {success_rate:.0f}%",
        ]
        if root_causes:
            check_msg.append("")
            check_msg.append("Root Cause Analysis (from debug protocol):")
            for rc in root_causes[:5]:
                check_msg.append(
                    f"  - [{rc['severity']}] {rc['task']}: {rc['root_cause'][:100]}"
                )

        if issues:
            check_msg.append("")
            check_msg.append("Issues:")
            for iss in issues[:5]:
                check_msg.append(f"  - {iss['name']}: {iss['error'][:80]}")

        options = ["A"]
        check_msg.extend(
            [
                "",
                "[A] Continue - proceed to next phase",
            ]
        )
        if issues:
            options.append("B")
            check_msg.append("[B] Fix - retry failed tasks with debug protocol")
        if success_rate >= 80:
            options.append("C")
            check_msg.append("[C] Complete - mark pipeline done")
        options.append("D")
        check_msg.append("[D] Pause - halt pipeline")

        return {
            "action": "human_decision",
            "phase": pipeline.phase,
            "pipeline_id": pipeline.id,
            "question": "\n".join(check_msg),
            "options": options,
            "context_summary": "\n".join(check_msg),
        }

    def _analyze_failure_root_causes(self, issues: List[Dict]) -> List[Dict]:
        """Root cause analysis for failed tasks, inspired by Superpowers debug protocol."""
        root_causes = []
        for iss in issues[:10]:
            error = iss.get("error", "Unknown")
            name = iss.get("name", "unknown")
            severity = "critical"
            root_cause = "Unknown error"

            error_lower = error.lower()
            if "timeout" in error_lower:
                root_cause = "Task exceeded time limit"
                severity = "important"
            elif "import" in error_lower or "module" in error_lower:
                root_cause = "Missing dependency or import error"
                severity = "critical"
            elif "permission" in error_lower or "access" in error_lower:
                root_cause = "File access or permission issue"
                severity = "critical"
            elif "syntax" in error_lower or "parse" in error_lower:
                root_cause = "Syntax or parsing error in generated code"
                severity = "critical"
            elif "assert" in error_lower or "test" in error_lower:
                root_cause = "Test assertion failure"
                severity = "important"
            elif "not found" in error_lower:
                root_cause = "Resource or file not found"
                severity = "important"
            elif len(error) > 50:
                root_cause = f"Complex error: {error[:80]}"
                severity = "important"
            else:
                root_cause = error

            root_causes.append(
                {
                    "task": name,
                    "root_cause": root_cause,
                    "severity": severity,
                }
            )

        return root_causes

    def _handle_decide(self, pipeline: PipelineRun, result: Dict) -> Dict:
        decision = result.get("decision", "A").upper()
        pipeline.decision_history.append(
            {
                "phase": "pdca_decide",
                "cycle": pipeline.pdca_cycle,
                "decision": decision,
                "timestamp": datetime.now().isoformat(),
            }
        )

        if decision == "A":
            pipeline.phase = PipelinePhase.EVOLVE
            self._save_pipelines()
            return {
                "action": "call_skill",
                "skill": "spec-kit",
                "action_type": "evolve",
                "prompt": "Evolve specs based on completed work.",
                "pipeline_id": pipeline.id,
                "phase": pipeline.phase,
            }
        elif decision == "B":
            pipe_tasks = self.scheduler.task_queue.get_by_pipeline(pipeline.id)
            for t in pipe_tasks:
                if t.status == "failed":
                    self.scheduler.task_queue.increment_retry(t.id)
            pipeline.phase = PipelinePhase.EXECUTE
            self._save_pipelines()
            return {
                "action": "execute_next_task",
                "pipeline_id": pipeline.id,
                "phase": pipeline.phase,
            }
        elif decision == "C":
            pipeline.state = PipelineState.COMPLETED
            pipeline.phase = PipelinePhase.COMPLETED
            pipeline.completed_at = datetime.now()
            self._save_pipelines()
            self.context.save_state()
            return {
                "action": "completed",
                "phase": "completed",
                "pipeline_id": pipeline.id,
            }
        elif decision == "D":
            pipeline.state = PipelineState.PAUSED
            pipeline.phase = PipelinePhase.PAUSED
            self._save_pipelines()
            self.context.save_state()
            return {
                "action": "paused",
                "phase": "paused",
                "pipeline_id": pipeline.id,
            }
        return {"error": f"Unknown decision: {decision}"}

    def _handle_evolve(self, pipeline: PipelineRun, result: Dict) -> Dict:
        pipeline.phase = PipelinePhase.VERIFY
        self._save_pipelines()

        artifacts = self.context.get_artifacts(pipeline.id)
        try:
            prompt = self.prompt_manager.render(
                "pipeline/evolve",
                artifacts_summary=", ".join(list(artifacts.keys())[:20]),
                pdca_cycle=str(pipeline.pdca_cycle),
            )
        except Exception:
            prompt = f"Evolve specs based on completed work."

        return {
            "action": "call_skill",
            "skill": "spec-kit",
            "action_type": "evolve",
            "prompt": prompt,
            "pipeline_id": pipeline.id,
            "phase": pipeline.phase,
        }

    def _handle_verify(self, pipeline: PipelineRun, result: Dict) -> Dict:
        if result.get("success"):
            pipeline.state = PipelineState.COMPLETED
            pipeline.phase = PipelinePhase.COMPLETED
            pipeline.completed_at = datetime.now()
            self._emit_lifecycle(
                "on_pipeline_complete",
                {
                    "pipeline_id": pipeline.id,
                    "pdca_cycle": pipeline.pdca_cycle,
                    "description": pipeline.description,
                },
            )
        else:
            if pipeline.pdca_cycle < 3:
                pipeline.phase = PipelinePhase.EXECUTE
            else:
                pipeline.state = PipelineState.FAILED
                pipeline.phase = PipelinePhase.FAILED
        self._save_pipelines()
        self.context.save_state()
        return {
            "action": "completed"
            if pipeline.phase == PipelinePhase.COMPLETED
            else "execute_next_task",
            "phase": pipeline.phase,
            "pipeline_id": pipeline.id,
        }

    _phase_handlers = {
        PipelinePhase.INIT: _handle_init,
        PipelinePhase.ANALYZE: _handle_analyze,
        PipelinePhase.PLAN: _handle_plan,
        PipelinePhase.CONFIRM_PLAN: _handle_confirm_plan,
        PipelinePhase.EXECUTE: _handle_execute,
        PipelinePhase.CHECK: _handle_check,
        PipelinePhase.DECIDE: _handle_decide,
        PipelinePhase.EVOLVE: _handle_evolve,
        PipelinePhase.VERIFY: _handle_verify,
    }

    # ===== Multi-Round Prompt Passing =====

    def resume_model_request(
        self, session_id: str, model_response: str
    ) -> Dict[str, Any]:
        """
        Resume a paused execution after model response is provided.

        This is the key method for multi-round prompt-passing:
        1. Load the saved session state
        2. Feed the model response back to the skill
        3. If the skill returns another pending_model_request, save new session
        4. If the skill completes, process the result and advance pipeline

        Args:
            session_id: The session to resume
            model_response: The model's response text

        Returns:
            Dict with action, may contain new session_id or pipeline advancement
        """
        session = self.session_manager.load(session_id)
        if not session:
            return {"error": f"Session {session_id} not found or expired"}

        pipeline = self.pipelines.get(session.pipeline_id)
        if not pipeline:
            self.session_manager.remove(session_id)
            return {"error": f"Pipeline {session.pipeline_id} not found"}

        if session.round_number >= session.max_rounds:
            self.session_manager.remove(session_id)
            return {
                "action": "max_rounds_exceeded",
                "session_id": session_id,
                "pipeline_id": session.pipeline_id,
                "round_number": session.round_number,
            }

        skill_adapter = self.skills.get(session.skill_name)
        if not skill_adapter:
            self.session_manager.remove(session_id)
            return {"error": f"Skill {session.skill_name} not available"}

        continue_context = dict(session.context)
        continue_context["model_response"] = model_response
        continue_context["round_number"] = session.round_number + 1

        if hasattr(skill_adapter, "continue_execution"):
            result = skill_adapter.continue_execution(model_response, continue_context)
        else:
            result = {
                "success": True,
                "artifacts": {"response": model_response},
                "output": model_response,
            }

        pending = result.get("pending_model_request")
        if pending:
            new_session = create_session_from_pending(
                pending_request=pending,
                pipeline_id=session.pipeline_id,
                task_id=session.task_id,
                skill_name=session.skill_name,
                action=session.action,
                phase=session.phase,
                context=continue_context,
                loop_state={"parent_session_id": session_id},
                max_rounds=session.max_rounds,
            )
            new_session.round_number = session.round_number + 1
            new_sid = self.session_manager.save(new_session)
            self.session_manager.remove(session_id)

            return {
                "action": "model_request",
                "session_id": new_sid,
                "pipeline_id": session.pipeline_id,
                "task_id": session.task_id,
                "prompt": pending.get("prompt", ""),
                "model_request_type": pending.get("type", ""),
                "model_route": session.context.get("model_route"),
                "round": new_session.round_number,
                "rounds_remaining": new_session.rounds_remaining,
            }

        self.session_manager.remove(session_id)

        phase_result = {
            "task_id": session.task_id,
            "skill": session.skill_name,
            "task_result": result,
        }

        if session.action == "analyze" or session.action == "plan":
            phase_result["success"] = result.get("success", True)
            phase_result["artifacts"] = result.get("artifacts", {})

        if session.action == "execute_task":
            if result.get("success"):
                artifacts = result.get("artifacts", {})
                if artifacts:
                    for k, v in artifacts.items():
                        self.context.store_artifact(
                            session.pipeline_id, session.task_id, k, v
                        )
                self.scheduler.complete_task(session.task_id, True, result)

        return self.advance(session.pipeline_id, phase_result)

    def get_active_session(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """Get the active model request session for a pipeline, if any."""
        session = self.session_manager.load_by_pipeline(pipeline_id)
        if not session:
            return None
        return {
            "session_id": session.session_id,
            "pipeline_id": session.pipeline_id,
            "task_id": session.task_id,
            "skill_name": session.skill_name,
            "round": session.round_number,
            "rounds_remaining": session.rounds_remaining,
            "model_request_type": session.model_request_type,
        }

    # ===== Task Execution =====

    def _submit_plan_tasks(self, pipeline: PipelineRun, tasks: List[Dict]) -> List[str]:
        task_ids = []
        for tdef in tasks:
            role_id = tdef.get("role_id", tdef.get("role", "developer"))
            task_data = {
                "pipeline_id": pipeline.id,
                "role_id": role_id,
                "name": tdef.get("name", tdef.get("title", "Unnamed task")),
                "description": tdef.get("description", ""),
                "priority": tdef.get("priority", "P2"),
                "depends_on": tdef.get("depends_on", []),
                "max_steps": tdef.get("max_steps", 50),
            }
            result = self.scheduler.submit_task(task_data)
            if result["success"]:
                task_ids.append(result["task_id"])
        return task_ids

    def _get_next_ready_task(self, pipeline: PipelineRun) -> Optional[Dict]:
        for task_id in pipeline.tasks:
            task = self.scheduler.task_queue.get(task_id)
            if task and task.status == "pending":
                deps_ok = all(
                    self.scheduler.task_queue.get(d)
                    and self.scheduler.task_queue.get(d).status == "completed"
                    for d in task.depends_on
                )
                if deps_ok:
                    role = self.scheduler.registry.get(task.role_id)
                    if not role:
                        continue
                    context_str = self.context.get_context_for_task(
                        pipeline.id, task_id
                    )
                    prev_artifacts = self.context.get_previous_artifacts_summary(
                        pipeline.id
                    )

                    skill = self._role_to_skill(role.type)

                    try:
                        prompt = self.prompt_manager.compose(
                            "pipeline/execute_task",
                            sections=[
                                "spec_constraints",
                                "previous_artifacts",
                                "quality_gates",
                            ],
                            task_name=task.name,
                            task_description=task.description,
                            role_name=role.name,
                            role_type=role.type,
                            capabilities=", ".join(role.capabilities),
                            spec_context=context_str or "",
                            previous_artifacts_summary=prev_artifacts or "",
                        )
                    except Exception:
                        prompt = f"Execute task: {task.name}\n"
                        prompt += f"Description: {task.description}\n"
                        if context_str:
                            prompt += f"\n{context_str}\n"
                        if prev_artifacts:
                            prompt += f"\n{prev_artifacts}\n"
                        prompt += f"\nRole: {role.name} ({role.type})\n"
                        prompt += f"Capabilities: {', '.join(role.capabilities)}\n"

                    return {
                        "action": "call_skill",
                        "skill": skill,
                        "action_type": "execute_task",
                        "prompt": prompt,
                        "pipeline_id": pipeline.id,
                        "task_id": task_id,
                        "role_id": task.role_id,
                        "phase": pipeline.phase,
                    }
        return None

    def _role_to_skill(self, role_type: str) -> str:
        mapping = {
            "analyst": "bmad-evo",
            "architect": "bmad-evo",
            "developer": "superpowers",
            "coder": "superpowers",
            "implementer": "superpowers",
            "tester": "superpowers",
            "spec-writer": "spec-kit",
        }
        return mapping.get(role_type, "superpowers")

    # ===== Utilities =====

    def _emit_lifecycle(self, point: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if self.spec_gate and hasattr(self.spec_gate, "emit_lifecycle"):
            return self.spec_gate.emit_lifecycle(point, context)
        return context

    def _is_timed_out(self, pipeline: PipelineRun) -> bool:
        if not pipeline.started_at:
            return False
        max_seconds = pipeline.max_duration_hours * 3600
        elapsed = (datetime.now() - pipeline.started_at).total_seconds()
        return elapsed > max_seconds

    def _handle_timeout(self, pipeline: PipelineRun) -> Dict:
        pipeline.state = PipelineState.PAUSED
        pipeline.phase = PipelinePhase.PAUSED
        self._save_pipelines()
        self.checkpoint_mgr.create_full_snapshot(
            pipeline,
            self.scheduler.task_queue.get_statistics(),
            self.scheduler.registry.get_status(),
            label="timeout",
        )
        return {
            "action": "human_decision",
            "phase": "paused",
            "pipeline_id": pipeline.id,
            "question": f"Pipeline timed out after {pipeline.max_duration_hours}h. Resume?",
            "options": ["A", "B"],
            "option_labels": {
                "A": "Resume - extend timeout and continue",
                "B": "Stop - mark as failed",
            },
        }

    def _handle_failure(self, pipeline: PipelineRun, reason: str, result: Dict) -> Dict:
        logger.error(f"Pipeline {pipeline.id} failure: {reason}")
        self._emit_lifecycle(
            "on_error",
            {
                "pipeline_id": pipeline.id,
                "reason": reason,
                "phase": str(pipeline.phase),
            },
        )
        pipeline.state = PipelineState.FAILED
        pipeline.phase = PipelinePhase.FAILED
        self._save_pipelines()
        return {
            "action": "failed",
            "phase": "failed",
            "pipeline_id": pipeline.id,
            "reason": reason,
            "detail": result,
        }

    # ===== Resume / Status =====

    def resume_pipeline(self, pipeline_id: str) -> Dict:
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline:
            return {"error": f"Pipeline {pipeline_id} not found"}
        if pipeline.state != PipelineState.PAUSED:
            return {"error": f"Pipeline is {pipeline.state}, not paused"}

        latest = self.checkpoint_mgr.restore_latest(pipeline_id)
        if latest and latest.get("snapshot", {}).get("pipeline"):
            restored = PipelineRun.from_dict(latest["snapshot"]["pipeline"])
            pipeline.phase = restored.phase
            pipeline.started_at = restored.started_at

        pipeline.state = PipelineState.RUNNING
        if pipeline.phase == PipelinePhase.PAUSED:
            pipeline.phase = PipelinePhase.EXECUTE
        self._save_pipelines()

        return {
            "action": "execute_next_task",
            "pipeline_id": pipeline.id,
            "phase": pipeline.phase,
        }

    def get_pipeline_status(self, pipeline_id: str) -> Optional[Dict]:
        pipeline = self.pipelines.get(pipeline_id)
        if not pipeline:
            return None

        task_stats = self.scheduler.task_queue.get_statistics()
        pipe_tasks = self.scheduler.task_queue.get_by_pipeline(pipeline_id)
        completed = sum(1 for t in pipe_tasks if t.status == "completed")
        total = len(pipe_tasks)
        progress = (completed / total * 100) if total > 0 else 0

        elapsed = 0.0
        if pipeline.started_at:
            elapsed = (datetime.now() - pipeline.started_at).total_seconds() / 60

        return {
            "id": pipeline.id,
            "description": pipeline.description[:100],
            "state": pipeline.state,
            "phase": pipeline.phase,
            "progress": f"{progress:.0f}%",
            "tasks": f"{completed}/{total}",
            "pdca_cycle": pipeline.pdca_cycle,
            "elapsed_minutes": round(elapsed, 1),
            "roles": pipeline.roles,
            "task_statistics": task_stats,
        }

    def list_pipelines(self) -> List[Dict]:
        return [
            {
                "id": p.id,
                "description": p.description[:60],
                "state": p.state,
                "phase": p.phase,
                "created": p.created_at.isoformat() if p.created_at else None,
            }
            for p in sorted(
                self.pipelines.values(),
                key=lambda x: x.created_at or datetime.min,
                reverse=True,
            )
        ]

    def register_skill(self, name: str, adapter: Any):
        self.skills[name] = adapter

    def cleanup(self):
        self.scheduler.cleanup()
        for pid in list(self.pipelines.keys()):
            self.checkpoint_mgr.cleanup_old(pid, keep=5)
        self.context.save_state()
