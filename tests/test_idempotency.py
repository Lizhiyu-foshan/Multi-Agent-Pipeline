"""
Unit Tests for idempotency guarantee in PipelineOrchestrator.advance().
"""

import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.pipeline_orchestrator import PipelineOrchestrator
from pipeline.models import PipelinePhase, PipelineRun, PipelineState


class MockSkill:
    def execute(self, description, context):
        return {"success": True, "artifacts": {"test": "data"}}


@pytest.fixture
def temp_dir():
    td = tempfile.mkdtemp()
    yield td
    shutil.rmtree(td, ignore_errors=True)


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
    return o


class TestIdempotencyKey:
    def test_key_includes_pipeline_phase_task(self, orch):
        k1 = orch._idempotency_key("p1", "init", {"task_id": "t1", "decision": ""})
        k2 = orch._idempotency_key("p1", "init", {"task_id": "t2", "decision": ""})
        k3 = orch._idempotency_key("p1", "execute", {"task_id": "t1", "decision": ""})
        assert k1 != k2
        assert k1 != k3

    def test_same_input_same_key(self, orch):
        k1 = orch._idempotency_key("p1", "init", {"task_id": "t1"})
        k2 = orch._idempotency_key("p1", "init", {"task_id": "t1"})
        assert k1 == k2

    def test_decision_in_key(self, orch):
        k1 = orch._idempotency_key(
            "p1", "confirm_plan", {"task_id": "", "decision": "A"}
        )
        k2 = orch._idempotency_key(
            "p1", "confirm_plan", {"task_id": "", "decision": "B"}
        )
        assert k1 != k2


class TestIdempotentAdvance:
    def test_duplicate_advance_returns_cached(self, orch):
        pipeline, _ = orch.create_pipeline("test")
        result1 = orch.advance(pipeline.id, {"success": True})
        assert result1.get("idempotent") is not True

        result2 = orch.advance(pipeline.id, {"success": True})
        assert result2.get("idempotent") is True
        assert result1["action"] == result2["action"]

    def test_different_result_not_cached(self, orch):
        pipeline, _ = orch.create_pipeline("test")
        result1 = orch.advance(pipeline.id, {"success": True})

        orch.pipelines[pipeline.id].phase = PipelinePhase.INIT
        result2 = orch.advance(pipeline.id, {"success": True, "decision": "B"})
        assert result2.get("idempotent") is not True

    def test_new_phase_clears_old_cache(self, orch):
        pipeline, _ = orch.create_pipeline("test")

        orch.pipelines[pipeline.id].phase = PipelinePhase.INIT
        result1 = orch.advance(pipeline.id, {"success": True})

        orch.pipelines[pipeline.id].phase = PipelinePhase.ANALYZE
        result2 = orch.advance(pipeline.id, {"success": True})
        assert result2.get("idempotent") is not True

    def test_cache_per_pipeline_isolated(self, orch):
        p1, _ = orch.create_pipeline("test1")
        p2, _ = orch.create_pipeline("test2")

        r1 = orch.advance(p1.id, {"success": True})
        assert r1.get("idempotent") is not True

        r2 = orch.advance(p2.id, {"success": True})
        assert r2.get("idempotent") is not True

        r1_dup = orch.advance(p1.id, {"success": True})
        assert r1_dup.get("idempotent") is True

        r2_dup = orch.advance(p2.id, {"success": True})
        assert r2_dup.get("idempotent") is True

    def test_no_cache_on_error(self, orch):
        result = orch.advance("nonexistent", {"success": True})
        assert "error" in result

        result2 = orch.advance("nonexistent", {"success": True})
        assert "error" in result2
        assert result2.get("idempotent") is not True


class TestIdempotencyEdgeCases:
    def test_timeout_not_cached(self, orch):
        pipeline, _ = orch.create_pipeline("test")
        pipeline.state = PipelineState.RUNNING.value
        pipeline.started_at = datetime.now() - timedelta(hours=10)
        orch._save_pipelines()

        result1 = orch.advance(pipeline.id, {"success": True})
        assert result1.get("action") == "human_decision"

    def test_advance_after_phase_change_works(self, orch):
        pipeline, _ = orch.create_pipeline("test")

        orch.pipelines[pipeline.id].phase = PipelinePhase.INIT
        r1 = orch.advance(pipeline.id, {"success": True})
        cached = orch._advance_cache
        assert len(cached) == 1

        new_phase = orch.pipelines[pipeline.id].phase
        r2 = orch.advance(pipeline.id, {"success": True})
        assert r2.get("idempotent") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
