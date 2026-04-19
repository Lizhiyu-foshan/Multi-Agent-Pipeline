"""
Data Processor Skill Adapter

Create a data processing skill

Actions:
- execute: Default execution action
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_pipeline_src = str(Path(__file__).resolve().parents[1] / "src")
if _pipeline_src not in sys.path:
    sys.path.insert(0, _pipeline_src)


class DataProcessor_Adapter(object):
    name = "data-processor"
    version = "1.0"

    def __init__(self, project_path: str = None, prompt_manager=None):
        self.project_path = project_path or str(Path.cwd())
        self._prompt_manager = prompt_manager

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "execute")
        action_map = {
            "execute": self._action_execute,
        }
        handler = action_map.get(action)
        if not handler:
            return {
                "success": False,
                "error": f"Unknown action: {action}. Available: {list(action_map.keys())}",
            }
        try:
            return handler(task_description, context)
        except Exception as e:
            logger.error(f"Action {action} failed: {e}")
            return {"success": False, "error": str(e)}

    def _action_execute(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Default execution action."""
        return {
            "success": True,
            "action": "execute",
            "artifacts": {
                "message": "Action execute executed",
            },
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "project_path": self.project_path,
        }
