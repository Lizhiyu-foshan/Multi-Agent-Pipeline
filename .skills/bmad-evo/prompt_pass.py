"""
PromptPass - Prompt passing protocol for opencode skill proxy mode

Instead of trying to call models synchronously (which causes recursive blocking),
this module enables a multi-turn execution model:

1. adapter.execute() does local computation, then returns a "pending model request"
2. opencode agent reads the pending request from the result, executes model inference
3. opencode agent calls adapter.execute() again with the model response in context
4. adapter continues from where it left off

State is tracked in .bmad/execution-state.yaml

Protocol:
  - result["model_request"] != None → agent should execute this prompt and call back
  - result["model_request"] == None → execution complete for this step
  - context.get("model_response") → previous model response, continues execution
"""

import yaml
import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class PromptPass:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.bmad_dir = project_path / ".bmad"
        self.prompts_dir = self.bmad_dir / "prompts"
        self.state_file = self.bmad_dir / "execution-state.yaml"

    def create_request(
        self, prompt_id: str, prompt: str, model: str, context_type: str, caller: str
    ) -> Dict[str, Any]:
        """Create a pending model request. Returns a result dict that signals
        the opencode agent to execute model inference."""
        self.prompts_dir.mkdir(parents=True, exist_ok=True)

        request_file = self.prompts_dir / f"req-{prompt_id}.md"
        request_content = f"""# Model Request: {prompt_id}

**Caller**: {caller}
**Model**: {model}
**Type**: {context_type}
**Created**: {datetime.now().isoformat()}

## Prompt

{prompt}

## Instructions for Agent

1. Read and understand the prompt above
2. Execute model inference using your capabilities
3. Write the response to `.bmad/prompts/res-{prompt_id}.md`
4. Call the adapter again with `context.model_response = <your response>`
"""
        request_file.write_text(request_content, encoding="utf-8")

        self._save_state(prompt_id, "pending", caller, context_type)

        return {
            "model_request": {
                "id": prompt_id,
                "prompt": prompt,
                "model": model,
                "type": context_type,
                "request_file": str(request_file),
                "response_file": str(self.prompts_dir / f"res-{prompt_id}.md"),
            },
        }

    def get_response(self, context: Dict[str, Any]) -> Optional[str]:
        """Get model response from context (injected by opencode agent)."""
        response = context.get("model_response")
        if response:
            return response

        prompt_id = context.get("model_request_id")
        if not prompt_id:
            return None

        response_file = self.prompts_dir / f"res-{prompt_id}.md"
        if response_file.exists():
            content = response_file.read_text(encoding="utf-8")
            self._save_state(prompt_id, "completed")
            return self._extract_response(content)

        return None

    def _extract_response(self, content: str) -> str:
        if "## Response" in content:
            parts = content.split("## Response", 1)
            return parts[1].strip()
        return content.strip()

    def _save_state(
        self, prompt_id: str, status: str, caller: str = "", context_type: str = ""
    ):
        self.bmad_dir.mkdir(parents=True, exist_ok=True)
        state = self._load_state()
        state.setdefault("requests", []).append(
            {
                "id": prompt_id,
                "status": status,
                "caller": caller,
                "type": context_type,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self._write_state(state)

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            return yaml.safe_load(self.state_file.read_text(encoding="utf-8")) or {}
        return {}

    def _write_state(self, state: Dict[str, Any]):
        with open(self.state_file, "w", encoding="utf-8") as f:
            yaml.dump(state, f, allow_unicode=True, default_flow_style=False)

    def write_response_file(self, prompt_id: str, response: str):
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        response_file = self.prompts_dir / f"res-{prompt_id}.md"
        response_file.write_text(
            f"# Model Response: {prompt_id}\n\n## Response\n\n{response}\n",
            encoding="utf-8",
        )
