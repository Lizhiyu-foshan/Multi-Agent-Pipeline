"""
Project Registry - Registration and lifecycle management.

Handles:
- CRUD operations on ProjectRecord
- Lifecycle state transitions (init/active/paused/completed/abandoned/archived)
- Persistence to state/global/projects.json
- Project-scoped state directory creation
"""

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ProjectRecord, ProjectStatus

logger = logging.getLogger(__name__)


class ProjectRegistry:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = state_dir
        self._global_dir = Path(state_dir) / "global"
        self._projects_dir = Path(state_dir) / "projects"
        self._projects_file = self._global_dir / "projects.json"
        self._lock = threading.RLock()
        self._projects: Dict[str, ProjectRecord] = {}
        self._load()

    def _load(self):
        if not self._projects_file.exists():
            return
        try:
            with open(self._projects_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for pid, pdata in data.items():
                self._projects[pid] = ProjectRecord.from_dict(pdata)
            logger.info(f"Loaded {len(self._projects)} projects")
        except Exception as e:
            logger.error(f"Failed to load projects: {e}")

    def _save(self):
        with self._lock:
            try:
                os.makedirs(str(self._global_dir), exist_ok=True)
                data = {pid: p.to_dict() for pid, p in self._projects.items()}
                dir_name = str(self._global_dir)
                fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, str(self._projects_file))
                except Exception:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
            except Exception as e:
                logger.error(f"Failed to save projects: {e}")

    def _ensure_project_dirs(self, project_id: str):
        base = self._projects_dir / project_id
        for subdir in (
            "pipelines",
            "sessions",
            "checkpoints",
            "metrics",
            "workspace",
            "staging",
        ):
            (base / subdir).mkdir(parents=True, exist_ok=True)

    def register(self, project: ProjectRecord) -> Dict[str, Any]:
        with self._lock:
            if project.project_id in self._projects:
                return {
                    "success": False,
                    "error": f"Project {project.project_id} already exists",
                }
            self._projects[project.project_id] = project
            self._ensure_project_dirs(project.project_id)
            self._save()
            logger.info(f"Registered project: {project.project_id} ({project.name})")
            return {
                "success": True,
                "action": "project_registered",
                "project_id": project.project_id,
                "artifacts": project.to_dict(),
            }

    def get(self, project_id: str) -> Dict[str, Any]:
        with self._lock:
            project = self._projects.get(project_id)
            if not project:
                return {"success": False, "error": f"Project {project_id} not found"}
            return {
                "success": True,
                "action": "project_get",
                "project_id": project_id,
                "artifacts": project.to_dict(),
            }

    def list_projects(self, status: str = None) -> Dict[str, Any]:
        with self._lock:
            projects = list(self._projects.values())
            if status and status != "all":
                projects = [p for p in projects if p.status == status]
            return {
                "success": True,
                "action": "project_list",
                "artifacts": {
                    "projects": [p.to_dict() for p in projects],
                    "total": len(projects),
                    "filter": status,
                },
            }

    def update(self, project_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            project = self._projects.get(project_id)
            if not project:
                return {"success": False, "error": f"Project {project_id} not found"}
            updatable = [
                "name",
                "target_path",
                "repo_url",
                "default_branch",
                "tech_stack",
                "active_pack",
                "metadata",
            ]
            for key in updatable:
                if key in updates:
                    setattr(project, key, updates[key])
            project.updated_at = datetime.now()
            self._save()
            return {
                "success": True,
                "action": "project_update",
                "project_id": project_id,
                "artifacts": project.to_dict(),
            }

    def transition(self, project_id: str, new_status: str) -> Dict[str, Any]:
        with self._lock:
            project = self._projects.get(project_id)
            if not project:
                return {"success": False, "error": f"Project {project_id} not found"}
            if not project.can_transition_to(new_status):
                return {
                    "success": False,
                    "error": f"Cannot transition {project.project_id} from {project.status} to {new_status}",
                    "current_status": project.status,
                    "allowed": list(
                        project.VALID_TRANSITIONS.get(project.status, set())
                    ),
                }
            old_status = project.status
            project.status = new_status
            project.updated_at = datetime.now()
            if new_status == ProjectStatus.ARCHIVED.value:
                project.archived_at = datetime.now()
            self._save()
            logger.info(f"Project {project_id}: {old_status} -> {new_status}")
            return {
                "success": True,
                "action": "project_transition",
                "project_id": project_id,
                "artifacts": {
                    "from": old_status,
                    "to": new_status,
                    "project": project.to_dict(),
                },
            }

    def delete(self, project_id: str, keep_files: bool = True) -> Dict[str, Any]:
        with self._lock:
            project = self._projects.get(project_id)
            if not project:
                return {"success": False, "error": f"Project {project_id} not found"}
            del self._projects[project_id]
            self._save()
            state_dir = self._projects_dir / project_id
            if not keep_files and state_dir.exists():
                shutil.rmtree(str(state_dir), ignore_errors=True)
            logger.info(f"Deleted project: {project_id} (keep_files={keep_files})")
            return {
                "success": True,
                "action": "project_delete",
                "project_id": project_id,
                "keep_files": keep_files,
            }
