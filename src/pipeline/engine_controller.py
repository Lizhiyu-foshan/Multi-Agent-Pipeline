"""
EngineController - Unified entry point for the MAP engine control system.

Wires together:
- IgnitionGate: Should the engine start?
- TransmissionBridge: Prepare pipeline inputs
- PipelineOrchestrator: The engine itself
- CoolingSystem: Prevent context explosion
- BrakeSystem: Pause/stop/abort control

Usage:
    from pipeline.engine_controller import EngineController

    ctrl = EngineController(project_root="/path/to/project")
    result = ctrl.ignite("Complex task description")

    # Or directly handle simple tasks without engine:
    result = ctrl.handle("Simple task description")
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .cooling_system import CoolingSystem, CoolingConfig, CoolingLevel, CoolingState
from .brake_system import BrakeSystem, BrakeLevel, BrakeState
from .transmission import TransmissionBridge, TransmissionOutput, ProjectProfile
from .intent_gate import IntentGate, IntentResult, IntentType, ComplexityClass

logger = logging.getLogger(__name__)


class EngineState(str, Enum):
    OFF = "off"
    IGNITING = "igniting"
    RUNNING = "running"
    PAUSED = "paused"
    COOLING = "cooling"
    SHUTDOWN = "shutdown"


@dataclass
class IgnitionResult:
    should_start: bool = False
    complexity: ComplexityClass = ComplexityClass.SIMPLE
    intent_type: IntentType = IntentType.UNKNOWN
    reason: str = ""
    estimated_tasks: int = 0
    profile: Optional[ProjectProfile] = None


@dataclass
class EngineReport:
    state: EngineState = EngineState.OFF
    pipeline_id: str = ""
    started_at: Optional[datetime] = None
    runtime_seconds: float = 0.0
    tasks_completed: int = 0
    tasks_total: int = 0
    cooling_level: CoolingLevel = CoolingLevel.NORMAL
    brake_level: BrakeLevel = BrakeLevel.NONE
    shutdown_report: Optional[str] = None


class EngineController:
    def __init__(
        self,
        project_root: str = None,
        state_dir: str = None,
        max_runtime_hours: float = 5.0,
        skills: Dict[str, Any] = None,
    ):
        self.project_root = project_root or os.getcwd()
        if state_dir is None:
            state_dir = str(Path(self.project_root) / ".pipeline")
        self.state_dir = state_dir
        self.max_runtime_hours = max_runtime_hours

        self._state = EngineState.OFF
        self._orchestrator = None
        self._pipeline_id = None
        self._started_at: Optional[float] = None
        self._skills = skills or {}

        self.intent_gate = IntentGate(project_path=self.project_root)
        self.transmission = TransmissionBridge(project_root=self.project_root)

        cooling_config = CoolingConfig(
            shutdown_runtime_seconds=max_runtime_hours * 3600,
            snapshot_dir=str(Path(state_dir) / "cooling_snapshots"),
        )
        self.cooling = CoolingSystem(config=cooling_config, state_dir=state_dir)
        self.brake = BrakeSystem(state_dir=state_dir)

        self.brake.on_brake(BrakeLevel.PAUSE, self._on_pause)
        self.brake.on_brake(BrakeLevel.STOP, self._on_stop)
        self.brake.on_brake(BrakeLevel.ABORT, self._on_abort)

        self._report_path = Path(state_dir) / "engine_report.json"
        self._engine_state_path = Path(state_dir) / "engine_state.json"

        self._load_engine_state()

    @property
    def state(self) -> EngineState:
        return self._state

    def evaluate(self, description: str) -> IgnitionResult:
        """Evaluate whether the engine should start, without starting it."""
        intent = self.intent_gate.analyze(description)
        profile = self.transmission.analyze_project()

        should_start = intent.complexity_class in (
            ComplexityClass.MODERATE,
            ComplexityClass.COMPLEX,
            ComplexityClass.CRITICAL,
        )

        if intent.ambiguity_level.value in ("high", "moderate"):
            should_start = True

        estimated_tasks = max(1, len(intent.keywords) + len(intent.entities))

        return IgnitionResult(
            should_start=should_start,
            complexity=intent.complexity_class,
            intent_type=intent.intent_type,
            reason=self._ignition_reason(intent, profile),
            estimated_tasks=estimated_tasks,
            profile=profile,
        )

    def ignite(
        self,
        description: str,
        design_docs: List[str] = None,
        backlog_items: List[str] = None,
        auto_confirm: bool = False,
    ) -> Dict[str, Any]:
        """Start the engine for a complex task. Returns first action needed."""
        if self._state not in (EngineState.OFF, EngineState.SHUTDOWN):
            return {
                "status": "error",
                "reason": f"Engine is already {self._state.value}",
                "action": "none",
            }

        self._state = EngineState.IGNITING

        ignition = self.evaluate(description)
        if not ignition.should_start:
            self._state = EngineState.OFF
            return {
                "status": "skip",
                "reason": f"Task is {ignition.complexity.value}, engine not needed",
                "action": "handle_directly",
                "complexity": ignition.complexity.value,
            }

        logger.info(
            "Engine IGNITING: complexity=%s, intent=%s, estimated_tasks=%d",
            ignition.complexity.value, ignition.intent_type.value, ignition.estimated_tasks,
        )

        tx_output = self.transmission.generate_pipeline_input(
            description=description,
            design_docs=design_docs,
            backlog_items=backlog_items,
        )

        if not tx_output.profile.has_skills_dir:
            self.transmission.scaffold_skills(tx_output.profile)

        from .pipeline_orchestrator import PipelineOrchestrator
        self._orchestrator = PipelineOrchestrator(
            state_dir=self.state_dir,
            skills=self._skills,
        )

        pipeline, next_action = self._orchestrator.create_pipeline(description)
        self._pipeline_id = pipeline.id
        self._started_at = time.time()
        self._state = EngineState.RUNNING

        self.cooling.start()
        self._save_engine_state()

        logger.info("Engine RUNNING: pipeline_id=%s", self._pipeline_id)

        return {
            "status": "started",
            "pipeline_id": self._pipeline_id,
            "action": next_action.get("action", "unknown"),
            "prompt": next_action.get("prompt", ""),
            "ignition": {
                "complexity": ignition.complexity.value,
                "intent_type": ignition.intent_type.value,
                "estimated_tasks": ignition.estimated_tasks,
            },
            "project_type": tx_output.profile.project_type if tx_output.profile else "unknown",
        }

    def advance(self, phase_result: Dict[str, Any] = None) -> Dict[str, Any]:
        """Advance the pipeline one step. Returns next action or completion."""
        if self._state != EngineState.RUNNING:
            return {"status": "error", "reason": f"Engine is {self._state.value}", "action": "none"}

        self.brake.check_external_signal()
        if self.brake.is_braking:
            return {"status": "brake", "brake_level": self.brake.state.level.value, "action": "none"}

        pipeline_data = self._get_pipeline_data()
        cooling_level = self.cooling.check_and_cool(pipeline_data)

        if self.cooling.should_shutdown:
            self._shutdown("cooling_limit_reached")
            return {"status": "shutdown", "reason": "Runtime limit reached", "action": "none"}

        if self.cooling.is_low_power:
            logger.info("Low-power mode: serialized execution only")

        result = self._orchestrator.advance(self._pipeline_id, phase_result or {})

        if result.get("phase") == "completed" or result.get("phase") == "failed":
            self._shutdown(result.get("phase", "completed"))
            return {
                "status": "completed",
                "phase": result.get("phase"),
                "action": "none",
                "report": self.get_report(),
            }

        return result

    def respond(self, response: str) -> Dict[str, Any]:
        """Provide model response and continue."""
        if self._state != EngineState.RUNNING:
            return {"status": "error", "reason": f"Engine is {self._state.value}", "action": "none"}

        self.cooling.register_response(response)

        phase_result = {
            "success": True,
            "model_response": response,
        }
        return self.advance(phase_result)

    def handle(self, description: str) -> Dict[str, Any]:
        """Handle a simple task directly without starting the engine."""
        return {
            "status": "direct",
            "action": "execute_directly",
            "description": description,
            "message": "Task handled directly without engine",
        }

    def pause(self, reason: str = "") -> Dict[str, Any]:
        self.brake.pause(reason=reason, triggered_by="user")
        return {"status": "paused", "reason": reason}

    def resume(self) -> Dict[str, Any]:
        if self.brake.resume():
            self._state = EngineState.RUNNING
            self._save_engine_state()
            return {"status": "running", "pipeline_id": self._pipeline_id}
        return {"status": "error", "reason": "Cannot resume from current brake state"}

    def stop(self, reason: str = "") -> Dict[str, Any]:
        self.brake.stop(reason=reason, triggered_by="user")
        return {"status": "stopped", "reason": reason, "report": self.get_report()}

    def abort(self, reason: str = "") -> Dict[str, Any]:
        self.brake.abort(reason=reason, triggered_by="user")
        return {"status": "aborted", "reason": reason}

    def get_report(self) -> EngineReport:
        elapsed = (time.time() - self._started_at) if self._started_at else 0
        pipeline_data = self._get_pipeline_data()

        tasks = pipeline_data.get("tasks", [])
        completed = sum(1 for t in tasks if isinstance(t, dict) and t.get("status") == "completed")

        report = EngineReport(
            state=self._state,
            pipeline_id=self._pipeline_id or "",
            started_at=datetime.fromtimestamp(self._started_at) if self._started_at else None,
            runtime_seconds=elapsed,
            tasks_completed=completed,
            tasks_total=len(tasks),
            cooling_level=self.cooling.state.level,
            brake_level=self.brake.state.level,
            shutdown_report=self.cooling.state.shutdown_report_path,
        )
        return report

    def _get_pipeline_data(self) -> Dict[str, Any]:
        if not self._orchestrator or not self._pipeline_id:
            return {}
        pipeline = self._orchestrator.pipelines.get(self._pipeline_id)
        if not pipeline:
            return {}
        return {
            "phase": pipeline.phase,
            "tasks": [],
            "pdca_cycle": pipeline.pdca_cycle,
        }

    def _shutdown(self, reason: str):
        self._state = EngineState.SHUTDOWN
        self.cooling.stop()
        self._save_engine_state()
        logger.info("Engine SHUTDOWN: %s, runtime=%.1fh", reason,
                    (time.time() - self._started_at) / 3600 if self._started_at else 0)

        report = self.get_report()
        self._save_report(report)

    def _save_report(self, report: EngineReport):
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "state": report.state.value,
            "pipeline_id": report.pipeline_id,
            "started_at": report.started_at.isoformat() if report.started_at else None,
            "runtime_seconds": report.runtime_seconds,
            "tasks_completed": report.tasks_completed,
            "tasks_total": report.tasks_total,
            "cooling_level": report.cooling_level.value,
            "shutdown_report": report.shutdown_report,
        }
        with open(self._report_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_engine_state(self):
        self._engine_state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "state": self._state.value,
            "pipeline_id": self._pipeline_id or "",
            "started_at": self._started_at,
        }
        with open(self._engine_state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_engine_state(self):
        if not self._engine_state_path.exists():
            return
        try:
            with open(self._engine_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            saved_state = data.get("state", "off")
            try:
                self._state = EngineState(saved_state)
            except ValueError:
                self._state = EngineState.OFF

            saved_pid = data.get("pipeline_id", "")
            if saved_pid:
                self._pipeline_id = saved_pid

            self._started_at = data.get("started_at")

            if self._state in (EngineState.RUNNING, EngineState.PAUSED) and self._pipeline_id:
                self._reconnect_orchestrator()
        except Exception as e:
            logger.debug("Could not load engine state: %s", e)

    def _reconnect_orchestrator(self):
        if self._orchestrator is not None:
            return
        try:
            from .pipeline_orchestrator import PipelineOrchestrator
            self._orchestrator = PipelineOrchestrator(
                state_dir=self.state_dir,
                skills=self._skills,
            )
            if self._pipeline_id not in self._orchestrator.pipelines:
                logger.warning("Pipeline %s not found, resetting engine state", self._pipeline_id)
                self._state = EngineState.OFF
                self._pipeline_id = None
                self._save_engine_state()
        except Exception as e:
            logger.error("Failed to reconnect orchestrator: %s", e)
            self._state = EngineState.OFF

    def _on_pause(self, brake_state: BrakeState):
        self._state = EngineState.PAUSED
        self._save_engine_state()
        logger.info("Engine paused: %s", brake_state.reason)

    def _on_stop(self, brake_state: BrakeState):
        self._shutdown(f"user_stop: {brake_state.reason}")

    def _on_abort(self, brake_state: BrakeState):
        self._shutdown(f"emergency_abort: {brake_state.reason}")

    @staticmethod
    def _ignition_reason(intent: IntentResult, profile: ProjectProfile) -> str:
        parts = []
        parts.append(f"complexity={intent.complexity_class.value}")
        parts.append(f"intent={intent.intent_type.value}")
        if profile.stack:
            parts.append(f"stack={'+'.join(profile.stack)}")
        return ", ".join(parts)
