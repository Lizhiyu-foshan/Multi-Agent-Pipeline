"""
Agent Loop - Execute-Evaluate-Refine cycle with human escalation.

The fundamental execution pattern for multi-agent pipelines:

    for iteration in 1..MAX_ITERATIONS:
        result = execute(task, context + refinement_feedback)
        evaluation = evaluate(result, spec_requirements)
        if evaluation.passed:
            return success(result)
        refinement_feedback = build_refinement(evaluation)

    return escalate_to_human(task, evaluation_history)

Key properties:
- Self-correcting: each iteration includes feedback from the previous attempt
- Spec-gated: evaluation checks against constraints, not just "did it run"
- Bounded: max 5 iterations per task before human escalation
- Transparent: full history of attempts and evaluations available
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .execution_evaluator import EvaluationResult, ExecutionEvaluator

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 5
PASS_THRESHOLD = 0.6


@dataclass
class LoopIteration:
    iteration: int
    execution_result: Dict[str, Any] = field(default_factory=dict)
    evaluation: Optional[EvaluationResult] = None
    refinement_applied: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "success": self.execution_result.get("success", False),
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
            "refinement_applied": self.refinement_applied[:200],
        }


@dataclass
class LoopOutcome:
    task_id: str = ""
    skill_name: str = ""
    passed: bool = False
    total_iterations: int = 0
    iterations: List[LoopIteration] = field(default_factory=list)
    final_result: Dict[str, Any] = field(default_factory=dict)
    final_evaluation: Optional[EvaluationResult] = None
    escalated: bool = False
    escalation_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "skill_name": self.skill_name,
            "passed": self.passed,
            "total_iterations": self.total_iterations,
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
            "final_score": self.final_evaluation.score
            if self.final_evaluation
            else 0.0,
            "iterations": [it.to_dict() for it in self.iterations],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoopOutcome":
        iterations_data = data.get("iterations", [])
        iterations = []
        for it in iterations_data:
            if isinstance(it, dict):
                iterations.append(
                    LoopIteration(
                        iteration=it.get("iteration", 0),
                        execution_result={"success": it.get("success", False)},
                        refinement_applied=it.get("refinement_applied", ""),
                    )
                )
        return cls(
            task_id=data.get("task_id", ""),
            skill_name=data.get("skill_name", ""),
            passed=data.get("passed", False),
            total_iterations=data.get("total_iterations", 0),
            iterations=iterations,
            final_result=data.get("final_result", {}),
            escalated=data.get("escalated", False),
            escalation_reason=data.get("escalation_reason", ""),
        )


class AgentLoop:
    """
    Agent execution loop with self-evaluation and iterative refinement.

    Usage (synchronous, single skill):
        loop = AgentLoop(evaluator=evaluator)
        outcome = loop.run(
            task_description="Build user auth",
            skill_name="superpowers",
            skill_execute_fn=my_skill.execute,
            context={"project_path": "..."},
        )
        if outcome.escalated:
            # handle human intervention

    Usage (prompt-passing, multi-round):
        loop = AgentLoop(evaluator=evaluator)
        state = loop.start(task_description, skill_name, context)
        while not state.done:
            result = skill.execute(state.prompt, state.context)
            state = loop.receive_result(state, result)
            if state.needs_human:
                break
        # state.outcome has the final result
    """

    def __init__(
        self,
        evaluator: ExecutionEvaluator = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        pass_threshold: float = PASS_THRESHOLD,
    ):
        self.evaluator = evaluator or ExecutionEvaluator()
        self.max_iterations = max_iterations
        self.pass_threshold = pass_threshold

    def run(
        self,
        task_description: str,
        skill_name: str,
        skill_execute_fn: Callable,
        context: Dict[str, Any] = None,
        active_service: str = None,
        on_iteration_complete: Callable = None,
    ) -> LoopOutcome:
        """
        Run the full agent loop synchronously.

        Args:
            task_description: What the skill should produce
            skill_name: Which skill is executing
            skill_execute_fn: Callable(task_description, context) -> Dict
            context: Initial context for the skill
            active_service: Current service for spec checks
            on_iteration_complete: Optional callback(loop_state, iteration)

        Returns:
            LoopOutcome with full history and final result
        """
        ctx = dict(context or {})
        outcome = LoopOutcome(
            task_id=ctx.get("task_id", ""),
            skill_name=skill_name,
        )

        refinement_prompt = ""

        for i in range(1, self.max_iterations + 1):
            iteration = LoopIteration(iteration=i)

            if refinement_prompt:
                ctx["refinement_feedback"] = refinement_prompt
                ctx["iteration"] = i
                ctx["previous_attempts"] = [it.to_dict() for it in outcome.iterations]

            logger.info(f"[AgentLoop] {skill_name} iteration {i}/{self.max_iterations}")

            try:
                result = skill_execute_fn(task_description, ctx)
            except Exception as e:
                logger.error(f"[AgentLoop] Execution exception: {e}")
                result = {"success": False, "error": str(e)}

            iteration.execution_result = result

            evaluation = self.evaluator.evaluate(
                task_description=task_description,
                skill_name=skill_name,
                execution_result=result,
                iteration=i,
                active_service=active_service,
            )
            iteration.evaluation = evaluation

            logger.info(
                f"[AgentLoop] Iteration {i}: score={evaluation.score:.2f} "
                f"passed={evaluation.passed} issues={len(evaluation.issues)}"
            )

            outcome.iterations.append(iteration)

            if on_iteration_complete:
                try:
                    on_iteration_complete(outcome, iteration)
                except Exception:
                    pass

            if evaluation.passed:
                outcome.passed = True
                outcome.total_iterations = i
                outcome.final_result = result
                outcome.final_evaluation = evaluation
                logger.info(
                    f"[AgentLoop] PASSED at iteration {i} "
                    f"(score={evaluation.score:.2f})"
                )
                return outcome

            refinement_prompt = evaluation.build_refinement_prompt()
            iteration.refinement_applied = refinement_prompt[:200]

        outcome.total_iterations = self.max_iterations
        outcome.final_result = (
            outcome.iterations[-1].execution_result if outcome.iterations else {}
        )
        outcome.final_evaluation = (
            outcome.iterations[-1].evaluation if outcome.iterations else None
        )
        outcome.escalated = True
        outcome.escalation_reason = self._build_escalation_reason(outcome)

        logger.warning(
            f"[AgentLoop] ESCALATED after {self.max_iterations} iterations "
            f"(best score: {self._best_score(outcome):.2f})"
        )
        return outcome

    def build_escalation_message(self, outcome: LoopOutcome) -> Dict[str, Any]:
        """
        Build a human-readable escalation message with options.

        Returns a dict suitable for 'human_decision' action:
        {
            "action": "human_decision",
            "question": "...",
            "options": [...],
            "context": {...}
        }
        """
        best = self._best_score(outcome)
        best_iter = self._best_iteration(outcome)

        lines = [
            f"[AGENT LOOP ESCALATION] {outcome.skill_name}",
            f"",
            f"Task: {outcome.task_id}",
            f"Iterations: {outcome.total_iterations}/{self.max_iterations}",
            f"Best score: {best:.2f} (threshold: {self.pass_threshold})",
            f"",
        ]

        if best_iter and best_iter.evaluation:
            ev = best_iter.evaluation
            if ev.strengths:
                lines.append("What worked:")
                for s in ev.strengths[:3]:
                    lines.append(f"  + {s}")
                lines.append("")

            if ev.issues:
                lines.append("Persistent issues:")
                for iss in ev.issues[:5]:
                    lines.append(f"  - {iss}")
                lines.append("")

            if ev.suggestions:
                lines.append("Suggested fixes:")
                for sug in ev.suggestions[:3]:
                    lines.append(f"  > {sug}")
                lines.append("")

        score_trend = self._score_trend(outcome)
        if score_trend == "declining":
            lines.append("Score trend: DECLINING - task decomposition may be wrong")
        elif score_trend == "stagnant":
            lines.append("Score trend: STAGNANT - constraints may be too strict")
        else:
            lines.append("Score trend: IMPROVING - more iterations might help")

        lines.extend(
            [
                "",
                "Choose action:",
                "  [A] Retry with adjusted task description",
                "  [B] Relax constraints and retry",
                "  [C] Split task into smaller sub-tasks",
                "  [D] Skip this task and continue",
                "  [E] Abort pipeline",
            ]
        )

        return {
            "action": "human_decision",
            "question": "\n".join(lines),
            "options": ["A", "B", "C", "D", "E"],
            "loop_outcome": outcome.to_dict(),
            "escalation_context": {
                "skill_name": outcome.skill_name,
                "task_id": outcome.task_id,
                "total_iterations": outcome.total_iterations,
                "best_score": best,
                "score_trend": score_trend,
            },
        }

    def _best_score(self, outcome: LoopOutcome) -> float:
        scores = [it.evaluation.score for it in outcome.iterations if it.evaluation]
        return max(scores) if scores else 0.0

    def _best_iteration(self, outcome: LoopOutcome) -> Optional[LoopIteration]:
        best_score = 0.0
        best_it = None
        for it in outcome.iterations:
            if it.evaluation and it.evaluation.score > best_score:
                best_score = it.evaluation.score
                best_it = it
        return best_it

    def _score_trend(self, outcome: LoopOutcome) -> str:
        scores = [it.evaluation.score for it in outcome.iterations if it.evaluation]
        if len(scores) < 2:
            return "unknown"

        first_half = scores[: len(scores) // 2]
        second_half = scores[len(scores) // 2 :]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        diff = avg_second - avg_first
        if diff > 0.1:
            return "improving"
        elif diff < -0.1:
            return "declining"
        return "stagnant"

    def _build_escalation_reason(self, outcome: LoopOutcome) -> str:
        trend = self._score_trend(outcome)
        best = self._best_score(outcome)
        reasons = []
        if trend == "declining":
            reasons.append(
                "Score declining across iterations - task may need re-decomposition"
            )
        elif trend == "stagnant":
            reasons.append(
                "Score stagnant - constraints may be too strict or task ambiguous"
            )
        reasons.append(f"Best score {best:.2f} below threshold {self.pass_threshold}")
        return "; ".join(reasons)

    def start(
        self,
        task_description: str,
        skill_name: str,
        context: Dict[str, Any] = None,
    ) -> "LoopState":
        """
        Start a prompt-passing agent loop.

        Returns a LoopState that tracks progress across multiple
        model interaction rounds. Use with receive_result() for
        async/prompt-passing execution.

        Usage:
            state = loop.start(task_desc, skill_name, context)
            while not state.done:
                result = skill.execute(state.prompt, state.context)
                state = loop.receive_result(state, result)
                if state.needs_model:
                    # return state.prompt to caller for model inference
                    break
        """
        ctx = dict(context or {})
        state = LoopState(
            task_description=task_description,
            skill_name=skill_name,
            context=ctx,
            iteration=0,
            max_iterations=self.max_iterations,
            done=False,
            needs_model=False,
            prompt=task_description,
            outcome=LoopOutcome(
                task_id=ctx.get("task_id", ""),
                skill_name=skill_name,
            ),
            _pass_threshold=self.pass_threshold,
        )
        return state

    def receive_result(
        self,
        state: "LoopState",
        execution_result: Dict[str, Any],
    ) -> "LoopState":
        """
        Process an execution result and advance the loop state.

        If the result contains pending_model_request, sets needs_model=True
        so the caller can forward the prompt to the model. Otherwise,
        evaluates and either completes, refines, or escalates.
        """
        state = LoopState(
            task_description=state.task_description,
            skill_name=state.skill_name,
            context=dict(state.context),
            iteration=state.iteration + 1,
            max_iterations=state.max_iterations,
            done=False,
            needs_model=False,
            prompt="",
            outcome=state.outcome,
        )

        iteration = LoopIteration(iteration=state.iteration)
        iteration.execution_result = execution_result

        pending = execution_result.get("pending_model_request")
        if pending:
            state.needs_model = True
            state.prompt = pending.get("prompt", "")
            state.context["pending_model_request"] = pending
            state.context["model_request_type"] = pending.get("type", "")
            iteration.refinement_applied = (
                f"Model request: {pending.get('type', 'unknown')}"
            )
            state.outcome.iterations.append(iteration)
            return state

        evaluation = self.evaluator.evaluate(
            task_description=state.task_description,
            skill_name=state.skill_name,
            execution_result=execution_result,
            iteration=state.iteration,
            active_service=state.context.get("active_service"),
        )
        iteration.evaluation = evaluation
        state.outcome.iterations.append(iteration)

        if evaluation.passed:
            state.done = True
            state.outcome.passed = True
            state.outcome.total_iterations = state.iteration
            state.outcome.final_result = execution_result
            state.outcome.final_evaluation = evaluation
            return state

        if state.iteration >= self.max_iterations:
            state.done = True
            state.outcome.total_iterations = state.iteration
            state.outcome.final_result = execution_result
            state.outcome.final_evaluation = evaluation
            state.outcome.escalated = True
            state.outcome.escalation_reason = self._build_escalation_reason(
                state.outcome
            )
            return state

        refinement = evaluation.build_refinement_prompt()
        iteration.refinement_applied = refinement[:200]
        state.context["refinement_feedback"] = refinement
        state.context["iteration"] = state.iteration
        state.context["previous_attempts"] = [
            it.to_dict() for it in state.outcome.iterations
        ]
        state.prompt = state.task_description

        return state

    def resume_with_model(
        self,
        state: "LoopState",
        model_response: str,
    ) -> "LoopState":
        """
        Resume a loop that was paused for model interaction.

        Takes the paused state and the model's response, creates a
        synthetic execution result from the response, and feeds it
        back through receive_result for evaluation.

        Args:
            state: LoopState that has needs_model=True
            model_response: The model's response text

        Returns:
            Updated LoopState — may still need_model, be done, or
            need another skill execution.
        """
        pending = state.context.get("pending_model_request", {})
        synthetic_result = {
            "success": True,
            "output": model_response,
            "artifacts": pending.get("artifacts", {}),
        }

        state.needs_model = False
        state.context["model_response"] = model_response
        state.context.pop("pending_model_request", None)

        return self.receive_result(state, synthetic_result)


@dataclass
class LoopState:
    """Tracks multi-round AgentLoop state for prompt-passing execution."""

    task_description: str = ""
    skill_name: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    max_iterations: int = 5
    done: bool = False
    needs_model: bool = False
    prompt: str = ""
    outcome: LoopOutcome = field(default_factory=LoopOutcome)
    _pass_threshold: float = 0.6

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_description": self.task_description[:500],
            "skill_name": self.skill_name,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "done": self.done,
            "needs_model": self.needs_model,
            "prompt": self.prompt[:1000],
            "context": {
                k: v
                for k, v in self.context.items()
                if k
                not in (
                    "spec_context",
                    "previous_artifacts_summary",
                    "pending_model_request",
                )
            },
            "outcome": self.outcome.to_dict(),
            "pass_threshold": self._pass_threshold,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoopState":
        outcome_data = data.get("outcome", {})
        outcome = LoopOutcome.from_dict(outcome_data) if outcome_data else LoopOutcome()

        return cls(
            task_description=data.get("task_description", ""),
            skill_name=data.get("skill_name", ""),
            context=data.get("context", {}),
            iteration=data.get("iteration", 0),
            max_iterations=data.get("max_iterations", 5),
            done=data.get("done", False),
            needs_model=data.get("needs_model", False),
            prompt=data.get("prompt", ""),
            outcome=outcome,
            _pass_threshold=data.get("pass_threshold", 0.6),
        )
