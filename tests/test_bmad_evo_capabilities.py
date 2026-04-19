"""
Unit tests for bmad-evo adapter's new capabilities:
- plan action with task_graph generation
- eval_and_update action for post-PDCA evaluation
- _continue_analysis roles/tasks extraction
- _build_task_graph and _compute_execution_waves
- _fallback_analysis includes roles and tasks
"""

import sys
import json
import pytest
import importlib.util
from pathlib import Path

SRC_PATH = str(Path(__file__).resolve().parents[1] / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)


def _import_adapter():
    adapter_path = Path(__file__).resolve().parents[1] / ".skills" / "bmad-evo" / "adapter.py"
    spec = importlib.util.spec_from_file_location("bmad_evo_adapter", str(adapter_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Bmad_Evo_Adapter


@pytest.fixture
def adapter():
    Cls = _import_adapter()
    return Cls(project_path=str(Path(__file__).resolve().parents[1]))


class TestContinueAnalysisExtractsRolesAndTasks:
    def test_extracts_roles_from_model_response(self, adapter):
        response = json.dumps({
            "task_type": "feature",
            "complexity_score": 7,
            "roles": [
                {"type": "developer", "name": "dev1", "capabilities": ["code"]},
                {"type": "reviewer", "name": "rev1", "capabilities": ["review"]},
            ],
            "tasks": [
                {"name": "task1", "description": "Do something"},
            ],
        })
        result = adapter._continue_analysis(response, "test task", "")
        assert result["success"] is True
        assert len(result["artifacts"]["roles"]) == 2
        assert result["artifacts"]["roles"][0]["type"] == "developer"
        assert len(result["artifacts"]["tasks"]) == 1

    def test_extracts_tasks_from_task_breakdown_key(self, adapter):
        response = json.dumps({
            "task_type": "feature",
            "task_breakdown": [
                {"name": "t1", "description": "First"},
                {"name": "t2", "description": "Second"},
            ],
        })
        result = adapter._continue_analysis(response, "test", "")
        assert result["success"] is True
        assert len(result["artifacts"]["tasks"]) == 2

    def test_extracts_recommended_roles_as_list(self, adapter):
        response = json.dumps({
            "recommended_roles": [
                {"type": "dev", "name": "dev", "capabilities": ["code"]},
            ],
        })
        result = adapter._continue_analysis(response, "test", "")
        assert len(result["artifacts"]["roles"]) == 1

    def test_no_roles_or_tasks_when_absent(self, adapter):
        response = json.dumps({"task_type": "analysis"})
        result = adapter._continue_analysis(response, "test", "")
        assert "roles" not in result["artifacts"]
        assert "tasks" not in result["artifacts"]


class TestBuildTaskGraph:
    def test_builds_graph_from_task_dicts(self, adapter):
        tasks = [
            {"name": "task_a", "description": "First", "role": "developer"},
            {"name": "task_b", "description": "Second", "role": "reviewer", "depends_on": ["task_a"]},
        ]
        graph = adapter._build_task_graph(tasks, [])
        assert len(graph["tasks"]) == 2
        assert graph["tasks"][0]["name"] == "task_a"
        assert graph["tasks"][1]["depends_on"] == ["task_a"]
        assert len(graph["execution_waves"]) >= 1

    def test_handles_string_tasks(self, adapter):
        tasks = ["simple_task_1", "simple_task_2"]
        graph = adapter._build_task_graph(tasks, [])
        assert len(graph["tasks"]) == 2
        assert graph["tasks"][0]["name"] == "simple_task_1"

    def test_empty_tasks(self, adapter):
        graph = adapter._build_task_graph([], [])
        assert graph["tasks"] == []
        assert graph["execution_waves"] == []


class TestComputeExecutionWaves:
    def test_independent_tasks_single_wave(self, adapter):
        tasks = [
            {"name": "a", "depends_on": []},
            {"name": "b", "depends_on": []},
        ]
        waves = adapter._compute_execution_waves(tasks)
        assert len(waves) == 1
        assert set(waves[0]) == {0, 1}

    def test_linear_dependency_three_waves(self, adapter):
        tasks = [
            {"name": "a", "depends_on": []},
            {"name": "b", "depends_on": ["a"]},
            {"name": "c", "depends_on": ["b"]},
        ]
        waves = adapter._compute_execution_waves(tasks)
        assert len(waves) == 3
        assert 0 in waves[0]
        assert 1 in waves[1]
        assert 2 in waves[2]

    def test_diamond_dependency(self, adapter):
        tasks = [
            {"name": "a", "depends_on": []},
            {"name": "b", "depends_on": ["a"]},
            {"name": "c", "depends_on": ["a"]},
            {"name": "d", "depends_on": ["b", "c"]},
        ]
        waves = adapter._compute_execution_waves(tasks)
        assert len(waves) == 3
        assert 0 in waves[0]
        assert set(waves[1]) == {1, 2}
        assert 3 in waves[2]


class TestHandlePlan:
    def test_plan_from_context_tasks_and_roles(self, adapter):
        context = {
            "action": "plan",
            "roles": [
                {"type": "developer", "name": "dev", "capabilities": ["code"]},
            ],
            "tasks": [
                {"name": "t1", "description": "Do first", "role": "developer"},
                {"name": "t2", "description": "Do second", "role": "developer", "depends_on": ["t1"]},
            ],
        }
        result = adapter.execute("Plan the project", context)
        assert result["success"] is True
        assert "task_graph" in result["artifacts"]
        tg = result["artifacts"]["task_graph"]
        assert len(tg["tasks"]) == 2
        assert len(tg["execution_waves"]) >= 1
        assert "plan_report" in result["artifacts"]

    def test_plan_fallback_without_bmad(self, adapter):
        context = {"action": "plan"}
        result = adapter.execute("Plan the project", context)
        assert result["success"] is True or result.get("pending_model_request") is not None
        if result["success"]:
            assert "task_graph" in result["artifacts"]
            assert len(result["artifacts"]["task_graph"]["tasks"]) >= 3

    def test_continue_plan_from_model_response(self, adapter):
        model_response = json.dumps({
            "task_graph": {
                "tasks": [
                    {"name": "step1", "description": "First step", "role": "developer", "depends_on": []},
                ],
                "execution_waves": [[0]],
            },
            "roles": [{"type": "developer", "name": "dev", "capabilities": ["code"]}],
        })
        context = {
            "action": "plan",
            "model_response": model_response,
            "model_request_id": "req123",
        }
        result = adapter.execute("Plan", context)
        assert result["success"] is True
        assert len(result["artifacts"]["task_graph"]["tasks"]) == 1

    def test_continue_plan_builds_graph_from_tasks_list(self, adapter):
        model_response = json.dumps({
            "tasks": [
                {"name": "a", "description": "Task A"},
                {"name": "b", "description": "Task B", "depends_on": ["a"]},
            ],
        })
        context = {
            "action": "plan",
            "model_response": model_response,
            "model_request_id": "req456",
        }
        result = adapter.execute("Plan", context)
        assert result["success"] is True
        tg = result["artifacts"]["task_graph"]
        assert len(tg["tasks"]) == 2


class TestHandleEvalAndUpdate:
    def test_eval_with_issues_generates_fix_tasks(self, adapter):
        context = {
            "action": "eval_and_update",
            "pdca_cycle": 1,
            "task_results": {"completed": 3, "failed": 2, "total": 5},
            "issues": [
                {"name": "test_auth", "error": "AssertionError: expected 200"},
                {"name": "test_api", "error": "TimeoutError"},
            ],
            "existing_backlog": [],
            "existing_analysis": {"task_type": "feature"},
        }
        result = adapter.execute("Evaluate PDCA cycle", context)
        assert result["success"] is True
        assert len(result["artifacts"]["new_tasks"]) == 2
        assert all(t["origin"] == "pdca_discovery" for t in result["artifacts"]["new_tasks"])
        assert result["artifacts"]["success_rate"] == 60.0
        assert len(result["artifacts"]["quality_recommendations"]) > 0
        assert "updated_analysis" in result["artifacts"]
        assert "eval_report" in result["artifacts"]

    def test_eval_with_backlog_includes_backlog_items(self, adapter):
        context = {
            "action": "eval_and_update",
            "pdca_cycle": 2,
            "task_results": {"completed": 5, "failed": 0, "total": 5},
            "issues": [],
            "existing_backlog": [
                {"name": "feature_x", "description": "Implement feature X", "status": "pending", "role": "developer"},
                {"name": "feature_y", "description": "Implement feature Y", "status": "pending", "role": "developer"},
                {"name": "feature_z", "description": "Done", "status": "completed"},
            ],
            "existing_analysis": {},
        }
        result = adapter.execute("Evaluate", context)
        assert result["success"] is True
        new_names = {t["name"] for t in result["artifacts"]["new_tasks"]}
        assert "feature_x" in new_names
        assert "feature_y" in new_names
        assert "feature_z" not in new_names

    def test_eval_100_percent_no_issues_no_new_tasks(self, adapter):
        context = {
            "action": "eval_and_update",
            "pdca_cycle": 3,
            "task_results": {"completed": 5, "failed": 0, "total": 5},
            "issues": [],
            "existing_backlog": [],
            "existing_analysis": {},
        }
        result = adapter.execute("Evaluate", context)
        assert result["success"] is True
        assert len(result["artifacts"]["new_tasks"]) == 0
        assert result["artifacts"]["success_rate"] == 100.0

    def test_eval_low_success_rate_recommendation(self, adapter):
        context = {
            "action": "eval_and_update",
            "pdca_cycle": 1,
            "task_results": {"completed": 1, "failed": 4, "total": 5},
            "issues": [{"name": "t1", "error": "fail"}],
            "existing_backlog": [],
            "existing_analysis": {},
        }
        result = adapter.execute("Evaluate", context)
        recs = result["artifacts"]["quality_recommendations"]
        assert any("Low success rate" in r for r in recs)

    def test_eval_updates_analysis_with_pdca_history(self, adapter):
        context = {
            "action": "eval_and_update",
            "pdca_cycle": 1,
            "task_results": {"completed": 4, "failed": 1, "total": 5},
            "issues": [],
            "existing_backlog": [],
            "existing_analysis": {"task_type": "feature", "complexity_score": 7},
        }
        result = adapter.execute("Evaluate", context)
        updated = result["artifacts"]["updated_analysis"]
        assert "pdca_history" in updated
        assert updated["pdca_history"][0]["cycle"] == 1
        assert updated["task_type"] == "feature"


class TestFallbackAnalysisIncludesRolesAndTasks:
    def test_fallback_has_roles(self, adapter):
        result = adapter._fallback_analysis("test task", "")
        assert "roles" in result["artifacts"]
        assert len(result["artifacts"]["roles"]) == 2
        assert result["artifacts"]["roles"][0]["type"] == "developer"

    def test_fallback_has_tasks(self, adapter):
        result = adapter._fallback_analysis("build feature", "")
        assert "tasks" in result["artifacts"]
        assert len(result["artifacts"]["tasks"]) >= 1
