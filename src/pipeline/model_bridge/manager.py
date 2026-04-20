"""
ModelBridge Manager - High-level interface for the pipeline.

Loads config from map.json, registers available strategies,
and provides a simple call() interface for the orchestrator.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .base import ModelBridgeBase, ModelRequest, ModelResponse
from .registry import ModelRegistry

logger = logging.getLogger(__name__)

DEFAULT_MODEL_CONFIG = {
    "default_strategy": "synthetic",
    "default_model": "glm-5.1",
    "max_tokens": 4096,
    "temperature": 0.7,
    "timeout": 120,
    "routing": {},
    "fallback_chain": ["synthetic"],
    "strategies": {},
}


class ModelBridgeManager:
    """
    Top-level model bridge manager used by PipelineOrchestrator.

    Usage:
        manager = ModelBridgeManager(config=map_config)
        response = manager.call("Analyze this task", model="glm-5.1")
        response = manager.call(prompt, model="glm-5.1", task_type="analyze")
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._models_cfg = self._merge_defaults()
        self.registry = ModelRegistry(config={"models": self._models_cfg})
        self._strategies_loaded = False

    def _merge_defaults(self) -> Dict[str, Any]:
        merged = dict(DEFAULT_MODEL_CONFIG)
        file_cfg = self._config.get("models", {})
        for k, v in file_cfg.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged

    def load_strategies(self, project_root: str = "") -> None:
        if self._strategies_loaded:
            return

        strategies_cfg = self._models_cfg.get("strategies", {})

        if strategies_cfg.get("http", {}).get("enabled"):
            self._try_load_http(strategies_cfg["http"])

        if strategies_cfg.get("opencode", {}).get("enabled"):
            self._try_load_opencode()

        if strategies_cfg.get("synthetic", {}).get("enabled", True):
            self._try_load_synthetic(project_root)

        self._strategies_loaded = True
        registered = self.registry.get_registered()
        logger.info(
            f"ModelBridgeManager loaded {len(registered)} strategies: "
            f"{list(registered.keys())}"
        )

    def _try_load_http(self, cfg: Dict[str, Any]) -> None:
        try:
            from .http_bridge import HTTPBridge

            bridge = HTTPBridge(
                api_key=cfg.get("api_key", ""),
                api_base=cfg.get("api_base", ""),
                default_model=cfg.get("default_model", self._models_cfg.get("default_model", "")),
                timeout=cfg.get("timeout", self._models_cfg.get("timeout", 120)),
            )
            if bridge.is_available():
                self.registry.register("http", bridge)
        except Exception as e:
            logger.debug(f"HTTP bridge not loaded: {e}")

    def _try_load_opencode(self) -> None:
        try:
            from .opencode_bridge import OpencodeBridge

            bridge = OpencodeBridge()
            if bridge.is_available():
                self.registry.register("opencode", bridge)
        except Exception as e:
            logger.debug(f"Opencode bridge not loaded: {e}")

    def _try_load_synthetic(self, project_root: str = "") -> None:
        try:
            from .synthetic_bridge import SyntheticBridge

            bridge = SyntheticBridge(project_root=project_root)
            self.registry.register("synthetic", bridge)
        except Exception as e:
            logger.debug(f"Synthetic bridge not loaded: {e}")

    def call(
        self,
        prompt: str,
        model: str = "",
        task_type: str = "",
        strategy: str = "",
        max_tokens: int = 0,
        temperature: float = -1.0,
        timeout: int = 0,
        context: Optional[Dict[str, Any]] = None,
    ) -> ModelResponse:
        if not self._strategies_loaded:
            self.load_strategies()

        request = ModelRequest(
            prompt=prompt,
            model=model or self._models_cfg.get("default_model", ""),
            task_type=task_type,
            context=context or {},
            max_tokens=max_tokens or self._models_cfg.get("max_tokens", 4096),
            temperature=temperature if temperature >= 0 else self._models_cfg.get("temperature", 0.7),
            timeout=timeout or self._models_cfg.get("timeout", 120),
        )
        return self.registry.call(request, strategy=strategy)

    def call_simple(self, prompt: str, model: str = "", **kwargs) -> str:
        resp = self.call(prompt, model=model, **kwargs)
        if not resp.success:
            raise RuntimeError(f"Model call failed: {resp.error}")
        return resp.content

    def health_check(self) -> Dict[str, Any]:
        if not self._strategies_loaded:
            self.load_strategies()
        return {
            "default_strategy": self.registry.get_default_strategy(),
            "fallback_chain": self.registry.get_fallback_chain(),
            "routing": self.registry.get_routing(),
            "registered": self.registry.get_registered(),
        }

    def get_registry(self) -> ModelRegistry:
        return self.registry
