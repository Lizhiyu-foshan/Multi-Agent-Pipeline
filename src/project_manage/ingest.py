"""
External Change Ingestion - Records changes from OpenCode/Git/manual sources.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List

from .models import ExternalChangeEvent

logger = logging.getLogger(__name__)


class ChangeIngester:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._events_dir = Path(state_dir) / "global" / "change_events"
        self._events: Dict[str, ExternalChangeEvent] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        if not self._events_dir.exists():
            return
        for fp in self._events_dir.glob("evt_*.json"):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                evt = ExternalChangeEvent.from_dict(data)
                self._events[evt.event_id] = evt
            except Exception:
                pass

    def _save_event(self, event: ExternalChangeEvent):
        os.makedirs(str(self._events_dir), exist_ok=True)
        fp = self._events_dir / f"{event.event_id}.json"
        dir_name = str(self._events_dir)
        fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(event.to_dict(), f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(fp))
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def ingest(
        self,
        project_id: str,
        source: str = "manual",
        commit_range: str = "",
        files: List[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            event = ExternalChangeEvent(
                project_id=project_id,
                source=source,
                commit_range=commit_range,
                files=files or [],
            )
            self._events[event.event_id] = event
            try:
                self._save_event(event)
            except Exception as e:
                logger.error(f"Failed to save change event: {e}")
            logger.info(
                f"Ingested {len(event.files)} files from {source} for project {project_id}"
            )
            return {
                "success": True,
                "action": "ingest_external_changes",
                "artifacts": {
                    "event_id": event.event_id,
                    "project_id": project_id,
                    "source": source,
                    "files_count": len(event.files),
                    "files": event.files,
                },
            }

    def get_event(self, event_id: str) -> Dict[str, Any]:
        with self._lock:
            event = self._events.get(event_id)
            if not event:
                return {"success": False, "error": f"Event {event_id} not found"}
            return {
                "success": True,
                "artifacts": event.to_dict(),
            }

    def list_events(self, project_id: str = None) -> Dict[str, Any]:
        with self._lock:
            events = list(self._events.values())
            if project_id:
                events = [e for e in events if e.project_id == project_id]
            return {
                "success": True,
                "artifacts": {
                    "events": [e.to_dict() for e in events],
                    "total": len(events),
                },
            }
