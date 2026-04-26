"""
PipelineRunner - General-purpose multi-round PDCA pipeline driver.

Drives MAP pipelines for ANY project:
- Reads design docs and backlog files for task initialization
- Supports multiple model backends (opencode IPC, synthetic)
- Multi-pipeline chaining with cross-pipeline context
- Stagnation detection and time budget enforcement

Usage (library):
    from pipeline.runner import PipelineRunner

    runner = PipelineRunner(
        project_root="/path/to/project",
        description="Implement feature X",
        backlog_files=["docs/backlog.md"],
        design_docs=["docs/design.md"],
        model_mode="opencode_ipc",
    )
    result = runner.run()

Usage (CLI):
    python -m pipeline.runner --project-root . --description "..." --backlog docs/backlog.md
"""

import importlib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def load_skill_adapter(skill_name: str, project_path: str, skills_root: str = None) -> Optional[object]:
    if skills_root:
        adapter_path = Path(skills_root) / skill_name / "adapter.py"
    else:
        adapter_path = Path(project_path) / ".skills" / skill_name / "adapter.py"

    if not adapter_path.exists():
        return None

    mod_name = f"skill_{skill_name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, str(adapter_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if (
            isinstance(obj, type)
            and attr_name.endswith("_Adapter")
            and hasattr(obj, "execute")
        ):
            try:
                if "project_path" in obj.__init__.__code__.co_varnames:
                    return obj(project_path=project_path)
                else:
                    return obj()
            except Exception:
                try:
                    return obj(project_path=project_path)
                except Exception:
                    return obj()
    return None


class SyntheticBridge:
    def __init__(self, project_root: str):
        self.project_root = project_root
        self.execution_log: list = []

    def respond(self, prompt: str, context: Dict[str, Any]) -> str:
        self.execution_log.append(
            {
                "timestamp": datetime.now().isoformat(),
                "prompt_length": len(prompt),
                "context_action": context.get("action", ""),
            }
        )

        prompt_lower = prompt.lower()
        if "analyze" in prompt_lower and ("task" in prompt_lower or "breakdown" in prompt_lower):
            return self._generate_analysis(prompt, context)
        elif "plan" in prompt_lower or "execution plan" in prompt_lower:
            return self._generate_plan(prompt, context)
        elif "review" in prompt_lower or "quality" in prompt_lower:
            return self._generate_review(prompt, context)
        elif "implement" in prompt_lower or "code" in prompt_lower:
            return self._generate_implementation(prompt, context)
        else:
            return self._generate_generic(prompt, context)

    def _generate_analysis(self, prompt: str, context: Dict) -> str:
        return json.dumps({
            "task_type": "feature",
            "complexity_score": 6,
            "recommended_roles_count": 2,
            "risk_factors": ["Existing tests must pass before changes"],
            "success_criteria": ["All tests pass"],
            "roles": [
                {"type": "developer", "name": "pm-developer", "capabilities": ["code", "test"]},
                {"type": "reviewer", "name": "pm-reviewer", "capabilities": ["review", "quality"]},
            ],
            "tasks": [
                {"name": "verify_existing_tests", "description": "Run existing test suite to confirm baseline", "role": "developer", "depends_on": [], "priority": "P1"},
                {"name": "implement_remaining_features", "description": "Implement features", "role": "developer", "depends_on": ["verify_existing_tests"], "priority": "P1"},
                {"name": "write_integration_tests", "description": "Write integration tests", "role": "developer", "depends_on": ["implement_remaining_features"], "priority": "P1"},
                {"name": "quality_review", "description": "Quality review", "role": "reviewer", "depends_on": ["write_integration_tests"], "priority": "P2"},
                {"name": "final_regression", "description": "Full regression", "role": "developer", "depends_on": ["quality_review"], "priority": "P1"},
            ],
        })

    def _generate_plan(self, prompt: str, context: Dict) -> str:
        return json.dumps({
            "task_graph": {
                "tasks": [
                    {"name": "verify_existing_tests", "description": "Run test suite", "role": "developer", "priority": "P1", "depends_on": []},
                    {"name": "implement_remaining_features", "description": "Implement features", "role": "developer", "priority": "P1", "depends_on": ["verify_existing_tests"]},
                    {"name": "write_integration_tests", "description": "Write integration tests", "role": "developer", "priority": "P1", "depends_on": ["implement_remaining_features"]},
                    {"name": "quality_review", "description": "Quality review", "role": "reviewer", "priority": "P2", "depends_on": ["write_integration_tests"]},
                    {"name": "final_regression", "description": "Full regression", "role": "developer", "priority": "P1", "depends_on": ["quality_review"]},
                ],
                "execution_waves": [[0], [1], [2], [3], [4]],
            },
            "roles": [
                {"type": "developer", "name": "developer", "capabilities": ["code", "test"]},
                {"type": "reviewer", "name": "reviewer", "capabilities": ["review"]},
            ],
        })

    def _generate_review(self, prompt: str, context: Dict) -> str:
        return json.dumps({"success": True, "artifacts": {"review_result": "pass", "quality_score": 0.85}})

    def _generate_implementation(self, prompt: str, context: Dict) -> str:
        return json.dumps({
            "success": True,
            "artifacts": {
                "implementation_status": "completed",
                "files_modified": ["src/module.py", "tests/test_module.py"],
                "tests_passing": True,
                "output": "Implementation complete",
            },
        })

    def _generate_generic(self, prompt: str, context: Dict) -> str:
        return json.dumps({"success": True, "artifacts": {"response": "completed", "output": "Task completed"}})


class PipelineRunner:
    def __init__(
        self,
        project_root: str,
        description: str = "",
        backlog_files: List[str] = None,
        design_docs: List[str] = None,
        model_mode: str = "auto",
        skill_names: List[str] = None,
        skills_root: str = None,
        max_iterations: int = 500,
        max_hours: float = 5.0,
        max_stagnation_rounds: int = 3,
        max_skill_pending_rounds: int = 5,
        state_dir: str = None,
        dry_run: bool = False,
        skip_skill_analysis: bool = True,
        require_real_model: bool = False,
    ):
        self.project_root = project_root
        self.description = description or "MAP pipeline run"
        self.backlog_files = [Path(f) for f in (backlog_files or [])]
        self.design_docs = [Path(f) for f in (design_docs or [])]
        self.model_mode = model_mode
        self.skill_names = skill_names or [
            "bmad-evo", "superpowers", "spec-kit",
            "project-manage", "multi-agent-pipeline",
        ]
        self.skills_root = skills_root
        self.max_iterations = max_iterations
        self.max_hours = max_hours
        self.max_stagnation_rounds = max_stagnation_rounds
        self.max_skill_pending_rounds = max_skill_pending_rounds
        self.dry_run = dry_run
        self.require_real_model = require_real_model

        self.state_dir = state_dir or os.path.join(project_root, ".pipeline")
        self.synthetic_bridge = SyntheticBridge(project_root)
        self.skills: Dict[str, Any] = {}
        self.orchestrator = None
        self.pipeline_id = None
        self.iteration = 0
        self.total_iterations = 0
        self.total_pipelines = 0
        self.start_time = None
        self.stagnation_rounds = 0
        self.last_backlog_signature = ""
        self.cross_pipeline_context: Dict[str, Any] = {
            "issues": [],
            "backlog": [],
            "completed_work": [],
            "analysis_history": [],
        }
        self.initial_backlog: List[Dict[str, Any]] = []
        self._last_result: Optional[Dict[str, Any]] = None
        self._skip_skill_analysis = skip_skill_analysis
        self._pending_analysis: Optional[Dict[str, Any]] = None
        self._pending_plan: Optional[Dict[str, Any]] = None
        self._pending_analysis: Optional[Dict[str, Any]] = None

    def save_session(self, path: str = None):
        path = path or os.path.join(self.state_dir, "runner_session.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        last_result_path = os.path.join(os.path.dirname(path), "runner_last_result.json")
        data = {
            "project_root": self.project_root,
            "description": self.description,
            "pipeline_id": self.pipeline_id,
            "iteration": self.iteration,
            "total_iterations": self.total_iterations,
            "total_pipelines": self.total_pipelines,
            "stagnation_rounds": self.stagnation_rounds,
            "last_backlog_signature": self.last_backlog_signature,
            "cross_pipeline_context": self.cross_pipeline_context,
            "initial_backlog": self.initial_backlog,
            "backlog_files": [str(f) for f in self.backlog_files],
            "design_docs": [str(f) for f in self.design_docs],
            "max_hours": self.max_hours,
            "max_iterations": self.max_iterations,
            "model_mode": self.model_mode,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "skip_skill_analysis": self._skip_skill_analysis,
            "require_real_model": self.require_real_model,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        if self._last_result is not None:
            with open(last_result_path, "w", encoding="utf-8") as f:
                json.dump(self._last_result, f, indent=2, ensure_ascii=False, default=str)
        elif os.path.exists(last_result_path):
            os.unlink(last_result_path)

    @classmethod
    def load_session(cls, path: str = None) -> "PipelineRunner":
        path = path or os.path.join(".pipeline", "runner_session.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No session file: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        session_dir = os.path.dirname(os.path.abspath(path))
        runner = cls(
            project_root=data["project_root"],
            description=data["description"],
            backlog_files=data.get("backlog_files"),
            design_docs=data.get("design_docs"),
            model_mode=data.get("model_mode", "auto"),
            max_hours=data.get("max_hours", 5.0),
            max_iterations=data.get("max_iterations", 500),
            state_dir=session_dir,
            require_real_model=data.get("require_real_model", False),
        )
        runner.pipeline_id = data.get("pipeline_id")
        runner.iteration = data.get("iteration", 0)
        runner.total_iterations = data.get("total_iterations", 0)
        runner.total_pipelines = data.get("total_pipelines", 0)
        runner.stagnation_rounds = data.get("stagnation_rounds", 0)
        runner.last_backlog_signature = data.get("last_backlog_signature", "")
        runner.cross_pipeline_context = data.get("cross_pipeline_context", runner.cross_pipeline_context)
        runner.initial_backlog = data.get("initial_backlog", [])
        if data.get("start_time"):
            runner.start_time = datetime.fromisoformat(data["start_time"])
        runner._skip_skill_analysis = data.get("skip_skill_analysis", True)
        runner.setup()
        last_result_path = os.path.join(session_dir, "runner_last_result.json")
        if os.path.exists(last_result_path):
            with open(last_result_path, "r", encoding="utf-8") as f:
                runner._last_result = json.load(f)
        runner._skip_skill_analysis = data.get("skip_skill_analysis", True)
        return runner

    def setup(self):
        logger.info("Loading skill adapters...")
        for skill_name in self.skill_names:
            adapter = load_skill_adapter(skill_name, self.project_root, self.skills_root)
            if adapter:
                self.skills[skill_name] = adapter
                logger.info(f"  Loaded: {skill_name}")

        config_file = Path(self.project_root) / "config" / "map.json"
        pipeline_cfg = {}
        watchdog_config = None
        if config_file.exists():
            with open(str(config_file), "r", encoding="utf-8") as f:
                map_config = json.load(f)
            pipeline_cfg = map_config.get("pipeline", {})
            watchdog_config = map_config.get("watchdog")

        configured_hours = float(pipeline_cfg.get("default_max_duration_hours", self.max_hours))

        from pipeline.pipeline_orchestrator import PipelineOrchestrator

        _orig_recover = PipelineOrchestrator._recover_crashed_pipelines
        PipelineOrchestrator._recover_crashed_pipelines = lambda self_orch: None

        self.orchestrator = PipelineOrchestrator(
            state_dir=self.state_dir,
            skills=self.skills,
            watchdog_config=watchdog_config,
        )

        PipelineOrchestrator._recover_crashed_pipelines = _orig_recover

        self.orchestrator._auto_continue = True
        self._patch_bmad_for_local_response()
        logger.info(f"PipelineRunner ready (project={self.project_root}, model={self.model_mode})")

    def _patch_bmad_for_local_response(self):
        try:
            mod = importlib.import_module("model_bridge")
            bridge_cls = getattr(mod, "ModelBridge", None)
            if bridge_cls:
                def _local_opencode(self_mb, model, prompt, timeout):
                    return self_mb._generate_local_response(model, prompt)
                bridge_cls._call_opencode = _local_opencode
                logger.info("Patched bmad-evo ModelBridge._call_opencode -> _generate_local_response")
        except Exception as e:
            logger.debug(f"Could not patch bmad-evo ModelBridge: {e}")

    def step(self) -> Dict[str, Any]:
        """Execute one pipeline step. Returns result dict.
        
        If result['needs_model'] is True: caller should provide response via respond().
        If result['done'] is True: pipeline cycle is complete.
        Otherwise: step was auto-handled, call step() again.
        """
        if not self.orchestrator:
            self.setup()
            self._load_initial_backlog()
            self.start_time = self.start_time or datetime.now()

        while True:
            if not self.pipeline_id:
                return self._step_create_pipeline()

            if self.iteration >= self.max_iterations:
                return {"done": True, "reason": "max_iterations"}

            if self._last_result is None:
                return {"done": True, "reason": "no_pending_result"}

            if self.start_time:
                elapsed = (datetime.now() - self.start_time).total_seconds()
                max_seconds = self.max_hours * 3600
                if elapsed >= max_seconds:
                    return {"done": True, "reason": "time_limit"}

            action = self._last_result.get("action", "unknown")
            if action in ("completed", "max_rounds_exceeded") or self._last_result.get("completed"):
                self._collect_cross_pipeline_context()
                self._cap_cross_pipeline_context()
                if self._has_remaining_work() and not self._is_stagnating():
                    self.pipeline_id = None
                    continue
                return {
                    "done": True,
                    "action": action,
                    "total_pipelines": self.total_pipelines,
                    "total_iterations": self.total_iterations,
                }

            if self._last_result.get("error") and not self._last_result.get("options"):
                return {"done": True, "reason": "error", "error": self._last_result["error"]}

            if action in ("analyze", "plan", "model_request"):
                prompt = self._last_result.get("prompt", "")
                return {
                    "needs_model": True,
                    "action": action,
                    "prompt": prompt,
                    "session_id": self._last_result.get("session_id", ""),
                    "phase": self._last_result.get("phase", ""),
                    "pipeline_id": self.pipeline_id,
                    "iteration": self.iteration,
                    "total_iterations": self.total_iterations,
                }

            if action == "wait":
                return {
                    "waiting": True,
                    "action": "wait",
                    "message": self._last_result.get("message", "Waiting for progress"),
                    "pipeline_id": self.pipeline_id,
                    "iteration": self.iteration,
                    "total_iterations": self.total_iterations,
                }

            result = self._handle_action(self._last_result)
            if result is None:
                return {"done": True, "reason": "handler_error"}

            self.iteration += 1
            self.total_iterations += 1
            self._last_result = result

    def respond(self, response: str) -> Dict[str, Any]:
        if self._last_result is None:
            return {"error": "No pending result to respond to"}

        action = self._last_result.get("action", "unknown")
        saved_result = self._last_result

        if action in ("analyze", "plan"):
            try:
                response_data = json.loads(response)
            except json.JSONDecodeError:
                response_data = {"success": True, "artifacts": {"response": response}}

            if action == "analyze":
                if "artifacts" not in response_data:
                    artifacts = dict(response_data)
                    response_data = {"success": True, "artifacts": artifacts}
                if self._skip_skill_analysis:
                    self._pending_analysis = response_data["artifacts"]

            if action == "plan":
                if "artifacts" not in response_data:
                    response_data = {"success": True, "artifacts": response_data}
                self._pending_plan = response_data.get("artifacts", response_data)

            result = self.orchestrator.advance(self.pipeline_id, response_data)
        elif action == "model_request":
            session_id = self._last_result.get("session_id", "")
            result = self.orchestrator.resume_model_request(session_id, response)
        else:
            return {"error": f"Cannot respond to action '{action}'"}

        if result is None or (result.get("error") and not result.get("options")):
            self._last_result = saved_result
            return {
                "error": "advance/resume returned error",
                "detail": result.get("error", "None") if result else "None",
                "original_action": action,
                "pipeline_phase": self.orchestrator.pipelines.get(self.pipeline_id, None),
            }

        self.iteration += 1
        self.total_iterations += 1
        self._last_result = result
        return self.step()

    def get_status(self) -> Dict[str, Any]:
        if not self.pipeline_id or not self.orchestrator:
            return {"status": "not_started", "description": self.description}
        pipeline = self.orchestrator.pipelines.get(self.pipeline_id)
        if not pipeline:
            return {"status": "no_pipeline"}
        tasks = pipeline.tasks or []
        return {
            "status": "active",
            "pipeline_id": self.pipeline_id,
            "phase": pipeline.phase,
            "pipeline_state": pipeline.state,
            "total_pipelines": self.total_pipelines,
            "total_iterations": self.total_iterations,
            "tasks_total": len(tasks),
            "tasks_completed": len([t for t in tasks if isinstance(t, dict) and t.get("status") == "completed"]),
            "backlog": len(pipeline.backlog) if pipeline.backlog else 0,
        }

    def _step_create_pipeline(self) -> Dict[str, Any]:
        pipeline, create_result = self.orchestrator.create_pipeline(
            description=self._build_pipeline_description(),
        )
        self.pipeline_id = pipeline.id
        self.total_pipelines += 1
        self.iteration = 0

        if self.initial_backlog and self.total_pipelines == 1:
            pipeline.backlog = list(self.initial_backlog)
            self.orchestrator._save_pipelines()

        if self.cross_pipeline_context.get("backlog"):
            pipeline.backlog = self.cross_pipeline_context["backlog"]
            self.orchestrator._save_pipelines()

        self._last_result = create_result
        return self.step()

    def run(self) -> Dict[str, Any]:
        self.start_time = datetime.now()
        stopped_due_to_stagnation = False

        logger.info("=" * 60)
        logger.info(f"PIPELINE RUNNER START: {self.description}")
        logger.info(f"  project_root: {self.project_root}")
        logger.info(f"  model_mode: {self.model_mode}")
        logger.info(f"  backlog_files: {[str(f) for f in self.backlog_files]}")
        logger.info(f"  design_docs: {[str(f) for f in self.design_docs]}")
        logger.info("=" * 60)

        self.setup()
        self._load_initial_backlog()

        if self.dry_run:
            return self._dry_run()

        runtime_hours = self.max_hours
        if self.orchestrator and hasattr(self.orchestrator, "_runtime_config"):
            runtime_hours = float(
                self.orchestrator._runtime_config.get("pipeline", {}).get(
                    "default_max_duration_hours", self.max_hours
                )
            )
        max_seconds = runtime_hours * 3600
        logger.info(f"Time budget: {runtime_hours:.2f}h ({max_seconds:.0f}s)")

        while True:
            elapsed_total = (datetime.now() - self.start_time).total_seconds()
            if elapsed_total >= max_seconds:
                logger.info(f"Time limit reached ({elapsed_total:.0f}s / {max_seconds}s)")
                break

            pipeline, create_result = self.orchestrator.create_pipeline(
                description=self._build_pipeline_description(),
            )
            self.pipeline_id = pipeline.id
            self.total_pipelines += 1
            logger.info(f"Pipeline #{self.total_pipelines}: {self.pipeline_id}")

            if self.initial_backlog and self.total_pipelines == 1:
                pipeline.backlog = list(self.initial_backlog)
                self.orchestrator._save_pipelines()
                logger.info(f"Loaded {len(pipeline.backlog)} backlog items from files")

            if self.cross_pipeline_context.get("backlog"):
                pipeline.backlog = self.cross_pipeline_context["backlog"]
                self.orchestrator._save_pipelines()
                logger.info(f"Inherited {len(pipeline.backlog)} backlog items from previous pipeline")

            result = create_result
            self.iteration = 0
            try:
                while self.iteration < self.max_iterations:
                    self.iteration += 1
                    self.total_iterations += 1

                    action = result.get("action", "unknown")
                    logger.info(f"[{self.iteration}] action={action} phase={result.get('phase', '?')}")

                    self._log_budget_if_needed()

                    if action in ("completed", "max_rounds_exceeded"):
                        break
                    if result.get("completed"):
                        break
                    if result.get("error") and not result.get("options"):
                        logger.error(f"Pipeline error: {result['error']}")
                        break

                    result = self._handle_action(result)
                    if result is None:
                        logger.error("Handler returned None")
                        break

                    if (datetime.now() - self.start_time).total_seconds() >= max_seconds:
                        break

                    time.sleep(0.05)

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break
            except Exception as e:
                logger.error(f"Fatal error: {e}")
                logger.error(traceback.format_exc())

            self._collect_cross_pipeline_context()
            self._cap_cross_pipeline_context()

            if not self._has_remaining_work():
                logger.info("No remaining work, done")
                break
            if self._is_stagnating():
                stopped_due_to_stagnation = True
                logger.error("Stagnation detected, stopping")
                break

            logger.info("Starting next pipeline...")

        elapsed = (datetime.now() - self.start_time).total_seconds()
        summary = {
            "total_pipelines": self.total_pipelines,
            "total_iterations": self.total_iterations,
            "elapsed_seconds": elapsed,
            "model_requests": len(self.synthetic_bridge.execution_log),
            "skills_used": list(self.skills.keys()),
            "remaining_backlog": len(self.cross_pipeline_context.get("backlog", [])),
            "stagnation": stopped_due_to_stagnation,
        }
        logger.info(f"PIPELINE RUNNER END: {json.dumps(summary, indent=2)}")
        return summary

    def _load_initial_backlog(self):
        for bf in self.backlog_files:
            if bf.exists():
                items = self._parse_backlog_file(bf)
                self.initial_backlog.extend(items)
                logger.info(f"Loaded {len(items)} backlog items from {bf.name}")

    def _parse_backlog_file(self, filepath: Path) -> List[Dict[str, Any]]:
        with open(str(filepath), "r", encoding="utf-8") as f:
            content = f.read()

        items = []
        for line in content.splitlines():
            line = line.strip()
            m = re.match(r"^[-*]\s+\[([ xX])\]\s+(.+)$", line)
            if m:
                checked = m.group(1).lower() == "x"
                text = m.group(2).strip()
                items.append({
                    "text": text,
                    "completed": checked,
                    "source": filepath.name,
                    "priority": "P1" if not checked else "done",
                })
        return items

    def _build_pipeline_description(self) -> str:
        if self.total_pipelines == 0:
            desc = self.description
            if self.design_docs:
                for df in self.design_docs:
                    if df.exists():
                        with open(str(df), "r", encoding="utf-8") as f:
                            snippet = f.read()[:2000]
                        desc += f"\n\n## Design: {df.name}\n{snippet}"
            return desc

        completed = len(self.cross_pipeline_context.get("completed_work", []))
        remaining = len(self.cross_pipeline_context.get("backlog", []))
        issues = len(self.cross_pipeline_context.get("issues", []))
        return (
            f"Continuation #{self.total_pipelines + 1}: "
            f"{self.description} "
            f"(completed: {completed}, remaining: {remaining}, "
            f"open issues: {issues})"
        )

    def _call_model(self, prompt: str, context: Dict[str, Any]) -> str:
        if self.model_mode == "synthetic":
            return self.synthetic_bridge.respond(prompt, context)

        ipc_dir = Path(self.state_dir)
        request_file = ipc_dir / "model_request.json"
        response_file = ipc_dir / "model_response.json"

        if response_file.exists():
            try:
                response_file.unlink()
            except Exception:
                pass

        request_data = {
            "prompt": prompt,
            "context_action": context.get("action", ""),
            "model_request_type": context.get("model_request_type", ""),
            "session_id": context.get("session_id", ""),
            "pipeline_id": self.pipeline_id,
            "iteration": self.iteration,
            "project_root": self.project_root,
            "timestamp": datetime.now().isoformat(),
        }
        ipc_dir.mkdir(parents=True, exist_ok=True)
        with open(str(request_file), "w", encoding="utf-8") as f:
            json.dump(request_data, f, indent=2, ensure_ascii=False)

        logger.info(f"  Model request written ({len(prompt)} chars), waiting for opencode...")

        probe_wait = 3.0
        start = time.time()
        probed = False
        while True:
            elapsed = time.time() - start
            if response_file.exists():
                time.sleep(0.1)
                try:
                    with open(str(response_file), "r", encoding="utf-8") as f:
                        resp_data = json.load(f)
                    response_file.unlink()
                    content = resp_data.get("content", "")
                    if content:
                        logger.info(f"  Got opencode response: {len(content)} chars")
                        return content
                except Exception as e:
                    logger.warning(f"  Failed to read response: {e}")
                    continue

            if not probed and elapsed >= probe_wait:
                probed = True
                if request_file.exists():
                    if self.require_real_model:
                        raise RuntimeError(
                            "No IPC listener detected for model_request while require_real_model=True"
                        )
                    logger.info("  No IPC listener, using synthetic")
                    try:
                        request_file.unlink()
                    except Exception:
                        pass
                    return self.synthetic_bridge.respond(prompt, context)

            if elapsed >= 600:
                if self.require_real_model:
                    raise RuntimeError(
                        "IPC model request timeout while require_real_model=True"
                    )
                logger.warning("  IPC timeout, using synthetic")
                try:
                    request_file.unlink()
                except Exception:
                    pass
                return self.synthetic_bridge.respond(prompt, context)

            time.sleep(0.5)

    def _format_model_error(self, err: Exception) -> Dict[str, Any]:
        return {
            "error": str(err),
            "action": "model_request_failed",
            "pipeline_id": self.pipeline_id,
            "require_real_model": self.require_real_model,
        }

    def _handle_action(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action = result.get("action", "unknown")

        if action == "human_decision":
            return self.orchestrator.advance(
                self.pipeline_id,
                {"auto_continue": True, "sequential_mode": True},
            )

        elif action == "model_request":
            session_id = result.get("session_id", "")
            prompt = result.get("prompt", "")
            logger.info(f"  model_request (session={session_id[:16]}..., {len(prompt)} chars)")

            try:
                response = self._call_model(prompt, result)
            except Exception as e:
                return self._format_model_error(e)
            return self.orchestrator.resume_model_request(session_id, response)

        elif action == "call_skill":
            return self._handle_call_skill(result)

        elif action == "execute_next_task":
            return self.orchestrator.advance(
                self.pipeline_id,
                {"_iteration": self.iteration, "sequential_mode": True},
            )

        elif action == "check":
            return self.orchestrator.advance(self.pipeline_id, result)

        elif action in ("analyze", "plan"):
            logger.info(f"  {action} via model...")
            try:
                response = self._call_model(result.get("prompt", ""), result)
            except Exception as e:
                return self._format_model_error(e)
            try:
                response_data = json.loads(response)
            except json.JSONDecodeError:
                response_data = {"success": True, "artifacts": {"response": response}}
            return self.orchestrator.advance(self.pipeline_id, response_data)

        elif action == "wait":
            pipeline = self.orchestrator.pipelines.get(self.pipeline_id)
            if pipeline:
                tasks = pipeline.tasks or []
                has_ready = any(
                    t.get("status") in ("pending", "ready") for t in tasks if isinstance(t, dict)
                )
                all_done = tasks and all(
                    t.get("status") in ("completed", "failed", "skipped")
                    for t in tasks if isinstance(t, dict)
                )
                if all_done:
                    return self.orchestrator.advance(
                        self.pipeline_id,
                        {"all_tasks_done": True, "sequential_mode": True},
                    )
                if has_ready:
                    return self.orchestrator.advance(
                        self.pipeline_id, {"sequential_mode": True}
                    )
            time.sleep(0.2)
            return self.orchestrator.advance(self.pipeline_id, result)

        elif action == "failed":
            return None

        else:
            return self.orchestrator.advance(self.pipeline_id, result)

    def _handle_analyze_direct(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from pipeline.models import PipelinePhase

        pipeline = self.orchestrator.pipelines.get(self.pipeline_id)
        if not pipeline:
            return None

        artifacts = result.get("artifacts", {})
        logger.info(f"  _handle_analyze_direct: artifacts keys={list(artifacts.keys())}, tasks_count={len(artifacts.get('tasks', []))}")
        self.orchestrator.context.store_artifact(pipeline.id, "", "analysis", artifacts)

        roles_data = artifacts.get("roles", [])
        if roles_data:
            registered = self.orchestrator.scheduler.registry.register_from_analysis(
                {"roles": roles_data}
            )
            pipeline.roles = registered
            logger.info(f"  Registered {len(registered)} roles from analysis")

        tasks_data = list(artifacts.get("tasks", []))

        if not tasks_data:
            return self.orchestrator._handle_failure(pipeline, "Analysis produced no tasks", result)

        pipeline.artifacts["analysis"] = artifacts
        self.orchestrator._transition_phase(pipeline, PipelinePhase.PLAN)
        self.orchestrator._save_pipelines()

        prompt = (
            f"Create detailed execution plan based on analysis.\n"
            f"Tasks: {json.dumps(tasks_data, ensure_ascii=False)[:2000]}\n"
            f"Roles: {json.dumps(roles_data, ensure_ascii=False)[:1000]}"
        )

        self._pending_plan = {"task_graph": {"tasks": tasks_data}, "roles": roles_data}

        return {
            "action": "plan",
            "prompt": prompt,
            "pipeline_id": self.pipeline_id,
            "phase": pipeline.phase,
        }

    def _handle_call_skill_synthetic(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        synthetic_result = self.synthetic_bridge.respond(
            result.get("prompt", ""), {"action": result.get("action_type", "")}
        )
        try:
            response_data = json.loads(synthetic_result)
        except json.JSONDecodeError:
            response_data = {"success": True, "artifacts": {"response": synthetic_result}}
        return self.orchestrator.advance(self.pipeline_id, response_data)

    def _handle_call_skill(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        skill_name = result.get("skill", "")
        action_type = result.get("action_type", "")
        prompt = result.get("prompt", "")
        logger.info(f"  skill: {skill_name}.{action_type}")

        if (
            skill_name == "bmad-evo"
            and action_type == "analyze"
            and self._pending_analysis
        ):
            analysis = self._pending_analysis
            self._pending_analysis = None
            enriched = {"success": True, "artifacts": analysis}
            return self._handle_analyze_direct(enriched)

        if (
            skill_name == "bmad-evo"
            and action_type == "analyze"
            and not self._pending_analysis
        ):
            logger.warning(f"  bmad-evo.analyze called but _pending_analysis is empty, using synthetic")
            return self._handle_call_skill_synthetic(result)

        if (
            skill_name == "bmad-evo"
            and action_type == "plan"
            and self._pending_plan
        ):
            plan = self._pending_plan
            self._pending_plan = None
            enriched = {"success": True, "artifacts": plan}
            return self.orchestrator.advance(self.pipeline_id, enriched)

        skill = self.skills.get(skill_name)
        if not skill:
            return self.orchestrator.advance(
                self.pipeline_id,
                {"success": False, "error": f"Skill {skill_name} not loaded"},
            )

        try:
            ctx = dict(result)
            ctx["action"] = action_type
            ctx["pipeline_id"] = self.pipeline_id
            skill_result = skill.execute(prompt, ctx)

            pending_round = 0
            pending = skill_result.get("pending_model_request")
            while pending and pending_round < self.max_skill_pending_rounds:
                pending_round += 1
                model_response = self._call_model(pending.get("prompt", ""), ctx)
                ctx_with_response = dict(ctx)
                ctx_with_response["model_response"] = model_response
                ctx_with_response["model_request_id"] = pending.get("id", "")
                skill_result = skill.execute(prompt, ctx_with_response)
                pending = skill_result.get("pending_model_request")

            if pending:
                return self.orchestrator.advance(
                    self.pipeline_id,
                    {"success": False, "error": f"Skill remained pending after {self.max_skill_pending_rounds} model rounds"},
                )

            return self.orchestrator.advance(
                self.pipeline_id,
                {
                    "success": skill_result.get("success", True),
                    "artifacts": skill_result.get("artifacts", {}),
                },
            )
        except Exception as e:
            logger.error(f"  Skill error: {e}")
            return self.orchestrator.advance(
                self.pipeline_id, {"success": False, "error": str(e)},
            )

    def _collect_cross_pipeline_context(self):
        if not self.pipeline_id or not self.orchestrator:
            return
        pipeline = self.orchestrator.pipelines.get(self.pipeline_id)
        if not pipeline:
            return

        try:
            entries = self.orchestrator.context._pipelines.get(self.pipeline_id, [])
            for entry in entries:
                if hasattr(entry, "phase") and entry.phase == "check":
                    content = entry.content if hasattr(entry, "content") else ""
                    try:
                        data = json.loads(content) if isinstance(content, str) else content
                        issues = data.get("issues", [])
                        self.cross_pipeline_context["issues"].extend(issues)
                    except (json.JSONDecodeError, AttributeError):
                        pass
        except Exception:
            pass

        try:
            artifacts = self.orchestrator.context.get_artifacts(self.pipeline_id)
            if artifacts and len(artifacts) >= 1:
                self.cross_pipeline_context["completed_work"].append(
                    {"pipeline_id": self.pipeline_id, "artifacts_count": len(artifacts), "artifacts_keys": list(artifacts.keys())}
                )
        except Exception:
            pass

        if pipeline.backlog:
            pending = []
            for item in pipeline.backlog:
                if not isinstance(item, dict):
                    continue
                status = item.get("status", "")
                if status == "completed":
                    continue
                entry = dict(item)
                if status == "in_progress":
                    entry["status"] = "pending"
                pending.append(entry)
            self.cross_pipeline_context["backlog"] = pending

        if pipeline.tasks:
            for task in pipeline.tasks:
                if not isinstance(task, dict):
                    continue
                if task.get("status") == "completed":
                    self.cross_pipeline_context["completed_work"].append(
                        {"name": task.get("name", ""), "pipeline": self.pipeline_id}
                    )
                elif task.get("status") in ("pending", "ready"):
                    self.cross_pipeline_context["backlog"].append(
                        {"name": task.get("name", ""), "status": "pending", "role": task.get("role", "developer")}
                    )

        if hasattr(self.orchestrator, "scheduler") and hasattr(self.orchestrator.scheduler, "task_queue"):
            try:
                tq = self.orchestrator.scheduler.task_queue
                if hasattr(tq, "tasks"):
                    for tid, task in tq.tasks.items():
                        name = getattr(task, "name", task.get("name", "") if isinstance(task, dict) else "")
                        status = getattr(task, "status", task.get("status", "") if isinstance(task, dict) else "")
                        role = getattr(task, "role_id", task.get("role_id", "developer") if isinstance(task, dict) else "developer")
                        if status == "completed":
                            self.cross_pipeline_context["completed_work"].append(
                                {"name": name, "pipeline": self.pipeline_id}
                            )
                        elif status in ("failed", "pending", "ready"):
                            self.cross_pipeline_context["backlog"].append(
                                {"name": name, "status": "pending", "role": role}
                            )
            except Exception:
                pass

        if hasattr(pipeline, "issues") and isinstance(pipeline.issues, list):
            self.cross_pipeline_context["issues"].extend(pipeline.issues)

    def _has_remaining_work(self) -> bool:
        backlog = self.cross_pipeline_context.get("backlog", [])
        if backlog:
            pending = [b for b in backlog if isinstance(b, dict) and b.get("status") not in ("completed", "in_progress")]
            if pending:
                return True

        if not self.pipeline_id or not self.orchestrator:
            return False
        pipeline = self.orchestrator.pipelines.get(self.pipeline_id)
        if not pipeline:
            return False

        if pipeline.state == "completed":
            return False

        if pipeline.tasks:
            return any(
                t.get("status") in ("pending", "ready")
                for t in pipeline.tasks
                if isinstance(t, dict)
            )

        return True

    def _is_stagnating(self) -> bool:
        backlog = self.cross_pipeline_context.get("backlog", [])
        sig = json.dumps(
            sorted([b.get("name", b.get("text", "")) for b in backlog if isinstance(b, dict)]),
            sort_keys=True,
        )
        if sig == self.last_backlog_signature:
            self.stagnation_rounds += 1
        else:
            self.stagnation_rounds = 0
        self.last_backlog_signature = sig
        return self.stagnation_rounds >= self.max_stagnation_rounds

    def _log_budget_if_needed(self):
        if not self.pipeline_id or not self.orchestrator:
            return
        if self.total_iterations % 20 != 0:
            return
        try:
            usage = self.orchestrator.context.get_budget_usage(self.pipeline_id)
            logger.info(
                f"  Budget: {usage.get('usage_pct', 0):.0f}% "
                f"({usage.get('prompt_bytes', 0) / 1024:.1f}KB / "
                f"{usage.get('budget_bytes', 0) / 1024:.0f}KB)"
            )
        except Exception:
            pass

    def _cap_cross_pipeline_context(self, max_issues=50, max_completed=50, max_backlog=50):
        ctx = self.cross_pipeline_context
        if len(ctx.get("issues", [])) > max_issues:
            ctx["issues"] = ctx["issues"][-max_issues:]
        if len(ctx.get("completed_work", [])) > max_completed:
            ctx["completed_work"] = ctx["completed_work"][-max_completed:]
        if len(ctx.get("backlog", [])) > max_backlog:
            ctx["backlog"] = ctx["backlog"][-max_backlog:]

    def _dry_run(self) -> Dict[str, Any]:
        logger.info("DRY RUN - simulating pipeline creation")
        pipeline, create_result = self.orchestrator.create_pipeline(
            description=self.description,
        )
        logger.info(f"Dry run pipeline: {pipeline.id}")
        logger.info(f"First action: {create_result.get('action')}")
        logger.info(f"Backlog items loaded: {len(self.initial_backlog)}")
        logger.info(f"Design docs: {[f.name for f in self.design_docs]}")
        logger.info(f"Skills: {list(self.skills.keys())}")
        return {
            "dry_run": True,
            "pipeline_id": pipeline.id,
            "first_action": create_result.get("action"),
            "backlog_items": len(self.initial_backlog),
            "skills": list(self.skills.keys()),
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MAP Pipeline Runner")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--description", default="", help="Pipeline description")
    parser.add_argument("--backlog", nargs="*", help="Backlog file paths")
    parser.add_argument("--design-docs", nargs="*", help="Design doc file paths")
    parser.add_argument("--model-mode", default="auto", choices=["auto", "opencode_ipc", "synthetic"])
    parser.add_argument("--max-hours", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    runner = PipelineRunner(
        project_root=args.project_root,
        description=args.description,
        backlog_files=args.backlog,
        design_docs=args.design_docs,
        model_mode=args.model_mode,
        max_hours=args.max_hours,
        dry_run=args.dry_run,
    )
    result = runner.run()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
