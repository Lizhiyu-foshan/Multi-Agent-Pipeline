"""
Git Worktree Isolation - Isolated workspaces for parallel subagent execution.

When multiple subagents execute in parallel, they need isolated workspaces
to avoid file conflicts. Git worktrees provide lightweight clones that share
the .git database but have independent working directories.

Flow:
    1. Pipeline dispatches N parallel subagents
    2. WorktreeManager.create_worktree(task_id) -> worktree_path
    3. Subagent executes in worktree_path (isolated filesystem)
    4. Subagent completes, worktree has changes
    5. WorktreeManager.merge_worktree(task_id) -> merges back to main
    6. WorktreeManager.cleanup_worktree(task_id) -> removes worktree

Windows considerations:
    - Use subprocess for git commands (no fcntl)
    - Atomic branch creation with unique names
    - Force cleanup on orphaned worktrees
"""

import logging
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WORKTREE_PREFIX = "agent-"


@dataclass
class WorktreeInfo:
    task_id: str = ""
    branch_name: str = ""
    worktree_path: str = ""
    created_at: Optional[datetime] = None
    status: str = "active"
    has_changes: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "branch_name": self.branch_name,
            "worktree_path": self.worktree_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "status": self.status,
            "has_changes": self.has_changes,
        }


class WorktreeManager:
    """
    Manages git worktrees for isolated subagent execution.

    Each worktree gets its own branch and working directory.
    After subagent completes, changes can be merged back to the
    main branch or reviewed as a PR.
    """

    def __init__(self, repo_root: str = None):
        self.repo_root = Path(repo_root) if repo_root else Path.cwd()
        self._worktrees: Dict[str, WorktreeInfo] = {}
        self._worktree_base = self.repo_root / ".worktrees"
        self._git_available = self._check_git()

    def _check_git(self) -> bool:
        git_dir = self.repo_root / ".git"
        if not git_dir.exists():
            return False
        try:
            result = self._run_git(["rev-parse", "--git-dir"])
            return result.returncode == 0
        except Exception:
            return False

    def create_worktree(
        self,
        task_id: str,
        base_branch: str = None,
        branch_suffix: str = "",
    ) -> Dict[str, Any]:
        """
        Create an isolated worktree for a task.

        Args:
            task_id: Task identifier for tracking
            base_branch: Branch to base from (default: HEAD)
            branch_suffix: Optional suffix for branch name

        Returns:
            Dict with worktree_path, branch_name, success
        """
        if not self._git_available:
            return {
                "success": False,
                "error": "Git not available or not a git repo",
                "worktree_path": str(self.repo_root),
            }

        if task_id in self._worktrees:
            existing = self._worktrees[task_id]
            if existing.status == "active" and Path(existing.worktree_path).exists():
                return {
                    "success": True,
                    "worktree_path": existing.worktree_path,
                    "branch_name": existing.branch_name,
                    "reused": True,
                }

        short_id = task_id.replace("task_", "")[:8]
        uid = uuid.uuid4().hex[:6]
        branch_name = f"{WORKTREE_PREFIX}{short_id}-{uid}"
        if branch_suffix:
            branch_name += f"-{branch_suffix}"

        worktree_path = self._worktree_base / branch_name

        try:
            os.makedirs(str(self._worktree_base), exist_ok=True)

            head_result = self._run_git(["rev-parse", "--verify", "HEAD"])
            if head_result.returncode != 0:
                return {
                    "success": False,
                    "error": "Cannot create worktree: repository has no commits. "
                    "Make an initial commit first.",
                }

            cmd = ["worktree", "add", str(worktree_path), "-b", branch_name]
            if base_branch:
                cmd.append(base_branch)
            else:
                cmd.append("HEAD")

            result = self._run_git(cmd)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                if "already exists" in stderr:
                    result = self._run_git(
                        [
                            "worktree",
                            "add",
                            str(worktree_path),
                            "-b",
                            branch_name,
                            "--detach",
                        ]
                    )
                if result.returncode != 0:
                    return {
                        "success": False,
                        "error": f"git worktree add failed: {result.stderr.strip()}",
                    }

            info = WorktreeInfo(
                task_id=task_id,
                branch_name=branch_name,
                worktree_path=str(worktree_path),
                created_at=datetime.now(),
                status="active",
            )
            self._worktrees[task_id] = info

            logger.info(f"Created worktree for {task_id}: {worktree_path}")
            return {
                "success": True,
                "worktree_path": str(worktree_path),
                "branch_name": branch_name,
                "task_id": task_id,
            }

        except Exception as e:
            logger.error(f"Failed to create worktree for {task_id}: {e}")
            return {"success": False, "error": str(e)}

    def get_worktree(self, task_id: str) -> Optional[WorktreeInfo]:
        return self._worktrees.get(task_id)

    def has_changes(self, task_id: str) -> bool:
        info = self._worktrees.get(task_id)
        if not info or not Path(info.worktree_path).exists():
            return False
        result = self._run_git(
            ["status", "--porcelain"],
            cwd=info.worktree_path,
        )
        has = bool(result.stdout.strip())
        info.has_changes = has
        return has

    def stage_and_commit(
        self,
        task_id: str,
        message: str = "",
        files: List[str] = None,
    ) -> Dict[str, Any]:
        info = self._worktrees.get(task_id)
        if not info:
            return {"success": False, "error": f"No worktree for task {task_id}"}
        if not Path(info.worktree_path).exists():
            return {"success": False, "error": "Worktree path does not exist"}

        if not message:
            message = f"[agent] Task {task_id} completed"

        if files:
            for f in files:
                self._run_git(["add", f], cwd=info.worktree_path)
        else:
            self._run_git(["add", "-A"], cwd=info.worktree_path)

        result = self._run_git(
            ["commit", "-m", message, "--no-gpg-sign"],
            cwd=info.worktree_path,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            return {
                "success": False,
                "error": f"Commit failed: {result.stderr.strip()}",
            }

        return {
            "success": True,
            "task_id": task_id,
            "branch_name": info.branch_name,
            "commit_message": message,
        }

    def merge_worktree(
        self,
        task_id: str,
        target_branch: str = None,
        strategy: str = "merge",
    ) -> Dict[str, Any]:
        """
        Merge worktree branch back to target (main or current).

        Args:
            task_id: Task whose worktree to merge
            target_branch: Branch to merge into (default: current branch)
            strategy: 'merge', 'squash', or 'rebase'
        """
        info = self._worktrees.get(task_id)
        if not info:
            return {"success": False, "error": f"No worktree for task {task_id}"}

        current_branch = target_branch
        if not current_branch:
            result = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            current_branch = result.stdout.strip() or "main"

        if strategy == "squash":
            self._run_git(["merge", "--squash", info.branch_name])
            result = self._run_git(
                [
                    "commit",
                    "-m",
                    f"[agent] Squash merge from {info.branch_name}",
                    "--no-gpg-sign",
                ]
            )
        elif strategy == "rebase":
            result = self._run_git(["rebase", info.branch_name])
        else:
            result = self._run_git(["merge", info.branch_name, "--no-edit"])

        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Merge failed: {result.stderr.strip()}",
                "conflicts": True,
                "branch_name": info.branch_name,
            }

        info.status = "merged"
        return {
            "success": True,
            "task_id": task_id,
            "merged_to": current_branch,
            "branch_name": info.branch_name,
        }

    def cleanup_worktree(self, task_id: str, force: bool = False) -> Dict[str, Any]:
        info = self._worktrees.get(task_id)
        if not info:
            return {"success": True, "message": "No worktree to cleanup"}

        wt_path = Path(info.worktree_path)

        if wt_path.exists():
            result = self._run_git(
                ["worktree", "remove", str(wt_path), "--force" if force else ""]
            )
            if result.returncode != 0 and not force:
                if self.has_changes(task_id):
                    result = self._run_git(
                        ["worktree", "remove", str(wt_path), "--force"]
                    )

        try:
            self._run_git(["branch", "-d", info.branch_name])
        except Exception:
            pass

        info.status = "cleaned"
        del self._worktrees[task_id]
        logger.info(f"Cleaned up worktree for {task_id}")
        return {"success": True, "task_id": task_id}

    def cleanup_all(self, force: bool = True) -> Dict[str, Any]:
        cleaned = []
        for task_id in list(self._worktrees.keys()):
            result = self.cleanup_worktree(task_id, force=force)
            cleaned.append(result)

        try:
            if self._worktree_base.exists():
                import shutil

                shutil.rmtree(str(self._worktree_base), ignore_errors=True)
        except Exception:
            pass

        return {
            "success": True,
            "cleaned_count": len(cleaned),
            "cleaned": cleaned,
        }

    def list_worktrees(self) -> List[Dict[str, Any]]:
        result = self._run_git(["worktree", "list", "--porcelain"])
        worktrees = []
        if result.returncode == 0:
            current = {}
            for line in result.stdout.strip().splitlines():
                if " " in line:
                    key, value = line.split(" ", 1)
                    current[key] = value
                elif line.strip():
                    if current:
                        worktrees.append(current)
                    current = {}
            if current:
                worktrees.append(current)
        return worktrees

    def get_status(self) -> Dict[str, Any]:
        return {
            "git_available": self._git_available,
            "repo_root": str(self.repo_root),
            "active_worktrees": len(
                [w for w in self._worktrees.values() if w.status == "active"]
            ),
            "worktree_base": str(self._worktree_base),
            "tasks": {tid: info.to_dict() for tid, info in self._worktrees.items()},
        }

    def _run_git(
        self,
        args: List[str],
        cwd: str = None,
    ) -> subprocess.CompletedProcess:
        cmd = ["git"] + [a for a in args if a]
        return subprocess.run(
            cmd,
            cwd=cwd or str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
