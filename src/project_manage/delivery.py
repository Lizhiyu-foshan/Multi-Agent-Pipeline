"""
Delivery Manager - Local delivery with stage/gate/approve/promote/rollback.

Full four-stage delivery:
  stage -> gate_passed -> approved -> promoted -> verified
With rollback support and audit integration.
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

from .models import DeliveryRecord, DeliveryStatus, RollbackPoint, DriftSeverity

logger = logging.getLogger(__name__)


class DeliveryManager:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = state_dir
        self._deliveries_file = Path(state_dir) / "global" / "deliveries.json"
        self._rollbacks_dir = Path(state_dir) / "global" / "rollbacks"
        self._deliveries: Dict[str, DeliveryRecord] = {}
        self._rollback_points: Dict[str, RollbackPoint] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        if not self._deliveries_file.exists():
            return
        try:
            with open(self._deliveries_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for did, ddata in data.items():
                self._deliveries[did] = DeliveryRecord.from_dict(ddata)
        except Exception as e:
            logger.error(f"Failed to load deliveries: {e}")

    def _save(self):
        with self._lock:
            try:
                os.makedirs(str(self._deliveries_file.parent), exist_ok=True)
                data = {did: d.to_dict() for did, d in self._deliveries.items()}
                dir_name = str(self._deliveries_file.parent)
                fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, str(self._deliveries_file))
                except Exception:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
            except Exception as e:
                logger.error(f"Failed to save deliveries: {e}")

    def deliver_local(self, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("delivery_action", "stage")
        dispatch = {
            "stage": self._stage,
            "evaluate_gates": self._evaluate_gates,
            "request_approval": self._request_approval,
            "approve": self._approve,
            "promote": self._promote,
            "verify": self._verify,
            "rollback": self._rollback,
        }
        handler = dispatch.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown delivery action: {action}"}
        return handler(context)

    def get_delivery(self, delivery_id: str) -> Optional[DeliveryRecord]:
        return self._deliveries.get(delivery_id)

    def _stage(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        project_id = ctx.get("project_id", "")
        files = ctx.get("files", [])
        target_path = ctx.get("target_path", "")
        source_dir = ctx.get("source_dir", "")

        if source_dir:
            staging_dir = os.path.join(source_dir, ".staging")
            os.makedirs(staging_dir, exist_ok=True)
            for f in files:
                src = os.path.join(source_dir, f)
                dst = os.path.join(staging_dir, f)
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

        delivery = DeliveryRecord(
            project_id=project_id,
            pipeline_id=ctx.get("pipeline_id", ""),
            target="local",
            status=DeliveryStatus.STAGED.value,
            files=files,
            metadata={
                "target_path": target_path,
                "source_dir": source_dir,
            },
        )
        delivery.staged_at = datetime.now()
        with self._lock:
            self._deliveries[delivery.delivery_id] = delivery
            self._save()

        return {
            "success": True,
            "action": "deliver_stage",
            "artifacts": delivery.to_dict(),
        }

    def _evaluate_gates(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}
        if delivery.status != DeliveryStatus.STAGED.value:
            return {
                "success": False,
                "error": f"Delivery must be staged, got {delivery.status}",
            }

        from .gates import GateEvaluator

        gate_ctx = dict(ctx)

        should_run_change_control = not bool(ctx.get("skip_change_control", False))
        if should_run_change_control:
            from .change_control import ChangeControlManager

            cc = ChangeControlManager(state_dir=self._state_dir)

            if "risk_result" not in gate_ctx:
                drift_severity = str(ctx.get("drift_severity", "")).strip().lower()
                drift_result = ctx.get("drift_result")
                if drift_result and not drift_severity:
                    drift_severity = str(
                        drift_result.get("artifacts", {}).get("severity", "")
                    ).strip().lower()

                risk_result = cc.assess_risk(
                    delivery.project_id,
                    {
                        "files": list(delivery.files),
                        "drift_severity": drift_severity,
                    },
                )
                gate_ctx["risk_result"] = risk_result

            if "contamination_result" not in gate_ctx:
                source_path = str(
                    ctx.get("source_path")
                    or delivery.metadata.get("source_dir", "")
                ).strip()
                contamination_result = cc.assess_contamination(
                    delivery.project_id,
                    {
                        "files": list(delivery.files),
                        "source_path": source_path,
                        "file_contents": dict(ctx.get("file_contents", {})),
                    },
                )
                gate_ctx["contamination_result"] = contamination_result

        evaluator = GateEvaluator(state_dir=self._state_dir)
        gate_result = evaluator.evaluate(delivery.project_id, gate_ctx)
        gate_artifacts = gate_result.get("artifacts", {})

        if gate_artifacts.get("decision") == "pass":
            delivery.status = DeliveryStatus.GATE_PASSED.value
            with self._lock:
                self._save()
            return {
                "success": True,
                "action": "deliver_gate_passed",
                "artifacts": {
                    **delivery.to_dict(),
                    "gate": gate_artifacts,
                    "change_control": {
                        "risk": gate_ctx.get("risk_result", {}),
                        "contamination": gate_ctx.get("contamination_result", {}),
                    },
                },
            }
        else:
            return {
                "success": False,
                "action": "deliver_gate_blocked",
                "artifacts": {
                    "gate": gate_artifacts,
                    "change_control": {
                        "risk": gate_ctx.get("risk_result", {}),
                        "contamination": gate_ctx.get("contamination_result", {}),
                    },
                },
            }

    def _request_approval(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}
        if delivery.status not in (
            DeliveryStatus.STAGED.value,
            DeliveryStatus.GATE_PASSED.value,
        ):
            return {
                "success": False,
                "error": f"Delivery must be staged or gate_passed, got {delivery.status}",
            }

        required = ctx.get("required_approvals", 1)
        return {
            "success": True,
            "action": "request_approval",
            "delivery_id": delivery_id,
            "required_approvals": required,
        }

    def _approve(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        approver = ctx.get("approver", "")
        decision = ctx.get("approval_decision", "approved")
        comment = ctx.get("comment", "")

        if not approver:
            return {"success": False, "error": "approver required"}

        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}

        if decision == "approved":
            required = ctx.get("required_approvals", 1)
            current = delivery.metadata.get("approvals", [])
            current.append(
                {"approver": approver, "decision": "approved", "comment": comment}
            )
            delivery.metadata["approvals"] = current
            if len([a for a in current if a["decision"] == "approved"]) >= required:
                delivery.status = DeliveryStatus.APPROVED.value
            with self._lock:
                self._save()
            self._audit_log("delivery_approved", delivery)
            return {
                "success": True,
                "action": "deliver_approved"
                if delivery.status == DeliveryStatus.APPROVED.value
                else "deliver_approval_recorded",
                "artifacts": delivery.to_dict(),
            }
        else:
            delivery.status = DeliveryStatus.FAILED.value
            delivery.metadata["rejection"] = {
                "approver": approver,
                "decision": decision,
                "comment": comment,
            }
            with self._lock:
                self._save()
            self._audit_log("delivery_rejected", delivery)
            return {
                "success": False,
                "action": "deliver_rejected",
                "error": f"Rejected by {approver}: {comment}",
                "artifacts": {
                    "delivery_id": delivery_id,
                    "approver": approver,
                    "decision": decision,
                },
            }

    def _promote(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}
        if delivery.status not in (
            DeliveryStatus.GATE_PASSED.value,
            DeliveryStatus.APPROVED.value,
        ):
            return {
                "success": False,
                "error": f"Delivery status is {delivery.status}, must be gate_passed or approved",
            }

        target_path = delivery.metadata.get("target_path", "")
        source_dir = delivery.metadata.get("source_dir", "")

        rollback = None
        if target_path and os.path.exists(target_path) and delivery.files:
            os.makedirs(str(self._rollbacks_dir), exist_ok=True)
            backup_dir = os.path.join(str(self._rollbacks_dir), delivery_id)
            os.makedirs(backup_dir, exist_ok=True)
            try:
                for f in delivery.files:
                    src = os.path.join(target_path, f)
                    dst = os.path.join(backup_dir, f)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
                rollback = RollbackPoint(
                    delivery_id=delivery_id,
                    project_id=delivery.project_id,
                    snapshot_path=backup_dir,
                )
                delivery.rollback_point_id = rollback.rollback_id
                self._rollback_points[rollback.rollback_id] = rollback
            except Exception as e:
                logger.error(f"Backup failed: {e}")
                return {"success": False, "error": f"Backup failed: {e}"}

        if source_dir and target_path and delivery.files:
            staging_dir = os.path.join(source_dir, ".staging")
            try:
                for f in delivery.files:
                    src = (
                        os.path.join(staging_dir, f)
                        if os.path.exists(os.path.join(staging_dir, f))
                        else os.path.join(source_dir, f)
                    )
                    dst = os.path.join(target_path, f)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
            except Exception as e:
                if rollback:
                    self._do_rollback(delivery, rollback)
                return {"success": False, "error": f"Promote failed, rolled back: {e}"}

        delivery.status = DeliveryStatus.PROMOTED.value
        delivery.promoted_at = datetime.now()
        with self._lock:
            self._save()

        self._audit_log("delivery_promoted", delivery)

        return {
            "success": True,
            "action": "deliver_promote",
            "artifacts": {
                "delivery": delivery.to_dict(),
                "rollback_id": rollback.rollback_id if rollback else "",
            },
        }

    def _verify(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}
        smoke_pass = ctx.get("smoke_pass", True)
        if smoke_pass:
            delivery.status = DeliveryStatus.VERIFIED.value
            delivery.verified_at = datetime.now()
        else:
            delivery.status = DeliveryStatus.FAILED.value
        with self._lock:
            self._save()
        self._audit_log(
            "delivery_verified" if smoke_pass else "delivery_failed", delivery
        )
        return {
            "success": True,
            "action": "deliver_verify",
            "artifacts": delivery.to_dict(),
        }

    def _rollback(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}
        rbk = self._rollback_points.get(delivery.rollback_point_id)
        if not rbk:
            return {"success": False, "error": "No rollback point available"}

        self._do_rollback(delivery, rbk)
        rbk.restored = True
        delivery.status = DeliveryStatus.ROLLED_BACK.value
        with self._lock:
            self._save()
        self._audit_log("delivery_rolled_back", delivery)
        return {
            "success": True,
            "action": "deliver_rollback",
            "artifacts": delivery.to_dict(),
        }

    def _do_rollback(self, delivery: DeliveryRecord, rollback: RollbackPoint):
        target_path = delivery.metadata.get("target_path", "")
        if not target_path or not os.path.exists(rollback.snapshot_path):
            return
        for f in delivery.files:
            src = os.path.join(rollback.snapshot_path, f)
            dst = os.path.join(target_path, f)
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

    def _audit_log(self, event_type: str, delivery: DeliveryRecord):
        try:
            from .audit import AuditLogger

            audit = AuditLogger(state_dir=self._state_dir)
            audit.log(
                event_type,
                {
                    "delivery_id": delivery.delivery_id,
                    "project_id": delivery.project_id,
                    "status": delivery.status,
                    "files_count": len(delivery.files),
                },
            )
        except Exception:
            pass

    def update_status(self, delivery_id: str, status: str):
        delivery = self._deliveries.get(delivery_id)
        if delivery:
            delivery.status = status
