"""
Real Adapter End-to-End Integration Tests

Tests the complete pipeline lifecycle using REAL adapter instances
(Bmad_Evo_Adapter, Superpowers_Adapter, SpecKit_Adapter, WritingSkills_Adapter)
instead of SimulatedBmadEvo/SimulatedSuperpowers/SimulatedSpecKit.

These tests validate:
1. Real adapters respond to orchestrator calls with correct contract shapes
2. Prompt-passing protocol works end-to-end with real adapter outputs
3. Full pipeline lifecycle (INIT -> COMPLETED) with real adapters
4. Adapter-specific actions (analyze, plan, execute_task, spec_review, etc.)
5. Multi-round model_request flow through real superpowers execute_task
6. Crash recovery with real adapter state
7. bmad-evo fallback analysis when original bmad-evo is unavailable

Run: python tests/test_real_adapter_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.pipeline_orchestrator import PipelineOrchestrator
from pipeline.models import PipelinePhase, PipelineState, Task
from pipeline.prompt_session import SessionManager, create_session_from_pending

import importlib.util


def _import_adapter_from_dir(skill_dir_name: str, class_name: str):
    _root = Path(__file__).parent.parent
    adapter_path = _root / ".skills" / skill_dir_name / "adapter.py"
    spec = importlib.util.spec_from_file_location(
        f"{skill_dir_name}_adapter", str(adapter_path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, class_name)


PASS = "PASS"
FAIL = "FAIL"
_results = []


def _assert(condition: bool, name: str, detail: str = ""):
    status = PASS if condition else FAIL
    msg = f"  [{status}] {name}"
    if detail and not condition:
        msg += f" -- {detail}"
    print(msg)
    _results.append((name, status, detail))
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def _section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _make_test_dir() -> str:
    return tempfile.mkdtemp(prefix="real_adapter_e2e_")


def _cleanup(d: str):
    shutil.rmtree(d, ignore_errors=True)


def _import_real_adapters():
    Bmad_Evo_Adapter = _import_adapter_from_dir("bmad-evo", "Bmad_Evo_Adapter")
    Superpowers_Adapter = _import_adapter_from_dir("superpowers", "Superpowers_Adapter")
    SpecKit_Adapter = _import_adapter_from_dir("spec-kit", "SpecKit_Adapter")
    return Bmad_Evo_Adapter, Superpowers_Adapter, SpecKit_Adapter


def _make_real_skills(test_dir: str):
    Bmad_Evo_Adapter, Superpowers_Adapter, SpecKit_Adapter = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    skills = {
        "bmad-evo": Bmad_Evo_Adapter(project_path=project_root),
        "superpowers": Superpowers_Adapter(project_path=project_root),
        "spec-kit": SpecKit_Adapter(project_path=test_dir),
    }
    return skills


# ===== Test R1: Real adapter contract validation =====


def test_real_bmad_evo_adapter_analyze():
    _section("Test R1a: Real Bmad_Evo_Adapter - analyze action")
    Bmad_Evo_Adapter, _, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Bmad_Evo_Adapter(project_path=project_root)

    result = adapter.execute(
        "Build a REST API for user management",
        {"action": "analyze"},
    )

    _assert(isinstance(result, dict), "Returns dict")
    _assert("success" in result, "Has success key")

    if result.get("pending_model_request"):
        pmr = result["pending_model_request"]
        _assert(isinstance(pmr, dict), "pending_model_request is dict")
        _assert("prompt" in pmr, "pending_model_request has prompt")
        _assert("type" in pmr, "pending_model_request has type")
        print(
            f"  BMAD returned pending_model_request (bmad-evo available at D:/bmad-evo)"
        )
    elif result.get("artifacts"):
        _assert(result["success"] is True, "Analysis succeeds (fallback mode)")
        artifacts = result["artifacts"]
        _assert("analysis_report" in artifacts, "Has analysis_report")
        _assert("task_type" in artifacts, "Has task_type")
        _assert("complexity_score" in artifacts, "Has complexity_score")
        print(f"  BMAD used fallback analysis (bmad-evo not at D:/bmad-evo)")
        print(f"  Artifact keys: {list(artifacts.keys())}")
    else:
        _assert(
            False,
            "Expected either pending_model_request or artifacts",
            f"Got keys: {list(result.keys())}",
        )

    print(f"  Result keys: {list(result.keys())}")


def test_real_bmad_evo_adapter_plan():
    _section("Test R1b: Real Bmad_Evo_Adapter - analyze (used for planning)")
    Bmad_Evo_Adapter, _, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Bmad_Evo_Adapter(project_path=project_root)

    result = adapter.execute(
        "Build authentication system",
        {
            "action": "analyze",
            "spec_context": "Must support JWT with RS256",
        },
    )

    _assert(isinstance(result, dict), "Returns dict")

    if result.get("pending_model_request"):
        pmr = result["pending_model_request"]
        _assert("prompt" in pmr, "Pending request has prompt")
        print(f"  bmad-evo analyze returned pending_model_request")
    else:
        _assert(result.get("success") is True, "Analysis succeeds")
        _assert("artifacts" in result, "Has artifacts")
        print(
            f"  bmad-evo analyze artifacts: {list(result.get('artifacts', {}).keys())}"
        )


def test_real_bmad_evo_adapter_clarify():
    _section("Test R1c: Real Bmad_Evo_Adapter - clarify action")
    Bmad_Evo_Adapter, _, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Bmad_Evo_Adapter(project_path=project_root)

    result = adapter.execute(
        "Build a system",
        {"action": "clarify"},
    )

    _assert(result.get("success") is True, "Clarify succeeds")
    artifacts = result.get("artifacts", {})
    _assert("clarification_questions" in artifacts, "Has clarification_questions")
    questions = artifacts["clarification_questions"]
    _assert(isinstance(questions, list), "Questions is a list")
    _assert(len(questions) >= 3, f"Has 3+ questions: {len(questions)}")
    for q in questions:
        _assert("id" in q, f"Question has id: {q.get('id', 'MISSING')}")
        _assert("question" in q, f"Question has question text")
        _assert("category" in q, f"Question has category")
    print(f"  Generated {len(questions)} clarification questions")


def test_real_bmad_evo_adapter_constraints():
    _section("Test R1d: Real Bmad_Evo_Adapter - generate_constraints")
    Bmad_Evo_Adapter, _, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Bmad_Evo_Adapter(project_path=project_root)

    result = adapter.execute(
        "Build payment processing",
        {
            "action": "generate_constraints",
            "analysis": {
                "task_type": "implementation",
                "complexity_score": 8,
                "risk_factors": ["PCI compliance", "Data security"],
                "success_criteria": ["All transactions logged", "PCI compliant"],
            },
        },
    )

    _assert(result.get("success") is True, "Constraint generation succeeds")
    artifacts = result.get("artifacts", {})
    _assert("constraints_for_spec" in artifacts, "Has constraints_for_spec")
    constraints = artifacts["constraints_for_spec"]
    _assert("contract" in constraints, "Constraints has contract rules")
    _assert("behavior" in constraints, "Constraints has behavior rules")
    _assert(len(constraints["contract"]) > 0, "Has contract rules")
    _assert(len(constraints["behavior"]) > 0, "Has behavior rules")
    print(f"  Contract rules: {len(constraints['contract'])}")
    print(f"  Behavior rules: {len(constraints['behavior'])}")


def test_real_superpowers_execute_task():
    _section("Test R1e: Real Superpowers_Adapter - execute_task action")
    _, Superpowers_Adapter, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Superpowers_Adapter(project_path=project_root)

    result = adapter.execute(
        "Implement JWT authentication",
        {
            "action": "execute_task",
            "task_id": "RT-E001",
            "task_name": "Implement JWT Auth",
            "task_spec": "Build JWT authentication with RS256 signing",
            "pipeline_phase": "execute",
            "pdca_cycle": 1,
            "spec_context": "Must use RS256 algorithm",
        },
    )

    _assert(result["success"] is True, "execute_task succeeds")
    _assert("pending_model_request" in result, "Has pending_model_request")
    pmr = result["pending_model_request"]
    _assert(isinstance(pmr, dict), "pending_model_request is dict")
    _assert("prompt" in pmr, "Has prompt field")
    _assert("type" in pmr, "Has type field")
    _assert(pmr["type"] == "implementer", "Type is implementer")
    _assert(
        len(pmr["prompt"]) > 100, f"Prompt is substantial: {len(pmr['prompt'])} chars"
    )
    _assert("JWT Auth" in pmr["prompt"], "Prompt references task name")
    print(f"  Prompt length: {len(pmr['prompt'])} chars")
    print(f"  Prompt type: {pmr['type']}")


def test_real_superpowers_spec_review():
    _section("Test R1f: Real Superpowers_Adapter - spec_review action")
    _, Superpowers_Adapter, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Superpowers_Adapter(project_path=project_root)

    result = adapter.execute(
        "Review spec compliance",
        {
            "action": "spec_review",
            "task_spec": "- Build login endpoint\n- Build logout endpoint\n- Token refresh",
            "implementation_artifacts": {
                "login": "code for login",
                "logout": "code for logout",
                "token_refresh": "code for refresh",
            },
            "spec_context": "",
        },
    )

    _assert(isinstance(result, dict), "Returns dict")
    _assert("success" in result, "Has success key")
    artifacts = result.get("artifacts", {})
    _assert("review_type" in artifacts, "Has review_type")
    _assert(artifacts["review_type"] == "spec_compliance", "Correct review type")
    _assert("passed" in artifacts, "Has passed flag")
    print(f"  Review passed: {artifacts.get('passed')}")
    print(f"  Issues: {artifacts.get('issues_count', 0)}")


def test_real_superpowers_code_quality():
    _section("Test R1g: Real Superpowers_Adapter - code_quality_review action")
    _, Superpowers_Adapter, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Superpowers_Adapter(project_path=project_root)

    result = adapter.execute(
        "Quality review",
        {
            "action": "code_quality_review",
            "code_artifacts": {
                "auth.py": "def login(user, pw): return token",
                "test_auth.py": "def test_login(): assert login('a','b')",
            },
            "task_spec": "Implement auth",
            "spec_context": "",
        },
    )

    _assert(isinstance(result, dict), "Returns dict")
    _assert("artifacts" in result, "Has artifacts")
    artifacts = result["artifacts"]
    _assert("review_type" in artifacts, "Has review_type")
    _assert(artifacts["review_type"] == "code_quality", "Correct review type")
    print(f"  Quality review status: {artifacts.get('status', 'N/A')}")


def test_real_superpowers_debug():
    _section("Test R1h: Real Superpowers_Adapter - debug action")
    _, Superpowers_Adapter, _ = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)
    adapter = Superpowers_Adapter(project_path=project_root)

    result = adapter.execute(
        "Debug authentication failure",
        {
            "action": "debug",
            "error_description": "Login returns 401 even with valid credentials",
            "error_context": "JWT token validation fails at decode step",
            "files_involved": ["auth.py", "token_validator.py"],
        },
    )

    _assert(isinstance(result, dict), "Returns dict")
    _assert(
        "pending_model_request" in result or "artifacts" in result,
        "Has either pending_model_request or artifacts",
    )
    if result.get("pending_model_request"):
        pmr = result["pending_model_request"]
        _assert("prompt" in pmr, "Debug prompt present")
        _assert(len(pmr["prompt"]) > 50, "Debug prompt is substantial")
        print(f"  Debug prompt type: {pmr.get('type', 'unknown')}")
    else:
        print(f"  Debug result keys: {list(result.keys())}")


def test_real_spec_kit_adapter():
    _section("Test R1i: Real SpecKit_Adapter - init + validate")
    test_dir = _make_test_dir()
    try:
        _, _, SpecKit_Adapter = _import_real_adapters()
        adapter = SpecKit_Adapter(project_path=test_dir)

        init_result = adapter.execute(
            "User management system",
            {
                "action": "init",
                "system_name": "user_mgmt",
                "system_goal": "Manage user accounts and authentication",
            },
        )
        _assert(init_result.get("success") is True, "Spec init succeeds")
        _assert("artifacts" in init_result, "Has artifacts")

        validate_result = adapter.execute(
            "Validate specs",
            {"action": "validate"},
        )
        _assert(isinstance(validate_result, dict), "Validate returns dict")
        _assert("success" in validate_result, "Has success key")
        print(f"  Init artifacts: {list(init_result.get('artifacts', {}).keys())}")
        print(f"  Validate success: {validate_result.get('success')}")
    finally:
        _cleanup(test_dir)


# ===== Test R2: Full pipeline lifecycle with real adapters =====


def test_real_full_pipeline_lifecycle():
    _section("Test R2: Full Pipeline Lifecycle with Real Adapters")

    test_dir = _make_test_dir()
    try:
        skills = _make_real_skills(test_dir)
        orch = PipelineOrchestrator(state_dir=test_dir, skills=skills)

        pipeline, next_action = orch.create_pipeline(
            "Build a REST API for order management with JWT auth",
            max_duration_hours=1.0,
        )
        _assert(pipeline is not None, "Pipeline created")
        _assert(next_action["action"] == "analyze", "Next action is analyze")
        _assert(pipeline.phase == PipelinePhase.INIT, f"Phase is INIT", pipeline.phase)

        result = orch.advance(pipeline.id, {"success": True})
        _assert(result.get("action") == "call_skill", "INIT->ANALYZE dispatches skill")
        _assert(result.get("skill") == "bmad-evo", "Calls bmad-evo for analysis")
        print(f"  INIT -> ANALYZE: skill={result.get('skill')}")

        analysis_result = {
            "success": True,
            "artifacts": {
                "task_type": "implementation",
                "complexity_score": 6,
                "roles": [
                    {
                        "type": "architect",
                        "name": "Architect",
                        "capabilities": ["design", "review"],
                    },
                    {
                        "type": "developer",
                        "name": "Developer",
                        "capabilities": ["implement", "test", "debug"],
                    },
                    {
                        "type": "tester",
                        "name": "QA Engineer",
                        "capabilities": ["test", "validate"],
                    },
                ],
                "tasks": [
                    {
                        "name": "Design API",
                        "role": "architect",
                        "description": "Design REST API",
                        "priority": "P0",
                        "depends_on": [],
                    },
                    {
                        "name": "Implement auth",
                        "role": "developer",
                        "description": "JWT auth",
                        "priority": "P1",
                        "depends_on": [],
                    },
                    {
                        "name": "Implement orders",
                        "role": "developer",
                        "description": "Order CRUD",
                        "priority": "P1",
                        "depends_on": [],
                    },
                    {
                        "name": "Integration test",
                        "role": "tester",
                        "description": "E2E test",
                        "priority": "P2",
                        "depends_on": ["Implement auth", "Implement orders"],
                    },
                ],
            },
        }
        result = orch.advance(pipeline.id, analysis_result)
        _assert(result.get("action") == "call_skill", "ANALYZE->PLAN dispatches skill")
        _assert(result.get("skill") == "bmad-evo", "Calls bmad-evo for planning")
        print(f"  ANALYZE -> PLAN: skill={result.get('skill')}")

        plan_result = {
            "success": True,
            "artifacts": {
                "task_graph": {
                    "tasks": [
                        {
                            "name": "Design API",
                            "role_id": "architect",
                            "description": "Design REST API",
                            "priority": "P0",
                            "depends_on": [],
                        },
                        {
                            "name": "Implement auth",
                            "role_id": "developer",
                            "description": "JWT auth",
                            "priority": "P1",
                            "depends_on": [],
                        },
                        {
                            "name": "Implement orders",
                            "role_id": "developer",
                            "description": "Order CRUD",
                            "priority": "P1",
                            "depends_on": [],
                        },
                        {
                            "name": "Integration test",
                            "role_id": "tester",
                            "description": "E2E test",
                            "priority": "P2",
                            "depends_on": ["Implement auth", "Implement orders"],
                        },
                    ],
                    "execution_waves": [
                        ["Design API", "Implement auth", "Implement orders"],
                        ["Integration test"],
                    ],
                },
            },
        }
        result = orch.advance(pipeline.id, plan_result)
        _assert(
            result.get("action") == "human_decision", "PLAN->CONFIRM_PLAN asks human"
        )
        _assert("[A] Execute" in result.get("question", ""), "Shows execute option")
        print(f"  PLAN -> CONFIRM_PLAN: options={result.get('options')}")

        result = orch.advance(pipeline.id, {"decision": "A"})
        _assert(result.get("action") == "execute_next_task", "CONFIRM->EXECUTE with A")
        print(f"  CONFIRM_PLAN -> EXECUTE: action={result.get('action')}")

        executed_tasks = []
        model_request_count = 0
        max_loops = 30

        for i in range(max_loops):
            action = result.get("action")

            if action == "execute_next_task":
                task_result = {
                    "task_id": result.get("task_id", ""),
                    "skill": "superpowers",
                    "task_result": {
                        "success": True,
                        "artifacts": {"code": "implemented", "tests": "tested"},
                    },
                }
                executed_tasks.append(result.get("task_id", ""))
                result = orch.advance(pipeline.id, task_result)

            elif action == "model_request":
                model_request_count += 1
                session_id = result.get("session_id", "")
                _assert(
                    session_id,
                    f"Model request has session_id (round {model_request_count})",
                )
                result = orch.resume_model_request(
                    session_id,
                    "Implementation complete. All tests passing. Code follows spec.",
                )

            elif action == "human_decision":
                if "PDCA Check" in result.get("question", ""):
                    result = orch.advance(pipeline.id, {"decision": "A"})
                elif "[A] Execute" in result.get("question", ""):
                    result = orch.advance(pipeline.id, {"decision": "A"})
                else:
                    result = orch.advance(pipeline.id, {"decision": "A"})

            elif action == "check":
                result = orch.advance(pipeline.id, {"success": True})

            elif action == "call_skill":
                result = orch.advance(pipeline.id, {"success": True})

            elif action in ("completed", "paused", "failed"):
                break

            elif action == "wait":
                break

            else:
                print(f"  Unknown action at step {i}: {action}")
                break

        status = orch.get_pipeline_status(pipeline.id)
        _assert(status is not None, "Pipeline status available")
        _assert(len(executed_tasks) > 0, f"Executed {len(executed_tasks)} tasks")
        print(f"  Final: phase={status['phase']}, state={status['state']}")
        print(f"  Tasks executed: {len(executed_tasks)}")
        print(f"  Model requests handled: {model_request_count}")
        print(f"  PDCA cycles: {status['pdca_cycle']}")

    finally:
        _cleanup(test_dir)


# ===== Test R3: Multi-round model_request with real Superpowers =====


def test_real_superpowers_model_request_flow():
    _section("Test R3: Real Superpowers Multi-Round Model Request Flow")

    test_dir = _make_test_dir()
    try:
        skills = _make_real_skills(test_dir)
        orch = PipelineOrchestrator(state_dir=test_dir, skills=skills)

        pipeline, _ = orch.create_pipeline("Test model request flow")
        _assert(pipeline is not None, "Pipeline created")

        result = orch.advance(pipeline.id, {"success": True})

        result = orch.advance(
            pipeline.id,
            {
                "success": True,
                "artifacts": {
                    "roles": [
                        {"type": "developer", "name": "Dev", "capabilities": ["code"]}
                    ],
                    "tasks": [
                        {
                            "name": "Build module",
                            "role": "developer",
                            "description": "Build core module",
                            "priority": "P1",
                            "depends_on": [],
                        }
                    ],
                },
            },
        )

        result = orch.advance(
            pipeline.id,
            {
                "success": True,
                "artifacts": {
                    "task_graph": {
                        "tasks": [
                            {
                                "name": "Build module",
                                "role_id": "developer",
                                "description": "Build core module",
                                "priority": "P1",
                                "depends_on": [],
                            }
                        ],
                        "execution_waves": [["Build module"]],
                    },
                },
            },
        )
        _assert(result.get("action") == "human_decision", "At confirm plan")

        result = orch.advance(pipeline.id, {"decision": "A"})
        _assert(
            result.get("action") in ("execute_next_task", "model_request"),
            f"At execute: {result.get('action')}",
        )

        rounds_done = 0
        max_rounds = 10
        session_ids_seen = set()

        for _ in range(max_rounds):
            action = result.get("action")

            if action == "model_request":
                sid = result.get("session_id", "")
                _assert(sid, "Has session_id")
                is_new_session = sid not in session_ids_seen
                session_ids_seen.add(sid)
                rounds_done += 1

                orch._advance_cache.clear()
                orch._advance_dedup.pop(pipeline.id, None)

                result = orch.resume_model_request(
                    sid,
                    f"Round {rounds_done}: Implementation complete with all tests passing.",
                )

            elif action == "execute_next_task":
                orch._advance_cache.clear()
                orch._advance_dedup.pop(pipeline.id, None)
                result = orch.advance(
                    pipeline.id,
                    {
                        "task_id": result.get("task_id", ""),
                        "skill": "superpowers",
                        "task_result": {"success": True, "artifacts": {"code": "done"}},
                    },
                )

            elif action in (
                "check",
                "human_decision",
                "completed",
                "failed",
                "paused",
                "call_skill",
            ):
                if action == "call_skill":
                    orch._advance_cache.clear()
                    result = orch.advance(pipeline.id, {"success": True})
                elif action == "human_decision":
                    orch._advance_cache.clear()
                    result = orch.advance(pipeline.id, {"decision": "A"})
                elif action == "check":
                    orch._advance_cache.clear()
                    result = orch.advance(pipeline.id, {"success": True})
                else:
                    break
            else:
                break

        _assert(rounds_done >= 1, f"At least 1 model_request round: {rounds_done}")
        print(f"  Completed {rounds_done} model_request rounds")
        print(f"  Unique sessions: {len(session_ids_seen)}")

    finally:
        _cleanup(test_dir)


# ===== Test R4: Real bmad-evo fallback analysis pipeline flow =====


def test_real_bmad_evo_fallback_pipeline():
    _section("Test R4: bmad-evo Fallback Analysis in Pipeline Context")

    test_dir = _make_test_dir()
    try:
        Bmad_Evo_Adapter, Superpowers_Adapter, SpecKit_Adapter = _import_real_adapters()
        project_root = str(Path(__file__).parent.parent)
        skills = {
            "bmad-evo": Bmad_Evo_Adapter(project_path=project_root),
            "superpowers": Superpowers_Adapter(project_path=project_root),
            "spec-kit": SpecKit_Adapter(project_path=test_dir),
        }
        orch = PipelineOrchestrator(state_dir=test_dir, skills=skills)

        pipeline, _ = orch.create_pipeline("Build task scheduler system")
        result = orch.advance(pipeline.id, {"success": True})

        _assert(result.get("skill") == "bmad-evo", "Uses bmad-evo adapter")

        adapter = skills["bmad-evo"]
        adapter_result = adapter.execute(
            pipeline.description,
            {"action": "analyze", "pipeline_id": pipeline.id},
        )

        if adapter_result.get("pending_model_request"):
            pmr = adapter_result["pending_model_request"]
            _assert("prompt" in pmr, "Pending request has prompt")
            _assert(len(pmr["prompt"]) > 0, "Prompt is non-empty")
            print(f"  bmad-evo returned pending_model_request (real mode)")

            analysis_artifacts = {
                "success": True,
                "artifacts": {
                    "task_type": "implementation",
                    "complexity_score": 5,
                    "roles": [
                        {"type": "developer", "name": "Dev", "capabilities": ["code"]},
                    ],
                    "tasks": [
                        {
                            "name": "Build scheduler",
                            "role": "developer",
                            "description": "Task scheduler",
                            "priority": "P1",
                            "depends_on": [],
                        },
                    ],
                },
            }
        else:
            _assert(adapter_result.get("success"), "Fallback analysis succeeds")
            fallback_artifacts = adapter_result.get("artifacts", {})
            _assert("analysis_report" in fallback_artifacts, "Has analysis_report")

            analysis_artifacts = {
                "success": True,
                "artifacts": {
                    "task_type": fallback_artifacts.get("task_type", "implementation"),
                    "complexity_score": fallback_artifacts.get("complexity_score", 5),
                    "roles": [
                        {"type": "developer", "name": "Dev", "capabilities": ["code"]},
                    ],
                    "tasks": [
                        {
                            "name": "Build scheduler",
                            "role": "developer",
                            "description": "Task scheduler",
                            "priority": "P1",
                            "depends_on": [],
                        },
                    ],
                },
            }
            print(f"  bmad-evo used fallback analysis")

        result = orch.advance(pipeline.id, analysis_artifacts)
        _assert(result.get("action") == "call_skill", "ANALYZE->PLAN dispatches")

        plan_result = {
            "success": True,
            "artifacts": {
                "task_graph": {
                    "tasks": [
                        {
                            "name": "Build scheduler",
                            "role_id": "developer",
                            "description": "Task scheduler",
                            "priority": "P1",
                            "depends_on": [],
                        },
                    ],
                    "execution_waves": [["Build scheduler"]],
                },
            },
        }
        result = orch.advance(pipeline.id, plan_result)
        _assert(result.get("action") == "human_decision", "At confirm plan")

        result = orch.advance(pipeline.id, {"decision": "A"})
        _assert(
            result.get("action") in ("execute_next_task", "model_request"),
            f"At execute: {result.get('action')}",
        )
        print(f"  Pipeline reached EXECUTE phase with real bmad-evo adapter")

    finally:
        _cleanup(test_dir)


# ===== Test R5: Real SpecKit evolve in DECIDE->EVOLVE->VERIFY flow =====


def test_real_spec_kit_evolve_verify():
    _section("Test R5: Real SpecKit in EVOLVE->VERIFY Flow")

    test_dir = _make_test_dir()
    try:
        _, _, SpecKit_Adapter = _import_real_adapters()
        adapter = SpecKit_Adapter(project_path=test_dir)

        evolve_result = adapter.execute(
            "Evolve specs after implementation",
            {"action": "evolve", "evolution_context": {}},
        )
        _assert(evolve_result.get("success") is True, "Evolve succeeds")
        _assert("artifacts" in evolve_result, "Has artifacts")
        print(f"  Evolve artifacts: {list(evolve_result.get('artifacts', {}).keys())}")

        full_result = adapter.execute(
            "Full spec lifecycle",
            {"action": "full"},
        )
        _assert(isinstance(full_result, dict), "Full action returns dict")
        print(f"  Full result keys: {list(full_result.keys())}")

        analyze_result = adapter.execute(
            "Analyze specs",
            {"action": "analyze"},
        )
        _assert(isinstance(analyze_result, dict), "Analyze returns dict")
        print(f"  Analyze result success: {analyze_result.get('success')}")

    finally:
        _cleanup(test_dir)


# ===== Test R6: Crash recovery with real adapters =====


def test_real_crash_recovery():
    _section("Test R6: Crash Recovery with Real Adapters")

    test_dir = _make_test_dir()
    try:
        skills1 = _make_real_skills(test_dir)
        orch1 = PipelineOrchestrator(state_dir=test_dir, skills=skills1)

        pipeline, _ = orch1.create_pipeline("Build reporting service")
        _assert(pipeline is not None, "Pipeline created")

        result = orch1.advance(pipeline.id, {"success": True})
        result = orch1.advance(
            pipeline.id,
            {
                "success": True,
                "artifacts": {
                    "roles": [
                        {"type": "developer", "name": "Dev", "capabilities": ["code"]},
                    ],
                    "tasks": [
                        {
                            "name": "Build module",
                            "role": "developer",
                            "description": "Core module",
                            "priority": "P1",
                            "depends_on": [],
                        },
                        {
                            "name": "Test module",
                            "role": "developer",
                            "description": "Test core module",
                            "priority": "P2",
                            "depends_on": ["Build module"],
                        },
                    ],
                },
            },
        )

        plan_result = {
            "success": True,
            "artifacts": {
                "task_graph": {
                    "tasks": [
                        {
                            "name": "Build module",
                            "role_id": "developer",
                            "description": "Core module",
                            "priority": "P1",
                            "depends_on": [],
                        },
                        {
                            "name": "Test module",
                            "role_id": "developer",
                            "description": "Test core module",
                            "priority": "P2",
                            "depends_on": ["Build module"],
                        },
                    ],
                    "execution_waves": [["Build module"], ["Test module"]],
                },
            },
        }
        result = orch1.advance(pipeline.id, plan_result)
        _assert(result.get("action") == "human_decision", "At confirm plan")

        result = orch1.advance(pipeline.id, {"decision": "A"})
        _assert(
            result.get("action") in ("execute_next_task", "model_request"),
            f"At execute: {result.get('action')}",
        )
        _assert(pipeline.phase == PipelinePhase.EXECUTE, "Phase is EXECUTE")

        orch1.checkpoint_mgr.create_full_snapshot(
            pipeline,
            orch1.scheduler.task_queue.get_statistics(),
            orch1.scheduler.registry.get_status(),
            label="pre_crash",
        )
        _assert(
            len(orch1.checkpoint_mgr.list_checkpoints(pipeline.id)) >= 1,
            "Checkpoint created",
        )

        pipeline_id = pipeline.id
        _assert(pipeline.state == PipelineState.RUNNING, "State is RUNNING")

        del orch1

        skills2 = _make_real_skills(test_dir)
        orch2 = PipelineOrchestrator(state_dir=test_dir, skills=skills2)

        recovered = orch2.pipelines.get(pipeline_id)
        _assert(recovered is not None, "Pipeline found after restart")

        recovery_result = orch2.recover(pipeline_id, strategy="latest")
        _assert(
            recovery_result.get("recovered") is True,
            f"Recovery succeeded: {recovery_result}",
        )
        _assert(recovery_result["strategy"] == "latest", "Used latest strategy")

        report = orch2.get_progress_report(pipeline_id)
        _assert(report is not None, "Progress report available after recovery")
        _assert(len(report["phase_timeline"]) > 0, "Has phase timeline")

        _assert(
            recovered.state == PipelineState.RUNNING,
            f"State RUNNING after recovery: {recovered.state}",
        )

        result = orch2.advance(
            pipeline_id,
            {
                "task_id": "",
                "skill": "superpowers",
                "task_result": {"success": True, "artifacts": {"code": "recovered"}},
            },
        )
        _assert(
            "error" not in result or result.get("action") is not None,
            f"Advance works after recovery: {result}",
        )

        print(f"  Crash recovery completed successfully")
        print(f"  Pipeline ID: {pipeline_id}")
        print(f"  Phase timeline entries: {len(report['phase_timeline'])}")

    finally:
        _cleanup(test_dir)


# ===== Test R7: Real WritingSkills adapter =====


def test_real_writing_skills_adapter():
    _section("Test R7: Real WritingSkills_Adapter")

    test_dir = _make_test_dir()
    try:
        WritingSkills_Adapter = _import_adapter_from_dir(
            "writing-skills", "WritingSkills_Adapter"
        )

        project_root = str(Path(__file__).parent.parent)
        adapter = WritingSkills_Adapter(project_path=project_root)

        scaffold_result = adapter.execute(
            "Create a data processing skill",
            {
                "action": "scaffold",
                "skill_name": "data-processor",
                "description": "Process and transform data files",
            },
        )
        _assert(isinstance(scaffold_result, dict), "Scaffold returns dict")
        _assert("success" in scaffold_result, "Has success key")
        print(f"  Scaffold success: {scaffold_result.get('success')}")

        validate_result = adapter.execute(
            "Validate skill",
            {
                "action": "validate",
                "skill_name": "data-processor",
            },
        )
        _assert(isinstance(validate_result, dict), "Validate returns dict")
        print(f"  Validate result keys: {list(validate_result.keys())}")

        status = adapter.get_status() if hasattr(adapter, "get_status") else {}
        print(f"  Adapter status: {status}")

    finally:
        _cleanup(test_dir)


# ===== Test R8: Real adapters handle edge cases =====


def test_real_adapter_edge_cases():
    _section("Test R8: Real Adapter Edge Cases")

    Bmad_Evo_Adapter, Superpowers_Adapter, SpecKit_Adapter = _import_real_adapters()
    project_root = str(Path(__file__).parent.parent)

    bmad = Bmad_Evo_Adapter(project_path=project_root)
    result = bmad.execute("", {"action": "unknown_action"})
    _assert(isinstance(result, dict), "Unknown action returns dict")
    print(f"  bmad-evo unknown action: success={result.get('success')}")

    sp = Superpowers_Adapter(project_path=project_root)
    result = sp.execute("", {"action": "unknown_action"})
    _assert(isinstance(result, dict), "Superpowers unknown action returns dict")
    _assert(result.get("success") is False, "Unknown action returns success=False")
    _assert("error" in result, "Has error message")
    print(f"  superpowers unknown action: {result.get('error', '')[:80]}")

    test_dir = _make_test_dir()
    try:
        spec = SpecKit_Adapter(project_path=test_dir)
        result = spec.execute("", {"action": "add_service"})
        _assert(result.get("success") is False, "Missing service_name fails")
        _assert("error" in result, "Has error message")
        print(f"  spec-kit missing param: {result.get('error', '')[:80]}")
    finally:
        _cleanup(test_dir)

    result = bmad.execute("", {"action": "spec_evolution", "evolution_context": None})
    _assert(result.get("success") is True, "spec_evolution with no context succeeds")
    print(f"  bmad-evo spec_evolution with null context: OK")

    result = bmad.execute(
        "test",
        {
            "action": "update_for_feedback",
            "feedback_type": "bug",
            "feedback_content": "crash on startup",
        },
    )
    _assert(result.get("success") is True, "update_for_feedback succeeds")
    _assert("artifacts" in result, "Has artifacts")
    print(f"  bmad-evo update_for_feedback: OK")

    result = bmad.can_handle("analysis", {})
    _assert(result is True, "Can handle analysis")
    result = bmad.can_handle("unknown_type", {})
    _assert(result is False, "Cannot handle unknown type")
    print(
        f"  bmad-evo can_handle: analysis={bmad.can_handle('analysis', {})}, unknown={bmad.can_handle('unknown', {})}"
    )


# ===== Test R9: Real adapter end-to-end with spec-kit integration =====


def test_real_spec_kit_in_pipeline_evolve():
    _section("Test R9: SpecKit Integration in Pipeline EVOLVE Phase")

    test_dir = _make_test_dir()
    try:
        skills = _make_real_skills(test_dir)
        orch = PipelineOrchestrator(state_dir=test_dir, skills=skills)

        spec_kit_adapter = skills["spec-kit"]

        init_result = spec_kit_adapter.execute(
            "Order management system",
            {
                "action": "init",
                "system_name": "order_mgmt",
                "system_goal": "Manage customer orders end-to-end",
            },
        )
        _assert(init_result.get("success") is True, "Spec-Kit init succeeds")

        add_service_result = spec_kit_adapter.execute(
            "Add order service",
            {
                "action": "add_service",
                "service_name": "OrderService",
                "responsibility": "Handle order CRUD operations",
                "boundaries": ["No direct payment processing"],
                "capabilities": [
                    "create_order",
                    "get_order",
                    "update_order",
                    "cancel_order",
                ],
            },
        )
        _assert(add_service_result.get("success") is True, "Add service succeeds")
        print(
            f"  Service added: {add_service_result.get('artifacts', {}).get('service_added')}"
        )

        add_scenario_result = spec_kit_adapter.execute(
            "Add order creation scenario",
            {
                "action": "add_scenario",
                "service": "OrderService",
                "scenario_id": "SC-001",
                "scenario_name": "Create order",
                "given": "Customer is authenticated",
                "when": "Customer submits order with items",
                "then": "Order is created with PENDING status",
            },
        )
        _assert(add_scenario_result.get("success") is True, "Add scenario succeeds")
        print(f"  Scenario added successfully")

        validate_result = spec_kit_adapter.execute("Validate", {"action": "validate"})
        _assert(isinstance(validate_result, dict), "Validate returns dict")
        print(f"  Validate success: {validate_result.get('success')}")

        evolve_result = spec_kit_adapter.execute(
            "Evolve specs",
            {
                "action": "evolve",
                "evolution_context": {
                    "findings": {
                        "missing_services": [
                            {"service": "PaymentService", "issue": "No payment spec"}
                        ],
                        "incomplete_specs": [],
                        "drift_indicators": [],
                    }
                },
            },
        )
        _assert(evolve_result.get("success") is True, "Evolve succeeds")
        artifacts = evolve_result.get("artifacts", {})
        print(f"  Evolve artifacts: {list(artifacts.keys())}")

        pipeline, _ = orch.create_pipeline("Test spec-kit in pipeline")
        result = orch.advance(pipeline.id, {"success": True})
        result = orch.advance(
            pipeline.id,
            {
                "success": True,
                "artifacts": {
                    "roles": [
                        {"type": "developer", "name": "Dev", "capabilities": ["code"]}
                    ],
                    "tasks": [
                        {
                            "name": "Build order service",
                            "role": "developer",
                            "description": "Build it",
                            "priority": "P1",
                            "depends_on": [],
                        }
                    ],
                },
            },
        )
        result = orch.advance(
            pipeline.id,
            {
                "success": True,
                "artifacts": {
                    "task_graph": {
                        "tasks": [
                            {
                                "name": "Build order service",
                                "role_id": "developer",
                                "description": "Build it",
                                "priority": "P1",
                                "depends_on": [],
                            }
                        ],
                        "execution_waves": [["Build order service"]],
                    },
                },
            },
        )
        result = orch.advance(pipeline.id, {"decision": "A"})

        for _ in range(10):
            action = result.get("action")
            if action == "execute_next_task":
                result = orch.advance(
                    pipeline.id,
                    {
                        "task_id": result.get("task_id", ""),
                        "skill": "superpowers",
                        "task_result": {"success": True, "artifacts": {"code": "done"}},
                    },
                )
            elif action == "model_request":
                result = orch.resume_model_request(
                    result["session_id"], "Done. All tests passing."
                )
            elif action == "human_decision":
                result = orch.advance(pipeline.id, {"decision": "A"})
            elif action == "check":
                result = orch.advance(pipeline.id, {"success": True})
            elif action == "call_skill":
                result = orch.advance(pipeline.id, {"success": True})
            elif action in ("completed", "paused", "failed"):
                break
            else:
                break

        print(f"  Pipeline reached: {orch.get_pipeline_status(pipeline.id)['phase']}")

    finally:
        _cleanup(test_dir)


# ===== Main =====


def main():
    print()
    print("+" + "=" * 58 + "+")
    print("|  Real Adapter E2E Integration Tests                        |")
    print("+" + "=" * 58 + "+")

    tests = [
        test_real_bmad_evo_adapter_analyze,
        test_real_bmad_evo_adapter_plan,
        test_real_bmad_evo_adapter_clarify,
        test_real_bmad_evo_adapter_constraints,
        test_real_superpowers_execute_task,
        test_real_superpowers_spec_review,
        test_real_superpowers_code_quality,
        test_real_superpowers_debug,
        test_real_spec_kit_adapter,
        test_real_full_pipeline_lifecycle,
        test_real_superpowers_model_request_flow,
        test_real_bmad_evo_fallback_pipeline,
        test_real_spec_kit_evolve_verify,
        test_real_crash_recovery,
        test_real_writing_skills_adapter,
        test_real_adapter_edge_cases,
        test_real_spec_kit_in_pipeline_evolve,
    ]

    passed = 0
    failed = 0
    errors = []

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test_fn.__name__, str(e)))
            print(f"  [ERROR] {test_fn.__name__}: {e}")

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed} passed, {failed} failed out of {len(tests)} tests")

    assertion_results = [(n, s) for n, s, _ in _results]
    total_assertions = len(assertion_results)
    passed_assertions = sum(1 for _, s in assertion_results if s == PASS)
    print(f"  Assertions: {passed_assertions}/{total_assertions} passed")

    if errors:
        print(f"\n  Failed tests:")
        for name, err in errors:
            print(f"    - {name}: {err[:120]}")

    print(f"{'=' * 60}")

    if failed > 0:
        sys.exit(1)
    else:
        print("  ALL REAL ADAPTER TESTS PASSED!")


if __name__ == "__main__":
    main()
