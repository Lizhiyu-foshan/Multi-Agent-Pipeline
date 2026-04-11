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
        self._load()

    def _load(self):
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
        if not task.id:
            import uuid

            task.id = f"task_{uuid.uuid4().hex[:8]}"
        task.status = "pending"
        task.created_at = datetime.now()
        self.tasks[task.id] = task
        self._save()
        logger.info(f"Submitted task: {task.id} ({task.name}) -> role {task.role_id}")
        return task.id

    def get(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def get_next_for_role(self, role_id: str) -> Optional[Task]:
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
        task = self.tasks.get(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found for status update")
            return
        task.status = status
        if status == "processing":
            task.started_at = datetime.now()
        elif status in ("completed", "failed"):
            task.completed_at = datetime.now()
            if result:
                task.result = result
        self._save()
        logger.info(f"Task {task_id}: {status}")

    def increment_step(self, task_id: str) -> int:
        task = self.tasks.get(task_id)
        if not task:
            return 0
        task.step_count += 1
        self._save()
        return task.step_count

    def increment_retry(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        task.retry_count += 1
        if task.retry_count >= task.max_retries:
            logger.warning(f"Task {task_id} exceeded max retries")
            return False
        task.status = "pending"
        self._save()
        return True

    def get_statistics(self) -> Dict[str, int]:
        stats = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
        for t in self.tasks.values():
            stats[t.status] = stats.get(t.status, 0) + 1
        return stats

    def get_by_pipeline(self, pipeline_id: str) -> List[Task]:
        return [t for t in self.tasks.values() if t.pipeline_id == pipeline_id]

    def get_by_role(self, role_id: str) -> List[Task]:
        return [t for t in self.tasks.values() if t.role_id == role_id]

    def get_by_status(self, status: str) -> List[Task]:
        return [t for t in self.tasks.values() if t.status == status]

    def list_all(self) -> List[Task]:
        return list(self.tasks.values())

    def delete(self, task_id: str) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            self._save()
            return True
        return False
