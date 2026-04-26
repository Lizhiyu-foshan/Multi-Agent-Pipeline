"""
MAP slash-command proxy for chat environments.

Use this when a host can execute one registered slash command and forward the
rest of the raw input as arguments.

Examples:
    python scripts/map_command_proxy.py /?
    python scripts/map_command_proxy.py p /?
    python scripts/map_command_proxy.py p init demo --path D:\\demo
"""

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_HOOK = PROJECT_ROOT / "scripts" / "engine_hook.py"


def main() -> int:
    args = [a.replace("？", "?") for a in sys.argv[1:]]
    cmd = [sys.executable, str(ENGINE_HOOK)] + args
    completed = subprocess.run(cmd)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
