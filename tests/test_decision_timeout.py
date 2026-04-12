"""
Unit Tests for Decision Timeout (#1).
Tests _is_decision_timed_out, _auto_resolve_decision, _mark_decision_pending.
"""

from pathlib import Path
from datetime import datetime, timedelta
import pytest
import os
import shutil
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.pipeline_orchestrator import PipelineOrchestrator
from pipeline.models import PipelineRun, PipelineState, PipelinePhase


@pytest.fixture
def temp_dir():
    td = tempfile.mkdtemp()
    yield td
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def orchestrator(temp_dir):
    class MockSkill:
        def execute(self, description, context):
            return {"success": True, "artifacts": {"test": "data"}}

    return PipelineOrchestrator(
        state_dir=temp_dir,
        skills={
            "bmad-evo": MockSkill(),
            "superpowers": MockSkill(),
            "spec-kit": MockSkill(),
        },
        watchdog_config=False,
    )


class TestIsDecisionTimedOut:
    def test_not_timed_out_when_no_last_decision(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        assert orchestrator._is_decision_timed_out(pipeline) is False

    def test_not_timed_out_within_deadline(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.DECIDE.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=10)
        assert orchestrator._is_decision_timed_out(pipeline) is False

    def test_timed_out_after_30_min_default(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.DECIDE.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=31)
        assert orchestrator._is_decision_timed_out(pipeline) is True

    def test_not_timed_out_in_execute_phase(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.EXECUTE.value
        pipeline.last_decision_at = datetime.now() - timedelta(hours=2)
        assert orchestrator._is_decision_timed_out(pipeline) is False

    def test_timed_out_in_confirm_plan(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.CONFIRM_PLAN.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=31)
        assert orchestrator._is_decision_timed_out(pipeline) is True

    def test_custom_timeout_value(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.decision_timeout_seconds = 600  # 10 minutes
        pipeline.phase = PipelinePhase.DECIDE.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=11)
        assert orchestrator._is_decision_timed_out(pipeline) is True

    def test_timed_out_in_init_phase(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.INIT.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=31)
        assert orchestrator._is_decision_timed_out(pipeline) is True

    def test_not_timed_out_in_init_within_deadline(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.INIT.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=5)
        assert orchestrator._is_decision_timed_out(pipeline) is False


class TestMarkDecisionPending:
    def test_sets_last_decision_at(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        assert pipeline.last_decision_at is None
        orchestrator._mark_decision_pending(pipeline)
        assert pipeline.last_decision_at is not None
        assert (datetime.now() - pipeline.last_decision_at).total_seconds() < 5

    def test_persists_to_disk(self, orchestrator, temp_dir):
        pipeline, _ = orchestrator.create_pipeline("test")
        orchestrator._mark_decision_pending(pipeline)

        pipe_file = Path(temp_dir) / "state" / "pipelines.json"
        import json

        with open(pipe_file) as f:
            data = json.load(f)
        assert data[pipeline.id]["last_decision_at"] is not None


class TestAutoResolveDecision:
    def test_confirm_plan_defaults_to_A(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.CONFIRM_PLAN.value
        orchestrator._mark_decision_pending(pipeline)

        orchestrator.scheduler.registry.register("developer", "Dev", ["code"])
        t1 = orchestrator.scheduler.submit_task(
            {
                "pipeline_id": pipeline.id,
                "role_id": "developer",
                "name": "Task 1",
                "description": "test",
                "priority": "P1",
                "depends_on": [],
            }
        )
        pipeline.tasks = [t1["task_id"]]
        pipeline.artifacts["plan"] = {
            "task_graph": {
                "tasks": [
                    {
                        "name": "Task 1",
                        "role_id": "developer",
                        "description": "test",
                        "priority": "P1",
                        "depends_on": [],
                    }
                ]
            }
        }
        orchestrator._save_pipelines()

        result = orchestrator._auto_resolve_decision(pipeline)
        assert result["action"] == "execute_next_task"
        assert pipeline.phase == PipelinePhase.EXECUTE.value

    def test_decide_defaults_to_A(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.DECIDE.value
        orchestrator._mark_decision_pending(pipeline)
        orchestrator._save_pipelines()

        result = orchestrator._auto_resolve_decision(pipeline)
        assert "action" in result
        assert pipeline.last_decision_at is None

    def test_clears_last_decision_at(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.DECIDE.value
        orchestrator._mark_decision_pending(pipeline)
        assert pipeline.last_decision_at is not None

        orchestrator._auto_resolve_decision(pipeline)
        assert pipeline.last_decision_at is None

    def test_init_defaults_to_A(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("ambiguous requirement")
        pipeline.phase = PipelinePhase.INIT.value
        orchestrator._mark_decision_pending(pipeline)
        orchestrator._save_pipelines()

        result = orchestrator._auto_resolve_decision(pipeline)
        assert "action" in result
        assert pipeline.last_decision_at is None


class TestDecisionTimeoutIntegration:
    def test_advance_auto_resolves_on_timeout(self, orchestrator):
        """advance() should auto-resolve when decision timed out."""
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.DECIDE.value
        pipeline.state = PipelineState.RUNNING.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=31)
        orchestrator._save_pipelines()

        result = orchestrator.advance(pipeline.id, {"decision": "manual"})
        # Should have been auto-resolved (decision A = continue to EVOLVE)
        assert result.get("action") in (
            "call_skill",
            "execute_next_task",
            "human_decision",
        )

    def test_advance_does_not_auto_resolve_within_deadline(self, orchestrator):
        """advance() should NOT auto-resolve when within deadline."""
        pipeline, _ = orchestrator.create_pipeline("test")
        pipeline.phase = PipelinePhase.EXECUTE.value
        pipeline.state = PipelineState.RUNNING.value
        pipeline.last_decision_at = datetime.now() - timedelta(minutes=5)
        orchestrator._save_pipelines()

        result = orchestrator.advance(
            pipeline.id, {"task_id": "", "skill": "superpowers"}
        )
        # Should proceed normally, not auto-resolve
        assert "error" not in result or result.get("action") != "auto_resolved"


class TestPausedHandler:
    def test_paused_decision_A_resumes_pipeline(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("paused flow")
        pipeline.phase = PipelinePhase.PAUSED.value
        pipeline.state = PipelineState.PAUSED.value
        orchestrator._save_pipelines()

        result = orchestrator.advance(pipeline.id, {"decision": "A"})
        assert result.get("action") == "execute_next_task"
        assert orchestrator.pipelines[pipeline.id].state == PipelineState.RUNNING

    def test_paused_decision_B_fails_pipeline(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("paused stop")
        pipeline.phase = PipelinePhase.PAUSED.value
        pipeline.state = PipelineState.PAUSED.value
        orchestrator._save_pipelines()

        result = orchestrator.advance(pipeline.id, {"decision": "B"})
        assert result.get("action") == "failed"
        assert orchestrator.pipelines[pipeline.id].state == PipelineState.FAILED

    def test_paused_invalid_decision_reasks(self, orchestrator):
        pipeline, _ = orchestrator.create_pipeline("paused invalid")
        pipeline.phase = PipelinePhase.PAUSED.value
        pipeline.state = PipelineState.PAUSED.value
        orchestrator._save_pipelines()

        result = orchestrator.advance(pipeline.id, {"decision": "X"})
        assert result.get("action") == "human_decision"
        assert result.get("phase") == "paused"
        assert result.get("options") == ["A", "B"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
