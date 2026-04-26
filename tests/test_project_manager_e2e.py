"""
End-to-end tests for project-manage adapter + engine_hook commands.

Covers:
  1. Project init (new) with auto current
  2. Project init (local) with source analysis
  3. Project switch (auto pause/resume)
  4. Switch with engine running detection
  5. Health computation (5 dimensions, each <=20, total <=100)
  6. Document upsert + changelog + diff
  7. Document content + diff retrieval
  8. List and overview
  9. Archive and remove
  10. Project status detail with health
  11. CLI /map commands help text
  12. Auto pause/resume on project switch
  13. Unified init (new/clone/link -> init)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _load_adapter_class():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "project_manage_adapter",
        str(PROJECT_ROOT / ".skills" / "project-manage" / "adapter.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ProjectManage_Adapter


ProjectManage_Adapter = None

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
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _make_dir() -> str:
    return tempfile.mkdtemp(prefix="pm_e2e_")


def _cleanup(d: str):
    shutil.rmtree(d, ignore_errors=True)


def _get_adapter(state_dir):
    global ProjectManage_Adapter
    if ProjectManage_Adapter is None:
        ProjectManage_Adapter = _load_adapter_class()
    return ProjectManage_Adapter(state_dir=state_dir)


def _init_project(adapter, state_dir, name, source_path=""):
    ctx = {
        "action": "project_init",
        "mode": "new" if not source_path else "local",
        "name": name,
    }
    if source_path:
        ctx["target_path"] = source_path
    else:
        ctx["target_path"] = os.path.join(state_dir, "workspace", name)
        os.makedirs(ctx["target_path"], exist_ok=True)
    r = adapter.execute("", ctx)
    pid = r.get("artifacts", {}).get("project", {}).get("project_id", "")
    if pid:
        adapter.execute("", {"action": "current_switch", "project_id": pid})
    return r, pid


# ===== Test 1: Register project (new mode) =====


def test_register_new_project():
    _section("Test 1: Register New Project (init new)")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        r, pid = _init_project(adapter, td, "test_app")

        _assert(r["success"], "Init success")
        proj = r["artifacts"]["project"]
        _assert(proj["name"] == "test_app", "Name set")
        _assert(proj["status"] == "active", "Status active")
        _assert(proj["created_at"] is not None, "Created timestamp set")
        _assert(pid.startswith("proj_"), f"project_id format: {pid}")

        current = adapter.execute("", {"action": "current_get"})
        _assert(current["artifacts"]["project_id"] == pid, "Auto set as current")
    finally:
        _cleanup(td)


# ===== Test 2: Link project with source path =====


def test_link_project_with_source():
    _section("Test 2: Link Project with Source Path Analysis")

    source = _make_dir()
    td = _make_dir()
    try:
        pkg = {"dependencies": {"react": "^18"}}
        with open(os.path.join(source, "package.json"), "w") as f:
            json.dump(pkg, f)
        os.makedirs(os.path.join(source, "src"))
        with open(os.path.join(source, "src", "index.js"), "w") as f:
            f.write("console.log('hi')")

        adapter = _get_adapter(td)
        r, pid = _init_project(adapter, td, "frontend_app", source_path=source)

        _assert(r["success"], "Init success")
        proj = r["artifacts"]["project"]
        _assert(proj["target_path"] == source, "Source path set")
    finally:
        _cleanup(source)
        _cleanup(td)


# ===== Test 3: Switch project =====


def test_switch_project():
    _section("Test 3: Switch Project")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        r1, pid_a = _init_project(adapter, td, "proj_a")
        r2, pid_b = _init_project(adapter, td, "proj_b")

        current = adapter.execute("", {"action": "current_get"})
        _assert(current["artifacts"]["project_id"] == pid_b, "Current is proj_b (last registered)")

        result = adapter.execute("", {"action": "current_switch", "project_id": pid_a})
        _assert(result["success"], "Switched to proj_a")
        _assert(result["project_id"] == pid_a, "Result has project_id")

        current2 = adapter.execute("", {"action": "current_get"})
        _assert(current2["artifacts"]["project_id"] == pid_a, "Current updated to proj_a")
    finally:
        _cleanup(td)


# ===== Test 4: Switch with engine running detection =====


def test_switch_engine_running():
    _section("Test 4: Switch When Engine Running (Detection)")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        _, pid_a = _init_project(adapter, td, "proj_a")
        _, pid_b = _init_project(adapter, td, "proj_b")

        result = adapter.execute("", {
            "action": "current_switch",
            "project_id": pid_a,
            "engine_running": True,
        })
        _assert(result["success"], "Returns success")
        arts = result.get("artifacts", {})
        _assert(arts.get("options") is not None, "Has options for user choice")
        _assert(arts.get("current_project") == pid_b, "Reports current project")

        result2 = adapter.execute("", {
            "action": "current_switch",
            "project_id": pid_a,
            "engine_running": False,
        })
        _assert(result2["success"], "Switched when engine not running")
    finally:
        _cleanup(td)


# ===== Test 5: Health computation =====


def test_health_computation():
    _section("Test 5: Health Computation (5 Dimensions)")

    source = _make_dir()
    td = _make_dir()
    try:
        with open(os.path.join(source, "app.py"), "w") as f:
            f.write("from flask import Flask\napp = Flask(__name__)")
        with open(os.path.join(source, "requirements.txt"), "w") as f:
            f.write("flask\n")

        adapter = _get_adapter(td)
        r, pid = _init_project(adapter, td, "healthy_app", source_path=source)
        _assert(r["success"], "Init success")

        hr = adapter.execute("", {"action": "health_check", "project_id": pid})
        _assert(hr["success"], "health_check success")
        h = hr["artifacts"]

        _assert(h["total"] > 0, f"Health > 0: {h['total']}")
        _assert(h["buildability"] == 20, f"Buildability 20 (entry+deps): {h['buildability']}")
        _assert(h["activity"] == 20, f"Activity 20 (just created): {h['activity']}")
        _assert(h["total"] <= 100, f"Total <= 100: {h['total']}")

        _assert(h["doc_completeness"] <= 20, "Doc completeness <= 20")
        _assert(h["buildability"] <= 20, "Buildability <= 20")
        _assert(h["task_completion"] <= 20, "Task completion <= 20")
        _assert(h["constraint_adherence"] <= 20, "Constraint adherence <= 20")
        _assert(h["activity"] <= 20, "Activity <= 20")
    finally:
        _cleanup(source)
        _cleanup(td)


# ===== Test 6: Document update with changelog and diff =====


def test_doc_update_changelog():
    _section("Test 6: Document Update + Changelog + Diff")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        _, pid = _init_project(adapter, td, "doc_test")

        r1 = adapter.execute("", {
            "action": "doc_upsert",
            "project_id": pid,
            "category": "design_doc",
            "content": "# Design\n\n## Architecture\nModule A.\n",
            "trigger": "conversation",
            "trigger_reason": "Initial design",
        })
        _assert(r1["success"], "Doc v1 upserted")

        r2 = adapter.execute("", {
            "action": "doc_upsert",
            "project_id": pid,
            "category": "design_doc",
            "content": "# Design v2\n\n## Architecture\nModule A + B.\n## API\nREST API.\n",
            "trigger": "engine_run",
            "trigger_reason": "Added module B",
            "re_evaluated": True,
            "bmad_assessment": "Context need: 80K, tasks: 2",
        })
        _assert(r2["success"], "Doc v2 upserted")
        doc = r2["artifacts"]["document"]
        _assert(doc["version"] == 2, f"Version 2", f"got {doc['version']}")

        log_r = adapter.execute("", {"action": "doc_log", "project_id": pid, "category": "design_doc"})
        _assert(log_r["success"], "doc_log success")
        entries = log_r["artifacts"]["entries"]
        _assert(len(entries) == 2, f"2 changelog entries", f"got {len(entries)}")

        last = entries[-1]
        _assert(last["version_from"] == 1, "v1->v2")
        _assert(last["version_to"] == 2, "to v2")
        _assert(last["trigger"] == "engine_run", f"trigger is engine_run")
        _assert(last["re_evaluated"] is True, "re_evaluated flagged")
        _assert("80K" in last.get("bmad_assessment", ""), "bmad_assessment recorded")
        _assert(last["lines_added"] > 0, f"lines_added > 0: {last['lines_added']}")

        diff_r = adapter.execute("", {
            "action": "doc_diff",
            "project_id": pid,
            "category": "design_doc",
            "version_from": 1,
            "version_to": 2,
        })
        _assert(diff_r["success"], "doc_diff success")
        _assert("+" in diff_r["artifacts"]["diff"], "Diff has additions")
    finally:
        _cleanup(td)


# ===== Test 7: Document show and diff =====


def test_doc_show_and_diff():
    _section("Test 7: Document Show and Diff")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        _, pid = _init_project(adapter, td, "show_test")

        adapter.execute("", {
            "action": "doc_upsert", "project_id": pid,
            "category": "constraints", "content": "# Constraints v1\nTech constraints.\n",
        })
        adapter.execute("", {
            "action": "doc_upsert", "project_id": pid,
            "category": "constraints", "content": "# Constraints v2\nTech + perf constraints.\n## Performance\n< 200ms\n",
        })

        content_r = adapter.execute("", {
            "action": "doc_content", "project_id": pid, "category": "constraints",
        })
        _assert(content_r["success"], "doc_content success")
        _assert("v2" in content_r["artifacts"]["content"], "Shows latest version")

        all_r = adapter.execute("", {"action": "doc_content", "project_id": pid})
        _assert(all_r["success"], "doc_content (all) success")

        diff_r = adapter.execute("", {
            "action": "doc_diff", "project_id": pid,
            "category": "constraints", "version_from": 1, "version_to": 2,
        })
        _assert(diff_r["success"], "diff exists")
        _assert("+" in diff_r["artifacts"]["diff"], "Diff has additions")

        no_diff = adapter.execute("", {
            "action": "doc_diff", "project_id": pid,
            "category": "constraints", "version_from": 99, "version_to": 100,
        })
        _assert(not no_diff["success"], "Missing diff returns error")
        _assert("No diff found" in no_diff.get("error", ""), "Has error message")
    finally:
        _cleanup(td)


# ===== Test 8: List and overview =====


def test_list_and_overview():
    _section("Test 8: List and Overview")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        _init_project(adapter, td, "alpha")
        _init_project(adapter, td, "beta")
        _, pid_g = _init_project(adapter, td, "gamma")

        lr = adapter.execute("", {"action": "project_list", "status": "all"})
        _assert(lr["success"], "project_list success")
        _assert(lr["artifacts"]["total"] == 3, f"3 projects", f"got {lr['artifacts']['total']}")

        orr = adapter.execute("", {"action": "overview"})
        _assert(orr["success"], "overview success")
        projs = orr["artifacts"]["projects"]
        _assert(len(projs) == 3, "Overview has 3 entries")
        current_count = sum(1 for p in projs if p["is_current"])
        _assert(current_count == 1, "Exactly 1 current project")

        names = {p["name"] for p in projs}
        _assert(names == {"alpha", "beta", "gamma"}, "All names present")
    finally:
        _cleanup(td)


# ===== Test 9: Archive and remove =====


def test_archive_and_remove():
    _section("Test 9: Archive and Remove")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        _, pid_a = _init_project(adapter, td, "to_archive")
        _, pid_r = _init_project(adapter, td, "to_remove")

        ar = adapter.execute("", {"action": "project_archive", "project_id": pid_a})
        _assert(ar["success"], "Archived")
        check = adapter.execute("", {"action": "project_get", "project_id": pid_a})
        _assert(check["artifacts"]["status"] == "archived", "Status is archived")

        dr = adapter.execute("", {"action": "project_delete", "project_id": pid_r, "keep_files": False})
        _assert(dr["success"], "Removed")
        check2 = adapter.execute("", {"action": "project_get", "project_id": pid_r})
        _assert(not check2["success"], "No longer exists")
    finally:
        _cleanup(td)


# ===== Test 10: Project status detail =====


def test_project_status_detail():
    _section("Test 10: Project Status Detail")

    source = _make_dir()
    td = _make_dir()
    try:
        with open(os.path.join(source, "main.py"), "w") as f:
            f.write("print('hello')")
        with open(os.path.join(source, "requirements.txt"), "w") as f:
            f.write("flask\n")

        adapter = _get_adapter(td)
        r, pid = _init_project(adapter, td, "detail_test", source_path=source)

        pr = adapter.execute("", {"action": "project_get", "project_id": pid})
        _assert(pr["success"], "project_get success")
        _assert(pr["artifacts"]["name"] == "detail_test", "Name correct")

        hr = adapter.execute("", {"action": "health_check", "project_id": pid})
        _assert(hr["artifacts"]["total"] > 0, "Health > 0")

        sr = adapter.execute("", {"action": "project_status", "project_id": pid})
        _assert(sr["success"], "project_status success")

        cr = adapter.execute("", {"action": "current_get"})
        _assert(cr["artifacts"]["project_id"] == pid, "Is current project")
    finally:
        _cleanup(source)
        _cleanup(td)


# ===== Test 11: CLI /map commands =====


def test_cli_project_commands():
    _section("Test 11: CLI /map Commands")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{PROJECT_ROOT / 'src'};{PROJECT_ROOT / 'specs'}"

    r = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "engine_hook.py"), "/?"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=env,
    )
    _assert("Engine Commands" in r.stdout, "/map /? shows engine help")
    _assert("Project Commands" in r.stdout, "/map /? shows project help")
    _assert("/map p init" in r.stdout, "Help mentions unified init")
    _assert("/map p <name>" in r.stdout, "Help mentions shorthand switch")

    r = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "engine_hook.py"), "p", "/?"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=env,
    )
    _assert("Project Command Details" in r.stdout, "/map p /? shows project help")
    _assert("Auto-pauses" in r.stdout, "Help mentions auto-pause")
    _assert("auto-resumes" in r.stdout.lower(), "Help mentions auto-resume")

    r = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "engine_hook.py"), "doc", "/?"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT), env=env,
    )
    _assert("Document Command Details" in r.stdout, "/map doc /? shows doc help")


# ===== Test 12: Auto pause/resume on switch =====


def test_auto_pause_resume():
    _section("Test 12: Auto Pause/Resume on Switch")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)
        src1 = os.path.join(td, "src_a")
        os.makedirs(src1, exist_ok=True)
        with open(os.path.join(src1, "app.py"), 'w') as f:
            f.write('print("a")')
        src2 = os.path.join(td, "src_b")
        os.makedirs(src2, exist_ok=True)
        with open(os.path.join(src2, "main.py"), 'w') as f:
            f.write('print("b")')

        r1, pid_a = _init_project(adapter, td, "proj_a", source_path=src1)
        r2, pid_b = _init_project(adapter, td, "proj_b", source_path=src2)

        adapter.execute("", {"action": "current_switch", "project_id": pid_a})
        check_a = adapter.execute("", {"action": "project_get", "project_id": pid_a})
        _assert(check_a["artifacts"]["status"] == "active", "proj_a is active")
        check_b_before = adapter.execute("", {"action": "project_get", "project_id": pid_b})
        _assert(check_b_before["artifacts"]["status"] == "paused", "proj_b is paused (was auto-paused when switched away)")

        sw = adapter.execute("", {"action": "current_switch", "project_id": pid_b})
        _assert(sw["success"], "Switch to proj_b success")
        _assert(sw.get("auto_paused") == pid_a, f"auto-paused proj_a")
        _assert(sw.get("auto_resumed") == pid_b, "auto-resumed proj_b (was paused)")

        check_a2 = adapter.execute("", {"action": "project_get", "project_id": pid_a})
        _assert(check_a2["artifacts"]["status"] == "paused", "proj_a is now paused")
        check_b = adapter.execute("", {"action": "project_get", "project_id": pid_b})
        _assert(check_b["artifacts"]["status"] == "active", "proj_b is active")

        sw2 = adapter.execute("", {"action": "current_switch", "project_id": pid_a})
        _assert(sw2.get("auto_paused") == pid_b, "auto-paused proj_b")
        _assert(sw2.get("auto_resumed") == pid_a, "auto-resumed proj_a (was paused)")

        check_a3 = adapter.execute("", {"action": "project_get", "project_id": pid_a})
        _assert(check_a3["artifacts"]["status"] == "active", "proj_a is active again")
        check_b2 = adapter.execute("", {"action": "project_get", "project_id": pid_b})
        _assert(check_b2["artifacts"]["status"] == "paused", "proj_b is now paused")
    finally:
        _cleanup(td)


# ===== Test 13: Unified init (new/local modes) =====


def test_unified_init():
    _section("Test 13: Unified Init (new + local)")

    td = _make_dir()
    try:
        adapter = _get_adapter(td)

        new_path = os.path.join(td, "workspace", "fresh_proj")
        r1 = adapter.execute("", {
            "action": "project_init", "mode": "new",
            "name": "fresh_proj", "target_path": new_path,
        })
        _assert(r1["success"], "init new success")
        _assert(r1["artifacts"]["project"]["init_mode"] == "new", "init_mode is new")

        local_path = os.path.join(td, "local_proj")
        os.makedirs(local_path, exist_ok=True)
        with open(os.path.join(local_path, "manage.py"), "w") as f:
            f.write("from django.core.management import execute_from_command_line")
        r2 = adapter.execute("", {
            "action": "project_init", "mode": "local",
            "name": "local_proj", "target_path": local_path,
        })
        _assert(r2["success"], "init local success")
        _assert(r2["artifacts"]["project"]["init_mode"] == "local", "init_mode is local")
    finally:
        _cleanup(td)


# ===== Runner =====


def main():
    tests = [
        test_register_new_project,
        test_link_project_with_source,
        test_switch_project,
        test_switch_engine_running,
        test_health_computation,
        test_doc_update_changelog,
        test_doc_show_and_diff,
        test_list_and_overview,
        test_archive_and_remove,
        test_project_status_detail,
        test_cli_project_commands,
        test_auto_pause_resume,
        test_unified_init,
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

    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed} passed, {failed} failed, {len(tests)} total")
    print(f"{'='*60}")

    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print(f"  - {name}: {err[:100]}")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
