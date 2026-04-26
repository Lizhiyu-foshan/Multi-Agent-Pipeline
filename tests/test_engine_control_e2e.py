"""
End-to-End simulation tests for engine control system components:
  - CoolingSystem (tier transitions, auto-shutdown)
  - BrakeSystem (pause/stop/abort/resume, external signal)
  - TransmissionBridge (project analysis, skill scaffolding)
  - EngineController (full lifecycle with real PipelineOrchestrator)

Run: python tests/test_engine_control_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / ".skills" / "bmad-evo"))
sys.path.insert(0, str(Path(__file__).parent.parent / ".skills" / "superpowers"))
sys.path.insert(0, str(Path(__file__).parent.parent / ".skills" / "spec-kit"))

from pipeline.cooling_system import CoolingSystem, CoolingConfig, CoolingLevel, CoolingState
from pipeline.brake_system import BrakeSystem, BrakeLevel, BrakeState
from pipeline.transmission import TransmissionBridge, TransmissionOutput, ProjectProfile
from pipeline.engine_controller import (
    EngineController,
    EngineState,
    IgnitionResult,
    EngineReport,
)
from pipeline.intent_gate import IntentType, ComplexityClass
from pipeline.pipeline_orchestrator import PipelineOrchestrator

PASS = "PASS"
FAIL = "FAIL"
_results = []


def _assert(condition: bool, name: str, detail: str = ""):
    status = PASS if condition else FAIL
    msg = f"  [{status}] {name}"
    if detail and not condition:
        msg += f" -- {detail}"
    print(msg)
    _results.append((name, status, detail))
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def _section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _make_dir() -> str:
    return tempfile.mkdtemp(prefix="engine_e2e_")


def _cleanup(d: str):
    shutil.rmtree(d, ignore_errors=True)


# ===== Simulated skills (same as test_e2e.py) =====


class SimBmadEvo:
    def execute(self, desc: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        action = ctx.get("action", "analyze")
        if action == "analyze":
            return {
                "success": True,
                "artifacts": {
                    "roles": [
                        {"type": "architect", "name": "Arch", "capabilities": ["design"]},
                        {"type": "developer", "name": "Dev", "capabilities": ["code"]},
                    ],
                    "tasks": [
                        {"name": "Design", "role": "architect", "description": "Design", "priority": "P0", "depends_on": []},
                        {"name": "Implement", "role": "developer", "description": "Build", "priority": "P1", "depends_on": ["Design"]},
                    ],
                },
            }
        elif action in ("plan", "replan"):
            return {
                "success": True,
                "artifacts": {
                    "task_graph": {
                        "tasks": [
                            {"name": "Design", "role_id": "architect", "description": "Design", "priority": "P0", "depends_on": []},
                            {"name": "Implement", "role_id": "developer", "description": "Build", "priority": "P1", "depends_on": ["Design"]},
                        ],
                        "execution_waves": [["Design"], ["Implement"]],
                    }
                },
            }
        return {"success": True, "artifacts": {}}


class SimSuperpowers:
    def execute(self, desc: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "artifacts": {"code": "pass", "status": "DONE"}}


# ===== Test 1: CoolingSystem tier transitions =====


def test_cooling_system_tiers():
    _section("Test 1: CoolingSystem Tier Transitions")

    td = _make_dir()
    try:
        cfg = CoolingConfig(
            tier1_token_threshold=100,
            tier2_token_threshold=200,
            tier3_token_threshold=400,
            tier2_runtime_seconds=99999,
            tier3_runtime_seconds=99999,
            shutdown_runtime_seconds=99999,
            snapshot_dir=os.path.join(td, "snaps"),
        )
        cs = CoolingSystem(config=cfg, state_dir=td)
        cs.start()

        _assert(cs.state.level == CoolingLevel.NORMAL, "Starts at NORMAL")

        for i in range(25):
            cs.register_prompt("x" * 20)
        level = cs.check_and_cool({})
        _assert(level == CoolingLevel.LEVEL_1, f"Tier1 at ~100+ tokens", f"got {level.value}")

        for i in range(50):
            cs.register_prompt("y" * 20)
        level = cs.check_and_cool({})
        _assert(level == CoolingLevel.LEVEL_2, f"Tier2 at ~200+ tokens", f"got {level.value}")
        _assert(len(cs.state.snapshots) >= 1, "Snapshot saved at Tier2")

        for i in range(100):
            cs.register_prompt("z" * 20)
        level = cs.check_and_cool({})
        _assert(level == CoolingLevel.LEVEL_3, f"Tier3 at ~400+ tokens", f"got {level.value}")
        _assert(cs.is_low_power, "Low power mode at Tier3")
        _assert(not cs.should_shutdown, "Not shutting down yet")

        cs.stop()
        _assert(cs.state.compression_count >= 3, f"3+ compressions applied", f"got {cs.state.compression_count}")
    finally:
        _cleanup(td)


# ===== Test 2: CoolingSystem auto-shutdown by runtime =====


def test_cooling_system_shutdown():
    _section("Test 2: CoolingSystem Auto-Shutdown by Runtime")

    td = _make_dir()
    try:
        cfg = CoolingConfig(
            tier1_token_threshold=999999,
            tier2_token_threshold=999999,
            tier3_token_threshold=999999,
            shutdown_runtime_seconds=0.5,
            snapshot_dir=os.path.join(td, "snaps"),
        )
        cs = CoolingSystem(config=cfg, state_dir=td)
        cs.start()

        time.sleep(0.6)
        level = cs.check_and_cool({})
        _assert(level == CoolingLevel.SHUTDOWN, "Shutdown after timeout", f"got {level.value}")
        _assert(cs.should_shutdown, "should_shutdown is True")
        _assert(cs.state.shutdown_report_path is not None, "Shutdown report generated")
        _assert(os.path.exists(cs.state.shutdown_report_path), "Report file exists on disk")

        cs.stop()
    finally:
        _cleanup(td)


# ===== Test 3: BrakeSystem pause/stop/abort/resume =====


def test_brake_system_lifecycle():
    _section("Test 3: BrakeSystem Pause/Stop/Abort/Resume")

    td = _make_dir()
    try:
        bs = BrakeSystem(state_dir=td)

        _assert(bs.state.level == BrakeLevel.NONE, "Starts at NONE")
        _assert(bs.should_continue(), "Should continue at NONE")
        _assert(not bs.is_braking, "Not braking at NONE")

        bs.pause(reason="test pause", triggered_by="tester")
        _assert(bs.state.level == BrakeLevel.PAUSE, "Paused", f"got {bs.state.level.value}")
        _assert(bs.is_paused, "is_paused")
        _assert(not bs.should_continue(), "Should NOT continue when paused")
        _assert(bs.state.reason == "test pause", "Reason preserved")

        ok = bs.resume()
        _assert(ok, "Resume succeeds from PAUSE")
        _assert(bs.state.level == BrakeLevel.NONE, "Back to NONE after resume")

        bs.stop(reason="test stop")
        _assert(bs.state.level == BrakeLevel.STOP, "Stopped")

        ok = bs.resume()
        _assert(not ok, "Cannot resume from STOP")

        bs._state = BrakeState()
        bs._paused = False
        bs.abort(reason="emergency!")
        _assert(bs.state.level == BrakeLevel.ABORT, "Aborted")

        state_file = os.path.join(td, "brake_state.json")
        _assert(os.path.exists(state_file), "State persisted to disk")
        with open(state_file, "r") as f:
            data = json.load(f)
        _assert(data["level"] == "abort", "Persisted level is abort")
    finally:
        _cleanup(td)


# ===== Test 4: BrakeSystem external signal =====


def test_brake_system_external_signal():
    _section("Test 4: BrakeSystem External Signal")

    td = _make_dir()
    try:
        bs = BrakeSystem(state_dir=td)

        signal = {"level": "pause", "reason": "external trigger"}
        signal_path = bs._signal_file
        with open(signal_path, "w") as f:
            json.dump(signal, f)

        level = bs.check_external_signal()
        _assert(level == BrakeLevel.PAUSE, "External signal triggers PAUSE")
        _assert(bs.state.reason == "external trigger", "Reason from signal")

        bs.resume()

        signal2 = {"level": "stop", "reason": "sigstop"}
        with open(signal_path, "w") as f:
            json.dump(signal2, f)
        level = bs.check_external_signal()
        _assert(level == BrakeLevel.STOP, "External signal triggers STOP")

        _assert(not signal_path.exists(), "Signal file consumed after processing")
    finally:
        _cleanup(td)


# ===== Test 5: BrakeSystem callbacks =====


def test_brake_system_callbacks():
    _section("Test 5: BrakeSystem Callbacks")

    td = _make_dir()
    try:
        bs = BrakeSystem(state_dir=td)
        callback_log = []

        def on_pause(state):
            callback_log.append(("pause", state.reason))

        def on_stop(state):
            callback_log.append(("stop", state.reason))

        bs.on_brake(BrakeLevel.PAUSE, on_pause)
        bs.on_brake(BrakeLevel.STOP, on_stop)

        bs.pause(reason="cb test")
        _assert(len(callback_log) == 1, "Pause callback fired")
        _assert(callback_log[0] == ("pause", "cb test"), "Callback received correct reason")

        bs._state = BrakeState()
        bs._paused = False
        bs.stop(reason="stop cb")
        _assert(len(callback_log) == 2, "Stop callback fired")
        _assert(callback_log[1] == ("stop", "stop cb"), "Stop callback reason correct")
    finally:
        _cleanup(td)


# ===== Test 6: TransmissionBridge project analysis =====


def test_transmission_project_analysis():
    _section("Test 6: TransmissionBridge Project Analysis")

    td = _make_dir()
    try:
        pkg_file = os.path.join(td, "package.json")
        with open(pkg_file, "w") as f:
            f.write('{"dependencies": {"react": "^18"}}')

        app_dir = os.path.join(td, "src")
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "index.js"), "w") as f:
            f.write("console.log('hi')")

        tb = TransmissionBridge(project_root=td)
        profile = tb.analyze_project()

        _assert(profile.root == td, "Root set correctly")
        _assert(profile.name == os.path.basename(td), "Name is dir basename")
        _assert(profile.project_type == "frontend", f"Detected frontend", f"got {profile.project_type}")
        _assert("react" in profile.stack, f"React in stack", f"got {profile.stack}")
        _assert("package.json" in profile.entry_files or len(profile.key_directories) > 0, "Entry files or dirs found")
    finally:
        _cleanup(td)


# ===== Test 7: TransmissionBridge pipeline input generation =====


def test_transmission_pipeline_input():
    _section("Test 7: TransmissionBridge Pipeline Input Generation")

    td = _make_dir()
    try:
        tb = TransmissionBridge(project_root=td)
        output = tb.generate_pipeline_input(
            description="Build a REST API",
            design_docs=["doc1.md"],
            backlog_items=["task1", "task2"],
        )

        _assert(output.description == "Build a REST API", "Description preserved")
        _assert(output.project_root == td, "Project root set")
        _assert(output.design_docs == ["doc1.md"], "Design docs preserved")
        _assert(output.backlog_items == ["task1", "task2"], "Backlog preserved")
        _assert(output.model_mode in ("synthetic", "bridge", "opencode_ipc"), f"Valid model mode: {output.model_mode}")
        _assert(output.profile is not None, "Profile generated")
    finally:
        _cleanup(td)


# ===== Test 8: TransmissionBridge skill scaffolding =====


def test_transmission_skill_scaffold():
    _section("Test 8: TransmissionBridge Skill Scaffolding")

    td = _make_dir()
    try:
        tb = TransmissionBridge(project_root=td)
        profile = tb.analyze_project()

        _assert(not profile.has_skills_dir, "No .skills initially")

        ok = tb.scaffold_skills(profile)
        _assert(ok, "Scaffold returned True")

        adapter_path = os.path.join(td, ".skills", "adapter.py")
        _assert(os.path.exists(adapter_path), "adapter.py created")

        ok2 = tb.scaffold_skills(profile)
        _assert(ok2, "Second scaffold is no-op (already exists)")
    finally:
        _cleanup(td)


# ===== Test 9: EngineController evaluate =====


def test_engine_controller_evaluate():
    _section("Test 9: EngineController Evaluate (no side effects)")

    td = _make_dir()
    try:
        ctrl = EngineController(project_root=td, state_dir=td)

        simple = ctrl.evaluate("Fix a typo")
        _assert(not simple.should_start, "Simple task should NOT start engine", f"complexity={simple.complexity.value}")
        _assert(simple.complexity in (ComplexityClass.TRIVIAL, ComplexityClass.SIMPLE),
                f"Simple complexity: {simple.complexity.value}")

        complex_desc = (
            "Build a distributed microservice architecture with authentication, "
            "database migration, and deploy to production with CI/CD pipeline"
        )
        complex_r = ctrl.evaluate(complex_desc)
        _assert(complex_r.should_start, "Complex task SHOULD start engine",
                f"complexity={complex_r.complexity.value}, intent={complex_r.intent_type.value}")
        _assert(complex_r.estimated_tasks > 0, "Has estimated tasks")

        _assert(ctrl.state == EngineState.OFF, "Engine still OFF after evaluate")
    finally:
        _cleanup(td)


# ===== Test 10: EngineController ignite and advance (full lifecycle) =====


def test_engine_controller_ignite_advance():
    _section("Test 10: EngineController Ignite + Advance (Full Lifecycle)")

    td = _make_dir()
    try:
        skills = {
            "bmad-evo": SimBmadEvo(),
            "superpowers": SimSuperpowers(),
        }

        ctrl = EngineController(project_root=td, state_dir=td, skills=skills)

        result = ctrl.ignite(
            description="Build REST API with auth and database migration",
            auto_confirm=True,
        )

        _assert(result["status"] == "started", f"Engine started", f"got {result['status']}")
        _assert("pipeline_id" in result, "Has pipeline_id")
        _assert(ctrl.state == EngineState.RUNNING, "Engine is RUNNING", f"got {ctrl.state.value}")

        pid = result["pipeline_id"]

        init_result = ctrl.advance({"success": True})
        _assert(init_result.get("action") in ("call_skill", "analyze", "human_decision", "execute_next_task"),
                f"INIT advance got action: {init_result.get('action')}")

        analyze_result = ctrl.advance({
            "success": True,
            "artifacts": {
                "roles": [
                    {"type": "architect", "name": "Arch", "capabilities": ["design"]},
                    {"type": "developer", "name": "Dev", "capabilities": ["code"]},
                ],
                "tasks": [
                    {"name": "Design", "role": "architect", "description": "Design API", "priority": "P0", "depends_on": []},
                    {"name": "Build", "role": "developer", "description": "Build", "priority": "P1", "depends_on": ["Design"]},
                ],
            },
        })

        plan_result = ctrl.advance({
            "success": True,
            "artifacts": {
                "task_graph": {
                    "tasks": [
                        {"name": "Design", "role_id": "architect", "description": "Design API", "priority": "P0", "depends_on": []},
                        {"name": "Build", "role_id": "developer", "description": "Build", "priority": "P1", "depends_on": ["Design"]},
                    ],
                    "execution_waves": [["Design"], ["Build"]],
                },
            },
        })

        report = ctrl.get_report()
        _assert(report.pipeline_id == pid, f"Report has pipeline_id")
        _assert(report.runtime_seconds >= 0, "Runtime >= 0")
    finally:
        _cleanup(td)


# ===== Test 11: EngineController pause/resume =====


def test_engine_controller_pause_resume():
    _section("Test 11: EngineController Pause/Resume")

    td = _make_dir()
    try:
        skills = {"bmad-evo": SimBmadEvo(), "superpowers": SimSuperpowers()}
        ctrl = EngineController(project_root=td, state_dir=td, skills=skills)

        ctrl.ignite("Complex multi-service build with auth", auto_confirm=True)
        _assert(ctrl.state == EngineState.RUNNING, "Running before pause")

        pr = ctrl.pause(reason="lunch break")
        _assert(pr["status"] == "paused", "Paused")
        _assert(ctrl.state == EngineState.PAUSED, "State is PAUSED", f"got {ctrl.state.value}")

        blocked = ctrl.advance({"success": True})
        _assert(blocked.get("status") in ("error", "brake"), "Advance blocked when paused",
                f"got {blocked.get('status')}")

        rr = ctrl.resume()
        _assert(rr["status"] == "running", "Resumed")
        _assert(ctrl.state == EngineState.RUNNING, "State back to RUNNING")
    finally:
        _cleanup(td)


# ===== Test 12: EngineController stop =====


def test_engine_controller_stop():
    _section("Test 12: EngineController Stop")

    td = _make_dir()
    try:
        skills = {"bmad-evo": SimBmadEvo(), "superpowers": SimSuperpowers()}
        ctrl = EngineController(project_root=td, state_dir=td, skills=skills)

        ctrl.ignite("Build distributed system", auto_confirm=True)

        sr = ctrl.stop(reason="user requested stop")
        _assert(sr["status"] == "stopped", "Stopped")
        _assert(ctrl.state == EngineState.SHUTDOWN, "State is SHUTDOWN", f"got {ctrl.state.value}")

        report = ctrl.get_report()
        _assert(report.brake_level == BrakeLevel.STOP, "Report shows STOP brake")

        report_file = os.path.join(td, "engine_report.json")
        _assert(os.path.exists(report_file), "Report persisted")
    finally:
        _cleanup(td)


# ===== Test 13: EngineController handle (direct, no engine) =====


def test_engine_controller_handle_direct():
    _section("Test 13: EngineController Handle Direct (No Engine)")

    td = _make_dir()
    try:
        ctrl = EngineController(project_root=td, state_dir=td)

        result = ctrl.handle("Fix a typo in README")
        _assert(result["status"] == "direct", "Direct handling")
        _assert(result["action"] == "execute_directly", "Action is execute_directly")
        _assert(ctrl.state == EngineState.OFF, "Engine stays OFF")
    finally:
        _cleanup(td)


# ===== Test 14: CoolingSystem + BrakeSystem integration =====


def test_cooling_brake_integration():
    _section("Test 14: CoolingSystem + BrakeSystem Integration")

    td = _make_dir()
    try:
        cfg = CoolingConfig(
            tier1_token_threshold=20,
            tier2_token_threshold=40,
            tier3_token_threshold=60,
            shutdown_runtime_seconds=99999,
            snapshot_dir=os.path.join(td, "snaps"),
        )
        cs = CoolingSystem(config=cfg, state_dir=td)
        bs = BrakeSystem(state_dir=td)

        cs.start()

        brake_triggered = []
        bs.on_brake(BrakeLevel.STOP, lambda s: brake_triggered.append(True))

        for i in range(50):
            cs.register_prompt("a" * 20)
            level = cs.check_and_cool({})
            if cs.state.level in (CoolingLevel.LEVEL_2, CoolingLevel.LEVEL_3):
                bs.stop(reason="cooling shutdown")
                break

        _assert(cs.state.level in (CoolingLevel.LEVEL_2, CoolingLevel.LEVEL_3, CoolingLevel.SHUTDOWN),
                f"Cooling escalated: {cs.state.level.value}")
        _assert(cs.state.compression_count >= 1, f"At least 1 compression: {cs.state.compression_count}")
        _assert(bs.state.level == BrakeLevel.STOP, "Brake triggered by cooling")

        cs.stop()
    finally:
        _cleanup(td)


# ===== Test 15: EngineController with cooling auto-shutdown =====


def test_engine_controller_cooling_shutdown():
    _section("Test 15: EngineController Cooling Auto-Shutdown")

    td = _make_dir()
    try:
        from pipeline.cooling_system import CoolingConfig

        skills = {"bmad-evo": SimBmadEvo(), "superpowers": SimSuperpowers()}

        shutdown_cfg = CoolingConfig(
            tier1_token_threshold=999999,
            tier2_token_threshold=999999,
            tier3_token_threshold=999999,
            shutdown_runtime_seconds=1.0,
            snapshot_dir=os.path.join(td, "cooling_snaps"),
        )

        ctrl = EngineController(project_root=td, state_dir=td, skills=skills)
        ctrl.cooling.stop()
        ctrl.cooling.config = shutdown_cfg
        ctrl.cooling._snap_dir = Path(os.path.join(td, "cooling_snaps"))
        ctrl.cooling._snap_dir.mkdir(parents=True, exist_ok=True)
        ctrl.cooling.start()

        ctrl.ignite("Build complex system", auto_confirm=True)
        _assert(ctrl.state == EngineState.RUNNING, "Running before shutdown")

        time.sleep(1.2)
        result = ctrl.advance({"success": True})
        _assert(ctrl.state == EngineState.SHUTDOWN, f"Auto-shutdown triggered",
                f"state={ctrl.state.value}, result_status={result.get('status')}")

        report = ctrl.get_report()
        _assert(report.cooling_level == CoolingLevel.SHUTDOWN, "Report shows shutdown cooling")
    finally:
        _cleanup(td)


# ===== Test 16: Engine report persistence =====


def test_engine_report_persistence():
    _section("Test 16: Engine Report Persistence")

    td = _make_dir()
    try:
        skills = {"bmad-evo": SimBmadEvo(), "superpowers": SimSuperpowers()}
        ctrl = EngineController(project_root=td, state_dir=td, skills=skills)

        ctrl.ignite("Build something", auto_confirm=True)
        ctrl._shutdown("test_end")

        report_path = os.path.join(td, "engine_report.json")
        _assert(os.path.exists(report_path), "Report file exists")

        with open(report_path, "r") as f:
            data = json.load(f)
        _assert(data["state"] == "shutdown", "Report state is shutdown")
        _assert("runtime_seconds" in data, "Has runtime_seconds")
    finally:
        _cleanup(td)


# ===== Test 17: BrakeSystem signal through file then check via EngineController =====


def test_brake_signal_through_engine():
    _section("Test 17: External Brake Signal Through Engine")

    td = _make_dir()
    try:
        skills = {"bmad-evo": SimBmadEvo(), "superpowers": SimSuperpowers()}
        ctrl = EngineController(project_root=td, state_dir=td, skills=skills)

        ctrl.ignite("Build complex system", auto_confirm=True)

        signal = {"level": "pause", "reason": "external pause test"}
        signal_path = ctrl.brake._signal_file
        with open(signal_path, "w") as f:
            json.dump(signal, f)

        result = ctrl.advance({"success": True})
        _assert(result.get("status") == "brake", "Engine detected brake signal",
                f"got status={result.get('status')}")
        _assert(ctrl.state == EngineState.PAUSED, "Engine is PAUSED", f"got {ctrl.state.value}")
    finally:
        _cleanup(td)


# ===== Runner =====


def main():
    tests = [
        test_cooling_system_tiers,
        test_cooling_system_shutdown,
        test_brake_system_lifecycle,
        test_brake_system_external_signal,
        test_brake_system_callbacks,
        test_transmission_project_analysis,
        test_transmission_pipeline_input,
        test_transmission_skill_scaffold,
        test_engine_controller_evaluate,
        test_engine_controller_ignite_advance,
        test_engine_controller_pause_resume,
        test_engine_controller_stop,
        test_engine_controller_handle_direct,
        test_cooling_brake_integration,
        test_engine_controller_cooling_shutdown,
        test_engine_report_persistence,
        test_brake_signal_through_engine,
    ]

    passed = 0
    failed = 0
    errors = []

    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((t.__name__, str(e)))
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"{'=' * 60}")

    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err[:100]}")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
