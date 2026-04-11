"""
SpecGate - Spec-gated execution middleware with lifecycle hooks.

Wraps every skill call with progressive spec context injection:
- L1 (~50 tokens): System anchor - always injected, prevents drift
- L2 (~200 tokens): Service-focused constraints + pending scenarios - only for relevant skills
- L3 (~1000 tokens): Full service spec - on-demand only

Superpowers-inspired enhancements:
- Self-review checklist injected at L2+ to standardize output quality
- YAGNI / stuck-protocol injected to prevent overbuilding and enable early blocking
- Two-stage review framework: spec compliance THEN quality (order matters)

Lifecycle Hooks (replaces OMO-style standalone hooks system):
- on_pipeline_start: fired when pipeline enters ANALYZE
- on_task_start: fired before each task executes (chained with pre_inject)
- on_task_complete: fired after each task completes (chained with post_check)
- on_pdca_cycle: fired at PDCA CHECK phase
- on_pipeline_complete: fired when pipeline reaches COMPLETED
- on_error: fired on task failure or pipeline failure

All hooks support chained handlers: register multiple handlers per point,
they execute in order, each can enrich/modify the context.
"""

import logging
from typing import Callable, Dict, Any, List, Optional
from pathlib import Path
from collections import defaultdict

from .reasoning_map import ReasoningMap
from .constraint_validator import ConstraintValidator
from .scenario_tracker import ScenarioTracker

logger = logging.getLogger(__name__)

SELF_REVIEW_CHECKLIST = (
    "\n[SELF-REVIEW] Before reporting done, verify:\n"
    "1. Completeness: Did you implement everything requested?\n"
    "2. Quality: Clear names? Clean code? Maintainable?\n"
    "3. Discipline: Did you avoid overbuilding? (YAGNI)\n"
    "4. Testing: Do tests verify real behavior?\n"
    "5. Spec: Does output align with system goal?\n"
)

YAGNI_STUCK_PROTOCOL = (
    "\n[PROTOCOL]\n"
    "- Build ONLY what is requested. Nothing extra. (YAGNI)\n"
    "- STOP and report BLOCKED if: multiple valid approaches exist, "
    "missing context, or uncertain about correctness.\n"
    "- Report format: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT\n"
)

TWO_STAGE_REVIEW_HINT = (
    "\n[REVIEW ORDER] After implementation:\n"
    "Stage 1 - Spec compliance: Did we build WHAT was asked? (required first)\n"
    "Stage 2 - Quality review: Is it WELL-BUILT? (only after Stage 1 passes)\n"
)

LIFECYCLE_POINTS = (
    "on_pipeline_start",
    "on_task_start",
    "on_task_complete",
    "on_pdca_cycle",
    "on_pipeline_complete",
    "on_error",
)


class LifecycleHookRegistry:
    """
    Chained handler registry for spec-kit lifecycle points.

    Each lifecycle point can have multiple handlers registered.
    Handlers execute in registration order; each receives the accumulated
    context dict and returns an enriched/modified context dict.
    A handler can set context["_abort"] = True to stop the chain.
    """

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = defaultdict(list)

    def register(self, point: str, handler: Callable, priority: int = 50):
        if point not in LIFECYCLE_POINTS:
            raise ValueError(
                f"Unknown lifecycle point: {point}. Available: {list(LIFECYCLE_POINTS)}"
            )
        handler._hook_priority = priority
        self._handlers[point].append(handler)
        self._handlers[point].sort(key=lambda h: getattr(h, "_hook_priority", 50))

    def unregister(self, point: str, handler: Callable):
        if point in self._handlers:
            self._handlers[point] = [
                h for h in self._handlers[point] if h is not handler
            ]

    def emit(self, point: str, context: Dict[str, Any]) -> Dict[str, Any]:
        for handler in self._handlers.get(point, []):
            try:
                result = handler(context)
                if result and isinstance(result, dict):
                    context.update(result)
                if context.get("_abort"):
                    logger.info(
                        f"Lifecycle chain aborted at {point} by {handler.__name__}"
                    )
                    break
            except Exception as e:
                logger.warning(
                    f"Lifecycle handler {getattr(handler, '__name__', handler)} "
                    f"failed at {point}: {e}"
                )
        context.pop("_abort", None)
        return context

    def list_handlers(self, point: str = None) -> Dict[str, List[str]]:
        if point:
            return {
                point: [
                    getattr(h, "__name__", str(h))
                    for h in self._handlers.get(point, [])
                ]
            }
        return {
            p: [getattr(h, "__name__", str(h)) for h in hs]
            for p, hs in self._handlers.items()
            if hs
        }

    def clear(self, point: str = None):
        if point:
            self._handlers.pop(point, None)
        else:
            self._handlers.clear()

    def get_status(self) -> Dict[str, Any]:
        return {
            "registered_points": {p: len(hs) for p, hs in self._handlers.items() if hs},
            "total_handlers": sum(len(hs) for hs in self._handlers.values()),
            "available_points": list(LIFECYCLE_POINTS),
        }


class SpecGate:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.reasoning_map = ReasoningMap(project_path)
        self.constraint_validator = ConstraintValidator(project_path)
        self.scenario_tracker = ScenarioTracker(project_path)
        self.hooks = LifecycleHookRegistry()

    def register_lifecycle_handler(
        self, point: str, handler: Callable, priority: int = 50
    ):
        self.hooks.register(point, handler, priority)

    def emit_lifecycle(self, point: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return self.hooks.emit(point, context)

    def pre_inject(
        self,
        skill_name: str,
        task_description: str,
        active_service: Optional[str] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build progressive context for a skill before execution.

        Now integrates with on_task_start lifecycle handlers:
        1. Build base spec context (L1/L2/L3)
        2. Run chained on_task_start handlers which can enrich context
        3. Return merged result

        Returns a dict with:
        - spec_context: the injected context string (compact)
        - active_service: the resolved service name
        - level: which context level was used
        """
        hook_context = {
            "skill_name": skill_name,
            "task_description": task_description,
            "active_service": active_service,
            **(extra_context or {}),
        }
        hook_context = self.hooks.emit("on_task_start", hook_context)

        if not self.reasoning_map.agent_md_path.exists():
            result = {
                "spec_context": hook_context.get("spec_context", ""),
                "active_service": hook_context.get("active_service"),
                "level": "none",
            }
            result.update({k: v for k, v in hook_context.items() if k not in result})
            return result

        l1 = self.reasoning_map.get_system_anchor()

        needs_l2 = skill_name in (
            "bmad-evo",
            "multi-agent-pipeline",
            "superpowers",
            "spec-kit",
        )
        service = (
            hook_context.get("active_service")
            or active_service
            or self._infer_service(task_description)
        )

        if needs_l2 and service:
            l2 = self.reasoning_map.get_service_focus(service)
            scenarios = self.scenario_tracker.get_pending_summary(service)
            constraints = self._get_relevant_constraints(service)
            context = f"{l1}\n{l2}\n{constraints}\n{scenarios}"

            if skill_name == "superpowers":
                context += SELF_REVIEW_CHECKLIST + YAGNI_STUCK_PROTOCOL
                if task_description and any(
                    kw in task_description.lower()
                    for kw in ("review", "check", "verify", "validate")
                ):
                    context += TWO_STAGE_REVIEW_HINT
            elif skill_name == "bmad-evo":
                context += YAGNI_STUCK_PROTOCOL
            elif skill_name == "spec-kit":
                context += SELF_REVIEW_CHECKLIST

            level = "L2"
        else:
            context = l1
            level = "L1"

        logger.info(
            f"SpecGate pre-inject: {level} for {skill_name} (service={service})"
        )

        result = {
            "spec_context": context,
            "active_service": service,
            "level": level,
        }
        result.update({k: v for k, v in hook_context.items() if k not in result})
        return result

    def post_check(
        self,
        skill_name: str,
        execution_result: Dict[str, Any],
        active_service: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Check execution result against spec constraints.

        Now integrates with on_task_complete lifecycle handlers:
        1. Run standard two-stage review
        2. Run chained on_task_complete handlers which can add warnings/override pass
        3. Return merged result

        Returns:
        - passed: whether the result passes spec checks
        - warnings: non-blocking issues
        - failed_scenarios: any scenarios that should be marked failed
        - stage1_passed: spec compliance stage result
        - stage2_passed: quality stage result
        """
        checks = {
            "passed": True,
            "warnings": [],
            "failed_scenarios": [],
            "drift_check": "",
            "stage1_passed": True,
            "stage1_issues": [],
            "stage2_passed": True,
            "stage2_issues": [],
        }

        if not self.reasoning_map.agent_md_path.exists():
            hook_ctx = self.hooks.emit(
                "on_task_complete",
                {
                    "skill_name": skill_name,
                    "execution_result": execution_result,
                    "active_service": active_service,
                    **checks,
                },
            )
            for k in ("passed", "stage1_passed", "stage2_passed"):
                checks[k] = hook_ctx.get(k, checks[k])
            for k in ("warnings", "stage1_issues", "stage2_issues", "failed_scenarios"):
                checks[k] = hook_ctx.get(k, checks[k])
            return checks

        failed_summary = self.scenario_tracker.get_failed_summary(active_service)
        if failed_summary:
            checks["warnings"].append(failed_summary)

        # Stage 1: Spec Compliance (drift + boundary)
        stage1_issues = self._check_spec_compliance(
            skill_name, execution_result, active_service
        )
        if stage1_issues:
            checks["stage1_passed"] = False
            checks["stage1_issues"] = stage1_issues
            checks["warnings"].extend(stage1_issues)

        # Stage 2: Quality (constraints + scenario coverage)
        stage2_issues = self._check_quality(
            skill_name, execution_result, active_service
        )
        if stage2_issues:
            checks["stage2_passed"] = False
            checks["stage2_issues"] = stage2_issues
            checks["warnings"].extend(stage2_issues)

        checks["passed"] = checks["stage1_passed"] and checks["stage2_passed"]

        if skill_name == "superpowers":
            artifacts = execution_result.get("artifacts", {})
            code_content = artifacts.get("code", "")
            if code_content and isinstance(code_content, str):
                goal = self.reasoning_map.get_system_goal()
                if goal and self._code_drifts_from_goal(
                    code_content, goal, active_service
                ):
                    checks["warnings"].append(
                        "[DRIFT] Generated code may not align with system goal"
                    )
                    checks["passed"] = False
                    checks["stage1_passed"] = False
                    checks["stage1_issues"].append("Goal drift detected")

        checks["drift_check"] = self.reasoning_map.get_system_anchor()

        hook_ctx = self.hooks.emit(
            "on_task_complete",
            {
                "skill_name": skill_name,
                "execution_result": execution_result,
                "active_service": active_service,
                **checks,
            },
        )
        for k in ("passed", "stage1_passed", "stage2_passed"):
            if k in hook_ctx:
                checks[k] = hook_ctx[k]
        for k in ("warnings", "stage1_issues", "stage2_issues", "failed_scenarios"):
            if k in hook_ctx:
                extra = hook_ctx[k]
                if isinstance(extra, list):
                    checks[k] = checks[k] + [x for x in extra if x not in checks[k]]

        logger.info(
            f"SpecGate post-check for {skill_name}: "
            f"stage1={'PASS' if checks['stage1_passed'] else 'FAIL'} "
            f"stage2={'PASS' if checks['stage2_passed'] else 'FAIL'} "
            f"warnings={len(checks['warnings'])}"
        )
        return checks

    def _check_spec_compliance(
        self,
        skill_name: str,
        execution_result: Dict[str, Any],
        active_service: Optional[str],
    ) -> List[str]:
        """Stage 1: Did the output match what was specified?"""
        issues = []
        if not active_service:
            return issues

        services = self.reasoning_map.get_services()
        svc = next((s for s in services if s.get("name") == active_service), None)
        if not svc:
            return issues

        artifacts = execution_result.get("artifacts", {})
        artifact_text = str(artifacts).lower()

        capabilities = svc.get("capabilities", [])
        if capabilities:
            mentioned = sum(1 for cap in capabilities if cap.lower() in artifact_text)
            if mentioned == 0 and execution_result.get("success"):
                issues.append(
                    f"[SPEC] Output doesn't reference any known capability "
                    f"of {active_service}: {capabilities[:5]}"
                )

        return issues

    def _check_quality(
        self,
        skill_name: str,
        execution_result: Dict[str, Any],
        active_service: Optional[str],
    ) -> List[str]:
        """Stage 2: Is the output well-structured?"""
        issues = []
        artifacts = execution_result.get("artifacts", {})

        if not artifacts and execution_result.get("success"):
            issues.append("[QUALITY] Successful execution produced no artifacts")

        if active_service:
            pending = self.scenario_tracker.get_scenarios(
                service_name=active_service, status="pending"
            )
            high_priority_pending = [
                s for s in pending if s.get("priority") in ("P0", "P1")
            ]
            if high_priority_pending and execution_result.get("success"):
                issues.append(
                    f"[QUALITY] {len(high_priority_pending)} high-priority scenario(s) "
                    f"remain pending for {active_service}"
                )

        return issues

    def build_enriched_context(
        self, task_description: str, active_service: Optional[str] = None
    ) -> str:
        """Build full L3 context for on-demand loading (e.g., for bmad-evo deep analysis)."""
        service = active_service or self._infer_service(task_description)

        parts = [self.reasoning_map.get_system_anchor()]

        if service:
            parts.append(self.reasoning_map.get_service_focus(service))
            parts.append(self.scenario_tracker.get_pending_summary(service))
            parts.append(self._get_relevant_constraints(service))

            detail = self.reasoning_map.get_detailed_spec(service)
            if detail:
                parts.append(f"[DETAILED SPEC]\n{detail}")

        return "\n".join(parts)

    def _infer_service(self, task_description: str) -> Optional[str]:
        """Try to match task description to a known service."""
        services = self.reasoning_map.get_services()
        desc_lower = task_description.lower()
        for svc in services:
            name = svc.get("name", "")
            if name and name.lower().replace("-", " ") in desc_lower.replace("-", " "):
                return name
            resp = svc.get("responsibility", "").lower()
            keywords = resp.split()
            matches = sum(1 for kw in keywords if kw in desc_lower and len(kw) > 3)
            if matches >= 2:
                return name
        return None

    def _get_relevant_constraints(self, service_name: str) -> str:
        """Compact constraint summary for context injection."""
        rules = self.constraint_validator.load_constraints()
        parts = ["[CONSTRAINTS]"]

        contract_rules = rules.get("contract", [])
        for r in contract_rules:
            scope = r.get("scope", "global")
            if scope == "global" or scope == service_name:
                parts.append(f"- {r['rule']}")

        behavior_rules = rules.get("behavior", [])
        for r in behavior_rules:
            applies = r.get("applies_to", ["*"])
            if "*" in applies or service_name in applies:
                parts.append(f"- {r['rule']}")

        result = " | ".join(parts[:5])
        if len(parts) > 5:
            result += f" +{len(parts) - 5} more"
        return result

    def _code_drifts_from_goal(
        self, code: str, goal: str, service: Optional[str]
    ) -> bool:
        """Heuristic check: does generated code reference concepts outside the service boundary?"""
        if not service:
            return False
        services = self.reasoning_map.get_services()
        svc = next((s for s in services if s.get("name") == service), None)
        if not svc:
            return False
        boundaries = svc.get("boundaries", [])
        for boundary in boundaries:
            boundary_keywords = [w.lower() for w in boundary.split() if len(w) > 4]
            for kw in boundary_keywords:
                if kw in code.lower():
                    return True
        return False
