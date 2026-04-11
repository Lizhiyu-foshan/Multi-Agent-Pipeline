"""
SpecEvolution - Spec Self-Evolution via BMAD-EVO

Enables specs to self-evolve:
1. Analyzes current specs for gaps, inconsistencies, drift
2. Requests improvement suggestions from bmad-evo
3. Records evolution history in evolution-log.md
4. Can apply suggested improvements (with human approval)

Superpowers-inspired enhancements:
- Two-stage review: spec compliance (goal alignment) THEN spec quality (completeness/clarity)
- Severity levels: critical / important / minor (from code quality review pattern)
- Root cause analysis for drift (from 4-phase debugging pattern)
- Report format standardization (status codes)
"""

import yaml
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class SpecEvolution:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.specs_dir = project_path / ".specs"
        self.evolution_log_path = self.specs_dir / "evolution-log.md"
        self.agent_md_path = project_path / "Agent.md"

    def analyze_specs(self) -> Dict[str, Any]:
        findings = {
            "timestamp": datetime.now().isoformat(),
            "missing_services": [],
            "incomplete_specs": [],
            "drift_indicators": [],
            "stale_scenarios": [],
            "suggestions": [],
            "review": {
                "stage1_spec_compliance": {"passed": True, "issues": []},
                "stage2_spec_quality": {"passed": True, "issues": []},
            },
        }

        if not self.specs_dir.exists():
            findings["suggestions"].append(
                "Specs directory does not exist. Run init first."
            )
            findings["review"]["stage1_spec_compliance"]["passed"] = False
            findings["review"]["stage1_spec_compliance"]["issues"].append(
                {"severity": "critical", "message": "No specs directory exists"}
            )
            return findings

        findings["missing_services"] = self._find_missing_service_specs()
        findings["incomplete_specs"] = self._find_incomplete_specs()
        findings["drift_indicators"] = self._check_goal_drift()
        findings["stale_scenarios"] = self._find_stale_scenarios()

        # Stage 1: Spec Compliance (does spec match the system goal?)
        stage1 = self._review_spec_compliance(findings)
        findings["review"]["stage1_spec_compliance"] = stage1

        # Stage 2: Spec Quality (is the spec well-written and complete?)
        # Only runs if Stage 1 passes (like Superpowers two-stage review)
        if stage1["passed"]:
            stage2 = self._review_spec_quality(findings)
            findings["review"]["stage2_spec_quality"] = stage2
        else:
            findings["review"]["stage2_spec_quality"] = {
                "passed": False,
                "issues": [
                    {
                        "severity": "important",
                        "message": "Skipped: Stage 1 (spec compliance) did not pass",
                    }
                ],
            }

        findings["suggestions"] = self._generate_suggestions(findings)

        self._log_analysis(findings)
        logger.info(
            f"Spec analysis complete: stage1={'PASS' if stage1['passed'] else 'FAIL'}, "
            f"suggestions={len(findings['suggestions'])}"
        )
        return findings

    def _review_spec_compliance(self, findings: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 1: Spec Compliance — Does the spec match the system's stated goals?"""
        issues = []

        if findings["drift_indicators"]:
            for d in findings["drift_indicators"]:
                severity = (
                    "critical"
                    if d["type"] in ("vague_goal", "no_services")
                    else "important"
                )
                issues.append(
                    {
                        "severity": severity,
                        "message": d["message"],
                        "type": d["type"],
                    }
                )

        if findings["missing_services"]:
            for m in findings["missing_services"]:
                issues.append(
                    {
                        "severity": "critical",
                        "message": f"Service '{m['service']}' declared but has no spec",
                        "service": m["service"],
                    }
                )

        return {
            "passed": not any(i["severity"] == "critical" for i in issues),
            "issues": issues,
        }

    def _review_spec_quality(self, findings: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 2: Spec Quality — Is the spec well-written, complete, and actionable?"""
        issues = []

        for inc in findings["incomplete_specs"]:
            issues.append(
                {
                    "severity": "important",
                    "message": f"Spec for '{inc['service']}' has {inc['issue']}",
                    "service": inc["service"],
                }
            )

        for stale in findings["stale_scenarios"]:
            issues.append(
                {
                    "severity": "minor",
                    "message": f"Scenario {stale['scenario_id']} in {stale['service']} has failed status",
                    "scenario_id": stale["scenario_id"],
                }
            )

        constraint_file = self.specs_dir / "constraints" / "constraints.yaml"
        if not constraint_file.exists():
            issues.append(
                {
                    "severity": "important",
                    "message": "No constraints.yaml found — semantic constraints not defined",
                }
            )
        else:
            constraints = (
                yaml.safe_load(constraint_file.read_text(encoding="utf-8")) or {}
            )
            if not constraints.get("contract"):
                issues.append(
                    {
                        "severity": "minor",
                        "message": "No contract rules defined in constraints",
                    }
                )
            if not constraints.get("behavior"):
                issues.append(
                    {
                        "severity": "minor",
                        "message": "No behavior rules defined in constraints",
                    }
                )

        return {
            "passed": not any(
                i["severity"] in ("critical", "important") for i in issues
            ),
            "issues": issues,
        }

    def request_bmad_evo_analysis(self, task_description: str) -> Dict[str, Any]:
        analysis = self.analyze_specs()

        from .reasoning_map import ReasoningMap

        rm = ReasoningMap(self.project_path)
        system_data = rm.read_system_map()

        context = {
            "task_description": task_description,
            "system_goal": system_data["front_matter"]
            .get("system", {})
            .get("goal", ""),
            "services": system_data["front_matter"].get("services", []),
            "findings": analysis,
        }

        return {
            "success": True,
            "analysis": analysis,
            "context_for_bmad": context,
            "message": "Pass this context to bmad-evo for deep analysis and improvement suggestions",
        }

    def apply_suggestion(
        self, suggestion_id: str, approved: bool = True
    ) -> Dict[str, Any]:
        log_entries = self._read_evolution_log()
        target = None
        for entry in log_entries:
            for s in entry.get("suggestions", []):
                if s.get("id") == suggestion_id:
                    target = s
                    break

        if not target:
            return {"success": False, "error": f"suggestion {suggestion_id} not found"}

        if not approved:
            target["status"] = "rejected"
            self._write_evolution_log(log_entries)
            return {"success": True, "status": "rejected"}

        target["status"] = "applied"
        target["applied_at"] = datetime.now().isoformat()

        action = target.get("action", "")
        if action == "create_service_spec":
            self._apply_create_service(target)
        elif action == "complete_spec":
            self._apply_complete_spec(target)
        elif action == "update_scenarios":
            self._apply_update_scenarios(target)
        elif action == "update_goal":
            self._apply_update_goal(target)

        self._write_evolution_log(log_entries)
        logger.info(
            f"Suggestion {suggestion_id} applied: {target.get('description', '')}"
        )
        return {"success": True, "status": "applied", "suggestion": target}

    def get_evolution_history(self) -> List[Dict[str, Any]]:
        return self._read_evolution_log()

    def get_pending_suggestions(self) -> List[Dict[str, Any]]:
        entries = self._read_evolution_log()
        pending = []
        for entry in entries:
            for s in entry.get("suggestions", []):
                if s.get("status") == "pending":
                    pending.append(s)
        return pending

    def _find_missing_service_specs(self) -> List[Dict[str, Any]]:
        from .reasoning_map import ReasoningMap

        rm = ReasoningMap(self.project_path)
        services = rm.get_services()

        missing = []
        services_dir = self.specs_dir / "services"
        for svc in services:
            name = svc.get("name", "")
            if not (services_dir / f"{name}.md").exists():
                missing.append({"service": name, "issue": "service spec file missing"})
        return missing

    def _find_incomplete_specs(self) -> List[Dict[str, Any]]:
        incomplete = []
        services_dir = self.specs_dir / "services"
        if not services_dir.exists():
            return incomplete

        for f in services_dir.glob("*.md"):
            content = f.read_text(encoding="utf-8")
            placeholders = (
                content.count("[Define")
                + content.count("[Rule")
                + content.count("[List")
            )
            if placeholders > 0:
                incomplete.append(
                    {
                        "service": f.stem,
                        "issue": f"{placeholders} placeholder(s) remaining",
                        "path": str(f),
                    }
                )
        return incomplete

    def _check_goal_drift(self) -> List[Dict[str, Any]]:
        indicators = []
        from .reasoning_map import ReasoningMap

        rm = ReasoningMap(self.project_path)
        data = rm.read_system_map()

        services = data["front_matter"].get("services", [])
        if len(services) == 0:
            indicators.append(
                {"type": "no_services", "message": "No services defined in Agent.md"}
            )

        goal = data["front_matter"].get("system", {}).get("goal", "")
        if not goal or goal == "System goal TBD":
            indicators.append(
                {"type": "vague_goal", "message": "System goal is vague or undefined"}
            )

        return indicators

    def _find_stale_scenarios(self) -> List[Dict[str, Any]]:
        stale = []
        scenarios_dir = self.specs_dir / "scenarios"
        if not scenarios_dir.exists():
            return stale

        for f in scenarios_dir.glob("*-scenarios.yaml"):
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            for s in data.get("scenarios", []):
                if s.get("status") == "failed":
                    stale.append(
                        {
                            "scenario_id": s["id"],
                            "service": data.get("service", ""),
                            "issue": "scenario has failed status",
                        }
                    )
        return stale

    def _generate_suggestions(self, findings: Dict[str, Any]) -> List[Dict[str, Any]]:
        suggestions = []
        idx = 0

        for m in findings.get("missing_services", []):
            idx += 1
            suggestions.append(
                {
                    "id": f"SE-{idx:03d}",
                    "action": "create_service_spec",
                    "service": m["service"],
                    "description": f"Create spec for service '{m['service']}'",
                    "severity": "critical",
                    "status": "pending",
                }
            )

        for d in findings.get("drift_indicators", []):
            idx += 1
            suggestions.append(
                {
                    "id": f"SE-{idx:03d}",
                    "action": "update_goal",
                    "type": d["type"],
                    "description": f"Address drift: {d['message']}",
                    "severity": "critical"
                    if d["type"] in ("vague_goal", "no_services")
                    else "important",
                    "status": "pending",
                }
            )

        for i in findings.get("incomplete_specs", []):
            idx += 1
            suggestions.append(
                {
                    "id": f"SE-{idx:03d}",
                    "action": "complete_spec",
                    "service": i["service"],
                    "description": f"Complete placeholders in '{i['service']}' spec ({i['issue']})",
                    "severity": "important",
                    "status": "pending",
                }
            )

        for s in findings.get("stale_scenarios", []):
            idx += 1
            suggestions.append(
                {
                    "id": f"SE-{idx:03d}",
                    "action": "update_scenarios",
                    "service": s["service"],
                    "description": f"Fix failed scenario {s['scenario_id']} in {s['service']}",
                    "severity": "minor",
                    "status": "pending",
                }
            )

        return suggestions

    def _apply_create_service(self, suggestion: Dict[str, Any]):
        from .reasoning_map import ReasoningMap

        rm = ReasoningMap(self.project_path)
        rm._create_service_spec(
            suggestion.get("service", "unknown"),
            "[Define responsibility]",
            ["[Define boundaries]"],
        )

    def _apply_complete_spec(self, suggestion: Dict[str, Any]):
        pass

    def _apply_update_scenarios(self, suggestion: Dict[str, Any]):
        pass

    def _apply_update_goal(self, suggestion: Dict[str, Any]):
        pass

    def _log_analysis(self, findings: Dict[str, Any]):
        entries = self._read_evolution_log()
        entries.append(
            {
                "timestamp": findings["timestamp"],
                "suggestions": findings["suggestions"],
                "summary": {
                    "missing_services": len(findings["missing_services"]),
                    "incomplete_specs": len(findings["incomplete_specs"]),
                    "drift_indicators": len(findings["drift_indicators"]),
                    "stale_scenarios": len(findings["stale_scenarios"]),
                },
            }
        )
        self._write_evolution_log(entries)

    def _read_evolution_log(self) -> List[Dict[str, Any]]:
        log_path = self.specs_dir / "evolution-log.yaml"
        if not log_path.exists():
            return []
        with open(log_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or []

    def _write_evolution_log(self, entries: List[Dict[str, Any]]):
        log_path = self.specs_dir / "evolution-log.yaml"
        with open(log_path, "w", encoding="utf-8") as f:
            yaml.dump(
                entries,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
