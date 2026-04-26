"""
Tests for Layer 3 (Cross-Cycle Context) in self_bootstrap.py
and Layer 1 auto_continue sequential_mode gap.

Covers:
- SelfBootstrapDriver._build_pipeline_description()
- SelfBootstrapDriver._has_remaining_work()
- SelfBootstrapDriver._collect_cross_pipeline_context()
- SelfBootstrapDriver cross_pipeline_context tracking
- Pipeline chaining outer while loop (simulated)
- Backlog inheritance from cross_pipeline_context
- LocalModelBridge response routing and output format
- auto_continue recursive call preserving sequential_mode
"""

import json
import os
import sys
import tempfile
import shutil
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pipeline.pipeline_orchestrator import PipelineOrchestrator
from pipeline.models import PipelineRun, PipelinePhase, PipelineState

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
            },
        }


def _make_orchestrator(state_dir):
    o = PipelineOrchestrator(
        state_dir=state_dir,
        skills={
            "bmad-evo": MockSkill(),
            "superpowers": MockSkill(),
            "spec-kit": MockSkill(),
        },
        watchdog_config=False,
    )
    o._auto_continue = False
    o._default_decision_timeout_seconds = 86400.0
    o.scheduler.registry.register("developer", "developer", ["code", "test"])
    o.scheduler.registry.register("reviewer", "reviewer", ["review", "quality"])
    return o


# ============================================================
# Layer 3: _build_pipeline_description
# ============================================================


class TestBuildPipelineDescription:
    def test_first_pipeline_returns_original_description(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="Build feature X")
        assert driver._build_pipeline_description() == "Build feature X"

    def test_subsequent_pipeline_includes_counts(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="Build feature X")
        driver.total_pipelines = 2
        driver.cross_pipeline_context = {
            "completed_work": [{"pipeline_id": "p1"}, {"pipeline_id": "p2"}],
            "backlog": [{"name": "a", "status": "pending"}, {"name": "b", "status": "pending"}],
            "issues": ["issue1"],
            "analysis_history": [],
        }
        desc = driver._build_pipeline_description()
        assert "Continuation #3" in desc
        assert "completed: 2" in desc
        assert "remaining: 2" in desc
        assert "open issues: 1" in desc
        assert "Build feature X" in desc

    def test_empty_context_shows_zeros(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="Test project")
        driver.total_pipelines = 1
        driver.cross_pipeline_context = {
            "completed_work": [],
            "backlog": [],
            "issues": [],
            "analysis_history": [],
        }
        desc = driver._build_pipeline_description()
        assert "completed: 0" in desc
        assert "remaining: 0" in desc
        assert "open issues: 0" in desc


# ============================================================
# Layer 3: _has_remaining_work
# ============================================================


class TestHasRemainingWork:
    def test_returns_false_when_no_backlog(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context = {"backlog": []}
        assert driver._has_remaining_work() is False

    def test_returns_false_when_all_backlog_completed(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context = {
            "backlog": [
                {"name": "a", "status": "completed"},
                {"name": "b", "status": "in_progress"},
            ]
        }
        assert driver._has_remaining_work() is False

    def test_returns_true_when_pending_backlog_exists(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context = {
            "backlog": [
                {"name": "a", "status": "completed"},
                {"name": "b", "status": "pending"},
            ]
        }
        assert driver._has_remaining_work() is True

    def test_returns_true_when_multiple_pending(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context = {
            "backlog": [
                {"name": "a", "status": "pending"},
                {"name": "b", "status": "pending"},
            ]
        }
        assert driver._has_remaining_work() is True


# ============================================================
# Layer 3: _collect_cross_pipeline_context
# ============================================================


class TestCollectCrossPipelineContext:
    def test_noop_when_no_pipeline_id(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.pipeline_id = None
        driver._collect_cross_pipeline_context()
        assert driver.cross_pipeline_context["completed_work"] == []
        assert driver.cross_pipeline_context["issues"] == []

    def test_collects_artifacts_from_completed_pipeline(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            p = PipelineRun(description="test pipeline", max_duration_hours=5.0)
            p.phase = PipelinePhase.EXECUTE.value
            p.state = PipelineState.RUNNING
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            orch.context.store_artifact(p.id, "", "analysis", {"key": "val"})
            orch.context.store_artifact(p.id, "", "plan", {"task_graph": {}})

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch
            driver.pipeline_id = p.id

            driver._collect_cross_pipeline_context()

            cw = driver.cross_pipeline_context["completed_work"]
            assert len(cw) == 1
            assert cw[0]["pipeline_id"] == p.id
            assert any("analysis" in k for k in cw[0]["artifacts_keys"])
            assert any("plan" in k for k in cw[0]["artifacts_keys"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_collects_issues_from_check_entries(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.EXECUTE.value
            p.state = PipelineState.RUNNING
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            orch.context.add_entry(
                p.id, "", "check",
                "check",
                json.dumps({"issues": ["test failure in module_a", "import error in module_b"]}),
            )
            orch.context.add_entry(
                p.id, "", "orchestrator",
                "execute",
                "regular entry without issues",
            )

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch
            driver.pipeline_id = p.id

            driver._collect_cross_pipeline_context()

            issues = driver.cross_pipeline_context["issues"]
            assert len(issues) == 2
            assert "test failure in module_a" in issues
            assert "import error in module_b" in issues
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_handles_exception_gracefully(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            orch.context = MagicMock()
            orch.context.get_artifacts.side_effect = RuntimeError("boom")

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch
            driver.pipeline_id = "fake_id"

            driver._collect_cross_pipeline_context()

            assert driver.cross_pipeline_context["completed_work"] == []
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_collects_pending_backlog_from_pipeline(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.EXECUTE.value
            p.state = PipelineState.RUNNING
            p.backlog = [
                {"name": "pending_item", "status": "pending", "role": "developer"},
                {"name": "done_item", "status": "completed", "role": "developer"},
            ]
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch
            driver.pipeline_id = p.id
            driver._collect_cross_pipeline_context()

            names = {item.get("name") for item in driver.cross_pipeline_context["backlog"]}
            assert "pending_item" in names
            assert "done_item" not in names
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_collects_in_progress_backlog_as_pending_for_next_pipeline(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.EXECUTE.value
            p.state = PipelineState.RUNNING
            p.backlog = [
                {"name": "work_in_progress", "status": "in_progress", "role": "developer"},
            ]
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch
            driver.pipeline_id = p.id
            driver._collect_cross_pipeline_context()

            backlog = driver.cross_pipeline_context["backlog"]
            assert len(backlog) == 1
            assert backlog[0]["name"] == "work_in_progress"
            assert backlog[0]["status"] == "pending"
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_collects_unresolved_tasks_into_backlog(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.EXECUTE.value
            p.state = PipelineState.RUNNING
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            tid = orch.scheduler.submit_task({
                "pipeline_id": p.id,
                "role_id": "developer",
                "name": "carry_task",
                "description": "Carry me",
                "priority": "P2",
                "depends_on": [],
            }).get("task_id")
            orch.scheduler.task_queue.update_status(tid, "failed", {"error": "boom"})

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch
            driver.pipeline_id = p.id
            driver._collect_cross_pipeline_context()

            names = {item.get("name") for item in driver.cross_pipeline_context["backlog"]}
            assert "carry_task" in names
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ============================================================
# Layer 3: cross_pipeline_context tracking
# ============================================================


class TestCrossPipelineContextTracking:
    def test_initial_context_has_empty_lists(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        ctx = driver.cross_pipeline_context
        assert ctx["issues"] == []
        assert ctx["backlog"] == []
        assert ctx["completed_work"] == []
        assert ctx["analysis_history"] == []

    def test_context_accumulates_across_collections(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)

            p1 = PipelineRun(description="pipeline 1", max_duration_hours=5.0)
            p1.phase = PipelinePhase.EXECUTE.value
            p1.state = PipelineState.RUNNING
            orch.pipelines[p1.id] = p1
            orch._save_pipelines()
            orch.context.store_artifact(p1.id, "", "analysis", {"key": "val1"})

            p2 = PipelineRun(description="pipeline 2", max_duration_hours=5.0)
            p2.phase = PipelinePhase.EXECUTE.value
            p2.state = PipelineState.RUNNING
            orch.pipelines[p2.id] = p2
            orch._save_pipelines()
            orch.context.store_artifact(p2.id, "", "analysis", {"key": "val2"})

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch

            driver.pipeline_id = p1.id
            driver._collect_cross_pipeline_context()

            driver.pipeline_id = p2.id
            driver._collect_cross_pipeline_context()

            assert len(driver.cross_pipeline_context["completed_work"]) == 2
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ============================================================
# Layer 3: Pipeline chaining outer loop (simulated)
# ============================================================


class TestPipelineChainingLoop:
    def test_has_remaining_work_controls_loop_continuation(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")

        driver.cross_pipeline_context["backlog"] = [
            {"name": "item_a", "status": "pending"},
        ]
        assert driver._has_remaining_work() is True

        driver._collect_cross_pipeline_context()

        assert driver._has_remaining_work() is True

        driver.cross_pipeline_context["backlog"] = []
        assert driver._has_remaining_work() is False

    def test_description_changes_between_iterations(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="Original task")

        desc1 = driver._build_pipeline_description()
        assert desc1 == "Original task"

        driver.total_pipelines = 1
        driver.cross_pipeline_context["completed_work"] = [{"pipeline_id": "p1"}]
        driver.cross_pipeline_context["backlog"] = [{"name": "x", "status": "pending"}]

        desc2 = driver._build_pipeline_description()
        assert desc2 != desc1
        assert "Continuation #2" in desc2

        driver.total_pipelines = 2
        driver.cross_pipeline_context["completed_work"].append({"pipeline_id": "p2"})
        desc3 = driver._build_pipeline_description()
        assert "Continuation #3" in desc3

    def test_second_pipeline_analyze_plan_receives_carried_tasks(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            driver = SelfBootstrapDriver(description="continue work")
            orch = _make_orchestrator(d)
            driver.orchestrator = orch

            p1 = PipelineRun(description="first", max_duration_hours=5.0)
            p1.phase = PipelinePhase.EXECUTE.value
            p1.state = PipelineState.RUNNING
            p1.backlog = [
                {"name": "pending_from_backlog", "description": "pending", "status": "pending", "role": "developer"}
            ]
            orch.pipelines[p1.id] = p1
            orch._save_pipelines()

            tid = orch.scheduler.submit_task(
                {
                    "pipeline_id": p1.id,
                    "role_id": "developer",
                    "name": "failed_from_taskqueue",
                    "description": "failed task",
                    "priority": "P1",
                    "depends_on": [],
                }
            ).get("task_id")
            orch.scheduler.task_queue.update_status(tid, "failed", {"error": "x"})

            driver.pipeline_id = p1.id
            driver._collect_cross_pipeline_context()
            assert driver._has_remaining_work() is True

            p2, _ = orch.create_pipeline("continuation")
            if driver.cross_pipeline_context.get("backlog"):
                p2.backlog = driver.cross_pipeline_context["backlog"]
                orch._save_pipelines()

            analyze_result = orch._handle_analyze(
                p2,
                {
                    "success": True,
                    "artifacts": {
                        "roles": [{"type": "developer", "name": "dev", "capabilities": ["code"]}],
                        "tasks": [],
                    },
                },
            )

            assert analyze_result.get("action") == "call_skill"
            assert analyze_result.get("action_type") == "plan"
            prompt = analyze_result.get("prompt", "")
            assert "pending_from_backlog" in prompt
            assert "failed_from_taskqueue" in prompt
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ============================================================
# Layer 3: Backlog inheritance from cross_pipeline_context
# ============================================================


class TestBacklogInheritance:
    def test_backlog_copied_to_new_pipeline(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            driver = SelfBootstrapDriver(description="test")
            driver.state_dir = d

            orch = _make_orchestrator(d)
            driver.orchestrator = orch

            driver.cross_pipeline_context["backlog"] = [
                {"name": "deferred_1", "description": "Deferred work", "status": "pending"},
                {"name": "deferred_2", "description": "More work", "status": "pending"},
            ]

            pipeline, _ = orch.create_pipeline("continuation")
            driver.pipeline_id = pipeline.id

            if driver.cross_pipeline_context.get("backlog"):
                pipeline.backlog = driver.cross_pipeline_context["backlog"]
                orch._save_pipelines()

            loaded = orch.pipelines[pipeline.id]
            assert len(loaded.backlog) == 2
            assert loaded.backlog[0]["name"] == "deferred_1"
            assert loaded.backlog[1]["name"] == "deferred_2"
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_empty_backlog_not_copied(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            driver = SelfBootstrapDriver(description="test")
            driver.state_dir = d

            orch = _make_orchestrator(d)
            driver.orchestrator = orch

            driver.cross_pipeline_context["backlog"] = []

            pipeline, _ = orch.create_pipeline("continuation")
            driver.pipeline_id = pipeline.id

            if driver.cross_pipeline_context.get("backlog"):
                pipeline.backlog = driver.cross_pipeline_context["backlog"]
                orch._save_pipelines()

            loaded = orch.pipelines[pipeline.id]
            assert loaded.backlog == []
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ============================================================
# Layer 3: LocalModelBridge
# ============================================================


class TestLocalModelBridge:
    def test_analysis_response_has_roles_and_tasks(self):
        from self_bootstrap import LocalModelBridge

        bridge = LocalModelBridge(str(PROJECT_ROOT))
        response = bridge.respond(
            "Analyze the following task breakdown for the project",
            {"action": "analyze"},
        )
        data = json.loads(response)
        assert "roles" in data
        assert "tasks" in data
        assert len(data["roles"]) >= 2
        assert len(data["tasks"]) >= 1

    def test_plan_response_has_task_graph(self):
        from self_bootstrap import LocalModelBridge

        bridge = LocalModelBridge(str(PROJECT_ROOT))
        response = bridge.respond(
            "Create an execution plan for this project",
            {"action": "plan"},
        )
        data = json.loads(response)
        assert "task_graph" in data
        assert "tasks" in data["task_graph"]
        assert "execution_waves" in data["task_graph"]

    def test_implementation_response_has_status(self):
        from self_bootstrap import LocalModelBridge

        bridge = LocalModelBridge(str(PROJECT_ROOT))
        response = bridge.respond(
            "Implement the feature and write code",
            {"action": "execute"},
        )
        data = json.loads(response)
        assert data["success"] is True
        assert "artifacts" in data
        assert data["artifacts"]["implementation_status"] == "completed"

    def test_execution_log_tracks_calls(self):
        from self_bootstrap import LocalModelBridge

        bridge = LocalModelBridge(str(PROJECT_ROOT))
        bridge.respond("analyze task", {"action": "analyze"})
        bridge.respond("plan execution", {"action": "plan"})
        assert len(bridge.execution_log) == 2
        assert bridge.execution_log[0]["context_action"] == "analyze"

    def test_generic_response_for_unknown_prompt(self):
        from self_bootstrap import LocalModelBridge

        bridge = LocalModelBridge(str(PROJECT_ROOT))
        response = bridge.respond(
            "Some random prompt without keywords",
            {"action": "unknown"},
        )
        data = json.loads(response)
        assert data["success"] is True
        assert "artifacts" in data


# ============================================================
# Layer 1 gap: auto_continue recursive call preserves sequential_mode
# ============================================================


class TestAutoContinueSequentialMode:
    def test_auto_continue_forwards_sequential_mode(self):
        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            orch._auto_continue = True

            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.CONFIRM_PLAN.value
            p.state = PipelineState.RUNNING
            p.started_at = datetime.now()
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            plan_data = {
                "task_graph": {
                    "tasks": [{"name": "t1", "description": "T1", "role": "developer", "priority": "P1", "depends_on": []}],
                    "execution_waves": [[0]],
                }
            }
            orch.context.store_artifact(p.id, "", "plan", plan_data)
            p.artifacts["plan"] = plan_data
            orch._save_pipelines()

            orch._mark_decision_pending(p)

            result = orch.advance(
                p.id,
                {"decision": "A", "sequential_mode": True},
            )

            assert result.get("action") in ("execute_next_task", "human_decision")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_auto_continue_resolved_flag_prevents_recursion(self):
        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            orch._auto_continue = True

            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.CONFIRM_PLAN.value
            p.state = PipelineState.RUNNING
            p.started_at = datetime.now()
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            plan_data = {
                "task_graph": {
                    "tasks": [{"name": "t1", "description": "T1", "role": "developer", "priority": "P1", "depends_on": []}],
                    "execution_waves": [[0]],
                }
            }
            orch.context.store_artifact(p.id, "", "plan", plan_data)
            p.artifacts["plan"] = plan_data
            orch._save_pipelines()

            orch._mark_decision_pending(p)

            result = orch.advance(
                p.id,
                {"decision": "A", "auto_continue_resolved": True},
            )

            assert result.get("action") in ("execute_next_task", "human_decision", "model_request")
            assert result.get("auto_continue_resolved") is not True
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ============================================================
# Context budget-aware integration
# ============================================================


class TestContextBudgetIntegration:
    def test_orchestrator_uses_budget_aware_context(self):
        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)

            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.EXECUTE.value
            p.state = PipelineState.RUNNING
            p.started_at = datetime.now()
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            for i in range(50):
                orch.context.add_entry(
                    p.id, "task_1", "orchestrator", "execute",
                    f"Entry {i} " + "x" * 500,
                )

            ctx = orch.context.get_context_for_prompt(p.id, "task_1")
            ctx_bytes = len(ctx.encode("utf-8"))

            assert ctx_bytes <= 110_000, f"Context {ctx_bytes} bytes exceeds budget"
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_compression_triggers_on_large_context(self):
        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)

            p = PipelineRun(description="test", max_duration_hours=5.0)
            p.phase = PipelinePhase.EXECUTE.value
            p.state = PipelineState.RUNNING
            p.started_at = datetime.now()
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            for i in range(200):
                orch.context.add_entry(
                    p.id, "", "orchestrator", "execute",
                    f"Entry {i} " + "y" * 500,
                )

            usage = orch.context.get_budget_usage(p.id)
            assert usage["compressed"] is True
            assert usage["usage_pct"] <= 100
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_budget_usage_returns_valid_structure(self):
        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)

            p = PipelineRun(description="test", max_duration_hours=5.0)
            orch.pipelines[p.id] = p
            orch._save_pipelines()

            orch.context.add_entry(p.id, "", "dev", "init", "created pipeline")

            usage = orch.context.get_budget_usage(p.id)
            assert "entries_count" in usage
            assert "entries_bytes" in usage
            assert "budget_bytes" in usage
            assert "usage_pct" in usage
            assert "compressed" in usage
            assert usage["entries_count"] >= 1
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestCrossPipelineContextCap:
    def test_caps_issues_to_max(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context["issues"] = [f"issue_{i}" for i in range(100)]
        driver._cap_cross_pipeline_context(max_issues=50)
        assert len(driver.cross_pipeline_context["issues"]) == 50
        assert "issue_99" in driver.cross_pipeline_context["issues"]

    def test_caps_completed_work_to_max(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context["completed_work"] = [
            {"pipeline_id": f"p_{i}"} for i in range(30)
        ]
        driver._cap_cross_pipeline_context(max_completed=20)
        assert len(driver.cross_pipeline_context["completed_work"]) == 20

    def test_no_cap_needed_when_under_limit(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context["issues"] = ["issue_1", "issue_2"]
        driver.cross_pipeline_context["completed_work"] = [{"p": "1"}]
        driver._cap_cross_pipeline_context(max_issues=50, max_completed=20)
        assert len(driver.cross_pipeline_context["issues"]) == 2
        assert len(driver.cross_pipeline_context["completed_work"]) == 1

    def test_caps_backlog_to_max(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.cross_pipeline_context["backlog"] = [
            {"name": f"item_{i}", "status": "pending"} for i in range(150)
        ]
        driver._cap_cross_pipeline_context(max_backlog=100)
        assert len(driver.cross_pipeline_context["backlog"]) == 100


class TestLogBudgetIfNeeded:
    def test_logs_budget_every_20_iterations(self):
        from self_bootstrap import SelfBootstrapDriver

        d = tempfile.mkdtemp()
        try:
            orch = _make_orchestrator(d)
            p = PipelineRun(description="test", max_duration_hours=5.0)
            orch.pipelines[p.id] = p
            orch._save_pipelines()
            orch.context.add_entry(p.id, "", "dev", "init", "start")

            driver = SelfBootstrapDriver(description="test")
            driver.orchestrator = orch
            driver.pipeline_id = p.id

            driver.total_iterations = 20
            driver._log_budget_if_needed()

            usage = orch.context.get_budget_usage(p.id)
            assert usage["entries_count"] >= 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_skips_logging_when_not_20th_iteration(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.pipeline_id = None
        driver.total_iterations = 15
        driver._log_budget_if_needed()


class TestSelfBootstrapP0Guards:
    def test_handle_call_skill_fails_when_pending_never_resolves(self):
        from self_bootstrap import SelfBootstrapDriver

        class AlwaysPendingSkill:
            def execute(self, prompt, ctx):
                return {
                    "success": True,
                    "pending_model_request": {
                        "id": "req_1",
                        "prompt": "Please continue",
                    },
                    "artifacts": {},
                }

        driver = SelfBootstrapDriver(description="test")
        driver.max_skill_pending_rounds = 2
        driver.skills = {"test-skill": AlwaysPendingSkill()}
        driver.pipeline_id = "pipe_x"

        orch = MagicMock()
        orch.advance.return_value = {"action": "failed"}
        driver.orchestrator = orch

        driver._handle_call_skill(
            {
                "skill": "test-skill",
                "action_type": "execute_task",
                "prompt": "run",
            }
        )

        assert orch.advance.called
        args = orch.advance.call_args[0]
        assert args[0] == "pipe_x"
        assert args[1]["success"] is False
        assert "remained pending" in args[1]["error"]

    def test_run_uses_configured_time_budget(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")

        orch = MagicMock()
        orch._runtime_config = {"pipeline": {"default_max_duration_hours": 0.0}}

        def fake_setup():
            driver.orchestrator = orch

        driver.setup = fake_setup

        result = driver.run()
        assert result["total_pipelines"] == 0
        assert result["elapsed_seconds"] >= 0


class TestStagnationGuard:
    def test_detects_stagnation_after_repeated_same_backlog(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.max_stagnation_rounds = 2
        driver.cross_pipeline_context["backlog"] = [
            {"name": "same_task", "role": "developer", "priority": "P2", "status": "pending"}
        ]

        assert driver._is_stagnating() is False
        assert driver._is_stagnating() is False
        assert driver._is_stagnating() is True

    def test_stagnation_resets_when_backlog_changes(self):
        from self_bootstrap import SelfBootstrapDriver

        driver = SelfBootstrapDriver(description="test")
        driver.max_stagnation_rounds = 2
        driver.cross_pipeline_context["backlog"] = [
            {"name": "task_a", "status": "pending"}
        ]

        assert driver._is_stagnating() is False
        assert driver._is_stagnating() is False

        driver.cross_pipeline_context["backlog"] = [
            {"name": "task_b", "status": "pending"}
        ]
        assert driver._is_stagnating() is False
        assert driver.stagnation_rounds == 0


class TestCrossProcessSessionRoundTrip:
    def test_init_save_load_respond_completes_analyze(self):
        d = tempfile.mkdtemp()
        try:
            from pipeline.runner import PipelineRunner

            runner = PipelineRunner(
                project_root=str(PROJECT_ROOT),
                description="Test cross-process round-trip",
                model_mode="synthetic",
                state_dir=d,
                skip_skill_analysis=True,
            )
            runner.setup()
            runner._load_initial_backlog()
            runner.start_time = datetime.now()

            result = runner.step()
            assert result.get("needs_model") is True
            assert result.get("action") == "analyze"

            session_path = os.path.join(d, "runner_session.json")
            runner.save_session()
            assert os.path.exists(session_path)

            runner2 = PipelineRunner.load_session(session_path)
            assert runner2.pipeline_id == runner.pipeline_id
            assert runner2._last_result.get("action") == "analyze"
            assert runner2._pending_analysis is None

            analysis = json.dumps({
                "success": True,
                "artifacts": {
                    "roles": [{"type": "developer", "name": "dev", "capabilities": ["code"]}],
                    "tasks": [{"name": "t1", "description": "Do it", "role": "developer", "depends_on": [], "priority": "P1"}],
                },
            })
            result2 = runner2.respond(analysis)
            assert not result2.get("error"), f"respond failed: {result2}"
            assert result2.get("needs_model") is True or result2.get("done") is True
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestRequireRealModelGuard:
    def test_call_model_raises_without_ipc_listener_when_required(self):
        from pipeline.runner import PipelineRunner

        d = tempfile.mkdtemp()
        try:
            runner = PipelineRunner(
                project_root=str(PROJECT_ROOT),
                description="real-model required",
                model_mode="opencode_ipc",
                state_dir=d,
                require_real_model=True,
            )
            runner.pipeline_id = "pipe_test"

            with patch("pipeline.runner.time.time", side_effect=[100.0, 103.2]):
                with pytest.raises(RuntimeError, match="No IPC listener"):
                    runner._call_model("Analyze this", {"action": "analyze"})
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_call_model_falls_back_to_synthetic_when_not_required(self):
        from pipeline.runner import PipelineRunner

        d = tempfile.mkdtemp()
        try:
            runner = PipelineRunner(
                project_root=str(PROJECT_ROOT),
                description="fallback allowed",
                model_mode="opencode_ipc",
                state_dir=d,
                require_real_model=False,
            )
            runner.pipeline_id = "pipe_test"

            with patch("pipeline.runner.time.time", side_effect=[200.0, 203.2]):
                response = runner._call_model("Analyze task breakdown", {"action": "analyze"})

            parsed = json.loads(response)
            assert isinstance(parsed, dict)
            assert "tasks" in parsed
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestCrossProcessSessionPersistence:

    def test_init_save_load_step_produces_same_state(self):
        d = tempfile.mkdtemp()
        try:
            from pipeline.runner import PipelineRunner

            runner = PipelineRunner(
                project_root=str(PROJECT_ROOT),
                description="Session persistence test",
                model_mode="synthetic",
                state_dir=d,
                skip_skill_analysis=True,
            )
            runner.setup()
            runner._load_initial_backlog()
            runner.start_time = datetime.now()

            result1 = runner.step()

            session_path = os.path.join(d, "runner_session.json")
            runner.save_session()

            runner2 = PipelineRunner.load_session(session_path)
            assert runner2.pipeline_id == runner.pipeline_id
            assert runner2.total_pipelines == runner.total_pipelines
            assert runner2.iteration == runner.iteration

            result2 = runner2.step()
            assert result2.get("action") == result1.get("action")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_full_step_respond_cycle_through_analyze_and_plan(self):
        d = tempfile.mkdtemp()
        try:
            from pipeline.runner import PipelineRunner

            runner = PipelineRunner(
                project_root=str(PROJECT_ROOT),
                description="Full cycle test",
                model_mode="synthetic",
                state_dir=d,
                skip_skill_analysis=True,
            )
            runner.setup()
            runner._load_initial_backlog()
            runner.start_time = datetime.now()

            result = runner.step()
            assert result.get("action") == "analyze"

            analysis = json.dumps({
                "success": True,
                "artifacts": {
                    "roles": [{"type": "developer", "name": "dev", "capabilities": ["code"]}],
                    "tasks": [
                        {"name": "t1", "description": "First", "role": "developer", "depends_on": [], "priority": "P1"},
                        {"name": "t2", "description": "Second", "role": "developer", "depends_on": ["t1"], "priority": "P2"},
                    ],
                },
            })
            result2 = runner.respond(analysis)
            assert not result2.get("error"), f"respond(analysis) failed: {result2}"

            session_path = os.path.join(d, "runner_session.json")
            runner.save_session()

            runner3 = PipelineRunner.load_session(session_path)
            result3 = runner3.step()
            assert not result3.get("error"), f"step after load failed: {result3}"
        finally:
            shutil.rmtree(d, ignore_errors=True)
