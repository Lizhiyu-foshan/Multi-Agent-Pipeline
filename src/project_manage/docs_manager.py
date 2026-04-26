"""
Project documentation manager.

Supports:
- Document registry per project/category
- Versioned documents (v1, v2, ...)
- Active version tracking by category
- Todo/progress board for development tracking
- Document changelog with diff tracking
"""

import difflib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


DOC_CATEGORIES = {
    "design_doc",
    "work_breakdown",
    "progress_report",
    "timeline_plan",
    "constraints",
    "acceptance_criteria",
    "test_manual",
}


def _utc_now() -> str:
    return datetime.now().isoformat()


class ProjectDocsManager:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = Path(state_dir)

    def _project_docs_dir(self, project_id: str) -> Path:
        return self._state_dir / "projects" / project_id / "docs"

    def _manifest_path(self, project_id: str) -> Path:
        return self._project_docs_dir(project_id) / "manifest.json"

    def _ensure_project(self, project_id: str):
        docs_dir = self._project_docs_dir(project_id)
        docs_dir.mkdir(parents=True, exist_ok=True)

    def _load_manifest(self, project_id: str) -> Dict[str, Any]:
        self._ensure_project(project_id)
        manifest_path = self._manifest_path(project_id)
        if not manifest_path.exists():
            return {
                "project_id": project_id,
                "documents": {},
                "active_versions": {},
                "todo": [],
                "updated_at": _utc_now(),
            }
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_manifest(self, project_id: str, manifest: Dict[str, Any]):
        self._ensure_project(project_id)
        manifest["updated_at"] = _utc_now()
        path = self._manifest_path(project_id)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _validate_category(self, category: str) -> str:
        if not category:
            return ""
        normalized = str(category).strip().lower()
        return normalized if normalized in DOC_CATEGORIES else ""

    def upsert_document(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        category = self._validate_category(ctx.get("category", ""))
        if not category:
            return {
                "success": False,
                "action": "doc_upsert",
                "error": "Invalid category. Use one of: design_doc, work_breakdown, progress_report, timeline_plan, constraints, acceptance_criteria, test_manual",
            }

        title = str(ctx.get("title", category)).strip()
        content = str(ctx.get("content", "")).strip()
        if not content:
            return {
                "success": False,
                "action": "doc_upsert",
                "error": "content is required",
            }

        manifest = self._load_manifest(project_id)
        docs = manifest.setdefault("documents", {})
        versions = docs.setdefault(category, [])
        next_version = len(versions) + 1
        version_id = f"v{next_version}"

        old_content = ""
        old_active_vid = manifest.get("active_versions", {}).get(category)
        if old_active_vid:
            old_file = f"{category}_{old_active_vid}.md"
            old_path = self._project_docs_dir(project_id) / old_file
            if old_path.exists():
                old_content = old_path.read_text(encoding="utf-8")

        file_name = f"{category}_{version_id}.md"
        rel_path = f"docs/{file_name}"
        abs_path = self._project_docs_dir(project_id) / file_name
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        entry = {
            "version": next_version,
            "version_id": version_id,
            "title": title,
            "category": category,
            "path": rel_path,
            "created_at": _utc_now(),
            "notes": str(ctx.get("notes", "")),
        }
        versions.append(entry)

        if bool(ctx.get("set_active", True)):
            manifest.setdefault("active_versions", {})[category] = version_id

        self._save_manifest(project_id, manifest)

        v_from = next_version - 1
        self.record_change(
            project_id=project_id,
            category=category,
            old_content=old_content,
            new_content=content,
            version_from=v_from,
            version_to=next_version,
            trigger=ctx.get("trigger", "conversation"),
            trigger_reason=ctx.get("trigger_reason", ""),
            pipeline_id=ctx.get("pipeline_id", ""),
            re_evaluated=ctx.get("re_evaluated", False),
            bmad_assessment=ctx.get("bmad_assessment", ""),
        )

        return {
            "success": True,
            "action": "doc_upsert",
            "project_id": project_id,
            "artifacts": {
                "document": entry,
                "active_versions": manifest.get("active_versions", {}),
            },
        }

    def list_documents(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        manifest = self._load_manifest(project_id)
        category = self._validate_category(ctx.get("category", "")) if ctx.get("category") else ""
        docs = manifest.get("documents", {})
        if category:
            docs = {category: docs.get(category, [])}

        return {
            "success": True,
            "action": "doc_list",
            "project_id": project_id,
            "artifacts": {
                "documents": docs,
                "active_versions": manifest.get("active_versions", {}),
                "updated_at": manifest.get("updated_at"),
            },
        }

    def set_active_version(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        category = self._validate_category(ctx.get("category", ""))
        version_id = str(ctx.get("version_id", "")).strip()
        if not category or not version_id:
            return {
                "success": False,
                "action": "doc_set_active",
                "error": "category and version_id are required",
            }

        manifest = self._load_manifest(project_id)
        versions = manifest.get("documents", {}).get(category, [])
        if not any(v.get("version_id") == version_id for v in versions):
            return {
                "success": False,
                "action": "doc_set_active",
                "error": f"Version {version_id} not found for category {category}",
            }

        manifest.setdefault("active_versions", {})[category] = version_id
        self._save_manifest(project_id, manifest)
        return {
            "success": True,
            "action": "doc_set_active",
            "project_id": project_id,
            "artifacts": {
                "category": category,
                "active_version": version_id,
                "active_versions": manifest.get("active_versions", {}),
            },
        }

    def update_todo(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        todo_items = ctx.get("todo_items", [])
        if not isinstance(todo_items, list):
            return {
                "success": False,
                "action": "project_todo_update",
                "error": "todo_items must be a list",
            }

        normalized: List[Dict[str, Any]] = []
        for item in todo_items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "title": str(item.get("title", "")).strip(),
                    "status": str(item.get("status", "pending")).strip().lower(),
                    "owner": str(item.get("owner", "")).strip(),
                    "due": str(item.get("due", "")).strip(),
                    "updated_at": _utc_now(),
                }
            )

        manifest = self._load_manifest(project_id)
        manifest["todo"] = normalized
        self._save_manifest(project_id, manifest)
        return {
            "success": True,
            "action": "project_todo_update",
            "project_id": project_id,
            "artifacts": {
                "todo": normalized,
                "todo_total": len(normalized),
            },
        }

    def project_status(self, project_id: str) -> Dict[str, Any]:
        manifest = self._load_manifest(project_id)
        todo = manifest.get("todo", [])

        counts = {"pending": 0, "in_progress": 0, "blocked": 0, "completed": 0}
        for t in todo:
            status = str(t.get("status", "pending")).lower()
            if status not in counts:
                status = "pending"
            counts[status] += 1

        total = len(todo)
        completed = counts["completed"]
        progress_pct = round((completed / total) * 100, 1) if total else 0.0

        return {
            "success": True,
            "action": "project_status",
            "project_id": project_id,
            "artifacts": {
                "active_versions": manifest.get("active_versions", {}),
                "todo_counts": counts,
                "todo_total": total,
                "progress_pct": progress_pct,
                "todo": todo,
                "documents": manifest.get("documents", {}),
                "updated_at": manifest.get("updated_at"),
            },
        }

    def _changelog_path(self, project_id: str) -> Path:
        d = self._project_docs_dir(project_id) / "history"
        d.mkdir(parents=True, exist_ok=True)
        return d / "changelog.json"

    def _load_changelog(self, project_id: str) -> List[Dict[str, Any]]:
        path = self._changelog_path(project_id)
        if not path.exists():
            return []
        with open(str(path), "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_changelog(self, project_id: str, entries: List[Dict[str, Any]]):
        path = self._changelog_path(project_id)
        with open(str(path), "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def _diff_path(self, project_id: str, category: str, v1: int, v2: int) -> Path:
        d = self._project_docs_dir(project_id) / "history"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{category}_v{v1}_to_v{v2}.diff"

    def record_change(
        self,
        project_id: str,
        category: str,
        old_content: str,
        new_content: str,
        version_from: int,
        version_to: int,
        trigger: str = "conversation",
        trigger_reason: str = "",
        pipeline_id: str = "",
        re_evaluated: bool = False,
        bmad_assessment: str = "",
    ) -> Dict[str, Any]:
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        lines_added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        lines_removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

        if diff_lines:
            dp = self._diff_path(project_id, category, version_from, version_to)
            with open(str(dp), "w", encoding="utf-8") as f:
                f.write("\n".join(diff_lines))

        summary = self._summarize_change(old_content, new_content)

        entry = {
            "timestamp": _utc_now(),
            "trigger": trigger,
            "trigger_reason": trigger_reason,
            "pipeline_id": pipeline_id,
            "file": category,
            "version_from": version_from,
            "version_to": version_to,
            "summary": summary,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "re_evaluated": re_evaluated,
            "bmad_assessment": bmad_assessment,
        }

        changelog = self._load_changelog(project_id)
        changelog.append(entry)
        self._save_changelog(project_id, changelog)

        return entry

    def _summarize_change(self, old_content: str, new_content: str) -> str:
        old_lines = set(l.strip() for l in old_content.splitlines() if l.strip() and not l.strip().startswith("#"))
        new_lines = set(l.strip() for l in new_content.splitlines() if l.strip() and not l.strip().startswith("#"))
        added = new_lines - old_lines
        removed = old_lines - new_lines
        parts = []
        if added:
            sample = list(added)[:3]
            parts.append(f"Added: {'; '.join(sample)[:80]}")
        if removed:
            sample = list(removed)[:3]
            parts.append(f"Removed: {'; '.join(sample)[:80]}")
        if not parts:
            return "Minor formatting changes"
        return " | ".join(parts)

    def get_doc_log(self, project_id: str, category: str = None) -> Dict[str, Any]:
        changelog = self._load_changelog(project_id)
        if category:
            target = category
            changelog = [e for e in changelog if e.get("file", "") == target]
        return {
            "success": True,
            "action": "doc_log",
            "project_id": project_id,
            "artifacts": {
                "entries": changelog,
                "total": len(changelog),
            },
        }

    def get_doc_diff(self, project_id: str, category: str, v1: int, v2: int) -> Dict[str, Any]:
        dp = self._diff_path(project_id, category, v1, v2)
        if not dp.exists():
            return {
                "success": False,
                "action": "doc_diff",
                "error": f"No diff found for {category} v{v1} -> v{v2}",
            }
        with open(str(dp), "r", encoding="utf-8") as f:
            content = f.read()
        return {
            "success": True,
            "action": "doc_diff",
            "project_id": project_id,
            "artifacts": {
                "category": category,
                "version_from": v1,
                "version_to": v2,
                "diff": content,
            },
        }

    def get_doc_content(self, project_id: str, category: str = None) -> Dict[str, Any]:
        manifest = self._load_manifest(project_id)
        docs = manifest.get("documents", {})
        active = manifest.get("active_versions", {})

        if category:
            version_id = active.get(category)
            if not version_id:
                return {
                    "success": False,
                    "action": "doc_content",
                    "error": f"No active version for {category}",
                }
            file_name = f"{category}_{version_id}.md"
            doc_path = self._project_docs_dir(project_id) / file_name
            if not doc_path.exists():
                return {
                    "success": False,
                    "action": "doc_content",
                    "error": f"Document file not found: {file_name}",
                }
            with open(str(doc_path), "r", encoding="utf-8") as f:
                content = f.read()
            return {
                "success": True,
                "action": "doc_content",
                "project_id": project_id,
                "artifacts": {
                    "category": category,
                    "version_id": version_id,
                    "content": content,
                },
            }

        parts = []
        for cat in DOC_CATEGORIES:
            version_id = active.get(cat)
            if not version_id:
                continue
            file_name = f"{cat}_{version_id}.md"
            doc_path = self._project_docs_dir(project_id) / file_name
            if doc_path.exists():
                with open(str(doc_path), "r", encoding="utf-8") as f:
                    content = f.read()
                parts.append(f"{'='*60}")
                parts.append(f"  {cat} ({version_id})")
                parts.append(f"{'='*60}")
                parts.append(content)
                parts.append("")

        return {
            "success": True,
            "action": "doc_content",
            "project_id": project_id,
            "artifacts": {
                "content": "\n".join(parts) if parts else "(no documents)",
            },
        }
