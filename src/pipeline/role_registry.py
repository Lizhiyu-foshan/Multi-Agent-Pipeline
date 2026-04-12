"""
Role Registry - Dynamic role registration and management.

Unlike the reference (predefined fixed roles), this supports:
- Dynamic roles created from bmad-evo analysis results
- Dynamic capabilities
- Persistent state with atomic writes
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Role, RoleMetrics, RoleConfig, DateTimeEncoder

logger = logging.getLogger(__name__)


class RoleRegistry:
    def __init__(self, state_file: str = None):
        if state_file is None:
            state_file = Path.cwd() / ".pipeline" / "state" / "roles.json"
        else:
            state_file = Path(state_file)
        self.state_file = state_file
        self.roles: Dict[str, Role] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        with self._lock:
            if not self.state_file.exists():
                return
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for rid, rdata in data.get("roles", {}).items():
                    self.roles[rid] = Role.from_dict(rdata)
                logger.info(f"Loaded {len(self.roles)} roles")
            except json.JSONDecodeError:
                self._backup_corrupted()
                self.roles = {}
            except Exception as e:
                logger.error(f"Failed to load roles: {e}")
                self.roles = {}

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
                data = {
                    "roles": {rid: r.to_dict() for rid, r in self.roles.items()},
                    "last_updated": datetime.now().isoformat(),
                }
                dir_name = os.path.dirname(self.state_file)
                fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(
                            data, f, indent=2, ensure_ascii=False, cls=DateTimeEncoder
                        )
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_path, str(self.state_file))
                except Exception:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                    raise
            except Exception as e:
                logger.error(f"Failed to save roles: {e}")

    def _backup_corrupted(self):
        try:
            if self.state_file.exists():
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup = f"{self.state_file}.corrupted.{ts}"
                os.rename(str(self.state_file), backup)
        except Exception as e:
            logger.error(f"Backup failed: {e}")

    def register(
        self,
        role_type: str,
        name: str,
        capabilities: List[str],
        config: RoleConfig = None,
    ) -> str:
        with self._lock:
            role_id = role_type
            if role_id in self.roles:
                logger.debug(f"Role already exists: {role_id}")
                return role_id
            role = Role(
                id=role_id,
                type=role_type,
                name=name,
                capabilities=capabilities,
                config=config or RoleConfig(),
            )
            self.roles[role_id] = role
            self._save()
            logger.info(
                f"Registered role: {role_id} ({name}) [{', '.join(capabilities)}]"
            )
            return role_id

    def register_from_analysis(self, analysis_result: Dict[str, Any]) -> List[str]:
        """
        Register roles dynamically from bmad-evo analysis output.

        Expected analysis_result format:
        {
            "roles": [
                {"type": "architect", "name": "架构师", "capabilities": ["design", ...]},
                ...
            ]
        }
        """
        registered = []
        for role_def in analysis_result.get("roles", []):
            rid = self.register(
                role_type=role_def["type"],
                name=role_def.get("name", role_def["type"]),
                capabilities=role_def.get("capabilities", []),
            )
            registered.append(rid)
        return registered

    def get(self, role_id: str) -> Optional[Role]:
        with self._lock:
            return self.roles.get(role_id)

    def get_by_type(self, role_type: str) -> List[Role]:
        with self._lock:
            return [r for r in self.roles.values() if r.type == role_type]

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                rid: {
                    "id": r.id,
                    "type": r.type,
                    "name": r.name,
                    "status": r.status,
                    "queue_depth": len(r.queue),
                    "current_task": r.current_task,
                    "capabilities": r.capabilities,
                    "metrics": r.metrics.to_dict(),
                }
                for rid, r in self.roles.items()
            }

    def update_status(self, role_id: str, status: str, current_task: str = None):
        with self._lock:
            role = self.roles.get(role_id)
            if role:
                role.status = status
                if current_task is not None:
                    role.current_task = current_task
                self._save()

    def add_to_queue(self, role_id: str, task_id: str):
        with self._lock:
            role = self.roles.get(role_id)
            if role and task_id not in role.queue:
                role.queue.append(task_id)
                self._save()

    def remove_from_queue(self, role_id: str, task_id: str):
        with self._lock:
            role = self.roles.get(role_id)
            if role and task_id in role.queue:
                role.queue.remove(task_id)
                self._save()

    def update_metrics(self, role_id: str, duration_seconds: float, success: bool):
        with self._lock:
            role = self.roles.get(role_id)
            if role:
                role.metrics.update(duration_seconds, success)
                self._save()

    def list_all(self) -> List[Role]:
        with self._lock:
            return list(self.roles.values())

    def get_idle_roles(self, role_type: str = None) -> List[Role]:
        with self._lock:
            idle = [r for r in self.roles.values() if r.status == "idle"]
            if role_type:
                idle = [r for r in idle if r.type == role_type]
            return idle

    def unregister(self, role_id: str) -> bool:
        with self._lock:
            if role_id in self.roles:
                del self.roles[role_id]
                self._save()
                logger.info(f"Unregistered role: {role_id}")
                return True
            return False
