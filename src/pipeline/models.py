"""
Data models for the Multi-Agent Pipeline.

Adapted from the reference implementation with:
- Windows compatibility (no fcntl)
- Dynamic roles (from bmad-evo analysis, not predefined)
- Prompt-passing protocol (no external AI calls)
- Long-running pipeline support (up to 8 hours)
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class PipelinePhase(str, Enum):
    INIT = "init"
    ANALYZE = "analyze"
    PLAN = "plan"
    CONFIRM_PLAN = "confirm_plan"
    EXECUTE = "execute"
    CHECK = "check"
    DECIDE = "decide"
    EVOLVE = "evolve"
    VERIFY = "verify"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class PipelineState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskPriority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class HumanDecision(str, Enum):
    CONTINUE = "continue"
    ADJUST = "adjust"
    COMPLETE = "complete"
    PAUSE = "pause"
    CANCEL = "cancel"


@dataclass
class RoleMetrics:
    total_tasks: int = 0
    success_count: int = 0
    fail_count: int = 0
    avg_duration_seconds: float = 0.0
    success_rate: float = 1.0

    def update(self, duration_seconds: float, success: bool):
        self.total_tasks += 1
        if success:
            self.success_count += 1
        else:
            self.fail_count += 1
        self.avg_duration_seconds = (
            self.avg_duration_seconds * (self.total_tasks - 1) + duration_seconds
        ) / self.total_tasks
        self.success_rate = self.success_count / self.total_tasks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "avg_duration_seconds": round(self.avg_duration_seconds, 2),
            "success_rate": round(self.success_rate, 2),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RoleMetrics":
        m = cls()
        m.total_tasks = data.get("total_tasks", 0)
        m.success_count = data.get("success_count", 0)
        m.fail_count = data.get("fail_count", 0)
        m.avg_duration_seconds = data.get("avg_duration_seconds", 0.0)
        m.success_rate = data.get("success_rate", 1.0)
        return m


@dataclass
class RoleConfig:
    poll_interval_seconds: float = 5.0
    lock_timeout_seconds: int = 120
    max_retries: int = 3
    max_steps_per_task: int = 50
    task_timeout_seconds: int = 600


@dataclass
class Role:
    id: str
    type: str
    name: str
    capabilities: List[str] = field(default_factory=list)
    status: str = "idle"
    queue: List[str] = field(default_factory=list)
    current_task: Optional[str] = None
    metrics: RoleMetrics = field(default_factory=RoleMetrics)
    config: RoleConfig = field(default_factory=RoleConfig)

    def __post_init__(self):
        if isinstance(self.metrics, dict):
            self.metrics = RoleMetrics.from_dict(self.metrics)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "capabilities": self.capabilities,
            "status": self.status,
            "queue": self.queue,
            "current_task": self.current_task,
            "metrics": self.metrics.to_dict(),
            "config": {
                "poll_interval_seconds": self.config.poll_interval_seconds,
                "lock_timeout_seconds": self.config.lock_timeout_seconds,
                "max_retries": self.config.max_retries,
                "max_steps_per_task": self.config.max_steps_per_task,
                "task_timeout_seconds": self.config.task_timeout_seconds,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Role":
        config_data = data.get("config", {})
        config = RoleConfig(
            poll_interval_seconds=config_data.get("poll_interval_seconds", 5.0),
            lock_timeout_seconds=config_data.get("lock_timeout_seconds", 120),
            max_retries=config_data.get("max_retries", 3),
            max_steps_per_task=config_data.get("max_steps_per_task", 50),
            task_timeout_seconds=config_data.get("task_timeout_seconds", 600),
        )
        return cls(
            id=data["id"],
            type=data["type"],
            name=data["name"],
            capabilities=data.get("capabilities", []),
            status=data.get("status", "idle"),
            queue=data.get("queue", []),
            current_task=data.get("current_task"),
            metrics=RoleMetrics.from_dict(data.get("metrics", {})),
            config=config,
        )


@dataclass
class Task:
    id: str = ""
    pipeline_id: str = ""
    role_id: str = ""
    name: str = ""
    description: str = ""
    priority: str = "P2"
    status: str = "pending"
    depends_on: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3
    retry_delay_seconds: float = 30.0
    retry_backoff_factor: float = 2.0
    last_retry_at: Optional[datetime] = None
    failure_history: List[Dict[str, Any]] = field(default_factory=list)
    step_count: int = 0
    max_steps: int = 50
    result: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    context_injection: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.id:
            import uuid

            self.id = f"task_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "pipeline_id": self.pipeline_id,
            "role_id": self.role_id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "depends_on": self.depends_on,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
            "retry_backoff_factor": self.retry_backoff_factor,
            "last_retry_at": self.last_retry_at.isoformat()
            if self.last_retry_at
            else None,
            "failure_history": self.failure_history,
            "step_count": self.step_count,
            "max_steps": self.max_steps,
            "result": self.result,
            "artifacts": self.artifacts,
            "context_injection": self.context_injection,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        t = cls(
            id=data.get("id", ""),
            pipeline_id=data.get("pipeline_id", ""),
            role_id=data.get("role_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            priority=data.get("priority", "P2"),
            status=data.get("status", "pending"),
            depends_on=data.get("depends_on", []),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            retry_delay_seconds=data.get("retry_delay_seconds", 30.0),
            retry_backoff_factor=data.get("retry_backoff_factor", 2.0),
            failure_history=data.get("failure_history", []),
            step_count=data.get("step_count", 0),
            max_steps=data.get("max_steps", 50),
            result=data.get("result", {}),
            artifacts=data.get("artifacts", {}),
            context_injection=data.get("context_injection"),
        )
        if data.get("created_at"):
            t.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("started_at"):
            t.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            t.completed_at = datetime.fromisoformat(data["completed_at"])
        if data.get("last_retry_at"):
            t.last_retry_at = datetime.fromisoformat(data["last_retry_at"])
        return t


@dataclass
class Checkpoint:
    id: str = ""
    pipeline_id: str = ""
    phase: str = ""
    task_id: Optional[str] = None
    snapshot: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    label: str = ""

    def __post_init__(self):
        if not self.id:
            import uuid

            self.id = f"ckpt_{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "pipeline_id": self.pipeline_id,
            "phase": self.phase,
            "task_id": self.task_id,
            "snapshot": self.snapshot,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Checkpoint":
        c = cls(
            id=data.get("id", ""),
            pipeline_id=data.get("pipeline_id", ""),
            phase=data.get("phase", ""),
            task_id=data.get("task_id"),
            snapshot=data.get("snapshot", {}),
            label=data.get("label", ""),
        )
        if data.get("created_at"):
            c.created_at = datetime.fromisoformat(data["created_at"])
        return c


@dataclass
class DecisionPoint:
    phase: str
    question: str
    options: List[str] = field(default_factory=list)
    context_summary: str = ""
    chosen: Optional[str] = None
    chosen_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "question": self.question,
            "options": self.options,
            "context_summary": self.context_summary,
            "chosen": self.chosen,
            "chosen_at": self.chosen_at.isoformat() if self.chosen_at else None,
        }


@dataclass
class PipelineRun:
    id: str = ""
    description: str = ""
    state: str = "idle"
    phase: str = "init"
    tasks: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    decision_history: List[Dict[str, Any]] = field(default_factory=list)
    pdca_cycle: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    max_duration_hours: float = 5.0
    started_at: Optional[datetime] = None
    recovery_count: int = 0
    last_recovery_at: Optional[datetime] = None
    last_checkpoint_id: str = ""
    decision_timeout_seconds: float = 1800.0
    last_decision_at: Optional[datetime] = None
    phase_history: List[Dict[str, Any]] = field(default_factory=list)
    backlog: List[Dict[str, Any]] = field(default_factory=list)
    pdca_max_cycles: int = 20

    def __post_init__(self):
        if not self.id:
            import uuid

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.id = f"pipe_{ts}_{uuid.uuid4().hex[:4]}"
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = datetime.now()
        if not self.phase_history:
            self.phase_history = [
                {
                    "phase": self.phase,
                    "at": self.created_at.isoformat()
                    if self.created_at
                    else datetime.now().isoformat(),
                }
            ]

    def record_phase(self, new_phase: str):
        if self.phase != new_phase:
            self.phase_history.append(
                {"from": self.phase, "to": new_phase, "at": datetime.now().isoformat()}
            )
            self.phase = new_phase
            self.updated_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "state": self.state,
            "phase": self.phase,
            "tasks": self.tasks,
            "roles": self.roles,
            "artifacts": self.artifacts,
            "decision_history": self.decision_history,
            "pdca_cycle": self.pdca_cycle,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "max_duration_hours": self.max_duration_hours,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "recovery_count": self.recovery_count,
            "last_recovery_at": self.last_recovery_at.isoformat()
            if self.last_recovery_at
            else None,
            "last_checkpoint_id": self.last_checkpoint_id,
            "decision_timeout_seconds": self.decision_timeout_seconds,
            "last_decision_at": self.last_decision_at.isoformat()
            if self.last_decision_at
            else None,
            "phase_history": self.phase_history,
            "backlog": self.backlog,
            "pdca_max_cycles": self.pdca_max_cycles,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineRun":
        p = cls(
            id=data.get("id", ""),
            description=data.get("description", ""),
            state=data.get("state", "idle"),
            phase=data.get("phase", "init"),
            tasks=data.get("tasks", []),
            roles=data.get("roles", []),
            artifacts=data.get("artifacts", {}),
            decision_history=data.get("decision_history", []),
            pdca_cycle=data.get("pdca_cycle", 0),
            max_duration_hours=data.get("max_duration_hours", 8.0),
            recovery_count=data.get("recovery_count", 0),
            last_checkpoint_id=data.get("last_checkpoint_id", ""),
            decision_timeout_seconds=data.get("decision_timeout_seconds", 1800.0),
            phase_history=data.get("phase_history", []),
            backlog=data.get("backlog", []),
            pdca_max_cycles=data.get("pdca_max_cycles", 20),
        )
        if data.get("created_at"):
            p.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("updated_at"):
            p.updated_at = datetime.fromisoformat(data["updated_at"])
        if data.get("completed_at"):
            p.completed_at = datetime.fromisoformat(data["completed_at"])
        if data.get("started_at"):
            p.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("last_recovery_at"):
            p.last_recovery_at = datetime.fromisoformat(data["last_recovery_at"])
        if data.get("last_decision_at"):
            p.last_decision_at = datetime.fromisoformat(data["last_decision_at"])
        return p


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return super().default(obj)
