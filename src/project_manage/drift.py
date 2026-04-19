"""
Drift Detection - Compare current state against activated constraint pack.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import DriftReport, DriftViolation, DriftSeverity
from .packs import ConstraintPackManager
from .registry import ProjectRegistry
from .ingest import ChangeIngester

logger = logging.getLogger(__name__)


class DriftDetector:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = state_dir
        self._reports: Dict[str, DriftReport] = {}

    def check(
        self,
        project_id: str,
        event_id: str = "",
        registry: ProjectRegistry = None,
        packs: ConstraintPackManager = None,
        ingester: ChangeIngester = None,
    ) -> Dict[str, Any]:
        violations: List[DriftViolation] = []

        if registry is None:
            registry = ProjectRegistry(state_dir=self._state_dir)
        if packs is None:
            packs = ConstraintPackManager(state_dir=self._state_dir)

        proj_result = registry.get(project_id)
        if not proj_result.get("success"):
            return proj_result

        project_data = proj_result["artifacts"]
        active_pack = project_data.get("active_pack", {})
        pack_name = active_pack.get("name", "")
        pack_version = active_pack.get("version", "")
        target_path = project_data.get("target_path", "")

        if not pack_name or not pack_version:
            report = DriftReport(
                project_id=project_id,
                violations=[
                    DriftViolation(
                        rule="active_pack_check",
                        severity=DriftSeverity.MEDIUM.value,
                        message="No active constraint pack bound to project",
                    )
                ],
                severity=DriftSeverity.MEDIUM.value,
            )
            self._reports[report.report_id] = report
            return {
                "success": True,
                "action": "drift_check",
                "artifacts": report.to_dict(),
            }

        if event_id and ingester:
            evt_result = ingester.get_event(event_id)
            if evt_result.get("success"):
                changed_files = evt_result["artifacts"].get("files", [])
                for cf in changed_files:
                    violations.append(
                        DriftViolation(
                            rule="changed_file",
                            severity=DriftSeverity.LOW.value,
                            message=f"Externally changed file: {cf}",
                            file=cf,
                        )
                    )

        if target_path and pack_name and pack_version:
            import os

            if os.path.exists(target_path):
                rule_result = packs.run_all_rules(pack_name, pack_version, target_path)
                if not rule_result.get("pass", True):
                    for issue in rule_result.get("issues", []):
                        violations.append(
                            DriftViolation(
                                rule=issue.get("rule", "unknown"),
                                severity=issue.get(
                                    "severity", DriftSeverity.MEDIUM.value
                                ),
                                message=issue.get("message", ""),
                                file=issue.get("file", ""),
                            )
                        )

        report = DriftReport(
            project_id=project_id,
            pack_name=pack_name,
            pack_version=pack_version,
            violations=violations,
        )
        self._reports[report.report_id] = report

        logger.info(
            f"Drift check for {project_id}: {len(violations)} violations, severity={report.severity}"
        )

        return {
            "success": True,
            "action": "drift_check",
            "artifacts": report.to_dict(),
        }
