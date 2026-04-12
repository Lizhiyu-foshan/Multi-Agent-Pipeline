"""
Checkpoint Manager - Snapshot and restore for failure recovery.

Supports:
- Periodic snapshots of pipeline state
- Restore from latest checkpoint on failure
- Labeled checkpoints at decision points
- Automatic cleanup of old checkpoints
"""

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Checkpoint, DateTimeEncoder, PipelineRun

logger = logging.getLogger(__name__)


class CheckpointManager:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = Path.cwd() / ".pipeline" / "checkpoints"
        else:
            state_dir = Path(state_dir)
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._last_snapshot: Dict[str, Dict[str, Any]] = {}

    def create_checkpoint(
        self,
        pipeline_id: str,
        phase: str,
        task_id: str = None,
        label: str = "",
        snapshot: Dict[str, Any] = None,
    ) -> Checkpoint:
        ckpt = Checkpoint(
            pipeline_id=pipeline_id,
            phase=phase,
            task_id=task_id,
            label=label,
            snapshot=snapshot or {},
        )
        self._save_checkpoint(ckpt)
        logger.info(
            f"Checkpoint created: {ckpt.id} @ {phase}"
            + (f" ({label})" if label else "")
        )
        return ckpt

    def create_full_snapshot(
        self,
        pipeline_run: PipelineRun,
        task_queue_snapshot: Dict[str, Any],
        roles_snapshot: Dict[str, Any],
        context_summary: str = "",
        label: str = "",
    ) -> Checkpoint:
        current = {
            "pipeline": pipeline_run.to_dict(),
            "task_queue_snapshot": task_queue_snapshot,
            "tasks": task_queue_snapshot,
            "roles": roles_snapshot,
            "context_summary": context_summary,
            "timestamp": datetime.now().isoformat(),
        }
        last = self._last_snapshot.get(pipeline_run.id)
        if last:
            delta = self._compute_delta(last, current)
            if delta.get("is_delta"):
                ckpt = self.create_checkpoint(
                    pipeline_id=pipeline_run.id,
                    phase=pipeline_run.phase,
                    label=label or f"pdca_cycle_{pipeline_run.pdca_cycle}",
                    snapshot=delta,
                )
                self._last_snapshot[pipeline_run.id] = current
                return ckpt

        self._last_snapshot[pipeline_run.id] = current
        return self.create_checkpoint(
            pipeline_id=pipeline_run.id,
            phase=pipeline_run.phase,
            label=label or f"pdca_cycle_{pipeline_run.pdca_cycle}",
            snapshot=current,
        )

    def _compute_delta(
        self, previous: Dict[str, Any], current: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compute delta between previous and current snapshot."""
        delta: Dict[str, Any] = {"is_delta": True}
        has_changes = False

        prev_pipe = previous.get("pipeline", {})
        curr_pipe = current.get("pipeline", {})

        pipe_diff = {}
        for key in (
            "phase",
            "state",
            "pdca_cycle",
            "tasks",
            "roles",
            "artifacts",
            "decision_history",
            "recovery_count",
            "last_checkpoint_id",
        ):
            if curr_pipe.get(key) != prev_pipe.get(key):
                pipe_diff[key] = curr_pipe.get(key)
                has_changes = True

        if pipe_diff:
            pipe_diff["id"] = curr_pipe.get("id", "")
            delta["pipeline_delta"] = pipe_diff
        else:
            delta["pipeline_delta"] = None

        prev_tasks = previous.get("task_queue_snapshot", previous.get("tasks", {}))
        curr_tasks = current.get("task_queue_snapshot", current.get("tasks", {}))
        if curr_tasks != prev_tasks:
            delta["task_queue_snapshot"] = curr_tasks
            delta["tasks"] = curr_tasks
            has_changes = True
        else:
            delta["task_queue_snapshot"] = None
            delta["tasks"] = None

        delta["roles"] = (
            current.get("roles")
            if current.get("roles") != previous.get("roles")
            else None
        )
        delta["context_summary"] = current.get("context_summary", "")
        delta["timestamp"] = current.get("timestamp", "")
        delta["base_checkpoint_id"] = previous.get("pipeline", {}).get("id", "")

        if not has_changes:
            return {"is_delta": False}

        return delta

    def _apply_delta(
        self, base: Dict[str, Any], delta: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply a delta to a base snapshot to reconstruct full state."""
        if not delta.get("is_delta"):
            return delta

        result = dict(base)
        pipe_delta = delta.get("pipeline_delta")
        if pipe_delta:
            pipeline = dict(result.get("pipeline", {}))
            pipeline.update(pipe_delta)
            result["pipeline"] = pipeline

        tasks_delta = delta.get("task_queue_snapshot", delta.get("tasks"))
        if tasks_delta is not None:
            result["task_queue_snapshot"] = tasks_delta
            result["tasks"] = tasks_delta
        if delta.get("roles") is not None:
            result["roles"] = delta["roles"]
        result["context_summary"] = delta.get("context_summary", "")
        result["timestamp"] = delta.get("timestamp", "")
        return result

    def restore_latest(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        checkpoints = self.list_checkpoints(pipeline_id)
        if not checkpoints:
            logger.warning(f"No checkpoints found for {pipeline_id}")
            return None
        latest = checkpoints[-1]
        data = self._load_checkpoint(latest["id"])
        if data and data.get("snapshot", {}).get("is_delta"):
            data["snapshot"] = self._reconstruct_from_delta(pipeline_id, checkpoints)
        return data

    def _reconstruct_from_delta(
        self, pipeline_id: str, checkpoints: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Walk backwards from latest to find full base, then apply deltas forward."""
        all_snaps = []
        for ckpt_meta in reversed(checkpoints):
            data = self._load_checkpoint(ckpt_meta["id"])
            if data:
                all_snaps.append(data.get("snapshot", {}))

        if not all_snaps:
            return {}

        base_idx = None
        for i, snap in enumerate(all_snaps):
            if not snap.get("is_delta"):
                base_idx = i
                break

        if base_idx is None:
            return all_snaps[0] if all_snaps else {}

        result = all_snaps[base_idx]
        for i in range(base_idx - 1, -1, -1):
            result = self._apply_delta(result, all_snaps[i])

        return result

    def restore_to_phase(
        self, pipeline_id: str, phase: str
    ) -> Optional[Dict[str, Any]]:
        checkpoints = self.list_checkpoints(pipeline_id)
        phase_ckpts = [c for c in checkpoints if c["phase"] == phase]
        if not phase_ckpts:
            logger.warning(f"No checkpoint at phase {phase} for {pipeline_id}")
            return None
        return self._load_checkpoint(phase_ckpts[-1]["id"])

    def restore_by_label(
        self, pipeline_id: str, label: str
    ) -> Optional[Dict[str, Any]]:
        checkpoints = self.list_checkpoints(pipeline_id)
        labeled = [c for c in checkpoints if c.get("label") == label]
        if not labeled:
            logger.warning(f"No checkpoint with label '{label}' for {pipeline_id}")
            return None
        return self._load_checkpoint(labeled[-1]["id"])

    def list_checkpoints(self, pipeline_id: str) -> List[Dict[str, Any]]:
        ckpt_dir = self.state_dir / pipeline_id
        if not ckpt_dir.exists():
            return []
        results = []
        for f in ckpt_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                results.append(
                    {
                        "id": data.get("id", f.stem),
                        "phase": data.get("phase", ""),
                        "label": data.get("label", ""),
                        "task_id": data.get("task_id"),
                        "created_at": data.get("created_at", ""),
                    }
                )
            except Exception:
                continue
        results.sort(key=lambda x: x.get("created_at", ""))
        return results

    def cleanup_old(self, pipeline_id: str, keep: int = 10):
        ckpt_dir = self.state_dir / pipeline_id
        if not ckpt_dir.exists():
            return
        files = sorted(ckpt_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        if len(files) > keep:
            for f in files[: len(files) - keep]:
                try:
                    f.unlink()
                except OSError:
                    pass

    def _save_checkpoint(self, ckpt: Checkpoint):
        ckpt_dir = self.state_dir / ckpt.pipeline_id
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        ckpt_file = ckpt_dir / f"{ckpt.id}.json"
        try:
            with open(ckpt_file, "w", encoding="utf-8") as f:
                json.dump(
                    ckpt.to_dict(), f, indent=2, ensure_ascii=False, cls=DateTimeEncoder
                )
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def _load_checkpoint(self, ckpt_id: str) -> Optional[Dict[str, Any]]:
        for pipe_dir in self.state_dir.iterdir():
            if not pipe_dir.is_dir():
                continue
            ckpt_file = pipe_dir / f"{ckpt_id}.json"
            if ckpt_file.exists():
                try:
                    with open(ckpt_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    return None
        return None
