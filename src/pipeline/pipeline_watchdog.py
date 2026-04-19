"""
Pipeline Watchdog - Health monitor for long-running pipelines.

Detects:
- Pipeline timeout (exceeds max_duration_hours)
- Task stall (tasks stuck in 'processing' beyond threshold)
- Session leak (orphaned sessions with no activity)
- Progress stall (no task completion within interval)

Actions:
- Log warning with diagnostics
- Emit on_recover lifecycle event
- Auto-recover via orchestrator.recover() if configured
- Notify via callback for human intervention
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    STALLED = "stalled"
    TIMED_OUT = "timed_out"
    RECOVERED = "recovered"
    FAILED = "failed"


@dataclass
class WatchdogConfig:
    check_interval_seconds: float = 60.0
    task_stall_threshold_seconds: float = 300.0
    session_idle_threshold_seconds: float = 1800.0
    progress_stall_threshold_seconds: float = 600.0
    auto_recover: bool = False
    max_auto_recover_attempts: int = 2
    auto_retry_stalled_tasks: bool = False
    auto_retry_idle_model_request: bool = False
    model_request_retry_max_attempts: int = 3
    model_request_retry_cooldown_seconds: float = 120.0
    model_request_retry_reason: str = "watchdog_session_idle"
    on_stall_callback: Optional[Callable] = None
    on_timeout_callback: Optional[Callable] = None


@dataclass
class HealthCheckResult:
    pipeline_id: str
    status: HealthStatus
    issues: List[str] = field(default_factory=list)
    stalled_tasks: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    task_stats: Dict[str, int] = field(default_factory=dict)
    active_session_id: str = ""
    session_idle_seconds: float = 0.0
    checked_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.checked_at:
            self.checked_at = datetime.now()


class PipelineWatchdog:
    """
    Monitors pipeline health and detects stalls/timeouts.

    Usage:
        watchdog = PipelineWatchdog(orchestrator=orch, config=WatchdogConfig())
        watchdog.start()  # starts background thread
        # ... pipeline runs ...
        watchdog.stop()

    Or use synchronously:
        result = watchdog.check(pipeline_id)
        if result.status != HealthStatus.HEALTHY:
            watchdog.take_action(result)
    """

    def __init__(
        self,
        orchestrator: Any = None,
        config: WatchdogConfig = None,
    ):
        self.orchestrator = orchestrator
        self.config = config or WatchdogConfig()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._recovery_attempts: Dict[str, int] = {}
        self._model_retry_attempts: Dict[str, int] = {}
        self._last_model_retry_at: Dict[str, datetime] = {}
        self._last_progress_time: Dict[str, datetime] = {}
        self._last_completed_counts: Dict[str, int] = {}
        self._monitored_pipelines: set = set()

    @property
    def is_running(self) -> bool:
        return self._monitor_thread is not None and self._monitor_thread.is_alive()

    @property
    def monitored_count(self) -> int:
        return len(self._monitored_pipelines)

    def register_pipeline(self, pipeline_id: str):
        self._monitored_pipelines.add(pipeline_id)
        if pipeline_id not in self._last_progress_time:
            self._last_progress_time[pipeline_id] = datetime.now()
        if pipeline_id not in self._last_completed_counts:
            self._last_completed_counts[pipeline_id] = 0

    def unregister_pipeline(self, pipeline_id: str):
        self._monitored_pipelines.discard(pipeline_id)
        self._recovery_attempts.pop(pipeline_id, None)
        self._model_retry_attempts.pop(pipeline_id, None)
        self._last_model_retry_at.pop(pipeline_id, None)
        self._last_progress_time.pop(pipeline_id, None)
        self._last_completed_counts.pop(pipeline_id, None)

    def check(self, pipeline_id: str) -> HealthCheckResult:
        """Perform a single health check on a pipeline."""
        if not self.orchestrator:
            return HealthCheckResult(
                pipeline_id=pipeline_id,
                status=HealthStatus.FAILED,
                issues=["No orchestrator configured"],
            )

        pipeline = self.orchestrator.pipelines.get(pipeline_id)
        if not pipeline:
            return HealthCheckResult(
                pipeline_id=pipeline_id,
                status=HealthStatus.FAILED,
                issues=[f"Pipeline {pipeline_id} not found"],
            )

        result = HealthCheckResult(
            pipeline_id=pipeline_id,
            status=HealthStatus.HEALTHY,
        )

        if pipeline.state in ("completed", "failed", "idle"):
            return result

        pipe_tasks = self.orchestrator.scheduler.task_queue.get_by_pipeline(pipeline_id)
        task_stats = self._pipeline_task_stats(pipe_tasks)
        result.task_stats = task_stats

        if pipeline.started_at:
            elapsed = (datetime.now() - pipeline.started_at).total_seconds()
            result.elapsed_seconds = elapsed

            max_seconds = pipeline.max_duration_hours * 3600
            if elapsed > max_seconds:
                result.status = HealthStatus.TIMED_OUT
                result.issues.append(
                    f"Pipeline exceeded {pipeline.max_duration_hours}h "
                    f"(elapsed: {elapsed / 3600:.1f}h)"
                )
                return result

        stalled = []
        for task in pipe_tasks:
            if task.status == "processing" and task.started_at:
                processing_time = (datetime.now() - task.started_at).total_seconds()
                if processing_time > self.config.task_stall_threshold_seconds:
                    stalled.append(task.id)

        if stalled:
            result.stalled_tasks = stalled
            result.status = HealthStatus.STALLED
            result.issues.append(
                f"{len(stalled)} task(s) stalled in processing: "
                f"{', '.join(stalled[:5])}"
            )

        completed_count = task_stats.get("completed", 0)
        prev_count = self._last_completed_counts.get(pipeline_id, 0)
        now = datetime.now()

        if pipeline_id not in self._last_progress_time:
            self._last_progress_time[pipeline_id] = now
        if pipeline_id not in self._last_completed_counts:
            self._last_completed_counts[pipeline_id] = completed_count
            prev_count = completed_count

        if completed_count > prev_count:
            self._last_progress_time[pipeline_id] = now
            self._last_completed_counts[pipeline_id] = completed_count
        else:
            last_progress = self._last_progress_time.get(pipeline_id)
            if last_progress:
                no_progress_seconds = (now - last_progress).total_seconds()
                if no_progress_seconds > self.config.progress_stall_threshold_seconds:
                    if result.status == HealthStatus.HEALTHY:
                        result.status = HealthStatus.WARNING
                    result.issues.append(
                        f"No task completion in {no_progress_seconds:.0f}s"
                    )

        self._check_session_idle(pipeline_id, result)

        return result

    def _pipeline_task_stats(self, pipe_tasks: List[Any]) -> Dict[str, int]:
        stats = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
        for t in pipe_tasks:
            stats[t.status] = stats.get(t.status, 0) + 1
        stats["total"] = len(pipe_tasks)
        return stats

    def _check_session_idle(self, pipeline_id: str, result: HealthCheckResult):
        if not self.orchestrator:
            return
        if not hasattr(self.orchestrator, "session_manager"):
            return
        sm = getattr(self.orchestrator, "session_manager", None)
        if not sm or not hasattr(sm, "load_by_pipeline"):
            return
        try:
            session = sm.load_by_pipeline(pipeline_id)
        except Exception:
            return
        if not session or not getattr(session, "last_active_at", None):
            return

        idle_seconds = (datetime.now() - session.last_active_at).total_seconds()
        result.active_session_id = getattr(session, "session_id", "")
        result.session_idle_seconds = idle_seconds
        if idle_seconds > self.config.session_idle_threshold_seconds:
            if result.status == HealthStatus.HEALTHY:
                result.status = HealthStatus.WARNING
            result.issues.append(
                f"Active model session idle for {idle_seconds:.0f}s "
                f"(threshold: {self.config.session_idle_threshold_seconds:.0f}s)"
            )

    def check_all(self) -> List[HealthCheckResult]:
        """Check health of all registered pipelines."""
        results = []
        if not self.orchestrator:
            return results
        for pid in list(self._monitored_pipelines):
            results.append(self.check(pid))
        return results

    def take_action(self, health: HealthCheckResult) -> Dict[str, Any]:
        """Take corrective action based on health check result."""
        actions_taken = []

        if health.status == HealthStatus.HEALTHY:
            return {"actions": [], "status": "healthy"}

        if health.status == HealthStatus.TIMED_OUT:
            if self.config.on_timeout_callback:
                try:
                    self.config.on_timeout_callback(health)
                    actions_taken.append("timeout_callback_executed")
                except Exception as e:
                    logger.error(f"Timeout callback failed: {e}")

            if self.orchestrator:
                try:
                    self.orchestrator._handle_timeout(
                        self.orchestrator.pipelines.get(health.pipeline_id)
                    )
                    actions_taken.append("timeout_handled")
                except Exception as e:
                    logger.error(f"Timeout handling failed: {e}")

        elif health.status == HealthStatus.STALLED:
            if self.config.auto_retry_stalled_tasks and self.orchestrator:
                for task_id in health.stalled_tasks:
                    task = self.orchestrator.scheduler.task_queue.get(task_id)
                    if task and task.retry_count < task.max_retries:
                        self.orchestrator.scheduler.task_queue.increment_retry(task_id)
                        actions_taken.append(f"retry_stalled:{task_id}")

            if self.config.on_stall_callback:
                try:
                    self.config.on_stall_callback(health)
                    actions_taken.append("stall_callback_executed")
                except Exception as e:
                    logger.error(f"Stall callback failed: {e}")

        if (
            self.config.auto_retry_idle_model_request
            and self.orchestrator
            and health.status in (HealthStatus.WARNING, HealthStatus.STALLED)
            and health.active_session_id
            and hasattr(self.orchestrator, "retry_model_request")
        ):
            attempts = self._model_retry_attempts.get(health.pipeline_id, 0)
            last_retry = self._last_model_retry_at.get(health.pipeline_id)
            cooldown_ok = (
                last_retry is None
                or (datetime.now() - last_retry).total_seconds()
                >= self.config.model_request_retry_cooldown_seconds
            )

            if attempts < self.config.model_request_retry_max_attempts and cooldown_ok:
                try:
                    retry_result = self.orchestrator.retry_model_request(
                        health.active_session_id,
                        reason=self.config.model_request_retry_reason,
                    )
                    action = retry_result.get("action")
                    if action == "model_request":
                        attempts += 1
                        self._model_retry_attempts[health.pipeline_id] = attempts
                        self._last_model_retry_at[health.pipeline_id] = datetime.now()
                        actions_taken.append(
                            f"retry_model_request:{health.active_session_id}:attempt_{attempts}"
                        )
                    elif action == "model_request_retry_exhausted":
                        self._model_retry_attempts[health.pipeline_id] = (
                            self.config.model_request_retry_max_attempts
                        )
                        actions_taken.append(
                            f"retry_model_request_exhausted:{health.active_session_id}"
                        )
                    elif retry_result.get("error"):
                        actions_taken.append(
                            f"retry_model_request_failed:{retry_result.get('error')}"
                        )
                except Exception as e:
                    logger.error(f"Idle model request retry failed: {e}")
                    actions_taken.append("retry_model_request_exception")

        if self.config.auto_recover and self.orchestrator:
            attempts = self._recovery_attempts.get(health.pipeline_id, 0)
            if attempts < self.config.max_auto_recover_attempts:
                try:
                    recover_result = self.orchestrator.recover(
                        health.pipeline_id, strategy="clean"
                    )
                    if recover_result.get("recovered"):
                        self._recovery_attempts[health.pipeline_id] = attempts + 1
                        actions_taken.append(f"auto_recovered:attempt_{attempts + 1}")
                except Exception as e:
                    logger.error(f"Auto-recover failed: {e}")

        self.orchestrator._emit_lifecycle(
            "on_recover",
            {
                "pipeline_id": health.pipeline_id,
                "health_status": health.status,
                "actions_taken": actions_taken,
                "issues": health.issues,
            },
        )

        return {
            "actions": actions_taken,
            "status": health.status,
            "pipeline_id": health.pipeline_id,
        }

    def start(self):
        """Start the background monitoring thread."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="PipelineWatchdog"
        )
        self._monitor_thread.start()
        logger.info(
            f"Watchdog started (interval={self.config.check_interval_seconds}s)"
        )

    def stop(self):
        """Stop the background monitoring thread."""
        self._stop_event.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)
            self._monitor_thread = None
        logger.info("Watchdog stopped")

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                results = self.check_all()
                for health in results:
                    if health.status != HealthStatus.HEALTHY:
                        logger.warning(
                            f"Watchdog: pipeline {health.pipeline_id} "
                            f"status={health.status} issues={health.issues}"
                        )
                        self.take_action(health)
            except Exception as e:
                logger.error(f"Watchdog monitor error: {e}")

            self._stop_event.wait(timeout=self.config.check_interval_seconds)

    def get_status(self) -> Dict[str, Any]:
        """Return current watchdog status."""
        return {
            "running": self._monitor_thread is not None
            and self._monitor_thread.is_alive(),
            "config": {
                "check_interval": self.config.check_interval_seconds,
                "task_stall_threshold": self.config.task_stall_threshold_seconds,
                "auto_recover": self.config.auto_recover,
                "max_auto_recover_attempts": self.config.max_auto_recover_attempts,
                "auto_retry_idle_model_request": self.config.auto_retry_idle_model_request,
                "model_request_retry_max_attempts": self.config.model_request_retry_max_attempts,
                "model_request_retry_cooldown_seconds": self.config.model_request_retry_cooldown_seconds,
            },
            "recovery_attempts": dict(self._recovery_attempts),
            "model_retry_attempts": dict(self._model_retry_attempts),
        }
