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
        snapshot = {
            "pipeline": pipeline_run.to_dict(),
            "tasks": task_queue_snapshot,
            "roles": roles_snapshot,
            "context_summary": context_summary,
            "timestamp": datetime.now().isoformat(),
        }
        return self.create_checkpoint(
            pipeline_id=pipeline_run.id,
            phase=pipeline_run.phase,
            label=label or f"pdca_cycle_{pipeline_run.pdca_cycle}",
            snapshot=snapshot,
        )

    def restore_latest(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        checkpoints = self.list_checkpoints(pipeline_id)
        if not checkpoints:
            logger.warning(f"No checkpoints found for {pipeline_id}")
            return None
        latest = checkpoints[-1]
        return self._load_checkpoint(latest["id"])

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
        for f in sorted(ckpt_dir.glob("*.json")):
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
