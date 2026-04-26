"""
CoolingSystem - Three-tier context compression to prevent context explosion.

Monitors accumulated context during long-running pipelines and applies
progressive compression to keep the engine running smoothly.

Tier 1 (>50K tokens): Compress completed tasks to summaries
Tier 2 (>100K tokens or >3h): Deep compression + checkpoint archiving
Tier 3 (>150K tokens or >4.5h): Minimal context + low-power mode
Auto-shutdown (>=5h): Save state + shutdown report + engine off
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CoolingLevel(str, Enum):
    NORMAL = "normal"
    LEVEL_1 = "level_1"
    LEVEL_2 = "level_2"
    LEVEL_3 = "level_3"
    SHUTDOWN = "shutdown"


@dataclass
class CoolingState:
    level: CoolingLevel = CoolingLevel.NORMAL
    total_tokens_estimate: int = 0
    runtime_seconds: float = 0.0
    compression_count: int = 0
    last_compression_at: Optional[datetime] = None
    snapshots: List[str] = field(default_factory=list)
    shutdown_report_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "total_tokens_estimate": self.total_tokens_estimate,
            "runtime_seconds": self.runtime_seconds,
            "compression_count": self.compression_count,
            "last_compression_at": self.last_compression_at.isoformat() if self.last_compression_at else None,
            "snapshots": self.snapshots,
            "shutdown_report_path": self.shutdown_report_path,
        }


@dataclass
class CoolingConfig:
    tier1_token_threshold: int = 50_000
    tier2_token_threshold: int = 100_000
    tier3_token_threshold: int = 150_000
    tier2_runtime_seconds: float = 3 * 3600
    tier3_runtime_seconds: float = 4.5 * 3600
    shutdown_runtime_seconds: float = 5 * 3600
    snapshot_dir: str = ".pipeline/cooling_snapshots"


class CoolingSystem:
    def __init__(self, config: CoolingConfig = None, state_dir: str = None):
        self.config = config or CoolingConfig()
        self.state = CoolingState()
        self._start_time: Optional[float] = None
        self._token_accumulator: int = 0
        self._compressed_summaries: List[str] = []
        self._active_tasks_summary: List[str] = []

        if state_dir:
            snap_dir = Path(state_dir) / "cooling_snapshots"
        else:
            snap_dir = Path(self.config.snapshot_dir)
        self._snap_dir = snap_dir
        self._snap_dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        self._start_time = time.time()
        self.state = CoolingState()
        logger.info("CoolingSystem started, monitoring context pressure")

    def stop(self):
        self._start_time = None
        logger.info("CoolingSystem stopped after %d compressions", self.state.compression_count)

    def register_prompt(self, text: str):
        tokens = len(text) // 4
        self._token_accumulator += tokens
        self.state.total_tokens_estimate += tokens

    def register_response(self, text: str):
        tokens = len(text) // 4
        self._token_accumulator += tokens
        self.state.total_tokens_estimate += tokens

    def register_task_summary(self, summary: str):
        self._compressed_summaries.append(summary)

    def check_and_cool(self, pipeline_data: Dict[str, Any] = None) -> CoolingLevel:
        if self._start_time is None:
            return CoolingLevel.NORMAL

        elapsed = time.time() - self._start_time
        self.state.runtime_seconds = elapsed
        tokens = self.state.total_tokens_estimate

        if elapsed >= self.config.shutdown_runtime_seconds:
            level = CoolingLevel.SHUTDOWN
        elif tokens >= self.config.tier3_token_threshold or elapsed >= self.config.tier3_runtime_seconds:
            level = CoolingLevel.LEVEL_3
        elif tokens >= self.config.tier2_token_threshold or elapsed >= self.config.tier2_runtime_seconds:
            level = CoolingLevel.LEVEL_2
        elif tokens >= self.config.tier1_token_threshold:
            level = CoolingLevel.LEVEL_1
        else:
            level = CoolingLevel.NORMAL

        if level != self.state.level:
            logger.warning(
                "Cooling level changed: %s -> %s (tokens=%d, elapsed=%.0fs)",
                self.state.level.value, level.value, tokens, elapsed,
            )
            self.state.level = level

            if level == CoolingLevel.LEVEL_1:
                self._apply_tier1(pipeline_data)
            elif level == CoolingLevel.LEVEL_2:
                self._apply_tier2(pipeline_data)
            elif level == CoolingLevel.LEVEL_3:
                self._apply_tier3(pipeline_data)
            elif level == CoolingLevel.SHUTDOWN:
                self._apply_shutdown(pipeline_data)

        return level

    def _apply_tier1(self, pipeline_data: Dict[str, Any] = None):
        self.state.compression_count += 1
        self.state.last_compression_at = datetime.now()
        reduction = int(self._token_accumulator * 0.5)
        self.state.total_tokens_estimate = max(0, self.state.total_tokens_estimate - reduction)
        self._token_accumulator = 0
        logger.info("Tier 1 cooling applied: reduced ~%d tokens", reduction)

    def _apply_tier2(self, pipeline_data: Dict[str, Any] = None):
        self.state.compression_count += 1
        self.state.last_compression_at = datetime.now()
        self._save_snapshot(pipeline_data)
        reduction = int(self.state.total_tokens_estimate * 0.7)
        self.state.total_tokens_estimate = max(0, self.state.total_tokens_estimate - reduction)
        self._token_accumulator = 0
        logger.info("Tier 2 cooling applied: reduced ~%d tokens, snapshot saved", reduction)

    def _apply_tier3(self, pipeline_data: Dict[str, Any] = None):
        self.state.compression_count += 1
        self.state.last_compression_at = datetime.now()
        self._save_snapshot(pipeline_data)
        self.state.total_tokens_estimate = max(
            5000, int(self.state.total_tokens_estimate * 0.1)
        )
        self._token_accumulator = 0
        logger.warning("Tier 3 cooling applied: deep compression, entering low-power mode")

    def _apply_shutdown(self, pipeline_data: Dict[str, Any] = None):
        report_path = self._generate_shutdown_report(pipeline_data)
        self.state.shutdown_report_path = report_path
        logger.warning("Engine shutdown triggered: runtime exceeded limit. Report: %s", report_path)

    def _save_snapshot(self, pipeline_data: Dict[str, Any] = None):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._snap_dir / f"cooling_{ts}.json"
        snapshot = {
            "timestamp": ts,
            "level": self.state.level.value,
            "total_tokens_estimate": self.state.total_tokens_estimate,
            "runtime_seconds": self.state.runtime_seconds,
            "compressed_summaries": self._compressed_summaries[-20:],
            "pipeline_data_keys": list(pipeline_data.keys()) if pipeline_data else [],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        self.state.snapshots.append(str(path))

    def _generate_shutdown_report(self, pipeline_data: Dict[str, Any] = None) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self._snap_dir / f"shutdown_report_{ts}.md"

        elapsed_h = self.state.runtime_seconds / 3600
        lines = [
            f"# MAP Engine Shutdown Report",
            f"",
            f"**Shutdown Time**: {datetime.now().isoformat()}",
            f"**Runtime**: {elapsed_h:.1f} hours",
            f"**Reason**: Auto-shutdown (time limit reached)",
            f"**Total Compressions**: {self.state.compression_count}",
            f"**Final Token Estimate**: {self.state.total_tokens_estimate:,}",
            f"",
            f"## Completed Work",
            f"",
        ]
        for s in self._compressed_summaries:
            lines.append(f"- {s}")

        if pipeline_data:
            tasks = pipeline_data.get("tasks", [])
            completed = [t for t in tasks if isinstance(t, dict) and t.get("status") == "completed"]
            pending = [t for t in tasks if isinstance(t, dict) and t.get("status") != "completed"]
            lines.append(f"")
            lines.append(f"**Tasks Completed**: {len(completed)}")
            lines.append(f"**Tasks Remaining**: {len(pending)}")
            if pending:
                lines.append(f"")
                lines.append(f"### Remaining Tasks")
                for t in pending[:20]:
                    lines.append(f"- {t.get('description', t.get('name', str(t)))}")

        lines.append(f"")
        lines.append(f"## Snapshots")
        for s in self.state.snapshots:
            lines.append(f"- {s}")

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return str(report_path)

    def get_compressed_context(self) -> str:
        if not self._compressed_summaries:
            return ""
        return "\n".join(self._compressed_summaries[-10:])

    @property
    def is_low_power(self) -> bool:
        return self.state.level == CoolingLevel.LEVEL_3

    @property
    def should_shutdown(self) -> bool:
        return self.state.level == CoolingLevel.SHUTDOWN
