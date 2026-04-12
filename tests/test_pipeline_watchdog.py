"""
Unit Tests for PipelineWatchdog.
Tests health checking, action taking, and monitoring loop logic.
"""

from pathlib import Path
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datetime import datetime, timedelta
from pipeline.pipeline_watchdog import (
    PipelineWatchdog,
    WatchdogConfig,
    HealthStatus,
)
from pipeline.models import PipelineRun, PipelineState, PipelinePhase


class MockOrchestrator:
    """Mock orchestrator for testing."""

    def __init__(self):
        self.pipelines = {}
        self.scheduler = MockScheduler()
        self.recovery_calls = []

    def add_pipeline(
        self, pipeline_id, state=PipelineState.RUNNING, phase=PipelinePhase.EXECUTE
    ):
        pipeline = PipelineRun(
            id=pipeline_id,
            description=f"Pipeline {pipeline_id}",
            state=state.value,
            phase=phase.value,
        )
        self.pipelines[pipeline_id] = pipeline
        return pipeline

    def recover(self, pipeline_id, strategy):
        """Mock recover that records calls."""
        self.recovery_calls.append({"pipeline_id": pipeline_id, "strategy": strategy})
        return {"recovered": True, "strategy": strategy}

    def _emit_lifecycle(self, point, context):
        """Mock lifecycle emission."""
        pass


class MockScheduler:
    """Mock scheduler for testing."""

    def __init__(self):
        self.tasks = {}
        self.task_queue = self

    def add_task(self, task_id, pipeline_id, status="pending", started_at=None):
        from pipeline.models import Task

        task = Task(
            id=task_id,
            pipeline_id=pipeline_id,
            role_id="dev",
            name="Test task",
        )
        task.status = status
        if started_at:
            task.started_at = started_at
        self.tasks[task_id] = task
        return task

    def task_queue_get(self, task_id):
        return self.tasks.get(task_id)

    def get_statistics(self):
        return {"pending": 1, "processing": 2, "completed": 3, "failed": 1}

    def get_by_pipeline(self, pipeline_id):
        return [t for t in self.tasks.values() if t.pipeline_id == pipeline_id]


class MockTaskQueue:
    """Mock task queue with get_by_pipeline and increment_retry."""

    def __init__(self):
        self.tasks = {}

    def add_task(self, task_id, pipeline_id, status="failed"):
        from pipeline.models import Task

        task = Task(
            id=task_id,
            pipeline_id=pipeline_id,
            role_id="dev",
            name="Stalled task",
        )
        task.status = status
        task.retry_count = 0
        task.max_retries = 3
        self.tasks[task_id] = task

    def get(self, task_id):
        return self.tasks.get(task_id)

    def get_by_pipeline(self, pipeline_id):
        return [t for t in self.tasks.values() if t.pipeline_id == pipeline_id]

    def increment_retry(self, task_id):
        task = self.tasks.get(task_id)
        if task and task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = "pending"
            return True
        return False


class TestHealthCheck:
    """Tests for PipelineWatchdog.check() method."""

    def test_check_returns_healthy_for_running_pipeline(self):
        """Running pipeline with no issues should be HEALTHY."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1")

        wd = PipelineWatchdog(orchestrator=orch, config=WatchdogConfig())

        health = wd.check("pipe_1")

        assert health.status == HealthStatus.HEALTHY
        assert len(health.issues) == 0
        assert health.pipeline_id == "pipe_1"

    def test_check_returns_failed_for_nonexistent_pipeline(self):
        """Should return FAILED for nonexistent pipeline."""
        orch = MockOrchestrator()
        wd = PipelineWatchdog(orchestrator=orch)

        health = wd.check("nonexistent")

        assert health.status == HealthStatus.FAILED
        assert any("not found" in i.lower() for i in health.issues)

    def test_check_detects_timeout(self):
        """Should detect pipeline exceeding max_duration_hours."""
        orch = MockOrchestrator()
        pipeline = orch.add_pipeline("pipe_1")
        pipeline.max_duration_hours = 0.001  # ~3.6 seconds
        pipeline.started_at = datetime.now() - timedelta(seconds=10)

        wd = PipelineWatchdog(orchestrator=orch)

        health = wd.check("pipe_1")

        assert health.status == HealthStatus.TIMED_OUT
        assert any(
            "timeout" in i.lower() or "exceeded" in i.lower() for i in health.issues
        )

    def test_check_detects_stalled_tasks(self):
        """Should detect tasks stuck in processing beyond threshold."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1")

        # Add a stalled task
        orch.scheduler.add_task(
            "task_1",
            "pipe_1",
            status="processing",
            started_at=datetime.now() - timedelta(seconds=600),
        )

        config = WatchdogConfig(task_stall_threshold_seconds=300)
        wd = PipelineWatchdog(orchestrator=orch, config=config)

        health = wd.check("pipe_1")

        assert health.status == HealthStatus.STALLED
        assert "task_1" in health.stalled_tasks
        assert any("stalled" in i.lower() for i in health.issues)

    def test_check_ignores_recently_started_tasks(self):
        """Should not flag tasks started recently."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1")

        orch.scheduler.add_task(
            "task_1",
            "pipe_1",
            status="processing",
            started_at=datetime.now() - timedelta(seconds=10),
        )

        config = WatchdogConfig(task_stall_threshold_seconds=300)
        wd = PipelineWatchdog(orchestrator=orch, config=config)

        health = wd.check("pipe_1")

        assert health.status == HealthStatus.HEALTHY
        assert len(health.stalled_tasks) == 0

    def test_check_skips_completed_pipelines(self):
        """Should not check completed/failed/idle pipelines."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1", state=PipelineState.COMPLETED)

        wd = PipelineWatchdog(orchestrator=orch)

        health = wd.check("pipe_1")

        assert health.status == HealthStatus.HEALTHY


class TestTakeAction:
    """Tests for PipelineWatchdog.take_action() method."""

    def test_take_action_returns_nothing_for_healthy(self):
        """Should take no action for healthy pipeline."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1")
        wd = PipelineWatchdog(orchestrator=orch)

        health = wd.check("pipe_1")
        action = wd.take_action(health)

        assert action["status"] == HealthStatus.HEALTHY
        assert len(action["actions"]) == 0

    def test_take_action_calls_timeout_callback(self):
        """Should call timeout_callback for TIMED_OUT status."""
        callback_calls = []

        def timeout_cb(health):
            callback_calls.append(health.pipeline_id)

        config = WatchdogConfig(on_timeout_callback=timeout_cb)
        orch = MockOrchestrator()
        wd = PipelineWatchdog(orchestrator=orch, config=config)

        health = HealthCheckResult(pipeline_id="pipe_1", status=HealthStatus.TIMED_OUT)
        wd.take_action(health)

        assert len(callback_calls) == 1
        assert callback_calls[0] == "pipe_1"

    def test_take_action_calls_stall_callback(self):
        """Should call stall_callback for STALLED status."""
        callback_calls = []

        def stall_cb(health):
            callback_calls.append(health.pipeline_id)

        config = WatchdogConfig(on_stall_callback=stall_cb)
        orch = MockOrchestrator()
        wd = PipelineWatchdog(orchestrator=orch, config=config)

        health = HealthCheckResult(
            pipeline_id="pipe_1",
            status=HealthStatus.STALLED,
            stalled_tasks=["task_1"],
        )
        wd.take_action(health)

        assert len(callback_calls) == 1
        assert callback_calls[0] == "pipe_1"

    def test_take_action_auto_retrys_stalled_tasks(self):
        """Should auto-retry stalled tasks when configured."""
        orch = MockOrchestrator()
        orch.scheduler.task_queue = MockTaskQueue()
        orch.scheduler.task_queue.add_task("task_1", "pipe_1", status="processing")

        config = WatchdogConfig(auto_retry_stalled_tasks=True)
        wd = PipelineWatchdog(orchestrator=orch, config=config)
        wd.orchestrator.scheduler.task_queue.get_statistics = lambda: {"processing": 1}

        health = HealthCheckResult(
            pipeline_id="pipe_1",
            status=HealthStatus.STALLED,
            stalled_tasks=["task_1"],
        )
        action = wd.take_action(health)

        assert any("retry_stalled:task_1" in a for a in action["actions"])

    def test_take_action_auto_recovers_when_enabled(self):
        """Should auto-recover pipeline when auto_recover=True."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1")

        config = WatchdogConfig(auto_recover=True, max_auto_recover_attempts=3)
        wd = PipelineWatchdog(orchestrator=orch, config=config)

        health = HealthCheckResult(pipeline_id="pipe_1", status=HealthStatus.STALLED)
        action = wd.take_action(health)

        assert any("auto_recovered" in a for a in action["actions"])
        assert len(orch.recovery_calls) == 1
        assert orch.recovery_calls[0]["strategy"] == "clean"

    def test_take_action_limits_auto_recover_attempts(self):
        """Should limit auto-recover to max_auto_recover_attempts."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1")

        config = WatchdogConfig(auto_recover=True, max_auto_recover_attempts=2)
        wd = PipelineWatchdog(orchestrator=orch, config=config)

        # First attempt
        health = HealthCheckResult(pipeline_id="pipe_1", status=HealthStatus.STALLED)
        wd.take_action(health)

        # Second attempt
        wd.take_action(health)

        # Third attempt should be blocked
        action = wd.take_action(health)

        assert not any("auto_recovered" in a for a in action["actions"])


class TestCheckAll:
    """Tests for PipelineWatchdog.check_all() method."""

    def test_check_all_returns_all_running_pipelines(self):
        """Should return health results for registered pipelines."""
        orch = MockOrchestrator()
        orch.add_pipeline("pipe_1")
        orch.add_pipeline("pipe_2")
        orch.add_pipeline("pipe_3", state=PipelineState.COMPLETED)

        wd = PipelineWatchdog(orchestrator=orch)
        wd.register_pipeline("pipe_1")
        wd.register_pipeline("pipe_2")
        wd.register_pipeline("pipe_3")

        results = wd.check_all()

        assert len(results) == 3  # All registered
        assert any(r.pipeline_id == "pipe_1" for r in results)
        assert any(r.pipeline_id == "pipe_2" for r in results)

    def test_check_all_empty_when_no_pipelines(self):
        """Should return empty list when no running pipelines."""
        orch = MockOrchestrator()
        wd = PipelineWatchdog(orchestrator=orch)

        results = wd.check_all()

        assert len(results) == 0


class TestWatchdogConfig:
    """Tests for WatchdogConfig dataclass."""

    def test_default_values(self):
        """Should have sensible default values."""
        config = WatchdogConfig()

        assert config.check_interval_seconds == 60.0
        assert config.task_stall_threshold_seconds == 300.0
        assert config.session_idle_threshold_seconds == 1800.0
        assert config.progress_stall_threshold_seconds == 600.0
        assert config.auto_recover is False
        assert config.max_auto_recover_attempts == 2
        assert config.auto_retry_stalled_tasks is False

    def test_custom_values(self):
        """Should accept custom configuration values."""
        config = WatchdogConfig(
            check_interval_seconds=30.0,
            task_stall_threshold_seconds=600.0,
            auto_recover=True,
            max_auto_recover_attempts=5,
        )

        assert config.check_interval_seconds == 30.0
        assert config.task_stall_threshold_seconds == 600.0
        assert config.auto_recover is True
        assert config.max_auto_recover_attempts == 5


class TestHealthStatusEnum:
    """Tests for HealthStatus enum."""

    def test_has_all_expected_values(self):
        """Should have all expected health status values."""
        expected = {
            "HEALTHY",
            "WARNING",
            "STALLED",
            "TIMED_OUT",
            "RECOVERED",
            "FAILED",
        }

        assert set(HealthStatus) == {HealthStatus[v] for v in expected}


class TestGetStatus:
    """Tests for PipelineWatchdog.get_status() method."""

    def test_get_status_when_not_running(self):
        """Should show running=False when not started."""
        orch = MockOrchestrator()
        wd = PipelineWatchdog(orchestrator=orch)

        status = wd.get_status()

        assert status["running"] is False
        assert "config" in status
        assert "recovery_attempts" in status

    def test_get_status_includes_config(self):
        """Should include configuration in status."""
        config = WatchdogConfig(
            check_interval_seconds=45.0,
            auto_recover=True,
        )
        orch = MockOrchestrator()
        wd = PipelineWatchdog(orchestrator=orch, config=config)

        status = wd.get_status()

        assert status["config"]["check_interval"] == 45.0
        assert status["config"]["auto_recover"] is True


# Helper class for testing
class HealthCheckResult:
    """Simple health result for testing."""

    def __init__(self, pipeline_id, status, issues=None, stalled_tasks=None):
        self.pipeline_id = pipeline_id
        self.status = status
        self.issues = issues or []
        self.stalled_tasks = stalled_tasks or []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
