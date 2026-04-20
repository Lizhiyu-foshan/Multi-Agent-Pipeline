"""
Tests for ModelBridge infrastructure: base, registry, manager, strategies.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.model_bridge.base import ModelBridgeBase, ModelRequest, ModelResponse
from pipeline.model_bridge.registry import ModelRegistry
from pipeline.model_bridge.manager import ModelBridgeManager
from pipeline.model_bridge.synthetic_bridge import SyntheticBridge


class AlwaysAvailableBridge(ModelBridgeBase):
    @property
    def name(self):
        return "always"

    def is_available(self):
        return True

    def call(self, request):
        return ModelResponse(
            content=f"response for: {request.prompt[:50]}",
            model=request.model,
            request_id=request.model_id,
            success=True,
        )


class NeverAvailableBridge(ModelBridgeBase):
    @property
    def name(self):
        return "never"

    def is_available(self):
        return False

    def call(self, request):
        return ModelResponse(content="unreachable", success=False)


class FailingBridge(ModelBridgeBase):
    @property
    def name(self):
        return "failing"

    def is_available(self):
        return True

    def call(self, request):
        return ModelResponse(
            content="",
            success=False,
            error="deliberate failure",
            request_id=request.model_id,
        )


# ============================================================
# Base
# ============================================================


class TestModelRequest:
    def test_auto_generates_id(self):
        r = ModelRequest(prompt="test")
        assert r.model_id.startswith("req_")

    def test_preserves_custom_id(self):
        r = ModelRequest(prompt="test", model_id="custom_1")
        assert r.model_id == "custom_1"


class TestModelResponse:
    def test_defaults(self):
        r = ModelResponse()
        assert r.success is True
        assert r.content == ""


# ============================================================
# Registry
# ============================================================


class TestRegistry:
    def test_register_and_select(self):
        reg = ModelRegistry()
        reg.register("a", AlwaysAvailableBridge())
        bridge = reg.select(strategy="a")
        assert bridge is not None
        assert bridge.name == "always"

    def test_select_unavailable_falls_through(self):
        reg = ModelRegistry()
        reg.register("unavail", NeverAvailableBridge())
        reg.register("avail", AlwaysAvailableBridge())
        reg.set_default("avail")
        bridge = reg.select(strategy="unavail")
        assert bridge is not None
        assert bridge.name == "always"

    def test_model_routing(self):
        reg = ModelRegistry(config={"models": {"routing": {"glm-5.1": "http"}}})
        reg.register("http", AlwaysAvailableBridge())
        bridge = reg.select(model="glm-5.1")
        assert bridge is not None

    def test_task_type_routing(self):
        reg = ModelRegistry(config={"models": {"routing": {"task:analyze": "fast"}}})
        reg.register("fast", AlwaysAvailableBridge())
        bridge = reg.select(task_type="analyze")
        assert bridge is not None

    def test_fallback_chain(self):
        reg = ModelRegistry(config={"models": {"fallback_chain": ["b1", "b2"]}})
        reg.register("b2", AlwaysAvailableBridge())
        bridge = reg.select()
        assert bridge is not None
        assert bridge.name == "always"

    def test_no_available_returns_none(self):
        reg = ModelRegistry()
        reg.register("unavail", NeverAvailableBridge())
        assert reg.select() is None

    def test_call_delegates_to_bridge(self):
        reg = ModelRegistry()
        reg.register("a", AlwaysAvailableBridge())
        resp = reg.call(ModelRequest(prompt="hello"))
        assert resp.success is True
        assert "hello" in resp.content

    def test_call_no_bridge_returns_error(self):
        reg = ModelRegistry()
        resp = reg.call(ModelRequest(prompt="hello"))
        assert resp.success is False

    def test_set_default_raises_on_unknown(self):
        reg = ModelRegistry()
        with pytest.raises(ValueError):
            reg.set_default("nonexistent")


# ============================================================
# Manager
# ============================================================


class TestManager:
    def test_loads_synthetic_by_default(self):
        mgr = ModelBridgeManager()
        mgr.load_strategies()
        hc = mgr.health_check()
        assert "synthetic" in hc["registered"]

    def test_call_uses_synthetic(self):
        mgr = ModelBridgeManager()
        resp = mgr.call("analyze this task", model="test")
        assert resp.success is True

    def test_call_simple_raises_on_failure(self):
        mgr = ModelBridgeManager()
        mgr.load_strategies()
        mgr.registry._bridges.clear()
        with pytest.raises(RuntimeError):
            mgr.call_simple("test")

    def test_config_merges_with_defaults(self):
        cfg = {"models": {"default_model": "glm-4-plus", "temperature": 0.3}}
        mgr = ModelBridgeManager(config=cfg)
        assert mgr._models_cfg["default_model"] == "glm-4-plus"
        assert mgr._models_cfg["temperature"] == 0.3
        assert mgr._models_cfg["max_tokens"] == 4096

    def test_http_not_loaded_without_config(self):
        mgr = ModelBridgeManager()
        mgr.load_strategies()
        hc = mgr.health_check()
        assert "http" not in hc["registered"]

    def test_health_check_structure(self):
        mgr = ModelBridgeManager()
        mgr.load_strategies()
        hc = mgr.health_check()
        assert "default_strategy" in hc
        assert "fallback_chain" in hc
        assert "routing" in hc
        assert "registered" in hc


# ============================================================
# SyntheticBridge
# ============================================================


class TestSyntheticBridge:
    def test_always_available(self):
        b = SyntheticBridge()
        assert b.is_available() is True

    def test_analysis_response(self):
        b = SyntheticBridge(enable_test_runner=False)
        resp = b.call(ModelRequest(prompt="analyze the task breakdown"))
        assert resp.success is True
        data = json.loads(resp.content)
        assert "roles" in data
        assert "tasks" in data

    def test_plan_response(self):
        b = SyntheticBridge(enable_test_runner=False)
        resp = b.call(ModelRequest(prompt="create execution plan"))
        data = json.loads(resp.content)
        assert "task_graph" in data

    def test_implementation_response(self):
        b = SyntheticBridge(enable_test_runner=False)
        resp = b.call(ModelRequest(prompt="implement the code"))
        data = json.loads(resp.content)
        assert data["implementation_status"] == "completed"

    def test_debug_response(self):
        b = SyntheticBridge(enable_test_runner=False)
        resp = b.call(ModelRequest(prompt="debug the error"))
        data = json.loads(resp.content)
        assert data["fix_applied"] is True

    def test_generic_response(self):
        b = SyntheticBridge(enable_test_runner=False)
        resp = b.call(ModelRequest(prompt="some random text"))
        data = json.loads(resp.content)
        assert data["action_taken"] == "proceed"

    def test_call_log(self):
        b = SyntheticBridge(enable_test_runner=False)
        b.call(ModelRequest(prompt="test 1"))
        b.call(ModelRequest(prompt="test 2"))
        assert len(b.get_call_log()) == 2


# ============================================================
# Integration: Manager + Registry + Config
# ============================================================


class TestManagerIntegration:
    def test_full_flow_with_custom_strategy(self):
        mgr = ModelBridgeManager()
        mgr.load_strategies()
        mgr.registry.register("custom", AlwaysAvailableBridge())
        mgr.registry.set_default("custom")
        resp = mgr.call("hello world")
        assert resp.success is True
        assert "hello world" in resp.content

    def test_fallback_from_failing_to_synthetic(self):
        mgr = ModelBridgeManager()
        mgr.load_strategies()
        mgr.registry.register("failing", FailingBridge())
        mgr.registry.set_default("failing")
        mgr.registry._fallback_chain = ["synthetic"]

        reg = mgr.get_registry()
        bridge = reg.select()
        assert bridge.name == "failing"

        resp = reg.call(ModelRequest(prompt="test"))
        assert resp.success is False

        bridge2 = reg.select(strategy="synthetic")
        assert bridge2 is not None
        resp2 = bridge2.call(ModelRequest(prompt="test"))
        assert resp2.success is True

    def test_map_json_model_config_loadable(self):
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "map.json"
        if not cfg_path.exists():
            pytest.skip("config/map.json not found")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        models = cfg.get("models", {})
        assert "default_strategy" in models
        assert "strategies" in models
        assert "routing" in models
        assert "fallback_chain" in models
