import importlib.util
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.pipeline_orchestrator import PipelineOrchestrator
from pipeline.models import PipelinePhase, PipelineState


def _load_superpowers_adapter_class():
    repo_root = Path(__file__).parent.parent
    adapter_path = repo_root / ".skills" / "superpowers" / "adapter.py"
    spec = importlib.util.spec_from_file_location(
        "superpowers_adapter", str(adapter_path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Superpowers_Adapter


class _MockSkill:
    def execute(self, description, context):
        return {"success": True, "artifacts": {"ok": True}}


def test_watchdog_auto_retries_real_model_request_session():
    td = tempfile.mkdtemp(prefix="wd_model_retry_")
    try:
        repo_root = str(Path(__file__).parent.parent)
        Superpowers_Adapter = _load_superpowers_adapter_class()

        orch = PipelineOrchestrator(
            state_dir=td,
            skills={
                "bmad-evo": _MockSkill(),
                "spec-kit": _MockSkill(),
                "superpowers": Superpowers_Adapter(project_path=repo_root),
            },
            watchdog_config={
                "auto_retry_idle_model_request": True,
                "model_request_retry_max_attempts": 2,
                "model_request_retry_cooldown_seconds": 0.0,
                "session_idle_threshold_seconds": 30.0,
            },
        )

        pipeline, _ = orch.create_pipeline("Watchdog real model request retry")

        orch.scheduler.registry.register("developer", "Developer", ["code"])
        t1 = orch.scheduler.submit_task(
            {
                "pipeline_id": pipeline.id,
                "role_id": "developer",
                "name": "Implement auth",
                "description": "Build login/logout with JWT",
                "priority": "P1",
                "depends_on": [],
            }
        )
        pipeline.tasks = [t1["task_id"]]
        pipeline.phase = PipelinePhase.EXECUTE
        pipeline.state = PipelineState.RUNNING
        orch._save_pipelines()

        task_data = orch._get_next_ready_task(pipeline)
        assert task_data is not None

        exec_result = orch._execute_task_with_loop(pipeline, task_data)
        assert exec_result.get("action") == "model_request"
        session_id = exec_result.get("session_id")
        assert session_id

        session = orch.session_manager.load(session_id)
        assert session is not None
        session.last_active_at = datetime.now() - timedelta(seconds=90)
        orch.session_manager._sessions[session_id] = session

        health = orch.watchdog.check(pipeline.id)
        assert health.status.value == "warning"
        assert health.active_session_id == session_id
        assert health.session_idle_seconds >= 30

        action = orch.watchdog.take_action(health)
        assert any(
            f"retry_model_request:{session_id}:attempt_1" in a
            for a in action.get("actions", [])
        )

        retried_session = orch.session_manager.load(session_id)
        assert retried_session is not None
        assert retried_session.context.get("_model_retry_count") == 1

        wd_status = orch.watchdog.get_status()
        assert wd_status["model_retry_attempts"].get(pipeline.id) == 1
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_watchdog_model_retry_cooldown_and_max_attempts_boundary():
    td = tempfile.mkdtemp(prefix="wd_model_retry_boundary_")
    try:
        repo_root = str(Path(__file__).parent.parent)
        Superpowers_Adapter = _load_superpowers_adapter_class()

        orch = PipelineOrchestrator(
            state_dir=td,
            skills={
                "bmad-evo": _MockSkill(),
                "spec-kit": _MockSkill(),
                "superpowers": Superpowers_Adapter(project_path=repo_root),
            },
            watchdog_config={
                "auto_retry_idle_model_request": True,
                "model_request_retry_max_attempts": 2,
                "model_request_retry_cooldown_seconds": 60.0,
                "session_idle_threshold_seconds": 30.0,
            },
        )

        pipeline, _ = orch.create_pipeline("Watchdog model retry boundary")
        orch.scheduler.registry.register("developer", "Developer", ["code"])
        t1 = orch.scheduler.submit_task(
            {
                "pipeline_id": pipeline.id,
                "role_id": "developer",
                "name": "Implement payment",
                "description": "Build payment flow with retries",
                "priority": "P1",
                "depends_on": [],
            }
        )
        pipeline.tasks = [t1["task_id"]]
        pipeline.phase = PipelinePhase.EXECUTE
        pipeline.state = PipelineState.RUNNING
        orch._save_pipelines()

        task_data = orch._get_next_ready_task(pipeline)
        assert task_data is not None

        exec_result = orch._execute_task_with_loop(pipeline, task_data)
        assert exec_result.get("action") == "model_request"
        session_id = exec_result.get("session_id")
        assert session_id

        session = orch.session_manager.load(session_id)
        assert session is not None
        session.last_active_at = datetime.now() - timedelta(seconds=120)
        orch.session_manager._sessions[session_id] = session

        health = orch.watchdog.check(pipeline.id)
        assert health.status.value == "warning"

        action1 = orch.watchdog.take_action(health)
        assert any(
            f"retry_model_request:{session_id}:attempt_1" in a
            for a in action1.get("actions", [])
        )

        action_cooldown = orch.watchdog.take_action(health)
        assert not any(
            a.startswith(f"retry_model_request:{session_id}:attempt_")
            for a in action_cooldown.get("actions", [])
        )
        wd_status_mid = orch.watchdog.get_status()
        assert wd_status_mid["model_retry_attempts"].get(pipeline.id) == 1

        orch.watchdog._last_model_retry_at[pipeline.id] = datetime.now() - timedelta(
            seconds=120
        )
        action2 = orch.watchdog.take_action(health)
        assert any(
            f"retry_model_request:{session_id}:attempt_2" in a
            for a in action2.get("actions", [])
        )

        orch.watchdog._last_model_retry_at[pipeline.id] = datetime.now() - timedelta(
            seconds=120
        )
        action3 = orch.watchdog.take_action(health)
        assert not any(
            f"retry_model_request:{session_id}:attempt_3" in a
            for a in action3.get("actions", [])
        )
        wd_status_final = orch.watchdog.get_status()
        assert wd_status_final["model_retry_attempts"].get(pipeline.id) == 2

        retried_session = orch.session_manager.load(session_id)
        assert retried_session is not None
        assert retried_session.context.get("_model_retry_count") == 2
    finally:
        shutil.rmtree(td, ignore_errors=True)
