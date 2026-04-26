"""
Project-Manage Skill Adapter.

Multi-project governance: registration, lifecycle, constraint packs,
change ingestion, drift detection, gate evaluation, delivery, dashboard,
document/version tracking, and change-control operations.
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_PIPELINE_SRC = str(Path(__file__).resolve().parent.parent.parent / "src")
if _PIPELINE_SRC not in sys.path:
    sys.path.insert(0, _PIPELINE_SRC)


class ProjectManage_Adapter:
    name = "project-manage"
    version = "0.1.0"

    def __init__(self, state_dir: str = None, config: Dict[str, Any] = None):
        self._state_dir = state_dir
        self._config = config or {}
        self._registry = None
        self._packs = None
        self._ingest = None
        self._drift = None
        self._gates = None
        self._delivery = None
        self._approval = None
        self._audit = None
        self._metrics = None
        self._project_init = None
        self._github_delivery = None
        self._docs = None
        self._change_control = None

    def _get_registry(self):
        if self._registry is None:
            from project_manage.registry import ProjectRegistry

            self._registry = ProjectRegistry(state_dir=self._state_dir)
        return self._registry

    def _get_packs(self):
        if self._packs is None:
            from project_manage.packs import ConstraintPackManager

            self._packs = ConstraintPackManager(
                state_dir=self._state_dir, registry=self._get_registry()
            )
        return self._packs

    def _get_ingest(self):
        if self._ingest is None:
            from project_manage.ingest import ChangeIngester

            self._ingest = ChangeIngester(state_dir=self._state_dir)
        return self._ingest

    def _get_drift(self):
        if self._drift is None:
            from project_manage.drift import DriftDetector

            self._drift = DriftDetector(state_dir=self._state_dir)
        return self._drift

    def _get_gates(self):
        if self._gates is None:
            from project_manage.gates import GateEvaluator

            self._gates = GateEvaluator(state_dir=self._state_dir)
        return self._gates

    def _get_delivery(self):
        if self._delivery is None:
            from project_manage.delivery import DeliveryManager

            self._delivery = DeliveryManager(state_dir=self._state_dir)
        return self._delivery

    def _get_approval(self):
        if self._approval is None:
            from project_manage.approval import ApprovalManager

            self._approval = ApprovalManager(state_dir=self._state_dir)
        return self._approval

    def _get_audit(self):
        if self._audit is None:
            from project_manage.audit import AuditLogger

            self._audit = AuditLogger(state_dir=self._state_dir)
        return self._audit

    def _get_github_delivery(self):
        if self._github_delivery is None:
            from project_manage.github_delivery import GitHubDeliveryManager

            self._github_delivery = GitHubDeliveryManager(state_dir=self._state_dir)
        return self._github_delivery

    def _get_metrics(self):
        if self._metrics is None:
            from project_manage.metrics import MetricsAggregator

            self._metrics = MetricsAggregator(state_dir=self._state_dir)
        return self._metrics

    def _get_docs(self):
        if self._docs is None:
            from project_manage.docs_manager import ProjectDocsManager

            self._docs = ProjectDocsManager(state_dir=self._state_dir)
        return self._docs

    def _get_change_control(self):
        if self._change_control is None:
            from project_manage.change_control import ChangeControlManager

            self._change_control = ChangeControlManager(
                state_dir=self._state_dir,
                registry=self._get_registry(),
            )
        return self._change_control

    def _get_project_init(self):
        if self._project_init is None:
            from project_manage.project_init import ProjectInitializer

            self._project_init = ProjectInitializer(
                state_dir=self._state_dir,
                registry=self._get_registry(),
                packs=self._get_packs(),
            )
        return self._project_init

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "project_list")
        action_map = {
            "project_init": self._action_project_init,
            "project_get": self._action_project_get,
            "project_list": self._action_project_list,
            "project_update": self._action_project_update,
            "project_pause": self._action_project_pause,
            "project_resume": self._action_project_resume,
            "project_archive": self._action_project_archive,
            "project_delete": self._action_project_delete,
            "pack_activate": self._action_pack_activate,
            "ingest_external_changes": self._action_ingest,
            "drift_check": self._action_drift_check,
            "evaluate_gates": self._action_evaluate_gates,
            "deliver_local": self._action_deliver_local,
            "deliver_github": self._action_deliver_github,
            "dashboard_summary": self._action_dashboard,
            "doc_upsert": self._action_doc_upsert,
            "doc_list": self._action_doc_list,
            "doc_set_active": self._action_doc_set_active,
            "doc_log": self._action_doc_log,
            "doc_diff": self._action_doc_diff,
            "doc_content": self._action_doc_content,
            "project_todo_update": self._action_project_todo_update,
            "project_status": self._action_project_status,
            "risk_assess": self._action_risk_assess,
            "contamination_check": self._action_contamination_check,
            "version_backup": self._action_version_backup,
            "version_record": self._action_version_record,
            "version_list": self._action_version_list,
            "merge_update": self._action_merge_update,
            "change_flow": self._action_change_flow,
            "health_check": self._action_health_check,
            "current_get": self._action_current_get,
            "current_switch": self._action_current_switch,
            "overview": self._action_overview,
            "deliver": self._action_deliver,
            "assess_all": self._action_assess_all,
        }
        handler = action_map.get(action)
        if not handler:
            return {
                "success": False,
                "error": f"Unknown action: {action}. Available: {list(action_map.keys())}",
            }
        try:
            return handler(task_description, context)
        except Exception as e:
            logger.error(f"Action {action} failed: {e}")
            return {"success": False, "action": action, "error": str(e)}

    def _action_project_init(self, desc, ctx):
        return self._get_project_init().initialize(desc, ctx)

    def _action_project_get(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        return self._get_registry().get(project_id)

    def _action_project_list(self, desc, ctx):
        status = ctx.get("status")
        return self._get_registry().list_projects(status=status)

    def _action_project_update(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        return self._get_registry().update(project_id, ctx)

    def _action_project_pause(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        return self._get_registry().transition(project_id, "paused")

    def _action_project_resume(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        return self._get_registry().transition(project_id, "active")

    def _action_project_archive(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        return self._get_registry().transition(project_id, "archived")

    def _action_project_delete(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        keep_files = ctx.get("keep_files", True)
        return self._get_registry().delete(project_id, keep_files=keep_files)

    def _action_pack_activate(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        pack_name = ctx.get("pack_name", "")
        version = ctx.get("version", "")
        return self._get_packs().activate(project_id, pack_name, version)

    def _action_ingest(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        return self._get_ingest().ingest(
            project_id=project_id,
            source=ctx.get("source", "manual"),
            commit_range=ctx.get("commit_range", ""),
            files=ctx.get("files", []),
        )

    def _action_drift_check(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        event_id = ctx.get("event_id", "")
        return self._get_drift().check(project_id, event_id=event_id)

    def _action_evaluate_gates(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        return self._get_gates().evaluate(project_id, ctx)

    def _action_deliver_local(self, desc, ctx):
        return self._get_delivery().deliver_local(ctx)

    def _action_deliver_github(self, desc, ctx):
        return self._get_github_delivery().deliver_github(ctx)

    def _action_dashboard(self, desc, ctx):
        return self._get_metrics().summary()

    def _action_doc_upsert(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "doc_upsert", "error": "project_id required"}
        return self._get_docs().upsert_document(project_id, ctx)

    def _action_doc_list(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "doc_list", "error": "project_id required"}
        return self._get_docs().list_documents(project_id, ctx)

    def _action_doc_set_active(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "doc_set_active", "error": "project_id required"}
        return self._get_docs().set_active_version(project_id, ctx)

    def _action_project_todo_update(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "project_todo_update", "error": "project_id required"}
        return self._get_docs().update_todo(project_id, ctx)

    def _action_project_status(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "project_status", "error": "project_id required"}
        return self._get_docs().project_status(project_id)

    def _action_risk_assess(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "risk_assess", "error": "project_id required"}
        return self._get_change_control().assess_risk(project_id, ctx)

    def _action_contamination_check(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {
                "success": False,
                "action": "contamination_check",
                "error": "project_id required",
            }
        return self._get_change_control().assess_contamination(project_id, ctx)

    def _action_version_backup(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "version_backup", "error": "project_id required"}
        return self._get_change_control().create_compressed_backup(project_id, ctx)

    def _action_version_record(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "version_record", "error": "project_id required"}
        return self._get_change_control().record_version(project_id, ctx)

    def _action_version_list(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "version_list", "error": "project_id required"}
        return self._get_change_control().list_versions(project_id)

    def _action_merge_update(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "merge_update", "error": "project_id required"}
        return self._get_change_control().merge_update(project_id, ctx)

    def _action_change_flow(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            return {"success": False, "action": "change_flow", "error": "project_id required"}
        return self._get_change_control().run_change_flow(project_id, ctx)

    def _action_health_check(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            current = self._get_registry().get_current_project()
            if not current:
                return {"success": False, "action": "health_check", "error": "project_id required (no current project)"}
            project_id = current
        health = self._get_registry().compute_health(project_id)
        return {
            "success": True,
            "action": "health_check",
            "project_id": project_id,
            "artifacts": health,
        }

    def _action_current_get(self, desc, ctx):
        current = self._get_registry().get_current_project()
        if not current:
            return {"success": True, "action": "current_get", "artifacts": {"project_id": None}}
        project = self._get_registry().get(current)
        return {
            "success": True,
            "action": "current_get",
            "artifacts": {
                "project_id": current,
                "project": project.get("artifacts") if project.get("success") else None,
            },
        }

    def _action_current_switch(self, desc, ctx):
        name_or_id = ctx.get("project_id", "") or ctx.get("name", "")
        if not name_or_id:
            return {"success": False, "action": "current_switch", "error": "project_id or name required"}
        registry = self._get_registry()
        project_id = name_or_id
        if name_or_id not in [p.project_id for p in registry._projects.values()]:
            found = registry.find_by_name(name_or_id)
            if found:
                project_id = found
            else:
                return {"success": False, "action": "current_switch", "error": f"Project '{name_or_id}' not found"}
        engine_running = ctx.get("engine_running", False)
        if engine_running:
            current = registry.get_current_project() or "unknown"
            return {
                "success": True,
                "action": "current_switch",
                "status": "engine_running",
                "artifacts": {
                    "current_project": current,
                    "target_project": project_id,
                    "options": [
                        "A: Pause current engine and switch",
                        "B: Wait for task completion",
                        "C: Keep engine running and switch (risk: context not isolated)",
                    ],
                },
            }
        return registry.set_current_project(project_id)

    def _action_overview(self, desc, ctx):
        return self._get_registry().overview()

    def _action_doc_log(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            current = self._get_registry().get_current_project()
            if not current:
                return {"success": False, "action": "doc_log", "error": "project_id required (no current project)"}
            project_id = current
        category = ctx.get("category")
        return self._get_docs().get_doc_log(project_id, category=category)

    def _action_doc_diff(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            current = self._get_registry().get_current_project()
            if not current:
                return {"success": False, "action": "doc_diff", "error": "project_id required (no current project)"}
            project_id = current
        category = ctx.get("category", "")
        v1 = int(ctx.get("version_from", 0))
        v2 = int(ctx.get("version_to", 0))
        if not category or not v1 or not v2:
            return {"success": False, "action": "doc_diff", "error": "category, version_from, version_to required"}
        return self._get_docs().get_doc_diff(project_id, category, v1, v2)

    def _action_doc_content(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            current = self._get_registry().get_current_project()
            if not current:
                return {"success": False, "action": "doc_content", "error": "project_id required (no current project)"}
            project_id = current
        category = ctx.get("category")
        return self._get_docs().get_doc_content(project_id, category=category)

    def _action_deliver(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            current = self._get_registry().get_current_project()
            if not current:
                return {"success": False, "action": "deliver", "error": "project_id required (no current project)"}
            project_id = current
        project = self._get_registry().get(project_id)
        if not project.get("success"):
            return {"success": False, "action": "deliver", "error": f"Project {project_id} not found"}
        init_mode = project["artifacts"].get("init_mode", "")
        ctx["project_id"] = project_id
        if init_mode == "clone":
            return self._get_github_delivery().deliver_github(ctx)
        return self._get_delivery().deliver_local(ctx)

    def _action_assess_all(self, desc, ctx):
        project_id = ctx.get("project_id", "")
        if not project_id:
            current = self._get_registry().get_current_project()
            if not current:
                return {"success": False, "action": "assess_all", "error": "project_id required (no current project)"}
            project_id = current
        ctx["project_id"] = project_id
        results = {}
        results["health"] = self._get_registry().compute_health(project_id)
        results["gates"] = self._get_gates().evaluate(project_id, ctx)
        results["drift"] = self._get_drift().check(project_id, event_id=ctx.get("event_id", ""))
        results["risk"] = self._get_change_control().assess_risk(project_id, ctx)
        results["contamination"] = self._get_change_control().assess_contamination(project_id, ctx)
        return {
            "success": True,
            "action": "assess_all",
            "project_id": project_id,
            "artifacts": results,
        }
