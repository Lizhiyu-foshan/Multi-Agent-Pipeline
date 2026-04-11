"""
Spec-Kit Skill Adapter

Integrates GitHub Spec Kit with the Multi-Agent Pipeline.
Manages the full spec lifecycle: init -> specify -> plan -> tasks -> scenarios -> evolve
"""

from typing import Dict, Any
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from specs.spec_manager import SpecManager
from specs.reasoning_map import ReasoningMap
from specs.constraint_validator import ConstraintValidator
from specs.scenario_tracker import ScenarioTracker
from specs.spec_evolution import SpecEvolution


class SpecKit_Adapter:
    """Spec-Kit Skill Adapter for Multi-Agent Pipeline"""

    name = "spec-kit"
    version = "2.0"

    def __init__(self, project_path: str = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        self.spec_manager = SpecManager(self.project_path)
        self.reasoning_map = ReasoningMap(self.project_path)
        self.constraint_validator = ConstraintValidator(self.project_path)
        self.scenario_tracker = ScenarioTracker(self.project_path)
        self.spec_evolution = SpecEvolution(self.project_path)

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "full")
        handlers = {
            "init": self._handle_init,
            "add_service": self._handle_add_service,
            "add_scenario": self._handle_add_scenario,
            "update_scenario": self._handle_update_scenario,
            "get_context": self._handle_get_context,
            "validate": self._handle_validate,
            "analyze": self._handle_analyze,
            "evolve": self._handle_evolve,
            "full": self._handle_full,
        }

        handler = handlers.get(action, self._handle_full)
        return handler(task_description, context)

    def _handle_init(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        system_name = context.get("system_name", "")
        system_goal = context.get("system_goal", task_description)

        init_result = self.spec_manager.init_specs(system_name, system_goal)

        return {
            "success": True,
            "artifacts": {
                "init_result": init_result,
                "message": f"Specs initialized with goal: {system_goal}",
            },
        }

    def _handle_add_service(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        name = context.get("service_name", "")
        responsibility = context.get("responsibility", task_description)
        boundaries = context.get("boundaries", [])
        capabilities = context.get("capabilities", [])

        if not name:
            return {"success": False, "error": "service_name required in context"}

        result = self.reasoning_map.add_service(
            name, responsibility, boundaries, capabilities
        )

        return {
            "success": True,
            "artifacts": {
                "service_added": name,
                "result": result,
            },
        }

    def _handle_add_scenario(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        service = context.get("service", "")
        scenario_id = context.get("scenario_id", "SC-001")
        name = context.get("scenario_name", task_description[:50])
        given = context.get("given", "")
        when = context.get("when", "")
        then = context.get("then", "")
        priority = context.get("priority", "P1")

        if not all([service, given, when, then]):
            return {"success": False, "error": "service, given, when, then required"}

        result = self.scenario_tracker.add_scenario(
            service, scenario_id, name, given, when, then, priority
        )

        return {
            "success": True,
            "artifacts": {"scenario_added": scenario_id, "result": result},
        }

    def _handle_update_scenario(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        service = context.get("service", "")
        scenario_id = context.get("scenario_id", "")
        status = context.get("status", "passed")
        notes = context.get("notes", "")

        if not all([service, scenario_id]):
            return {"success": False, "error": "service and scenario_id required"}

        result = self.scenario_tracker.update_scenario_status(
            service, scenario_id, status, notes
        )

        return {
            "success": True,
            "artifacts": {"scenario_updated": scenario_id, "result": result},
        }

    def _handle_get_context(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        service = context.get("service")

        agent_context = self.reasoning_map.get_context_for_agent(service)
        constraints = self.constraint_validator.get_rules_for_context(service)
        scenarios = self.scenario_tracker.format_for_agent(
            service, status_filter="pending"
        )

        full_context = f"{agent_context}\n\n---\n\n{constraints}\n\n---\n\n{scenarios}"

        return {
            "success": True,
            "artifacts": {
                "agent_context": full_context,
                "system_goal": self.reasoning_map.get_system_goal(),
            },
        }

    def _handle_validate(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        results = self.constraint_validator.validate_project()

        passed = sum(1 for r in results if r["passed"])
        failed = sum(1 for r in results if not r["passed"])

        return {
            "success": True,
            "artifacts": {
                "validation_results": results,
                "passed": passed,
                "failed": failed,
            },
        }

    def _handle_analyze(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        analysis = self.spec_evolution.analyze_specs()

        return {
            "success": True,
            "artifacts": {
                "analysis": analysis,
                "suggestions_count": len(analysis.get("suggestions", [])),
            },
        }

    def _handle_evolve(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        result = self.spec_evolution.request_bmad_evo_analysis(task_description)

        return {
            "success": True,
            "artifacts": {
                "evolution_context": result,
                "message": "Context prepared for bmad-evo analysis",
            },
        }

    def _handle_full(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        status = self.spec_manager.get_status()

        if not status["specs_dir_exists"]:
            return self._handle_init(task_description, context)

        agent_context = self.reasoning_map.get_context_for_agent()
        constraints = self.constraint_validator.get_rules_for_context()
        scenario_summary = self.scenario_tracker.get_summary()

        return {
            "success": True,
            "artifacts": {
                "status": status,
                "system_goal": self.reasoning_map.get_system_goal(),
                "context_for_agent": agent_context,
                "constraints_summary": constraints,
                "scenario_summary": scenario_summary,
            },
        }

    def can_handle(self, task_type: str, context: Dict) -> bool:
        return task_type in [
            "specification",
            "documentation",
            "architecture",
            "constitution",
            "validation",
            "evolution",
        ]

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "available": True,
            "spec_status": self.spec_manager.get_status(),
        }
