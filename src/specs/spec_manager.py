"""
SpecManager - Spec File CRUD Management

Central manager for all spec file operations:
- Initialize .specs/ directory structure
- Create/read/update/delete spec files
- List specs by type (system, service, feature)
- Manage spec metadata
"""

import yaml
import logging
import shutil
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class SpecManager:
    SPEC_DIRS = [
        "services",
        "features",
        "scenarios",
        "constraints",
    ]

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.specs_dir = project_path / ".specs"
        self.agent_md_path = project_path / "Agent.md"

    def init_specs(
        self, system_name: str = "", system_goal: str = ""
    ) -> Dict[str, Any]:
        if self.specs_dir.exists():
            return {
                "success": True,
                "message": "specs dir already exists",
                "path": str(self.specs_dir),
            }

        self.specs_dir.mkdir(parents=True, exist_ok=True)
        for d in self.SPEC_DIRS:
            (self.specs_dir / d).mkdir(parents=True, exist_ok=True)

        if system_name or system_goal:
            self._init_agent_md(system_name, system_goal)

        self._init_constraints_yaml()
        self._init_evolution_log()

        logger.info(f"Specs initialized at {self.specs_dir}")
        return {"success": True, "path": str(self.specs_dir)}

    def _init_agent_md(self, name: str, goal: str):
        if self.agent_md_path.exists():
            return
        from .reasoning_map import ReasoningMap

        rm = ReasoningMap(self.project_path)
        rm.create_initial(name or "MySystem", goal or "System goal TBD")

    def _init_constraints_yaml(self):
        path = self.specs_dir / "constraints" / "constraints.yaml"
        if path.exists():
            return
        from .constraint_validator import ConstraintValidator

        cv = ConstraintValidator(self.project_path)
        cv.create_initial_constraints()

    def _init_evolution_log(self):
        path = self.specs_dir / "evolution-log.md"
        if path.exists():
            return
        self._write_file(
            path,
            "# Spec Evolution Log\n\nAll spec changes and bmad-evo improvement suggestions.\n\n",
        )

    def list_specs(self, spec_type: Optional[str] = None) -> List[Dict[str, Any]]:
        result = []

        if spec_type == "system" or spec_type is None:
            if self.agent_md_path.exists():
                result.append(
                    {
                        "type": "system",
                        "name": "Agent.md",
                        "path": str(self.agent_md_path),
                    }
                )

        if spec_type == "service" or spec_type is None:
            services_dir = self.specs_dir / "services"
            if services_dir.exists():
                for f in services_dir.glob("*.md"):
                    result.append({"type": "service", "name": f.stem, "path": str(f)})

        if spec_type == "feature" or spec_type is None:
            features_dir = self.specs_dir / "features"
            if features_dir.exists():
                for d in features_dir.iterdir():
                    if d.is_dir():
                        result.append(
                            {"type": "feature", "name": d.name, "path": str(d)}
                        )

        if spec_type == "scenario" or spec_type is None:
            scenarios_dir = self.specs_dir / "scenarios"
            if scenarios_dir.exists():
                for f in scenarios_dir.glob("*.yaml"):
                    result.append({"type": "scenario", "name": f.stem, "path": str(f)})

        return result

    def read_spec(self, spec_path: str) -> Optional[str]:
        path = Path(spec_path)
        if not path.exists():
            path = self.specs_dir / spec_path
        if not path.exists():
            logger.warning(f"Spec not found: {spec_path}")
            return None
        return self._read_file(path)

    def write_spec(self, spec_path: str, content: str) -> Dict[str, Any]:
        path = Path(spec_path)
        if not path.is_absolute():
            path = self.specs_dir / spec_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_file(path, content)
        return {"success": True, "path": str(path)}

    def create_feature_spec(
        self, feature_name: str, description: str
    ) -> Dict[str, Any]:
        feature_dir = self.specs_dir / "features" / feature_name
        feature_dir.mkdir(parents=True, exist_ok=True)

        spec_content = self._generate_feature_spec(feature_name, description)
        spec_path = feature_dir / "spec.md"
        self._write_file(spec_path, spec_content)

        return {"success": True, "path": str(spec_path), "feature": feature_name}

    def _generate_feature_spec(self, name: str, description: str) -> str:
        return f"""# Feature Specification: {name}

**Created**: {datetime.now().isoformat()}
**Status**: Draft
**Input**: {description}

## User Scenarios & Testing

### User Story 1 - [Title] (Priority: P1)

[Describe this user journey in plain language]

**Acceptance Scenarios**:

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

## Requirements

### Functional Requirements

- **FR-001**: System MUST [specific capability]

## Success Criteria

### Measurable Outcomes

- **SC-001**: [Measurable metric]

## Assumptions

- [Assumption about target users or scope]
"""

    def get_status(self) -> Dict[str, Any]:
        status = {
            "specs_dir_exists": self.specs_dir.exists(),
            "agent_md_exists": self.agent_md_path.exists(),
            "services": [],
            "features": [],
            "scenarios": [],
            "constraints_exists": (
                self.specs_dir / "constraints" / "constraints.yaml"
            ).exists(),
        }
        if self.specs_dir.exists():
            services_dir = self.specs_dir / "services"
            if services_dir.exists():
                status["services"] = [f.stem for f in services_dir.glob("*.md")]
            features_dir = self.specs_dir / "features"
            if features_dir.exists():
                status["features"] = [
                    d.name for d in features_dir.iterdir() if d.is_dir()
                ]
            scenarios_dir = self.specs_dir / "scenarios"
            if scenarios_dir.exists():
                status["scenarios"] = [f.stem for f in scenarios_dir.glob("*.yaml")]
        return status

    @staticmethod
    def _read_file(path: Path) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _write_file(path: Path, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
