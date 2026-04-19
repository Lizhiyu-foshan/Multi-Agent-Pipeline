from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from adapters.platform_adapter import (
    OpenCodeAdapter,
    ClaudeCodeAdapter,
    OpenClawAdapter,
    GenericAdapter,
)


def test_execute_skill_stubs_raise_not_implemented():
    adapters = [
        (OpenCodeAdapter, "OpenCodeAdapter"),
        (ClaudeCodeAdapter, "ClaudeCodeAdapter"),
        (OpenClawAdapter, "OpenClawAdapter"),
        (GenericAdapter, "GenericAdapter"),
    ]
    for adapter_cls, expected_name in adapters:
        try:
            adapter_cls.execute_skill("dummy", {})
            assert False, f"{adapter_cls.__name__} should raise NotImplementedError"
        except NotImplementedError as e:
            assert expected_name in str(e)
