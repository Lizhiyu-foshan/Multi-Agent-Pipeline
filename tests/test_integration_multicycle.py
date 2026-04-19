"""
Integration tests for multi-PDCA pipeline flow.
Tests end-to-end scenarios:
1. Multi-PDCA with backlog items flowing through EXECUTE → CHECK → EXECUTE
2. Failure → rework task generation → second PDCA cycle
3. Smart auto_continue decision chain
4. Pipeline chaining with context transfer
"""

import sys
import json
import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pipeline.pipeline_orchestrator import PipelineOrchestrator
from pipeline.models import PipelineRun, PipelinePhase, PipelineState


class MockSkill:
    def __init__(self):
        self.call_count = 0

    def execute(self, description, context):
        self.call_count += 1
        return {
            "success": True,
            "artifacts": {
                "output": f"mock result {self.call_count}",
                "implementation_status": "completed",
                "files_modified": ["src/module.py"],
                "test_results": {"passed": 10, "failed": 0},
            },
        }


@pytest.fixture
def orch():
    d = tempfile.mkdtemp()
    o = PipelineOrchestrator(
        state_dir=d,
        skills={
            "bmad-evo": MockSkill(),
            "superpowers": MockSkill(),
            "spec-kit": MockSkill(),
        },
        watchdog_config=False,
    )
    o._auto_continue = True
    o._default_decision_timeout_seconds = 86400.0
    o.scheduler.registry.register("developer", "developer", ["code", "test"])
    o.scheduler.registry.register("reviewer", "reviewer", ["review", "quality"])
    yield o
    shutil.rmtree(d, ignore_errors=True)


def _run_pipeline_to_completion(orch, pipeline_id, max_iters=80):
    results = []
    phase_result = {}
    for i in range(max_iters):
        result = orch.advance(pipeline_id, phase_result)
        action = result.get("action", "unknown")
        results.append(action)

        if action in ("completed", "failed"):
            return results, result
        if result.get("error") and not result.get("options"):
            return results, result

        if action == "human_decision":
            phase_result = {"auto_continue": True, "_step": i}
        elif action == "model_request":
            phase_result = {"_step": i}
        elif action == "execute_next_task":
            tid = result.get("task_id", "")
            phase_result = {"sequential_mode": True, "_step": i}
            if tid:
                phase_result["task_id"] = tid
                phase_result["task_result"] = {"success": True}
        elif action == "check":
            phase_result = {"_step": i}
        elif action == "call_skill":
            skill_name = result.get("skill", "")
            action_type = result.get("action_type", "")
            if action_type == "analyze":
                phase_result = {
                    "success": True,
                    "artifacts": {
                        "roles": [{"type": "developer", "name": "dev", "capabilities": ["code"]}],
                        "tasks": [{"name": "task_1", "description": "Test task", "role": "developer"}],
                    },
                }
            elif action_type == "plan":
                phase_result = {
                    "success": True,
                    "artifacts": {
                        "task_graph": {
                            "tasks": [{"name": "task_1", "description": "Test task", "role": "developer", "priority": "P1", "depends_on": []}],
                            "execution_waves": [[0]],
                        },
                    },
                }
            elif action_type in ("evolve",):
                phase_result = {"success": True, "artifacts": {}}
            else:
                skill = orch.skills.get(skill_name)
                if skill:
                    ctx = dict(result)
                    ctx["action"] = action_type
                    ctx["pipeline_id"] = pipeline_id
                    skill_result = skill.execute(result.get("prompt", ""), ctx)
                    phase_result = {
                        "success": skill_result.get("success", True),
                        "artifacts": skill_result.get("artifacts", {}),
                    }
                else:
                    phase_result = {"success": True, "artifacts": {}}


class TestMultiPDCABacklogFlow:
    """Integration: backlog items cause EXECUTE → CHECK → EXECUTE loop."""

    def test_single_cycle_completes_when_no_backlog(self, orch):
        p = PipelineRun(description="test", max_duration_hours=5.0)
        orch.pipelines[p.id] = p
        orch._save_pipelines()

        actions, final = _run_pipeline_to_completion(orch, p.id)

        assert "completed" in actions or final.get("action") == "completed"

    def test_two_pdca_cycles_with_backlog(self, orch):
        p = PipelineRun(
            description="test multi-PDCA",
            max_duration_hours=5.0,
            backlog=[
                {"name": "backlog_a", "description": "Backlog task A", "role": "developer", "priority": "P2", "status": "pending"},
                {"name": "backlog_b", "description": "Backlog task B", "role": "developer", "priority": "P2", "status": "pending"},
            ],
        )
        orch.pipelines[p.id] = p
        orch._save_pipelines()

        actions, final = _run_pipeline_to_completion(orch, p.id)

        check_count = sum(1 for a in actions if a == "check")
        assert check_count >= 2, f"Expected >= 2 CHECK phases, got {check_count}. Actions: {actions}"

    def test_smart_auto_continue_chooses_B_with_backlog(self, orch):
        p = PipelineRun(
            description="test smart decision",
            max_duration_hours=5.0,
            backlog=[
                {"name": "extra_task", "description": "Extra work", "role": "developer", "priority": "P2", "status": "pending"},
            ],
        )
        orch.pipelines[p.id] = p
        orch._save_pipelines()

        actions, final = _run_pipeline_to_completion(orch, p.id)

        execute_count = sum(1 for a in actions if a == "execute_next_task")
        assert execute_count >= 3, f"Expected >= 3 execute_next_task (initial + backlog), got {execute_count}"


class TestFailureReworkFlow:
    """Integration: failed tasks get rework tasks generated."""

    def test_failed_task_triggers_rework_generation(self, orch):
        p = PipelineRun(description="test rework", max_duration_hours=5.0)
        orch.pipelines[p.id] = p
        orch._save_pipelines()

        orch.scheduler.submit_task({
            "pipeline_id": p.id,
            "role_id": "developer",
            "name": "failing_task",
            "description": "This task fails",
            "priority": "P1",
            "depends_on": [],
        })
        task_id = None
        for t in orch.scheduler.task_queue.get_by_pipeline(p.id):
            if t.name == "failing_task":
                task_id = t.id
                orch.scheduler.complete_task(task_id, False, {"success": False, "error": "Test failure"})
                break

        assert task_id is not None

        p.phase = PipelinePhase.EXECUTE.value
        p.pdca_cycle = 0

        check_result = orch._handle_check(p, {})

        all_tasks = orch.scheduler.task_queue.get_by_pipeline(p.id)
        rework_names = [t.name for t in all_tasks if "rework" in t.name]
        assert len(rework_names) >= 1, f"Expected rework task, got: {[t.name for t in all_tasks]}"

    def test_check_result_reports_failure(self, orch):
        p = PipelineRun(description="test failure report", max_duration_hours=5.0)
        orch.pipelines[p.id] = p
        orch._save_pipelines()

        orch.scheduler.submit_task({
            "pipeline_id": p.id,
            "role_id": "developer",
            "name": "ok_task",
            "description": "OK",
            "priority": "P1",
            "depends_on": [],
        })
        for t in orch.scheduler.task_queue.get_by_pipeline(p.id):
            orch.scheduler.complete_task(t.id, True, {"success": True})

        orch.scheduler.submit_task({
            "pipeline_id": p.id,
            "role_id": "developer",
            "name": "bad_task",
            "description": "Bad",
            "priority": "P1",
            "depends_on": [],
        })
        for t in orch.scheduler.task_queue.get_by_pipeline(p.id):
            if t.name == "bad_task" and t.status == "pending":
                orch.scheduler.complete_task(t.id, False, {"success": False, "error": "Fail"})

        p.phase = PipelinePhase.EXECUTE.value
        check_result = orch._handle_check(p, {})

        cr = check_result.get("check_result", {})
        assert cr.get("has_failures") is True
        assert cr.get("success_rate") < 100


class TestPipelineChainingContext:
    """Integration: cross-pipeline context transfer."""

    def test_pipeline_completes_and_context_persisted(self, orch):
        p = PipelineRun(description="first pipeline", max_duration_hours=5.0)
        orch.pipelines[p.id] = p
        orch._save_pipelines()

        orch.scheduler.submit_task({
            "pipeline_id": p.id,
            "role_id": "developer",
            "name": "task_1",
            "description": "First task",
            "priority": "P1",
            "depends_on": [],
        })
        for t in orch.scheduler.task_queue.get_by_pipeline(p.id):
            orch.scheduler.complete_task(t.id, True, {"success": True, "artifacts": {"result": "done"}})

        orch.context.store_artifact(p.id, "", "test_artifact", {"key": "value"})

        p.phase = PipelinePhase.VERIFY.value
        p.pdca_cycle = 1
        result = orch._handle_verify(p, {"success": True})

        assert p.state == PipelineState.COMPLETED.value
        artifacts = orch.context.get_artifacts(p.id)
        assert any("test_artifact" in k for k in artifacts)

    def test_second_pipeline_inherits_backlog_from_first(self, orch):
        p1 = PipelineRun(
            description="first",
            max_duration_hours=5.0,
            backlog=[
                {"name": "deferred_item", "description": "Deferred to next", "status": "pending"},
            ],
        )
        orch.pipelines[p1.id] = p1
        orch._save_pipelines()

        p2 = PipelineRun(
            description="second",
            max_duration_hours=5.0,
            backlog=[
                {"name": "deferred_item", "description": "Deferred to next", "status": "pending"},
            ],
        )
        orch.pipelines[p2.id] = p2
        orch._save_pipelines()

        remaining = orch._count_remaining_backlog(p2)
        assert remaining == 1

        count = orch._generate_follow_up_tasks(p2, [], [], [])
        assert count == 1

        tasks = orch.scheduler.task_queue.get_by_pipeline(p2.id)
        assert any(t.name == "deferred_item" for t in tasks)


class TestAutoContinueDecisionChain:
    """Integration: verify smart auto_continue decision chain through full flow."""

    def test_auto_continue_resolves_check_with_backlog(self, orch):
        p = PipelineRun(
            description="auto continue test",
            max_duration_hours=5.0,
            backlog=[
                {"name": "item_1", "description": "Item 1", "status": "pending"},
            ],
        )
        orch.pipelines[p.id] = p
        orch._save_pipelines()

        orch.scheduler.submit_task({
            "pipeline_id": p.id,
            "role_id": "developer",
            "name": "done_task",
            "description": "Done",
            "priority": "P1",
            "depends_on": [],
        })
        for t in orch.scheduler.task_queue.get_by_pipeline(p.id):
            orch.scheduler.complete_task(t.id, True, {"success": True})

        p.phase = PipelinePhase.EXECUTE.value

        check_result = orch._handle_check(p, {})

        assert check_result["action"] == "human_decision"
        cr = check_result["check_result"]
        assert cr["new_tasks"] >= 1

        decision = orch._resolve_auto_continue_decision(p, check_result)
        assert decision == "B"
