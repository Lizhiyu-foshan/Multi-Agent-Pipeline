"""
Project-Manage Skill Adapter

Multi-project governance: registration, lifecycle, constraint packs,
change ingestion, drift detection, gate evaluation, delivery, dashboard.

Actions (16):
- project_init, project_get, project_list, project_update
- project_pause, project_resume, project_archive, project_delete
- pack_activate, ingest_external_changes, drift_check, evaluate_gates
- deliver_local, deliver_github, dashboard_summary
- (constraint pack Python rule engine integrated in pack_activate)
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
