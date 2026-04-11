"""
Lock Manager - Windows-compatible file-based locking.

Replaces the reference's fcntl-based locking with msvcrt/msys-compatible approach.
Uses a combination of atomic file writes and timestamp-based expiry.
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from .models import DateTimeEncoder

logger = logging.getLogger(__name__)


class LockManager:
    """
    File-based lock manager compatible with Windows.

    Strategy:
    - Uses os.open with O_CREAT | O_EXCL for atomic lock acquisition
    - Falls back to timestamp-based expiry if atomic creation fails
    - No dependency on fcntl or msvcrt
    """

    def __init__(self, lock_dir: str = None, default_timeout_seconds: int = 120):
        if lock_dir is None:
            lock_dir = Path.cwd() / ".pipeline" / "locks"
        else:
            lock_dir = Path(lock_dir)

        self.lock_dir = lock_dir
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.default_timeout_seconds = default_timeout_seconds
        self._held_locks: Dict[str, str] = {}

    def acquire(self, role_id: str, task_id: str, timeout_seconds: int = None) -> bool:
        timeout_seconds = timeout_seconds or self.default_timeout_seconds
        lock_file = self.lock_dir / f"{role_id}.lock"

        if self._is_held_by_us(role_id):
            logger.debug(f"Lock already held by us: {role_id}")
            return True

        if self._is_expired(lock_file):
            self._force_release(lock_file)

        lock_data = {
            "role_id": role_id,
            "task_id": task_id,
            "acquired_at": datetime.now().isoformat(),
            "timeout_seconds": timeout_seconds,
            "pid": os.getpid(),
        }

        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                json.dump(lock_data, f)
            self._held_locks[role_id] = str(lock_file)
            logger.info(f"Lock acquired: {role_id} for task {task_id}")
            return True
        except FileExistsError:
            if self._is_expired(lock_file):
                try:
                    os.remove(str(lock_file))
                    return self.acquire(role_id, task_id, timeout_seconds)
                except OSError:
                    pass
            logger.debug(f"Lock busy: {role_id}")
            return False
        except Exception as e:
            logger.error(f"Failed to acquire lock for {role_id}: {e}")
            return False

    def release(self, role_id: str) -> bool:
        lock_file = self.lock_dir / f"{role_id}.lock"

        try:
            if lock_file.exists():
                os.remove(str(lock_file))
            self._held_locks.pop(role_id, None)
            logger.info(f"Lock released: {role_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to release lock for {role_id}: {e}")
            self._held_locks.pop(role_id, None)
            return False

    def is_locked(self, role_id: str) -> bool:
        if self._is_held_by_us(role_id):
            return True
        lock_file = self.lock_dir / f"{role_id}.lock"
        if not lock_file.exists():
            return False
        return not self._is_expired(lock_file)

    def get_lock_info(self, role_id: str) -> Optional[Dict]:
        lock_file = self.lock_dir / f"{role_id}.lock"
        if not lock_file.exists():
            return None
        try:
            with open(lock_file, "r") as f:
                return json.load(f)
        except Exception:
            return None

    def get_all_locks(self) -> Dict[str, Dict]:
        locks = {}
        for lock_file in self.lock_dir.glob("*.lock"):
            role_id = lock_file.stem
            info = self.get_lock_info(role_id)
            if info:
                locks[role_id] = info
        return locks

    def cleanup_expired(self) -> int:
        cleaned = 0
        for lock_file in list(self.lock_dir.glob("*.lock")):
            if self._is_expired(lock_file):
                self._force_release(lock_file)
                cleaned += 1
        return cleaned

    def force_release_all(self) -> int:
        released = 0
        for role_id in list(self._held_locks.keys()):
            if self.release(role_id):
                released += 1
        for lock_file in list(self.lock_dir.glob("*.lock")):
            try:
                os.remove(str(lock_file))
                released += 1
            except OSError:
                pass
        return released

    def _is_held_by_us(self, role_id: str) -> bool:
        if role_id not in self._held_locks:
            return False
        lock_file = self.lock_dir / f"{role_id}.lock"
        if not lock_file.exists():
            self._held_locks.pop(role_id, None)
            return False
        return True

    def _is_expired(self, lock_file: Path) -> bool:
        if not lock_file.exists():
            return True
        try:
            with open(lock_file, "r") as f:
                data = json.load(f)
            acquired_at = datetime.fromisoformat(data["acquired_at"])
            timeout = data.get("timeout_seconds", self.default_timeout_seconds)
            return datetime.now() - acquired_at > timedelta(seconds=timeout)
        except Exception:
            return True

    def _force_release(self, lock_file: Path):
        try:
            os.remove(str(lock_file))
            role_id = lock_file.stem
            self._held_locks.pop(role_id, None)
            logger.warning(f"Force released expired lock: {lock_file.name}")
        except OSError:
            pass
