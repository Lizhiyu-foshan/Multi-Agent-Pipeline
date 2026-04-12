"""
Unit Tests for PipelineMetrics runtime metrics collection.
"""

import json
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.metrics import PipelineMetrics


@pytest.fixture
def temp_dir():
    td = tempfile.mkdtemp()
    yield td
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def metrics(temp_dir):
    return PipelineMetrics(state_dir=temp_dir)


class TestPipelineMetricsBasic:
    def test_register_pipeline(self, metrics):
        metrics.register_pipeline("pipe_1")
        m = metrics.get_metrics("pipe_1")
        assert m is not None
        assert m["pipeline_id"] == "pipe_1"

    def test_unknown_pipeline_returns_none(self, metrics):
        assert metrics.get_metrics("nonexistent") is None

    def test_initial_metrics_values(self, metrics):
        metrics.register_pipeline("pipe_1")
        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["started"] == 0
        assert m["tasks"]["completed"] == 0
        assert m["tasks"]["failed"] == 0
        assert m["tasks"]["retried"] == 0
        assert m["tasks"]["peak_concurrent"] == 0
        assert m["tasks"]["avg_duration_seconds"] is None
        assert m["decisions"]["total"] == 0
        assert m["recoveries"]["total"] == 0
        assert m["checkpoints"] == 0


class TestPhaseMetrics:
    def test_phase_entry_and_exit(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_phase_entry("pipe_1", "init")
        metrics.record_phase_exit("pipe_1", "init")
        metrics.record_phase_entry("pipe_1", "execute")

        m = metrics.get_metrics("pipe_1")
        assert "init" in m["phase_durations"]
        assert m["phase_durations"]["init"] >= 0
        assert "execute" in m["phase_durations"]

    def test_active_phase_shows_duration(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_phase_entry("pipe_1", "execute")

        m = metrics.get_metrics("pipe_1")
        assert m["phase_durations"]["execute"] >= 0


class TestTaskMetrics:
    def test_task_start_and_complete(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_task_start("pipe_1", "task_1")
        metrics.record_task_complete("pipe_1", "task_1", duration_seconds=5.0)

        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["started"] == 1
        assert m["tasks"]["completed"] == 1
        assert m["tasks"]["avg_duration_seconds"] == 5.0

    def test_task_fail(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_task_start("pipe_1", "task_1")
        metrics.record_task_fail("pipe_1", "task_1", retry_count=1)

        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["started"] == 1
        assert m["tasks"]["failed"] == 1
        assert m["tasks"]["completed"] == 0

    def test_task_retry(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_task_retry("pipe_1", "task_1", attempt=2)

        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["retried"] == 1

    def test_peak_concurrent_tasks(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_task_start("pipe_1", "t1")
        metrics.record_task_start("pipe_1", "t2")
        metrics.record_task_start("pipe_1", "t3")

        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["peak_concurrent"] == 3

        metrics.record_task_complete("pipe_1", "t1")
        metrics.record_task_complete("pipe_1", "t2")
        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["peak_concurrent"] == 3

    def test_throughput_calculation(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_task_start("pipe_1", "t1")
        metrics.record_task_complete("pipe_1", "t1", duration_seconds=1.0)

        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["throughput_per_minute"] >= 0

    def test_avg_duration_multiple_tasks(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_task_complete("pipe_1", "t1", duration_seconds=4.0)
        metrics.record_task_complete("pipe_1", "t2", duration_seconds=6.0)

        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["avg_duration_seconds"] == 5.0


class TestDecisionMetrics:
    def test_record_decision(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_decision("pipe_1", "confirm_plan", "A")
        metrics.record_decision("pipe_1", "decide", "B", auto=True)

        m = metrics.get_metrics("pipe_1")
        assert m["decisions"]["total"] == 2
        assert m["decisions"]["auto_resolved"] == 1
        assert m["decisions"]["by_choice"]["A"] == 1
        assert m["decisions"]["by_choice"]["B"] == 1


class TestRecoveryMetrics:
    def test_record_recovery(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_recovery("pipe_1", "latest", True)
        metrics.record_recovery("pipe_1", "force", False)

        m = metrics.get_metrics("pipe_1")
        assert m["recoveries"]["total"] == 2
        assert m["recoveries"]["successful"] == 1


class TestCheckpointMetrics:
    def test_record_checkpoint(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_checkpoint("pipe_1")
        metrics.record_checkpoint("pipe_1")

        m = metrics.get_metrics("pipe_1")
        assert m["checkpoints"] == 2


class TestMetricsSummary:
    def test_summary_format(self, metrics):
        metrics.register_pipeline("pipe_1")
        metrics.record_phase_entry("pipe_1", "init")
        metrics.record_phase_exit("pipe_1", "init")
        metrics.record_task_start("pipe_1", "t1")
        metrics.record_task_complete("pipe_1", "t1", duration_seconds=3.0)
        metrics.record_decision("pipe_1", "confirm_plan", "A")

        summary = metrics.get_summary("pipe_1")
        assert "pipe_1" in summary
        assert "Tasks:" in summary
        assert "Decisions:" in summary

    def test_summary_unknown_pipeline(self, metrics):
        assert metrics.get_summary("nonexistent") == "No metrics available."


class TestMetricsPersistence:
    def test_save_and_load(self, metrics, temp_dir):
        metrics.register_pipeline("pipe_1")
        metrics.record_task_start("pipe_1", "t1")
        metrics.record_task_complete("pipe_1", "t1", duration_seconds=2.0)
        metrics.record_decision("pipe_1", "decide", "A")

        metrics.save("pipe_1")

        metrics2 = PipelineMetrics(state_dir=temp_dir)
        metrics2.load("pipe_1")

        m = metrics2.get_metrics("pipe_1")
        assert m is not None
        assert m["tasks"]["completed"] == 1
        assert m["decisions"]["total"] == 1

    def test_save_without_state_dir(self):
        m = PipelineMetrics(state_dir=None)
        m.register_pipeline("pipe_1")
        m.save("pipe_1")  # Should not raise


class TestMetricsConcurrency:
    def test_thread_safety(self, metrics):
        import threading

        metrics.register_pipeline("pipe_1")
        errors = []

        def record_tasks():
            try:
                for i in range(50):
                    metrics.record_task_start(
                        "pipe_1", f"t_{threading.current_thread().name}_{i}"
                    )
                    metrics.record_task_complete(
                        "pipe_1",
                        f"t_{threading.current_thread().name}_{i}",
                        duration_seconds=1.0,
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=record_tasks, name=f"w{i}") for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        m = metrics.get_metrics("pipe_1")
        assert m["tasks"]["started"] == 200
        assert m["tasks"]["completed"] == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
