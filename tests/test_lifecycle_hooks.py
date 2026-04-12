"""
Unit Tests for LifecycleHookRegistry named recovery functionality.
Tests register_named, unregister_by_name, export_state, import_state.
"""

from pathlib import Path
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from specs.spec_gate import (
    LifecycleHookRegistry,
    register_named_handler,
    get_named_handler,
    LIFECYCLE_POINTS,
)


class TestNamedHandlerRegistration:
    """Tests for named handler registration."""

    def test_register_named_adds_handler(self):
        """register_named should add handler to registry."""
        registry = LifecycleHookRegistry()

        def test_handler(ctx):
            return ctx

        registry.register_named(
            "on_task_start", "test_handler", test_handler, priority=10
        )

        listed = registry.list_handlers("on_task_start")
        assert len(listed["on_task_start"]) == 1
        assert "test_handler" in listed["on_task_start"]

    def test_register_named_sets_priority(self):
        """register_named should respect priority ordering."""
        registry = LifecycleHookRegistry()

        call_order = []

        def handler_a(ctx):
            call_order.append("a")
            return ctx

        def handler_b(ctx):
            call_order.append("b")
            return ctx

        registry.register_named("on_task_start", "handler_a", handler_a, priority=20)
        registry.register_named("on_task_start", "handler_b", handler_b, priority=10)

        registry.emit("on_task_start", {})

        # Lower priority runs first (10 before 20)
        assert call_order == ["b", "a"]

    def test_register_named_rejects_invalid_point(self):
        """Should raise ValueError for unknown lifecycle point."""
        registry = LifecycleHookRegistry()

        with pytest.raises(ValueError, match="Unknown lifecycle point"):
            registry.register_named("invalid_point", "h", lambda x: x)

    def test_register_named_stores_globally(self):
        """register_named should store handler globally for recovery."""

        def test_handler(ctx):
            return ctx

        registry = LifecycleHookRegistry()
        registry.register_named("on_task_start", "test_handler", test_handler)

        # Should be retrievable via get_named_handler
        retrieved = get_named_handler("on_task_start", "test_handler")
        assert retrieved is test_handler


class TestUnregisterByName:
    """Tests for unregister_by_name method."""

    def test_unregister_by_name_removes_handler(self):
        """Should remove handler by name."""
        registry = LifecycleHookRegistry()

        def handler_a(ctx):
            return ctx

        def handler_b(ctx):
            return ctx

        registry.register_named("on_task_start", "handler_a", handler_a)
        registry.register_named("on_task_start", "handler_b", handler_b)

        assert len(registry.list_handlers()["on_task_start"]) == 2

        registry.unregister_by_name("on_task_start", "handler_a")

        listed = registry.list_handlers("on_task_start")
        assert len(listed["on_task_start"]) == 1
        assert "handler_a" not in listed["on_task_start"]
        assert "handler_b" in listed["on_task_start"]

    def test_unregister_by_name_noop_for_nonexistent(self):
        """Should not raise for nonexistent handler name."""
        registry = LifecycleHookRegistry()

        # Should not raise
        registry.unregister_by_name("on_task_start", "nonexistent")

        listed = registry.list_handlers("on_task_start")
        assert len(listed.get("on_task_start", [])) == 0


class TestExportState:
    """Tests for export_state method."""

    def test_export_state_serializes_handlers(self):
        """Should serialize handler metadata to dict."""
        registry = LifecycleHookRegistry()

        def handler_a(ctx):
            return ctx

        registry.register_named("on_task_start", "handler_a", handler_a, priority=30)

        state = registry.export_state()

        assert "on_task_start" in state
        assert len(state["on_task_start"]) == 1
        assert state["on_task_start"][0]["name"] == "handler_a"
        assert state["on_task_start"][0]["priority"] == 30

    def test_export_state_includes_module_info(self):
        """Should include module and qualname for debugging."""
        registry = LifecycleHookRegistry()

        def test_handler(ctx):
            return ctx

        registry.register_named("on_pipeline_start", "test_h", test_handler)

        state = registry.export_state()

        entry = state["on_pipeline_start"][0]
        assert "module" in entry
        assert "qualname" in entry

    def test_export_state_empty_for_no_handlers(self):
        """Should return empty dict for registry with no handlers."""
        registry = LifecycleHookRegistry()

        state = registry.export_state()

        assert isinstance(state, dict)
        assert len(state) == 0


class TestImportState:
    """Tests for import_state method."""

    def test_import_state_restores_named_handlers(self):
        """Should restore handlers from exported state."""
        # Create first registry and register handler
        registry1 = LifecycleHookRegistry()

        def handler_a(ctx):
            return ctx

        registry1.register_named("on_task_start", "handler_a", handler_a, priority=20)

        state = registry1.export_state()

        # Create second registry and import state
        registry2 = LifecycleHookRegistry()
        registry2.import_state(state)

        listed = registry2.list_handlers("on_task_start")
        assert len(listed["on_task_start"]) == 1
        assert "handler_a" in listed["on_task_start"]

    def test_import_state_preserves_priority(self):
        """Should restore handlers with correct priority."""
        registry1 = LifecycleHookRegistry()

        def handler_a(ctx):
            return ctx

        registry1.register_named(
            "on_task_complete", "handler_a", handler_a, priority=50
        )
        state = registry1.export_state()

        registry2 = LifecycleHookRegistry()
        registry2.import_state(state)

        # Emit should work (handler was imported)
        result = registry2.emit("on_task_complete", {"test": "value"})
        assert result.get("test") == "value"

    def test_import_state_skips_invalid_points(self):
        """Should skip handlers for lifecycle points that don't exist."""

        # Register a named handler first
        def test_handler(ctx):
            return ctx

        register_named_handler("on_task_start", "test_h", test_handler)

        registry = LifecycleHookRegistry()

        # State with invalid point + valid point
        state = {
            "invalid_point": [{"name": "h1", "priority": 10}],
            "on_task_start": [{"name": "test_h", "priority": 20}],
        }

        # Should not raise
        registry.import_state(state)

        # Only valid point should be imported
        assert len(registry.list_handlers().get("on_task_start", [])) == 1

    def test_import_state_skips_unnamed_handlers(self):
        """Should skip handlers without names (cannot be recovered)."""
        registry = LifecycleHookRegistry()

        # State with unnamed handler
        state = {
            "on_task_start": [
                {"name": None, "priority": 10},  # Should skip
                {
                    "name": "valid_handler",
                    "priority": 20,
                },  # Should skip (not in global registry)
            ]
        }

        registry.import_state(state)

        # Neither should be imported (one unnamed, one not in global registry)
        assert len(registry.list_handlers().get("on_task_start", [])) == 0

    def test_import_state_does_not_duplicate(self):
        """Should not add duplicate handlers if already registered."""

        def handler_a(ctx):
            return ctx

        registry = LifecycleHookRegistry()
        registry.register_named("on_task_start", "handler_a", handler_a)

        state = registry.export_state()
        registry.import_state(state)

        listed = registry.list_handlers("on_task_start")
        # Should only have one instance, not two
        assert len(listed["on_task_start"]) == 1


class TestGlobalNamedHandlerRegistry:
    """Tests for global register_named_handler / get_named_handler functions."""

    def test_global_register_and_retrieve(self):
        """Global functions should work across registry instances."""

        def global_handler(ctx):
            return {"global": True}

        # Register globally
        register_named_handler("on_pipeline_start", "global_h", global_handler)

        # Retrieve in different scope
        retrieved = get_named_handler("on_pipeline_start", "global_h")
        assert retrieved is global_handler

    def test_global_retrieve_none_for_nonexistent(self):
        """Should return None for unregistered handler."""
        result = get_named_handler("on_task_start", "nonexistent")
        assert result is None


class TestLifecyclePointsConstant:
    """Tests for LIFECYCLE_POINTS constant."""

    def test_includes_all_points(self):
        """LIFECYCLE_POINTS should include all expected points."""
        expected = {
            "on_pipeline_start",
            "on_task_start",
            "on_task_complete",
            "on_pdca_cycle",
            "on_pipeline_complete",
            "on_error",
            "on_recover",
            "on_retry",
        }
        assert set(LIFECYCLE_POINTS) == expected

    def test_is_tuple(self):
        """LIFECYCLE_POINTS should be a tuple."""
        assert isinstance(LIFECYCLE_POINTS, tuple)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
