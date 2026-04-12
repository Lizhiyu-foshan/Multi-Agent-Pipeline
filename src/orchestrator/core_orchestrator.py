"""
Core Orchestrator - Agent-loop driven skill execution (SYSTEM level)

This orchestrator handles the simple/complex execution path where
skills run sequentially. It operates at SYSTEM level:
- ALL skills go through AgentLoop (multi-round + human confirm)
- Even analysis/design needs iterative refinement at system level
- Human confirmation required when evaluation passes

For sub-task level execution (per-task with differentiated policies),
see PipelineOrchestrator which uses LoopPolicy to distinguish.
"""

import json
import yaml
import time
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

try:
    from .complexity_evaluator import ComplexityEvaluator
    from .skill_loader import SkillLoader
    from .path_selector import PathSelector
    from .report_generator import ReportGenerator
    from ..adapters.platform_adapter import detect_platform
    from ..specs.spec_gate import SpecGate
    from ..pipeline.execution_evaluator import ExecutionEvaluator
    from ..pipeline.agent_loop import AgentLoop, LoopOutcome
    from ..pipeline.loop_policy import LoopPolicy, LoopConfig, LoopMode, ExecutionLevel
except ImportError:
    from src.orchestrator.complexity_evaluator import ComplexityEvaluator
    from src.orchestrator.skill_loader import SkillLoader
    from src.orchestrator.path_selector import PathSelector
    from src.orchestrator.report_generator import ReportGenerator
    from src.adapters.platform_adapter import detect_platform
    from src.specs.spec_gate import SpecGate
    from src.pipeline.execution_evaluator import ExecutionEvaluator
    from src.pipeline.agent_loop import AgentLoop, LoopOutcome
    from src.pipeline.loop_policy import (
        LoopPolicy,
        LoopConfig,
        LoopMode,
        ExecutionLevel,
    )

logger = logging.getLogger(__name__)


class CoreOrchestrator:
    """
    主编排器

    职责：
    1. 评估任务复杂度
    2. 选择执行路径
    3. 动态加载 Skill
    4. 管理执行流程
    5. 生成最终报告
    """

    def __init__(self, config_path: str = None, project_path: str = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        self._start_time = None

        self.config = self._load_config(config_path)

        self.complexity_evaluator = ComplexityEvaluator()
        self.skill_loader = SkillLoader(self.project_path)
        self.path_selector = PathSelector(self.config)
        self.report_generator = ReportGenerator()
        self.spec_gate = SpecGate(self.project_path)

        self.evaluator = ExecutionEvaluator(spec_gate=self.spec_gate)
        self.loop_policy = LoopPolicy()

        self._escalations: List[Dict[str, Any]] = []

        self.platform = detect_platform()
        logger.info(f"CoreOrchestrator initialized, platform: {self.platform}")

    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        """加载配置"""
        if config_path and Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        else:
            return {
                "skills": {
                    "orchestrator": {"enabled": True, "path": ".skills/orchestrator"},
                    "bmad-evo": {"enabled": True, "path": ".skills/bmad-evo"},
                    "spec-kit": {"enabled": True, "path": ".skills/spec-kit"},
                    "superpowers": {"enabled": True, "path": ".skills/superpowers"},
                    "multi-agent-pipeline": {
                        "enabled": True,
                        "path": ".skills/multi-agent-pipeline",
                    },
                },
                "routing_rules": {
                    "simple": ["spec-kit", "superpowers"],
                    "complex": [
                        "bmad-evo",
                        "multi-agent-pipeline",
                        "spec-kit",
                        "superpowers",
                    ],
                    "auto": "dynamic",
                },
                "execution": {
                    "max_parallel_tasks": 5,
                    "timeout": 600,
                    "max_retries": 3,
                },
            }

    def execute(
        self,
        task_description: str,
        path_type: str = "auto",
        max_duration_hours: float = 8.0,
    ) -> Dict[str, Any]:
        """
        执行完整工作流

        Args:
            task_description: 任务描述
            path_type: 执行路径类型
            max_duration_hours: 最大执行时间（小时）

        Returns:
            Dict: 执行结果
        """
        self._start_time = time.time()
        max_seconds = max_duration_hours * 3600

        logger.info(f"Starting execution: {task_description[:50]}...")

        try:
            if path_type == "auto":
                evaluation = self.complexity_evaluator.evaluate(task_description)
                path_type = evaluation.recommended_path
                logger.info(
                    f"Auto-selected path: {path_type} (complexity: {evaluation.overall_score}/10)"
                )

            skills_to_use = self.path_selector.select_path(path_type)
            logger.info(f"Skills to use: {skills_to_use}")

            loaded_skills = self.skill_loader.load_skills(skills_to_use)
            logger.info(f"Loaded {len(loaded_skills)} skills")

            execution_results = self._execute_with_skills(
                task_description, loaded_skills, max_seconds
            )

            report = self.report_generator.generate_report(
                task_description,
                path_type,
                execution_results,
                time.time() - self._start_time,
            )

            return {
                "success": True,
                "path_type": path_type,
                "skills_used": skills_to_use,
                "execution_results": execution_results,
                "report": report,
                "duration": time.time() - self._start_time,
            }

        except Exception as e:
            logger.error(f"Execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "duration": time.time() - self._start_time,
            }

    def _execute_with_skills(
        self, task_description: str, skills: Dict[str, Any], max_seconds: float
    ) -> Dict[str, Any]:
        """
        Execute skills at SYSTEM level using LoopPolicy.

        System-level means all skills (even analysis/design) go through
        AgentLoop with multi-round + human confirmation.
        """
        results = {}
        previous_artifacts = {}
        active_service = None
        self._escalations = []

        for skill_name, skill_adapter in skills.items():
            if time.time() - self._start_time > max_seconds:
                logger.warning("Time limit reached")
                break

            loop_config = self.loop_policy.get_config(
                level=ExecutionLevel.SYSTEM,
                skill_name=skill_name,
            )
            logger.info(
                f"=== {skill_name} [SYSTEM/{loop_config.mode.value}] "
                f"max_iter={loop_config.max_iterations} ==="
            )

            pre = self.spec_gate.pre_inject(
                skill_name, task_description, active_service
            )
            if pre.get("active_service"):
                active_service = pre["active_service"]

            base_context = {
                "task_description": task_description,
                "project_path": str(self.project_path),
                "skills": skills,
                "spec_context": pre.get("spec_context", ""),
                "spec_level": pre.get("level", "none"),
                "active_service": active_service,
            }

            if not loop_config.needs_loop:
                result = skill_adapter.execute(
                    task_description,
                    {
                        **base_context,
                        "previous_artifacts": previous_artifacts,
                    },
                )
                self._bridge_bmad_to_spec(skill_name, result)
                if result.get("success") and result.get("artifacts"):
                    previous_artifacts[skill_name] = result["artifacts"]
                results[skill_name] = {
                    "success": result.get("success", True),
                    "artifacts": result.get("artifacts", {}),
                    "error": result.get("error"),
                    "spec_level": pre.get("level", "none"),
                    "spec_passed": result.get("success", True),
                    "loop_mode": "one_pass",
                }
                logger.info(f"Skill {skill_name} completed (one-pass, no loop needed)")
                continue

            agent_loop = AgentLoop(
                evaluator=self.evaluator,
                max_iterations=loop_config.max_iterations,
                pass_threshold=loop_config.pass_threshold,
            )

            def make_execute_fn(adapter, ctx, prev_art):
                def execute_fn(desc, loop_ctx):
                    merged = {**ctx, **loop_ctx}
                    if loop_ctx.get("previous_artifacts"):
                        merged["previous_artifacts"] = loop_ctx["previous_artifacts"]
                    else:
                        merged["previous_artifacts"] = prev_art
                    return adapter.execute(desc, merged)

                return execute_fn

            execute_fn = make_execute_fn(
                skill_adapter, base_context, previous_artifacts
            )

            outcome = agent_loop.run(
                task_description=task_description,
                skill_name=skill_name,
                skill_execute_fn=execute_fn,
                context=base_context,
                active_service=active_service,
            )

            if outcome.passed and outcome.final_result.get("artifacts"):
                previous_artifacts[skill_name] = outcome.final_result["artifacts"]

            self._bridge_bmad_to_spec(skill_name, outcome.final_result)

            results[skill_name] = {
                "success": outcome.passed,
                "artifacts": outcome.final_result.get("artifacts", {}),
                "error": outcome.final_result.get("error"),
                "spec_level": pre.get("level", "none"),
                "spec_passed": outcome.passed,
                "loop_mode": loop_config.mode.value,
                "iterations": outcome.total_iterations,
                "final_score": outcome.final_evaluation.score
                if outcome.final_evaluation
                else 0.0,
            }

            if loop_config.human_confirm_on_pass and outcome.passed:
                results[skill_name]["human_confirm_needed"] = True
                results[skill_name]["confirm_message"] = (
                    f"Skill {skill_name} passed evaluation (score={outcome.final_evaluation.score:.2f}, "
                    f"{outcome.total_iterations} iteration(s)). Please review before proceeding."
                )

            if outcome.escalated:
                escalation = agent_loop.build_escalation_message(outcome)
                self._escalations.append(escalation)
                results[skill_name]["escalated"] = True
                results[skill_name]["escalation"] = escalation
                logger.warning(
                    f"Skill {skill_name} ESCALATED after {outcome.total_iterations} iterations"
                )
            else:
                score_str = (
                    f"(score={outcome.final_evaluation.score:.2f})"
                    if outcome.final_evaluation
                    else ""
                )
                logger.info(
                    f"Skill {skill_name} PASSED in {outcome.total_iterations} iteration(s) {score_str}"
                )

        return results

    def _bridge_bmad_to_spec(self, skill_name: str, result: Dict[str, Any]):
        """Route bmad-evo outputs to spec-kit automatically."""
        if skill_name != "bmad-evo" or not result.get("success"):
            return

        artifacts = result.get("artifacts", {})
        constraints = artifacts.get("constraints_for_spec")

        if not constraints:
            return

        try:
            from specs.constraint_validator import ConstraintValidator

            cv = ConstraintValidator(self.project_path)
            for rule in constraints.get("contract", []):
                cv.add_contract_rule(
                    rule.get("rule", ""),
                    rule.get("scope", "global"),
                    rule.get("details", ""),
                )
            for rule in constraints.get("behavior", []):
                cv.add_behavior_rule(
                    rule.get("rule", ""),
                    rule.get("applies_to", ["*"]),
                    rule.get("details", ""),
                )
            logger.info("Bridged bmad-evo constraints -> spec-kit")
        except Exception as e:
            logger.debug(f"Bridge bmad->spec skipped: {e}")

    def get_status(self) -> Dict[str, Any]:
        """获取编排器状态"""
        return {
            "platform": self.platform,
            "project_path": str(self.project_path),
            "skills_config": self.config.get("skills", {}),
            "routing_rules": self.config.get("routing_rules", {}),
        }

    def get_escalations(self) -> List[Dict[str, Any]]:
        """Get all pending human escalations from the agent loop."""
        return list(self._escalations)

    def handle_escalation_decision(
        self,
        skill_name: str,
        decision: str,
        task_description: str = None,
        skills: Dict[str, Any] = None,
        max_seconds: float = 3600,
    ) -> Dict[str, Any]:
        """
        Handle human decision for an escalated skill.

        Args:
            skill_name: The skill that was escalated
            decision: A/B/C/D/E from escalation options
            task_description: Override task description (for A)
            skills: Loaded skills dict
            max_seconds: Remaining time budget

        Options:
            A - Retry with adjusted task description
            B - Relax constraints and retry
            C - Skip this skill and continue
            D - Split task and retry (not implemented here, handled by pipeline)
            E - Abort
        """
        if not skills:
            skills = self.skill_loader.load_skills([skill_name])
        skill_adapter = skills.get(skill_name)
        if not skill_adapter:
            return {"success": False, "error": f"Skill {skill_name} not found"}

        if decision == "E":
            return {"success": False, "error": "Aborted by user", "aborted": True}

        if decision == "C":
            logger.info(f"Skipping escalated skill: {skill_name}")
            return {"success": True, "skipped": True}

        desc = (
            task_description or "Please improve the previous output based on feedback"
        )

        loop_evaluator = self.evaluator
        if decision == "B":
            loop_evaluator = ExecutionEvaluator(spec_gate=None)

        active_service = None
        pre = self.spec_gate.pre_inject(skill_name, desc, None)
        if pre.get("active_service"):
            active_service = pre["active_service"]

        context = {
            "task_description": desc,
            "project_path": str(self.project_path),
            "spec_context": pre.get("spec_context", ""),
            "relaxed_constraints": decision == "B",
        }

        loop_config = self.loop_policy.get_config(
            level=ExecutionLevel.SYSTEM,
            role_type=skill_name,
            skill_name=skill_name,
        )
        agent_loop = AgentLoop(
            evaluator=loop_evaluator,
            max_iterations=loop_config.max_iterations,
            pass_threshold=loop_config.pass_threshold,
        )

        outcome = agent_loop.run(
            task_description=desc,
            skill_name=skill_name,
            skill_execute_fn=lambda d, ctx: skill_adapter.execute(
                d, {**context, **ctx}
            ),
            context=context,
            active_service=active_service,
        )

        return {
            "success": outcome.passed,
            "iterations": outcome.total_iterations,
            "score": outcome.final_evaluation.score
            if outcome.final_evaluation
            else 0.0,
            "escalated": outcome.escalated,
        }
