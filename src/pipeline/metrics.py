"""
Runtime metrics collection for pipeline operations.

Tracks phase durations, task throughput, retry rates, decision stats,
recovery events, and other operational metrics for long-running pipelines.
"""

import json
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class PipelineMetrics:
    def __init__(self, state_dir: str = None):
        self._lock = threading.Lock()
        self._pipelines: Dict[str, Dict[str, Any]] = {}
        self._state_dir = Path(state_dir) if state_dir else None
        if self._state_dir:
            self._state_dir.mkdir(parents=True, exist_ok=True)

    def register_pipeline(self, pipeline_id: str):
        with self._lock:
            if pipeline_id not in self._pipelines:
                self._pipelines[pipeline_id] = {
                    "created_at": datetime.now().isoformat(),
                    "phase_entries": {},
                    "phase_exits": {},
                    "task_events": [],
                    "decisions": [],
                    "recoveries": [],
                    "checkpoints": 0,
                    "peak_concurrent_tasks": 0,
                    "_active_tasks": set(),
                }

    def record_phase_entry(self, pipeline_id: str, phase: str):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["phase_entries"][phase] = datetime.now().isoformat()

    def record_phase_exit(self, pipeline_id: str, phase: str):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["phase_exits"][phase] = datetime.now().isoformat()

    def record_task_start(self, pipeline_id: str, task_id: str):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["_active_tasks"].add(task_id)
            current = len(p["_active_tasks"])
            if current > p["peak_concurrent_tasks"]:
                p["peak_concurrent_tasks"] = current
            p["task_events"].append(
                {
                    "task_id": task_id,
                    "event": "start",
                    "at": datetime.now().isoformat(),
                }
            )

    def record_task_complete(
        self, pipeline_id: str, task_id: str, duration_seconds: float = None
    ):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["_active_tasks"].discard(task_id)
            entry: Dict[str, Any] = {
                "task_id": task_id,
                "event": "complete",
                "at": datetime.now().isoformat(),
            }
            if duration_seconds is not None:
                entry["duration_seconds"] = round(duration_seconds, 2)
            p["task_events"].append(entry)

    def record_task_fail(self, pipeline_id: str, task_id: str, retry_count: int = 0):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["_active_tasks"].discard(task_id)
            p["task_events"].append(
                {
                    "task_id": task_id,
                    "event": "fail",
                    "at": datetime.now().isoformat(),
                    "retry_count": retry_count,
                }
            )

    def record_task_retry(self, pipeline_id: str, task_id: str, attempt: int):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["task_events"].append(
                {
                    "task_id": task_id,
                    "event": "retry",
                    "at": datetime.now().isoformat(),
                    "attempt": attempt,
                }
            )

    def record_decision(
        self, pipeline_id: str, decision_point: str, choice: str, auto: bool = False
    ):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["decisions"].append(
                {
                    "point": decision_point,
                    "choice": choice,
                    "auto": auto,
                    "at": datetime.now().isoformat(),
                }
            )

    def record_recovery(self, pipeline_id: str, strategy: str, success: bool):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["recoveries"].append(
                {
                    "strategy": strategy,
                    "success": success,
                    "at": datetime.now().isoformat(),
                }
            )

    def record_checkpoint(self, pipeline_id: str):
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return
            p["checkpoints"] += 1

    def get_metrics(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            p = self._pipelines.get(pipeline_id)
            if not p:
                return None

        return self._compute_metrics(pipeline_id, p)

    def _compute_metrics(self, pipeline_id: str, p: Dict[str, Any]) -> Dict[str, Any]:
        task_events = p.get("task_events", [])
        starts = [e for e in task_events if e["event"] == "start"]
        completes = [e for e in task_events if e["event"] == "complete"]
        fails = [e for e in task_events if e["event"] == "fail"]
        retries = [e for e in task_events if e["event"] == "retry"]

        durations = [
            e["duration_seconds"] for e in completes if "duration_seconds" in e
        ]

        phase_durations = self._compute_phase_durations(p)

        decisions = p.get("decisions", [])
        auto_decisions = sum(1 for d in decisions if d.get("auto"))
        decision_breakdown = defaultdict(int)
        for d in decisions:
            decision_breakdown[d.get("choice", "unknown")] += 1

        recoveries = p.get("recoveries", [])
        recovery_successes = sum(1 for r in recoveries if r.get("success"))

        elapsed_seconds = 0.0
        created = p.get("created_at")
        if created:
            try:
                elapsed_seconds = (
                    datetime.now() - datetime.fromisoformat(created)
                ).total_seconds()
            except (ValueError, TypeError):
                pass

        throughput = (
            (len(completes) / elapsed_seconds * 60) if elapsed_seconds > 0 else 0.0
        )

        return {
            "pipeline_id": pipeline_id,
            "elapsed_seconds": round(elapsed_seconds, 1),
            "tasks": {
                "started": len(starts),
                "completed": len(completes),
                "failed": len(fails),
                "retried": len(retries),
                "peak_concurrent": p.get("peak_concurrent_tasks", 0),
                "avg_duration_seconds": round(sum(durations) / len(durations), 2)
                if durations
                else None,
                "throughput_per_minute": round(throughput, 2),
            },
            "phase_durations": phase_durations,
            "decisions": {
                "total": len(decisions),
                "auto_resolved": auto_decisions,
                "by_choice": dict(decision_breakdown),
            },
            "recoveries": {
                "total": len(recoveries),
                "successful": recovery_successes,
            },
            "checkpoints": p.get("checkpoints", 0),
        }

    def _compute_phase_durations(self, p: Dict[str, Any]) -> Dict[str, Any]:
        entries = p.get("phase_entries", {})
        exits = p.get("phase_exits", {})
        result = {}
        for phase, entry_time in entries.items():
            exit_time = exits.get(phase)
            if exit_time:
                try:
                    dur = (
                        datetime.fromisoformat(exit_time)
                        - datetime.fromisoformat(entry_time)
                    ).total_seconds()
                    result[phase] = round(dur, 2)
                except (ValueError, TypeError):
                    result[phase] = None
            else:
                try:
                    dur = (
                        datetime.now() - datetime.fromisoformat(entry_time)
                    ).total_seconds()
                    result[phase] = round(dur, 2)
                except (ValueError, TypeError):
                    result[phase] = None
        return result

    def get_summary(self, pipeline_id: str) -> str:
        m = self.get_metrics(pipeline_id)
        if not m:
            return "No metrics available."

        lines = [
            f"Pipeline: {m['pipeline_id']}",
            f"Elapsed: {m['elapsed_seconds']:.0f}s",
            f"Tasks: {m['tasks']['completed']}/{m['tasks']['started']} completed, {m['tasks']['failed']} failed, {m['tasks']['retried']} retried",
            f"Peak concurrent: {m['tasks']['peak_concurrent']}",
            f"Throughput: {m['tasks']['throughput_per_minute']:.1f}/min",
        ]
        if m["tasks"]["avg_duration_seconds"] is not None:
            lines.append(
                f"Avg task duration: {m['tasks']['avg_duration_seconds']:.1f}s"
            )

        if m["phase_durations"]:
            lines.append("Phase durations:")
            for phase, dur in m["phase_durations"].items():
                if dur is not None:
                    lines.append(f"  {phase}: {dur:.1f}s")

        lines.append(
            f"Decisions: {m['decisions']['total']} ({m['decisions']['auto_resolved']} auto)"
        )
        lines.append(
            f"Recoveries: {m['recoveries']['total']} ({m['recoveries']['successful']} successful)"
        )
        lines.append(f"Checkpoints: {m['checkpoints']}")

        return "\n".join(lines)

    def save(self, pipeline_id: str = None):
        if not self._state_dir:
            return
        with self._lock:
            targets = [pipeline_id] if pipeline_id else list(self._pipelines.keys())
            for pid in targets:
                p = self._pipelines.get(pid)
                if not p:
                    continue
                data = dict(p)
                data["_active_tasks"] = list(data.get("_active_tasks", set()))
                fpath = self._state_dir / f"{pid}_metrics.json"
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, default=str)
                except Exception:
                    pass

    def load(self, pipeline_id: str):
        if not self._state_dir:
            return
        fpath = self._state_dir / f"{pipeline_id}_metrics.json"
        if not fpath.exists():
            return
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["_active_tasks"] = set(data.get("_active_tasks", []))
            with self._lock:
                self._pipelines[pipeline_id] = data
        except Exception:
            pass
