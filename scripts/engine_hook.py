"""
Engine Hook - /map command entry point for the MAP engine control system.

Usage:
    /map <task description>                    Start engine for a task
    /map status                                Show engine + project status
    /map pause [--reason X]                    Pause engine
    /map resume                                Resume engine
    /map stop [--reason X]                     Stop engine
    /map report                                Show last engine run report
    /map overview                              Show all projects dashboard

    /map p new <name> [--path <dir>]           Create project + auto doc init
    /map p clone <name> <git-url>              Clone from GitHub + auto doc init
    /map p link <name> <path>                  Link existing project + auto doc init
    /map p switch <name>                       Switch active project
    /map p list                                List all projects with health
    /map p status [name]                       Show project detail + doc versions
    /map p archive <name>                      Archive project
    /map p remove <name>                       Remove project registration
    /map p assess [name]                       Consolidated assessment (gates+drift+risk+contamination)
    /map p deliver [name]                      Deliver (auto-detect local/github)
    /map p health [name]                       Show health score breakdown

    /map doc show [type]                       Show document content
    /map doc log [type]                        Show document change history
    /map doc diff <type> <v1> <v2>             Compare two document versions

    /map /?                                    Show this help
    /map p /?                                  Show project command help
    /map doc /?                                Show doc command help
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "specs"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("engine_hook")

STATE_FILE = PROJECT_ROOT / ".pipeline" / "engine_hook_state.json"


HELP_MAIN = """
MAP Engine Control - Command Reference
========================================

Engine Commands:
  /map <task description>       Start engine with task (simple tasks auto-skip)
  /map status                   Show engine state + current project
  /map pause [--reason X]       Pause engine (saves context)
  /map resume                   Resume from pause (loads context)
  /map stop [--reason X]        Stop engine gracefully
  /map report                   Show last completed engine run report
  /map overview                 Show all projects dashboard

Project Commands (/map p):
  /map p init <name> [--path D] [--url <git>]
                                Create project (auto-detect: new/clone/link)
  /map p <name>                 Switch to project (auto pause old / resume new)
  /map p list                   List all projects with health scores
  /map p status [name]          Show project detail + health + doc versions
  /map p archive <name>         Archive (freeze) project
  /map p remove <name>          Remove project registration
  /map p assess [name]          Consolidated assessment (gates+drift+risk+contamination)
  /map p deliver [name]         Deliver (auto-detect local/github by init mode)

Document Commands (/map doc):
  /map doc show [type]          Show document content
  /map doc log [type]           Show document change history
  /map doc diff <type> <v1> <v2> Compare two document versions

Help:
  /map /?                       Show this help
  /map p /?                     Show project command details
  /map doc /?                   Show document command details
"""

HELP_PROJECT = """
Project Command Details (/map p)
=================================

/map p init <name> [--path <dir>] [--url <git-url>]
  Create and register a project. Auto-detects mode:
    No --path, no --url    -> new:  create fresh project scaffold
    --path <local-dir>     -> link: scan existing local directory
    --url <git-url>        -> clone: clone from GitHub
  Auto-generates documents. Sets as current active project.

/map p <name>
  Switch current active project.
  Auto-pauses the old project, auto-resumes the target.
  If engine is running, prompts for:
    [A] Pause engine and switch
    [B] Wait for task completion
    [C] Keep engine running (risk)

/map p list
  Show all projects:
    name | status | health | stack | backlog | last active

/map p status [name]
  Default: current project.
  Shows: info, health (5 dimensions), doc versions, progress.

/map p archive <name>
  Mark project as archived. Removes from active list.

/map p remove <name>
  Delete project registration and stored data.
  Does NOT delete actual source code.

/map p assess [name]
  Consolidated assessment: health + gates + drift + risk + contamination.

/map p deliver [name]
  Auto-detect delivery mode based on init mode:
    new/link -> local delivery
    clone    -> github delivery
"""

HELP_DOC = """
Document Command Details (/map doc)
====================================

/map doc show [type]
  Display document content.
  Types: design_doc, work_breakdown, progress_report,
         timeline_plan, constraints, acceptance_criteria, test_manual
  Default: shows all documents.
  Documents are auto-maintained during conversation.

/map doc log [type]
  Show change history for documents.
  Each entry shows: timestamp, trigger, version change,
  lines added/removed, summary.

/map doc diff <type> <v1> <v2>
  Show unified diff between two versions.
  Example: /map doc diff design_doc 3 4

Note: Documents are updated automatically by the engine
during conversation. No manual update command needed.
"""

CONTROL_COMMANDS = {
    "pause", "resume", "stop", "abort", "status", "report",
    "signal", "respond", "advance", "evaluate", "overview",
    "p", "doc",
}


def _get_controller():
    from pipeline.engine_controller import EngineController
    return EngineController(
        project_root=str(PROJECT_ROOT),
        state_dir=str(PROJECT_ROOT / ".pipeline"),
    )


_ADAPTER_CACHE = None
_ADAPTER_MOD = None


def _get_adapter():
    global _ADAPTER_CACHE, _ADAPTER_MOD
    if _ADAPTER_CACHE is not None:
        return _ADAPTER_CACHE
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "project_manage_adapter",
        str(PROJECT_ROOT / ".skills" / "project-manage" / "adapter.py"),
    )
    _ADAPTER_MOD = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_ADAPTER_MOD)
    _ADAPTER_CACHE = _ADAPTER_MOD.ProjectManage_Adapter(
        state_dir=str(PROJECT_ROOT / ".pipeline")
    )
    return _ADAPTER_CACHE


def _load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(state: Dict[str, Any]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _print_result(result: Dict[str, Any]):
    success = result.get("success", result.get("status", "unknown"))
    action = result.get("action", "")

    print(f"\n{'='*60}")
    print(f"  Success: {success}")
    if action:
        print(f"  Action: {action}")

    artifacts = result.get("artifacts", {})
    if artifacts:
        for k, v in artifacts.items():
            if k in ("documents",) and isinstance(v, dict):
                for dk, dv in v.items():
                    print(f"  {dk}: {len(dv) if isinstance(dv, list) else dv}")
            elif isinstance(v, (str, int, float, bool)):
                print(f"  {k}: {v}")

    error = result.get("error")
    if error:
        print(f"  Error: {error}")

    print(f"{'='*60}\n")


# ===== Engine Commands =====


def cmd_engine_start(args):
    ctrl = _get_controller()
    description = args.description
    design_docs = []
    backlog_items = []

    adapter = _get_adapter()
    current_result = adapter.execute("", {"action": "current_get"})
    current = None
    if current_result.get("success"):
        current = current_result.get("artifacts", {}).get("project_id")

    if current:
        project_result = adapter.execute("", {"action": "project_get", "project_id": current})
        if project_result.get("success"):
            proj = project_result["artifacts"]
            source = proj.get("target_path", "")
            if source and os.path.isdir(source):
                ctrl.project_root = source
                ctrl.transmission = __import__(
                    "pipeline.transmission", fromlist=["TransmissionBridge"]
                ).TransmissionBridge(project_root=source)
                ctrl.intent_gate = __import__(
                    "pipeline.intent_gate", fromlist=["IntentGate"]
                ).IntentGate(project_path=source)

        docs_result = adapter.execute("", {"action": "doc_content", "project_id": current})
        if docs_result.get("success"):
            content = docs_result.get("artifacts", {}).get("content", "")
            if content:
                design_docs.append(content)

    result = ctrl.ignite(
        description=description,
        design_docs=design_docs,
        backlog_items=backlog_items,
    )

    state = _load_state()
    if result.get("status") == "started":
        state["pipeline_id"] = result.get("pipeline_id", "")
        state["started_at"] = time.time()
        state["status"] = "running"
    _save_state(state)
    _print_result(result)
    return result


def cmd_status(args):
    ctrl = _get_controller()
    report = ctrl.get_report()
    adapter = _get_adapter()
    current_result = adapter.execute("", {"action": "current_get"})
    current = current_result.get("artifacts", {}).get("project_id") if current_result.get("success") else None

    print(f"\n{'='*60}")
    print(f"  Engine State: {ctrl.state.value}")
    print(f"  Pipeline ID: {report.pipeline_id or 'none'}")
    if report.started_at:
        print(f"  Started: {report.started_at.isoformat()}")
        print(f"  Runtime: {report.runtime_seconds:.1f}s ({report.runtime_seconds/3600:.2f}h)")
    print(f"  Tasks: {report.tasks_completed}/{report.tasks_total}")
    print(f"  Cooling: {report.cooling_level.value}")
    print(f"  Brake: {report.brake_level.value}")
    print(f"  Current Project: {current or 'none'}")

    if current:
        health_result = adapter.execute("", {"action": "health_check", "project_id": current})
        if health_result.get("success"):
            h = health_result["artifacts"]
            print(f"  Project Health: {h['total']}/100")
            print(f"    Docs: {h['doc_completeness']}/20  Build: {h['buildability']}/20  "
                  f"Tasks: {h['task_completion']}/20  Constraints: {h['constraint_adherence']}/20  "
                  f"Activity: {h['activity']}/20")

    if report.shutdown_report:
        print(f"  Shutdown report: {report.shutdown_report}")
    print(f"{'='*60}\n")


def cmd_pause(args):
    ctrl = _get_controller()
    result = ctrl.pause(reason=getattr(args, "reason", "") or "")
    _print_result(result)
    _save_state({**_load_state(), "status": "paused"})
    return result


def cmd_resume(args):
    ctrl = _get_controller()
    result = ctrl.resume()
    _print_result(result)
    _save_state({**_load_state(), "status": "running"})
    return result


def cmd_stop(args):
    ctrl = _get_controller()
    result = ctrl.stop(reason=getattr(args, "reason", "") or "")
    _print_result(result)
    _save_state({**_load_state(), "status": "stopped"})
    return result


def cmd_report(args):
    report_path = PROJECT_ROOT / ".pipeline" / "engine_report.json"
    if report_path.exists():
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("No engine report found.")


def cmd_overview(args):
    adapter = _get_adapter()
    result = adapter.execute("", {"action": "overview"})
    if not result.get("success"):
        print(f"Error: {result.get('error', 'unknown')}")
        return

    projects = result.get("artifacts", {}).get("projects", [])
    print(f"\n{'='*75}")
    print(f"  {'Name':<22} {'Current':<8} {'Health':<8} {'Backlog':<10} {'Stack':<15} {'Status':<10}")
    print(f"  {'-'*22} {'-'*8} {'-'*8} {'-'*10} {'-'*15} {'-'*10}")
    for p in projects:
        cur = "*" if p["is_current"] else ""
        health_total = p["health"]["total"] if isinstance(p.get("health"), dict) else p.get("health", 0)
        stack = ",".join(p.get("stack", [])[:2])
        print(f"  {p['name']:<22} {cur:<8} {health_total:<8} {p['backlog']:<10} "
              f"{stack:<15} {p.get('status', ''):<10}")
    if not projects:
        print("  (no projects registered)")
    print(f"{'='*75}\n")


# ===== Project Commands =====


def _resolve_project_id(adapter, name_or_id: str) -> str:
    result = adapter.execute("", {"action": "project_get", "project_id": name_or_id})
    if result.get("success"):
        return name_or_id
    from project_manage.registry import ProjectRegistry
    registry = ProjectRegistry(state_dir=str(PROJECT_ROOT / ".pipeline"))
    found = registry.find_by_name(name_or_id)
    return found or name_or_id


def cmd_project(args):
    rest = getattr(args, "rest", [])
    if not rest or rest[0] in ("/?", "help", "?"):
        print(HELP_PROJECT)
        return

    sub = rest[0]
    adapter = _get_adapter()

    KNOWN_SUBCOMMANDS = {"init", "list", "status", "archive", "remove", "assess", "deliver"}

    if sub == "init":
        if len(rest) < 2:
            print("Usage: /map p init <name> [--path <dir>] [--url <git-url>]")
            return
        name = rest[1]
        path = ""
        url = ""
        if "--path" in rest:
            idx = rest.index("--path")
            if idx + 1 < len(rest):
                path = rest[idx + 1]
        if "--url" in rest:
            idx = rest.index("--url")
            if idx + 1 < len(rest):
                url = rest[idx + 1]

        if url:
            mode = "clone"
            target_path = str(PROJECT_ROOT / "_clones" / name)
            init_ctx = {"action": "project_init", "mode": "clone", "name": name, "repo_url": url, "target_path": target_path}
        elif path:
            if not os.path.isdir(path):
                print(f"Directory not found: {path}")
                return
            mode = "local"
            init_ctx = {"action": "project_init", "mode": "local", "name": name, "target_path": os.path.abspath(path)}
        else:
            mode = "new"
            init_ctx = {"action": "project_init", "mode": "new", "name": name, "target_path": str(PROJECT_ROOT / "workspace" / name)}

        result = adapter.execute("", init_ctx)
        if not result.get("success"):
            print(f"Error: {result.get('error', 'unknown')}")
            return
        pid = result.get("artifacts", {}).get("project", {}).get("project_id", "")
        if pid:
            adapter.execute("", {"action": "current_switch", "project_id": pid})
        proj = result.get("artifacts", {}).get("project", {})
        print(f"\n{'='*60}")
        print(f"  Created: {proj.get('name', name)} ({pid})")
        print(f"  Mode: {mode}")
        print(f"  Source: {init_ctx.get('target_path', '')}")
        print(f"  Stack: {', '.join(proj.get('tech_stack', {}).keys()) or 'unknown'}")
        print(f"  Status: current project set")
        print(f"{'='*60}\n")

    elif sub == "list":
        result = adapter.execute("", {"action": "project_list", "status": "all"})
        if not result.get("success"):
            print(f"Error: {result.get('error', 'unknown')}")
            return
        projects = result.get("artifacts", {}).get("projects", [])
        current_result = adapter.execute("", {"action": "current_get"})
        current = current_result.get("artifacts", {}).get("project_id") if current_result.get("success") else None

        health_map = {}
        for p in projects:
            pid = p.get("project_id", "")
            hr = adapter.execute("", {"action": "health_check", "project_id": pid})
            health_map[pid] = hr.get("artifacts", {}).get("total", 0) if hr.get("success") else 0

        print(f"\n{'='*80}")
        print(f"  {'Name':<22} {'Status':<10} {'Current':<8} {'Health':<8} {'Stack':<20} {'Last Active'}")
        print(f"  {'-'*22} {'-'*10} {'-'*8} {'-'*8} {'-'*20} {'-'*19}")
        for p in projects:
            pid = p.get("project_id", "")
            cur = " *" if pid == current else ""
            stack = ",".join(list(p.get("tech_stack", {}).keys())[:2])
            updated = p.get("updated_at", "")[:19]
            h = health_map.get(pid, 0)
            print(f"  {p.get('name', '') + cur:<22} {p.get('status', ''):<10} "
                  f"{'*' if pid == current else '':<8} {h:<8} {stack:<20} {updated}")
        print(f"{'='*80}\n")

    elif sub == "status":
        name = rest[1] if len(rest) > 1 else None
        if name:
            pid = _resolve_project_id(adapter, name)
        else:
            current_result = adapter.execute("", {"action": "current_get"})
            pid = current_result.get("artifacts", {}).get("project_id") if current_result.get("success") else None
        if not pid:
            print("No current project. Use /map p init <name> first.")
            return
        proj_result = adapter.execute("", {"action": "project_get", "project_id": pid})
        health_result = adapter.execute("", {"action": "health_check", "project_id": pid})
        status_result = adapter.execute("", {"action": "project_status", "project_id": pid})
        if not proj_result.get("success"):
            print(f"Error: {proj_result.get('error', 'unknown')}")
            return
        proj = proj_result["artifacts"]
        h = health_result.get("artifacts", {})
        st = status_result.get("artifacts", {})
        current_result = adapter.execute("", {"action": "current_get"})
        is_current = current_result.get("artifacts", {}).get("project_id") == pid
        print(f"\n{'='*60}")
        print(f"  Project: {proj.get('name', '')} ({pid})")
        print(f"  Status: {proj.get('status', '')}")
        print(f"  Source: {proj.get('target_path', '') or 'not set'}")
        print(f"  Stack: {', '.join(proj.get('tech_stack', {}).keys()) or 'unknown'}")
        print(f"  Init Mode: {proj.get('init_mode', 'unknown')}")
        print(f"  Current: {'yes' if is_current else 'no'}")
        print(f"")
        print(f"  Health: {h.get('total', 0)}/100")
        print(f"    Docs: {h.get('doc_completeness', 0)}/20  Build: {h.get('buildability', 0)}/20  "
              f"Tasks: {h.get('task_completion', 0)}/20  Constraints: {h.get('constraint_adherence', 0)}/20  "
              f"Activity: {h.get('activity', 0)}/20")
        print(f"")
        print(f"  Progress: {st.get('progress_pct', 0)}%")
        print(f"  Active Docs: {st.get('active_versions', {})}")
        print(f"{'='*60}\n")

    elif sub == "archive":
        if len(rest) < 2:
            print("Usage: /map p archive <name>")
            return
        pid = _resolve_project_id(adapter, rest[1])
        result = adapter.execute("", {"action": "project_archive", "project_id": pid})
        _print_result(result)

    elif sub == "remove":
        if len(rest) < 2:
            print("Usage: /map p remove <name>")
            return
        pid = _resolve_project_id(adapter, rest[1])
        result = adapter.execute("", {"action": "project_delete", "project_id": pid, "keep_files": False})
        _print_result(result)

    elif sub == "assess":
        name = rest[1] if len(rest) > 1 else None
        if name:
            pid = _resolve_project_id(adapter, name)
        else:
            current_result = adapter.execute("", {"action": "current_get"})
            pid = current_result.get("artifacts", {}).get("project_id") if current_result.get("success") else None
        if not pid:
            print("No current project. Use /map p init <name> first.")
            return
        print("Running consolidated assessment...")
        result = adapter.execute("", {"action": "assess_all", "project_id": pid})
        if not result.get("success"):
            print(f"Error: {result.get('error', 'unknown')}")
            return
        arts = result.get("artifacts", {})
        print(f"\n{'='*60}")
        print(f"  Consolidated Assessment: {pid}")
        h = arts.get("health", {})
        print(f"  Health: {h.get('total', 0)}/100")
        print(f"    Docs: {h.get('doc_completeness', 0)}/20  Build: {h.get('buildability', 0)}/20  "
              f"Tasks: {h.get('task_completion', 0)}/20  Constraints: {h.get('constraint_adherence', 0)}/20  "
              f"Activity: {h.get('activity', 0)}/20")
        gates = arts.get("gates", {})
        print(f"  Gates: {gates.get('artifacts', {}).get('decision', 'N/A')}")
        drift = arts.get("drift", {})
        print(f"  Drift: {drift.get('artifacts', {}).get('severity', 'N/A')}")
        risk = arts.get("risk", {})
        print(f"  Risk: {risk.get('artifacts', {}).get('risk_score', 'N/A')}")
        contam = arts.get("contamination", {})
        print(f"  Contamination: {contam.get('artifacts', {}).get('contamination_score', 'N/A')}")
        print(f"{'='*60}\n")

    elif sub == "deliver":
        name = rest[1] if len(rest) > 1 else None
        if name:
            pid = _resolve_project_id(adapter, name)
        else:
            current_result = adapter.execute("", {"action": "current_get"})
            pid = current_result.get("artifacts", {}).get("project_id") if current_result.get("success") else None
        if not pid:
            print("No current project. Use /map p init <name> first.")
            return
        result = adapter.execute("", {"action": "deliver", "project_id": pid})
        _print_result(result)

    elif sub not in KNOWN_SUBCOMMANDS:
        pid = _resolve_project_id(adapter, sub)
        found = adapter.execute("", {"action": "project_get", "project_id": pid})
        if not found.get("success"):
            print(f"Unknown command or project: {sub}")
            print(HELP_PROJECT)
            return
        engine_state_path = PROJECT_ROOT / ".pipeline" / "engine_state.json"
        engine_running = False
        if engine_state_path.exists():
            try:
                with open(engine_state_path, "r", encoding="utf-8") as f:
                    es = json.load(f)
                engine_running = es.get("state") == "running"
            except Exception:
                pass
        result = adapter.execute("", {
            "action": "current_switch",
            "project_id": pid,
            "engine_running": engine_running,
        })
        artifacts = result.get("artifacts", {})
        if artifacts.get("options"):
            print(f"\n{'='*60}")
            print(f"  Engine is running for '{artifacts.get('current_project', '')}'")
            print(f"  Choose:")
            for opt in artifacts["options"]:
                print(f"    {opt}")
            print(f"{'='*60}\n")
        else:
            if artifacts.get("auto_paused"):
                print(f"  Auto-paused: {artifacts['auto_paused']}")
            if artifacts.get("auto_resumed"):
                print(f"  Auto-resumed: {artifacts['auto_resumed']}")
            _print_result(result)

    else:
        print(f"Unknown project command: {sub}")
        print(HELP_PROJECT)


# ===== Document Commands =====


def cmd_doc(args):
    rest = getattr(args, "rest", [])
    if not rest or rest[0] in ("/?", "help", "?"):
        print(HELP_DOC)
        return

    sub = rest[0]
    adapter = _get_adapter()
    current_result = adapter.execute("", {"action": "current_get"})
    current = current_result.get("artifacts", {}).get("project_id") if current_result.get("success") else None
    if not current:
        print("No current project. Use /map p link <name> <path> first.")
        return

    if sub == "show":
        category = rest[1] if len(rest) > 1 else None
        result = adapter.execute("", {"action": "doc_content", "project_id": current, "category": category})
        if result.get("success"):
            print(result["artifacts"]["content"])
        else:
            print(f"Error: {result.get('error', 'unknown')}")

    elif sub == "log":
        category = rest[1] if len(rest) > 1 else None
        result = adapter.execute("", {"action": "doc_log", "project_id": current, "category": category})
        if not result.get("success"):
            print(f"Error: {result.get('error', 'unknown')}")
            return
        entries = result.get("artifacts", {}).get("entries", [])
        if not entries:
            print("No document changes recorded.")
            return
        print(f"\n{'='*70}")
        for e in entries[-20:]:
            print(f"  [{e.get('timestamp', '')[:19]}] {e.get('file', '')} "
                  f"v{e.get('version_from', '?')} -> v{e.get('version_to', '?')}")
            print(f"    Trigger: {e.get('trigger', '')} | {e.get('trigger_reason', '')}")
            print(f"    Summary: {e.get('summary', '')}")
            if e.get("lines_added") or e.get("lines_removed"):
                print(f"    Lines: +{e.get('lines_added', 0)} -{e.get('lines_removed', 0)}")
            if e.get("re_evaluated"):
                print(f"    BMAD Re-evaluated: {e.get('bmad_assessment', '')}")
            print()
        print(f"{'='*70}\n")

    elif sub == "diff":
        if len(rest) < 4:
            print("Usage: /map doc diff <type> <v1> <v2>")
            print("Example: /map doc diff design_doc 3 4")
            return
        category, v1, v2 = rest[1], int(rest[2]), int(rest[3])
        result = adapter.execute("", {
            "action": "doc_diff",
            "project_id": current,
            "category": category,
            "version_from": v1,
            "version_to": v2,
        })
        if result.get("success"):
            print(result["artifacts"]["diff"])
        else:
            print(result.get("error", "Unknown error"))

    else:
        print(f"Unknown doc command: {sub}")
        print(HELP_DOC)


# ===== Main Router =====


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="MAP Engine Control",
        add_help=False,
    )
    parser.add_argument("command", nargs="?", default=None)
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--reason", default="")
    parser.add_argument("--response", default="")
    parser.add_argument("--response-file", default="")
    parser.add_argument("--result-file", default="")
    parser.add_argument("--result-json", default="")
    parser.add_argument("--desc", default="")
    parser.add_argument("--design", nargs="*")
    parser.add_argument("--backlog", nargs="*")
    parser.add_argument("--auto-confirm", action="store_true")
    parser.add_argument("--level", default="")
    parser.add_argument("--force", default="")
    parser.add_argument("--path", default="")

    args = parser.parse_args()

    if not args.command:
        print(HELP_MAIN)
        return

    cmd = args.command
    if isinstance(cmd, str):
        cmd = cmd.replace("？", "?")

    if isinstance(args.rest, list):
        args.rest = [
            r.replace("？", "?") if isinstance(r, str) else r for r in args.rest
        ]

    if cmd in ("/?", "help", "?"):
        print(HELP_MAIN)
        return

    if cmd == "p":
        cmd_project(args)
        return

    if cmd == "doc":
        cmd_doc(args)
        return

    if cmd == "status":
        cmd_status(args)
        return

    if cmd == "pause":
        cmd_pause(args)
        return

    if cmd == "resume":
        cmd_resume(args)
        return

    if cmd == "stop":
        cmd_stop(args)
        return

    if cmd == "report":
        cmd_report(args)
        return

    if cmd == "overview":
        cmd_overview(args)
        return

    if cmd in ("respond", "advance", "evaluate", "signal", "abort"):
        print(f"(/{cmd} is for advanced/scripted use)")
        return

    description = " ".join([cmd] + (args.rest or []))
    if not description.strip():
        print(HELP_MAIN)
        return

    args.description = description
    cmd_engine_start(args)


if __name__ == "__main__":
    main()
