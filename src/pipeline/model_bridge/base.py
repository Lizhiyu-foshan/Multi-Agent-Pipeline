"""
ModelBridge Base - Abstract interface for model calling strategies.

Every model backend (HTTP API, opencode, synthetic, etc.) implements
this interface. The registry selects which strategy to use based on
config from map.json.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ModelRequest:
    prompt: str
    model: str = ""
    model_id: str = ""
    task_type: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 120
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.model_id:
            self.model_id = f"req_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now()


@dataclass
class ModelResponse:
    content: str = ""
    model: str = ""
    request_id: str = ""
    success: bool = True
    error: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    finish_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class ModelBridgeBase(ABC):
    """
    Abstract base for all model bridge strategies.

    Subclasses must implement:
    - call(request) -> ModelResponse
    - name (property)
    - is_available() -> bool
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def call(self, request: ModelRequest) -> ModelResponse:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    def health_check(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.is_available(),
            "type": self.__class__.__name__,
        }

    def call_simple(self, prompt: str, model: str = "", **kwargs) -> str:
        req = ModelRequest(prompt=prompt, model=model, **kwargs)
        resp = self.call(req)
        if not resp.success:
            raise RuntimeError(f"Model call failed: {resp.error}")
        return resp.content
