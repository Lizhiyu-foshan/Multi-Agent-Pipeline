"""
Multi-Agent-Pipeline Skill Adapter

Integrates the pipeline module into the skill system.
Provides actions for pipeline lifecycle management.

Actions:
- create_pipeline: Create a new pipeline from description
- advance: Advance pipeline to next phase with result
- human_decision: Submit a human decision for a pending pipeline
- get_status: Get pipeline status
- list_pipelines: List all pipelines
- resume: Resume a paused pipeline
- cleanup: Clean up expired locks and old checkpoints
- list_prompts: List available prompt templates and sections
- render_prompt: Render a specific prompt template with variables
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_PIPELINE_MODULE_PATH = str(Path(__file__).resolve().parent.parent.parent / "src")
if _PIPELINE_MODULE_PATH not in sys.path:
    sys.path.insert(0, _PIPELINE_MODULE_PATH)


class MultiAgentPipeline_Adapter:
    name = "multi-agent-pipeline"
    version = "2.1"

    def __init__(self, state_dir: str = None, skills: Dict[str, Any] = None):
        self._state_dir = state_dir
        self._skills = skills or {}
        self._orchestrator = None
        self._prompt_manager = None

    def _get_orchestrator(self):
        if self._orchestrator is None:
            from pipeline.pipeline_orchestrator import PipelineOrchestrator

            self._orchestrator = PipelineOrchestrator(
                state_dir=self._state_dir,
                skills=self._skills,
            )
        return self._orchestrator

    def _get_prompt_manager(self):
        if self._prompt_manager is None:
            from pipeline.prompt_manager import PromptManager

            project_path = self._state_dir
            if project_path:
                project_path = str(Path(project_path).parent)
            self._prompt_manager = PromptManager(project_path=project_path)
        return self._prompt_manager

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "create_pipeline")
        action_map = {
            "create_pipeline": self._action_create_pipeline,
            "advance": self._action_advance,
            "human_decision": self._action_human_decision,
            "get_status": self._action_get_status,
            "list_pipelines": self._action_list_pipelines,
            "resume": self._action_resume,
            "cleanup": self._action_cleanup,
            "list_prompts": self._action_list_prompts,
            "render_prompt": self._action_render_prompt,
            "dispatch_subagents": self._action_dispatch_subagents,
            "receive_subagent_results": self._action_receive_subagent_results,
            "resume_model_request": self._action_resume_model_request,
            "get_active_session": self._action_get_active_session,
        }

        handler = action_map.get(action)
        if not handler:
            return {
                "success": False,
                "error": f"Unknown action: {action}. Available: {list(action_map.keys())}",
            }
        try:
            return handler(task_description, context)
        except Exception as e:
            logger.error(f"Action {action} failed: {e}")
            return {"success": False, "error": str(e)}

    def _action_create_pipeline(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        orch = self._get_orchestrator()
        max_hours = context.get("max_duration_hours", 5.0)
        pipeline, next_action = orch.create_pipeline(description, max_hours)
        return {
            "success": True,
            "action": "created",
            "pipeline_id": pipeline.id,
            "next_action": next_action,
            "artifacts": {
                "pipeline_id": pipeline.id,
                "phase": pipeline.phase,
                "next_step": next_action["action"],
            },
        }

    def _action_advance(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        orch = self._get_orchestrator()
        pipeline_id = context.get("pipeline_id", "")
        phase_result = context.get("phase_result", {})

        result = orch.advance(pipeline_id, phase_result)
        result["success"] = True
        result["pipeline_id"] = pipeline_id
        return result

    def _action_human_decision(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        orch = self._get_orchestrator()
        pipeline_id = context.get("pipeline_id", "")
        decision = context.get("decision", "A")

        phase_result = {
            "decision": decision,
            "task_id": context.get("task_id", ""),
        }
        result = orch.advance(pipeline_id, phase_result)
        result["success"] = True
        result["pipeline_id"] = pipeline_id
        return result

    def _action_get_status(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        orch = self._get_orchestrator()
        pipeline_id = context.get("pipeline_id", "")
        status = orch.get_pipeline_status(pipeline_id)
        if not status:
            return {"success": False, "error": f"Pipeline {pipeline_id} not found"}
        return {"success": True, "status": status}

    def _action_list_pipelines(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        orch = self._get_orchestrator()
        pipelines = orch.list_pipelines()
        return {"success": True, "pipelines": pipelines}

    def _action_resume(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        orch = self._get_orchestrator()
        pipeline_id = context.get("pipeline_id", "")
        result = orch.resume_pipeline(pipeline_id)
        result["pipeline_id"] = pipeline_id
        return result

    def _action_cleanup(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        orch = self._get_orchestrator()
        orch.cleanup()
        return {"success": True, "message": "Cleanup complete"}

    def _action_list_prompts(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        pm = self._get_prompt_manager()
        skill_filter = context.get("skill")
        templates = pm.list_templates(skill=skill_filter)
        sections = pm.list_sections(category=context.get("category"))
        return {
            "success": True,
            "artifacts": {
                "templates": templates,
                "sections": sections,
                "status": pm.get_status(),
            },
        }

    def _action_render_prompt(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        pm = self._get_prompt_manager()
        template_name = context.get("template_name", "")
        if not template_name:
            return {"success": False, "error": "template_name required in context"}

        sections = context.get("sections", [])
        variables = {
            k: v
            for k, v in context.items()
            if k not in ("action", "template_name", "sections", "task_description")
        }

        try:
            if sections:
                prompt = pm.compose(template_name, sections=sections, **variables)
            else:
                prompt = pm.render(template_name, **variables)
            return {
                "success": True,
                "artifacts": {
                    "template": template_name,
                    "sections_used": sections,
                    "rendered_prompt": prompt,
                },
            }
        except KeyError as e:
            return {"success": False, "error": f"Template not found: {e}"}
        except Exception as e:
            return {"success": False, "error": f"Render failed: {e}"}

    def _action_dispatch_subagents(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Dispatch pipeline tasks as opencode Task tool subagent requests.

        The opencode agent should:
        1. Read subagent_requests from the result
        2. Launch each via Task tool (parallel if independent)
        3. Collect results
        4. Call back with receive_subagent_results
        """
        pipeline_id = context.get("pipeline_id", "")
        if not pipeline_id:
            return {"success": False, "error": "pipeline_id required"}

        orch = self._get_orchestrator()
        pipeline = orch.pipelines.get(pipeline_id)
        if not pipeline:
            return {"success": False, "error": f"Pipeline {pipeline_id} not found"}

        from pipeline.subagent_dispatcher import find_parallel_ready_tasks

        ready_tasks = find_parallel_ready_tasks(
            pipeline_tasks=pipeline.tasks,
            task_queue_get_fn=orch.scheduler.task_queue.get,
            task_queue_get_stats_fn=orch.scheduler.task_queue.get_statistics,
            max_parallel=context.get("max_parallel", 5),
        )

        if not ready_tasks:
            return {
                "success": True,
                "action": "dispatch_subagents",
                "subagent_requests": [],
                "message": "No ready tasks to dispatch",
            }

        pipeline_context = {
            "pipeline_id": pipeline_id,
            "project_path": str(Path(orch.state_dir).parent) if orch.state_dir else "",
            "spec_context": orch.context.get_previous_artifacts_summary(pipeline_id),
            "previous_artifacts_summary": orch.context.get_previous_artifacts_summary(
                pipeline_id
            ),
        }

        dispatch_result = orch.subagent_dispatcher.dispatch(
            ready_tasks=ready_tasks,
            pipeline_context=pipeline_context,
        )

        return {
            "success": True,
            "action": "dispatch_subagents",
            "pipeline_id": pipeline_id,
            "subagent_requests": dispatch_result["subagent_requests"],
            "parallel": dispatch_result["parallel"],
            "instructions": dispatch_result["instructions"],
        }

    def _action_receive_subagent_results(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Receive results from completed subagents and advance pipeline.

        The opencode agent calls this after all dispatched subagents finish.
        """
        pipeline_id = context.get("pipeline_id", "")
        results = context.get("results", [])

        if not pipeline_id:
            return {"success": False, "error": "pipeline_id required"}
        if not results:
            return {"success": False, "error": "results list required"}

        orch = self._get_orchestrator()
        pipeline = orch.pipelines.get(pipeline_id)
        if not pipeline:
            return {"success": False, "error": f"Pipeline {pipeline_id} not found"}

        advance_result = orch.advance(
            pipeline_id,
            {
                "action": "receive_subagent_results",
                "results": results,
            },
        )

        advance_result["success"] = True
        return advance_result

    def _action_resume_model_request(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Resume a paused execution with a model response.

        After a skill returns pending_model_request, the opencode agent
        provides the model's response via this action to continue execution.
        """
        session_id = context.get("session_id", "")
        model_response = context.get("model_response", "")

        if not session_id:
            return {"success": False, "error": "session_id required"}
        if not model_response:
            return {"success": False, "error": "model_response required"}

        orch = self._get_orchestrator()
        result = orch.resume_model_request(session_id, model_response)
        result["success"] = result.get("success", True) or result.get("action") in (
            "model_request",
            "execute_next_task",
            "check",
        )
        return result

    def _action_get_active_session(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Get the active model request session for a pipeline."""
        pipeline_id = context.get("pipeline_id", "")
        if not pipeline_id:
            return {"success": False, "error": "pipeline_id required"}

        orch = self._get_orchestrator()
        session_info = orch.get_active_session(pipeline_id)
        if not session_info:
            return {
                "success": True,
                "active_session": None,
                "message": "No active model request session",
            }
        return {"success": True, "active_session": session_info}
