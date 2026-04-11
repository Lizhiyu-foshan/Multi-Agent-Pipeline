"""
Subagent Dispatcher - Bridges pipeline tasks to opencode Task tool.

Design:
    PipelineOrchestrator produces tasks with prompts.
    SubagentDispatcher converts them to opencode Task tool requests.
    The opencode agent (you) executes them via Task(explore/general).
    Results flow back through receive_results().

Flow:
    1. Pipeline EXECUTE phase has N ready tasks
    2. SubagentDispatcher.dispatch() generates Task tool requests
    3. Opencode agent calls Task tool for each request (parallel if independent)
    4. Opencode agent calls receive_results() with results
    5. Pipeline advances

Request format (returned to opencode agent):
    {
        "action": "dispatch_subagents",
        "pipeline_id": "...",
        "subagent_requests": [
            {
                "task_id": "task_xxx",
                "task_name": "Implement auth",
                "subagent_type": "general",       # or "explore"
                "description": "5-word summary",
                "prompt": "full prompt for subagent",
                "project_path": "D:/...",
                "role_type": "developer",
                "depends_on": [],                  # empty = can run in parallel
            },
            ...
        ],
        "parallel": true,                          # if all are independent
        "instructions": "Use Task tool to execute each request..."
    }

Result format (passed back by opencode agent):
    {
        "action": "receive_subagent_results",
        "pipeline_id": "...",
        "results": [
            {
                "task_id": "task_xxx",
                "success": true,
                "artifacts": {"code": "...", "tests": "..."},
                "output": "subagent's text output",
            },
            ...
        ]
    }
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .prompt_manager import PromptManager

logger = logging.getLogger(__name__)

WORKTREE_INTEGRATION_ENABLED = os.environ.get("PIPELINE_WORKTREE", "0") == "1"


@dataclass
class SubagentRequest:
    task_id: str
    task_name: str
    prompt: str
    role_type: str
    project_path: str
    subagent_type: str = "general"
    depends_on: List[str] = field(default_factory=list)
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    max_steps: int = 50
    context_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "subagent_type": self.subagent_type,
            "description": self.task_name[:80],
            "prompt": self.prompt,
            "project_path": self.project_path,
            "role_type": self.role_type,
            "depends_on": self.depends_on,
            "files_to_create": self.files_to_create,
            "files_to_modify": self.files_to_modify,
            "max_steps": self.max_steps,
            "context_summary": self.context_summary[:200],
        }


class SubagentDispatcher:
    """
    Converts pipeline tasks to opencode Task tool requests.

    Usage (inside PipelineOrchestrator):
        dispatcher = SubagentDispatcher(prompt_manager=pm)
        requests = dispatcher.dispatch(tasks, pipeline_context)

        # Return requests to opencode agent
        # ... agent executes them via Task tool ...

        # Agent calls back with results
        dispatcher.receive_results(results, scheduler, context_mgr, pipeline)
    """

    ROLE_AGENT_MAP = {
        "analyst": "explore",
        "architect": "explore",
        "planner": "explore",
        "developer": "general",
        "coder": "general",
        "implementer": "general",
        "tester": "general",
        "spec-writer": "general",
    }

    def __init__(self, prompt_manager: PromptManager = None, worktree_manager=None):
        self.prompt_manager = prompt_manager or PromptManager()
        self.worktree_manager = worktree_manager
        self._active_dispatches: Dict[str, List[str]] = {}

    def dispatch(
        self,
        ready_tasks: List[Dict[str, Any]],
        pipeline_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Generate subagent requests for ready tasks.

        Args:
            ready_tasks: List of task dicts from _get_next_ready_task or similar
            pipeline_context: {pipeline_id, project_path, spec_context, ...}

        Returns:
            Dispatch request dict for opencode agent to execute.
        """
        pipeline_id = pipeline_context.get("pipeline_id", "")
        project_path = pipeline_context.get("project_path", str(Path.cwd()))

        requests: List[SubagentRequest] = []

        for task_data in ready_tasks:
            task_id = task_data.get("task_id", "")
            role_type = task_data.get("role_id", "developer")
            task_name = task_data.get("prompt", task_data.get("task_name", ""))[:100]
            prompt = self._build_subagent_prompt(task_data, pipeline_context)
            subagent_type = self.ROLE_AGENT_MAP.get(role_type, "general")

            deps = task_data.get("depends_on", [])
            if isinstance(deps, str):
                deps = [deps]

            req = SubagentRequest(
                task_id=task_id,
                task_name=task_name,
                prompt=prompt,
                role_type=role_type,
                project_path=project_path,
                subagent_type=subagent_type,
                depends_on=deps,
                files_to_create=task_data.get("files_to_create", []),
                files_to_modify=task_data.get("files_to_modify", []),
                max_steps=task_data.get("max_steps", 50),
                context_summary=pipeline_context.get("spec_context", "")[:200],
            )

            if (
                self.worktree_manager
                and WORKTREE_INTEGRATION_ENABLED
                and self.worktree_manager._git_available
            ):
                wt_result = self.worktree_manager.create_worktree(task_id)
                if wt_result.get("success"):
                    req.project_path = wt_result["worktree_path"]

            requests.append(req)

        if not requests:
            return {
                "action": "dispatch_subagents",
                "pipeline_id": pipeline_id,
                "subagent_requests": [],
                "parallel": False,
                "instructions": "No tasks to dispatch.",
            }

        all_independent = all(len(r.depends_on) == 0 for r in requests)
        parallel = all_independent and len(requests) > 1

        self._active_dispatches[pipeline_id] = [r.task_id for r in requests]

        request_dicts = [r.to_dict() for r in requests]

        instructions = self._build_instructions(request_dicts, parallel)

        logger.info(
            f"Dispatched {len(requests)} subagent tasks for pipeline {pipeline_id} "
            f"(parallel={parallel})"
        )

        return {
            "action": "dispatch_subagents",
            "pipeline_id": pipeline_id,
            "subagent_requests": request_dicts,
            "parallel": parallel,
            "instructions": instructions,
        }

    def receive_results(
        self,
        pipeline_id: str,
        results: List[Dict[str, Any]],
        scheduler=None,
        context_mgr=None,
        pipeline=None,
    ) -> Dict[str, Any]:
        """
        Process subagent results and update pipeline state.

        Args:
            pipeline_id: The pipeline these results belong to
            results: List of {task_id, success, artifacts, output}
            scheduler: ResourceSchedulerAPI for task completion
            context_mgr: ContextManager for artifact storage
            pipeline: PipelineRun to update

        Returns:
            Summary of processed results.
        """
        processed = []
        succeeded = 0
        failed = 0

        for result in results:
            task_id = result.get("task_id", "")
            success = result.get("success", False)
            artifacts = result.get("artifacts", {})
            output = result.get("output", "")

            if success:
                succeeded += 1
            else:
                failed += 1

            if scheduler:
                task_result = {
                    "success": success,
                    "artifacts": artifacts,
                    "output": output,
                }
                if artifacts and context_mgr:
                    for k, v in artifacts.items():
                        context_mgr.store_artifact(pipeline_id, task_id, k, v)
                scheduler.complete_task(task_id, success, task_result)

            processed.append(
                {
                    "task_id": task_id,
                    "success": success,
                    "artifacts_count": len(artifacts)
                    if isinstance(artifacts, dict)
                    else 0,
                }
            )

        if pipeline_id in self._active_dispatches:
            del self._active_dispatches[pipeline_id]

        logger.info(
            f"Received {len(results)} subagent results: "
            f"{succeeded} succeeded, {failed} failed"
        )

        return {
            "action": "subagent_results_received",
            "pipeline_id": pipeline_id,
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "processed": processed,
        }

    def get_active_dispatches(self) -> Dict[str, List[str]]:
        return dict(self._active_dispatches)

    def _build_subagent_prompt(
        self, task_data: Dict[str, Any], pipeline_context: Dict[str, Any]
    ) -> str:
        """Build a self-contained prompt for the subagent."""
        task_prompt = task_data.get("prompt", "")
        role_type = task_data.get("role_id", "developer")
        task_id = task_data.get("task_id", "")
        spec_context = pipeline_context.get("spec_context", "")
        prev_artifacts = pipeline_context.get("previous_artifacts_summary", "")

        template_name = self._role_to_template(role_type)
        sections = ["stuck_protocol", "report_format"]

        if spec_context:
            sections.insert(0, "spec_constraints")
        if prev_artifacts:
            sections.append("previous_artifacts")

        try:
            prompt = self.prompt_manager.compose(
                template_name,
                sections=sections,
                task_name=task_prompt[:100],
                task_description=task_prompt,
                role_name=role_type,
                role_type=role_type,
                capabilities="",
                spec_context=spec_context,
                previous_artifacts_summary=prev_artifacts,
            )
        except Exception:
            prompt = task_prompt
            if spec_context:
                prompt += f"\n\n[Constraints]\n{spec_context[:500]}"

        prompt += f"\n\n---\n[SUBAGENT TASK ID: {task_id}]"
        prompt += (
            "\nReport status as: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT"
        )
        prompt += "\nList all files created or modified with full paths."

        return prompt

    def _role_to_template(self, role_type: str) -> str:
        """Map role type to PromptManager template name."""
        role_map = {
            "developer": "pipeline/execute_task",
            "coder": "pipeline/execute_task",
            "implementer": "pipeline/execute_task",
            "tester": "pipeline/execute_task",
            "analyst": "pipeline/analyze",
            "architect": "pipeline/plan",
            "planner": "pipeline/plan",
            "spec-writer": "pipeline/execute_task",
        }
        return role_map.get(role_type, "pipeline/execute_task")

    def _build_instructions(
        self, requests: List[Dict[str, Any]], parallel: bool
    ) -> str:
        """Build instructions for the opencode agent on how to execute tasks."""
        lines = [
            "SUBAGENT DISPATCH INSTRUCTIONS",
            "=" * 40,
            "",
        ]

        if not requests:
            lines.append("No tasks to dispatch.")
            return "\n".join(lines)

        if parallel:
            lines.append(
                f"ALL {len(requests)} tasks are INDEPENDENT (no dependencies)."
            )
            lines.append(
                "Execute them in PARALLEL using multiple Task tool calls in one message."
            )
            lines.append("")
        else:
            deps = [
                (r["task_id"], r["depends_on"]) for r in requests if r["depends_on"]
            ]
            lines.append("Tasks have dependencies. Execute in dependency order.")
            if deps:
                lines.append("Dependencies:")
                for tid, dep_list in deps:
                    lines.append(f"  {tid} depends on: {', '.join(dep_list)}")
            lines.append("")

        lines.append("For each task, use the opencode Task tool:")
        lines.append("")

        for r in requests:
            stype = r["subagent_type"]
            desc = r["description"]
            tid = r["task_id"]
            lines.append(f"  Task [{tid}] ({stype}): {desc}")

        lines.append("")
        lines.append("After all subagents complete, call:")
        lines.append("  adapter.execute('', {")
        lines.append("    'action': 'receive_subagent_results',")
        lines.append(f"    'pipeline_id': '{requests[0].get('pipeline_id', '')}',")
        lines.append("    'results': [")
        lines.append("      {'task_id': '...', 'success': true/false,")
        lines.append("       'artifacts': {...}, 'output': '...'},")
        lines.append("      ...")
        lines.append("    ]")
        lines.append("  })")

        return "\n".join(lines)


def find_parallel_ready_tasks(
    pipeline_tasks: List[str],
    task_queue_get_fn,
    task_queue_get_stats_fn,
    max_parallel: int = 5,
) -> List[Dict[str, Any]]:
    """
    Find all tasks that can run in parallel (pending, deps met).

    Used by PipelineOrchestrator to enable parallel subagent dispatch.

    Args:
        pipeline_tasks: List of task IDs in the pipeline
        task_queue_get_fn: Callable(task_id) -> Task
        task_queue_get_stats_fn: Callable() -> stats dict
        max_parallel: Maximum concurrent subagents

    Returns:
        List of task data dicts ready for parallel execution.
    """
    ready = []

    for task_id in pipeline_tasks:
        task = task_queue_get_fn(task_id)
        if not task or task.status != "pending":
            continue

        deps_met = all(
            task_queue_get_fn(dep) and task_queue_get_fn(dep).status == "completed"
            for dep in task.depends_on
        )
        if not deps_met:
            continue

        ready.append(
            {
                "task_id": task.id,
                "role_id": task.role_id,
                "prompt": f"Execute task: {task.name}\nDescription: {task.description}",
                "task_name": task.name,
                "depends_on": task.depends_on,
                "max_steps": task.max_steps,
            }
        )

        if len(ready) >= max_parallel:
            break

    return ready
