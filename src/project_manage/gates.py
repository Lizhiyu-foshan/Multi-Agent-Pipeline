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
