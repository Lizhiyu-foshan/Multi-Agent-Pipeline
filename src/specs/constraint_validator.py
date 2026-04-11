"""
ConstraintValidator - Semantic Constraint Validation

Three-layer constraint system that enforces rules linters cannot:
1. Format layer: dependency direction, file size limits, naming conventions
2. Contract layer: API contracts, data contracts, error format consistency
3. Behavior layer: semantic rules agents must follow

Constraints are stored in .specs/constraints/constraints.yaml
"""

import yaml
import logging
from typing import Dict, Any, List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class ConstraintValidator:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.constraints_path = (
            project_path / ".specs" / "constraints" / "constraints.yaml"
        )

    def create_initial_constraints(self):
        self.constraints_path.parent.mkdir(parents=True, exist_ok=True)
        if self.constraints_path.exists():
            return

        initial = {
            "format": {
                "dependency_direction": [
                    {
                        "from": "ui",
                        "to": ["services"],
                        "description": "UI can only call service layer",
                    },
                    {
                        "from": "services",
                        "to": ["data", "external"],
                        "description": "Services can access data and external APIs",
                    },
                    {
                        "from": "data",
                        "to": [],
                        "description": "Data layer has no upstream dependencies",
                    },
                ],
                "file_size_limit": {
                    "lines": 300,
                    "description": "No single file should exceed this line count",
                },
                "naming_conventions": {
                    "files": "snake_case",
                    "classes": "PascalCase",
                    "functions": "snake_case",
                    "constants": "UPPER_SNAKE_CASE",
                    "test_files": "test_*.py",
                },
            },
            "contract": [
                {
                    "rule": "API endpoints must return consistent error format",
                    "scope": "global",
                    "details": "All errors use {error: string, code: string, details?: object}",
                },
                {
                    "rule": "All mutations must be idempotent",
                    "scope": "services",
                    "details": "Same request twice = same result",
                },
                {
                    "rule": "Input validation happens at boundary",
                    "scope": "services",
                    "details": "Validate at API entry point, not deep in business logic",
                },
            ],
            "behavior": [
                {
                    "rule": "Graceful degradation on external service failure",
                    "applies_to": ["*"],
                    "details": "Return cached data or meaningful error, never crash",
                },
                {
                    "rule": "All user-facing strings must be i18n-ready",
                    "applies_to": ["ui"],
                    "details": "Use i18n keys, not hardcoded strings",
                },
                {
                    "rule": "Logging must not contain sensitive data",
                    "applies_to": ["*"],
                    "details": "No passwords, tokens, PII in logs",
                },
            ],
        }

        self._write_yaml(self.constraints_path, initial)
        logger.info(f"Initial constraints created at {self.constraints_path}")

    def load_constraints(self) -> Dict[str, Any]:
        if not self.constraints_path.exists():
            return {"format": {}, "contract": [], "behavior": []}
        return self._read_yaml(self.constraints_path)

    def save_constraints(self, constraints: Dict[str, Any]):
        self.constraints_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_yaml(self.constraints_path, constraints)

    def add_contract_rule(self, rule: str, scope: str, details: str = ""):
        constraints = self.load_constraints()
        constraints.setdefault("contract", []).append(
            {
                "rule": rule,
                "scope": scope,
                "details": details,
            }
        )
        self.save_constraints(constraints)

    def add_behavior_rule(self, rule: str, applies_to: List[str], details: str = ""):
        constraints = self.load_constraints()
        constraints.setdefault("behavior", []).append(
            {
                "rule": rule,
                "applies_to": applies_to,
                "details": details,
            }
        )
        self.save_constraints(constraints)

    def add_dependency_direction(
        self, from_layer: str, to_layers: List[str], description: str = ""
    ):
        constraints = self.load_constraints()
        constraints.setdefault("format", {}).setdefault(
            "dependency_direction", []
        ).append(
            {
                "from": from_layer,
                "to": to_layers,
                "description": description,
            }
        )
        self.save_constraints(constraints)

    def validate_file_size(self, file_path: Path) -> Tuple[bool, str]:
        constraints = self.load_constraints()
        limit = (
            constraints.get("format", {}).get("file_size_limit", {}).get("lines", 300)
        )

        if not file_path.exists():
            return True, "file not found, skipped"

        line_count = len(file_path.read_text(encoding="utf-8").splitlines())
        if line_count > limit:
            return False, f"{file_path.name}: {line_count} lines (limit: {limit})"
        return True, f"{file_path.name}: {line_count} lines (OK)"

    def validate_naming(self, file_path: Path) -> Tuple[bool, str]:
        constraints = self.load_constraints()
        conventions = constraints.get("format", {}).get("naming_conventions", {})
        if not conventions:
            return True, "no naming conventions defined"

        file_pattern = conventions.get("files", "snake_case")
        name = file_path.stem

        if file_pattern == "snake_case":
            valid = name == name.lower().replace("-", "_") and " " not in name
        elif file_pattern == "PascalCase":
            valid = name[0].isupper() and "_" not in name and "-" not in name
        elif file_pattern == "kebab-case":
            valid = name == name.lower() and "_" not in name and " " not in name
        else:
            return True, "unknown convention, skipped"

        if not valid:
            return False, f"{file_path.name} does not match {file_pattern} convention"
        return True, f"{file_path.name} naming OK"

    def get_rules_for_context(self, service_name: str = None) -> str:
        constraints = self.load_constraints()
        parts = ["# Constraints Reference\n"]

        fmt = constraints.get("format", {})
        if fmt:
            parts.append("## Format Rules")
            for dep in fmt.get("dependency_direction", []):
                parts.append(
                    f"- Dependency: {dep.get('from')} -> {dep.get('to')} ({dep.get('description', '')})"
                )
            if "file_size_limit" in fmt:
                parts.append(
                    f"- File size limit: {fmt['file_size_limit'].get('lines', 'N/A')} lines"
                )
            if "naming_conventions" in fmt:
                for key, val in fmt["naming_conventions"].items():
                    parts.append(f"- Naming ({key}): {val}")

        parts.append("\n## Contract Rules")
        for rule in constraints.get("contract", []):
            scope = rule.get("scope", "global")
            if service_name and scope != "global" and scope != service_name:
                continue
            parts.append(f"- [{rule.get('scope', '')}] {rule.get('rule', '')}")
            if rule.get("details"):
                parts.append(f"  Details: {rule['details']}")

        parts.append("\n## Behavior Rules")
        for rule in constraints.get("behavior", []):
            applies = rule.get("applies_to", ["*"])
            if service_name and "*" not in applies and service_name not in applies:
                continue
            parts.append(f"- {rule.get('rule', '')}")
            if rule.get("details"):
                parts.append(f"  Details: {rule['details']}")

        return "\n".join(parts)

    def validate_project(self) -> List[Dict[str, Any]]:
        results = []
        constraints = self.load_constraints()

        for py_file in self.project_path.rglob("*.py"):
            if ".specs" in str(py_file) or "__pycache__" in str(py_file):
                continue

            ok, msg = self.validate_file_size(py_file)
            results.append(
                {
                    "file": str(py_file.relative_to(self.project_path)),
                    "check": "file_size",
                    "passed": ok,
                    "message": msg,
                }
            )

            ok, msg = self.validate_naming(py_file)
            results.append(
                {
                    "file": str(py_file.relative_to(self.project_path)),
                    "check": "naming",
                    "passed": ok,
                    "message": msg,
                }
            )

        passed = sum(1 for r in results if r["passed"])
        failed = sum(1 for r in results if not r["passed"])
        logger.info(f"Validation complete: {passed} passed, {failed} failed")
        return results

    def _read_yaml(self, path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write_yaml(self, path: Path, data: Dict[str, Any]):
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
