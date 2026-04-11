"""
Base Worker - Prompt-passing execution model.

Unlike the reference (threaded polling), this worker:
- Returns prompt requests to the orchestrator (prompt-passing protocol)
- Does NOT run in a thread - the orchestrator drives execution
- Integrates with the scheduler API for task lifecycle management
- Supports step-by-step execution with max_steps limits
"""

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import Task
from .scheduler_api import ResourceSchedulerAPI

logger = logging.getLogger(__name__)


class TaskResult:
    def __init__(
        self,
        success: bool,
        output: Any = None,
        error_message: str = None,
        artifacts: Dict[str, Any] = None,
        pending_model_request: Dict[str, Any] = None,
    ):
        self.success = success
        self.output = output
        self.error_message = error_message
        self.artifacts = artifacts or {}
        self.pending_model_request = pending_model_request
        self.completed_at = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        d = {
            "success": self.success,
            "output": self.output,
            "error_message": self.error_message,
            "artifacts": self.artifacts,
            "completed_at": self.completed_at,
        }
        if self.pending_model_request:
            d["pending_model_request"] = self.pending_model_request
        return d


class BaseWorker(ABC):
    """
    Base class for role workers in the prompt-passing model.

    Execution flow (driven by orchestrator):
    1. Orchestrator calls worker.execute_step(task_data, context)
    2. Worker returns either:
       - A final TaskResult (task done)
       - A pending_model_request (needs AI inference)
    3. If pending, orchestrator passes to opencode agent, gets response
    4. Orchestrator calls worker.continue_step(task_data, response)
    5. Repeat until final result or max_steps reached
    """

    def __init__(
        self,
        role_id: str,
        role_name: str,
        capabilities: List[str],
        scheduler: ResourceSchedulerAPI = None,
    ):
        self.role_id = role_id
        self.role_name = role_name
        self.capabilities = capabilities
        self.scheduler = scheduler

        self.stats = {
            "tasks_completed": 0,
            "tasks_failed": 0,
            "total_steps": 0,
            "total_execution_seconds": 0.0,
        }

    def execute_task(
        self, task_data: Dict[str, Any], context: Dict[str, Any] = None
    ) -> TaskResult:
        """
        Execute a task. Returns TaskResult which may be:
        - Final result (success/failure)
        - Pending (needs model inference via prompt-passing)
        """
        task_id = task_data.get("task_id", "")
        max_steps = task_data.get("max_steps", 50)
        logger.info(f"[{self.role_id}] Starting task: {task_id}")

        try:
            result = self.execute_step(task_data, context or {})

            if result.pending_model_request:
                return result

            if result.success:
                self.stats["tasks_completed"] += 1
            else:
                self.stats["tasks_failed"] += 1

            return result

        except Exception as e:
            logger.error(f"[{self.role_id}] Task {task_id} exception: {e}")
            self.stats["tasks_failed"] += 1
            return TaskResult(success=False, error_message=str(e))

    def continue_task(
        self,
        task_data: Dict[str, Any],
        model_response: str,
        context: Dict[str, Any] = None,
    ) -> TaskResult:
        """
        Continue a task after receiving model response (prompt-passing callback).
        """
        task_id = task_data.get("task_id", "")
        try:
            result = self.continue_step(task_data, model_response, context or {})

            if result.pending_model_request:
                return result

            if result.success:
                self.stats["tasks_completed"] += 1
            else:
                self.stats["tasks_failed"] += 1

            return result

        except Exception as e:
            logger.error(f"[{self.role_id}] Continue task {task_id} exception: {e}")
            self.stats["tasks_failed"] += 1
            return TaskResult(success=False, error_message=str(e))

    @abstractmethod
    def execute_step(
        self, task_data: Dict[str, Any], context: Dict[str, Any]
    ) -> TaskResult:
        """Execute first step of a task. Override in subclasses."""
        pass

    def continue_step(
        self,
        task_data: Dict[str, Any],
        model_response: str,
        context: Dict[str, Any],
    ) -> TaskResult:
        """
        Continue after model response. Default implementation treats
        the response as the final output.
        """
        return TaskResult(
            success=True,
            output=model_response,
            artifacts={"response": model_response},
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "capabilities": self.capabilities,
            "stats": self.stats,
        }


class SkillProxyWorker(BaseWorker):
    """
    Worker that delegates to a skill adapter.

    For prompt-passing integration, it wraps the skill's execute()
    and returns the result as a TaskResult.
    """

    def __init__(
        self,
        role_id: str,
        role_name: str,
        capabilities: List[str],
        skill_adapter: Any,
        scheduler: ResourceSchedulerAPI = None,
    ):
        super().__init__(role_id, role_name, capabilities, scheduler)
        self.skill_adapter = skill_adapter

    def execute_step(
        self, task_data: Dict[str, Any], context: Dict[str, Any]
    ) -> TaskResult:
        try:
            description = task_data.get("description", task_data.get("name", ""))
            skill_context = {
                "task_description": description,
                "project_path": context.get("project_path", ""),
                "role_id": self.role_id,
                "role_name": self.role_name,
                "capabilities": self.capabilities,
                "task_id": task_data.get("task_id", ""),
                "pipeline_id": task_data.get("pipeline_id", ""),
                **context,
            }

            result = self.skill_adapter.execute(description, skill_context)

            pending = (
                result.get("pending_model_request")
                if isinstance(result, dict)
                else None
            )

            return TaskResult(
                success=result.get("success", True),
                output=result.get("output", ""),
                error_message=result.get("error"),
                artifacts=result.get("artifacts", {}),
                pending_model_request=pending,
            )

        except Exception as e:
            return TaskResult(success=False, error_message=str(e))

    def continue_step(
        self,
        task_data: Dict[str, Any],
        model_response: str,
        context: Dict[str, Any],
    ) -> TaskResult:
        if hasattr(self.skill_adapter, "continue_execution"):
            try:
                result = self.skill_adapter.continue_execution(model_response, context)
                return TaskResult(
                    success=result.get("success", True),
                    output=result.get("output", model_response),
                    artifacts=result.get("artifacts", {}),
                )
            except Exception as e:
                return TaskResult(success=False, error_message=str(e))
        return TaskResult(
            success=True,
            output=model_response,
            artifacts={"response": model_response},
        )


class WorkerPool:
    """
    Worker pool - manages worker instances by role_id.
    """

    def __init__(self):
        self.workers: Dict[str, BaseWorker] = {}

    def register(self, worker: BaseWorker) -> bool:
        if worker.role_id in self.workers:
            return False
        self.workers[worker.role_id] = worker
        logger.info(f"Registered worker: {worker.role_id}")
        return True

    def get(self, role_id: str) -> Optional[BaseWorker]:
        return self.workers.get(role_id)

    def get_all(self) -> Dict[str, BaseWorker]:
        return dict(self.workers)

    def get_status(self) -> Dict[str, Any]:
        return {rid: w.get_status() for rid, w in self.workers.items()}

    def create_from_skills(
        self,
        scheduler: ResourceSchedulerAPI,
        skills: Dict[str, Any],
        role_definitions: List[Dict[str, Any]],
    ) -> List[str]:
        """
        Create workers dynamically from skill adapters and role definitions.
        Returns list of registered role_ids.
        """
        registered = []
        for rdef in role_definitions:
            role_id = rdef["type"]
            role_name = rdef.get("name", role_id)
            capabilities = rdef.get("capabilities", ["general"])

            skill_type = rdef.get("skill", "superpowers")
            skill_adapter = skills.get(skill_type)

            if skill_adapter:
                worker = SkillProxyWorker(
                    role_id=role_id,
                    role_name=role_name,
                    capabilities=capabilities,
                    skill_adapter=skill_adapter,
                    scheduler=scheduler,
                )
                self.register(worker)
                registered.append(role_id)
            else:
                logger.warning(
                    f"No skill adapter found for role {role_id} (skill: {skill_type})"
                )
        return registered
