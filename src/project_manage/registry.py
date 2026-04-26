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
        self._current_file = self._global_dir / "_current.json"
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
            "docs",
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
            current = self.get_current_project()
            if current == project_id:
                self.clear_current_project()
            logger.info(f"Deleted project: {project_id} (keep_files={keep_files})")
            return {
                "success": True,
                "action": "project_delete",
                "project_id": project_id,
                "keep_files": keep_files,
            }

    def get_current_project(self) -> Optional[str]:
        if not self._current_file.exists():
            return None
        try:
            with open(str(self._current_file), "r", encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("project_id", "")
            if pid and pid in self._projects:
                return pid
        except Exception:
            pass
        return None

    def set_current_project(self, project_id: str) -> Dict[str, Any]:
        with self._lock:
            if project_id not in self._projects:
                return {"success": False, "error": f"Project {project_id} not found"}
            previous = self.get_current_project()
            paused_project = None
            resumed_project = None

            if previous and previous != project_id:
                old_proj = self._projects.get(previous)
                if old_proj and old_proj.status == ProjectStatus.ACTIVE.value:
                    old_proj.status = ProjectStatus.PAUSED.value
                    old_proj.updated_at = datetime.now()
                    paused_project = previous
                    logger.info(f"Auto-paused project: {previous}")

            new_proj = self._projects.get(project_id)
            if new_proj and new_proj.status == ProjectStatus.PAUSED.value:
                new_proj.status = ProjectStatus.ACTIVE.value
                new_proj.updated_at = datetime.now()
                resumed_project = project_id
                logger.info(f"Auto-resumed project: {project_id}")

            data = {
                "project_id": project_id,
                "switched_at": datetime.now().isoformat(),
            }
            os.makedirs(str(self._global_dir), exist_ok=True)
            with open(str(self._current_file), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            if paused_project or resumed_project:
                self._save()

            return {
                "success": True,
                "action": "current_project_set",
                "project_id": project_id,
                "previous_project_id": previous,
                "auto_paused": paused_project,
                "auto_resumed": resumed_project,
            }

    def clear_current_project(self):
        if self._current_file.exists():
            try:
                self._current_file.unlink()
            except Exception:
                pass

    def find_by_name(self, name: str) -> Optional[str]:
        with self._lock:
            for pid, proj in self._projects.items():
                if proj.name == name:
                    return pid
        return None

    def compute_health(self, project_id: str) -> Dict[str, Any]:
        project = self._projects.get(project_id)
        if not project:
            return {
                "total": 0,
                "doc_completeness": 0,
                "buildability": 0,
                "task_completion": 0,
                "constraint_adherence": 0,
                "activity": 0,
            }

        doc_completeness = self._score_doc_completeness(project_id)
        buildability = self._score_buildability(project)
        task_completion = self._score_task_completion(project_id)
        constraint_adherence = self._score_constraint_adherence(project)
        activity = self._score_activity(project)

        total = (
            doc_completeness
            + buildability
            + task_completion
            + constraint_adherence
            + activity
        )
        return {
            "total": total,
            "doc_completeness": doc_completeness,
            "buildability": buildability,
            "task_completion": task_completion,
            "constraint_adherence": constraint_adherence,
            "activity": activity,
        }

    def _score_doc_completeness(self, project_id: str) -> int:
        docs_dir = self._projects_dir / project_id / "docs"
        manifest_path = docs_dir / "manifest.json"
        if not manifest_path.exists():
            return 0
        try:
            with open(str(manifest_path), "r", encoding="utf-8") as f:
                manifest = json.load(f)
            docs = manifest.get("documents", {})
            score = 0
            for category, versions in docs.items():
                if not versions:
                    continue
                active = manifest.get("active_versions", {}).get(category)
                if active:
                    file_name = f"{category}_{active}.md"
                    doc_path = docs_dir / file_name
                    if doc_path.exists():
                        content = doc_path.read_text(encoding="utf-8")
                        non_empty = [
                            l
                            for l in content.splitlines()
                            if l.strip() and not l.strip().startswith("*")
                        ]
                        if len(non_empty) > 3:
                            score += 3
                        elif len(non_empty) > 0:
                            score += 1
            return min(score, 20)
        except Exception:
            return 0

    def _score_buildability(self, project: ProjectRecord) -> int:
        source = project.target_path
        if not source or not os.path.isdir(source):
            return 0
        score = 0
        entry_candidates = [
            "app.py", "main.py", "manage.py",
            "index.js", "index.ts", "server.py",
        ]
        if any(os.path.exists(os.path.join(source, e)) for e in entry_candidates):
            score += 10
        if os.path.exists(os.path.join(source, "requirements.txt")) or os.path.exists(
            os.path.join(source, "package.json")
        ):
            score += 10
        return min(score, 20)

    def _score_task_completion(self, project_id: str) -> int:
        docs_dir = self._projects_dir / project_id / "docs"
        manifest_path = docs_dir / "manifest.json"
        if not manifest_path.exists():
            return 10
        try:
            with open(str(manifest_path), "r", encoding="utf-8") as f:
                manifest = json.load(f)
            todo = manifest.get("todo", [])
            if not todo:
                return 10
            total = len(todo)
            completed = sum(1 for t in todo if t.get("status") == "completed")
            return int((completed / total) * 20) if total > 0 else 10
        except Exception:
            return 10

    def _score_constraint_adherence(self, project: ProjectRecord) -> int:
        if project.active_pack:
            return 18
        return 12

    def _score_activity(self, project: ProjectRecord) -> int:
        last_active = project.updated_at
        if not last_active:
            return 0
        try:
            if isinstance(last_active, str):
                last_dt = datetime.fromisoformat(last_active)
            else:
                last_dt = last_active
            hours_ago = (datetime.now() - last_dt).total_seconds() / 3600
            if hours_ago < 24:
                return 20
            elif hours_ago < 168:
                return 15
            elif hours_ago < 720:
                return 10
            else:
                return 5
        except Exception:
            return 5

    def overview(self) -> Dict[str, Any]:
        current = self.get_current_project()
        entries = []
        for pid, proj in self._projects.items():
            health = self.compute_health(pid)
            todo_counts = self._get_todo_counts(pid)
            entries.append(
                {
                    "project_id": pid,
                    "name": proj.name,
                    "status": proj.status,
                    "is_current": pid == current,
                    "health": health,
                    "backlog": f"{todo_counts['completed']}/{todo_counts['total']}",
                    "stack": list(proj.tech_stack.keys()) if proj.tech_stack else [],
                    "last_active": proj.updated_at.isoformat() if proj.updated_at else "",
                }
            )
        return {
            "success": True,
            "action": "overview",
            "artifacts": {"projects": entries, "total": len(entries)},
        }

    def _get_todo_counts(self, project_id: str) -> Dict[str, int]:
        docs_dir = self._projects_dir / project_id / "docs"
        manifest_path = docs_dir / "manifest.json"
        if not manifest_path.exists():
            return {"total": 0, "completed": 0, "pending": 0, "in_progress": 0, "blocked": 0}
        try:
            with open(str(manifest_path), "r", encoding="utf-8") as f:
                manifest = json.load(f)
            todo = manifest.get("todo", [])
            counts = {"total": len(todo), "completed": 0, "pending": 0, "in_progress": 0, "blocked": 0}
            for t in todo:
                s = t.get("status", "pending")
                if s in counts:
                    counts[s] += 1
                else:
                    counts["pending"] += 1
            return counts
        except Exception:
            return {"total": 0, "completed": 0, "pending": 0, "in_progress": 0, "blocked": 0}
