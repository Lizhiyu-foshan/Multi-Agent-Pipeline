"""
Resource Scheduler API - Layer 1 facade.

Unified API for Layer 2 (PipelineOrchestrator) and Layer 0 (Workers).
Coordinates TaskQueue, RoleRegistry, and LockManager.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .task_queue import TaskQueue
from .role_registry import RoleRegistry
from .lock_manager import LockManager
from .models import Task, Role, RoleConfig

logger = logging.getLogger(__name__)


class ResourceSchedulerAPI:
    def __init__(self, state_dir: str = None, lock_dir: str = None):
        if state_dir is None:
            from pathlib import Path

            base = Path.cwd() / ".pipeline" / "state"
            lock_base = Path.cwd() / ".pipeline" / "locks"
        else:
            from pathlib import Path

            base = Path(state_dir)
            lock_base = Path(lock_dir) if lock_dir else base.parent / "locks"

        self.registry = RoleRegistry(str(base / "roles.json"))
        self.lock_manager = LockManager(str(lock_base))
        self.task_queue = TaskQueue(str(base / "task_queue.json"))

    # ===== Layer 2 (Orchestrator) API =====

    def get_roles_status(self) -> Dict[str, Any]:
        return {
            "roles": self.registry.get_status(),
            "timestamp": datetime.now().isoformat(),
        }

    def submit_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(task_data, Task):
            task = task_data
        else:
            task = Task(
                pipeline_id=task_data.get("pipeline_id", ""),
                role_id=task_data.get("role_id", ""),
                name=task_data.get("name", ""),
                description=task_data.get("description", ""),
                priority=task_data.get("priority", "P2"),
                depends_on=task_data.get("depends_on", []),
                max_retries=task_data.get("max_retries", 3),
                max_steps=task_data.get("max_steps", 50),
                context_injection=task_data.get("context_injection"),
            )

        if not self.registry.get(task.role_id):
            return {
                "success": False,
                "task_id": None,
                "message": f"Role {task.role_id} does not exist",
            }

        task_id = self.task_queue.submit(task)
        self.registry.add_to_queue(task.role_id, task_id)

        return {
            "success": True,
            "task_id": task_id,
            "message": "Task submitted",
        }

    def submit_tasks_batch(self, tasks: List[Dict[str, Any]]) -> List[str]:
        task_ids = []
        for td in tasks:
            result = self.submit_task(td)
            if result["success"]:
                task_ids.append(result["task_id"])
            else:
                logger.warning(f"Failed to submit task: {result['message']}")
        return task_ids

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = self.task_queue.get(task_id)
        if not task:
            return None
        return {
            "task_id": task.id,
            "name": task.name,
            "status": task.status,
            "priority": task.priority,
            "role_id": task.role_id,
            "pipeline_id": task.pipeline_id,
            "depends_on": task.depends_on,
            "retry_count": task.retry_count,
            "step_count": task.step_count,
            "result": task.result,
            "artifacts": task.artifacts,
        }

    def get_pipeline_tasks(self, pipeline_id: str) -> List[Dict[str, Any]]:
        tasks = self.task_queue.get_by_pipeline(pipeline_id)
        return [
            {
                "task_id": t.id,
                "name": t.name,
                "status": t.status,
                "role_id": t.role_id,
                "priority": t.priority,
            }
            for t in tasks
        ]

    # ===== Layer 0 (Worker) API =====

    def acquire_lock(self, role_id: str, task_id: str) -> Dict[str, Any]:
        acquired = self.lock_manager.acquire(role_id, task_id)
        if acquired:
            self.registry.update_status(role_id, "busy", task_id)
            self.task_queue.update_status(task_id, "processing")
        return {
            "acquired": acquired,
            "role_id": role_id,
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
        }

    def release_lock(self, role_id: str) -> Dict[str, Any]:
        released = self.lock_manager.release(role_id)
        if released:
            self.registry.update_status(role_id, "idle", None)
        return {
            "released": released,
            "role_id": role_id,
            "timestamp": datetime.now().isoformat(),
        }

    def poll_task(self, role_id: str) -> Optional[Dict[str, Any]]:
        lock_info = self.lock_manager.get_lock_info(role_id)
        if not lock_info:
            return None
        task = self.task_queue.get_next_for_role(role_id)
        if task:
            return {
                "task_id": task.id,
                "pipeline_id": task.pipeline_id,
                "name": task.name,
                "description": task.description,
                "priority": task.priority,
                "depends_on": task.depends_on,
                "step_count": task.step_count,
                "max_steps": task.max_steps,
                "context_injection": task.context_injection,
            }
        return None

    def complete_task(
        self, task_id: str, success: bool, result: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        status = "completed" if success else "failed"
        self.task_queue.update_status(task_id, status, result)

        task = self.task_queue.get(task_id)
        if task:
            if task.started_at and task.completed_at:
                duration = (task.completed_at - task.started_at).total_seconds()
                self.registry.update_metrics(task.role_id, duration, success)
            self.registry.remove_from_queue(task.role_id, task_id)

        return {
            "task_id": task_id,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }

    def update_task_artifacts(self, task_id: str, artifacts: Dict[str, Any]):
        task = self.task_queue.get(task_id)
        if task:
            task.artifacts.update(artifacts)
            self.task_queue._save()

    def increment_task_step(self, task_id: str) -> int:
        return self.task_queue.increment_step(task_id)

    # ===== Maintenance =====

    def cleanup(self) -> Dict[str, Any]:
        cleaned_locks = self.lock_manager.cleanup_expired()
        return {
            "cleaned_locks": cleaned_locks,
            "timestamp": datetime.now().isoformat(),
        }

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "roles": {
                "total": len(self.registry.list_all()),
                "idle": len(self.registry.get_idle_roles()),
                "busy": len(
                    [r for r in self.registry.list_all() if r.status == "busy"]
                ),
            },
            "tasks": self.task_queue.get_statistics(),
            "locks": {"active": len(self.lock_manager.get_all_locks())},
            "timestamp": datetime.now().isoformat(),
        }
