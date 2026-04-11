"""
HashlineEditTool — Content-hash-verified file editing for agents.

Solves the Harness Problem: most agent failures aren't the model's fault,
they're the edit tool's. When a model reads a file, edits the wrong lines
because the file changed, or produces stale-line errors — this tool prevents it.

Inspired by OMO's Hashline and oh-my-pi's LINE#ID approach.

How it works:
    1. Agent reads file via read_file() → each line annotated with hash:
         1#a1b2c3| def hello():
         2#d4e5f6|     return "world"
    2. Agent sends edit referencing line hashes
    3. Tool validates hash matches current file content BEFORE applying
    4. If hash mismatch → edit REJECTED (file changed since last read)
    5. If hash matches → atomic write (temp file + os.replace)

Edit operations:
    - replace: Replace lines matching old hashes with new content
    - insert_after: Insert new lines after a line matching a hash
    - insert_before: Insert new lines before a line matching a hash
    - delete: Delete lines matching hashes
    - multi_edit: Batch of the above in one atomic operation

Usage:
    tool = HashlineEditTool()

    # Read
    content = tool.read_file("src/main.py")

    # Edit (agent provides hash-based reference)
    result = tool.replace_lines("src/main.py", [
        {"line_hash": "a1b2c3", "line_number": 1, "old_content": "def hello():", "new_content": "def hello(name):"}
    ])

    # Multi-edit (atomic batch)
    result = tool.multi_edit("src/main.py", [
        {"op": "replace", "line_hash": "a1b2c3", "line_number": 1, "new_content": "def hello(name):"},
        {"op": "insert_after", "line_hash": "d4e5f6", "line_number": 2, "new_content": '    print(f"Hello {name}")'},
    ])
"""

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HASH_LENGTH = 6


@dataclass
class AnnotatedLine:
    line_number: int
    content: str
    content_hash: str
    raw_content: str

    def to_annotated(self) -> str:
        return f"{self.line_number}#{self.content_hash}|{self.raw_content}"


@dataclass
class EditOp:
    op: str
    line_hash: str
    line_number: int
    new_content: str = ""
    old_content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "op": self.op,
            "line_hash": self.line_hash,
            "line_number": self.line_number,
        }
        if self.new_content:
            d["new_content"] = self.new_content
        if self.old_content:
            d["old_content"] = self.old_content
        return d


@dataclass
class EditResult:
    success: bool
    file_path: str
    operations_applied: int = 0
    operations_rejected: int = 0
    rejected_edits: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    backup_path: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "file_path": self.file_path,
            "operations_applied": self.operations_applied,
            "operations_rejected": self.operations_rejected,
            "rejected_edits": self.rejected_edits,
            "error": self.error,
            "backup_path": self.backup_path,
            "timestamp": self.timestamp,
        }


def _compute_hash(line_content: str) -> str:
    raw = line_content.rstrip("\n").rstrip("\r")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:HASH_LENGTH]


class HashlineEditTool:
    """
    Content-hash-verified file editing.

    Prevents stale-line errors by requiring every edit to reference
    the hash of the line it intends to change. If the file was modified
    since the agent last read it, the hash won't match and the edit
    is rejected before any changes are applied.
    """

    def __init__(self, backup_dir: str = None):
        self._cache: Dict[str, List[AnnotatedLine]] = {}
        self._backup_dir = backup_dir
        if backup_dir:
            os.makedirs(backup_dir, exist_ok=True)

    def read_file(self, file_path: str) -> Dict[str, Any]:
        """
        Read a file and return annotated content with line hashes.

        Each line is annotated as: LINE_NUMBER#HASH|original_content

        The hash is computed from the stripped line content (no trailing newline).
        The cache is updated so subsequent edits can validate against it.
        """
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "content": "",
                "lines": [],
            }

        try:
            with open(str(path), "r", encoding="utf-8", errors="replace") as f:
                raw_lines = f.readlines()
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to read: {e}",
                "content": "",
                "lines": [],
            }

        annotated = []
        for i, raw in enumerate(raw_lines, 1):
            content = raw.rstrip("\n").rstrip("\r")
            h = _compute_hash(content)
            annotated.append(
                AnnotatedLine(
                    line_number=i,
                    content=content,
                    content_hash=h,
                    raw_content=raw,
                )
            )

        self._cache[str(path.resolve())] = list(annotated)

        annotated_text = "\n".join(al.to_annotated() for al in annotated)

        return {
            "success": True,
            "file_path": str(path),
            "content": annotated_text,
            "lines": [al.to_annotated() for al in annotated],
            "total_lines": len(annotated),
        }

    def replace_lines(self, file_path: str, edits: List[Dict[str, Any]]) -> EditResult:
        """
        Replace lines verified by content hash.

        Each edit dict:
            line_hash: The hash from the annotated read
            line_number: Expected line number (for extra safety)
            old_content: Optional — if provided, also verified
            new_content: The replacement content

        All edits are applied atomically (all-or-nothing).
        """
        ops = []
        for e in edits:
            ops.append(
                EditOp(
                    op="replace",
                    line_hash=e.get("line_hash", ""),
                    line_number=e.get("line_number", 0),
                    new_content=e.get("new_content", ""),
                    old_content=e.get("old_content", ""),
                )
            )
        return self._apply_edits(file_path, ops)

    def insert_after(
        self,
        file_path: str,
        line_hash: str,
        line_number: int,
        new_content: str,
    ) -> EditResult:
        return self._apply_edits(
            file_path,
            [
                EditOp(
                    op="insert_after",
                    line_hash=line_hash,
                    line_number=line_number,
                    new_content=new_content,
                )
            ],
        )

    def insert_before(
        self,
        file_path: str,
        line_hash: str,
        line_number: int,
        new_content: str,
    ) -> EditResult:
        return self._apply_edits(
            file_path,
            [
                EditOp(
                    op="insert_before",
                    line_hash=line_hash,
                    line_number=line_number,
                    new_content=new_content,
                )
            ],
        )

    def delete_lines(
        self, file_path: str, deletions: List[Dict[str, Any]]
    ) -> EditResult:
        ops = []
        for d in deletions:
            ops.append(
                EditOp(
                    op="delete",
                    line_hash=d.get("line_hash", ""),
                    line_number=d.get("line_number", 0),
                )
            )
        return self._apply_edits(file_path, ops)

    def multi_edit(
        self, file_path: str, operations: List[Dict[str, Any]]
    ) -> EditResult:
        ops = []
        for o in operations:
            ops.append(
                EditOp(
                    op=o.get("op", "replace"),
                    line_hash=o.get("line_hash", ""),
                    line_number=o.get("line_number", 0),
                    new_content=o.get("new_content", ""),
                    old_content=o.get("old_content", ""),
                )
            )
        return self._apply_edits(file_path, ops)

    def _apply_edits(self, file_path: str, ops: List[EditOp]) -> EditResult:
        path = Path(file_path).resolve()
        path_str = str(path)

        if not path.exists():
            return EditResult(
                success=False, file_path=path_str, error=f"File not found: {path_str}"
            )

        try:
            with open(str(path), "r", encoding="utf-8", errors="replace") as f:
                current_lines = f.readlines()
        except Exception as e:
            return EditResult(
                success=False, file_path=path_str, error=f"Read failed: {e}"
            )

        rejected = []
        validated = []

        for op in ops:
            ln = op.line_number
            if ln < 1 or ln > len(current_lines):
                rejected.append(
                    {
                        "op": op.op,
                        "line_hash": op.line_hash,
                        "line_number": ln,
                        "reason": "line_number_out_of_range",
                    }
                )
                continue

            actual_line = current_lines[ln - 1].rstrip("\n").rstrip("\r")
            actual_hash = _compute_hash(actual_line)

            if actual_hash != op.line_hash:
                rejected.append(
                    {
                        "op": op.op,
                        "line_hash": op.line_hash,
                        "expected_hash": op.line_hash,
                        "actual_hash": actual_hash,
                        "line_number": ln,
                        "reason": "hash_mismatch_file_changed_since_read",
                    }
                )
                continue

            if op.old_content:
                expected = op.old_content.rstrip("\n").rstrip("\r")
                if actual_line != expected:
                    rejected.append(
                        {
                            "op": op.op,
                            "line_hash": op.line_hash,
                            "line_number": ln,
                            "reason": "old_content_mismatch",
                        }
                    )
                    continue

            validated.append(op)

        if rejected and not validated:
            return EditResult(
                success=False,
                file_path=path_str,
                operations_applied=0,
                operations_rejected=len(rejected),
                rejected_edits=rejected,
                error="All edits rejected — file has changed since last read. "
                "Re-read the file and retry.",
            )

        new_lines = list(current_lines)
        offset = 0

        sorted_ops = sorted(validated, key=lambda o: o.line_number)

        for op in sorted_ops:
            idx = op.line_number - 1 + offset

            if op.op == "replace":
                new_content = op.new_content
                if not new_content.endswith("\n"):
                    new_content += "\n"
                new_lines[idx] = new_content

            elif op.op == "delete":
                if 0 <= idx < len(new_lines):
                    new_lines.pop(idx)
                    offset -= 1

            elif op.op == "insert_after":
                insert_lines = op.new_content.split("\n")
                insert_content = [l + "\n" for l in insert_lines]
                for j, il in enumerate(insert_content):
                    new_lines.insert(idx + 1 + j, il)
                offset += len(insert_content)

            elif op.op == "insert_before":
                insert_lines = op.new_content.split("\n")
                insert_content = [l + "\n" for l in insert_lines]
                for j, il in enumerate(insert_content):
                    new_lines.insert(idx + j, il)
                offset += len(insert_content)

        backup_path = self._create_backup(path, current_lines)

        try:
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".hashline.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                os.replace(tmp, str(path))
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except Exception as e:
            return EditResult(
                success=False,
                file_path=path_str,
                error=f"Write failed: {e}",
                backup_path=backup_path,
            )

        self._cache.pop(path_str, None)

        logger.info(
            f"HashlineEdit: {len(validated)} ops applied, "
            f"{len(rejected)} rejected for {path_str}"
        )

        return EditResult(
            success=True,
            file_path=path_str,
            operations_applied=len(validated),
            operations_rejected=len(rejected),
            rejected_edits=rejected,
            backup_path=backup_path,
        )

    def _create_backup(self, path: Path, original_lines: List[str]) -> str:
        if not self._backup_dir:
            return ""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_name = path.name.replace(".", "_")
            backup_name = f"{safe_name}.bak.{ts}"
            backup_path = Path(self._backup_dir) / backup_name
            with open(str(backup_path), "w", encoding="utf-8") as f:
                f.writelines(original_lines)
            return str(backup_path)
        except Exception as e:
            logger.debug(f"Backup failed: {e}")
            return ""

    def restore_backup(self, backup_path: str, target_path: str) -> Dict[str, Any]:
        bp = Path(backup_path)
        tp = Path(target_path)
        if not bp.exists():
            return {"success": False, "error": f"Backup not found: {backup_path}"}
        try:
            with open(str(bp), "r", encoding="utf-8") as f:
                content = f.read()
            with open(str(tp), "w", encoding="utf-8") as f:
                f.write(content)
            self._cache.pop(str(tp.resolve()), None)
            return {"success": True, "restored_from": str(bp)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_diff_preview(
        self, file_path: str, operations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Preview what changes would be made without actually applying them.
        Returns unified diff-style output.
        """
        path = Path(file_path).resolve()
        if not path.exists():
            return {"success": False, "error": "File not found"}

        try:
            with open(str(path), "r", encoding="utf-8", errors="replace") as f:
                current = f.readlines()
        except Exception as e:
            return {"success": False, "error": str(e)}

        ops = [
            EditOp(
                op=o.get("op", "replace"),
                line_hash=o.get("line_hash", ""),
                line_number=o.get("line_number", 0),
                new_content=o.get("new_content", ""),
                old_content=o.get("old_content", ""),
            )
            for o in operations
        ]

        lines_before = [l.rstrip("\n").rstrip("\r") for l in current]

        valid = []
        invalid = 0
        for op in ops:
            ln = op.line_number
            if ln < 1 or ln > len(current):
                invalid += 1
                continue
            actual_hash = _compute_hash(current[ln - 1].rstrip("\n").rstrip("\r"))
            if actual_hash != op.line_hash:
                invalid += 1
                continue
            valid.append(op)

        diff_lines = []
        for op in sorted(valid, key=lambda o: o.line_number):
            ln = op.line_number
            if op.op == "replace":
                old = current[ln - 1].rstrip("\n").rstrip("\r")
                diff_lines.append(f"  Line {ln}#{op.line_hash}:")
                diff_lines.append(f"  - {old}")
                diff_lines.append(f"  + {op.new_content}")
            elif op.op == "delete":
                old = current[ln - 1].rstrip("\n").rstrip("\r")
                diff_lines.append(f"  Line {ln}#{op.line_hash}:")
                diff_lines.append(f"  - {old}")
            elif op.op in ("insert_after", "insert_before"):
                where = "after" if op.op == "insert_after" else "before"
                diff_lines.append(f"  Insert {where} Line {ln}#{op.line_hash}:")
                for il in op.new_content.split("\n"):
                    diff_lines.append(f"  + {il}")

        return {
            "success": True,
            "file_path": str(path),
            "valid_ops": len(valid),
            "invalid_ops": invalid,
            "diff": "\n".join(diff_lines),
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "cached_files": len(self._cache),
            "cached_file_paths": list(self._cache.keys()),
            "backup_dir": self._backup_dir or "none",
            "hash_length": HASH_LENGTH,
        }

    def clear_cache(self, file_path: str = None):
        if file_path:
            self._cache.pop(str(Path(file_path).resolve()), None)
        else:
            self._cache.clear()
