"""
Context Manager - Dynamic context compression and long memory.

Prevents context explosion during 3-5 hour runs by:
- Tracking accumulated context per pipeline/task
- Compressing older context into summaries
- Providing tiered context access (full vs summary)
- Managing artifact cross-references between tasks
"""

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_FULL_CONTEXT_BYTES = 50_000
MAX_SUMMARY_CONTEXT_BYTES = 5_000
COMPRESSION_THRESHOLD = 0.8
PROMPT_BUDGET_BYTES = 100_000

ENTRY_IMPORTANCE = {
    "decide": 10,
    "confirm_plan": 10,
    "plan": 8,
    "check": 8,
    "error": 9,
    "escalation": 9,
    "execute": 5,
    "init": 7,
    "analyze": 6,
    "evolve": 4,
    "verify": 7,
    "orchestrator": 3,
}


class ContextEntry:
    __slots__ = ("task_id", "role_id", "phase", "content", "timestamp", "token_count")

    def __init__(
        self,
        task_id: str,
        role_id: str,
        phase: str,
        content: str,
        token_count: int = 0,
    ):
        self.task_id = task_id
        self.role_id = role_id
        self.phase = phase
        self.content = content
        self.timestamp = datetime.now().isoformat()
        self.token_count = token_count or len(content.split())


class ContextManager:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = Path.cwd() / ".pipeline" / "context"
        else:
            state_dir = Path(state_dir)
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pipelines: Dict[str, List[ContextEntry]] = {}
        self._summaries: Dict[str, str] = {}
        self._artifacts: Dict[str, Dict[str, Any]] = {}
        self.load_state()

    def add_entry(
        self,
        pipeline_id: str,
        task_id: str,
        role_id: str,
        phase: str,
        content: str,
    ):
        with self._lock:
            if pipeline_id not in self._pipelines:
                self._pipelines[pipeline_id] = []
            entry = ContextEntry(task_id, role_id, phase, content)
            self._pipelines[pipeline_id].append(entry)
            self._check_compression(pipeline_id)
        self._persist_entry(pipeline_id, entry)

    def store_artifact(self, pipeline_id: str, task_id: str, key: str, value: Any):
        with self._lock:
            if pipeline_id not in self._artifacts:
                self._artifacts[pipeline_id] = {}
            self._artifacts[pipeline_id][f"{task_id}:{key}"] = {
                "value": value,
                "timestamp": datetime.now().isoformat(),
            }

    def get_artifacts(
        self, pipeline_id: str, task_id: str = None, key: str = None
    ) -> Dict[str, Any]:
        pipe_artifacts = self._artifacts.get(pipeline_id, {})
        if task_id and key:
            v = pipe_artifacts.get(f"{task_id}:{key}")
            return v["value"] if v else None
        if task_id:
            prefix = f"{task_id}:"
            return {
                k[len(prefix) :]: v["value"]
                for k, v in pipe_artifacts.items()
                if k.startswith(prefix)
            }
        return {k: v["value"] for k, v in pipe_artifacts.items()}

    def get_context_for_task(
        self, pipeline_id: str, task_id: str, include_summary: bool = True
    ) -> str:
        parts = []
        if include_summary and pipeline_id in self._summaries:
            parts.append(f"[Previous Context Summary]\n{self._summaries[pipeline_id]}")

        entries = self._pipelines.get(pipeline_id, [])
        task_entries = [e for e in entries if e.task_id == task_id]
        if task_entries:
            parts.append(f"[Current Task Context ({len(task_entries)} entries)]")
            for e in task_entries[-10:]:
                parts.append(f"[{e.phase}] ({e.role_id}): {e.content[:500]}")

        recent = [e for e in entries if e.task_id != task_id][-5:]
        if recent:
            parts.append("[Recent Activity]")
            for e in recent:
                parts.append(
                    f"  [{e.phase}] {e.role_id}/{e.task_id[:12]}: {e.content[:200]}"
                )

        return "\n".join(parts)

    def get_context_for_prompt(self, pipeline_id: str, task_id: str = "") -> str:
        """
        Build context for prompt within budget.

        Priority:
        1. Summary of compressed history
        2. Decision/check entries (high importance)
        3. Current task entries
        4. Recent entries from other tasks
        5. Fill remaining budget with lower-priority entries
        """
        budget = PROMPT_BUDGET_BYTES
        parts = []

        summary = self._summaries.get(pipeline_id, "")
        if summary:
            summary_budget = min(len(summary.encode("utf-8")), int(budget * 0.3))
            summary_bytes = summary.encode("utf-8")[:summary_budget].decode(
                "utf-8", errors="ignore"
            )
            parts.append(f"[Previous Context Summary]\n{summary_bytes}")
            budget -= summary_budget

        entries = self._pipelines.get(pipeline_id, [])
        if not entries:
            return "\n".join(parts)

        scored = []
        for e in entries:
            importance = ENTRY_IMPORTANCE.get(e.phase, 3)
            if e.task_id == task_id:
                importance += 3
            scored.append((importance, e))
        scored.sort(key=lambda x: -x[0])

        used = 0
        high_parts = []
        for importance, e in scored:
            line = f"[{e.phase}] {e.role_id}/{e.task_id[:12]}: {e.content[:300]}\n"
            line_bytes = len(line.encode("utf-8"))
            if used + line_bytes > budget:
                break
            high_parts.append((importance, e.timestamp, line))
            used += line_bytes

        high_parts.sort(key=lambda x: x[1])
        if high_parts:
            parts.append("[Prioritized Context]")
            parts.extend(p[2] for p in high_parts)

        artifacts = self.get_previous_artifacts_summary(pipeline_id)
        if artifacts:
            art_bytes = len(artifacts.encode("utf-8"))
            if used + art_bytes < budget:
                parts.append(artifacts)

        return "\n".join(parts)

    def get_budget_usage(self, pipeline_id: str) -> Dict[str, Any]:
        """Return current context budget usage for a pipeline."""
        entries = self._pipelines.get(pipeline_id, [])
        summary = self._summaries.get(pipeline_id, "")
        entries_bytes = sum(len(e.content.encode("utf-8")) for e in entries)
        summary_bytes = len(summary.encode("utf-8"))
        prompt_bytes = len(self.get_context_for_prompt(pipeline_id).encode("utf-8"))
        return {
            "entries_count": len(entries),
            "entries_bytes": entries_bytes,
            "summary_bytes": summary_bytes,
            "prompt_bytes": prompt_bytes,
            "budget_bytes": PROMPT_BUDGET_BYTES,
            "usage_pct": round(prompt_bytes / PROMPT_BUDGET_BYTES * 100, 1)
            if PROMPT_BUDGET_BYTES
            else 0,
            "compressed": summary != "",
        }

    def get_previous_artifacts_summary(self, pipeline_id: str) -> str:
        artifacts = self._artifacts.get(pipeline_id, {})
        if not artifacts:
            return ""
        lines = ["[Available Artifacts from Previous Tasks]"]
        for k, v in artifacts.items():
            val = v["value"]
            if isinstance(val, dict):
                lines.append(f"  {k}: ({len(val)} items)")
            elif isinstance(val, str):
                lines.append(f"  {k}: {val[:100]}")
            else:
                lines.append(f"  {k}: {type(val).__name__}")
        return "\n".join(lines)

    def _check_compression(self, pipeline_id: str):
        entries = self._pipelines.get(pipeline_id, [])
        total_size = sum(len(e.content) for e in entries)
        if total_size > MAX_FULL_CONTEXT_BYTES * COMPRESSION_THRESHOLD:
            self._compress(pipeline_id)

    def _compress(self, pipeline_id: str):
        entries = self._pipelines.get(pipeline_id, [])
        if len(entries) <= 5:
            return

        scored = []
        for i, e in enumerate(entries):
            importance = ENTRY_IMPORTANCE.get(e.phase, 3)
            recency = min(i / max(len(entries), 1), 1.0)
            score = importance * 0.6 + recency * 0.4
            scored.append((score, i, e))
        scored.sort(key=lambda x: -x[0])

        keep_indices = set()
        for e in entries[-5:]:
            keep_indices.add(id(e))

        for score, idx, e in scored:
            if len(keep_indices) >= 15:
                break
            keep_indices.add(id(e))

        keep = [e for e in entries if id(e) in keep_indices]
        compress = [e for e in entries if id(e) not in keep_indices]

        compressed_lines = []
        if pipeline_id in self._summaries:
            compressed_lines.append(self._summaries[pipeline_id])

        compress.sort(key=lambda e: ENTRY_IMPORTANCE.get(e.phase, 3), reverse=True)
        for e in compress:
            compressed_lines.append(
                f"[{e.phase}] {e.role_id}/{e.task_id[:12]}: {e.content[:150]}"
            )

        summary = "\n".join(compressed_lines)
        if len(summary) > MAX_SUMMARY_CONTEXT_BYTES:
            summary = summary[:MAX_SUMMARY_CONTEXT_BYTES] + "\n...[truncated]"

        self._summaries[pipeline_id] = summary
        keep.sort(key=lambda e: entries.index(e))
        self._pipelines[pipeline_id] = keep
        self._persist_summary(pipeline_id)
        self._snapshot_pipelines()
        logger.info(
            f"Compressed context for {pipeline_id}: "
            f"{len(compress)} -> summary, kept {len(keep)} entries (priority-weighted)"
        )

    def _persist_entry(self, pipeline_id: str, entry: ContextEntry):
        log_file = self.state_dir / f"{pipeline_id}.log"
        line = json.dumps(
            {
                "task_id": entry.task_id,
                "role_id": entry.role_id,
                "phase": entry.phase,
                "content": entry.content[:1000],
                "timestamp": entry.timestamp,
            },
            ensure_ascii=False,
        )
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.debug(f"Failed to persist context entry: {e}")

    def _persist_summary(self, pipeline_id: str):
        summary = self._summaries.get(pipeline_id, "")
        if not summary:
            return
        summary_file = self.state_dir / f"{pipeline_id}_summary.txt"
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(summary)
        except Exception as e:
            logger.debug(f"Failed to persist summary: {e}")

    def save_state(self):
        with self._lock:
            for pid in self._summaries:
                self._persist_summary(pid)
            self._snapshot_pipelines()
            artifacts_file = self.state_dir / "artifacts.json"
            try:
                with open(artifacts_file, "w", encoding="utf-8") as f:
                    json.dump(self._artifacts, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.debug(f"Failed to save artifacts state: {e}")

    def load_state(self):
        artifacts_file = self.state_dir / "artifacts.json"
        if artifacts_file.exists():
            try:
                with open(artifacts_file, "r", encoding="utf-8") as f:
                    self._artifacts = json.load(f)
            except Exception:
                self._artifacts = {}
        self._load_pipelines_from_snapshot()
        self._load_summaries_from_disk()
        self._replay_log_entries()

    def _snapshot_pipelines(self):
        state_file = self.state_dir / "context_state.json"
        try:
            data = {
                "pipelines": {
                    pid: [
                        {
                            "task_id": e.task_id,
                            "role_id": e.role_id,
                            "phase": e.phase,
                            "content": e.content,
                            "timestamp": e.timestamp,
                            "token_count": e.token_count,
                        }
                        for e in entries
                    ]
                    for pid, entries in self._pipelines.items()
                },
                "summaries": dict(self._summaries),
            }
            fd, tmp = tempfile.mkstemp(dir=str(self.state_dir), suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp, str(state_file))
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except Exception as e:
            logger.debug(f"Failed to snapshot context state: {e}")

    def _load_pipelines_from_snapshot(self):
        state_file = self.state_dir / "context_state.json"
        if not state_file.exists():
            return
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for pid, entries_data in data.get("pipelines", {}).items():
                entries = []
                for ed in entries_data:
                    e = ContextEntry(
                        task_id=ed["task_id"],
                        role_id=ed["role_id"],
                        phase=ed["phase"],
                        content=ed["content"],
                        token_count=ed.get("token_count", 0),
                    )
                    e.timestamp = ed.get("timestamp", datetime.now().isoformat())
                    entries.append(e)
                self._pipelines[pid] = entries
        except Exception as e:
            logger.debug(f"Failed to load context snapshot: {e}")

    def _load_summaries_from_disk(self):
        for summary_file in self.state_dir.glob("*_summary.txt"):
            pipeline_id = summary_file.stem.replace("_summary", "")
            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    self._summaries[pipeline_id] = f.read()
            except Exception:
                pass

    def _replay_log_entries(self):
        known_timestamps: Dict[str, set] = {}
        for pid, entries in self._pipelines.items():
            known_timestamps[pid] = {e.timestamp for e in entries}

        for log_file in self.state_dir.glob("*.log"):
            pipeline_id = log_file.stem
            if pipeline_id in known_timestamps:
                existing = known_timestamps[pipeline_id]
            else:
                existing = set()
                if pipeline_id not in self._pipelines:
                    self._pipelines[pipeline_id] = []
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ed = json.loads(line)
                            ts = ed.get("timestamp", "")
                            if ts and ts not in existing:
                                e = ContextEntry(
                                    task_id=ed.get("task_id", ""),
                                    role_id=ed.get("role_id", ""),
                                    phase=ed.get("phase", ""),
                                    content=ed.get("content", ""),
                                )
                                e.timestamp = ts
                                self._pipelines[pipeline_id].append(e)
                                existing.add(ts)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass
