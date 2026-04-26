"""Session driver for PipelineRunner step-by-step mode."""
import sys, json, os
sys.path.insert(0, "src")
sys.path.insert(0, "specs")
sys.path.insert(0, ".skills/bmad-evo")
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
SESSION_FILE = os.path.join(PROJECT_ROOT, ".pipeline_session", "runner_session.json")

def cmd_init(description, require_real_model=False, max_iterations=500, max_hours=5.0):
    from pipeline.runner import PipelineRunner
    session_dir = os.path.join(PROJECT_ROOT, ".pipeline_session")
    runner = PipelineRunner(
        project_root=PROJECT_ROOT,
        description=description,
        backlog_files=[os.path.join(PROJECT_ROOT, "docs", "project-manage-backlog-v4.md")],
        design_docs=[os.path.join(PROJECT_ROOT, "docs", "project-manage-design-v4.md")],
        model_mode="opencode_ipc",
        max_hours=max_hours,
        max_iterations=max_iterations,
        state_dir=session_dir,
        require_real_model=require_real_model,
    )
    runner.setup()
    runner._load_initial_backlog()
    runner.start_time = __import__("datetime").datetime.now()
    result = runner.step()
    runner.save_session()
    return result, runner

def cmd_step():
    from pipeline.runner import PipelineRunner
    runner = PipelineRunner.load_session(SESSION_FILE)
    result = runner.step()
    runner.save_session()
    return result, runner

def cmd_respond(response_text):
    from pipeline.runner import PipelineRunner
    runner = PipelineRunner.load_session(SESSION_FILE)
    if os.path.exists(response_text):
        with open(response_text, "r", encoding="utf-8") as f:
            response_text = f.read()
    result = runner.respond(response_text)
    runner.save_session()
    return result, runner

def cmd_status():
    from pipeline.runner import PipelineRunner
    runner = PipelineRunner.load_session(SESSION_FILE)
    return runner.get_status(), runner

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "step", "respond", "status"])
    parser.add_argument("--desc", default="")
    parser.add_argument("--response", default="")
    parser.add_argument("--require-real-model", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=500)
    parser.add_argument("--max-hours", type=float, default=5.0)
    args = parser.parse_args()

    if args.command == "init":
        result, runner = cmd_init(
            args.desc or "MAP self-bootstrap development",
            require_real_model=args.require_real_model,
            max_iterations=args.max_iterations,
            max_hours=args.max_hours,
        )
    elif args.command == "step":
        result, runner = cmd_step()
    elif args.command == "respond":
        result, runner = cmd_respond(args.response)
    elif args.command == "status":
        result, runner = cmd_status()

    if isinstance(result, dict):
        out = {k: v for k, v in result.items() if k != "prompt"}
        if result.get("prompt"):
            out["prompt_preview"] = result["prompt"][:200]
            out["prompt_full_len"] = len(result["prompt"])
        print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
        if result.get("prompt"):
            prompt_file = os.path.join(PROJECT_ROOT, ".pipeline", "current_prompt.txt")
            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write(result["prompt"])
            print(f"\nPrompt written to {prompt_file}")
