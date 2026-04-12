"""
Unit Tests for TaskQueue retry functionality.
Tests retry logic, backoff calculation, failure recording, and eligibility.
"""

import time
from datetime import datetime, timedelta
from pathlib import Path
import pytest
import os
import tempfile
import shutil

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.task_queue import TaskQueue
from pipeline.models import Task


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test state files."""
    td = tempfile.mkdtemp()
    yield td
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
def task_queue(temp_dir):
    """Create a TaskQueue instance with a temp state file."""
    state_file = os.path.join(temp_dir, "task_queue.json")
    return TaskQueue(state_file=state_file)


@pytest.fixture
def sample_task():
    """Create a sample task for testing."""
    return Task(
        pipeline_id="test_pipeline",
        role_id="developer",
        name="Test task",
        description="Test task description",
        max_retries=3,
        retry_delay_seconds=10.0,
        retry_backoff_factor=2.0,
    )


class TestTaskRetryWithBackoff:
    """Tests for retry_with_backoff method."""

    def test_retry_success_first_attempt(self, task_queue, sample_task):
        """First retry should succeed with base delay."""
        task_id = task_queue.submit(sample_task)
        task_queue.update_status(task_id, "failed", {"error": "test error"})

        result = task_queue.retry_with_backoff(task_id, error="test failure")

        assert result["retried"] is True
        assert result["delay"] == 10.0
        assert result["attempts_remaining"] == 2
        assert result["task_id"] == task_id

        task = task_queue.get(task_id)
        assert task.status == "pending"
        assert task.retry_count == 1
        assert task.last_retry_at is not None

    def test_retry_exponential_backoff(self, task_queue, sample_task):
        """Delays should increase exponentially with each retry."""
        sample_task.max_retries = 4
        task_id = task_queue.submit(sample_task)

        # First retry: 10s
        task_queue.update_status(task_id, "failed")
        r1 = task_queue.retry_with_backoff(task_id, "error 1")
        assert r1["retried"] is True
        assert r1["delay"] == 10.0

        # Second retry: 20s (10 * 2^1)
        task_queue.update_status(task_id, "failed")
        r2 = task_queue.retry_with_backoff(task_id, "error 2")
        assert r2["retried"] is True
        assert r2["delay"] == 20.0

        # Third retry: 40s (10 * 2^2)
        task_queue.update_status(task_id, "failed")
        r3 = task_queue.retry_with_backoff(task_id, "error 3")
        assert r3["retried"] is True
        assert r3["delay"] == 40.0

    def test_retry_exceeds_max_retries(self, task_queue, sample_task):
        """Should fail when max_retries is exceeded."""
        sample_task.max_retries = 4
        task_id = task_queue.submit(sample_task)

        # max_retries=4, so 3 successful retries, then block
        for i in range(3):
            task_queue.update_status(task_id, "failed")
            result = task_queue.retry_with_backoff(task_id, f"error {i}")
            assert result["retried"] is True

        # 4th attempt should be blocked
        task_queue.update_status(task_id, "failed")
        result = task_queue.retry_with_backoff(task_id, "final error")
        assert result["retried"] is False

    def test_retry_non_failed_task(self, task_queue, sample_task):
        """Cannot retry a task that's not in failed status."""
        task_id = task_queue.submit(sample_task)
        task_queue.update_status(task_id, "pending")

        result = task_queue.retry_with_backoff(task_id, "error")
        assert result["retried"] is False
        assert "not failed" in result["reason"].lower()

    def test_retry_nonexistent_task(self, task_queue):
        """Should return error for nonexistent task."""
        result = task_queue.retry_with_backoff("nonexistent_task", "error")
        assert result["retried"] is False
        assert "not found" in result["reason"].lower()


class TestRecordFailure:
    """Tests for record_failure method."""

    def test_record_failure_increments_history(self, task_queue, sample_task):
        """Each failure should be recorded in failure_history."""
        task_id = task_queue.submit(sample_task)

        task_queue.record_failure(task_id, error="error 1")
        task_queue.record_failure(task_id, error="error 2")
        task_queue.record_failure(task_id, error="error 3")

        task = task_queue.get(task_id)
        assert len(task.failure_history) == 3
        assert task.failure_history[0]["error"] == "error 1"
        assert task.failure_history[1]["error"] == "error 2"
        assert task.failure_history[2]["error"] == "error 3"

    def test_record_failure_captures_metadata(self, task_queue, sample_task):
        """Failure history should capture attempt number and timestamp."""
        task_id = task_queue.submit(sample_task)

        task_queue.record_failure(task_id, error="test error", result={"code": 500})

        task = task_queue.get(task_id)
        assert len(task.failure_history) == 1
        entry = task.failure_history[0]
        assert entry["attempt"] == 1  # retry_count was 0, so attempt is 1
        assert entry["error"] == "test error"
        assert "timestamp" in entry
        assert entry["status_at_failure"] == "pending"  # initial status

    def test_record_failure_stores_result(self, task_queue, sample_task):
        """Result should be stored in task if provided."""
        task_id = task_queue.submit(sample_task)

        test_result = {"error": "timeout", "details": "connection lost"}
        task_queue.record_failure(task_id, error="error", result=test_result)

        task = task_queue.get(task_id)
        assert task.result == test_result


class TestGetRetryDelay:
    """Tests for get_retry_delay method."""

    def test_base_delay_when_no_retries(self, task_queue, sample_task):
        """First retry should use base delay."""
        task_id = task_queue.submit(sample_task)

        delay = task_queue.get_retry_delay(task_id)
        assert delay == 10.0

    def test_exponential_backoff_formula(self, task_queue, sample_task):
        """Verify backoff formula: base * factor^(retry_count-1)."""
        task_id = task_queue.submit(sample_task)
        task = task_queue.get(task_id)

        # Simulate retry_count values and verify delays
        for retry_count in [1, 2, 3, 4]:
            task.retry_count = retry_count
            expected = 10.0 * (2.0 ** (retry_count - 1))
            actual = task_queue.get_retry_delay(task_id)
            assert actual == expected, (
                f"retry_count={retry_count}: expected {expected}, got {actual}"
            )

    def test_custom_backoff_factor(self, task_queue):
        """Verify custom backoff factor is used."""
        task = Task(
            pipeline_id="test",
            role_id="dev",
            name="test",
            retry_delay_seconds=5.0,
            retry_backoff_factor=3.0,
        )
        task_id = task_queue.submit(task)
        t = task_queue.get(task_id)

        t.retry_count = 2
        delay = task_queue.get_retry_delay(task_id)
        assert delay == 5.0 * (3.0**1)  # 15.0


class TestIsRetryReady:
    """Tests for is_retry_ready method."""

    def test_ready_when_no_last_retry(self, task_queue, sample_task):
        """Task is ready to retry if it has never been retried."""
        task_id = task_queue.submit(sample_task)

        assert task_queue.is_retry_ready(task_id) is True

    def test_ready_when_delay_elapsed(self, task_queue, sample_task):
        """Task is ready when delay has elapsed."""
        task_id = task_queue.submit(sample_task)
        task = task_queue.get(task_id)

        task.last_retry_at = datetime.now() - timedelta(seconds=15)
        task.retry_delay_seconds = 10.0

        assert task_queue.is_retry_ready(task_id) is True

    def test_not_ready_when_delay_not_elapsed(self, task_queue, sample_task):
        """Task is not ready if delay hasn't elapsed."""
        task_id = task_queue.submit(sample_task)
        task = task_queue.get(task_id)

        task.last_retry_at = datetime.now() - timedelta(seconds=5)
        task.retry_delay_seconds = 10.0

        assert task_queue.is_retry_ready(task_id) is False

    def test_not_ready_when_max_retries_exceeded(self, task_queue, sample_task):
        """Task is not ready if max_retries exceeded."""
        task_id = task_queue.submit(sample_task)
        task = task_queue.get(task_id)

        task.retry_count = 3  # max_retries=3
        task.last_retry_at = None

        assert task_queue.is_retry_ready(task_id) is False


class TestGetRetryableTasks:
    """Tests for get_retryable_tasks method."""

    def test_finds_retryable_tasks(self, task_queue):
        """Should find failed tasks with retries remaining and delay elapsed."""
        task1 = Task(pipeline_id="p1", role_id="dev", name="t1", max_retries=3)
        task2 = Task(pipeline_id="p1", role_id="dev", name="t2", max_retries=2)
        task3 = Task(pipeline_id="p2", role_id="dev", name="t3", max_retries=3)

        t1_id = task_queue.submit(task1)
        t2_id = task_queue.submit(task2)
        t3_id = task_queue.submit(task3)

        # Set up retryable states
        task_queue.update_status(t1_id, "failed")
        task_queue.update_status(t2_id, "failed")
        task_queue.update_status(t3_id, "failed")

        task = task_queue.get(t2_id)
        task.retry_count = 2  # max_retries=2, so not retryable
        task_queue._save()

        retryable = task_queue.get_retryable_tasks(pipeline_id="p1")

        assert len(retryable) == 1
        assert retryable[0].id == t1_id

    def test_filters_by_pipeline_id(self, task_queue):
        """Should only return tasks for specified pipeline."""
        task1 = Task(pipeline_id="p1", role_id="dev", name="t1")
        task2 = Task(pipeline_id="p2", role_id="dev", name="t2")

        t1_id = task_queue.submit(task1)
        t2_id = task_queue.submit(task2)

        task_queue.update_status(t1_id, "failed")
        task_queue.update_status(t2_id, "failed")

        retryable_p1 = task_queue.get_retryable_tasks(pipeline_id="p1")
        retryable_p2 = task_queue.get_retryable_tasks(pipeline_id="p2")

        assert len(retryable_p1) == 1
        assert len(retryable_p2) == 1
        assert retryable_p1[0].id != retryable_p2[0].id

    def test_returns_all_when_no_pipeline_filter(self, task_queue):
        """Should return all retryable tasks when pipeline_id is None."""
        task1 = Task(pipeline_id="p1", role_id="dev", name="t1")
        task2 = Task(pipeline_id="p2", role_id="dev", name="t2")

        t1_id = task_queue.submit(task1)
        t2_id = task_queue.submit(task2)

        task_queue.update_status(t1_id, "failed")
        task_queue.update_status(t2_id, "failed")

        retryable = task_queue.get_retryable_tasks()

        assert len(retryable) == 2


class TestIncrementRetry:
    """Tests for increment_retry method (existing behavior)."""

    def test_increments_retry_count(self, task_queue, sample_task):
        """Should increment retry_count and set status to pending."""
        task_id = task_queue.submit(sample_task)
        task_queue.update_status(task_id, "failed")

        success = task_queue.increment_retry(task_id)

        assert success is True
        task = task_queue.get(task_id)
        assert task.retry_count == 1
        assert task.status == "pending"
        assert task.last_retry_at is not None

    def test_blocks_at_max_retries(self, task_queue, sample_task):
        """Should return False when max_retries reached."""
        task_id = task_queue.submit(sample_task)

        # Increment to max_retries-1
        for _ in range(2):
            task_queue.update_status(task_id, "failed")
            task_queue.increment_retry(task_id)

        # Third attempt should block (retry_count=3, max_retries=3)
        task_queue.update_status(task_id, "failed")
        success = task_queue.increment_retry(task_id)

        assert success is False
        task = task_queue.get(task_id)
        assert task.retry_count == 3
        assert task.status == "failed"  # Should not change to pending


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
