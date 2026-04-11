"""
Loop Policy - Determines AgentLoop behavior and model routing per task.

Two execution levels:
1. SYSTEM level: whole-project analysis, architecture, planning
    → AgentLoop with multi-round + human confirmation required
    → Deep/Ultrabrain models for complex reasoning

2. SUB_TASK level: individual tasks after clean decomposition by bmad-evo
    → Analysis/design tasks: 1-pass, quick model
    → Implementation/testing tasks: AgentLoop with standard model
    → Review/debug: deep model for thorough analysis

Model routing (inspired by OMO's Category → Model routing):
- Each LoopConfig carries a ModelRoute indicating which model to use
- Categories: quick (fast), standard (balanced), deep (thorough), ultrabrain (critical)
- The route is a hint — the actual model selection happens at the caller
  (prompt-passing protocol, API layer, etc.)
- Custom routing via context override or register_model_route()
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ExecutionLevel(str, Enum):
    SYSTEM = "system"
    SUB_TASK = "sub_task"


class LoopMode(str, Enum):
    ONE_PASS = "one_pass"
    ITERATE_WITH_HUMAN = "iterate_with_human"
    ITERATE_AUTO_ESCALATE = "iterate_auto_escalate"


class ModelCategory(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"
    ULTRABRAIN = "ultrabrain"


@dataclass
class ModelRoute:
    category: ModelCategory
    model_hint: str = ""
    capabilities: List[str] = field(default_factory=list)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    priority: int = 50

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "model_hint": self.model_hint,
            "capabilities": self.capabilities,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelRoute":
        cat = data.get("category", "standard")
        if isinstance(cat, str):
            cat = ModelCategory(cat)
        return cls(
            category=cat,
            model_hint=data.get("model_hint", ""),
            capabilities=data.get("capabilities", []),
            temperature=data.get("temperature"),
            max_tokens=data.get("max_tokens"),
            priority=data.get("priority", 50),
        )


QUICK_ROUTE = ModelRoute(
    category=ModelCategory.QUICK,
    model_hint="fast",
    capabilities=["fast_response"],
    temperature=0.3,
    max_tokens=2048,
    priority=20,
)

STANDARD_ROUTE = ModelRoute(
    category=ModelCategory.STANDARD,
    model_hint="balanced",
    capabilities=["general"],
    temperature=0.7,
    max_tokens=4096,
    priority=50,
)

DEEP_ROUTE = ModelRoute(
    category=ModelCategory.DEEP,
    model_hint="deep",
    capabilities=["analysis", "review", "reasoning"],
    temperature=0.5,
    max_tokens=8192,
    priority=80,
)

ULTRABRAIN_ROUTE = ModelRoute(
    category=ModelCategory.ULTRABRAIN,
    model_hint="ultrabrain",
    capabilities=["critical_decisions", "architecture", "complex_reasoning"],
    temperature=0.4,
    max_tokens=16384,
    priority=99,
)


def route_for_task(
    role_type: str = "",
    skill_name: str = "",
    task_keywords: Optional[List[str]] = None,
) -> ModelRoute:
    """Determine model route from task context keywords."""
    if task_keywords:
        kw_lower = [k.lower() for k in task_keywords]
        if any(
            k in kw_lower for k in ("critical", "architecture", "decision", "emergency")
        ):
            return ULTRABRAIN_ROUTE
        if any(
            k in kw_lower
            for k in ("review", "audit", "debug", "analyze", "investigate")
        ):
            return DEEP_ROUTE
        if any(
            k in kw_lower for k in ("format", "rename", "trivial", "quick", "simple")
        ):
            return QUICK_ROUTE
    return STANDARD_ROUTE


@dataclass
class LoopConfig:
    max_iterations: int
    pass_threshold: float
    mode: LoopMode
    human_confirm_on_pass: bool = False
    model_route: ModelRoute = field(default_factory=lambda: STANDARD_ROUTE)

    @property
    def needs_loop(self) -> bool:
        return self.mode != LoopMode.ONE_PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_iterations": self.max_iterations,
            "pass_threshold": self.pass_threshold,
            "mode": self.mode.value,
            "human_confirm_on_pass": self.human_confirm_on_pass,
            "model_route": self.model_route.to_dict(),
        }


SYSTEM_ROLE_POLICIES = {
    "analyst": LoopConfig(
        max_iterations=5,
        pass_threshold=0.6,
        mode=LoopMode.ITERATE_WITH_HUMAN,
        human_confirm_on_pass=True,
        model_route=DEEP_ROUTE,
    ),
    "architect": LoopConfig(
        max_iterations=5,
        pass_threshold=0.6,
        mode=LoopMode.ITERATE_WITH_HUMAN,
        human_confirm_on_pass=True,
        model_route=ULTRABRAIN_ROUTE,
    ),
    "planner": LoopConfig(
        max_iterations=3,
        pass_threshold=0.5,
        mode=LoopMode.ITERATE_WITH_HUMAN,
        human_confirm_on_pass=True,
        model_route=DEEP_ROUTE,
    ),
}

SUB_TASK_ROLE_POLICIES = {
    "analyst": LoopConfig(
        max_iterations=1,
        pass_threshold=0.3,
        mode=LoopMode.ONE_PASS,
        model_route=QUICK_ROUTE,
    ),
    "architect": LoopConfig(
        max_iterations=1,
        pass_threshold=0.3,
        mode=LoopMode.ONE_PASS,
        model_route=QUICK_ROUTE,
    ),
    "planner": LoopConfig(
        max_iterations=1,
        pass_threshold=0.3,
        mode=LoopMode.ONE_PASS,
        model_route=QUICK_ROUTE,
    ),
    "spec-writer": LoopConfig(
        max_iterations=1,
        pass_threshold=0.3,
        mode=LoopMode.ONE_PASS,
        model_route=QUICK_ROUTE,
    ),
    "developer": LoopConfig(
        max_iterations=5,
        pass_threshold=0.6,
        mode=LoopMode.ITERATE_AUTO_ESCALATE,
        model_route=STANDARD_ROUTE,
    ),
    "coder": LoopConfig(
        max_iterations=5,
        pass_threshold=0.6,
        mode=LoopMode.ITERATE_AUTO_ESCALATE,
        model_route=STANDARD_ROUTE,
    ),
    "implementer": LoopConfig(
        max_iterations=5,
        pass_threshold=0.6,
        mode=LoopMode.ITERATE_AUTO_ESCALATE,
        model_route=STANDARD_ROUTE,
    ),
    "tester": LoopConfig(
        max_iterations=5,
        pass_threshold=0.6,
        mode=LoopMode.ITERATE_AUTO_ESCALATE,
        model_route=DEEP_ROUTE,
    ),
    "reviewer": LoopConfig(
        max_iterations=3,
        pass_threshold=0.7,
        mode=LoopMode.ITERATE_AUTO_ESCALATE,
        model_route=DEEP_ROUTE,
    ),
}

SYSTEM_SKILL_POLICIES = {
    "bmad-evo": LoopConfig(
        max_iterations=5,
        pass_threshold=0.5,
        mode=LoopMode.ITERATE_WITH_HUMAN,
        human_confirm_on_pass=True,
        model_route=DEEP_ROUTE,
    ),
    "spec-kit": LoopConfig(
        max_iterations=3,
        pass_threshold=0.5,
        mode=LoopMode.ITERATE_WITH_HUMAN,
        human_confirm_on_pass=True,
        model_route=STANDARD_ROUTE,
    ),
    "superpowers": LoopConfig(
        max_iterations=5,
        pass_threshold=0.6,
        mode=LoopMode.ITERATE_AUTO_ESCALATE,
        model_route=STANDARD_ROUTE,
    ),
    "multi-agent-pipeline": LoopConfig(
        max_iterations=1,
        pass_threshold=0.3,
        mode=LoopMode.ONE_PASS,
        model_route=QUICK_ROUTE,
    ),
}


class LoopPolicy:
    """
    Determines how the AgentLoop should behave for a given execution context.

    Usage:
        policy = LoopPolicy()
        config = policy.get_config(
            level=ExecutionLevel.SYSTEM,
            role_type="analyst",
            skill_name="bmad-evo",
        )
        if config.needs_loop:
            loop = AgentLoop(max_iterations=config.max_iterations, ...)

    Custom routing:
        policy.register_model_route("architect", ExecutionLevel.SYSTEM, ULTRABRAIN_ROUTE)
        policy.register_model_route("my_custom_role", ExecutionLevel.SUB_TASK, DEEP_ROUTE)
    """

    def __init__(self):
        self._custom_routes: Dict[str, Dict[str, ModelRoute]] = {}

    def register_model_route(
        self,
        key: str,
        level: ExecutionLevel,
        route: ModelRoute,
    ) -> None:
        """Register a custom model route for a role or skill at a given level.

        Args:
            key: role_type or skill_name to match
            level: ExecutionLevel.SYSTEM or ExecutionLevel.SUB_TASK
            route: ModelRoute to use when this key is resolved at this level
        """
        level_key = level.value
        if level_key not in self._custom_routes:
            self._custom_routes[level_key] = {}
        self._custom_routes[level_key][key] = route
        logger.info(
            f"Registered custom model route: {key}@{level_key} -> {route.category.value}"
        )

    def _resolve_route(
        self,
        level: ExecutionLevel,
        role_type: str = None,
        skill_name: str = None,
        context: Dict[str, Any] = None,
    ) -> Optional[ModelRoute]:
        """Resolve model route from custom registrations or context override.

        Returns None if no explicit override was found, signaling the caller
        to keep the policy's built-in route.
        """
        if context and "model_route" in context:
            mr = context["model_route"]
            if isinstance(mr, ModelRoute):
                return mr
            if isinstance(mr, dict):
                return ModelRoute.from_dict(mr)

        level_key = level.value
        level_routes = self._custom_routes.get(level_key, {})

        if role_type and role_type in level_routes:
            return level_routes[role_type]
        if skill_name and skill_name in level_routes:
            return level_routes[skill_name]

        return None

    def _copy_config(self, cfg: LoopConfig) -> LoopConfig:
        """Create a copy of a LoopConfig so mutations don't affect shared policy tables."""
        return LoopConfig(
            max_iterations=cfg.max_iterations,
            pass_threshold=cfg.pass_threshold,
            mode=cfg.mode,
            human_confirm_on_pass=cfg.human_confirm_on_pass,
            model_route=cfg.model_route,
        )

    def get_config(
        self,
        level: ExecutionLevel,
        role_type: str = None,
        skill_name: str = None,
        context: Dict[str, Any] = None,
    ) -> LoopConfig:
        """
        Get loop configuration for the given execution context.

        Priority:
        1. Context override (explicit config from caller)
        2. Role-based policy
        3. Skill-based policy
        4. Default

        Model route is resolved via _resolve_route and attached to the config.
        """
        if context and "loop_config" in context:
            cfg = context["loop_config"]
            if isinstance(cfg, LoopConfig):
                return cfg

        resolved_route = self._resolve_route(level, role_type, skill_name, context)

        if level == ExecutionLevel.SYSTEM:
            if skill_name and skill_name in SYSTEM_SKILL_POLICIES:
                cfg = self._copy_config(SYSTEM_SKILL_POLICIES[skill_name])
                if resolved_route:
                    cfg.model_route = resolved_route
                return cfg
            if role_type and role_type in SYSTEM_ROLE_POLICIES:
                cfg = self._copy_config(SYSTEM_ROLE_POLICIES[role_type])
                if resolved_route:
                    cfg.model_route = resolved_route
                return cfg
            default = LoopConfig(
                max_iterations=5,
                pass_threshold=0.6,
                mode=LoopMode.ITERATE_WITH_HUMAN,
                human_confirm_on_pass=True,
                model_route=STANDARD_ROUTE,
            )
            if resolved_route:
                default.model_route = resolved_route
            return default

        if level == ExecutionLevel.SUB_TASK:
            if role_type and role_type in SUB_TASK_ROLE_POLICIES:
                cfg = self._copy_config(SUB_TASK_ROLE_POLICIES[role_type])
                if resolved_route:
                    cfg.model_route = resolved_route
                return cfg
            default = LoopConfig(
                max_iterations=5,
                pass_threshold=0.6,
                mode=LoopMode.ITERATE_AUTO_ESCALATE,
                model_route=route_for_task(
                    role_type=role_type or "",
                    skill_name=skill_name or "",
                    task_keywords=context.get("task_keywords") if context else None,
                ),
            )
            if resolved_route:
                default.model_route = resolved_route
            return default

        default = LoopConfig(
            max_iterations=3,
            pass_threshold=0.5,
            mode=LoopMode.ITERATE_AUTO_ESCALATE,
            model_route=route_for_task(
                role_type=role_type or "",
                skill_name=skill_name or "",
                task_keywords=context.get("task_keywords") if context else None,
            ),
        )
        if resolved_route:
            default.model_route = resolved_route
        return default

    def is_system_phase(self, phase: str) -> bool:
        """Check if a pipeline phase is system-level."""
        return phase in (
            "init",
            "analyze",
            "plan",
            "confirm_plan",
            "evolve",
            "verify",
        )

    def is_subtask_phase(self, phase: str) -> bool:
        """Check if a pipeline phase is sub-task level."""
        return phase in ("execute",)

    def get_level_for_phase(self, phase: str) -> ExecutionLevel:
        if self.is_system_phase(phase):
            return ExecutionLevel.SYSTEM
        return ExecutionLevel.SUB_TASK
