"""
OpenCode Driver - Step-by-step pipeline driver where opencode is the model.

Usage:
    python scripts/opencode_driver.py init --desc "description"
    python scripts/opencode_driver.py status
    python scripts/opencode_driver.py respond --response-file path/to/response.txt
    python scripts/opencode_driver.py auto [--max-steps 50]
    python scripts/opencode_driver.py prompt
    python scripts/opencode_driver.py reset

State is persisted to .pipeline/opencode_driver_state.json.
Pipeline state is persisted by the orchestrator to .pipeline/state/pipelines.json.
"""

import argparse
import importlib
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "specs"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("opencode_driver")

DRIVER_STATE_FILE = PROJECT_ROOT / ".pipeline" / "opencode_driver_state.json"


def load_skill_adapter(skill_name: str, project_path: str) -> Optional[object]:
    adapter_map = {
        "bmad-evo": PROJECT_ROOT / ".skills" / "bmad-evo" / "adapter.py",
        "superpowers": PROJECT_ROOT / ".skills" / "superpowers" / "adapter.py",
        "spec-kit": PROJECT_ROOT / ".skills" / "spec-kit" / "adapter.py",
        "writing-skills": PROJECT_ROOT / ".skills" / "writing-skills" / "adapter.py",
        "multi-agent-pipeline": PROJECT_ROOT
        / ".skills"
        / "multi-agent-pipeline"
        / "adapter.py",
        "project-manage": PROJECT_ROOT / ".skills" / "project-manage" / "adapter.py",
    }
    adapter_path = adapter_map.get(skill_name)
    if not adapter_path or not adapter_path.exists():
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
                elif "state_dir" in obj.__init__.__code__.co_varnames:
                    state_dir = os.path.join(project_path, ".pipeline")
                    return obj(state_dir=state_dir)
                else:
                    return obj()
            except TypeError:
                try:
                    return obj()
                except Exception:
                    pass
    return None


class DriverState:
    def __init__(self):
        self.pipeline_id: str = ""
        self.last_action: str = ""
        self.last_result: Dict[str, Any] = {}
        self.total_steps: int = 0
        self.created_at: str = ""
        self.updated_at: str = ""
        self.pending_analysis: Dict[str, Any] = {}

    def save(self):
        DRIVER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now().isoformat()
        data = {
            "pipeline_id": self.pipeline_id,
            "last_action": self.last_action,
            "last_result": self.last_result,
            "total_steps": self.total_steps,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pending_analysis": self.pending_analysis,
        }
        fd, tmp = __import__("tempfile").mkstemp(
            dir=str(DRIVER_STATE_FILE.parent), suffix=".json.tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
            os.replace(tmp, str(DRIVER_STATE_FILE))
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @classmethod
    def load(cls) -> "DriverState":
        state = cls()
        if DRIVER_STATE_FILE.exists():
            try:
                with open(str(DRIVER_STATE_FILE), "r", encoding="utf-8") as f:
                    data = json.load(f)
                state.pipeline_id = data.get("pipeline_id", "")
                state.last_action = data.get("last_action", "")
                state.last_result = data.get("last_result", {})
                state.total_steps = data.get("total_steps", 0)
                state.created_at = data.get("created_at", "")
                state.updated_at = data.get("updated_at", "")
                state.pending_analysis = data.get("pending_analysis", {})
            except Exception:
                pass
        return state

    @staticmethod
    def reset():
        if DRIVER_STATE_FILE.exists():
            os.unlink(str(DRIVER_STATE_FILE))


class OpenCodeDriver:
    def __init__(self):
        self.state = DriverState.load()
        self.skills: Dict[str, Any] = {}
        self.orchestrator = None

    def _setup(self):
        if self.orchestrator is not None:
            return

        os.environ["BMAD_EVO_LOCAL_RESPONSE"] = "1"

        logger.info("Loading skill adapters...")
        for skill_name in [
            "bmad-evo",
            "superpowers",
            "spec-kit",
            "project-manage",
            "multi-agent-pipeline",
        ]:
            adapter = load_skill_adapter(skill_name, str(PROJECT_ROOT))
            if adapter:
                self.skills[skill_name] = adapter
                logger.info(f"  Loaded: {skill_name}")

        from pipeline.pipeline_orchestrator import PipelineOrchestrator

        watchdog_config = None
        config_file = PROJECT_ROOT / "config" / "map.json"
        if config_file.exists():
            with open(str(config_file), "r", encoding="utf-8") as f:
                config = json.load(f)
            watchdog_config = config.get("watchdog")

        old_recover = PipelineOrchestrator._recover_crashed_pipelines
        PipelineOrchestrator._recover_crashed_pipelines = lambda self_orch: None

        self.orchestrator = PipelineOrchestrator(
            state_dir=str(PROJECT_ROOT / ".pipeline"),
            skills=self.skills,
            watchdog_config=watchdog_config if watchdog_config is not None else False,
        )

        PipelineOrchestrator._recover_crashed_pipelines = old_recover

        self.orchestrator._auto_continue = True
        self._patch_bmad_for_local_response()
        logger.info("Orchestrator initialized")

    def cmd_init(self, description: str) -> Dict[str, Any]:
        self._setup()

        if self.state.pipeline_id:
            existing = self.orchestrator.pipelines.get(self.state.pipeline_id)
            if existing:
                return {
                    "error": f"Pipeline already exists: {self.state.pipeline_id}. Use 'reset' first.",
                    "pipeline_id": self.state.pipeline_id,
                }

        pipeline, create_result = self.orchestrator.create_pipeline(
            description=description,
        )
        self.state.pipeline_id = pipeline.id
        self.state.last_action = create_result.get("action", "")
        self.state.last_result = create_result
        self.state.total_steps = 0
        self.state.created_at = datetime.now().isoformat()
        self.state.save()

        return self._format_output(create_result)

    def _patch_bmad_for_local_response(self):
        try:
            import importlib
            mod = importlib.import_module("model_bridge")
            bridge_cls = getattr(mod, "ModelBridge", None)
            if bridge_cls:
                original_call_opencode = bridge_cls._call_opencode

                def _local_opencode(self_mb, model, prompt, timeout):
                    return self_mb._generate_local_response(model, prompt)

                bridge_cls._call_opencode = _local_opencode
                logger.info("Patched bmad-evo ModelBridge._call_opencode -> _generate_local_response")
        except Exception as e:
            logger.debug(f"Could not patch bmad-evo ModelBridge: {e}")

    def _suppress_recovery(self):
        for p in self.orchestrator.pipelines.values():
            if p.state in ("running", "RUNNING"):
                if p.phase not in ("completed", "failed"):
                    p.state = "paused"
        self.orchestrator._save_pipelines()

    def _ensure_running(self):
        pipeline = self.orchestrator.pipelines.get(self.state.pipeline_id)
        if pipeline and pipeline.state != "running":
            pipeline.state = "running"
            self.orchestrator._save_pipelines()

    def cmd_status(self) -> Dict[str, Any]:
        self._setup()

        if not self.state.pipeline_id:
            return {"status": "no_pipeline", "message": "No active pipeline. Use 'init' first."}

        pipeline = self.orchestrator.pipelines.get(self.state.pipeline_id)
        if not pipeline:
            return {"status": "pipeline_not_found", "pipeline_id": self.state.pipeline_id}

        return {
            "status": "active",
            "pipeline_id": self.state.pipeline_id,
            "pipeline_state": pipeline.state,
            "pipeline_phase": pipeline.phase,
            "total_steps": self.state.total_steps,
            "last_action": self.state.last_action,
            "backlog_count": len(pipeline.backlog) if pipeline.backlog else 0,
            "tasks_total": len(pipeline.tasks) if pipeline.tasks else 0,
            "tasks_completed": len(
                [t for t in (pipeline.tasks or []) if isinstance(t, dict) and t.get("status") == "completed"]
            ),
            "created_at": self.state.created_at,
            "updated_at": self.state.updated_at,
        }

    def cmd_prompt(self) -> Dict[str, Any]:
        if not self.state.last_result:
            return {"error": "No pending prompt"}

        action = self.state.last_action
        if action in ("analyze", "plan", "model_request"):
            prompt = self.state.last_result.get("prompt", "")
            if prompt:
                return {
                    "action": action,
                    "prompt": prompt,
                    "session_id": self.state.last_result.get("session_id", ""),
                    "prompt_length": len(prompt),
                    "prompt_length_kb": round(len(prompt.encode("utf-8")) / 1024, 1),
                }

        return {
            "action": action,
            "message": f"Current action '{action}' does not have a model prompt. Use 'auto' to handle it.",
        }

    def cmd_respond(self, response: str) -> Dict[str, Any]:
        self._setup()

        if not self.state.pipeline_id:
            return {"error": "No active pipeline. Use 'init' first."}

        self._ensure_running()
        action = self.state.last_action
        self.state.total_steps += 1

        if action in ("analyze", "plan"):
            try:
                response_data = json.loads(response)
            except json.JSONDecodeError:
                response_data = {"success": True, "artifacts": {"response": response}}

            if action == "analyze":
                self.state.pending_analysis = response_data
                self.state.save()

            result = self.orchestrator.advance(
                self.state.pipeline_id, response_data
            )

        elif action == "model_request":
            session_id = self.state.last_result.get("session_id", "")
            if not session_id:
                return {"error": "No session_id in last result"}

            result = self.orchestrator.resume_model_request(session_id, response)

        else:
            return {
                "error": f"Cannot respond to action '{action}'. Use 'auto' for non-model actions.",
                "current_action": action,
            }

        new_action = result.get("action", "unknown")
        self.state.last_action = new_action
        self.state.last_result = result
        self.state.save()

        return self._format_output(result)

    def cmd_auto(self, max_steps: int = 50) -> Dict[str, Any]:
        self._setup()

        if not self.state.pipeline_id:
            return {"error": "No active pipeline. Use 'init' first."}

        self._ensure_running()
        steps_taken = 0
        last_result = self.state.last_result
        wait_cycles = 0
        max_wait_cycles = 10

        while steps_taken < max_steps:
            action = last_result.get("action", "unknown")

            if action == "wait":
                wait_cycles += 1
                if wait_cycles > max_wait_cycles:
                    self.state.last_action = action
                    self.state.last_result = last_result
                    self.state.save()
                    return {
                        "paused": True,
                        "reason": "wait_deadlock",
                        "steps_taken": steps_taken,
                        "total_steps": self.state.total_steps,
                        "message": "Pipeline appears stuck in 'wait' state",
                    }
            else:
                wait_cycles = 0

            if action in ("completed", "max_rounds_exceeded"):
                self.state.last_action = action
                self.state.last_result = last_result
                self.state.save()
                return {
                    "done": True,
                    "action": action,
                    "steps_taken": steps_taken,
                    "total_steps": self.state.total_steps,
                }

            if last_result.get("completed"):
                self.state.last_action = "completed"
                self.state.last_result = last_result
                self.state.save()
                return {
                    "done": True,
                    "action": "completed",
                    "steps_taken": steps_taken,
                    "total_steps": self.state.total_steps,
                }

            if action in ("analyze", "plan", "model_request"):
                self.state.last_action = action
                self.state.last_result = last_result
                self.state.save()
                return {
                    "paused": True,
                    "reason": "needs_model_response",
                    "action": action,
                    "prompt": last_result.get("prompt", ""),
                    "session_id": last_result.get("session_id", ""),
                    "steps_taken": steps_taken,
                    "total_steps": self.state.total_steps,
                    "prompt_length": len(last_result.get("prompt", "")),
                }

            if last_result.get("error") and not last_result.get("options"):
                self.state.last_action = action
                self.state.last_result = last_result
                self.state.save()
                return {
                    "error": True,
                    "action": action,
                    "error_message": last_result.get("error", ""),
                    "steps_taken": steps_taken,
                }

            result = self._handle_auto_action(last_result)
            if result is None:
                self.state.last_action = action
                self.state.last_result = last_result
                self.state.save()
                return {
                    "error": True,
                    "action": action,
                    "error_message": "Handler returned None",
                    "steps_taken": steps_taken,
                }

            last_result = result
            steps_taken += 1
            self.state.total_steps += 1

        self.state.last_action = last_result.get("action", "unknown")
        self.state.last_result = last_result
        self.state.save()
        return {
            "paused": True,
            "reason": "max_steps_reached",
            "steps_taken": steps_taken,
            "total_steps": self.state.total_steps,
            "last_action": self.state.last_action,
        }

    def _handle_auto_action(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action = result.get("action", "unknown")

        if action == "human_decision":
            return self.orchestrator.advance(
                self.state.pipeline_id,
                {"auto_continue": True, "sequential_mode": True},
            )

        elif action == "call_skill":
            return self._handle_call_skill(result)

        elif action == "execute_next_task":
            return self.orchestrator.advance(
                self.state.pipeline_id,
                {"_iteration": self.state.total_steps, "sequential_mode": True},
            )

        elif action == "check":
            return self.orchestrator.advance(self.state.pipeline_id, result)

        elif action == "failed":
            return None

        elif action == "wait":
            time.sleep(0.1)
            pipeline = self.orchestrator.pipelines.get(self.state.pipeline_id)
            if not pipeline:
                return None
            tasks = pipeline.tasks or []
            all_done = all(
                t.get("status") in ("completed", "failed", "skipped")
                for t in tasks
            )
            if all_done and tasks:
                return self.orchestrator.advance(
                    self.state.pipeline_id,
                    {"all_tasks_done": True, "sequential_mode": True},
                )
            has_ready = any(
                t.get("status") in ("pending", "ready") for t in tasks
            )
            if has_ready:
                return self.orchestrator.advance(
                    self.state.pipeline_id,
                    {"sequential_mode": True},
                )
            return self.orchestrator.advance(self.state.pipeline_id, result)

        else:
            logger.info(f"  Unknown action '{action}', passing through")
            return self.orchestrator.advance(self.state.pipeline_id, result)

    def _handle_call_skill(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        skill_name = result.get("skill", "")
        action_type = result.get("action_type", "")
        prompt = result.get("prompt", "")
        logger.info(f"  Calling skill: {skill_name}.{action_type}")

        if (
            skill_name == "bmad-evo"
            and action_type == "analyze"
            and self.state.pending_analysis
        ):
            analysis = self.state.pending_analysis
            self.state.pending_analysis = {}
            self.state.save()

            enriched = dict(analysis)
            if "artifacts" not in enriched:
                enriched["artifacts"] = {}
            enriched["artifacts"].update(analysis)
            enriched["success"] = True

            logger.info("  Using opencode-provided analysis instead of bmad-evo.analyze")
            return self.orchestrator.advance(
                self.state.pipeline_id, enriched
            )

        skill = self.skills.get(skill_name)
        if not skill:
            return self.orchestrator.advance(
                self.state.pipeline_id,
                {"success": False, "error": f"Skill {skill_name} not loaded"},
            )

        try:
            ctx = dict(result)
            ctx["action"] = action_type
            ctx["pipeline_id"] = self.state.pipeline_id
            skill_result = skill.execute(prompt, ctx)

            pending_round = 0
            pending = skill_result.get("pending_model_request")
            while pending and pending_round < 5:
                pending_round += 1
                synthetic_response = self._synthetic_model_response(
                    pending.get("prompt", ""), ctx
                )

                ctx_with_response = dict(ctx)
                ctx_with_response["model_response"] = synthetic_response
                ctx_with_response["model_request_id"] = pending.get("id", "")

                skill_result = skill.execute(prompt, ctx_with_response)
                pending = skill_result.get("pending_model_request")

            if pending:
                logger.warning(
                    f"  Skill {skill_name}.{action_type} remained pending after 5 rounds"
                )
                return self.orchestrator.advance(
                    self.state.pipeline_id,
                    {
                        "success": False,
                        "error": "Skill remained pending after 5 model rounds",
                    },
                )

            return self.orchestrator.advance(
                self.state.pipeline_id,
                {
                    "success": skill_result.get("success", True),
                    "artifacts": skill_result.get("artifacts", {}),
                },
            )
        except Exception as e:
            logger.error(f"  Skill execution error: {e}")
            return self.orchestrator.advance(
                self.state.pipeline_id,
                {"success": False, "error": str(e)},
            )

    def _synthetic_model_response(self, prompt: str, context: Dict) -> str:
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from pipeline.model_bridge.synthetic_bridge import SyntheticBridge

        bridge = SyntheticBridge(project_root=str(PROJECT_ROOT))
        from pipeline.model_bridge.base import ModelRequest
        req = ModelRequest(prompt=prompt, context=context)
        resp = bridge.call(req)
        return resp.content

    def _format_output(self, result: Dict[str, Any]) -> Dict[str, Any]:
        action = result.get("action", "unknown")
        output = {
            "action": action,
            "pipeline_id": result.get("pipeline_id", self.state.pipeline_id),
            "total_steps": self.state.total_steps,
        }

        if action in ("analyze", "plan", "model_request"):
            output["needs_response"] = True
            output["session_id"] = result.get("session_id", "")
            output["prompt"] = result.get("prompt", "")
            output["prompt_length"] = len(result.get("prompt", ""))
            output["round"] = result.get("round")
            output["rounds_remaining"] = result.get("rounds_remaining")
        elif action in ("completed", "max_rounds_exceeded"):
            output["needs_response"] = False
            output["done"] = True
        elif action == "call_skill":
            output["needs_response"] = False
            output["skill"] = result.get("skill", "")
            output["action_type"] = result.get("action_type", "")
        else:
            output["needs_response"] = False
            output["phase"] = result.get("phase", "")

        if result.get("error"):
            output["error"] = result["error"]

        return output

    def cmd_reset(self) -> Dict[str, Any]:
        DriverState.reset()
        self.state = DriverState.load()
        return {"reset": True, "message": "Driver state cleared. Use 'init' to start fresh."}


def main():
    parser = argparse.ArgumentParser(description="OpenCode Driver for MAP Pipeline")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a new pipeline")
    init_parser.add_argument("--desc", required=True, help="Pipeline description")

    subparsers.add_parser("status", help="Show current pipeline status")
    subparsers.add_parser("prompt", help="Show current pending prompt")

    respond_parser = subparsers.add_parser("respond", help="Provide model response")
    respond_group = respond_parser.add_mutually_exclusive_group(required=True)
    respond_group.add_argument("--response", help="Response text directly")
    respond_group.add_argument("--response-file", help="Read response from file")

    auto_parser = subparsers.add_parser("auto", help="Auto-handle non-model actions")
    auto_parser.add_argument("--max-steps", type=int, default=50)

    subparsers.add_parser("reset", help="Reset driver state")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    driver = OpenCodeDriver()

    try:
        if args.command == "init":
            result = driver.cmd_init(args.desc)
        elif args.command == "status":
            result = driver.cmd_status()
        elif args.command == "prompt":
            result = driver.cmd_prompt()
        elif args.command == "respond":
            if args.response:
                response_text = args.response
            else:
                with open(args.response_file, "r", encoding="utf-8") as f:
                    response_text = f.read()
            result = driver.cmd_respond(response_text)
        elif args.command == "auto":
            result = driver.cmd_auto(max_steps=args.max_steps)
        elif args.command == "reset":
            result = driver.cmd_reset()
        else:
            result = {"error": f"Unknown command: {args.command}"}

        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    except Exception as e:
        error_output = {
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(error_output, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
