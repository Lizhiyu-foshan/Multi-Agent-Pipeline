import shutil
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.prompt_session import PromptPassingSession, SessionManager


@pytest.fixture
def temp_dir():
    td = tempfile.mkdtemp()
    yield td
    shutil.rmtree(td, ignore_errors=True)


def test_session_manager_basic_save_load_remove(temp_dir):
    sm = SessionManager(state_dir=temp_dir)
    sess = PromptPassingSession(pipeline_id="pipe_1", task_id="task_1", skill_name="s")
    sid = sm.save(sess)
    loaded = sm.load(sid)
    assert loaded is not None
    assert loaded.pipeline_id == "pipe_1"

    sm.remove(sid)
    assert sm.load(sid) is None


def test_session_manager_thread_safety(temp_dir):
    sm = SessionManager(state_dir=temp_dir)
    errors = []

    def worker(i):
        try:
            s = PromptPassingSession(
                pipeline_id=f"pipe_{i % 5}",
                task_id=f"task_{i}",
                skill_name="superpowers",
            )
            sid = sm.save(s)
            _ = sm.load(sid)
            sm.touch(sid)
            if i % 3 == 0:
                sm.remove(sid)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0


def test_session_is_dead_uses_created_at_hard_cap():
    sess = PromptPassingSession(pipeline_id="pipe_1", task_id="task_1", skill_name="s")
    sess.created_at = datetime.now() - timedelta(days=2)
    sess.last_active_at = datetime.now()
    assert sess.is_dead is True
