"""
BrakeSystem - Three-level pipeline control: pause, stop, abort.

Provides safe pipeline lifecycle management:
- Yellow (pause): Save state, freeze execution, resumable
- Red (stop): Graceful shutdown with partial results saved
- Emergency abort: Immediate stop with checkpoint preserved

Can be triggered by:
- User command via /map pause|stop|abort
- Auto-trigger from CoolingSystem shutdown
- Auto-trigger from stagnation detection
- External signal via .pipeline/brake file
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class BrakeLevel(str, Enum):
    NONE = "none"
    PAUSE = "pause"
    STOP = "stop"
    ABORT = "abort"


@dataclass
class BrakeState:
    level: BrakeLevel = BrakeLevel.NONE
    reason: str = ""
    triggered_at: Optional[datetime] = None
    triggered_by: str = ""
    partial_results: Dict[str, Any] = None

    def __post_init__(self):
        if self.partial_results is None:
            self.partial_results = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "reason": self.reason,
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "triggered_by": self.triggered_by,
        }


class BrakeSystem:
    def __init__(self, state_dir: str = None, signal_dir: str = None):
        self._state = BrakeState()
        self._callbacks: Dict[BrakeLevel, List[Callable]] = {
            BrakeLevel.PAUSE: [],
            BrakeLevel.STOP: [],
            BrakeLevel.ABORT: [],
        }
        self._paused = False

        if state_dir:
            self._state_dir = Path(state_dir)
        else:
            self._state_dir = Path(".pipeline")
        self._state_dir.mkdir(parents=True, exist_ok=True)

        self._signal_file = self._state_dir / "brake_signal.json"

    @property
    def state(self) -> BrakeState:
        return self._state

    @property
    def is_braking(self) -> bool:
        return self._state.level != BrakeLevel.NONE

    @property
    def is_paused(self) -> bool:
        return self._paused

    def on_brake(self, level: BrakeLevel, callback: Callable):
        self._callbacks[level].append(callback)

    def pause(self, reason: str = "", triggered_by: str = "user") -> BrakeState:
        self._state = BrakeState(
            level=BrakeLevel.PAUSE,
            reason=reason or "User requested pause",
            triggered_at=datetime.now(),
            triggered_by=triggered_by,
        )
        self._paused = True
        self._persist_state()
        self._fire_callbacks(BrakeLevel.PAUSE)
        logger.info("Brake PAUSED: %s (by %s)", reason, triggered_by)
        return self._state

    def stop(self, reason: str = "", triggered_by: str = "user") -> BrakeState:
        self._state = BrakeState(
            level=BrakeLevel.STOP,
            reason=reason or "User requested stop",
            triggered_at=datetime.now(),
            triggered_by=triggered_by,
        )
        self._paused = True
        self._persist_state()
        self._fire_callbacks(BrakeLevel.STOP)
        logger.info("Brake STOPPED: %s (by %s)", reason, triggered_by)
        return self._state

    def abort(self, reason: str = "", triggered_by: str = "emergency") -> BrakeState:
        self._state = BrakeState(
            level=BrakeLevel.ABORT,
            reason=reason or "Emergency abort",
            triggered_at=datetime.now(),
            triggered_by=triggered_by,
        )
        self._paused = True
        self._persist_state()
        self._fire_callbacks(BrakeLevel.ABORT)
        logger.warning("Brake ABORTED: %s (by %s)", reason, triggered_by)
        return self._state

    def resume(self) -> bool:
        if self._state.level not in (BrakeLevel.PAUSE, BrakeLevel.NONE):
            logger.warning("Cannot resume from %s, only from PAUSE", self._state.level.value)
            return False
        self._state = BrakeState()
        self._paused = False
        self._clear_signal()
        logger.info("Brake released, execution resumed")
        return True

    def check_external_signal(self) -> Optional[BrakeLevel]:
        if not self._signal_file.exists():
            return None
        try:
            with open(self._signal_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            level_str = data.get("level", "")
            reason = data.get("reason", "")
            try:
                level = BrakeLevel(level_str)
            except ValueError:
                return None

            if level == BrakeLevel.PAUSE:
                self.pause(reason=reason, triggered_by="external_signal")
            elif level == BrakeLevel.STOP:
                self.stop(reason=reason, triggered_by="external_signal")
            elif level == BrakeLevel.ABORT:
                self.abort(reason=reason, triggered_by="external_signal")
            self._clear_signal()
            return level
        except Exception as e:
            logger.error("Error reading brake signal: %s", e)
            return None

    def should_continue(self) -> bool:
        return self._state.level == BrakeLevel.NONE

    def _fire_callbacks(self, level: BrakeLevel):
        for cb in self._callbacks.get(level, []):
            try:
                cb(self._state)
            except Exception as e:
                logger.error("Brake callback error: %s", e)

    def _persist_state(self):
        path = self._state_dir / "brake_state.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._state.to_dict(), f, ensure_ascii=False, indent=2)

    def _clear_signal(self):
        if self._signal_file.exists():
            self._signal_file.unlink()
