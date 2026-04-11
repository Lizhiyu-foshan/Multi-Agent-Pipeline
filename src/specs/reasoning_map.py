"""
ReasoningMap - Agent Reasoning Map (System Level + Service Level)

Two-layer progressive reasoning map for agents:
1. System level: goals, services, responsibilities, boundaries (prevents goal drift)
2. Service level: capabilities, interface semantics, behavior rules

Agent.md is the entry point with YAML front-matter for structured data
and Markdown body for detailed descriptions.
"""

import yaml
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class ReasoningMap:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.agent_md_path = project_path / "Agent.md"
        self.services_dir = project_path / ".specs" / "services"

    def create_initial(self, system_name: str, system_goal: str) -> Dict[str, Any]:
        front_matter = {
            "system": {
                "name": system_name,
                "goal": system_goal,
                "version": "0.1.0",
                "phase": "development",
                "created": datetime.now().isoformat(),
            },
            "services": [],
            "constraints_file": ".specs/constraints/constraints.yaml",
            "evolution_log": ".specs/evolution-log.md",
        }

        body = f"""# {system_name} - Agent Reasoning Map

## System Goal

{system_goal}

## Architecture Overview

[Describe the high-level architecture: what services exist, how they connect]

## Service Inventory

[Services are listed in YAML front-matter above. Each service has a detailed spec in .specs/services/]

## Development Focus

**Current Phase**: development
**Focus**: [What the agent should focus on right now]

## Anti-Drift Guidelines

When working on any task, periodically check:
1. Does this task contribute to the system goal stated above?
2. Am I staying within the service boundaries defined here?
3. Is this the right priority for the current development phase?
"""

        self._write_agent_md(front_matter, body)
        logger.info(f"Agent.md created at {self.agent_md_path}")
        return {"success": True, "path": str(self.agent_md_path)}

    def read_system_map(self) -> Dict[str, Any]:
        if not self.agent_md_path.exists():
            return {"front_matter": {}, "body": ""}

        content = self._read_file(self.agent_md_path)
        fm, body = self._parse_front_matter(content)
        return {"front_matter": fm, "body": body}

    def get_system_goal(self) -> str:
        data = self.read_system_map()
        return data["front_matter"].get("system", {}).get("goal", "")

    def get_services(self) -> List[Dict[str, Any]]:
        data = self.read_system_map()
        return data["front_matter"].get("services", [])

    def add_service(
        self,
        name: str,
        responsibility: str,
        boundaries: List[str],
        capabilities: List[str] = None,
    ) -> Dict[str, Any]:
        data = self.read_system_map()
        services = data["front_matter"].get("services", [])

        service_entry = {
            "name": name,
            "responsibility": responsibility,
            "boundaries": boundaries,
            "spec_file": f".specs/services/{name}.md",
        }
        services.append(service_entry)
        data["front_matter"]["services"] = services

        self._write_agent_md(data["front_matter"], data["body"])

        self._create_service_spec(name, responsibility, boundaries, capabilities or [])

        logger.info(f"Service added: {name}")
        return {"success": True, "service": name}

    def remove_service(self, name: str) -> Dict[str, Any]:
        data = self.read_system_map()
        services = data["front_matter"].get("services", [])
        data["front_matter"]["services"] = [
            s for s in services if s.get("name") != name
        ]
        self._write_agent_md(data["front_matter"], data["body"])
        return {"success": True}

    def update_development_focus(self, phase: str, focus: str) -> Dict[str, Any]:
        data = self.read_system_map()
        data["front_matter"]["system"]["phase"] = phase

        lines = data["body"].split("\n")
        new_lines = []
        in_focus = False
        focus_written = False
        for line in lines:
            if line.startswith("## Development Focus"):
                in_focus = True
                new_lines.append(line)
                continue
            if in_focus:
                if line.startswith("**Current Phase"):
                    new_lines.append(f"**Current Phase**: {phase}")
                    continue
                if line.startswith("**Focus**"):
                    new_lines.append(f"**Focus**: {focus}")
                    focus_written = True
                    continue
                if line.startswith("## ") and in_focus:
                    in_focus = False
            new_lines.append(line)

        data["body"] = "\n".join(new_lines)
        self._write_agent_md(data["front_matter"], data["body"])
        return {"success": True}

    def get_system_anchor(self) -> str:
        """L1: Ultra-short system anchor (~50 tokens). Given to every skill."""
        if not self.agent_md_path.exists():
            return ""
        data = self.read_system_map()
        fm = data["front_matter"]
        sys_info = fm.get("system", {})
        goal = sys_info.get("goal", "")
        phase = sys_info.get("phase", "")
        svc_names = [s.get("name", "") for s in fm.get("services", [])]
        return (
            f"[ANCHOR] Goal: {goal} | Phase: {phase} | Services: {', '.join(svc_names)}"
        )

    def get_service_focus(self, service_name: str) -> str:
        """L2: Service-scoped context (~200 tokens). Only for the active service."""
        data = self.read_system_map()
        fm = data["front_matter"]
        services = fm.get("services", [])
        svc = next((s for s in services if s.get("name") == service_name), None)
        if not svc:
            return ""

        parts = [f"[SERVICE:{service_name}]"]
        parts.append(f"Responsibility: {svc.get('responsibility', '')}")
        parts.append(f"Boundaries: {'; '.join(svc.get('boundaries', []))}")

        service_spec_path = self.services_dir / f"{service_name}.md"
        if service_spec_path.exists():
            content = self._read_file(service_spec_path)
            behavior_section = self._extract_section(content, "## Behavior Rules")
            if behavior_section:
                parts.append(f"Behavior Rules: {behavior_section}")

        return " | ".join(parts)

    def get_detailed_spec(self, service_name: str) -> str:
        """L3: Full service spec (~1000 tokens). On-demand only."""
        service_spec_path = self.services_dir / f"{service_name}.md"
        if service_spec_path.exists():
            return self._read_file(service_spec_path)
        return ""

    def get_context_for_agent(self, service_name: Optional[str] = None) -> str:
        data = self.read_system_map()
        fm = data["front_matter"]
        body = data["body"]

        context_parts = [f"# System Context (from Agent.md)\n"]
        context_parts.append(
            f"## System Goal\n{fm.get('system', {}).get('goal', 'N/A')}\n"
        )
        context_parts.append(
            f"## Current Phase\n{fm.get('system', {}).get('phase', 'unknown')}\n"
        )

        if service_name:
            services = fm.get("services", [])
            svc = next((s for s in services if s.get("name") == service_name), None)
            if svc:
                context_parts.append(f"\n## Active Service: {service_name}")
                context_parts.append(
                    f"- Responsibility: {svc.get('responsibility', 'N/A')}"
                )
                context_parts.append(
                    f"- Boundaries: {', '.join(svc.get('boundaries', []))}"
                )

                service_spec_path = self.services_dir / f"{service_name}.md"
                if service_spec_path.exists():
                    context_parts.append(
                        f"\n## Service Spec\n{self._read_file(service_spec_path)}"
                    )

        context_parts.append(
            f"\n## Anti-Drift Reminders\n{body.split('## Anti-Drift')[1] if '## Anti-Drift' in body else ''}"
        )

        return "\n".join(context_parts)

    @staticmethod
    def _extract_section(content: str, section_header: str) -> str:
        lines = content.split("\n")
        capture = False
        section_lines = []
        for line in lines:
            if line.startswith(section_header):
                capture = True
                continue
            if capture:
                if line.startswith("## "):
                    break
                section_lines.append(line)
        return " ".join(l.strip("- ").strip() for l in section_lines if l.strip())

    def _create_service_spec(
        self,
        name: str,
        responsibility: str,
        boundaries: List[str],
        capabilities: List[str],
    ):
        self.services_dir.mkdir(parents=True, exist_ok=True)
        path = self.services_dir / f"{name}.md"

        caps_section = (
            "\n".join(f"- {c}" for c in capabilities)
            if capabilities
            else "- [Define capabilities]"
        )
        bounds_section = (
            "\n".join(f"- {b}" for b in boundaries)
            if boundaries
            else "- [Define boundaries]"
        )

        content = f"""# Service: {name}

## Responsibility
{responsibility}

## Capabilities
{caps_section}

## Interface Semantics
[Define the key interfaces: what they accept, what they return, error semantics]

## Behavior Rules
- [Rule 1: e.g., all mutations must be idempotent]
- [Rule 2: e.g., graceful degradation on failure]

## Boundaries (What This Service Does NOT Do)
{bounds_section}

## Dependencies
[List other services or external systems this service depends on]
"""
        self._write_file(path, content)

    def _parse_front_matter(self, content: str) -> tuple:
        if not content.startswith("---"):
            return {}, content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content

        try:
            fm = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            fm = {}

        return fm, parts[2].strip()

    def _write_agent_md(self, front_matter: Dict, body: str):
        fm_str = yaml.dump(
            front_matter, allow_unicode=True, default_flow_style=False, sort_keys=False
        )
        content = f"---\n{fm_str}---\n\n{body}\n"
        self._write_file(self.agent_md_path, content)

    @staticmethod
    def _read_file(path: Path) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _write_file(path: Path, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
