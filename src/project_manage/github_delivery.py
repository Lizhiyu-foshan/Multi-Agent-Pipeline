"""
GitHub Delivery Manager - PR-based delivery via git + gh CLI.

Flow:
  stage_github -> create feature branch, copy files
  evaluate_gates -> run gate evaluation (reuses gates.py)
  request_approval / approve -> approval workflow
  promote_github -> git add + commit + push + gh pr create
  verify -> check PR status
  rollback -> revert commit or close PR
"""

import json
import logging
import os
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .models import DeliveryRecord, DeliveryStatus

logger = logging.getLogger(__name__)


class GitHubDeliveryManager:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = state_dir
        self._deliveries_file = Path(state_dir) / "global" / "deliveries.json"
        self._deliveries: Dict[str, DeliveryRecord] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        if not self._deliveries_file.exists():
            return
        try:
            with open(self._deliveries_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for did, ddata in data.items():
                rec = DeliveryRecord.from_dict(ddata)
                if rec.target == "github":
                    self._deliveries[did] = rec
        except Exception as e:
            logger.error(f"Failed to load github deliveries: {e}")

    def _save(self):
        with self._lock:
            try:
                os.makedirs(str(self._deliveries_file.parent), exist_ok=True)
                existing = {}
                if self._deliveries_file.exists():
                    with open(str(self._deliveries_file), "r", encoding="utf-8") as f:
                        existing = json.load(f)
                for did, d in self._deliveries.items():
                    existing[did] = d.to_dict()
                dir_name = str(self._deliveries_file.parent)
                fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(existing, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, str(self._deliveries_file))
                except Exception:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
            except Exception as e:
                logger.error(f"Failed to save github deliveries: {e}")

    def deliver_github(self, context: Dict[str, Any]) -> Dict[str, Any]:
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
            return {
                "success": False,
                "error": f"Unknown github delivery action: {action}",
            }
        return handler(context)

    def get_delivery(self, delivery_id: str) -> Optional[DeliveryRecord]:
        return self._deliveries.get(delivery_id)

    def _run_git(self, args: list, cwd: str, timeout: int = 30) -> tuple:
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except FileNotFoundError:
            return -1, "", "git not found on PATH"
        except subprocess.TimeoutExpired:
            return -1, "", "git command timed out"
        except Exception as e:
            return -1, "", str(e)

    def _run_gh(self, args: list, cwd: str, timeout: int = 60) -> tuple:
        try:
            result = subprocess.run(
                ["gh"] + args,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except FileNotFoundError:
            return -1, "", "gh CLI not found on PATH"
        except subprocess.TimeoutExpired:
            return -1, "", "gh command timed out"
        except Exception as e:
            return -1, "", str(e)

    def _stage(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        project_id = ctx.get("project_id", "")
        repo_path = ctx.get("repo_path", "")
        files = ctx.get("files", [])
        branch_name = ctx.get(
            "branch_name", f"feature/dlv-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        base_branch = ctx.get("base_branch", "main")

        if not repo_path or not os.path.exists(repo_path):
            return {"success": False, "error": f"repo_path does not exist: {repo_path}"}

        rc, out, err = self._run_git(["checkout", base_branch], cwd=repo_path)
        if rc != 0:
            return {
                "success": False,
                "error": f"git checkout {base_branch} failed: {err}",
            }

        rc, out, err = self._run_git(["checkout", "-b", branch_name], cwd=repo_path)
        if rc != 0:
            rc2, _, err2 = self._run_git(["checkout", branch_name], cwd=repo_path)
            if rc2 != 0:
                return {
                    "success": False,
                    "error": f"git checkout -b {branch_name} failed: {err}",
                }

        delivery = DeliveryRecord(
            project_id=project_id,
            pipeline_id=ctx.get("pipeline_id", ""),
            target="github",
            status=DeliveryStatus.STAGED.value,
            files=files,
            metadata={
                "repo_path": repo_path,
                "branch_name": branch_name,
                "base_branch": base_branch,
            },
        )
        delivery.staged_at = datetime.now()

        with self._lock:
            self._deliveries[delivery.delivery_id] = delivery
            self._save()

        return {
            "success": True,
            "action": "deliver_github_stage",
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

        evaluator = GateEvaluator(state_dir=self._state_dir)
        gate_result = evaluator.evaluate(delivery.project_id, ctx)
        gate_artifacts = gate_result.get("artifacts", {})

        if gate_artifacts.get("decision") == "pass":
            delivery.status = DeliveryStatus.GATE_PASSED.value
            with self._lock:
                self._save()
            return {
                "success": True,
                "action": "deliver_github_gate_passed",
                "artifacts": {**delivery.to_dict(), "gate": gate_artifacts},
            }
        else:
            return {
                "success": False,
                "action": "deliver_github_gate_blocked",
                "artifacts": {"gate": gate_artifacts},
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
            return {"success": False, "error": f"Delivery status is {delivery.status}"}
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
            self._audit_log("delivery_github_approved", delivery)
            return {
                "success": True,
                "action": "deliver_github_approved"
                if delivery.status == DeliveryStatus.APPROVED.value
                else "deliver_github_approval_recorded",
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
            self._audit_log("delivery_github_rejected", delivery)
            return {
                "success": False,
                "action": "deliver_github_rejected",
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

        repo_path = delivery.metadata.get("repo_path", "")
        branch_name = delivery.metadata.get("branch_name", "")
        base_branch = delivery.metadata.get("base_branch", "main")
        pr_title = ctx.get("pr_title", f"[Auto] Delivery {delivery_id[:12]}")
        pr_body = ctx.get(
            "pr_body",
            f"Automated delivery for project {delivery.project_id}\n\nFiles: {', '.join(delivery.files)}",
        )

        if not repo_path or not os.path.exists(repo_path):
            return {"success": False, "error": f"repo_path not found: {repo_path}"}

        rc, _, err = self._run_git(["add"] + delivery.files, cwd=repo_path)
        if rc != 0:
            return {"success": False, "error": f"git add failed: {err}"}

        commit_msg = ctx.get("commit_message", f"delivery: {delivery_id[:12]}")
        rc, _, err = self._run_git(
            ["commit", "-m", commit_msg, "--allow-empty"],
            cwd=repo_path,
        )
        if rc != 0:
            return {"success": False, "error": f"git commit failed: {err}"}

        rc, out, err = self._run_git(
            ["push", "-u", "origin", branch_name],
            cwd=repo_path,
            timeout=60,
        )
        if rc != 0:
            logger.warning(f"git push failed (may need force or remote missing): {err}")

        rc, out, err = self._run_gh(
            [
                "pr",
                "create",
                "--title",
                pr_title,
                "--body",
                pr_body,
                "--base",
                base_branch,
                "--head",
                branch_name,
            ],
            cwd=repo_path,
            timeout=60,
        )
        pr_url = ""
        if rc == 0 and out:
            for line in out.splitlines():
                if line.startswith("http"):
                    pr_url = line.strip()
                    break
            if not pr_url:
                pr_url = out.splitlines()[0] if out else ""
        else:
            delivery.metadata["gh_error"] = err
            logger.warning(f"gh pr create failed: {err}")

        delivery.metadata["pr_url"] = pr_url
        delivery.metadata["commit_message"] = commit_msg
        delivery.status = DeliveryStatus.PROMOTED.value
        delivery.promoted_at = datetime.now()

        with self._lock:
            self._save()

        self._audit_log("delivery_github_promoted", delivery)

        return {
            "success": True,
            "action": "deliver_github_promote",
            "artifacts": {
                "delivery": delivery.to_dict(),
                "pr_url": pr_url,
            },
        }

    def _verify(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}

        pr_url = delivery.metadata.get("pr_url", "")
        merged = ctx.get("pr_merged", False)

        if merged:
            delivery.status = DeliveryStatus.VERIFIED.value
            delivery.verified_at = datetime.now()
        else:
            repo_path = delivery.metadata.get("repo_path", "")
            if repo_path and pr_url:
                rc, out, err = self._run_gh(
                    ["pr", "view", "--json", "state", "--jq", ".state"],
                    cwd=repo_path,
                    timeout=30,
                )
                if rc == 0 and "MERGED" in out.upper():
                    delivery.status = DeliveryStatus.VERIFIED.value
                    delivery.verified_at = datetime.now()
                else:
                    smoke_pass = ctx.get("smoke_pass", True)
                    if smoke_pass:
                        delivery.status = DeliveryStatus.VERIFIED.value
                        delivery.verified_at = datetime.now()
                    else:
                        delivery.status = DeliveryStatus.FAILED.value
            else:
                smoke_pass = ctx.get("smoke_pass", True)
                if smoke_pass:
                    delivery.status = DeliveryStatus.VERIFIED.value
                    delivery.verified_at = datetime.now()
                else:
                    delivery.status = DeliveryStatus.FAILED.value

        with self._lock:
            self._save()

        self._audit_log(
            "delivery_github_verified"
            if delivery.status == DeliveryStatus.VERIFIED.value
            else "delivery_github_failed",
            delivery,
        )

        return {
            "success": True,
            "action": "deliver_github_verify",
            "artifacts": delivery.to_dict(),
        }

    def _rollback(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        delivery_id = ctx.get("delivery_id", "")
        delivery = self._deliveries.get(delivery_id)
        if not delivery:
            return {"success": False, "error": f"Delivery {delivery_id} not found"}

        repo_path = delivery.metadata.get("repo_path", "")
        branch_name = delivery.metadata.get("branch_name", "")
        base_branch = delivery.metadata.get("base_branch", "main")

        if repo_path and os.path.exists(repo_path):
            rc, _, err = self._run_git(["checkout", base_branch], cwd=repo_path)
            if rc == 0 and branch_name:
                self._run_git(
                    ["branch", "-D", branch_name],
                    cwd=repo_path,
                )
                self._run_git(
                    ["push", "origin", "--delete", branch_name],
                    cwd=repo_path,
                    timeout=30,
                )

            pr_url = delivery.metadata.get("pr_url", "")
            if pr_url:
                self._run_gh(
                    ["pr", "close", pr_url, "--comment", "Rolled back by MAP"],
                    cwd=repo_path,
                    timeout=30,
                )

        delivery.status = DeliveryStatus.ROLLED_BACK.value
        with self._lock:
            self._save()

        self._audit_log("delivery_github_rolled_back", delivery)

        return {
            "success": True,
            "action": "deliver_github_rollback",
            "artifacts": delivery.to_dict(),
        }

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
                    "target": "github",
                    "files_count": len(delivery.files),
                },
            )
        except Exception:
            pass
