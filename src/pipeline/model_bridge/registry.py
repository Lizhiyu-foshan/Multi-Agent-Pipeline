"""
Model Registry - Strategy registration and selection.

Reads model configuration from map.json and selects the appropriate
bridge strategy based on:
1. Task type -> model routing
2. Model name -> backend selection
3. Fallback chain
"""

import logging
from typing import Any, Dict, List, Optional, Type

from .base import ModelBridgeBase, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


class ModelRegistry:
    """
    Registry of model bridge strategies with config-driven selection.

    Usage:
        registry = ModelRegistry()
        registry.register("http", HTTPBridge(config))
        registry.register("synthetic", SyntheticBridge())
        registry.set_default("http")
        bridge = registry.select(model="glm-5.1")
        response = bridge.call(request)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._bridges: Dict[str, ModelBridgeBase] = {}
        self._default_strategy: str = ""
        self._model_routing: Dict[str, str] = {}
        self._fallback_chain: List[str] = []
        self._config = config or {}
        self._load_config_routing()

    def _load_config_routing(self):
        models_cfg = self._config.get("models", {})
        if not models_cfg:
            return

        self._default_strategy = models_cfg.get("default_strategy", "")
        self._model_routing = dict(models_cfg.get("routing", {}))
        self._fallback_chain = list(models_cfg.get("fallback_chain", []))

    def register(self, name: str, bridge: ModelBridgeBase) -> None:
        self._bridges[name] = bridge
        logger.info(f"Registered model bridge strategy: {name} ({bridge.name})")

    def unregister(self, name: str) -> None:
        self._bridges.pop(name, None)

    def set_default(self, name: str) -> None:
        if name not in self._bridges:
            raise ValueError(f"Bridge strategy '{name}' not registered")
        self._default_strategy = name

    def select(
        self,
        model: str = "",
        task_type: str = "",
        strategy: str = "",
    ) -> Optional[ModelBridgeBase]:
        if strategy and strategy in self._bridges:
            bridge = self._bridges[strategy]
            if bridge.is_available():
                return bridge

        if model:
            routed_strategy = self._model_routing.get(model)
            if routed_strategy and routed_strategy in self._bridges:
                bridge = self._bridges[routed_strategy]
                if bridge.is_available():
                    return bridge

        if task_type:
            routed_strategy = self._model_routing.get(f"task:{task_type}")
            if routed_strategy and routed_strategy in self._bridges:
                bridge = self._bridges[routed_strategy]
                if bridge.is_available():
                    return bridge

        if self._default_strategy and self._default_strategy in self._bridges:
            bridge = self._bridges[self._default_strategy]
            if bridge.is_available():
                return bridge

        for fallback_name in self._fallback_chain:
            if fallback_name in self._bridges:
                bridge = self._bridges[fallback_name]
                if bridge.is_available():
                    return bridge

        available = {n for n, b in self._bridges.items() if b.is_available()}
        if available:
            name = next(iter(available))
            return self._bridges[name]

        return None

    def call(self, request: ModelRequest, strategy: str = "") -> ModelResponse:
        bridge = self.select(
            model=request.model,
            task_type=request.task_type,
            strategy=strategy,
        )
        if not bridge:
            return ModelResponse(
                success=False,
                error="No available model bridge strategy",
                request_id=request.model_id,
            )
        return bridge.call(request)

    def get_registered(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: bridge.health_check()
            for name, bridge in self._bridges.items()
        }

    def get_default_strategy(self) -> str:
        return self._default_strategy

    def get_fallback_chain(self) -> List[str]:
        return list(self._fallback_chain)

    def get_routing(self) -> Dict[str, str]:
        return dict(self._model_routing)
