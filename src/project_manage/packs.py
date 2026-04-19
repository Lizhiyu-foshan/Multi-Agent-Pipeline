"""
Constraint Pack Manager - Registration, activation, rollback.
Supports both static (JSON) and executable (Python) rules.
"""

import importlib.util
import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ConstraintPack, ConstraintRule, RuleType

logger = logging.getLogger(__name__)


class ConstraintPackManager:
    def __init__(self, state_dir: str = None, registry=None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._global_dir = Path(state_dir) / "global"
        self._packs_file = self._global_dir / "constraint_packs.json"
        self._packs: Dict[str, ConstraintPack] = {}
        self._lock = threading.RLock()
        self._registry = registry
        self._state_dir = state_dir
        self._load()

    def _pack_key(self, name: str, version: str) -> str:
        return f"{name}@{version}"

    def _load(self):
        if not self._packs_file.exists():
            return
        try:
            with open(self._packs_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, pdata in data.items():
                self._packs[key] = ConstraintPack.from_dict(pdata)
            logger.info(f"Loaded {len(self._packs)} constraint packs")
        except Exception as e:
            logger.error(f"Failed to load packs: {e}")

    def _save(self):
        with self._lock:
            try:
                os.makedirs(str(self._global_dir), exist_ok=True)
                data = {k: p.to_dict() for k, p in self._packs.items()}
                dir_name = str(self._global_dir)
                fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, str(self._packs_file))
                except Exception:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    raise
            except Exception as e:
                logger.error(f"Failed to save packs: {e}")

    def register(self, pack: ConstraintPack) -> Dict[str, Any]:
        with self._lock:
            key = self._pack_key(pack.name, pack.version)
            if key in self._packs:
                return {"success": False, "error": f"Pack {key} already registered"}
            self._packs[key] = pack
            self._save()
            logger.info(f"Registered pack: {key} ({len(pack.rules)} rules)")
            return {
                "success": True,
                "action": "pack_registered",
                "pack_key": key,
                "artifacts": pack.to_dict(),
            }

    def get(self, name: str, version: str) -> Dict[str, Any]:
        with self._lock:
            key = self._pack_key(name, version)
            pack = self._packs.get(key)
            if not pack:
                return {"success": False, "error": f"Pack {key} not found"}
            return {
                "success": True,
                "pack_key": key,
                "artifacts": pack.to_dict(),
            }

    def list_versions(self, name: str) -> Dict[str, Any]:
        with self._lock:
            versions = []
            for key, pack in self._packs.items():
                if pack.name == name:
                    versions.append({"version": pack.version, "key": key})
            return {
                "success": True,
                "pack_name": name,
                "versions": versions,
            }

    def activate(self, project_id: str, pack_name: str, version: str) -> Dict[str, Any]:
        with self._lock:
            key = self._pack_key(pack_name, version)
            pack = self._packs.get(key)
            if not pack:
                return {"success": False, "error": f"Pack {key} not found"}

            if self._registry is None:
                from .registry import ProjectRegistry

                self._registry = ProjectRegistry(state_dir=self._state_dir)
            self._registry.update(
                project_id, {"active_pack": {"name": pack_name, "version": version}}
            )

            return {
                "success": True,
                "action": "pack_activated",
                "project_id": project_id,
                "artifacts": {
                    "pack_name": pack_name,
                    "version": version,
                    "rules_count": len(pack.rules),
                },
            }

    def rollback(
        self, project_id: str, pack_name: str, target_version: str
    ) -> Dict[str, Any]:
        key = self._pack_key(pack_name, target_version)
        with self._lock:
            if key not in self._packs:
                return {"success": False, "error": f"Pack {key} not found"}
        return self.activate(project_id, pack_name, target_version)

    def execute_rule(
        self, rule: ConstraintRule, project_path: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        if rule.rule_type == RuleType.STATIC.value:
            return self._execute_static_rule(rule, project_path, context)
        elif rule.rule_type == RuleType.EXECUTABLE.value:
            return self._execute_python_rule(rule, project_path, context)
        return {"pass": True, "issues": []}

    def _execute_static_rule(
        self, rule: ConstraintRule, project_path: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        issues = []
        try:
            static_config = (
                json.loads(rule.content)
                if isinstance(rule.content, str)
                else rule.content
            )
            required_dirs = static_config.get("required_dirs", [])
            for d in required_dirs:
                if not os.path.exists(os.path.join(project_path, d)):
                    issues.append(
                        {
                            "rule": rule.name,
                            "severity": rule.severity,
                            "message": f"Missing required directory: {d}",
                        }
                    )
            required_files = static_config.get("required_files", [])
            for f in required_files:
                if not os.path.exists(os.path.join(project_path, f)):
                    issues.append(
                        {
                            "rule": rule.name,
                            "severity": rule.severity,
                            "message": f"Missing required file: {f}",
                        }
                    )
        except Exception as e:
            issues.append(
                {
                    "rule": rule.name,
                    "severity": "high",
                    "message": f"Rule execution error: {e}",
                }
            )
        return {"pass": len(issues) == 0, "issues": issues}

    def _execute_python_rule(
        self, rule: ConstraintRule, project_path: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        rule_path = rule.file_path
        if not rule_path or not os.path.exists(rule_path):
            if rule.content:
                fd, tmp = tempfile.mkstemp(suffix=".py", prefix="pm_rule_")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(rule.content)
                    rule_path = tmp
                except Exception:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                    return {
                        "pass": False,
                        "issues": [
                            {
                                "rule": rule.name,
                                "severity": "high",
                                "message": "Failed to write rule file",
                            }
                        ],
                    }
            else:
                return {
                    "pass": False,
                    "issues": [
                        {
                            "rule": rule.name,
                            "severity": "high",
                            "message": "No rule file or content",
                        }
                    ],
                }

        try:
            spec = importlib.util.spec_from_file_location(
                f"pm_rule_{rule.rule_id}", rule_path
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not hasattr(mod, "check"):
                return {
                    "pass": False,
                    "issues": [
                        {
                            "rule": rule.name,
                            "severity": "high",
                            "message": "Rule file has no check() function",
                        }
                    ],
                }
            result = mod.check(project_path, context)
            return {
                "pass": result.get("pass", False),
                "issues": result.get("issues", []),
            }
        except Exception as e:
            return {
                "pass": False,
                "issues": [
                    {
                        "rule": rule.name,
                        "severity": "high",
                        "message": f"Rule execution error: {e}",
                    }
                ],
            }
        finally:
            if rule.file_path and not rule.file_path and os.path.exists(rule_path):
                try:
                    os.unlink(rule_path)
                except Exception:
                    pass

    def run_all_rules(
        self,
        pack_name: str,
        version: str,
        project_path: str,
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            key = self._pack_key(pack_name, version)
            pack = self._packs.get(key)
            if not pack:
                return {"success": False, "error": f"Pack {key} not found"}
            all_issues = []
            ctx = context or {}
            for rule in pack.rules:
                result = self.execute_rule(rule, project_path, ctx)
                all_issues.extend(result.get("issues", []))
            return {
                "success": True,
                "pack_key": key,
                "total_rules": len(pack.rules),
                "issues": all_issues,
                "pass": len(all_issues) == 0,
            }
