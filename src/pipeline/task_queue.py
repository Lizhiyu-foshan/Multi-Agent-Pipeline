"""
Task Queue - File-persisted task management with atomic writes.

Adapted from the reference implementation, keeping:
- Atomic file persistence (tempfile + os.replace)
- Priority-based task ordering
- Dependency resolution
- Retry management
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import Task, DateTimeEncoder

logger = logging.getLogger(__name__)


class TaskQueue:
    def __init__(self, state_file: str = None):
        if state_file is None:
            state_file = Path.cwd() / ".pipeline" / "state" / "task_queue.json"
        else:
            state_file = Path(state_file)
        self.state_file = state_file
        self.tasks: Dict[str, Task] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if not self.state_file.exists():
                return
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for tid, tdata in data.get("tasks", {}).items():
                    self.tasks[tid] = Task.from_dict(tdata)
                logger.info(f"Loaded {len(self.tasks)} tasks")
            except json.JSONDecodeError:
                self._backup_corrupted()
                self.tasks = {}
            except Exception as e:
                logger.error(f"Failed to load tasks: {e}")
                self.tasks = {}

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
                data = {
                    "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
                    "last_updated": datetime.now().isoformat(),
                }
                dir_name = os.path.dirname(self.state_file)
                fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(
                            data, f, indent=2, ensure_ascii=False, cls=DateTimeEncoder
                        )
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_path, str(self.state_file))
                except Exception:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                    raise
            except Exception as e:
                logger.error(f"Failed to save tasks: {e}")

    def _backup_corrupted(self):
        try:
            if self.state_file.exists():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = f"{self.state_file}.corrupted.{ts}"
                os.rename(str(self.state_file), backup)
        except Exception as e:
            logger.error(f"Backup failed: {e}")

    def submit(self, task: Task) -> str:
        with self._lock:
            if not task.id:
                import uuid

                task.id = f"task_{uuid.uuid4().hex[:8]}"
            task.status = "pending"
            task.created_at = datetime.now()
            self.tasks[task.id] = task
            self._save()
            logger.info(
                f"Submitted task: {task.id} ({task.name}) -> role {task.role_id}"
            )
            return task.id

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self.tasks.get(task_id)

    def get_next_for_role(self, role_id: str) -> Optional[Task]:
        with self._lock:
            role_tasks = [
                t
                for t in self.tasks.values()
                if t.role_id == role_id and t.status == "pending"
            ]
            ready = []
            for t in role_tasks:
                deps_ok = all(
                    self.tasks.get(d) and self.tasks[d].status == "completed"
                    for d in t.depends_on
                )
                if deps_ok:
                    ready.append(t)
            if not ready:
                return None
            priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
            ready.sort(
                key=lambda t: (
                    priority_order.get(t.priority, 2),
                    t.created_at or datetime.min,
                )
            )
            return ready[0]

    def update_status(self, task_id: str, status: str, result: Dict = None):
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                logger.warning(f"Task {task_id} not found for status update")
                return
            task.status = status
            if result:
                task.result = result
            if status == "processing":
                task.started_at = datetime.now()
            elif status in ("completed", "failed"):
                task.completed_at = datetime.now()
                if status == "failed":
                    error_msg = ""
                    if result and isinstance(result, dict):
                        error_msg = str(result.get("error", ""))[:500]
                    self.record_failure(task_id, error=error_msg)
            self._save()
            logger.info(f"Task {task_id}: {status}")

    def increment_step(self, task_id: str) -> int:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return 0
            task.step_count += 1
            self._save()
            return task.step_count

    def increment_retry(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            task.retry_count += 1
            if task.retry_count >= task.max_retries:
                logger.warning(f"Task {task_id} exceeded max retries")
                return False
            task.status = "pending"
            task.last_retry_at = datetime.now()
            self._save()
            return True

    def record_failure(self, task_id: str, error: str = "", result: Dict = None):
        """Record a task failure in its history for retry diagnostics."""
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task.failure_history.append(
                {
                    "attempt": task.retry_count + 1,
                    "error": error[:500],
                    "timestamp": datetime.now().isoformat(),
                    "status_at_failure": task.status,
                }
            )
            if result:
                task.result = result
            self._save()

    def get_retry_delay(self, task_id: str) -> float:
        """Calculate retry delay with exponential backoff."""
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return 0.0
            delay = task.retry_delay_seconds
            if task.retry_backoff_factor > 1 and task.retry_count > 0:
                delay *= task.retry_backoff_factor ** (task.retry_count - 1)
            return delay

    def is_retry_ready(self, task_id: str) -> bool:
        """Check if a failed task's retry delay has elapsed."""
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.retry_count >= task.max_retries:
                return False
            if not task.last_retry_at:
                return True
            delay = self.get_retry_delay(task_id)
            elapsed = (datetime.now() - task.last_retry_at).total_seconds()
            return elapsed >= delay

    def get_retryable_tasks(self, pipeline_id: str = None) -> List[Task]:
        """Get failed tasks that can be retried (delay elapsed, retries remaining)."""
        with self._lock:
            candidates = []
            for task in self.tasks.values():
                if task.status != "failed":
                    continue
                if task.retry_count >= task.max_retries:
                    continue
                if pipeline_id and task.pipeline_id != pipeline_id:
                    continue
                if self.is_retry_ready(task.id):
                    candidates.append(task)
            return candidates

    def retry_with_backoff(self, task_id: str, error: str = "") -> Dict:
        """
        Attempt to retry a failed task with backoff.

        Returns dict with:
        - 'retried': bool - whether retry was initiated
        - 'delay': float - delay before task becomes eligible
        - 'attempts_remaining': int
        - 'reason': str - explanation if not retried
        """
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return {"retried": False, "reason": "Task not found"}

            if task.status != "failed":
                return {
                    "retried": False,
                    "reason": f"Task status is {task.status}, not failed",
                }

            self.record_failure(task_id, error)

            success = self.increment_retry(task_id)
            if not success:
                return {
                    "retried": False,
                    "reason": "Max retries exceeded or increment failed",
                    "attempts_remaining": max(0, task.max_retries - task.retry_count),
                }

            delay = self.get_retry_delay(task_id)

            return {
                "retried": True,
                "delay": delay,
                "attempts_remaining": task.max_retries - task.retry_count,
                "task_id": task_id,
                "retry_count": task.retry_count,
            }

    def get_statistics(self) -> Dict[str, int]:
        with self._lock:
            stats = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
            for t in self.tasks.values():
                stats[t.status] = stats.get(t.status, 0) + 1
            return stats

    def get_by_pipeline(self, pipeline_id: str) -> List[Task]:
        with self._lock:
            return [t for t in self.tasks.values() if t.pipeline_id == pipeline_id]

    def get_by_role(self, role_id: str) -> List[Task]:
        with self._lock:
            return [t for t in self.tasks.values() if t.role_id == role_id]

    def get_by_status(self, status: str) -> List[Task]:
        with self._lock:
            return [t for t in self.tasks.values() if t.status == status]

    def list_all(self) -> List[Task]:
        with self._lock:
            return list(self.tasks.values())

    def delete(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self.tasks:
                del self.tasks[task_id]
                self._save()
                return True
            return False

    def persist(self):
        """Persist current task queue state to disk."""
        self._save()
