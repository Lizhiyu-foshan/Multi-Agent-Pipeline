"""
Audit Logger - Append-only audit trail for deliveries, approvals, rollbacks.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._audit_file = Path(state_dir) / "global" / "delivery_audit.jsonl"

    def log(self, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            **data,
        }
        try:
            os.makedirs(str(self._audit_file.parent), exist_ok=True)
            with open(str(self._audit_file), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
        except Exception as e:
            logger.error(f"Audit log write failed: {e}")
            return {"success": False, "error": str(e)}
        return {"success": True, "event_type": event_type}

    def query(
        self,
        project_id: str = None,
        event_type: str = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        entries = []
        if not self._audit_file.exists():
            return {"success": True, "entries": [], "total": 0}
        try:
            with open(str(self._audit_file), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if project_id and entry.get("project_id") != project_id:
                        continue
                    if event_type and entry.get("event_type") != event_type:
                        continue
                    entries.append(entry)
        except Exception as e:
            logger.error(f"Audit log read failed: {e}")
        entries = entries[-limit:]
        return {"success": True, "entries": entries, "total": len(entries)}
