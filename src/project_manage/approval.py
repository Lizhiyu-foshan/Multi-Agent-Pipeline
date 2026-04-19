"""
Approval Manager - Simple string-identified approver workflow.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .models import ApprovalRecord

logger = logging.getLogger(__name__)


class ApprovalManager:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = state_dir
        self._approvals: Dict[str, List[ApprovalRecord]] = {}

    def request_approval(
        self, delivery_id: str, required_count: int = 1
    ) -> Dict[str, Any]:
        if delivery_id not in self._approvals:
            self._approvals[delivery_id] = []
        return {
            "success": True,
            "action": "request_approval",
            "delivery_id": delivery_id,
            "required_approvals": required_count,
            "current_approvals": len(self._approvals.get(delivery_id, [])),
        }

    def submit_approval(
        self,
        delivery_id: str,
        approver: str,
        decision: str = "approved",
        comment: str = "",
    ) -> Dict[str, Any]:
        if delivery_id not in self._approvals:
            self._approvals[delivery_id] = []
        record = ApprovalRecord(
            delivery_id=delivery_id,
            approver=approver,
            decision=decision,
            comment=comment,
        )
        self._approvals[delivery_id].append(record)
        logger.info(f"Approval for {delivery_id}: {approver} -> {decision}")
        return {
            "success": True,
            "action": "submit_approval",
            "artifacts": record.to_dict(),
        }

    def is_approved(self, delivery_id: str, required_count: int = 1) -> bool:
        records = self._approvals.get(delivery_id, [])
        approved = [r for r in records if r.decision == "approved"]
        return len(approved) >= required_count

    def get_approvals(self, delivery_id: str) -> Dict[str, Any]:
        records = self._approvals.get(delivery_id, [])
        return {
            "success": True,
            "artifacts": {
                "approvals": [r.to_dict() for r in records],
                "total": len(records),
                "approved_count": len([r for r in records if r.decision == "approved"]),
            },
        }
