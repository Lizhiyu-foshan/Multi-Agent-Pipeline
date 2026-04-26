"""
Prompt Passing Session - Multi-round model interaction state management.

When a skill returns pending_model_request, the orchestrator needs to:
1. Save the execution state (pipeline, task, loop iteration)
2. Return the prompt to the caller (opencode agent)
3. Accept the model response
4. Resume execution from the saved state

This module provides:
- PromptPassingSession: Serializable state for a paused multi-round interaction
- SessionManager: Manages active sessions, persistence, expiry
- Integration hooks for PipelineOrchestrator and AgentLoop

Flow:
    orchestrator.advance(pipeline_id, result)
      -> skill returns pending_model_request
      -> SessionManager.save(session)
      -> return {action: "model_request", prompt: ..., session_id: ...}

    orchestrator.resume_model_request(session_id, model_response)
      -> SessionManager.load(session_id)
      -> skill.continue_execution(model_response, saved_context)
      -> may return another pending_model_request (loop)
      -> or final result -> orchestrator.advance continues
"""

import json
import logging
import os
import threading
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import PipelinePhase, PipelineRun, Task

logger = logging.getLogger(__name__)

SESSION_EXPIRY_SECONDS = 3600
SESSION_MAX_LIVE_SECONDS = 86400


@dataclass
class PromptPassingSession:
    session_id: str = ""
    pipeline_id: str = ""
    task_id: str = ""
    skill_name: str = ""
    action: str = ""
    phase: str = ""
    round_number: int = 0
    max_rounds: int = 10
    prompt: str = ""
    model_request_type: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    loop_state: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    completed: bool = False

    def __post_init__(self):
        if not self.session_id:
            self.session_id = f"sess_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now()
        self.last_active_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "pipeline_id": self.pipeline_id,
            "task_id": self.task_id,
            "skill_name": self.skill_name,
            "action": self.action,
            "phase": self.phase,
            "round_number": self.round_number,
            "max_rounds": self.max_rounds,
            "prompt": self.prompt,
            "model_request_type": self.model_request_type,
            "context": self.context,
            "loop_state": self.loop_state,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_active_at": self.last_active_at.isoformat()
            if self.last_active_at
            else None,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptPassingSession":
        s = cls(
            session_id=data.get("session_id", ""),
            pipeline_id=data.get("pipeline_id", ""),
            task_id=data.get("task_id", ""),
            skill_name=data.get("skill_name", ""),
            action=data.get("action", ""),
            phase=data.get("phase", ""),
            round_number=data.get("round_number", 0),
            max_rounds=data.get("max_rounds", 10),
            prompt=data.get("prompt", ""),
            model_request_type=data.get("model_request_type", ""),
            context=data.get("context", {}),
            loop_state=data.get("loop_state", {}),
            completed=data.get("completed", False),
        )
        if data.get("created_at"):
            s.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("last_active_at"):
            s.last_active_at = datetime.fromisoformat(data["last_active_at"])
        return s

    @property
    def is_expired(self) -> bool:
        if not self.last_active_at:
            return True
        elapsed = (datetime.now() - self.last_active_at).total_seconds()
        return elapsed > SESSION_EXPIRY_SECONDS

    @property
    def is_dead(self) -> bool:
        if not self.created_at:
            return True
        elapsed = (datetime.now() - self.created_at).total_seconds()
        return elapsed > SESSION_MAX_LIVE_SECONDS

    @property
    def rounds_remaining(self) -> int:
        return self.max_rounds - self.round_number


class SessionManager:
    """
    Manages PromptPassingSessions with persistence and cleanup.

    Sessions are stored in memory and optionally persisted to disk.
    Each session tracks a multi-round model interaction that was
    interrupted by a pending_model_request from a skill.
    """

    def __init__(self, state_dir: str = None, pipeline_state_fn: Callable = None):
        self._sessions: Dict[str, PromptPassingSession] = {}
        self._pipeline_sessions: Dict[str, str] = {}
        self._last_failure: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._state_dir = state_dir
        self._pipeline_state_fn = pipeline_state_fn
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
            self._load_from_disk()

    def save(self, session: PromptPassingSession) -> str:
        with self._lock:
            expected_dir = str(session.context.get("_sessions_state_dir", "")).strip()
            if expected_dir:
                expected_norm = os.path.abspath(expected_dir)
                current_norm = os.path.abspath(str(self._state_dir or ""))
                if expected_norm != current_norm:
                    self._last_failure[session.session_id] = {
                        "code": "SESSION_STATE_DIR_MISMATCH",
                        "message": "session directory mismatch",
                        "expected": expected_norm,
                        "actual": current_norm,
                        "timestamp": datetime.now().isoformat(),
                    }
                    raise ValueError(
                        f"Session state dir mismatch: expected={expected_norm} actual={current_norm}"
                    )

            session.last_active_at = datetime.now()

            old_sid = self._pipeline_sessions.get(session.pipeline_id)
            if old_sid and old_sid != session.session_id:
                self._last_failure[old_sid] = {
                    "code": "SESSION_SUPERSEDED",
                    "message": "session superseded by newer pipeline session",
                    "pipeline_id": session.pipeline_id,
                    "new_session_id": session.session_id,
                    "timestamp": datetime.now().isoformat(),
                }
                self.remove(old_sid)

            self._sessions[session.session_id] = session
            if session.pipeline_id:
                self._pipeline_sessions[session.pipeline_id] = session.session_id
            self._persist_to_disk(session)
            logger.info(
                f"Session {session.session_id} saved (round {session.round_number}/{session.max_rounds})"
            )
            return session.session_id

    def load(self, session_id: str) -> Optional[PromptPassingSession]:
        session, _ = self.load_with_diagnostics(session_id)
        return session

    def load_with_diagnostics(
        self, session_id: str
    ) -> Tuple[Optional[PromptPassingSession], Dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                failure = self._last_failure.get(session_id)
                if failure:
                    return None, {"status": "missing", "failure": dict(failure)}
                return None, {
                    "status": "missing",
                    "failure": {
                        "code": "SESSION_NOT_FOUND",
                        "message": "session not found",
                    },
                }
            if session.is_expired:
                if session.is_dead:
                    logger.warning(f"Session {session_id} dead (max live exceeded)")
                    self._last_failure[session_id] = {
                        "code": "SESSION_DEAD",
                        "message": "session exceeded max live time",
                        "pipeline_id": session.pipeline_id,
                        "last_active_at": session.last_active_at.isoformat()
                        if session.last_active_at
                        else None,
                        "timestamp": datetime.now().isoformat(),
                    }
                    self.remove(session_id)
                    return None, {
                        "status": "dead",
                        "failure": dict(self._last_failure[session_id]),
                    }
                if self._is_pipeline_running(session.pipeline_id):
                    session.last_active_at = datetime.now()
                    self._persist_to_disk(session)
                    logger.info(
                        f"Session {session_id} auto-renewed for running pipeline"
                    )
                    return session, {"status": "ok"}
                else:
                    logger.warning(
                        f"Session {session_id} expired (pipeline not running)"
                    )
                    self._last_failure[session_id] = {
                        "code": "SESSION_EXPIRED",
                        "message": "session expired while pipeline not running",
                        "pipeline_id": session.pipeline_id,
                        "last_active_at": session.last_active_at.isoformat()
                        if session.last_active_at
                        else None,
                        "timestamp": datetime.now().isoformat(),
                    }
                    self.remove(session_id)
                    return None, {
                        "status": "expired",
                        "failure": dict(self._last_failure[session_id]),
                    }
            return session, {"status": "ok"}

    def load_by_pipeline(self, pipeline_id: str) -> Optional[PromptPassingSession]:
        with self._lock:
            session_id = self._pipeline_sessions.get(pipeline_id)
            if not session_id:
                return None
            return self.load(session_id)

    def remove(self, session_id: str):
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session and session.pipeline_id:
                self._pipeline_sessions.pop(session.pipeline_id, None)
            if self._state_dir and session:
                fp = Path(self._state_dir) / f"{session_id}.json"
                if fp.exists():
                    os.unlink(fp)

    def complete_session(self, session_id: str) -> Optional[PromptPassingSession]:
        with self._lock:
            session = self.load(session_id)
            if session:
                session.completed = True
                session.last_active_at = datetime.now()
                self._last_failure[session_id] = {
                    "code": "SESSION_COMPLETED",
                    "message": "session already completed",
                    "pipeline_id": session.pipeline_id,
                    "timestamp": datetime.now().isoformat(),
                }
                self.remove(session_id)
            return session

    def list_active(self) -> List[PromptPassingSession]:
        with self._lock:
            return [
                s
                for s in self._sessions.values()
                if not s.completed and not s.is_expired
            ]

    def cleanup_expired(self) -> int:
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s.is_expired]
            for sid in expired:
                self.remove(sid)
            return len(expired)

    def touch(self, session_id: str):
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.last_active_at = datetime.now()
                self._persist_to_disk(session)

    def get_last_failure(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            failure = self._last_failure.get(session_id)
            return dict(failure) if failure else None

    def _is_pipeline_running(self, pipeline_id: str) -> bool:
        if not pipeline_id:
            return False
        if self._pipeline_state_fn:
            try:
                state = self._pipeline_state_fn(pipeline_id)
                return state == "running"
            except Exception:
                return False
        return True

    def _persist_to_disk(self, session: PromptPassingSession):
        if not self._state_dir:
            return
        try:
            fp = Path(self._state_dir) / f"{session.session_id}.json"
            fd, tmp = tempfile.mkstemp(
                dir=str(Path(self._state_dir)), suffix=".json.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(session.to_dict(), f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, str(fp))
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except Exception as e:
            logger.error(f"Failed to persist session {session.session_id}: {e}")

    def _load_from_disk(self):
        if not self._state_dir:
            return
        state_path = Path(self._state_dir)
        for fp in state_path.glob("sess_*.json"):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session = PromptPassingSession.from_dict(data)
                if not session.is_expired and not session.completed:
                    self._sessions[session.session_id] = session
                    if session.pipeline_id:
                        self._pipeline_sessions[session.pipeline_id] = (
                            session.session_id
                        )
            except Exception as e:
                logger.debug(f"Failed to load session file {fp}: {e}")


def create_session_from_pending(
    pending_request: Dict[str, Any],
    pipeline_id: str = "",
    task_id: str = "",
    skill_name: str = "",
    action: str = "",
    phase: str = "",
    context: Dict[str, Any] = None,
    loop_state: Dict[str, Any] = None,
    max_rounds: int = 10,
) -> PromptPassingSession:
    """
    Factory: create a session from a pending_model_request returned by a skill.

    Args:
        pending_request: The pending_model_request dict from skill.execute()
        pipeline_id: Current pipeline
        task_id: Current task
        skill_name: Skill that generated the request
        action: Action being executed
        phase: Pipeline phase
        context: Full execution context (for resumption)
        loop_state: AgentLoop state if applicable
        max_rounds: Maximum model interaction rounds

    Returns:
        PromptPassingSession ready for SessionManager.save()
    """
    prompt = pending_request.get("prompt", "")
    model_type = pending_request.get("type", "")

    return PromptPassingSession(
        pipeline_id=pipeline_id,
        task_id=task_id,
        skill_name=skill_name,
        action=action,
        phase=phase,
        round_number=1,
        max_rounds=max_rounds,
        prompt=prompt,
        model_request_type=model_type,
        context=context or {},
        loop_state=loop_state or {},
    )
