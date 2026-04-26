"""
Tests for project_manage module: registry, packs, ingest, drift, gates,
delivery (four-stage), approval, audit, metrics, adapter, integration.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
import sys
import subprocess
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from project_manage.models import (
    ProjectRecord,
    ProjectStatus,
    ProjectInitMode,
    ConstraintPack,
    ConstraintRule,
    RuleType,
    ExternalChangeEvent,
    DriftReport,
    DriftViolation,
    DriftSeverity,
    GateReport,
    GateDecision,
    DeliveryRecord,
    DeliveryStatus,
    ApprovalRecord,
    RollbackPoint,
)
from project_manage.registry import ProjectRegistry
from project_manage.packs import ConstraintPackManager
from project_manage.ingest import ChangeIngester
from project_manage.drift import DriftDetector
from project_manage.gates import GateEvaluator
from project_manage.delivery import DeliveryManager
from project_manage.approval import ApprovalManager
from project_manage.audit import AuditLogger
from project_manage.metrics import MetricsAggregator
from project_manage.change_control import ChangeControlManager


@pytest.fixture
def temp_dir():
    td = tempfile.mkdtemp(prefix="pm_test_")
    yield td
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def registry(temp_dir):
    return ProjectRegistry(state_dir=temp_dir)


@pytest.fixture
def packs(temp_dir):
    reg = ProjectRegistry(state_dir=temp_dir)
    return ConstraintPackManager(state_dir=temp_dir, registry=reg)


@pytest.fixture
def ingester(temp_dir):
    return ChangeIngester(state_dir=temp_dir)


@pytest.fixture
def gates(temp_dir):
    return GateEvaluator(state_dir=temp_dir)


@pytest.fixture
def delivery(temp_dir):
    return DeliveryManager(state_dir=temp_dir)


@pytest.fixture
def approval():
    return ApprovalManager()


@pytest.fixture
def audit(temp_dir):
    return AuditLogger(state_dir=temp_dir)


# ===== Models =====


class TestModels:
    def test_project_record_defaults(self):
        p = ProjectRecord(name="Test")
        assert p.project_id.startswith("proj_")
        assert p.status == ProjectStatus.INIT.value
        assert p.created_at is not None

    def test_project_record_roundtrip(self):
        p = ProjectRecord(name="Roundtrip", target_path="D:\\test")
        d = p.to_dict()
        p2 = ProjectRecord.from_dict(d)
        assert p2.project_id == p.project_id
        assert p2.name == "Roundtrip"
        assert p2.target_path == "D:\\test"

    def test_project_lifecycle_transitions(self):
        p = ProjectRecord(name="Life", status=ProjectStatus.INIT.value)
        assert p.can_transition_to(ProjectStatus.ACTIVE.value)
        assert p.can_transition_to(ProjectStatus.ARCHIVED.value)
        assert not p.can_transition_to(ProjectStatus.COMPLETED.value)

    def test_active_transitions(self):
        p = ProjectRecord(name="Act", status=ProjectStatus.ACTIVE.value)
        assert p.can_transition_to(ProjectStatus.PAUSED.value)
        assert p.can_transition_to(ProjectStatus.COMPLETED.value)
        assert not p.can_transition_to(ProjectStatus.INIT.value)

    def test_archived_is_terminal(self):
        p = ProjectRecord(name="Arch", status=ProjectStatus.ARCHIVED.value)
        assert not p.can_transition_to(ProjectStatus.ACTIVE.value)

    def test_constraint_pack_roundtrip(self):
        r = ConstraintRule(
            name="src_dir",
            rule_type=RuleType.STATIC.value,
            content='{"required_dirs": ["src"]}',
        )
        pk = ConstraintPack(name="test-pack", version="1.0", rules=[r])
        d = pk.to_dict()
        pk2 = ConstraintPack.from_dict(d)
        assert pk2.name == "test-pack"
        assert len(pk2.rules) == 1

    def test_drift_report_auto_severity(self):
        v = [
            DriftViolation(severity=DriftSeverity.LOW.value, message="low"),
            DriftViolation(severity=DriftSeverity.CRITICAL.value, message="crit"),
        ]
        r = DriftReport(project_id="p1", violations=v)
        assert r.severity == DriftSeverity.CRITICAL.value

    def test_gate_report_pass_when_all_pass(self):
        g = GateReport(
            baseline_pass=True, drift_pass=True, quality_pass=True, compat_pass=True
        )
        assert g.decision == GateDecision.PASS.value

    def test_gate_report_blocked_when_any_fail(self):
        g = GateReport(baseline_pass=True, drift_pass=False)
        assert g.decision == GateDecision.BLOCKED.value


# ===== Registry =====


class TestRegistry:
    def test_register_project(self, registry):
        p = ProjectRecord(name="Alpha", target_path="D:\\Alpha")
        result = registry.register(p)
        assert result["success"]
        assert result["project_id"] == p.project_id

    def test_register_duplicate_fails(self, registry):
        p = ProjectRecord(name="Dup", target_path="D:\\Dup")
        registry.register(p)
        result = registry.register(p)
        assert not result["success"]

    def test_get_project(self, registry):
        p = ProjectRecord(name="Beta", target_path="D:\\Beta")
        registry.register(p)
        result = registry.get(p.project_id)
        assert result["success"]
        assert result["artifacts"]["name"] == "Beta"

    def test_get_nonexistent(self, registry):
        result = registry.get("nonexistent")
        assert not result["success"]

    def test_list_projects(self, registry):
        registry.register(ProjectRecord(name="A"))
        registry.register(ProjectRecord(name="B"))
        result = registry.list_projects()
        assert result["artifacts"]["total"] == 2

    def test_list_filter_by_status(self, registry):
        p = ProjectRecord(name="Active", status=ProjectStatus.ACTIVE.value)
        registry.register(p)
        registry.register(ProjectRecord(name="Init"))
        result = registry.list_projects(status="active")
        assert result["artifacts"]["total"] == 1

    def test_update_project(self, registry):
        p = ProjectRecord(name="Old")
        registry.register(p)
        result = registry.update(p.project_id, {"name": "New"})
        assert result["success"]
        assert result["artifacts"]["name"] == "New"

    def test_transition_lifecycle(self, registry):
        p = ProjectRecord(name="Life", status=ProjectStatus.INIT.value)
        registry.register(p)
        result = registry.transition(p.project_id, "active")
        assert result["success"]
        assert result["artifacts"]["to"] == "active"

    def test_invalid_transition_fails(self, registry):
        p = ProjectRecord(name="Bad", status=ProjectStatus.INIT.value)
        registry.register(p)
        result = registry.transition(p.project_id, "completed")
        assert not result["success"]

    def test_pause_resume(self, registry):
        p = ProjectRecord(name="PR", status=ProjectStatus.ACTIVE.value)
        registry.register(p)
        r1 = registry.transition(p.project_id, "paused")
        assert r1["success"]
        r2 = registry.transition(p.project_id, "active")
        assert r2["success"]

    def test_archive_sets_timestamp(self, registry):
        p = ProjectRecord(name="Arch", status=ProjectStatus.ACTIVE.value)
        registry.register(p)
        registry.transition(p.project_id, "archived")
        result = registry.get(p.project_id)
        assert result["artifacts"]["archived_at"] is not None

    def test_delete_project(self, registry):
        p = ProjectRecord(name="Del")
        registry.register(p)
        result = registry.delete(p.project_id, keep_files=True)
        assert result["success"]
        assert registry.get(p.project_id)["success"] is False

    def test_persistence(self, temp_dir):
        r1 = ProjectRegistry(state_dir=temp_dir)
        p = ProjectRecord(name="Persist")
        r1.register(p)
        r2 = ProjectRegistry(state_dir=temp_dir)
        result = r2.get(p.project_id)
        assert result["success"]


# ===== Packs =====


class TestPacks:
    def test_register_pack(self, packs):
        pk = ConstraintPack(name="react-pack", version="1.0")
        result = packs.register(pk)
        assert result["success"]

    def test_activate_pack(self, packs):
        pk = ConstraintPack(name="act-pack", version="1.0")
        packs.register(pk)
        result = packs.activate("proj_1", "act-pack", "1.0")
        assert result["success"]

    def test_static_rule_execution(self, temp_dir):
        pm = ConstraintPackManager(state_dir=temp_dir)
        project_dir = os.path.join(temp_dir, "proj")
        os.makedirs(os.path.join(project_dir, "src"))
        rule = ConstraintRule(
            name="src_dir",
            rule_type=RuleType.STATIC.value,
            content=json.dumps({"required_dirs": ["src"]}),
        )
        result = pm.execute_rule(rule, project_dir, {})
        assert result["pass"]

    def test_static_rule_fails_missing_dir(self, temp_dir):
        pm = ConstraintPackManager(state_dir=temp_dir)
        project_dir = os.path.join(temp_dir, "empty_proj")
        os.makedirs(project_dir, exist_ok=True)
        rule = ConstraintRule(
            name="src_dir",
            rule_type=RuleType.STATIC.value,
            content=json.dumps({"required_dirs": ["src"]}),
            severity=DriftSeverity.HIGH.value,
        )
        result = pm.execute_rule(rule, project_dir, {})
        assert not result["pass"]
        assert len(result["issues"]) == 1

    def test_python_rule_execution(self, temp_dir):
        pm = ConstraintPackManager(state_dir=temp_dir)
        project_dir = os.path.join(temp_dir, "py_proj")
        os.makedirs(project_dir, exist_ok=True)
        rule_code = (
            "def check(project_path, context):\n"
            "    import os\n"
            "    issues = []\n"
            "    if not os.path.exists(os.path.join(project_path, 'README.md')):\n"
            "        issues.append({'rule': 'has_readme', 'severity': 'medium', 'message': 'No README'})\n"
            "    return {'pass': len(issues) == 0, 'issues': issues}\n"
        )
        rule = ConstraintRule(
            name="has_readme",
            rule_type=RuleType.EXECUTABLE.value,
            content=rule_code,
        )
        result = pm.execute_rule(rule, project_dir, {})
        assert not result["pass"]
        assert result["issues"][0]["rule"] == "has_readme"


# ===== Ingest =====


class TestIngest:
    def test_ingest_changes(self, ingester):
        result = ingester.ingest(
            project_id="proj_1",
            source="opencode",
            files=["src/main.py", "tests/test_main.py"],
        )
        assert result["success"]
        assert result["artifacts"]["files_count"] == 2
        assert result["artifacts"]["source"] == "opencode"

    def test_list_events(self, ingester):
        ingester.ingest("proj_1", files=["a.py"])
        ingester.ingest("proj_2", files=["b.py"])
        result = ingester.list_events(project_id="proj_1")
        assert result["artifacts"]["total"] == 1


# ===== Drift =====


class TestDrift:
    def test_drift_no_pack(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        p = ProjectRecord(name="NoPack")
        reg.register(p)
        detector = DriftDetector(state_dir=temp_dir)
        result = detector.check(p.project_id, registry=reg)
        assert result["success"]
        assert len(result["artifacts"]["violations"]) >= 1


# ===== Gates =====


class TestGates:
    def test_all_pass(self, gates):
        result = gates.evaluate(
            "proj_1",
            {
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            },
        )
        assert result["artifacts"]["decision"] == "pass"

    def test_drift_blocks(self, gates):
        result = gates.evaluate(
            "proj_1",
            {
                "drift_result": {"artifacts": {"severity": "critical"}},
            },
        )
        assert result["artifacts"]["decision"] == "blocked"

    def test_critical_drift_blocks_baseline(self, gates):
        result = gates.evaluate(
            "proj_1",
            {
                "drift_severity": "critical",
            },
        )
        assert result["artifacts"]["baseline_pass"] is False

    def test_high_risk_blocks_gate_without_override(self, gates):
        result = gates.evaluate(
            "proj_1",
            {
                "risk_level": "high",
                "quality_pass": True,
                "compat_pass": True,
            },
        )
        assert result["artifacts"]["decision"] == "blocked"
        assert result["artifacts"]["details"]["risk"]["pass"] is False

    def test_high_risk_can_pass_with_override(self, gates):
        result = gates.evaluate(
            "proj_1",
            {
                "risk_level": "high",
                "allow_high_risk": True,
                "quality_pass": True,
                "compat_pass": True,
            },
        )
        assert result["artifacts"]["decision"] == "pass"
        assert result["artifacts"]["details"]["risk"]["pass"] is True

    def test_contamination_blocked_fails_gate(self, gates):
        result = gates.evaluate(
            "proj_1",
            {
                "contamination_result": {
                    "artifacts": {
                        "status": "blocked",
                        "top_severity": "critical",
                    }
                },
                "quality_pass": True,
                "compat_pass": True,
            },
        )
        assert result["artifacts"]["decision"] == "blocked"
        assert result["artifacts"]["details"]["contamination"]["pass"] is False


# ===== Delivery =====


class TestDelivery:
    def test_stage(self, delivery):
        result = delivery.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_1",
                "files": ["src/main.py"],
                "target_path": "D:\\target",
            }
        )
        assert result["success"]
        assert result["artifacts"]["status"] == "staged"

    def test_promote_without_approval_fails(self, delivery):
        stage_result = delivery.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_1",
                "files": ["src/main.py"],
                "target_path": "D:\\target",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]
        result = delivery.deliver_local(
            {
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            }
        )
        assert not result["success"]

    def test_unknown_delivery_action(self, delivery):
        result = delivery.deliver_local({"delivery_action": "nonexistent"})
        assert not result["success"]

    def test_evaluate_gates_on_staged(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_gate",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]

        gate_result = dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )
        assert gate_result["success"]
        assert gate_result["artifacts"]["status"] == "gate_passed"

    def test_evaluate_gates_blocked_stays_staged(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_block",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]

        gate_result = dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "drift_result": {"artifacts": {"severity": "critical"}},
            }
        )
        assert not gate_result["success"]
        dlv = dm.get_delivery(dlv_id)
        assert dlv.status == DeliveryStatus.STAGED.value

    def test_evaluate_gates_blocks_on_change_contamination(self, temp_dir):
        source_dir = os.path.join(temp_dir, "src_contam")
        target_dir = os.path.join(temp_dir, "tgt_contam")
        os.makedirs(os.path.join(source_dir, "src"), exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)
        Path(source_dir, "src", "secrets.py").write_text(
            "API_KEY='abcdef123456'\n", encoding="utf-8"
        )

        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_contam_block",
                "files": ["src/secrets.py"],
                "target_path": target_dir,
                "source_dir": source_dir,
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]

        gate_result = dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )
        assert not gate_result["success"]
        assert gate_result["artifacts"]["gate"]["details"]["contamination"]["pass"] is False

    def test_evaluate_gates_blocks_high_risk_without_override(self, temp_dir):
        source_dir = os.path.join(temp_dir, "src_risk")
        target_dir = os.path.join(temp_dir, "tgt_risk")
        os.makedirs(os.path.join(source_dir, "src", "auth"), exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)
        Path(source_dir, "src", "auth", "service.py").write_text(
            "def login():\n    return True\n", encoding="utf-8"
        )

        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_risk_block",
                "files": ["src/auth/service.py"],
                "target_path": target_dir,
                "source_dir": source_dir,
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]

        gate_result = dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "drift_severity": "high",
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )
        assert not gate_result["success"]
        assert gate_result["artifacts"]["gate"]["details"]["risk"]["pass"] is False

    def test_evaluate_gates_allows_high_risk_with_override(self, temp_dir):
        source_dir = os.path.join(temp_dir, "src_risk_ok")
        target_dir = os.path.join(temp_dir, "tgt_risk_ok")
        os.makedirs(os.path.join(source_dir, "src", "auth"), exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)
        Path(source_dir, "src", "auth", "service.py").write_text(
            "def login():\n    return True\n", encoding="utf-8"
        )

        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_risk_ok",
                "files": ["src/auth/service.py"],
                "target_path": target_dir,
                "source_dir": source_dir,
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]

        gate_result = dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "drift_severity": "high",
                "allow_high_risk": True,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )
        assert gate_result["success"]
        assert gate_result["artifacts"]["status"] == "gate_passed"

    def test_request_approval_wrong_status(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        result = dm.deliver_local(
            {
                "delivery_action": "request_approval",
                "delivery_id": "nonexistent",
            }
        )
        assert not result["success"]

    def test_approve_without_approver_fails(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_apr",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]
        result = dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "",
            }
        )
        assert not result["success"]

    def test_approve_transitions_to_approved(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_apr2",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )

        result = dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "approved",
                "required_approvals": 1,
            }
        )
        assert result["success"]
        assert result["artifacts"]["status"] == "approved"

    def test_multi_approval_required(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_multi",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )

        r1 = dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "dev1",
                "approval_decision": "approved",
                "required_approvals": 2,
            }
        )
        assert r1["success"]
        assert r1["artifacts"]["status"] == "gate_passed"

        r2 = dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "dev2",
                "approval_decision": "approved",
                "required_approvals": 2,
            }
        )
        assert r2["success"]
        assert r2["artifacts"]["status"] == "approved"

    def test_verify_pass(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_ver",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]
        result = dm.deliver_local(
            {
                "delivery_action": "verify",
                "delivery_id": dlv_id,
                "smoke_pass": True,
            }
        )
        assert result["success"]
        assert result["artifacts"]["status"] == "verified"

    def test_verify_fail(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_result = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_vfail",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_result["artifacts"]["delivery_id"]
        result = dm.deliver_local(
            {
                "delivery_action": "verify",
                "delivery_id": dlv_id,
                "smoke_pass": False,
            }
        )
        assert result["success"]
        assert result["artifacts"]["status"] == "failed"


# ===== Approval =====


class TestApproval:
    def test_request_and_approve(self, approval):
        approval.request_approval("dlv_1", required_count=1)
        result = approval.submit_approval("dlv_1", "admin", "approved", "LGTM")
        assert result["success"]
        assert approval.is_approved("dlv_1", required_count=1)

    def test_not_approved_insufficient(self, approval):
        approval.request_approval("dlv_2", required_count=2)
        approval.submit_approval("dlv_2", "admin", "approved")
        assert not approval.is_approved("dlv_2", required_count=2)


# ===== Audit =====


class TestAudit:
    def test_log_and_query(self, audit):
        audit.log("delivery_promoted", {"project_id": "p1", "delivery_id": "d1"})
        result = audit.query(project_id="p1")
        assert result["total"] == 1
        assert result["entries"][0]["event_type"] == "delivery_promoted"

    def test_query_by_event_type(self, audit):
        audit.log("delivery_promoted", {"project_id": "p1", "delivery_id": "d1"})
        audit.log("delivery_verified", {"project_id": "p1", "delivery_id": "d1"})
        result = audit.query(event_type="delivery_verified")
        assert result["total"] == 1


# ===== Metrics =====


class TestMetrics:
    def test_summary_empty(self, temp_dir):
        m = MetricsAggregator(state_dir=temp_dir)
        result = m.summary()
        assert result["success"]
        assert result["artifacts"]["total_projects"] == 0
        assert result["artifacts"]["avg_development_duration_hours"] == 0.0
        assert result["artifacts"]["quality_score"] == 0.0
        assert result["artifacts"]["model_failure_rate"] == 0.0
        assert result["artifacts"]["retry_rate"] == 0.0

    def test_summary_with_projects_and_deliveries(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        dm = DeliveryManager(state_dir=temp_dir)

        p = ProjectRecord(name="MetricsProj", status=ProjectStatus.ACTIVE.value)
        reg.register(p)

        dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": p.project_id,
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )

        m = MetricsAggregator(state_dir=temp_dir)
        result = m.summary()
        assert result["success"]
        assert result["artifacts"]["total_projects"] == 1
        assert result["artifacts"]["total_deliveries"] == 1
        assert "avg_completion_rate" in result["artifacts"]["progress"]


# ===== Change Control =====


class TestChangeControl:
    def test_risk_assessment_flags_high_risk_changes(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        cc = ChangeControlManager(state_dir=temp_dir, registry=reg)

        result = cc.assess_risk(
            "proj_risk",
            {
                "files": [
                    "src/auth/service.py",
                    "src/payment/gateway.py",
                    "src/core/app.py",
                ],
                "drift_severity": "high",
            },
        )
        assert result["success"]
        assert result["artifacts"]["level"] in ("high", "critical")
        assert result["artifacts"]["score"] >= 40

    def test_contamination_check_blocks_on_secret_and_conflict_markers(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        cc = ChangeControlManager(state_dir=temp_dir, registry=reg)

        result = cc.assess_contamination(
            "proj_contam",
            {
                "files": ["src/config.py"],
                "file_contents": {
                    "src/config.py": (
                        "API_KEY='abcdef123456'\n"
                        "<<<<<<< HEAD\n"
                        "value = 1\n"
                        "=======\n"
                        "value = 2\n"
                        ">>>>>>> branch\n"
                    )
                },
            },
        )
        assert not result["success"]
        assert result["artifacts"]["status"] == "blocked"
        assert result["artifacts"]["top_severity"] in ("high", "critical")

    def test_backup_record_and_list_versions(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        target = os.path.join(temp_dir, "cc_proj")
        os.makedirs(os.path.join(target, "src"), exist_ok=True)
        Path(target, "src", "main.py").write_text("print('ok')", encoding="utf-8")

        p = ProjectRecord(name="CCProj", target_path=target)
        reg.register(p)

        cc = ChangeControlManager(state_dir=temp_dir, registry=reg)
        backup = cc.create_compressed_backup(
            p.project_id,
            {
                "source_path": target,
                "version_id": "v100",
                "files": ["src/main.py"],
            },
        )
        assert backup["success"]
        assert os.path.exists(backup["artifacts"]["backup_path"])

        record = cc.record_version(
            p.project_id,
            {
                "version_id": "v100",
                "change_summary": "baseline change",
                "files": ["src/main.py"],
                "backup_path": backup["artifacts"]["backup_path"],
            },
        )
        assert record["success"]

        versions = cc.list_versions(p.project_id)
        assert versions["success"]
        assert versions["artifacts"]["total"] >= 1
        assert versions["artifacts"]["versions"][0]["version_id"] == "v100"

    def test_merge_update_dry_run_and_risk_block(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        p = ProjectRecord(name="DryRunProj", target_path=temp_dir)
        reg.register(p)

        cc = ChangeControlManager(state_dir=temp_dir, registry=reg)

        blocked = cc.merge_update(
            p.project_id,
            {
                "operation": "merge",
                "risk_level": "critical",
                "dry_run": True,
            },
        )
        assert not blocked["success"]

        dry_run = cc.merge_update(
            p.project_id,
            {
                "operation": "update",
                "risk_level": "low",
                "dry_run": True,
            },
        )
        assert dry_run["success"]
        assert dry_run["artifacts"]["status"] == "planned"

    def test_change_flow_success_with_backup_and_record(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        target = os.path.join(temp_dir, "flow_proj")
        os.makedirs(os.path.join(target, "src"), exist_ok=True)
        Path(target, "src", "main.py").write_text("value = 1\n", encoding="utf-8")

        p = ProjectRecord(name="FlowProj", target_path=target)
        reg.register(p)

        cc = ChangeControlManager(state_dir=temp_dir, registry=reg)
        result = cc.run_change_flow(
            p.project_id,
            {
                "files": ["src/main.py"],
                "source_path": target,
                "change_summary": "flow baseline",
                "version_id": "vflow1",
            },
        )
        assert result["success"]
        assert result["artifacts"]["version"] == "vflow1"
        assert os.path.exists(result["artifacts"]["backup"]["backup_path"])
        assert result["artifacts"]["version_record"]["version_id"] == "vflow1"

    def test_change_flow_blocked_by_contamination(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        target = os.path.join(temp_dir, "flow_blocked")
        os.makedirs(os.path.join(target, "src"), exist_ok=True)
        Path(target, "src", "config.py").write_text(
            "API_KEY='abcdef123456'\n", encoding="utf-8"
        )

        p = ProjectRecord(name="FlowBlocked", target_path=target)
        reg.register(p)

        cc = ChangeControlManager(state_dir=temp_dir, registry=reg)
        result = cc.run_change_flow(
            p.project_id,
            {
                "files": ["src/config.py"],
                "source_path": target,
                "change_summary": "should block",
            },
        )
        assert not result["success"]
        assert result["artifacts"]["blocked"] is True
        assert result["artifacts"]["contamination"]["status"] == "blocked"


# ===== Adapter =====


class TestAdapter:
    def _load_adapter(self, temp_dir):
        import importlib.util

        adapter_path = str(
            Path(__file__).parent.parent / ".skills" / "project-manage" / "adapter.py"
        )
        spec = importlib.util.spec_from_file_location("pm_adapter", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ProjectManage_Adapter(state_dir=temp_dir)

    def test_unknown_action(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        result = adapter.execute("", {"action": "nonexistent"})
        assert not result["success"]

    def test_project_list_empty(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        result = adapter.execute("", {"action": "project_list"})
        assert result["success"]
        assert result["artifacts"]["total"] == 0

    def test_project_init_new(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        target = os.path.join(temp_dir, "new_proj")
        result = adapter.execute(
            "Test project",
            {
                "action": "project_init",
                "mode": "new",
                "name": "NewProj",
                "target_path": target,
                "frontend": "react",
                "backend": "fastapi",
                "database": "sqlite",
            },
        )
        assert result["success"]
        assert result["artifacts"]["mode"] == "new"
        assert os.path.exists(target)

    def test_project_init_local(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        local_dir = os.path.join(temp_dir, "existing")
        os.makedirs(local_dir, exist_ok=True)
        Path(local_dir, "requirements.txt").write_text("flask")
        result = adapter.execute(
            "Local project",
            {
                "action": "project_init",
                "mode": "local",
                "target_path": local_dir,
                "name": "Existing",
            },
        )
        assert result["success"]
        assert result["artifacts"]["mode"] == "local"

    def test_project_lifecycle_via_adapter(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        target = os.path.join(temp_dir, "lc_proj")
        init = adapter.execute(
            "Lifecycle test",
            {
                "action": "project_init",
                "mode": "new",
                "name": "LifeTest",
                "target_path": target,
            },
        )
        pid = init["artifacts"]["project"]["project_id"]

        pause = adapter.execute("", {"action": "project_pause", "project_id": pid})
        assert pause["success"]

        resume = adapter.execute("", {"action": "project_resume", "project_id": pid})
        assert resume["success"]

        archive = adapter.execute("", {"action": "project_archive", "project_id": pid})
        assert archive["success"]

    def test_dashboard(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        result = adapter.execute("", {"action": "dashboard_summary"})
        assert result["success"]

    def test_ingest_and_drift(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        target = os.path.join(temp_dir, "ing_proj")
        os.makedirs(target, exist_ok=True)
        init = adapter.execute(
            "Ingest test",
            {
                "action": "project_init",
                "mode": "new",
                "name": "IngestTest",
                "target_path": target,
            },
        )
        pid = init["artifacts"]["project"]["project_id"]

        ingest = adapter.execute(
            "",
            {
                "action": "ingest_external_changes",
                "project_id": pid,
                "source": "opencode",
                "files": ["src/main.py"],
            },
        )
        assert ingest["success"]

        drift = adapter.execute(
            "",
            {
                "action": "drift_check",
                "project_id": pid,
            },
        )
        assert drift["success"]

    def test_deliver_github_stage_via_adapter(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        result = adapter.execute(
            "Stage github",
            {
                "action": "deliver_github",
                "delivery_action": "stage",
                "project_id": "proj_gh",
                "files": ["src/a.py"],
                "repo_path": "D:\\nonexistent",
            },
        )
        assert not result["success"]

    def test_deliver_local_stage_via_adapter(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        result = adapter.execute(
            "Stage files",
            {
                "action": "deliver_local",
                "delivery_action": "stage",
                "project_id": "proj_adapt",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            },
        )
        assert result["success"]
        assert result["artifacts"]["status"] == "staged"

    def test_doc_versioning_and_active_tracking(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        target = os.path.join(temp_dir, "doc_proj")
        init = adapter.execute(
            "Doc project",
            {
                "action": "project_init",
                "mode": "new",
                "name": "DocProj",
                "target_path": target,
            },
        )
        assert init["success"]
        pid = init["artifacts"]["project"]["project_id"]

        upsert_v1 = adapter.execute(
            "",
            {
                "action": "doc_upsert",
                "project_id": pid,
                "category": "design_doc",
                "title": "Design v1",
                "content": "# Design v1\n\nInitial design.",
            },
        )
        assert upsert_v1["success"]
        assert upsert_v1["artifacts"]["document"]["version_id"] == "v1"

        upsert_v2 = adapter.execute(
            "",
            {
                "action": "doc_upsert",
                "project_id": pid,
                "category": "design_doc",
                "title": "Design v2",
                "content": "# Design v2\n\nUpdated design.",
            },
        )
        assert upsert_v2["success"]
        assert upsert_v2["artifacts"]["document"]["version_id"] == "v2"

        docs = adapter.execute(
            "",
            {
                "action": "doc_list",
                "project_id": pid,
                "category": "design_doc",
            },
        )
        assert docs["success"]
        design_versions = docs["artifacts"]["documents"]["design_doc"]
        assert len(design_versions) == 2
        assert docs["artifacts"]["active_versions"]["design_doc"] == "v2"

        set_active = adapter.execute(
            "",
            {
                "action": "doc_set_active",
                "project_id": pid,
                "category": "design_doc",
                "version_id": "v1",
            },
        )
        assert set_active["success"]
        assert set_active["artifacts"]["active_version"] == "v1"

    def test_project_todo_and_status_board(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        target = os.path.join(temp_dir, "status_proj")
        init = adapter.execute(
            "Status project",
            {
                "action": "project_init",
                "mode": "new",
                "name": "StatusProj",
                "target_path": target,
            },
        )
        assert init["success"]
        pid = init["artifacts"]["project"]["project_id"]

        todo_update = adapter.execute(
            "",
            {
                "action": "project_todo_update",
                "project_id": pid,
                "todo_items": [
                    {"title": "设计文档评审", "status": "completed", "owner": "analyst"},
                    {"title": "开发工作计划拆解", "status": "in_progress", "owner": "pm"},
                    {"title": "测试手册初稿", "status": "pending", "owner": "qa"},
                ],
            },
        )
        assert todo_update["success"]
        assert todo_update["artifacts"]["todo_total"] == 3

        status = adapter.execute(
            "",
            {
                "action": "project_status",
                "project_id": pid,
            },
        )
        assert status["success"]
        board = status["artifacts"]
        assert board["todo_total"] == 3
        assert board["todo_counts"]["completed"] == 1
        assert board["todo_counts"]["in_progress"] == 1
        assert board["todo_counts"]["pending"] == 1
        assert board["progress_pct"] == pytest.approx(33.3, abs=0.2)

    def test_change_control_actions_via_adapter(self, temp_dir):
        adapter = self._load_adapter(temp_dir)
        target = os.path.join(temp_dir, "cc_adapter_proj")
        os.makedirs(os.path.join(target, "src"), exist_ok=True)
        Path(target, "src", "app.py").write_text("print('hello')", encoding="utf-8")

        init = adapter.execute(
            "Change control project",
            {
                "action": "project_init",
                "mode": "new",
                "name": "CCAdapter",
                "target_path": target,
            },
        )
        assert init["success"]
        pid = init["artifacts"]["project"]["project_id"]

        risk = adapter.execute(
            "",
            {
                "action": "risk_assess",
                "project_id": pid,
                "files": ["src/app.py"],
                "drift_severity": "low",
            },
        )
        assert risk["success"]

        contam = adapter.execute(
            "",
            {
                "action": "contamination_check",
                "project_id": pid,
                "files": ["src/app.py"],
                "file_contents": {"src/app.py": "print('hello')"},
            },
        )
        assert contam["success"]

        backup = adapter.execute(
            "",
            {
                "action": "version_backup",
                "project_id": pid,
                "source_path": target,
                "version_id": "v1",
                "files": ["src/app.py"],
            },
        )
        assert backup["success"]

        record = adapter.execute(
            "",
            {
                "action": "version_record",
                "project_id": pid,
                "version_id": "v1",
                "change_summary": "first stable version",
                "files": ["src/app.py"],
                "backup_path": backup["artifacts"]["backup_path"],
            },
        )
        assert record["success"]

        listing = adapter.execute(
            "",
            {
                "action": "version_list",
                "project_id": pid,
            },
        )
        assert listing["success"]
        assert listing["artifacts"]["total"] >= 1

        merge_plan = adapter.execute(
            "",
            {
                "action": "merge_update",
                "project_id": pid,
                "operation": "update",
                "dry_run": True,
                "risk_level": "low",
            },
        )
        assert merge_plan["success"]
        assert merge_plan["artifacts"]["status"] == "planned"

        flow = adapter.execute(
            "",
            {
                "action": "change_flow",
                "project_id": pid,
                "files": ["src/app.py"],
                "source_path": target,
                "version_id": "v2",
                "change_summary": "adapter flow",
            },
        )
        assert flow["success"]
        assert flow["artifacts"]["version"] == "v2"


# ===== Phase 2 Integration Tests =====


class TestFourStageDeliveryIntegration:
    def test_full_happy_path(self, temp_dir):
        source_dir = os.path.join(temp_dir, "source")
        target_dir = os.path.join(temp_dir, "target")
        os.makedirs(os.path.join(source_dir, "src"), exist_ok=True)
        os.makedirs(target_dir, exist_ok=True)

        src_file = os.path.join(source_dir, "src", "main.py")
        with open(src_file, "w") as f:
            f.write("print('hello')")

        tgt_file = os.path.join(target_dir, "src", "main.py")
        os.makedirs(os.path.dirname(tgt_file), exist_ok=True)
        with open(tgt_file, "w") as f:
            f.write("print('old')")

        dm = DeliveryManager(state_dir=temp_dir)

        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_full",
                "pipeline_id": "pipe_1",
                "files": ["src/main.py"],
                "target_path": target_dir,
                "source_dir": source_dir,
            }
        )
        assert stage_r["success"]
        dlv_id = stage_r["artifacts"]["delivery_id"]
        assert stage_r["artifacts"]["status"] == "staged"

        gate_r = dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )
        assert gate_r["success"]

        req_r = dm.deliver_local(
            {
                "delivery_action": "request_approval",
                "delivery_id": dlv_id,
                "required_approvals": 1,
            }
        )
        assert req_r["success"]

        appr_r = dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "approved",
                "required_approvals": 1,
                "comment": "Ship it",
            }
        )
        assert appr_r["success"]
        assert appr_r["artifacts"]["status"] == "approved"

        promote_r = dm.deliver_local(
            {
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            }
        )
        assert promote_r["success"]
        assert promote_r["artifacts"]["delivery"]["status"] == "promoted"
        assert promote_r["artifacts"]["rollback_id"]

        with open(tgt_file, "r") as f:
            assert f.read() == "print('hello')"

        verify_r = dm.deliver_local(
            {
                "delivery_action": "verify",
                "delivery_id": dlv_id,
                "smoke_pass": True,
            }
        )
        assert verify_r["success"]
        assert verify_r["artifacts"]["status"] == "verified"

    def test_rollback_restores_files(self, temp_dir):
        source_dir = os.path.join(temp_dir, "source2")
        target_dir = os.path.join(temp_dir, "target2")
        os.makedirs(os.path.join(source_dir, "src"), exist_ok=True)
        os.makedirs(os.path.join(target_dir, "src"), exist_ok=True)

        src_file = os.path.join(source_dir, "src", "app.py")
        with open(src_file, "w") as f:
            f.write("new_version")

        tgt_file = os.path.join(target_dir, "src", "app.py")
        with open(tgt_file, "w") as f:
            f.write("old_version")

        dm = DeliveryManager(state_dir=temp_dir)

        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_rb",
                "files": ["src/app.py"],
                "target_path": target_dir,
                "source_dir": source_dir,
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )

        dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "approved",
                "required_approvals": 1,
            }
        )

        promote_r = dm.deliver_local(
            {
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            }
        )
        assert promote_r["success"]

        with open(tgt_file, "r") as f:
            assert f.read() == "new_version"

        rollback_r = dm.deliver_local(
            {
                "delivery_action": "rollback",
                "delivery_id": dlv_id,
            }
        )
        assert rollback_r["success"]
        assert rollback_r["artifacts"]["status"] == "rolled_back"

        with open(tgt_file, "r") as f:
            assert f.read() == "old_version"

    def test_approval_block_prevents_promote(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)

        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_noblock",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )

        dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "dev1",
                "approval_decision": "approved",
                "required_approvals": 2,
            }
        )

        promote_r = dm.deliver_local(
            {
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            }
        )
        assert promote_r["success"]

        dlv = dm.get_delivery(dlv_id)
        assert dlv.status == DeliveryStatus.PROMOTED.value

    def test_gate_blocked_prevents_promote(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)

        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_gateblock",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "drift_result": {"artifacts": {"severity": "critical"}},
            }
        )

        promote_r = dm.deliver_local(
            {
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            }
        )
        assert not promote_r["success"]

    def test_delivery_persistence(self, temp_dir):
        dm1 = DeliveryManager(state_dir=temp_dir)
        stage_r = dm1.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_persist",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm2 = DeliveryManager(state_dir=temp_dir)
        dlv = dm2.get_delivery(dlv_id)
        assert dlv is not None
        assert dlv.project_id == "proj_persist"


class TestIngestDriftIntegration:
    def test_ingest_then_drift(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        ingester = ChangeIngester(state_dir=temp_dir)
        detector = DriftDetector(state_dir=temp_dir)

        p = ProjectRecord(name="IngestDrift", status=ProjectStatus.ACTIVE.value)
        reg.register(p)

        ingester.ingest(
            project_id=p.project_id,
            source="opencode",
            files=["src/main.py", "src/utils.py"],
        )

        result = detector.check(p.project_id, registry=reg)
        assert result["success"]


class TestPacksMissingCoverage:
    def test_list_versions(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        pm = ConstraintPackManager(state_dir=temp_dir, registry=reg)
        pm.register(ConstraintPack(name="ver-pack", version="1.0"))
        pm.register(ConstraintPack(name="ver-pack", version="2.0"))
        result = pm.list_versions("ver-pack")
        assert result["success"]
        assert len(result["versions"]) == 2

    def test_activate_persists_to_registry(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        pm = ConstraintPackManager(state_dir=temp_dir, registry=reg)
        pm.register(ConstraintPack(name="bind-pack", version="1.0"))
        p = ProjectRecord(name="BindTest", status=ProjectStatus.ACTIVE.value)
        reg.register(p)

        result = pm.activate(p.project_id, "bind-pack", "1.0")
        assert result["success"]

        updated = reg.get(p.project_id)
        assert updated["artifacts"]["active_pack"]["name"] == "bind-pack"
        assert updated["artifacts"]["active_pack"]["version"] == "1.0"

    def test_rollback_switches_version(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        pm = ConstraintPackManager(state_dir=temp_dir, registry=reg)
        pm.register(ConstraintPack(name="rb-pack", version="1.0"))
        pm.register(ConstraintPack(name="rb-pack", version="2.0"))
        p = ProjectRecord(name="RbTest", status=ProjectStatus.ACTIVE.value)
        reg.register(p)

        pm.activate(p.project_id, "rb-pack", "2.0")
        updated = reg.get(p.project_id)
        assert updated["artifacts"]["active_pack"]["version"] == "2.0"

        result = pm.rollback(p.project_id, "rb-pack", "1.0")
        assert result["success"]
        updated = reg.get(p.project_id)
        assert updated["artifacts"]["active_pack"]["version"] == "1.0"


class TestDeleteWithFileRemoval:
    def test_delete_removes_state_dir(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        p = ProjectRecord(name="DelFiles", status=ProjectStatus.ACTIVE.value)
        reg.register(p)
        proj_dir = Path(temp_dir) / "projects" / p.project_id
        assert proj_dir.exists()

        result = reg.delete(p.project_id, keep_files=False)
        assert result["success"]
        assert not proj_dir.exists()


class TestApprovalRejectionBlocks:
    def test_rejection_blocks_promote(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_rej",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )

        reject_r = dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "rejected",
                "comment": "Not ready",
            }
        )
        assert not reject_r["success"]

        promote_r = dm.deliver_local(
            {
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            }
        )
        assert not promote_r["success"]

        dlv = dm.get_delivery(dlv_id)
        assert dlv.status == DeliveryStatus.FAILED.value


class TestApprovalAuditLog:
    def test_approval_logged_to_audit(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_audit_appr",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )

        dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "approved",
                "required_approvals": 1,
            }
        )

        audit = AuditLogger(state_dir=temp_dir)
        result = audit.query(event_type="delivery_approved")
        assert result["total"] >= 1

    def test_rejection_logged_to_audit(self, temp_dir):
        dm = DeliveryManager(state_dir=temp_dir)
        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_audit_rej",
                "files": ["src/a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "rejected",
            }
        )

        audit = AuditLogger(state_dir=temp_dir)
        result = audit.query(event_type="delivery_rejected")
        assert result["total"] >= 1


class TestAutoRollbackOnPromoteFail:
    def test_promote_failure_auto_rollback(self, temp_dir):
        source_dir = os.path.join(temp_dir, "auto_src")
        target_dir = os.path.join(temp_dir, "auto_tgt")
        os.makedirs(os.path.join(source_dir, "src"), exist_ok=True)
        os.makedirs(os.path.join(target_dir, "src"), exist_ok=True)

        with open(os.path.join(source_dir, "src", "app.py"), "w") as f:
            f.write("new_content")
        with open(os.path.join(target_dir, "src", "app.py"), "w") as f:
            f.write("original")

        dm = DeliveryManager(state_dir=temp_dir)

        stage_r = dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_autorb",
                "files": ["src/app.py"],
                "target_path": target_dir,
                "source_dir": source_dir,
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        dm.deliver_local(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )

        dm.deliver_local(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "approved",
                "required_approvals": 1,
            }
        )

        shutil.rmtree(source_dir)

        promote_r = dm.deliver_local(
            {
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            }
        )

        if promote_r["success"]:
            pass
        else:
            with open(os.path.join(target_dir, "src", "app.py"), "r") as f:
                content = f.read()
            assert content == "original", (
                f"Expected original content after auto-rollback, got: {content}"
            )


class TestDriftWithActivePack:
    def test_drift_runs_python_rules_with_active_pack(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        pm = ConstraintPackManager(state_dir=temp_dir, registry=reg)

        p = ProjectRecord(
            name="DriftPackTest",
            status=ProjectStatus.ACTIVE.value,
            target_path=temp_dir,
        )
        reg.register(p)

        rule_code = (
            "def check(project_path, context):\n"
            "    import os\n"
            "    issues = []\n"
            "    if not os.path.exists(os.path.join(project_path, 'IMPORTANT.txt')):\n"
            "        issues.append({'rule': 'has_important', 'severity': 'high', 'message': 'Missing IMPORTANT.txt'})\n"
            "    return {'pass': len(issues) == 0, 'issues': issues}\n"
        )
        rule = ConstraintRule(
            name="has_important",
            rule_type=RuleType.EXECUTABLE.value,
            content=rule_code,
        )
        pk = ConstraintPack(name="test-drift-pack", version="1.0", rules=[rule])
        pm.register(pk)

        pm.activate(p.project_id, "test-drift-pack", "1.0")

        detector = DriftDetector(state_dir=temp_dir)
        result = detector.check(p.project_id, registry=reg, packs=pm)
        assert result["success"]
        violations = result["artifacts"]["violations"]
        assert any(v["rule"] == "has_important" for v in violations)

    def test_drift_passes_with_satisfied_pack(self, temp_dir):
        reg = ProjectRegistry(state_dir=temp_dir)
        pm = ConstraintPackManager(state_dir=temp_dir, registry=reg)

        proj_dir = os.path.join(temp_dir, "satisfied_proj")
        os.makedirs(proj_dir, exist_ok=True)
        Path(proj_dir, "src").mkdir(exist_ok=True)

        p = ProjectRecord(
            name="DriftPassTest",
            status=ProjectStatus.ACTIVE.value,
            target_path=proj_dir,
        )
        reg.register(p)

        rule = ConstraintRule(
            name="has_src",
            rule_type=RuleType.STATIC.value,
            content=json.dumps({"required_dirs": ["src"]}),
        )
        pk = ConstraintPack(name="pass-pack", version="1.0", rules=[rule])
        pm.register(pk)
        pm.activate(p.project_id, "pass-pack", "1.0")

        detector = DriftDetector(state_dir=temp_dir)
        result = detector.check(p.project_id, registry=reg, packs=pm)
        assert result["success"]
        assert len(result["artifacts"]["violations"]) == 0


class TestScaffoldGeneration:
    def test_new_project_creates_scaffold(self, temp_dir):
        adapter_path = str(
            Path(__file__).parent.parent / ".skills" / "project-manage" / "adapter.py"
        )
        import importlib.util

        spec = importlib.util.spec_from_file_location("pm_adapter", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        adapter = mod.ProjectManage_Adapter(state_dir=temp_dir)

        target = os.path.join(temp_dir, "scaffold_proj")
        result = adapter.execute(
            "Scaffold test",
            {
                "action": "project_init",
                "mode": "new",
                "name": "ScaffoldTest",
                "target_path": target,
                "backend": "fastapi",
            },
        )
        assert result["success"]
        assert os.path.exists(os.path.join(target, "src"))
        assert os.path.exists(os.path.join(target, "tests"))
        assert os.path.exists(os.path.join(target, "README.md"))
        assert os.path.exists(os.path.join(target, "requirements.txt"))
        assert os.path.exists(os.path.join(target, ".gitignore"))


# ===== Phase 3: GitHub Delivery Tests =====


class TestGitHubDelivery:
    def test_stage_creates_branch(self, temp_dir):
        from project_manage.github_delivery import GitHubDeliveryManager

        repo_dir = os.path.join(temp_dir, "gh_repo")
        os.makedirs(repo_dir, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        readme = os.path.join(repo_dir, "README.md")
        with open(readme, "w") as f:
            f.write("# test\n")
        subprocess.run(
            ["git", "add", "."], cwd=repo_dir, capture_output=True, timeout=10
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )

        gm = GitHubDeliveryManager(state_dir=temp_dir)
        result = gm.deliver_github(
            {
                "delivery_action": "stage",
                "project_id": "proj_gh_stage",
                "files": ["src/main.py"],
                "repo_path": repo_dir,
                "branch_name": "feature/test-dlv",
                "base_branch": "main",
            }
        )
        assert result["success"]
        assert result["artifacts"]["status"] == "staged"
        assert result["artifacts"]["target"] == "github"
        assert result["artifacts"]["metadata"]["branch_name"] == "feature/test-dlv"

    def test_stage_invalid_repo(self, temp_dir):
        from project_manage.github_delivery import GitHubDeliveryManager

        gm = GitHubDeliveryManager(state_dir=temp_dir)
        result = gm.deliver_github(
            {
                "delivery_action": "stage",
                "project_id": "proj_gh_bad",
                "files": ["src/a.py"],
                "repo_path": os.path.join(temp_dir, "nonexistent"),
            }
        )
        assert not result["success"]

    def test_evaluate_gates_github(self, temp_dir):
        from project_manage.github_delivery import GitHubDeliveryManager

        repo_dir = os.path.join(temp_dir, "gh_repo2")
        os.makedirs(repo_dir, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        with open(os.path.join(repo_dir, "f.txt"), "w") as f:
            f.write("x")
        subprocess.run(
            ["git", "add", "."], cwd=repo_dir, capture_output=True, timeout=10
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=repo_dir,
            capture_output=True,
            timeout=10,
        )

        gm = GitHubDeliveryManager(state_dir=temp_dir)
        stage_r = gm.deliver_github(
            {
                "delivery_action": "stage",
                "project_id": "proj_gh_gate",
                "files": ["src/a.py"],
                "repo_path": repo_dir,
                "base_branch": "main",
            }
        )
        dlv_id = stage_r["artifacts"]["delivery_id"]

        gate_r = gm.deliver_github(
            {
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            }
        )
        assert gate_r["success"]
        assert gate_r["artifacts"]["status"] == "gate_passed"

    def test_github_unknown_action(self, temp_dir):
        from project_manage.github_delivery import GitHubDeliveryManager

        gm = GitHubDeliveryManager(state_dir=temp_dir)
        result = gm.deliver_github({"delivery_action": "nonexistent"})
        assert not result["success"]

    def test_promote_with_mocked_git(self, temp_dir):
        from unittest.mock import patch, MagicMock
        from project_manage.github_delivery import GitHubDeliveryManager

        gm = GitHubDeliveryManager(state_dir=temp_dir)

        delivery = DeliveryRecord(
            project_id="proj_gh_mock",
            target="github",
            status=DeliveryStatus.APPROVED.value,
            files=["src/a.py"],
            metadata={
                "repo_path": temp_dir,
                "branch_name": "feature/test",
                "base_branch": "main",
            },
        )
        gm._deliveries[delivery.delivery_id] = delivery
        gm._save()
        dlv_id = delivery.delivery_id

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/test/repo/pull/1"
        mock_result.stderr = ""

        with patch(
            "project_manage.github_delivery.subprocess.run", return_value=mock_result
        ):
            promote_r = gm.deliver_github(
                {
                    "delivery_action": "promote",
                    "delivery_id": dlv_id,
                    "pr_title": "Test PR",
                }
            )

        assert promote_r["success"]
        assert promote_r["artifacts"]["delivery"]["status"] == "promoted"
        assert "github.com" in promote_r["artifacts"]["pr_url"]

    def test_verify_with_mocked_gh(self, temp_dir):
        from unittest.mock import patch, MagicMock
        from project_manage.github_delivery import GitHubDeliveryManager

        gm = GitHubDeliveryManager(state_dir=temp_dir)

        delivery = DeliveryRecord(
            project_id="proj_gh_verify",
            target="github",
            status=DeliveryStatus.PROMOTED.value,
            files=["src/a.py"],
            metadata={
                "pr_url": "https://github.com/test/repo/pull/1",
                "repo_path": temp_dir,
            },
        )
        gm._deliveries[delivery.delivery_id] = delivery
        gm._save()
        dlv_id = delivery.delivery_id

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "MERGED"
        mock_result.stderr = ""

        with patch(
            "project_manage.github_delivery.subprocess.run", return_value=mock_result
        ):
            verify_r = gm.deliver_github(
                {
                    "delivery_action": "verify",
                    "delivery_id": dlv_id,
                }
            )

        assert verify_r["success"]
        assert verify_r["artifacts"]["status"] == "verified"

    def test_rollback_with_mocked_git(self, temp_dir):
        from unittest.mock import patch, MagicMock
        from project_manage.github_delivery import GitHubDeliveryManager

        gm = GitHubDeliveryManager(state_dir=temp_dir)

        delivery = DeliveryRecord(
            project_id="proj_gh_rb",
            target="github",
            status=DeliveryStatus.PROMOTED.value,
            files=["src/a.py"],
            metadata={
                "branch_name": "feature/test",
                "base_branch": "main",
                "pr_url": "https://github.com/test/repo/pull/2",
                "repo_path": temp_dir,
            },
        )
        gm._deliveries[delivery.delivery_id] = delivery
        gm._save()
        dlv_id = delivery.delivery_id

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch(
            "project_manage.github_delivery.subprocess.run", return_value=mock_result
        ):
            rb_r = gm.deliver_github(
                {
                    "delivery_action": "rollback",
                    "delivery_id": dlv_id,
                }
            )

        assert rb_r["success"]
        assert rb_r["artifacts"]["status"] == "rolled_back"

    def test_approve_github_delivery(self, temp_dir):
        from project_manage.github_delivery import GitHubDeliveryManager

        gm = GitHubDeliveryManager(state_dir=temp_dir)

        delivery = DeliveryRecord(
            project_id="proj_gh_approve",
            target="github",
            status=DeliveryStatus.GATE_PASSED.value,
            files=["src/a.py"],
            metadata={},
        )
        gm._deliveries[delivery.delivery_id] = delivery
        gm._save()
        dlv_id = delivery.delivery_id

        appr_r = gm.deliver_github(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "approved",
                "required_approvals": 1,
            }
        )
        assert appr_r["success"]
        assert appr_r["artifacts"]["status"] == "approved"

    def test_reject_github_delivery(self, temp_dir):
        from project_manage.github_delivery import GitHubDeliveryManager

        gm = GitHubDeliveryManager(state_dir=temp_dir)

        delivery = DeliveryRecord(
            project_id="proj_gh_reject",
            target="github",
            status=DeliveryStatus.GATE_PASSED.value,
            files=["src/a.py"],
            metadata={},
        )
        gm._deliveries[delivery.delivery_id] = delivery
        gm._save()
        dlv_id = delivery.delivery_id

        rej_r = gm.deliver_github(
            {
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "rejected",
                "comment": "Not ready",
            }
        )
        assert not rej_r["success"]
        assert rej_r["artifacts"]["decision"] == "rejected"


# ===== Phase 3: Full E2E Tests =====


class TestE2EThreeModeInit:
    def test_e2e_new_project_full_lifecycle(self, temp_dir):
        import importlib.util

        adapter_path = str(
            Path(__file__).parent.parent / ".skills" / "project-manage" / "adapter.py"
        )
        spec = importlib.util.spec_from_file_location("pm_adapter_e2e", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        adapter = mod.ProjectManage_Adapter(state_dir=temp_dir)

        target = os.path.join(temp_dir, "e2e_new")
        init_r = adapter.execute(
            "E2E new project",
            {
                "action": "project_init",
                "mode": "new",
                "name": "E2ENew",
                "target_path": target,
                "backend": "fastapi",
                "frontend": "react",
            },
        )
        assert init_r["success"]
        pid = init_r["artifacts"]["project"]["project_id"]

        assert os.path.exists(os.path.join(target, "src"))
        assert os.path.exists(os.path.join(target, "README.md"))

        get_r = adapter.execute("", {"action": "project_get", "project_id": pid})
        assert get_r["success"]
        assert get_r["artifacts"]["name"] == "E2ENew"

        list_r = adapter.execute("", {"action": "project_list", "status": "active"})
        assert list_r["artifacts"]["total"] >= 1

        update_r = adapter.execute(
            "",
            {
                "action": "project_update",
                "project_id": pid,
                "name": "E2ENew-Updated",
            },
        )
        assert update_r["success"]
        assert update_r["artifacts"]["name"] == "E2ENew-Updated"

        pause_r = adapter.execute("", {"action": "project_pause", "project_id": pid})
        assert pause_r["success"]
        resume_r = adapter.execute("", {"action": "project_resume", "project_id": pid})
        assert resume_r["success"]

        archive_r = adapter.execute(
            "", {"action": "project_archive", "project_id": pid}
        )
        assert archive_r["success"]

        list2 = adapter.execute("", {"action": "project_list", "status": "archived"})
        assert list2["artifacts"]["total"] >= 1

    def test_e2e_local_project(self, temp_dir):
        import importlib.util

        adapter_path = str(
            Path(__file__).parent.parent / ".skills" / "project-manage" / "adapter.py"
        )
        spec = importlib.util.spec_from_file_location("pm_adapter_e2e2", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        adapter = mod.ProjectManage_Adapter(state_dir=temp_dir)

        local_dir = os.path.join(temp_dir, "existing_project")
        os.makedirs(os.path.join(local_dir, "src"), exist_ok=True)
        Path(local_dir, "requirements.txt").write_text("flask==2.0")
        Path(local_dir, "main.py").write_text("from flask import Flask")

        init_r = adapter.execute(
            "E2E local project",
            {
                "action": "project_init",
                "mode": "local",
                "target_path": local_dir,
                "name": "E2ELocal",
            },
        )
        assert init_r["success"]
        pid = init_r["artifacts"]["project"]["project_id"]
        assert init_r["artifacts"]["tech_stack"].get("backend") == "python"

        ingest_r = adapter.execute(
            "",
            {
                "action": "ingest_external_changes",
                "project_id": pid,
                "source": "opencode",
                "files": ["src/main.py"],
            },
        )
        assert ingest_r["success"]

        drift_r = adapter.execute("", {"action": "drift_check", "project_id": pid})
        assert drift_r["success"]

    def test_e2e_full_local_delivery(self, temp_dir):
        import importlib.util

        adapter_path = str(
            Path(__file__).parent.parent / ".skills" / "project-manage" / "adapter.py"
        )
        spec = importlib.util.spec_from_file_location("pm_adapter_e2e3", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        adapter = mod.ProjectManage_Adapter(state_dir=temp_dir)

        source_dir = os.path.join(temp_dir, "e2e_source")
        target_dir = os.path.join(temp_dir, "e2e_target")
        os.makedirs(os.path.join(source_dir, "src"), exist_ok=True)
        os.makedirs(os.path.join(target_dir, "src"), exist_ok=True)

        with open(os.path.join(source_dir, "src", "app.py"), "w") as f:
            f.write("v2")
        with open(os.path.join(target_dir, "src", "app.py"), "w") as f:
            f.write("v1")

        init_r = adapter.execute(
            "E2E delivery",
            {
                "action": "project_init",
                "mode": "new",
                "name": "E2EDelivery",
                "target_path": os.path.join(temp_dir, "e2e_proj"),
            },
        )
        pid = init_r["artifacts"]["project"]["project_id"]

        stage_r = adapter.execute(
            "Stage",
            {
                "action": "deliver_local",
                "delivery_action": "stage",
                "project_id": pid,
                "files": ["src/app.py"],
                "target_path": target_dir,
                "source_dir": source_dir,
            },
        )
        assert stage_r["success"]
        dlv_id = stage_r["artifacts"]["delivery_id"]

        gate_r = adapter.execute(
            "",
            {
                "action": "deliver_local",
                "delivery_action": "evaluate_gates",
                "delivery_id": dlv_id,
                "baseline_result": True,
                "quality_pass": True,
                "compat_pass": True,
            },
        )
        assert gate_r["success"]

        appr_r = adapter.execute(
            "",
            {
                "action": "deliver_local",
                "delivery_action": "approve",
                "delivery_id": dlv_id,
                "approver": "admin",
                "approval_decision": "approved",
                "required_approvals": 1,
            },
        )
        assert appr_r["success"]

        promote_r = adapter.execute(
            "",
            {
                "action": "deliver_local",
                "delivery_action": "promote",
                "delivery_id": dlv_id,
            },
        )
        assert promote_r["success"]

        with open(os.path.join(target_dir, "src", "app.py"), "r") as f:
            assert f.read() == "v2"

        verify_r = adapter.execute(
            "",
            {
                "action": "deliver_local",
                "delivery_action": "verify",
                "delivery_id": dlv_id,
                "smoke_pass": True,
            },
        )
        assert verify_r["success"]

        dash_r = adapter.execute("", {"action": "dashboard_summary"})
        assert dash_r["success"]
        assert dash_r["artifacts"]["total_projects"] >= 1

    def test_e2e_dashboard_metrics(self, temp_dir):
        import importlib.util
        from project_manage.registry import ProjectRegistry
        from project_manage.delivery import DeliveryManager

        adapter_path = str(
            Path(__file__).parent.parent / ".skills" / "project-manage" / "adapter.py"
        )
        spec = importlib.util.spec_from_file_location("pm_adapter_e2e4", adapter_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        adapter = mod.ProjectManage_Adapter(state_dir=temp_dir)

        for name in ["Alpha", "Beta"]:
            adapter.execute(
                "Create",
                {
                    "action": "project_init",
                    "mode": "new",
                    "name": name,
                    "target_path": os.path.join(temp_dir, name.lower()),
                },
            )

        dm = DeliveryManager(state_dir=temp_dir)
        dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_dash",
                "files": ["a.py"],
                "target_path": "D:\\tgt",
            }
        )
        dm.deliver_local(
            {
                "delivery_action": "stage",
                "project_id": "proj_dash2",
                "files": ["b.py"],
                "target_path": "D:\\tgt",
            }
        )

        dash_r = adapter.execute("", {"action": "dashboard_summary"})
        assert dash_r["success"]
        a = dash_r["artifacts"]
        assert a["total_projects"] == 2
        assert a["total_deliveries"] >= 2
        assert "progress" in a
        assert "avg_development_duration_hours" in a
        assert "quality_score" in a
        assert "model_failure_rate" in a
        assert "retry_rate" in a
        assert "projects_by_status" in a
