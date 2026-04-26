"""Auto-drive PipelineRunner session for long-running stability tests.

Modes:
- synthetic: auto-generates templated model responses (fast orchestration checks)
- real_ipc: only advances when real IPC model responses are available
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, "specs")
sys.path.insert(0, ".skills/bmad-evo")

from pipeline.runner import PipelineRunner


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = PROJECT_ROOT / ".pipeline_session" / "runner_session.json"
LOG_FILE = PROJECT_ROOT / ".pipeline_session" / "auto_drive_log.jsonl"


def _analysis_response() -> str:
    return json.dumps(
        {
            "success": True,
            "artifacts": {
                "task_type": "feature",
                "complexity_score": 7,
                "recommended_roles_count": 3,
                "roles": [
                    {
                        "type": "analyst",
                        "name": "pm-analyst",
                        "capabilities": ["analysis", "planning"],
                    },
                    {
                        "type": "developer",
                        "name": "pm-developer",
                        "capabilities": ["code", "test"],
                    },
                    {
                        "type": "reviewer",
                        "name": "pm-reviewer",
                        "capabilities": ["review", "quality"],
                    },
                ],
                "tasks": [
                    {
                        "name": "doc_model_design",
                        "description": "Define document categories and versioning schema",
                        "role": "analyst",
                        "depends_on": [],
                        "priority": "P1",
                    },
                    {
                        "name": "doc_version_api",
                        "description": "Implement create/list/set-active document APIs",
                        "role": "developer",
                        "depends_on": ["doc_model_design"],
                        "priority": "P1",
                    },
                    {
                        "name": "progress_todo_board",
                        "description": "Implement todo status board and progress percentage",
                        "role": "developer",
                        "depends_on": ["doc_version_api"],
                        "priority": "P1",
                    },
                    {
                        "name": "regression_validation",
                        "description": "Run tests and summarize delivery readiness",
                        "role": "reviewer",
                        "depends_on": ["progress_todo_board"],
                        "priority": "P2",
                    },
                ],
            },
        },
        ensure_ascii=False,
    )


def _plan_response() -> str:
    tasks = json.loads(_analysis_response())["artifacts"]["tasks"]
    return json.dumps(
        {
            "success": True,
            "artifacts": {
                "task_graph": {
                    "tasks": tasks,
                    "execution_waves": [[0], [1], [2], [3]],
                },
                "roles": json.loads(_analysis_response())["artifacts"]["roles"],
            },
        },
        ensure_ascii=False,
    )


def _generic_model_response(action: str) -> str:
    if action == "analyze":
        return _analysis_response()
    if action == "plan":
        return _plan_response()
    return json.dumps(
        {
            "success": True,
            "artifacts": {
                "implementation_status": "completed",
                "tests_passing": True,
                "output": "Auto-driven model response for continuous run",
            },
        },
        ensure_ascii=False,
    )


def log_event(data):
    os.makedirs(LOG_FILE.parent, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=str(SESSION_FILE))
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--max-steps", type=int, default=3000)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument(
        "--mode",
        choices=["synthetic", "real_ipc"],
        default="synthetic",
        help="synthetic auto-responds; real_ipc waits for external IPC response",
    )
    parser.add_argument(
        "--max-no-model-seconds",
        type=float,
        default=120.0,
        help="real_ipc mode: stop after this many seconds with no external model response",
    )
    args = parser.parse_args()

    start = time.time()
    end = start + (args.minutes * 60.0)
    steps = 0
    external_response_seen = False
    no_model_since = time.time()
    end_reason = "time_or_steps_limit"

    log_event(
        {
            "ts": datetime.now().isoformat(),
            "event": "start",
            "session": args.session,
            "mode": args.mode,
        }
    )

    while time.time() < end and steps < args.max_steps:
        runner = PipelineRunner.load_session(args.session)
        result = runner.step()
        runner.save_session(args.session)
        steps += 1

        event = {
            "ts": datetime.now().isoformat(),
            "step": steps,
            "result_action": result.get("action"),
            "needs_model": bool(result.get("needs_model")),
            "done": bool(result.get("done")),
            "reason": result.get("reason", ""),
        }
        log_event(event)

        if result.get("done"):
            end_reason = "runner_done"
            break

        if result.get("needs_model"):
            if args.mode == "synthetic":
                response = _generic_model_response(result.get("action", "model_request"))
                runner = PipelineRunner.load_session(args.session)
                rr = runner.respond(response)
                runner.save_session(args.session)
                steps += 1
                log_event(
                    {
                        "ts": datetime.now().isoformat(),
                        "step": steps,
                        "event": "respond",
                        "mode": "synthetic",
                        "respond_to": result.get("action"),
                        "next_action": rr.get("action"),
                        "done": bool(rr.get("done")),
                        "reason": rr.get("reason", ""),
                        "error": rr.get("error", ""),
                    }
                )
                if rr.get("done"):
                    end_reason = "runner_done"
                    break
            else:
                log_event(
                    {
                        "ts": datetime.now().isoformat(),
                        "step": steps,
                        "event": "await_external_model_response",
                        "mode": "real_ipc",
                        "pending_action": result.get("action"),
                    }
                )
                if external_response_seen is False:
                    if (time.time() - no_model_since) >= args.max_no_model_seconds:
                        end_reason = "no_external_model_response"
                        break

        if result.get("needs_model") is False:
            no_model_since = time.time()
            external_response_seen = True

        time.sleep(args.sleep)

    duration = round(time.time() - start, 2)
    log_event(
        {
            "ts": datetime.now().isoformat(),
            "event": "end",
            "steps": steps,
            "duration_seconds": duration,
            "reason": end_reason,
            "mode": args.mode,
        }
    )

    print(
        json.dumps(
            {
                "success": True,
                "steps": steps,
                "duration_seconds": duration,
                "reason": end_reason,
                "log": str(LOG_FILE),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
