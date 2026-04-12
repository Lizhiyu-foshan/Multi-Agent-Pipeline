import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.models import PipelinePhase, PipelineRun, PipelineState, Task
from pipeline.pipeline_orchestrator import PipelineOrchestrator


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
    return PipelineOrchestrator(
        state_dir=temp_dir,
        skills={
            "bmad-evo": MockSkill(),
            "superpowers": MockSkill(),
            "spec-kit": MockSkill(),
        },
        watchdog_config=False,
    )


def _make_pipeline(orch, **kw):
    p = PipelineRun(**kw)
    orch.pipelines[p.id] = p
    return p


def _add_task(
    orch,
    pipeline_id,
    status="pending",
    name="task",
    role_id="dev",
    started_at=None,
    completed_at=None,
):
    t = Task(
        id=f"task_{len(orch.scheduler.task_queue.tasks)}",
        pipeline_id=pipeline_id,
        name=name,
        role_id=role_id,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
    )
    orch.scheduler.task_queue.tasks[t.id] = t
    return t


class TestGetProgressReport:
    def test_returns_none_for_unknown_pipeline(self, orch):
        assert orch.get_progress_report("nonexistent") is None

    def test_basic_report_structure(self, orch):
        p = _make_pipeline(orch, description="test pipeline")
        report = orch.get_progress_report(p.id)
        assert report is not None
        assert report["id"] == p.id
        assert report["description"] == "test pipeline"
        assert "state" in report
        assert "phase" in report
        assert "progress_pct" in report
        assert "tasks" in report
        assert "phase_timeline" in report
        assert "elapsed_minutes" in report
        assert "task_details" in report

    def test_zero_tasks_progress(self, orch):
        p = _make_pipeline(orch)
        report = orch.get_progress_report(p.id)
        assert report["progress_pct"] == 0.0
        assert report["tasks"]["total"] == 0
        assert report["tasks"]["completed"] == 0
        assert report["tasks"]["by_status"] == {}

    def test_task_counts_by_status(self, orch):
        p = _make_pipeline(orch)
        _add_task(orch, p.id, status="completed", name="t1")
        _add_task(orch, p.id, status="completed", name="t2")
        _add_task(orch, p.id, status="failed", name="t3")
        _add_task(orch, p.id, status="processing", name="t4")
        _add_task(orch, p.id, status="pending", name="t5")

        report = orch.get_progress_report(p.id)
        assert report["tasks"]["total"] == 5
        assert report["tasks"]["completed"] == 2
        assert report["tasks"]["failed"] == 1
        assert report["tasks"]["processing"] == 1
        assert report["tasks"]["pending"] == 1
        assert report["progress_pct"] == 40.0

    def test_task_details_include_timing(self, orch):
        p = _make_pipeline(orch)
        now = datetime.now()
        _add_task(
            orch,
            p.id,
            status="completed",
            name="timed_task",
            started_at=now - timedelta(minutes=5),
            completed_at=now,
        )

        report = orch.get_progress_report(p.id)
        details = report["task_details"]
        assert len(details) == 1
        d = details[0]
        assert d["status"] == "completed"
        assert "started_at" in d
        assert "completed_at" in d
        assert "duration_seconds" in d
        assert d["duration_seconds"] > 0

    def test_task_details_no_timing_for_pending(self, orch):
        p = _make_pipeline(orch)
        _add_task(orch, p.id, status="pending", name="pending_task")

        report = orch.get_progress_report(p.id)
        d = report["task_details"][0]
        assert "started_at" not in d
        assert "completed_at" not in d
        assert "duration_seconds" not in d

    def test_eta_calculation(self, orch):
        p = _make_pipeline(orch)
        now = datetime.now()
        p.started_at = now - timedelta(minutes=10)

        _add_task(
            orch,
            p.id,
            status="completed",
            name="t1",
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=5),
        )
        _add_task(
            orch,
            p.id,
            status="completed",
            name="t2",
            started_at=now - timedelta(minutes=5),
            completed_at=now,
        )
        _add_task(orch, p.id, status="pending", name="t3")
        _add_task(orch, p.id, status="pending", name="t4")

        report = orch.get_progress_report(p.id)
        assert report["eta_minutes"] is not None
        assert report["eta_minutes"] == 10.0  # (10min/2completed) * 2remaining

    def test_eta_none_when_no_completed(self, orch):
        p = _make_pipeline(orch)
        p.started_at = datetime.now()
        _add_task(orch, p.id, status="pending", name="t1")

        report = orch.get_progress_report(p.id)
        assert report["eta_minutes"] is None

    def test_elapsed_minutes(self, orch):
        p = _make_pipeline(orch)
        p.started_at = datetime.now() - timedelta(minutes=5)

        report = orch.get_progress_report(p.id)
        assert report["elapsed_minutes"] >= 4.9

    def test_elapsed_zero_when_not_started(self, orch):
        p = _make_pipeline(orch)
        assert p.started_at is None

        report = orch.get_progress_report(p.id)
        assert report["elapsed_minutes"] == 0.0

    def test_phase_timeline(self, orch):
        p = _make_pipeline(orch)
        assert len(p.phase_history) >= 1
        assert p.phase_history[0]["phase"] == "init"

        p.record_phase(PipelinePhase.ANALYZE)
        p.record_phase(PipelinePhase.PLAN)

        report = orch.get_progress_report(p.id)
        timeline = report["phase_timeline"]
        assert len(timeline) >= 3
        assert timeline[0]["phase"] == "init"
        assert timeline[1].get("to") == "analyze"
        assert timeline[2].get("to") == "plan"

    def test_phase_timeline_records_from_to(self, orch):
        p = _make_pipeline(orch)
        p.record_phase(PipelinePhase.EXECUTE)

        report = orch.get_progress_report(p.id)
        entry = report["phase_timeline"][-1]
        assert entry["from"] == "init"
        assert entry["to"] == "execute"
        assert "at" in entry

    def test_record_phase_noop_same_phase(self, orch):
        p = _make_pipeline(orch)
        initial_count = len(p.phase_history)
        p.record_phase(p.phase)
        assert len(p.phase_history) == initial_count

    def test_checkpoint_count(self, orch):
        p = _make_pipeline(orch)
        report = orch.get_progress_report(p.id)
        assert report["checkpoint_count"] == 0

        orch.checkpoint_mgr.create_full_snapshot(
            p, {"pending": 1}, {"roles": []}, label="first"
        )
        report = orch.get_progress_report(p.id)
        assert report["checkpoint_count"] == 1

    def test_recovery_and_decision_counts(self, orch):
        p = _make_pipeline(orch)
        p.recovery_count = 3
        p.decision_history.append({"cycle": 1, "decision": "continue"})
        p.decision_history.append({"cycle": 2, "decision": "fix"})

        report = orch.get_progress_report(p.id)
        assert report["recovery_count"] == 3
        assert report["decision_count"] == 2

    def test_active_session_none_when_no_session(self, orch):
        p = _make_pipeline(orch)
        report = orch.get_progress_report(p.id)
        assert report["active_session"] is None

    def test_context_budget_included(self, orch):
        p = _make_pipeline(orch)
        report = orch.get_progress_report(p.id)
        assert report["context_budget"] is not None
        assert "budget_bytes" in report["context_budget"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
