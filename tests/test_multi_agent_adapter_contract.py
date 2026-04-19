import shutil
import tempfile
from pathlib import Path
import sys


def _load_adapter_class():
    adapter_path = str(
        Path(__file__).parent.parent / ".skills" / "multi-agent-pipeline"
    )
    if adapter_path not in sys.path:
        sys.path.insert(0, adapter_path)
    from adapter import MultiAgentPipeline_Adapter

    return MultiAgentPipeline_Adapter


class _FakeOrchestrator:
    def __init__(self, result):
        self._result = result

    def advance(self, pipeline_id, phase_result):
        return dict(self._result)

    def retry_model_request(self, session_id, reason="timeout"):
        return {
            "action": "model_request",
            "session_id": session_id,
            "retry": {"count": 1, "max": 5, "reason": reason},
        }


def test_advance_keeps_failure_semantics():
    MultiAgentPipeline_Adapter = _load_adapter_class()
    td = tempfile.mkdtemp()
    try:
        adapter = MultiAgentPipeline_Adapter(state_dir=td, skills={})
        adapter._orchestrator = _FakeOrchestrator({"error": "boom"})

        result = adapter.execute(
            "",
            {
                "action": "advance",
                "pipeline_id": "pipe_1",
                "phase_result": {"success": True},
            },
        )

        assert result.get("success") is False
        assert result.get("error") == "boom"
        assert result.get("pipeline_id") == "pipe_1"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_advance_marks_success_when_no_error():
    MultiAgentPipeline_Adapter = _load_adapter_class()
    td = tempfile.mkdtemp()
    try:
        adapter = MultiAgentPipeline_Adapter(state_dir=td, skills={})
        adapter._orchestrator = _FakeOrchestrator({"action": "execute_next_task"})

        result = adapter.execute(
            "",
            {
                "action": "advance",
                "pipeline_id": "pipe_1",
                "phase_result": {"success": True},
            },
        )

        assert result.get("success") is True
        assert result.get("action") == "execute_next_task"
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_retry_model_request_action_works():
    MultiAgentPipeline_Adapter = _load_adapter_class()
    td = tempfile.mkdtemp()
    try:
        adapter = MultiAgentPipeline_Adapter(state_dir=td, skills={})
        adapter._orchestrator = _FakeOrchestrator({"action": "noop"})

        result = adapter.execute(
            "",
            {
                "action": "retry_model_request",
                "session_id": "sess_123",
                "reason": "rate_limit",
            },
        )

        assert result.get("success") is True
        assert result.get("action") == "model_request"
        assert result.get("retry", {}).get("reason") == "rate_limit"
    finally:
        shutil.rmtree(td, ignore_errors=True)
