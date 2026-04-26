"""
Gate Evaluation - Unified gate: baseline + drift + quality + compat.
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from .models import GateReport, GateDecision, DriftSeverity

logger = logging.getLogger(__name__)


class GateEvaluator:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = state_dir

    def evaluate(
        self, project_id: str, context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        ctx = context or {}

        baseline_pass = self._check_baseline(ctx)
        drift_pass = self._check_drift(project_id, ctx)
        quality_pass = self._check_quality(ctx)
        compat_pass = self._check_compat(ctx)
        risk_info = self._check_risk(ctx)
        contamination_info = self._check_contamination(ctx)

        baseline_pass = (
            baseline_pass
            and risk_info["pass"]
            and contamination_info["pass"]
        )

        has_critical_drift = ctx.get("drift_severity") == DriftSeverity.CRITICAL.value
        if has_critical_drift:
            baseline_pass = False

        report = GateReport(
            project_id=project_id,
            delivery_id=ctx.get("delivery_id", ""),
            baseline_pass=baseline_pass,
            drift_pass=drift_pass,
            quality_pass=quality_pass,
            compat_pass=compat_pass,
            details={
                "baseline": {"pass": baseline_pass},
                "drift": {
                    "pass": drift_pass,
                    "severity": ctx.get("drift_severity", "none"),
                },
                "quality": {"pass": quality_pass},
                "compat": {"pass": compat_pass},
                "risk": risk_info,
                "contamination": contamination_info,
            },
        )

        return {
            "success": True,
            "action": "evaluate_gates",
            "project_id": project_id,
            "artifacts": report.to_dict(),
        }

    def _check_baseline(self, ctx: Dict[str, Any]) -> bool:
        if "baseline_result" in ctx:
            return bool(ctx["baseline_result"])
        if ctx.get("skip_baseline"):
            return True
        return True

    def _check_drift(self, project_id: str, ctx: Dict[str, Any]) -> bool:
        drift_result = ctx.get("drift_result")
        if drift_result is not None:
            artifacts = drift_result.get("artifacts", {})
            severity = artifacts.get("severity", "none")
            return severity in ("none", "low")
        return True

    def _check_quality(self, ctx: Dict[str, Any]) -> bool:
        if "quality_pass" in ctx:
            return bool(ctx["quality_pass"])
        return True

    def _check_compat(self, ctx: Dict[str, Any]) -> bool:
        if "compat_pass" in ctx:
            return bool(ctx["compat_pass"])
        return True

    def _check_risk(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        allow_high_risk = bool(ctx.get("allow_high_risk", False))
        risk_level = str(ctx.get("risk_level", "")).strip().lower()

        risk_result = ctx.get("risk_result")
        if risk_result is not None:
            artifacts = risk_result.get("artifacts", {})
            risk_level = str(artifacts.get("level", risk_level)).strip().lower()

        if not risk_level:
            risk_level = "none"

        passed = risk_level in ("none", "low", "medium") or allow_high_risk
        return {
            "pass": passed,
            "level": risk_level,
            "allow_high_risk": allow_high_risk,
        }

    def _check_contamination(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        contam_status = str(ctx.get("contamination_status", "")).strip().lower()
        top_severity = str(ctx.get("contamination_top_severity", "")).strip().lower()

        contamination_result = ctx.get("contamination_result")
        if contamination_result is not None:
            artifacts = contamination_result.get("artifacts", {})
            contam_status = str(artifacts.get("status", contam_status)).strip().lower()
            top_severity = str(artifacts.get("top_severity", top_severity)).strip().lower()

        if not contam_status and top_severity:
            contam_status = "pass" if top_severity in ("none", "low") else "blocked"

        if not contam_status:
            contam_status = "pass"
        if not top_severity:
            top_severity = "none"

        passed = contam_status == "pass" and top_severity in ("none", "low")
        return {
            "pass": passed,
            "status": contam_status,
            "top_severity": top_severity,
        }
