from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orchestrator.core_orchestrator import CoreOrchestrator


class _SimpleSkill:
    def execute(self, task_description, context):
        return {"success": True, "artifacts": {"note": "ok"}}


def test_handle_escalation_decision_B_does_not_require_self_agent_loop():
    orch = CoreOrchestrator(project_path=str(Path(__file__).parent.parent))

    skills = {"superpowers": _SimpleSkill()}
    result = orch.handle_escalation_decision(
        skill_name="superpowers",
        decision="B",
        task_description="Fix previous output",
        skills=skills,
        max_seconds=30,
    )

    assert "success" in result
    assert result.get("escalated") in (True, False)


def test_handle_escalation_decision_C_skips():
    orch = CoreOrchestrator(project_path=str(Path(__file__).parent.parent))
    skills = {"superpowers": _SimpleSkill()}
    result = orch.handle_escalation_decision(
        skill_name="superpowers",
        decision="C",
        skills=skills,
    )
    assert result.get("success") is True
    assert result.get("skipped") is True
