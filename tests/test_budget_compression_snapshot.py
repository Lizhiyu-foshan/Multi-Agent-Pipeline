"""
Unit Tests for Prompt Budget (#2), Smart Compression (#6), and Incremental Snapshots (#5).
"""

from pathlib import Path
from datetime import datetime, timedelta
import pytest
import os
import shutil
import tempfile
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.context_manager import (
    ContextManager,
    PROMPT_BUDGET_BYTES,
    ENTRY_IMPORTANCE,
)
from pipeline.checkpoint_manager import CheckpointManager
from pipeline.models import PipelineRun, PipelinePhase, PipelineState


@pytest.fixture
def temp_dir():
    td = tempfile.mkdtemp()
    yield td
    shutil.rmtree(td, ignore_errors=True)


# ===== #2: Prompt Length Budget =====


class TestPromptBudget:
    def test_get_context_for_prompt_returns_string(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        cm.add_entry("pipe_1", "task_1", "dev", "execute", "Do something")
        result = cm.get_context_for_prompt("pipe_1", "task_1")
        assert isinstance(result, str)
        assert "Do something" in result

    def test_prompt_respects_budget(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        for i in range(200):
            cm.add_entry(
                "pipe_1",
                "task_1",
                "dev",
                "execute",
                "A" * 2000,
            )
        result = cm.get_context_for_prompt("pipe_1", "task_1")
        assert len(result.encode("utf-8")) <= PROMPT_BUDGET_BYTES * 1.1

    def test_prioritizes_important_phases(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        cm.add_entry("pipe_1", "t1", "dev", "execute", "x" * 10000)
        cm.add_entry("pipe_1", "t1", "dev", "decide", "critical decision")
        cm._pipelines["pipe_1"][-1].phase = "decide"

        result = cm.get_context_for_prompt("pipe_1", "t1")
        assert "critical decision" in result

    def test_get_budget_usage(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        cm.add_entry("pipe_1", "t1", "dev", "execute", "test content")
        usage = cm.get_budget_usage("pipe_1")
        assert "entries_count" in usage
        assert "prompt_bytes" in usage
        assert "budget_bytes" in usage
        assert usage["entries_count"] == 1
        assert usage["budget_bytes"] == PROMPT_BUDGET_BYTES

    def test_empty_pipeline_returns_empty(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        result = cm.get_context_for_prompt("nonexistent")
        assert result == ""

    def test_includes_summary_when_available(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        cm._summaries["pipe_1"] = "Summary of previous work"
        cm.add_entry("pipe_1", "t1", "dev", "execute", "current task")
        result = cm.get_context_for_prompt("pipe_1", "t1")
        assert "Previous Context Summary" in result
        assert "current task" in result


# ===== #6: Smart Compression =====


class TestSmartCompression:
    def test_compress_keeps_important_entries(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        cm.add_entry("pipe_1", "t1", "dev", "decide", "critical decision entry")

        for i in range(20):
            cm.add_entry(
                "pipe_1",
                "t1",
                "dev",
                "execute",
                f"Routine execution step {i} " + "x" * 3000,
            )

        cm._check_compression("pipe_1")
        entries = cm._pipelines.get("pipe_1", [])
        phases = [e.phase for e in entries]
        assert "decide" in phases

    def test_compress_respects_importance_weights(self, temp_dir):
        assert ENTRY_IMPORTANCE["decide"] > ENTRY_IMPORTANCE["execute"]
        assert ENTRY_IMPORTANCE["error"] > ENTRY_IMPORTANCE["execute"]
        assert ENTRY_IMPORTANCE["plan"] > ENTRY_IMPORTANCE["orchestrator"]

    def test_compress_triggers_at_threshold(self, temp_dir):
        cm = ContextManager(state_dir=temp_dir)
        for i in range(100):
            cm.add_entry(
                "pipe_1",
                "t1",
                "dev",
                "execute",
                "x" * 500,
            )
        assert "pipe_1" in cm._summaries or len(cm._pipelines["pipe_1"]) < 100


# ===== #5: Incremental Snapshots =====


class TestIncrementalSnapshots:
    def test_first_snapshot_is_full(self, temp_dir):
        cm = CheckpointManager(state_dir=temp_dir)
        pipeline = PipelineRun(id="pipe_1", description="test")
        ckpt = cm.create_full_snapshot(
            pipeline, {"pending": 1}, {"roles": []}, label="first"
        )
        assert ckpt.snapshot.get("is_delta") is not True
        assert "pipeline" in ckpt.snapshot
        assert "task_queue_snapshot" in ckpt.snapshot
        assert ckpt.snapshot["task_queue_snapshot"]["pending"] == 1

    def test_identical_snapshot_produces_no_delta(self, temp_dir):
        cm = CheckpointManager(state_dir=temp_dir)
        pipeline = PipelineRun(id="pipe_1", description="test")
        pipeline.state = PipelineState.RUNNING.value

        cm.create_full_snapshot(pipeline, {"pending": 1}, {"roles": []}, label="first")
        ckpt2 = cm.create_full_snapshot(
            pipeline, {"pending": 1}, {"roles": []}, label="second"
        )
        assert ckpt2.snapshot.get("is_delta") is not True

    def test_changed_pipeline_produces_delta(self, temp_dir):
        cm = CheckpointManager(state_dir=temp_dir)
        pipeline = PipelineRun(id="pipe_1", description="test")
        pipeline.state = PipelineState.RUNNING.value

        cm.create_full_snapshot(pipeline, {"pending": 1}, {"roles": []}, label="first")

        pipeline.phase = PipelinePhase.EXECUTE.value
        pipeline.tasks = ["task_1"]
        ckpt2 = cm.create_full_snapshot(
            pipeline, {"pending": 1}, {"roles": []}, label="second"
        )
        assert ckpt2.snapshot.get("is_delta") is True
        assert "pipeline_delta" in ckpt2.snapshot

    def test_delta_applied_correctly(self, temp_dir):
        cm = CheckpointManager(state_dir=temp_dir)
        pipeline = PipelineRun(id="pipe_delta_apply", description="test")
        pipeline.state = PipelineState.RUNNING.value

        cm.create_full_snapshot(pipeline, {"pending": 1}, {"roles": []}, label="base")

        pipeline.phase = PipelinePhase.EXECUTE.value
        pipeline.tasks = ["task_1", "task_2"]
        cm.create_full_snapshot(pipeline, {"pending": 2}, {"roles": []}, label="delta1")

        restored = cm.restore_latest("pipe_delta_apply")
        assert restored is not None
        snap = restored["snapshot"]
        assert "pipeline" in snap
        assert snap["pipeline"]["phase"] == PipelinePhase.EXECUTE.value
        assert snap["pipeline"]["tasks"] == ["task_1", "task_2"]
        assert snap["task_queue_snapshot"]["pending"] == 2

    def test_multiple_deltas_chain(self, temp_dir):
        cm = CheckpointManager(state_dir=temp_dir)
        pipeline = PipelineRun(id="pipe_chain", description="test")
        pipeline.state = PipelineState.RUNNING.value

        cm.create_full_snapshot(pipeline, {"pending": 1}, {"roles": []}, label="base")

        for i in range(3):
            pipeline.pdca_cycle = i + 1
            cm.create_full_snapshot(
                pipeline, {"completed": i + 1}, {"roles": []}, label=f"delta_{i}"
            )

        restored = cm.restore_latest("pipe_chain")
        assert restored is not None
        assert restored["snapshot"]["pipeline"]["pdca_cycle"] == 3

    def test_restore_to_phase_reconstructs_delta_snapshot(self, temp_dir):
        cm = CheckpointManager(state_dir=temp_dir)
        pipeline = PipelineRun(id="pipe_phase_restore", description="test")
        pipeline.state = PipelineState.RUNNING.value

        cm.create_full_snapshot(pipeline, {"pending": 1}, {"roles": []}, label="base")
        pipeline.phase = PipelinePhase.EXECUTE.value
        pipeline.tasks = ["task_1"]
        cm.create_full_snapshot(
            pipeline, {"pending": 2, "completed": 1}, {"roles": []}, label="exec_delta"
        )

        restored = cm.restore_to_phase("pipe_phase_restore", PipelinePhase.EXECUTE.value)
        assert restored is not None
        snap = restored["snapshot"]
        assert "pipeline" in snap
        assert snap["pipeline"]["phase"] == PipelinePhase.EXECUTE.value
        assert snap["pipeline"]["tasks"] == ["task_1"]
        assert snap["task_queue_snapshot"]["pending"] == 2

    def test_restore_by_label_reconstructs_delta_snapshot(self, temp_dir):
        cm = CheckpointManager(state_dir=temp_dir)
        pipeline = PipelineRun(id="pipe_label_restore", description="test")
        pipeline.state = PipelineState.RUNNING.value

        cm.create_full_snapshot(pipeline, {"pending": 1}, {"roles": []}, label="base")
        pipeline.phase = PipelinePhase.CHECK.value
        pipeline.pdca_cycle = 1
        cm.create_full_snapshot(
            pipeline, {"pending": 0, "completed": 1}, {"roles": []}, label="target_label"
        )

        restored = cm.restore_by_label("pipe_label_restore", "target_label")
        assert restored is not None
        snap = restored["snapshot"]
        assert "pipeline" in snap
        assert snap["pipeline"]["phase"] == PipelinePhase.CHECK.value
        assert snap["pipeline"]["pdca_cycle"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
