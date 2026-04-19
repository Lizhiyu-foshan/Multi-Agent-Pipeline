"""
Unit tests for Layer 1 (multi-PDCA loop) and Layer 3 (cross-cycle context).
Tests the orchestrator's new capabilities:
- _generate_follow_up_tasks (from backlog + failed tasks)
- _resolve_auto_continue_decision (smart decision logic)
- _count_remaining_backlog
- _handle_check with task generation
- _handle_verify with backlog-based loopback
- PipelineRun backlog and pdca_max_cycles fields
"""

import sys
import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipeline.pipeline_orchestrator import PipelineOrchestrator
from pipeline.models import PipelineRun, PipelinePhase, PipelineState, Task


class MockSkill:
    def execute(self, description, context):
        return {"success": True, "artifacts": {"output": "mock"}}


@pytest.fixture
def orch(temp_dir):
    o = PipelineOrchestrator(
        state_dir=temp_dir,
        skills={
            "bmad-evo": MockSkill(),
            "superpowers": MockSkill(),
            "spec-kit": MockSkill(),
        },
        watchdog_config=False,
    )
    o._auto_continue = False
    o._default_decision_timeout_seconds = 1800.0
    o.scheduler.registry.register("developer", "developer", ["code", "test"])
    o.scheduler.registry.register("reviewer", "reviewer", ["review", "quality"])
    return o


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def _create_pipeline(orch, backlog=None):
    p = PipelineRun(
        description="test pipeline",
        max_duration_hours=5.0,
    )
    if backlog:
        p.backlog = backlog
    orch.pipelines[p.id] = p
    orch._save_pipelines()
    return p


def _submit_task(orch, pipeline_id, name, status="pending", role_id="developer"):
    result = orch.scheduler.submit_task({
        "pipeline_id": pipeline_id,
        "role_id": role_id,
        "name": name,
        "description": f"Task: {name}",
        "priority": "P2",
        "depends_on": [],
    })
    tid = result.get("task_id", "")
    if status == "completed":
        orch.scheduler.complete_task(tid, True, {"success": True})
    elif status == "failed":
        orch.scheduler.complete_task(tid, False, {"success": False, "error": f"Error in {name}"})
    return tid


class TestGenerateFollowUpTasks:
    def test_generates_rework_for_failed_tasks(self, orch):
        p = _create_pipeline(orch)
        tid = _submit_task(orch, p.id, "auth_test", "failed")
        issues = [{"task_id": tid, "name": "auth_test", "error": "AssertionError"}]
        root_causes = [{"task": "auth_test", "root_cause": "Logic error", "severity": "critical"}]

        count = orch._generate_follow_up_tasks(p, [], issues, root_causes)

        assert count == 1
        tasks = orch.scheduler.task_queue.get_by_pipeline(p.id)
        rework_names = [t.name for t in tasks if t.name.startswith("rework_")]
        assert "rework_auth_test" in rework_names

    def test_generates_tasks_from_backlog(self, orch):
        backlog = [
            {"name": "feature_x", "description": "Implement X", "role": "developer", "priority": "P2", "status": "pending"},
            {"name": "feature_y", "description": "Implement Y", "role": "developer", "priority": "P2", "status": "pending"},
        ]
        p = _create_pipeline(orch, backlog=backlog)

        count = orch._generate_follow_up_tasks(p, [], [], [])

        assert count == 2
        tasks = orch.scheduler.task_queue.get_by_pipeline(p.id)
        names = {t.name for t in tasks}
        assert "feature_x" in names
        assert "feature_y" in names

    def test_skips_already_existing_rework(self, orch):
        p = _create_pipeline(orch)
        _submit_task(orch, p.id, "rework_auth_test", "pending")
        issues = [{"task_id": "x", "name": "auth_test", "error": "fail"}]

        count = orch._generate_follow_up_tasks(p, [], issues, [])

        assert count == 0

    def test_skips_backlog_items_already_as_tasks(self, orch):
        backlog = [{"name": "existing_task", "description": "Already a task", "status": "pending"}]
        p = _create_pipeline(orch, backlog=backlog)
        _submit_task(orch, p.id, "existing_task", "pending")

        count = orch._generate_follow_up_tasks(p, [], [], [])

        assert count == 0

    def test_skips_non_pending_backlog_items(self, orch):
        backlog = [
            {"name": "done_item", "description": "Already done", "status": "completed"},
            {"name": "in_progress_item", "description": "In progress", "status": "in_progress"},
            {"name": "pending_item", "description": "Still pending", "status": "pending"},
        ]
        p = _create_pipeline(orch, backlog=backlog)

        count = orch._generate_follow_up_tasks(p, [], [], [])

        assert count == 1

    def test_updates_backlog_status_to_in_progress(self, orch):
        backlog = [{"name": "item_a", "description": "Task A", "status": "pending"}]
        p = _create_pipeline(orch, backlog=backlog)

        orch._generate_follow_up_tasks(p, [], [], [])

        assert p.backlog[0]["status"] == "in_progress"


class TestCountRemainingBacklog:
    def test_counts_pending_not_yet_tasks(self, orch):
        backlog = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
            {"name": "c", "status": "completed"},
        ]
        p = _create_pipeline(orch, backlog=backlog)

        assert orch._count_remaining_backlog(p) == 2

    def test_excludes_names_already_in_task_queue(self, orch):
        backlog = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "pending"},
        ]
        p = _create_pipeline(orch, backlog=backlog)
        _submit_task(orch, p.id, "a", "pending")

        assert orch._count_remaining_backlog(p) == 1

    def test_empty_backlog(self, orch):
        p = _create_pipeline(orch)
        assert orch._count_remaining_backlog(p) == 0


class TestResolveAutoContinueDecision:
    def test_chooses_B_when_new_tasks(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.DECIDE.value
        result = {
            "action": "human_decision",
            "options": ["A", "B"],
            "check_result": {
                "success_rate": 100,
                "new_tasks": 3,
                "has_failures": False,
                "remaining_backlog": 0,
            },
        }
        decision = orch._resolve_auto_continue_decision(p, result)
        assert decision == "B"

    def test_chooses_B_when_has_failures(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.DECIDE.value
        result = {
            "action": "human_decision",
            "options": ["A", "B"],
            "check_result": {
                "success_rate": 60,
                "new_tasks": 0,
                "has_failures": True,
                "remaining_backlog": 0,
            },
        }
        decision = orch._resolve_auto_continue_decision(p, result)
        assert decision == "B"

    def test_chooses_B_when_remaining_backlog(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.DECIDE.value
        result = {
            "action": "human_decision",
            "options": ["A", "B"],
            "check_result": {
                "success_rate": 100,
                "new_tasks": 0,
                "has_failures": False,
                "remaining_backlog": 5,
            },
        }
        decision = orch._resolve_auto_continue_decision(p, result)
        assert decision == "B"

    def test_chooses_C_when_success_and_no_work(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.DECIDE.value
        result = {
            "action": "human_decision",
            "options": ["A", "B", "C"],
            "check_result": {
                "success_rate": 100,
                "new_tasks": 0,
                "has_failures": False,
                "remaining_backlog": 0,
            },
        }
        decision = orch._resolve_auto_continue_decision(p, result)
        assert decision == "C"

    def test_chooses_A_for_non_decide_phase(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.CONFIRM_PLAN.value
        result = {"action": "human_decision", "options": ["A"]}
        decision = orch._resolve_auto_continue_decision(p, result)
        assert decision == "A"

    def test_chooses_A_default(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.PAUSED.value
        result = {"action": "human_decision", "options": ["A", "B"]}
        decision = orch._resolve_auto_continue_decision(p, result)
        assert decision == "A"

    def test_chooses_A_when_B_not_in_options(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.DECIDE.value
        result = {
            "action": "human_decision",
            "options": ["A"],
            "check_result": {
                "success_rate": 60,
                "new_tasks": 2,
                "has_failures": True,
                "remaining_backlog": 0,
            },
        }
        decision = orch._resolve_auto_continue_decision(p, result)
        assert decision == "A"


class TestHandleCheckTaskGeneration:
    def test_check_generates_backlog_tasks(self, orch):
        backlog = [
            {"name": "task_alpha", "description": "Do alpha", "role": "developer", "priority": "P2", "status": "pending"},
            {"name": "task_beta", "description": "Do beta", "role": "developer", "priority": "P2", "status": "pending"},
        ]
        p = _create_pipeline(orch, backlog=backlog)
        _submit_task(orch, p.id, "initial_task", "completed")
        p.phase = PipelinePhase.EXECUTE.value

        result = orch._handle_check(p, {})

        assert result["action"] == "human_decision"
        check_result = result.get("check_result", {})
        assert check_result.get("new_tasks") == 2
        assert "B" in result["options"]

    def test_check_no_new_tasks_when_all_done_no_backlog(self, orch):
        p = _create_pipeline(orch)
        _submit_task(orch, p.id, "task_1", "completed")
        _submit_task(orch, p.id, "task_2", "completed")
        p.phase = PipelinePhase.EXECUTE.value

        result = orch._handle_check(p, {})

        check_result = result.get("check_result", {})
        assert check_result.get("new_tasks") == 0
        assert "C" in result["options"]

    def test_check_includes_remaining_backlog_count(self, orch):
        backlog = [
            {"name": "x", "description": "X", "status": "pending"},
            {"name": "y", "description": "Y", "status": "pending"},
            {"name": "z", "description": "Z", "status": "pending"},
        ]
        p = _create_pipeline(orch, backlog=backlog)
        _submit_task(orch, p.id, "done", "completed")
        p.phase = PipelinePhase.EXECUTE.value

        result = orch._handle_check(p, {})

        check_result = result.get("check_result", {})
        assert check_result.get("new_tasks") == 3
        assert check_result.get("remaining_backlog") == 0


class TestHandleVerifyBacklogLoopback:
    def test_loops_back_when_backlog_remaining(self, orch):
        backlog = [{"name": "pending_item", "description": "Still to do", "status": "pending"}]
        p = _create_pipeline(orch, backlog=backlog)
        p.pdca_cycle = 1
        p.phase = PipelinePhase.VERIFY.value

        result = orch._handle_verify(p, {"success": True})

        assert result.get("action") == "execute_next_task"
        assert p.phase == PipelinePhase.EXECUTE.value

    def test_completes_when_no_backlog_no_failures(self, orch):
        p = _create_pipeline(orch)
        _submit_task(orch, p.id, "done_task", "completed")
        p.pdca_cycle = 1
        p.phase = PipelinePhase.VERIFY.value

        result = orch._handle_verify(p, {"success": True})

        assert result.get("action") == "completed"
        assert p.state == PipelineState.COMPLETED.value

    def test_loops_back_when_has_failed_tasks(self, orch):
        p = _create_pipeline(orch)
        _submit_task(orch, p.id, "failed_task", "failed")
        p.pdca_cycle = 1
        p.phase = PipelinePhase.VERIFY.value

        result = orch._handle_verify(p, {"success": False})

        assert result.get("action") == "execute_next_task"
        assert p.phase == PipelinePhase.EXECUTE.value

    def test_fails_after_max_cycles(self, orch):
        p = _create_pipeline(orch)
        p.pdca_cycle = 20
        p.pdca_max_cycles = 20
        p.phase = PipelinePhase.VERIFY.value

        result = orch._handle_verify(p, {"success": False})

        assert p.state == PipelineState.FAILED.value


class TestPipelineRunBacklogFields:
    def test_backlog_defaults_to_empty_list(self):
        p = PipelineRun()
        assert p.backlog == []

    def test_pdca_max_cycles_defaults_to_20(self):
        p = PipelineRun()
        assert p.pdca_max_cycles == 20

    def test_max_duration_defaults_to_5h(self):
        p = PipelineRun()
        assert p.max_duration_hours == 5.0

    def test_to_dict_includes_backlog(self):
        p = PipelineRun(backlog=[{"name": "x", "status": "pending"}])
        d = p.to_dict()
        assert d["backlog"] == [{"name": "x", "status": "pending"}]
        assert "pdca_max_cycles" in d

    def test_from_dict_restores_backlog(self):
        data = {
            "id": "test",
            "backlog": [{"name": "y", "status": "pending"}],
            "pdca_max_cycles": 10,
        }
        p = PipelineRun.from_dict(data)
        assert len(p.backlog) == 1
        assert p.backlog[0]["name"] == "y"
        assert p.pdca_max_cycles == 10


class TestAnalyzeBacklogCarryForward:
    def test_analyze_merges_pending_backlog_into_tasks(self, orch):
        p = _create_pipeline(
            orch,
            backlog=[
                {
                    "name": "carry_feature",
                    "description": "Carry from previous pipeline",
                    "role": "developer",
                    "priority": "P2",
                    "status": "pending",
                }
            ],
        )

        result = orch._handle_analyze(
            p,
            {
                "success": True,
                "artifacts": {
                    "roles": [{"type": "developer", "name": "dev", "capabilities": ["code"]}],
                    "tasks": [],
                },
            },
        )

        assert result.get("action") == "call_skill"
        assert result.get("action_type") == "plan"
        analysis = p.artifacts.get("analysis", {})
        task_names = {t.get("name") for t in analysis.get("tasks", [])}
        assert "carry_feature" in task_names


class TestEvolveFlowAndRoleRouting:
    def test_handle_evolve_transitions_to_verify_without_extra_call_skill(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.EVOLVE.value
        p.state = PipelineState.RUNNING

        result = orch._handle_evolve(p, {"success": True, "artifacts": {}})

        assert result.get("action") in ("completed", "execute_next_task")
        assert p.phase in (PipelinePhase.COMPLETED.value, PipelinePhase.EXECUTE.value)

    def test_role_to_skill_maps_project_manage_roles(self, orch):
        assert orch._role_to_skill("project-manager") == "project-manage"
        assert orch._role_to_skill("manager") == "project-manage"
        assert orch._role_to_skill("governance") == "project-manage"

    def test_get_pipeline_state_handles_string_state(self, orch):
        p = _create_pipeline(orch)
        p.state = "running"
        orch._save_pipelines()
        assert orch._get_pipeline_state(p.id) == "running"

    def test_role_to_skill_strict_raises_on_unknown_role(self, orch):
        with pytest.raises(ValueError):
            orch._role_to_skill("alien-role", strict=True)

    def test_handle_execute_fails_on_unknown_role_type(self, orch):
        p = _create_pipeline(orch)
        p.phase = PipelinePhase.EXECUTE.value
        p.state = PipelineState.RUNNING

        orch.scheduler.registry.register("mystery", "Mystery", ["unknown"])
        role = orch.scheduler.registry.get("mystery")
        role.type = "alien-role"

        tid = _submit_task(orch, p.id, "unknown_role_task", "pending", role_id="mystery")
        p.tasks = [tid]
        orch._save_pipelines()

        result = orch._handle_execute(p, {})
        assert result.get("action") == "failed"
        assert "Unknown role type" in result.get("reason", "")
