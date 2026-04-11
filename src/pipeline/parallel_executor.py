"""
ParallelExecutor - True parallel task execution within the pipeline.

Bridges the gap between "dispatch decision" and "actual execution".
Uses ThreadPoolExecutor to run multiple skill tasks concurrently,
without depending on an external agent to issue Task tool calls.

Execution model:
    1. Orchestrator finds >=2 ready tasks (no dependencies)
    2. ParallelExecutor executes each via skill adapter in a thread
    3. Results collected, artifacts stored, tasks marked complete
    4. Orchestrator proceeds to CHECK phase

Thread safety:
    - scheduler.complete_task() uses file-level locks (safe)
    - context.store_artifact() writes per task_id (safe)
    - pipeline object: read-only during execution (safe)
    - lifecycle hooks: user responsibility (documented)

Fallback:
    - If ThreadPoolExecutor fails, falls back to sequential execution
    - Tasks needing model interaction get their request queued for later
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParallelResult:
    task_id: str
    success: bool
    artifacts: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    had_model_request: bool = False
    model_request: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "task_id": self.task_id,
            "success": self.success,
            "artifacts": self.artifacts,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "had_model_request": self.had_model_request,
        }
        if self.error:
            d["error"] = self.error
        if self.model_request:
            d["model_request"] = self.model_request
        return d


@dataclass
class ParallelBatchResult:
    results: List[ParallelResult] = field(default_factory=list)
    total_time_ms: float = 0.0
    parallelism: int = 0
    fallback_used: bool = False

    @property
    def succeeded(self) -> List[ParallelResult]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> List[ParallelResult]:
        return [r for r in self.results if not r.success]

    @property
    def pending_model(self) -> List[ParallelResult]:
        return [r for r in self.results if r.had_model_request]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": len(self.results),
            "succeeded": len(self.succeeded),
            "failed": len(self.failed),
            "pending_model": len(self.pending_model),
            "total_time_ms": round(self.total_time_ms, 2),
            "parallelism": self.parallelism,
            "fallback_used": self.fallback_used,
            "results": [r.to_dict() for r in self.results],
        }


class ParallelExecutor:
    """
    Executes multiple pipeline tasks concurrently.

    Usage:
        executor = ParallelExecutor(max_workers=3)
        batch = executor.execute_batch(
            tasks=ready_tasks,
            skill_execute_fn=lambda task: skill_adapter.execute(...),
            on_complete_fn=lambda task_id, result: ...,
        )
    """

    def __init__(self, max_workers: int = 3, timeout_per_task: float = 120.0):
        self.max_workers = max_workers
        self.timeout_per_task = timeout_per_task
        self._lock = threading.Lock()
        self._execution_log: List[Dict[str, Any]] = []

    def execute_batch(
        self,
        tasks: List[Dict[str, Any]],
        skill_execute_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        on_complete_fn: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> ParallelBatchResult:
        """
        Execute multiple tasks in parallel.

        Args:
            tasks: List of task data dicts (task_id, skill, role_id, etc.)
            skill_execute_fn: Callable that takes task_data, returns skill result dict
            on_complete_fn: Optional callback(task_id, result) after each task

        Returns:
            ParallelBatchResult with all outcomes
        """
        batch_start = time.time()
        parallelism = min(len(tasks), self.max_workers)

        if len(tasks) == 0:
            return ParallelBatchResult(
                total_time_ms=0, parallelism=0, fallback_used=False
            )

        if len(tasks) == 1:
            result = self._execute_single(tasks[0], skill_execute_fn, on_complete_fn)
            elapsed = (time.time() - batch_start) * 1000
            return ParallelBatchResult(
                results=[result],
                total_time_ms=elapsed,
                parallelism=1,
                fallback_used=False,
            )

        results = self._execute_concurrent(tasks, skill_execute_fn, on_complete_fn)

        if results is None:
            results = self._execute_sequential(tasks, skill_execute_fn, on_complete_fn)
            elapsed = (time.time() - batch_start) * 1000
            return ParallelBatchResult(
                results=results,
                total_time_ms=elapsed,
                parallelism=1,
                fallback_used=True,
            )

        elapsed = (time.time() - batch_start) * 1000
        return ParallelBatchResult(
            results=results,
            total_time_ms=elapsed,
            parallelism=parallelism,
            fallback_used=False,
        )

    def _execute_concurrent(
        self,
        tasks: List[Dict[str, Any]],
        skill_execute_fn: Callable,
        on_complete_fn: Optional[Callable],
    ) -> Optional[List[ParallelResult]]:
        try:
            results = [None] * len(tasks)
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                future_to_idx = {}
                for i, task in enumerate(tasks):
                    future = pool.submit(
                        self._execute_single, task, skill_execute_fn, on_complete_fn
                    )
                    future_to_idx[future] = i

                for future in as_completed(
                    future_to_idx, timeout=self.timeout_per_task
                ):
                    idx = future_to_idx[future]
                    try:
                        results[idx] = future.result(timeout=self.timeout_per_task)
                    except Exception as e:
                        task_id = tasks[idx].get("task_id", f"task_{idx}")
                        results[idx] = ParallelResult(
                            task_id=task_id, success=False, error=str(e)
                        )

            return results
        except Exception as e:
            logger.warning(
                f"Parallel execution failed, will fall back to sequential: {e}"
            )
            return None

    def _execute_sequential(
        self,
        tasks: List[Dict[str, Any]],
        skill_execute_fn: Callable,
        on_complete_fn: Optional[Callable],
    ) -> List[ParallelResult]:
        results = []
        for task in tasks:
            r = self._execute_single(task, skill_execute_fn, on_complete_fn)
            results.append(r)
        return results

    def _execute_single(
        self,
        task_data: Dict[str, Any],
        skill_execute_fn: Callable,
        on_complete_fn: Optional[Callable],
    ) -> ParallelResult:
        task_id = task_data.get("task_id", "unknown")
        start = time.time()

        try:
            result = skill_execute_fn(task_data)
            elapsed = (time.time() - start) * 1000

            had_model_request = bool(result.get("pending_model_request"))
            model_request = result.get("pending_model_request")

            pr = ParallelResult(
                task_id=task_id,
                success=result.get("success", True),
                artifacts=result.get("artifacts", {}),
                error=result.get("error"),
                execution_time_ms=elapsed,
                had_model_request=had_model_request,
                model_request=model_request,
            )

            if on_complete_fn and not had_model_request:
                try:
                    on_complete_fn(task_id, result)
                except Exception as e:
                    logger.warning(f"on_complete callback failed for {task_id}: {e}")

            with self._lock:
                self._execution_log.append(
                    {
                        "task_id": task_id,
                        "success": pr.success,
                        "time_ms": elapsed,
                        "had_model_request": had_model_request,
                    }
                )

            return pr

        except Exception as e:
            elapsed = (time.time() - start) * 1000
            logger.error(f"Task {task_id} execution failed: {e}")
            return ParallelResult(
                task_id=task_id,
                success=False,
                error=str(e),
                execution_time_ms=elapsed,
            )

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            total = len(self._execution_log)
            succeeded = sum(1 for e in self._execution_log if e["success"])
            return {
                "max_workers": self.max_workers,
                "timeout_per_task": self.timeout_per_task,
                "total_executed": total,
                "total_succeeded": succeeded,
                "total_failed": total - succeeded,
            }

    def get_execution_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._execution_log)

    def clear_log(self):
        with self._lock:
            self._execution_log.clear()
