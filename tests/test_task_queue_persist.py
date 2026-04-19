import tempfile
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.task_queue import TaskQueue
from pipeline.models import Task


def test_task_queue_persist_writes_state_file():
    td = tempfile.mkdtemp()
    try:
        state_file = Path(td) / "task_queue.json"
        tq = TaskQueue(state_file=str(state_file))

        task = Task(
            id="task_1",
            pipeline_id="pipe_1",
            role_id="developer",
            name="Persist test",
        )
        tq.tasks[task.id] = task
        tq.persist()

        assert state_file.exists()
        text = state_file.read_text(encoding="utf-8")
        assert "task_1" in text
    finally:
        shutil.rmtree(td, ignore_errors=True)
