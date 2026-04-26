"""
Change control manager for existing projects.

Provides:
- pre-merge/update risk assessment
- code contamination assessment
- compressed historical backups
- version record management
- merge/update operation orchestration
"""

import json
import os
import re
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .registry import ProjectRegistry


class ChangeControlManager:
    def __init__(self, state_dir: str = None, registry: ProjectRegistry = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = Path(state_dir)
        self._registry = registry or ProjectRegistry(state_dir=state_dir)
        self._global_dir = self._state_dir / "global"
        self._risk_file = self._global_dir / "risk_reports.json"
        self._contam_file = self._global_dir / "contamination_reports.json"
        self._version_file = self._global_dir / "version_records.json"
        self._backup_dir = self._global_dir / "version_backups"

    def _now(self) -> str:
        return datetime.now().isoformat()

    def _mkid(self, prefix: str) -> str:
        return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:22]}"

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_json(self, path: Path, data: Dict[str, Any]):
        os.makedirs(str(path.parent), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def assess_risk(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        files = list(ctx.get("files", []))
        drift_severity = str(ctx.get("drift_severity", "")).lower()
        score = 10
        reasons: List[str] = []

        if len(files) >= 20:
            score += 20
            reasons.append("Large change set (20+ files)")
        elif len(files) >= 8:
            score += 10
            reasons.append("Medium change set (8+ files)")

        high_risk_paths = re.compile(
            r"(auth|security|payment|deploy|migration|schema|database|gateway)",
            re.IGNORECASE,
        )
        for f in files:
            if high_risk_paths.search(str(f)):
                score += 15
                reasons.append(f"High-risk path touched: {f}")
                break

        has_test_change = any("test" in str(f).lower() for f in files)
        has_code_change = any(str(f).endswith((".py", ".js", ".ts", ".tsx")) for f in files)
        if has_code_change and not has_test_change:
            score += 12
            reasons.append("Code changes without test changes")

        if drift_severity == "critical":
            score += 35
            reasons.append("Critical drift severity")
        elif drift_severity == "high":
            score += 20
            reasons.append("High drift severity")

        score = min(score, 100)
        if score <= 25:
            level = "low"
        elif score <= 50:
            level = "medium"
        elif score <= 75:
            level = "high"
        else:
            level = "critical"

        recommendations = []
        if level in ("high", "critical"):
            recommendations.append("Require explicit approval before merge/update")
            recommendations.append("Run full regression and contamination checks")
        if not has_test_change and has_code_change:
            recommendations.append("Add or update tests before merge")

        report_id = self._mkid("risk")
        report = {
            "report_id": report_id,
            "project_id": project_id,
            "score": score,
            "level": level,
            "reasons": reasons,
            "files": files,
            "recommendations": recommendations,
            "created_at": self._now(),
        }

        all_reports = self._load_json(self._risk_file)
        all_reports[report_id] = report
        self._save_json(self._risk_file, all_reports)

        return {"success": True, "action": "risk_assess", "artifacts": report}

    def assess_contamination(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        files = list(ctx.get("files", []))
        source_path = str(ctx.get("source_path", ""))
        file_contents = dict(ctx.get("file_contents", {}))

        secret_re = re.compile(
            r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{6,}['\"]"
        )
        merge_marker_re = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
        issues: List[Dict[str, Any]] = []

        def _read(path: str) -> str:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception:
                return ""

        for f in files:
            content = file_contents.get(f, "")
            if not content and source_path:
                content = _read(os.path.join(source_path, f))
            if not content:
                continue

            if secret_re.search(content):
                issues.append({
                    "file": f,
                    "severity": "critical",
                    "type": "secret_leak",
                    "message": "Potential hard-coded secret detected",
                })
            if merge_marker_re.search(content):
                issues.append({
                    "file": f,
                    "severity": "high",
                    "type": "merge_conflict_marker",
                    "message": "Unresolved merge conflict marker detected",
                })
            if "console.log(" in content or "print(" in content:
                issues.append({
                    "file": f,
                    "severity": "low",
                    "type": "debug_artifact",
                    "message": "Debug logging statements detected",
                })

        severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        top = "none"
        if issues:
            top = max(issues, key=lambda i: severity_order.get(i.get("severity", "low"), 1)).get("severity", "low")

        status = "pass" if top in ("none", "low") else "blocked"
        report_id = self._mkid("contam")
        report = {
            "report_id": report_id,
            "project_id": project_id,
            "status": status,
            "top_severity": top,
            "issues": issues,
            "files_checked": files,
            "created_at": self._now(),
        }

        all_reports = self._load_json(self._contam_file)
        all_reports[report_id] = report
        self._save_json(self._contam_file, all_reports)

        return {"success": status == "pass", "action": "contamination_check", "artifacts": report}

    def create_compressed_backup(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        source_path = str(ctx.get("source_path", "")).strip()
        if not source_path:
            proj = self._registry.get(project_id)
            if proj.get("success"):
                source_path = str(proj["artifacts"].get("target_path", ""))

        if not source_path or not os.path.exists(source_path):
            return {"success": False, "action": "version_backup", "error": "source_path not found"}

        include_files = list(ctx.get("files", []))
        version_id = str(ctx.get("version_id", self._mkid("ver")))

        proj_dir = self._backup_dir / project_id
        os.makedirs(str(proj_dir), exist_ok=True)
        backup_path = proj_dir / f"{version_id}.zip"

        file_count = 0
        with zipfile.ZipFile(str(backup_path), "w", zipfile.ZIP_DEFLATED) as zf:
            if include_files:
                for rel in include_files:
                    absf = os.path.join(source_path, rel)
                    if os.path.exists(absf) and os.path.isfile(absf):
                        zf.write(absf, arcname=rel)
                        file_count += 1
            else:
                for root, dirs, files in os.walk(source_path):
                    dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".venv")]
                    for fn in files:
                        absf = os.path.join(root, fn)
                        rel = os.path.relpath(absf, source_path)
                        zf.write(absf, arcname=rel)
                        file_count += 1

        return {
            "success": True,
            "action": "version_backup",
            "artifacts": {
                "project_id": project_id,
                "version_id": version_id,
                "backup_path": str(backup_path),
                "compressed": True,
                "file_count": file_count,
                "created_at": self._now(),
            },
        }

    def record_version(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        version_id = str(ctx.get("version_id", self._mkid("ver")))
        record_id = self._mkid("vrec")
        record = {
            "record_id": record_id,
            "project_id": project_id,
            "version_id": version_id,
            "change_summary": str(ctx.get("change_summary", "")),
            "files": list(ctx.get("files", [])),
            "operation": str(ctx.get("operation", "manual")),
            "risk_report_id": str(ctx.get("risk_report_id", "")),
            "contamination_report_id": str(ctx.get("contamination_report_id", "")),
            "backup_path": str(ctx.get("backup_path", "")),
            "status": str(ctx.get("status", "recorded")),
            "created_at": self._now(),
        }

        all_records = self._load_json(self._version_file)
        all_records[record_id] = record
        self._save_json(self._version_file, all_records)

        self._registry.update(project_id, {"metadata": {**self._registry.get(project_id).get("artifacts", {}).get("metadata", {}), "active_version": version_id}})

        return {"success": True, "action": "version_record", "artifacts": record}

    def list_versions(self, project_id: str) -> Dict[str, Any]:
        all_records = self._load_json(self._version_file)
        rows = [r for r in all_records.values() if r.get("project_id") == project_id]
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return {
            "success": True,
            "action": "version_list",
            "artifacts": {
                "project_id": project_id,
                "total": len(rows),
                "versions": rows,
            },
        }

    def merge_update(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        operation = str(ctx.get("operation", "merge")).lower()
        dry_run = bool(ctx.get("dry_run", True))

        repo_path = str(ctx.get("repo_path", "")).strip()
        if not repo_path:
            proj = self._registry.get(project_id)
            if proj.get("success"):
                repo_path = str(proj["artifacts"].get("target_path", ""))

        risk_level = str(ctx.get("risk_level", "")).lower()
        allow_high_risk = bool(ctx.get("allow_high_risk", False))
        if risk_level in ("high", "critical") and not allow_high_risk:
            return {
                "success": False,
                "action": "merge_update",
                "error": f"Blocked by risk level: {risk_level}",
            }

        if operation not in ("merge", "update", "pull"):
            return {"success": False, "action": "merge_update", "error": f"Unsupported operation: {operation}"}

        if operation == "merge":
            from_branch = str(ctx.get("from_branch", "feature/current"))
            cmd = ["git", "merge", from_branch]
        else:
            cmd = ["git", "pull", "--ff-only"]

        if dry_run:
            return {
                "success": True,
                "action": "merge_update",
                "artifacts": {
                    "operation": operation,
                    "dry_run": True,
                    "repo_path": repo_path,
                    "command": cmd,
                    "status": "planned",
                },
            }

        if not repo_path or not os.path.exists(repo_path):
            return {"success": False, "action": "merge_update", "error": "repo_path not found"}

        try:
            run = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True, timeout=120)
            ok = run.returncode == 0
            return {
                "success": ok,
                "action": "merge_update",
                "artifacts": {
                    "operation": operation,
                    "dry_run": False,
                    "repo_path": repo_path,
                    "command": cmd,
                    "status": "applied" if ok else "failed",
                    "stdout": run.stdout[-2000:],
                    "stderr": run.stderr[-2000:],
                    "returncode": run.returncode,
                },
                "error": "" if ok else f"git command failed: {run.returncode}",
            }
        except Exception as e:
            return {"success": False, "action": "merge_update", "error": str(e)}

    def run_change_flow(self, project_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        files = list(ctx.get("files", []))
        source_path = str(ctx.get("source_path", "")).strip()
        drift_severity = str(ctx.get("drift_severity", "")).strip().lower()
        allow_high_risk = bool(ctx.get("allow_high_risk", False))
        create_backup = bool(ctx.get("create_backup", True))
        record_version = bool(ctx.get("record_version", True))
        skip_gate = bool(ctx.get("skip_gate", False))

        risk = self.assess_risk(
            project_id,
            {
                "files": files,
                "drift_severity": drift_severity,
            },
        )
        risk_art = risk.get("artifacts", {})
        risk_level = str(risk_art.get("level", "low"))

        contamination = self.assess_contamination(
            project_id,
            {
                "files": files,
                "source_path": source_path,
                "file_contents": dict(ctx.get("file_contents", {})),
            },
        )
        contamination_art = contamination.get("artifacts", {})

        gate_result: Dict[str, Any] = {
            "success": True,
            "action": "evaluate_gates",
            "artifacts": {
                "decision": "pass",
                "details": {},
            },
        }
        if not skip_gate:
            from .gates import GateEvaluator

            evaluator = GateEvaluator(state_dir=str(self._state_dir))
            gate_result = evaluator.evaluate(
                project_id,
                {
                    "risk_result": risk,
                    "contamination_result": contamination,
                    "drift_severity": drift_severity,
                    "allow_high_risk": allow_high_risk,
                    "baseline_result": bool(ctx.get("baseline_result", True)),
                    "quality_pass": bool(ctx.get("quality_pass", True)),
                    "compat_pass": bool(ctx.get("compat_pass", True)),
                },
            )

        gate_art = gate_result.get("artifacts", {})
        gate_decision = str(gate_art.get("decision", "blocked"))
        blocked = gate_decision != "pass"

        artifacts: Dict[str, Any] = {
            "project_id": project_id,
            "risk": risk_art,
            "contamination": contamination_art,
            "gate": gate_art,
            "blocked": blocked,
            "version": None,
            "backup": None,
        }

        if blocked:
            return {
                "success": False,
                "action": "change_flow",
                "error": "change flow blocked by gate",
                "artifacts": artifacts,
            }

        version_id = str(ctx.get("version_id", self._mkid("ver")))
        backup_result: Dict[str, Any] = {}
        if create_backup:
            backup_result = self.create_compressed_backup(
                project_id,
                {
                    "source_path": source_path,
                    "files": files,
                    "version_id": version_id,
                },
            )
            if not backup_result.get("success"):
                return {
                    "success": False,
                    "action": "change_flow",
                    "error": backup_result.get("error", "backup failed"),
                    "artifacts": {
                        **artifacts,
                        "version": version_id,
                    },
                }

        record_result: Dict[str, Any] = {}
        if record_version:
            record_result = self.record_version(
                project_id,
                {
                    "version_id": version_id,
                    "change_summary": str(ctx.get("change_summary", "")),
                    "files": files,
                    "operation": str(ctx.get("operation", "change_flow")),
                    "risk_report_id": str(risk_art.get("report_id", "")),
                    "contamination_report_id": str(contamination_art.get("report_id", "")),
                    "backup_path": str(backup_result.get("artifacts", {}).get("backup_path", "")),
                    "status": "released" if bool(ctx.get("released", False)) else "recorded",
                },
            )
            if not record_result.get("success"):
                return {
                    "success": False,
                    "action": "change_flow",
                    "error": record_result.get("error", "version record failed"),
                    "artifacts": {
                        **artifacts,
                        "version": version_id,
                        "backup": backup_result.get("artifacts", {}),
                    },
                }

        return {
            "success": True,
            "action": "change_flow",
            "artifacts": {
                **artifacts,
                "version": version_id,
                "backup": backup_result.get("artifacts", {}),
                "version_record": record_result.get("artifacts", {}),
            },
        }
