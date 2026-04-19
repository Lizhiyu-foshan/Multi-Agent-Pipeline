"""
Run MAP regression baseline test suite.

This script executes the stable baseline used after each code change.
It intentionally excludes script-style E2E suites that are slower and include
known environment-dependent behavior.
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "--ignore=tests/test_e2e.py",
        "--ignore=tests/test_real_adapter_e2e.py",
    ]

    print("Running MAP regression baseline...")
    print("Command:", " ".join(cmd))
    print("Repo:", str(repo_root))

    completed = subprocess.run(cmd, cwd=str(repo_root))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
