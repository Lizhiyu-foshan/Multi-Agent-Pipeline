"""
ScenarioTracker - WHEN/THEN Scenario Status Tracking

Manages structured acceptance criteria:
- Each scenario has: id, name, given, when, then, status, priority, service
- Status tracking: pending -> passed | failed
- Can query scenarios by service, priority, or status
- Stored in .specs/scenarios/<service>-scenarios.yaml
"""

import yaml
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class ScenarioTracker:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.scenarios_dir = project_path / ".specs" / "scenarios"

    def create_scenarios_file(
        self, service_name: str, scenarios: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        self.scenarios_dir.mkdir(parents=True, exist_ok=True)
        path = self.scenarios_dir / f"{service_name}-scenarios.yaml"

        data = {
            "service": service_name,
            "created": datetime.now().isoformat(),
            "scenarios": scenarios or [],
        }

        self._write_yaml(path, data)
        logger.info(f"Scenarios file created for {service_name}: {path}")
        return {"success": True, "path": str(path)}

    def add_scenario(
        self,
        service_name: str,
        scenario_id: str,
        name: str,
        given: str,
        when: str,
        then: str,
        priority: str = "P1",
        status: str = "pending",
    ) -> Dict[str, Any]:
        path = self._get_scenarios_path(service_name)
        data = self._load_or_create(service_name)

        scenario = {
            "id": scenario_id,
            "name": name,
            "given": given,
            "when": when,
            "then": then,
            "priority": priority,
            "status": status,
            "updated": datetime.now().isoformat(),
        }

        data["scenarios"].append(scenario)
        self._write_yaml(path, data)

        logger.info(f"Scenario {scenario_id} added to {service_name}")
        return {"success": True, "scenario_id": scenario_id}

    def update_scenario_status(
        self, service_name: str, scenario_id: str, status: str, notes: str = ""
    ) -> Dict[str, Any]:
        if status not in ("pending", "passed", "failed"):
            return {"success": False, "error": f"invalid status: {status}"}

        data = self._load_or_create(service_name)
        for s in data["scenarios"]:
            if s["id"] == scenario_id:
                s["status"] = status
                s["updated"] = datetime.now().isoformat()
                if notes:
                    s["notes"] = notes
                break
        else:
            return {"success": False, "error": f"scenario {scenario_id} not found"}

        path = self._get_scenarios_path(service_name)
        self._write_yaml(path, data)
        return {"success": True, "scenario_id": scenario_id, "status": status}

    def get_scenarios(
        self, service_name: str = None, status: str = None, priority: str = None
    ) -> List[Dict[str, Any]]:
        if service_name:
            data = self._load_or_create(service_name)
            scenarios = data.get("scenarios", [])
        else:
            scenarios = self._load_all_scenarios()

        if status:
            scenarios = [s for s in scenarios if s.get("status") == status]
        if priority:
            scenarios = [s for s in scenarios if s.get("priority") == priority]

        return scenarios

    def get_summary(self, service_name: str = None) -> Dict[str, Any]:
        scenarios = self.get_scenarios(service_name=service_name)

        total = len(scenarios)
        by_status = {"pending": 0, "passed": 0, "failed": 0}
        by_priority = {}

        for s in scenarios:
            by_status[s.get("status", "pending")] += 1
            p = s.get("priority", "unknown")
            by_priority[p] = by_priority.get(p, 0) + 1

        return {
            "total": total,
            "by_status": by_status,
            "by_priority": by_priority,
            "pass_rate": by_status["passed"] / total if total > 0 else 0,
        }

    def get_failed_scenarios(self, service_name: str = None) -> List[Dict[str, Any]]:
        return self.get_scenarios(service_name=service_name, status="failed")

    def get_pending_summary(self, service_name: str = None) -> str:
        """Compact one-liner of pending scenarios for context injection (~50 tokens)."""
        pending = self.get_scenarios(service_name=service_name, status="pending")
        if not pending:
            return "[SCENARIOS] No pending scenarios."
        items = "; ".join(
            f"{s['id']}({s.get('priority', '?')}): WHEN {s['when']} THEN {s['then']}"
            for s in pending[:5]
        )
        overflow = f" +{len(pending) - 5} more" if len(pending) > 5 else ""
        return f"[SCENARIOS] Pending: {items}{overflow}"

    def get_failed_summary(self, service_name: str = None) -> str:
        """Compact one-liner of failed scenarios for post-check."""
        failed = self.get_failed_scenarios(service_name)
        if not failed:
            return ""
        items = "; ".join(f"{s['id']}: {s.get('notes', s['then'])}" for s in failed[:3])
        return f"[FAILED] {items}"

    def format_for_agent(
        self, service_name: str = None, status_filter: str = None
    ) -> str:
        scenarios = self.get_scenarios(service_name=service_name, status=status_filter)
        if not scenarios:
            return "No scenarios found."

        lines = ["# Acceptance Scenarios", ""]
        for s in scenarios:
            status_icon = {
                "pending": "[ ]",
                "passed": "[PASS]",
                "failed": "[FAIL]",
            }.get(s["status"], "[?]")
            lines.append(
                f"## {status_icon} {s['id']}: {s['name']} ({s.get('priority', 'N/A')})"
            )
            lines.append(f"- **GIVEN** {s['given']}")
            lines.append(f"- **WHEN** {s['when']}")
            lines.append(f"- **THEN** {s['then']}")
            if s.get("notes"):
                lines.append(f"- Notes: {s['notes']}")
            lines.append("")

        return "\n".join(lines)

    def generate_report(self) -> str:
        all_scenarios = self._load_all_scenarios()
        total = len(all_scenarios)
        passed = sum(1 for s in all_scenarios if s.get("status") == "passed")
        failed = sum(1 for s in all_scenarios if s.get("status") == "failed")
        pending = sum(1 for s in all_scenarios if s.get("status") == "pending")

        lines = [
            "# Scenario Report",
            "",
            f"- Total: {total}",
            f"- Passed: {passed}",
            f"- Failed: {failed}",
            f"- Pending: {pending}",
            f"- Pass Rate: {passed / total * 100:.1f}%"
            if total > 0
            else "- Pass Rate: N/A",
            "",
        ]

        if failed > 0:
            lines.append("## Failed Scenarios")
            for s in all_scenarios:
                if s.get("status") == "failed":
                    lines.append(
                        f"- {s['id']}: {s['name']} - {s.get('notes', 'no notes')}"
                    )
            lines.append("")

        return "\n".join(lines)

    def _load_all_scenarios(self) -> List[Dict[str, Any]]:
        all_scenarios = []
        if not self.scenarios_dir.exists():
            return all_scenarios

        for f in self.scenarios_dir.glob("*-scenarios.yaml"):
            data = self._read_yaml(f)
            all_scenarios.extend(data.get("scenarios", []))

        return all_scenarios

    def _load_or_create(self, service_name: str) -> Dict[str, Any]:
        path = self._get_scenarios_path(service_name)
        if path.exists():
            return self._read_yaml(path)
        return {
            "service": service_name,
            "created": datetime.now().isoformat(),
            "scenarios": [],
        }

    def _get_scenarios_path(self, service_name: str) -> Path:
        return self.scenarios_dir / f"{service_name}-scenarios.yaml"

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write_yaml(self, path: Path, data: Dict[str, Any]):
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
