"""
MAP Self-Bootstrap - Thin wrapper around PipelineRunner.

Uses MAP's own pipeline/PDCA/watchdog to drive development of MAP itself.
This is just PipelineRunner(project_root=".") with MAP-specific config.

In opencode environment, the AI drives the pipeline step-by-step:
- step() returns a prompt when AI input is needed
- AI provides response via respond()
- Repeat until done

Usage:
    python scripts/self_bootstrap.py [--description "..."] [--dry-run]
"""

import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "specs"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(PROJECT_ROOT / "self_bootstrap.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("self_bootstrap")


from pipeline.runner import PipelineRunner, SyntheticBridge, load_skill_adapter as _load_skill_adapter


def SelfBootstrapDriver(description="",
                        project_root=None,
                        max_iterations=500,
                        dry_run=False,
                        **kwargs):
    return PipelineRunner(
        project_root=project_root or str(PROJECT_ROOT),
        description=description,
        max_iterations=max_iterations,
        dry_run=dry_run,
        **kwargs,
    )


def LocalModelBridge(project_root):
    return SyntheticBridge(project_root)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MAP Self-Bootstrap Driver")
    parser.add_argument("--description", "-d", default="MAP self-bootstrap: develop MAP using MAP")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backlog", nargs="*", default=[
        str(PROJECT_ROOT / "docs" / "project-manage-backlog-v4.md"),
    ])
    parser.add_argument("--design-docs", nargs="*", default=[
        str(PROJECT_ROOT / "docs" / "project-manage-design-v4.md"),
    ])
    parser.add_argument("--max-hours", type=float, default=5.0)
    args = parser.parse_args()

    runner = PipelineRunner(
        project_root=str(PROJECT_ROOT),
        description=args.description,
        backlog_files=args.backlog,
        design_docs=args.design_docs,
        max_hours=args.max_hours,
        dry_run=args.dry_run,
    )
    result = runner.run()
    print(json.dumps(result, indent=2, default=str))
    return result


if __name__ == "__main__":
    main()
