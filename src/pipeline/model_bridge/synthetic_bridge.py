"""
Synthetic Bridge - Local testing/development strategy.

Generates template-based responses for testing the pipeline
without a real model backend. Replaces LocalModelBridge.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .base import ModelBridgeBase, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


class SyntheticBridge(ModelBridgeBase):
    """
    Synthetic response generator for development and testing.

    Pattern-matches prompt keywords and returns structured JSON.
    The review path optionally runs real pytest via subprocess.
    """

    def __init__(self, project_root: str = "", enable_test_runner: bool = True):
        self._project_root = project_root or str(Path.cwd())
        self._enable_test_runner = enable_test_runner
        self._call_log: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "synthetic"

    def is_available(self) -> bool:
        return True

    def call(self, request: ModelRequest) -> ModelResponse:
        start = time.time()
        prompt = request.prompt
        prompt_lower = prompt.lower()

        if "analyze" in prompt_lower and ("task" in prompt_lower or "breakdown" in prompt_lower):
            content = self._generate_analysis(request)
        elif "plan" in prompt_lower or "execution plan" in prompt_lower:
            content = self._generate_plan(request)
        elif "review" in prompt_lower or "quality" in prompt_lower:
            content = self._generate_review(request)
        elif "debug" in prompt_lower or "error" in prompt_lower:
            content = self._generate_debug(request)
        elif "implement" in prompt_lower or "code" in prompt_lower:
            content = self._generate_implementation(request)
        else:
            content = self._generate_generic(request)

        latency = (time.time() - start) * 1000

        self._call_log.append({
            "model_id": request.model_id,
            "task_type": request.task_type,
            "prompt_len": len(prompt),
            "latency_ms": latency,
        })

        return ModelResponse(
            content=content,
            model=request.model,
            request_id=request.model_id,
            success=True,
            latency_ms=latency,
        )

    def _generate_analysis(self, request: ModelRequest) -> str:
        return json.dumps({
            "roles": [
                {"type": "pm-developer", "name": "Feature Developer", "capabilities": ["code", "test"]},
                {"type": "pm-reviewer", "name": "Code Reviewer", "capabilities": ["review", "quality"]},
            ],
            "tasks": [
                {"name": "analyze_requirements", "description": "Analyze requirements and create design", "role": "pm-developer", "priority": "P1"},
                {"name": "implement_core", "description": "Implement core functionality", "role": "pm-developer", "priority": "P1"},
                {"name": "write_tests", "description": "Write comprehensive tests", "role": "pm-developer", "priority": "P1"},
                {"name": "code_review", "description": "Review code quality and standards", "role": "pm-reviewer", "priority": "P2"},
                {"name": "integration_test", "description": "Run integration tests and verify", "role": "pm-developer", "priority": "P2"},
            ],
            "complexity": 6,
            "success_criteria": "All tests pass",
        }, ensure_ascii=False)

    def _generate_plan(self, request: ModelRequest) -> str:
        return json.dumps({
            "task_graph": {
                "tasks": [
                    {"name": "analyze_requirements", "description": "Analyze requirements", "role": "pm-developer", "priority": "P1", "depends_on": []},
                    {"name": "implement_core", "description": "Implement core", "role": "pm-developer", "priority": "P1", "depends_on": ["analyze_requirements"]},
                    {"name": "write_tests", "description": "Write tests", "role": "pm-developer", "priority": "P1", "depends_on": ["implement_core"]},
                    {"name": "code_review", "description": "Code review", "role": "pm-reviewer", "priority": "P2", "depends_on": ["write_tests"]},
                    {"name": "integration_test", "description": "Integration test", "role": "pm-developer", "priority": "P2", "depends_on": ["code_review"]},
                ],
                "execution_waves": [[0], [1], [2], [3], [4]],
            },
        }, ensure_ascii=False)

    def _generate_review(self, request: ModelRequest) -> str:
        test_output = ""
        if self._enable_test_runner:
            test_output = self._run_tests()

        return json.dumps({
            "review_passed": True,
            "quality_score": 0.8,
            "test_output": test_output[:500] if test_output else "No test run",
            "issues": [],
            "suggestions": ["Consider adding edge case tests"],
        }, ensure_ascii=False)

    def _generate_debug(self, request: ModelRequest) -> str:
        return json.dumps({
            "diagnosis": "Automated debug analysis completed",
            "fix_applied": True,
            "root_cause": "Generic diagnostic response",
            "suggested_fix": "Review the error context and apply targeted fix",
        }, ensure_ascii=False)

    def _generate_implementation(self, request: ModelRequest) -> str:
        return json.dumps({
            "implementation_status": "completed",
            "files_modified": ["src/module.py", "tests/test_module.py"],
            "test_results": {"passed": 269, "failed": 0},
            "artifacts": {"output": "Implementation completed successfully"},
        }, ensure_ascii=False)

    def _generate_generic(self, request: ModelRequest) -> str:
        return json.dumps({
            "action_taken": "proceed",
            "status": "completed",
            "artifacts": {"output": "Task processed"},
        }, ensure_ascii=False)

    def _run_tests(self) -> str:
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-q", "--tb=line"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=self._project_root,
            )
            return result.stdout[-1000:] if result.stdout else ""
        except subprocess.TimeoutExpired:
            return "Test run timed out after 120s"
        except Exception as e:
            return f"Test runner error: {e}"

    def get_call_log(self) -> list:
        return list(self._call_log)
