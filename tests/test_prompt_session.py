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


def test_load_with_diagnostics_for_missing_session(temp_dir):
    sm = SessionManager(state_dir=temp_dir)
    session, diag = sm.load_with_diagnostics("sess_missing")
    assert session is None
    assert diag["status"] == "missing"
    assert diag["failure"]["code"] == "SESSION_NOT_FOUND"


def test_single_active_session_per_pipeline_supersedes_old(temp_dir):
    sm = SessionManager(state_dir=temp_dir)
    s1 = PromptPassingSession(
        session_id="sess_old",
        pipeline_id="pipe_same",
        task_id="task_1",
        skill_name="s",
    )
    s2 = PromptPassingSession(
        session_id="sess_new",
        pipeline_id="pipe_same",
        task_id="task_2",
        skill_name="s",
    )

    sm.save(s1)
    sm.save(s2)

    assert sm.load("sess_old") is None
    assert sm.load("sess_new") is not None

    failure = sm.get_last_failure("sess_old")
    assert failure is not None
    assert failure["code"] == "SESSION_SUPERSEDED"


def test_save_rejects_state_dir_mismatch(temp_dir):
    sm = SessionManager(state_dir=temp_dir)
    sess = PromptPassingSession(
        session_id="sess_mismatch",
        pipeline_id="pipe_1",
        task_id="task_1",
        skill_name="s",
        context={"_sessions_state_dir": str(Path(temp_dir) / "other_sessions")},
    )

    with pytest.raises(ValueError, match="Session state dir mismatch"):
        sm.save(sess)
