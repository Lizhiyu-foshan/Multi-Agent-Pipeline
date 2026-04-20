"""
Opencode Bridge - Delegates to the opencode host agent.

When running inside opencode, the host agent IS the model.
This bridge returns a pending request for the host to execute.
"""

import logging
from typing import Any, Dict, List

from .base import ModelBridgeBase, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


class OpencodeBridge(ModelBridgeBase):
    """
    Opencode environment bridge.

    Returns a pending response that signals the caller to delegate
    model execution to the opencode host agent.
    """

    _pending: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "opencode"

    def is_available(self) -> bool:
        import os
        return bool(os.environ.get("OPENCODE") or os.environ.get("OPENCODE_VERSION"))

    def call(self, request: ModelRequest) -> ModelResponse:
        pending_entry = {
            "id": request.model_id,
            "prompt": request.prompt,
            "model": request.model,
            "type": request.task_type or "model_inference",
        }
        OpencodeBridge._pending.append(pending_entry)

        return ModelResponse(
            content="__OPENCODE_MODEL_REQUEST_PENDING__",
            model=request.model,
            request_id=request.model_id,
            success=True,
            finish_reason="pending",
            raw=pending_entry,
        )

    @classmethod
    def get_pending(cls) -> List[Dict[str, Any]]:
        return list(cls._pending)

    @classmethod
    def clear_pending(cls):
        cls._pending.clear()
