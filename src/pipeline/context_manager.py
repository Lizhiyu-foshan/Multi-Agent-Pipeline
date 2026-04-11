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
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_FULL_CONTEXT_BYTES = 50_000
MAX_SUMMARY_CONTEXT_BYTES = 5_000
COMPRESSION_THRESHOLD = 0.8


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
        self._pipelines: Dict[str, List[ContextEntry]] = {}
        self._summaries: Dict[str, str] = {}
        self._artifacts: Dict[str, Dict[str, Any]] = {}

    def add_entry(
        self,
        pipeline_id: str,
        task_id: str,
        role_id: str,
        phase: str,
        content: str,
    ):
        if pipeline_id not in self._pipelines:
            self._pipelines[pipeline_id] = []
        entry = ContextEntry(task_id, role_id, phase, content)
        self._pipelines[pipeline_id].append(entry)
        self._check_compression(pipeline_id)
        self._persist_entry(pipeline_id, entry)

    def store_artifact(self, pipeline_id: str, task_id: str, key: str, value: Any):
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

        keep = entries[-5:]
        compress = entries[:-5]

        compressed_lines = []
        if pipeline_id in self._summaries:
            compressed_lines.append(self._summaries[pipeline_id])

        for e in compress:
            compressed_lines.append(
                f"[{e.phase}] {e.role_id}/{e.task_id[:12]}: {e.content[:150]}"
            )

        summary = "\n".join(compressed_lines)
        if len(summary) > MAX_SUMMARY_CONTEXT_BYTES:
            summary = summary[:MAX_SUMMARY_CONTEXT_BYTES] + "\n...[truncated]"

        self._summaries[pipeline_id] = summary
        self._pipelines[pipeline_id] = keep
        logger.info(
            f"Compressed context for {pipeline_id}: {len(compress)} -> summary, kept {len(keep)} entries"
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
        for pid in self._summaries:
            self._persist_summary(pid)
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
