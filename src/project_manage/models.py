"""
Data models for Project Manage module.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ProjectStatus(str, Enum):
    INIT = "init"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    ARCHIVED = "archived"


class ProjectInitMode(str, Enum):
    NEW = "new"
    CLONE = "clone"
    LOCAL = "local"


class DeliveryStatus(str, Enum):
    DRAFT = "draft"
    STAGED = "staged"
    GATE_PASSED = "gate_passed"
    APPROVED = "approved"
    PROMOTED = "promoted"
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class DriftSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GateDecision(str, Enum):
    PASS = "pass"
    BLOCKED = "blocked"
    NEEDS_FIX = "needs_fix"


class RuleType(str, Enum):
    STATIC = "static"
    EXECUTABLE = "executable"


@dataclass
class ProjectRecord:
    project_id: str = ""
    name: str = ""
    status: str = ProjectStatus.INIT.value
    init_mode: str = ""
    target_path: str = ""
    repo_url: str = ""
    default_branch: str = "main"
    tech_stack: Dict[str, str] = field(default_factory=dict)
    active_pack: Dict[str, str] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.project_id:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.project_id = f"proj_{ts}_{uuid.uuid4().hex[:4]}"
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = datetime.now()

    VALID_TRANSITIONS = {
        ProjectStatus.INIT.value: {
            ProjectStatus.ACTIVE.value,
            ProjectStatus.ARCHIVED.value,
        },
        ProjectStatus.ACTIVE.value: {
            ProjectStatus.PAUSED.value,
            ProjectStatus.COMPLETED.value,
            ProjectStatus.ARCHIVED.value,
        },
        ProjectStatus.PAUSED.value: {
            ProjectStatus.ACTIVE.value,
            ProjectStatus.ARCHIVED.value,
        },
        ProjectStatus.COMPLETED.value: {ProjectStatus.ARCHIVED.value},
        ProjectStatus.ABANDONED.value: {ProjectStatus.ARCHIVED.value},
        ProjectStatus.ARCHIVED.value: set(),
    }

    def can_transition_to(self, new_status: str) -> bool:
        allowed = self.VALID_TRANSITIONS.get(self.status, set())
        return new_status in allowed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status,
            "init_mode": self.init_mode,
            "target_path": self.target_path,
            "repo_url": self.repo_url,
            "default_branch": self.default_branch,
            "tech_stack": self.tech_stack,
            "active_pack": self.active_pack,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "archived_at": self.archived_at.isoformat() if self.archived_at else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectRecord":
        p = cls(
            project_id=data.get("project_id", ""),
            name=data.get("name", ""),
            status=data.get("status", ProjectStatus.INIT.value),
            init_mode=data.get("init_mode", ""),
            target_path=data.get("target_path", ""),
            repo_url=data.get("repo_url", ""),
            default_branch=data.get("default_branch", "main"),
            tech_stack=data.get("tech_stack", {}),
            active_pack=data.get("active_pack", {}),
            metadata=data.get("metadata", {}),
        )
        if data.get("created_at"):
            p.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("updated_at"):
            p.updated_at = datetime.fromisoformat(data["updated_at"])
        if data.get("archived_at"):
            p.archived_at = datetime.fromisoformat(data["archived_at"])
        return p


@dataclass
class ConstraintRule:
    rule_id: str = ""
    name: str = ""
    rule_type: str = RuleType.STATIC.value
    content: str = ""
    file_path: str = ""
    severity: str = DriftSeverity.MEDIUM.value

    def __post_init__(self):
        if not self.rule_id:
            self.rule_id = f"rule_{uuid.uuid4().hex[:6]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "rule_type": self.rule_type,
            "content": self.content,
            "file_path": self.file_path,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConstraintRule":
        return cls(
            rule_id=data.get("rule_id", ""),
            name=data.get("name", ""),
            rule_type=data.get("rule_type", RuleType.STATIC.value),
            content=data.get("content", ""),
            file_path=data.get("file_path", ""),
            severity=data.get("severity", DriftSeverity.MEDIUM.value),
        )


@dataclass
class ConstraintPack:
    pack_id: str = ""
    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    rules: List[ConstraintRule] = field(default_factory=list)
    quality_gates: Dict[str, Any] = field(default_factory=dict)
    risk_policy: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.pack_id:
            self.pack_id = f"pack_{uuid.uuid4().hex[:6]}"
        if not self.created_at:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "rules": [r.to_dict() for r in self.rules],
            "quality_gates": self.quality_gates,
            "risk_policy": self.risk_policy,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConstraintPack":
        rules = [ConstraintRule.from_dict(r) for r in data.get("rules", [])]
        p = cls(
            pack_id=data.get("pack_id", ""),
            name=data.get("name", ""),
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            rules=rules,
            quality_gates=data.get("quality_gates", {}),
            risk_policy=data.get("risk_policy", {}),
        )
        if data.get("created_at"):
            p.created_at = datetime.fromisoformat(data["created_at"])
        return p


@dataclass
class ExternalChangeEvent:
    event_id: str = ""
    project_id: str = ""
    source: str = "manual"
    commit_range: str = ""
    files: List[str] = field(default_factory=list)
    timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"evt_{uuid.uuid4().hex[:6]}"
        if not self.timestamp:
            self.timestamp = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "project_id": self.project_id,
            "source": self.source,
            "commit_range": self.commit_range,
            "files": self.files,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExternalChangeEvent":
        e = cls(
            event_id=data.get("event_id", ""),
            project_id=data.get("project_id", ""),
            source=data.get("source", "manual"),
            commit_range=data.get("commit_range", ""),
            files=data.get("files", []),
            metadata=data.get("metadata", {}),
        )
        if data.get("timestamp"):
            e.timestamp = datetime.fromisoformat(data["timestamp"])
        return e


@dataclass
class DriftViolation:
    rule: str = ""
    severity: str = DriftSeverity.LOW.value
    message: str = ""
    file: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "file": self.file,
        }


@dataclass
class DriftReport:
    report_id: str = ""
    project_id: str = ""
    pack_name: str = ""
    pack_version: str = ""
    violations: List[DriftViolation] = field(default_factory=list)
    severity: str = DriftSeverity.LOW.value
    status: str = "open"
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.report_id:
            self.report_id = f"drift_{uuid.uuid4().hex[:6]}"
        if not self.created_at:
            self.created_at = datetime.now()
        if self.violations:
            severity_order = {
                DriftSeverity.CRITICAL.value: 4,
                DriftSeverity.HIGH.value: 3,
                DriftSeverity.MEDIUM.value: 2,
                DriftSeverity.LOW.value: 1,
            }
            max_sev = max(
                self.violations, key=lambda v: severity_order.get(v.severity, 0)
            )
            self.severity = max_sev.severity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "project_id": self.project_id,
            "pack_name": self.pack_name,
            "pack_version": self.pack_version,
            "violations": [v.to_dict() for v in self.violations],
            "severity": self.severity,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class GateReport:
    gate_id: str = ""
    project_id: str = ""
    delivery_id: str = ""
    baseline_pass: bool = False
    drift_pass: bool = False
    quality_pass: bool = False
    compat_pass: bool = False
    decision: str = GateDecision.BLOCKED.value
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.gate_id:
            self.gate_id = f"gate_{uuid.uuid4().hex[:6]}"
        if (
            self.baseline_pass
            and self.drift_pass
            and self.quality_pass
            and self.compat_pass
        ):
            self.decision = GateDecision.PASS.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "project_id": self.project_id,
            "delivery_id": self.delivery_id,
            "baseline_pass": self.baseline_pass,
            "drift_pass": self.drift_pass,
            "quality_pass": self.quality_pass,
            "compat_pass": self.compat_pass,
            "decision": self.decision,
            "details": self.details,
        }


@dataclass
class DeliveryRecord:
    delivery_id: str = ""
    project_id: str = ""
    pipeline_id: str = ""
    target: str = "local"
    status: str = DeliveryStatus.DRAFT.value
    risk_level: str = DriftSeverity.LOW.value
    files: List[str] = field(default_factory=list)
    staged_at: Optional[datetime] = None
    promoted_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    rollback_point_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.delivery_id:
            self.delivery_id = f"dlv_{uuid.uuid4().hex[:6]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "project_id": self.project_id,
            "pipeline_id": self.pipeline_id,
            "target": self.target,
            "status": self.status,
            "risk_level": self.risk_level,
            "files": self.files,
            "staged_at": self.staged_at.isoformat() if self.staged_at else None,
            "promoted_at": self.promoted_at.isoformat() if self.promoted_at else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "rollback_point_id": self.rollback_point_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeliveryRecord":
        d = cls(
            delivery_id=data.get("delivery_id", ""),
            project_id=data.get("project_id", ""),
            pipeline_id=data.get("pipeline_id", ""),
            target=data.get("target", "local"),
            status=data.get("status", DeliveryStatus.DRAFT.value),
            risk_level=data.get("risk_level", DriftSeverity.LOW.value),
            files=data.get("files", []),
            rollback_point_id=data.get("rollback_point_id", ""),
            metadata=data.get("metadata", {}),
        )
        if data.get("staged_at"):
            d.staged_at = datetime.fromisoformat(data["staged_at"])
        if data.get("promoted_at"):
            d.promoted_at = datetime.fromisoformat(data["promoted_at"])
        if data.get("verified_at"):
            d.verified_at = datetime.fromisoformat(data["verified_at"])
        return d


@dataclass
class ApprovalRecord:
    approval_id: str = ""
    delivery_id: str = ""
    approver: str = ""
    decision: str = ""
    comment: str = ""
    timestamp: Optional[datetime] = None

    def __post_init__(self):
        if not self.approval_id:
            self.approval_id = f"apr_{uuid.uuid4().hex[:6]}"
        if not self.timestamp:
            self.timestamp = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "delivery_id": self.delivery_id,
            "approver": self.approver,
            "decision": self.decision,
            "comment": self.comment,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class RollbackPoint:
    rollback_id: str = ""
    delivery_id: str = ""
    project_id: str = ""
    snapshot_path: str = ""
    created_at: Optional[datetime] = None
    restored: bool = False

    def __post_init__(self):
        if not self.rollback_id:
            self.rollback_id = f"rbk_{uuid.uuid4().hex[:6]}"
        if not self.created_at:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rollback_id": self.rollback_id,
            "delivery_id": self.delivery_id,
            "project_id": self.project_id,
            "snapshot_path": self.snapshot_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "restored": self.restored,
        }
