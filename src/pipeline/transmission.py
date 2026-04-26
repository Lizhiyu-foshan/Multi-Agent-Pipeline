"""
TransmissionBridge - Converts conversation context into pipeline inputs.

Responsible for:
1. Project identification (type detection, entry point, directory structure)
2. Skill adapter scaffolding for target projects
3. Pipeline input generation (description, design docs, backlog)
4. Model bridge configuration (opencode / http / synthetic)
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ProjectProfile:
    root: str
    name: str = ""
    project_type: str = "unknown"
    stack: List[str] = field(default_factory=list)
    entry_files: List[str] = field(default_factory=list)
    key_directories: List[str] = field(default_factory=list)
    has_skills_dir: bool = False
    framework_hints: Dict[str, str] = field(default_factory=dict)


@dataclass
class TransmissionOutput:
    description: str
    project_root: str
    design_docs: List[str] = field(default_factory=list)
    backlog_items: List[str] = field(default_factory=list)
    model_mode: str = "synthetic"
    profile: Optional[ProjectProfile] = None


FRAMEWORK_SIGNATURES = {
    "flask": {
        "files": ["app.py", "config.py"],
        "imports": ["flask", "Flask"],
        "dirs": ["routes", "models", "templates"],
        "type": "web_backend",
    },
    "django": {
        "files": ["manage.py", "settings.py"],
        "imports": ["django"],
        "dirs": ["apps", "migrations"],
        "type": "web_fullstack",
    },
    "vue": {
        "files": ["vue.config.js", "vite.config.js"],
        "imports": ["vue"],
        "dirs": ["src", "public", "components"],
        "type": "frontend",
    },
    "react": {
        "files": ["next.config.js", "package.json"],
        "imports": ["react"],
        "dirs": ["src", "components", "pages"],
        "type": "frontend",
    },
    "fastapi": {
        "files": ["main.py"],
        "imports": ["fastapi", "FastAPI"],
        "dirs": ["routers", "schemas"],
        "type": "api_backend",
    },
}


class TransmissionBridge:
    def __init__(self, project_root: str = None):
        self.project_root = project_root or os.getcwd()

    def analyze_project(self) -> ProjectProfile:
        root = Path(self.project_root)
        profile = ProjectProfile(
            root=str(root),
            name=root.name,
        )

        if not root.exists():
            return profile

        entries = set()
        for p in root.iterdir():
            entries.add(p.name)

        dirs = {e for e in entries if (root / e).is_dir()}
        files = {e for e in entries if not (root / e).is_dir()}

        profile.key_directories = sorted(dirs)[:20]
        profile.has_skills_dir = ".skills" in dirs

        for fw_name, sig in FRAMEWORK_SIGNATURES.items():
            score = 0
            for f in sig["files"]:
                if f in files:
                    score += 2
            for d in sig["dirs"]:
                if d in dirs:
                    score += 1
            if score >= 2:
                profile.stack.append(fw_name)
                profile.framework_hints[fw_name] = sig["type"]

        if profile.stack:
            primary = profile.stack[0]
            profile.project_type = FRAMEWORK_SIGNATURES[primary]["type"]

        entry_candidates = ["app.py", "main.py", "manage.py", "index.js", "index.ts", "server.py"]
        profile.entry_files = [f for f in entry_candidates if f in files]

        return profile

    def generate_pipeline_input(
        self,
        description: str,
        design_docs: List[str] = None,
        backlog_items: List[str] = None,
    ) -> TransmissionOutput:
        profile = self.analyze_project()

        model_mode = self._detect_model_mode()

        output = TransmissionOutput(
            description=description,
            project_root=self.project_root,
            design_docs=design_docs or [],
            backlog_items=backlog_items or [],
            model_mode=model_mode,
            profile=profile,
        )
        return output

    def scaffold_skills(self, profile: ProjectProfile = None) -> bool:
        if profile is None:
            profile = self.analyze_project()

        if profile.has_skills_dir:
            logger.info("Project already has .skills/ directory, skipping scaffold")
            return True

        skills_dir = Path(profile.root) / ".skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        adapter_code = self._generate_generic_adapter(profile)
        adapter_path = skills_dir / "adapter.py"
        with open(adapter_path, "w", encoding="utf-8") as f:
            f.write(adapter_code)

        logger.info("Scaffolded .skills/adapter.py for %s project", profile.project_type)
        return True

    def _detect_model_mode(self) -> str:
        if os.environ.get("OPENCODE") or os.environ.get("OPENCODE_VERSION"):
            return "opencode_ipc"

        try:
            from pipeline.model_bridge.manager import ModelBridgeManager
            mgr = ModelBridgeManager()
            default = mgr._config.get("default_strategy", "synthetic")
            if default and default != "synthetic":
                return "bridge"
        except Exception:
            pass

        return "synthetic"

    def _generate_generic_adapter(self, profile: ProjectProfile) -> str:
        return f'''"""
Generic skill adapter for {profile.name}.
Auto-generated by TransmissionBridge.
"""

from typing import Any, Dict, Optional


class Generic_Adapter:
    def __init__(self, project_path: str = None):
        self.project_path = project_path or "."

    def execute(self, action: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        params = params or {{}}
        handlers = {{
            "analyze": self._analyze,
            "implement": self._implement,
            "test": self._test,
            "review": self._review,
        }}
        handler = handlers.get(action, self._unknown)
        return handler(params)

    def _analyze(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {{
            "status": "success",
            "action": "analyze",
            "result": "Analysis complete",
            "artifacts": [],
        }}

    def _implement(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {{
            "status": "success",
            "action": "implement",
            "result": "Implementation complete",
        }}

    def _test(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {{
            "status": "success",
            "action": "test",
            "result": "Tests passed",
        }}

    def _review(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {{
            "status": "success",
            "action": "review",
            "result": "Review complete",
        }}

    def _unknown(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {{
            "status": "error",
            "action": "unknown",
            "error": "Unknown action",
        }}
'''
