import json
import shutil
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.context_manager import ContextManager


def test_context_entry_redacts_sensitive_tokens():
    td = tempfile.mkdtemp()
    try:
        cm = ContextManager(state_dir=td)
        cm.add_entry(
            "pipe_1",
            "task_1",
            "orchestrator",
            "execute",
            "token=abc123 api_key=xyz789 password=hunter2",
        )

        log_path = Path(td) / "pipe_1.log"
        assert log_path.exists()
        text = log_path.read_text(encoding="utf-8")
        assert "abc123" not in text
        assert "xyz789" not in text
        assert "hunter2" not in text
        assert "REDACTED" in text
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_artifact_state_redacts_sensitive_keys_on_disk():
    td = tempfile.mkdtemp()
    try:
        cm = ContextManager(state_dir=td)
        cm.store_artifact(
            "pipe_1",
            "task_1",
            "credentials",
            {
                "api_key": "xyz789",
                "nested": {"token": "abc123"},
                "safe": "value",
            },
        )
        cm.save_state()

        artifacts_file = Path(td) / "artifacts.json"
        assert artifacts_file.exists()
        data = json.loads(artifacts_file.read_text(encoding="utf-8"))
        dumped = json.dumps(data)
        assert "xyz789" not in dumped
        assert "abc123" not in dumped
        assert "REDACTED" in dumped
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_context_snapshot_redacts_sensitive_content():
    td = tempfile.mkdtemp()
    try:
        cm = ContextManager(state_dir=td)
        cm.add_entry(
            "pipe_1",
            "task_1",
            "orchestrator",
            "execute",
            "authorization=Bearer SECRET_TOKEN password=supersecret",
        )
        cm.save_state()

        snapshot_file = Path(td) / "context_state.json"
        assert snapshot_file.exists()
        text = snapshot_file.read_text(encoding="utf-8")
        assert "SECRET_TOKEN" not in text
        assert "supersecret" not in text
        assert "REDACTED" in text
    finally:
        shutil.rmtree(td, ignore_errors=True)
