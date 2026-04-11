"""
Prompt Manager - Unified prompt template management for the multi-agent pipeline.

Solves: prompts scattered across adapters with manual str.replace(),
no shared sections, no discovery, no structured substitution.

Architecture:
- PromptSection: reusable composable prompt fragment
- PromptTemplate: named template with variables and metadata
- PromptRegistry: stores templates + sections, lookup by name/skill/role
- PromptComposer: renders template + sections + variables -> final prompt string

Usage:
    pm = PromptManager(project_path)

    # Render a skill prompt
    prompt = pm.render("superpowers/execute_task",
        task_id="T001", task_name="Build auth", task_spec="...")

    # Compose with shared sections
    prompt = pm.compose("superpowers/execute_task",
        sections=["role_context", "spec_constraints", "quality_gates"],
        task_id="T001", ...)

    # Register a new template
    pm.register_template("my_skill/my_action", content="...",
        skill="my_skill", variables=["foo", "bar"])
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    name: str
    content: str
    description: str = ""
    variables: List[str] = field(default_factory=list)
    category: str = "common"

    def render(self, **kwargs) -> str:
        result = self.content
        for var in self.variables:
            placeholder = "{" + var + "}"
            value = str(kwargs.get(var, ""))
            result = result.replace(placeholder, value)
        return result


@dataclass
class PromptTemplate:
    name: str
    content: str
    skill: str = ""
    role: str = ""
    description: str = ""
    variables: List[str] = field(default_factory=list)
    required_sections: List[str] = field(default_factory=list)
    optional_sections: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def render(self, **kwargs) -> str:
        result = self.content
        for var in self.variables:
            placeholder = "{" + var + "}"
            value = str(kwargs.get(var, ""))
            result = result.replace(placeholder, value)
        return result

    def extract_variables(self) -> Set[str]:
        return set(re.findall(r"\{(\w+)\}", self.content))


class PromptRegistry:
    def __init__(self):
        self._templates: Dict[str, PromptTemplate] = {}
        self._sections: Dict[str, PromptSection] = {}
        self._by_skill: Dict[str, List[str]] = {}
        self._by_role: Dict[str, List[str]] = {}

    def register_template(self, template: PromptTemplate):
        self._templates[template.name] = template
        if template.skill:
            self._by_skill.setdefault(template.skill, []).append(template.name)
        if template.role:
            self._by_role.setdefault(template.role, []).append(template.name)

    def register_section(self, section: PromptSection):
        self._sections[section.name] = section

    def get_template(self, name: str) -> Optional[PromptTemplate]:
        return self._templates.get(name)

    def get_section(self, name: str) -> Optional[PromptSection]:
        return self._sections.get(name)

    def list_templates(self, skill: str = None) -> List[PromptTemplate]:
        if skill:
            names = self._by_skill.get(skill, [])
            return [self._templates[n] for n in names if n in self._templates]
        return list(self._templates.values())

    def list_sections(self, category: str = None) -> List[PromptSection]:
        if category:
            return [s for s in self._sections.values() if s.category == category]
        return list(self._sections.values())

    def find_templates(self, query: str) -> List[PromptTemplate]:
        query_lower = query.lower()
        results = []
        for t in self._templates.values():
            if (
                query_lower in t.name.lower()
                or query_lower in t.description.lower()
                or query_lower in t.skill.lower()
            ):
                results.append(t)
        return results


class PromptComposer:
    SECTION_MARKER = "\n\n---\n\n"

    def __init__(self, registry: PromptRegistry):
        self.registry = registry

    def render_template(self, name: str, **kwargs) -> str:
        template = self.registry.get_template(name)
        if not template:
            raise KeyError(f"Prompt template not found: {name}")
        return template.render(**kwargs)

    def compose(
        self,
        name: str,
        sections: List[str] = None,
        section_mode: str = "append",
        **kwargs,
    ) -> str:
        template = self.registry.get_template(name)
        if not template:
            raise KeyError(f"Prompt template not found: {name}")

        rendered = template.render(**kwargs)

        active_sections = list(sections or [])
        for req in template.required_sections:
            if req not in active_sections:
                active_sections.append(req)

        section_parts = []
        for sec_name in active_sections:
            section = self.registry.get_section(sec_name)
            if section:
                section_parts.append(section.render(**kwargs))
            else:
                logger.debug(f"Section '{sec_name}' not found, skipping")

        if section_mode == "prepend":
            return self.SECTION_MARKER.join(section_parts + [rendered])
        elif section_mode == "replace_markers":
            return self._replace_markers(rendered, section_parts)
        else:
            return self.SECTION_MARKER.join([rendered] + section_parts)

    def _replace_markers(self, content: str, section_parts: List[str]) -> str:
        result = content
        for part in section_parts:
            marker_match = re.search(r"\{section:(\w+)\}", result)
            if marker_match:
                result = result.replace(marker_match.group(0), part)
        return result

    def compose_for_role(
        self,
        name: str,
        role_type: str,
        role_name: str = "",
        capabilities: List[str] = None,
        **kwargs,
    ) -> str:
        kwargs.setdefault("role_type", role_type)
        kwargs.setdefault("role_name", role_name or role_type)
        kwargs.setdefault("capabilities", ", ".join(capabilities or []))

        sections = kwargs.pop("sections", None) or []
        if "role_context" not in sections:
            sections.insert(0, "role_context")

        return self.compose(name, sections=sections, **kwargs)


class PromptManager:
    def __init__(self, project_path: str = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        self.registry = PromptRegistry()
        self.composer = PromptComposer(self.registry)
        self._load_builtin_sections()
        self._load_builtin_templates()

    def render(self, name: str, **kwargs) -> str:
        return self.composer.render_template(name, **kwargs)

    def compose(self, name: str, sections: List[str] = None, **kwargs) -> str:
        return self.composer.compose(name, sections=sections, **kwargs)

    def compose_for_role(self, name: str, role_type: str, **kwargs) -> str:
        return self.composer.compose_for_role(name, role_type, **kwargs)

    def register_template(
        self,
        name: str,
        content: str,
        skill: str = "",
        role: str = "",
        description: str = "",
        variables: List[str] = None,
        required_sections: List[str] = None,
        optional_sections: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> None:
        template = PromptTemplate(
            name=name,
            content=content,
            skill=skill,
            role=role,
            description=description,
            variables=variables or [],
            required_sections=required_sections or [],
            optional_sections=optional_sections or [],
            metadata=metadata or {},
        )
        if not template.variables:
            template.variables = sorted(template.extract_variables())
        self.registry.register_template(template)

    def register_section(
        self,
        name: str,
        content: str,
        description: str = "",
        variables: List[str] = None,
        category: str = "common",
    ) -> None:
        section = PromptSection(
            name=name,
            content=content,
            description=description,
            variables=variables or sorted(set(re.findall(r"\{(\w+)\}", content))),
            category=category,
        )
        self.registry.register_section(section)

    def load_prompt_file(
        self, filepath: str, name: str = None, skill: str = "", **kwargs
    ) -> Optional[str]:
        path = Path(filepath)
        if not path.is_absolute():
            path = self.project_path / path
        if not path.exists():
            logger.warning(f"Prompt file not found: {path}")
            return None

        content = path.read_text(encoding="utf-8")

        if "## Template" in content:
            parts = content.split("## Template", 1)
            content = parts[1].strip()
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        template_name = name or path.stem
        self.register_template(template_name, content=content, skill=skill, **kwargs)
        return template_name

    def list_templates(self, skill: str = None) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "skill": t.skill,
                "role": t.role,
                "description": t.description,
                "variables": t.variables,
                "required_sections": t.required_sections,
            }
            for t in self.registry.list_templates(skill)
        ]

    def list_sections(self, category: str = None) -> List[Dict[str, Any]]:
        return [
            {
                "name": s.name,
                "category": s.category,
                "description": s.description,
                "variables": s.variables,
            }
            for s in self.registry.list_sections(category)
        ]

    def get_status(self) -> Dict[str, Any]:
        return {
            "templates": len(self.registry._templates),
            "sections": len(self.registry._sections),
            "skills": list(self.registry._by_skill.keys()),
            "template_names": sorted(self.registry._templates.keys()),
            "section_names": sorted(self.registry._sections.keys()),
        }

    def _load_builtin_sections(self):
        self.register_section(
            name="role_context",
            content="## Your Role\n\n"
            "You are acting as: {role_name} ({role_type})\n"
            "Capabilities: {capabilities}\n",
            description="Role identity and capabilities context",
            variables=["role_name", "role_type", "capabilities"],
            category="context",
        )

        self.register_section(
            name="spec_constraints",
            content="## Spec Constraints\n\n{spec_context}\n",
            description="Spec requirements and constraints from spec-kit",
            variables=["spec_context"],
            category="context",
        )

        self.register_section(
            name="pipeline_context",
            content="## Pipeline Context\n\n"
            "- Pipeline: {pipeline_id}\n"
            "- Phase: {pipeline_phase}\n"
            "- PDCA Cycle: {pdca_cycle}\n"
            "- Task: {task_id}\n",
            description="Current pipeline execution context",
            variables=["pipeline_id", "pipeline_phase", "pdca_cycle", "task_id"],
            category="context",
        )

        self.register_section(
            name="previous_artifacts",
            content="## Previous Work\n\n{previous_artifacts_summary}\n",
            description="Summary of artifacts from previous pipeline stages",
            variables=["previous_artifacts_summary"],
            category="context",
        )

        self.register_section(
            name="quality_gates",
            content="## Quality Gates\n\n"
            "Before reporting completion, verify:\n"
            "- Completeness: Did you implement everything in the spec?\n"
            "- Quality: Are names clear? Is code maintainable?\n"
            "- Discipline: Did you avoid overbuilding? (YAGNI)\n"
            "- Testing: Do tests verify behavior? TDD followed?\n"
            "- Spec: Does output pass spec constraint checks?\n",
            description="Standard quality verification checklist",
            variables=[],
            category="quality",
        )

        self.register_section(
            name="report_format",
            content="## Report Format\n\n"
            "Report back with:\n"
            "- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT\n"
            "- What you implemented\n"
            "- What you tested and test results\n"
            "- Files changed (with exact paths)\n"
            "- Self-review findings\n"
            "- Issues or concerns\n",
            description="Standard completion report format",
            variables=[],
            category="format",
        )

        self.register_section(
            name="refinement_feedback",
            content="## Refinement Feedback (Previous Attempt)\n\n"
            "{refinement_feedback}\n",
            description="Feedback from previous iteration's evaluation",
            variables=["refinement_feedback"],
            category="feedback",
        )

        self.register_section(
            name="stuck_protocol",
            content="## When You're Stuck\n\n"
            "STOP and report BLOCKED or NEEDS_CONTEXT if:\n"
            "- Task requires architectural decisions with multiple valid approaches\n"
            "- You need context beyond what was provided\n"
            "- You're uncertain about approach correctness\n",
            description="Protocol for when agent encounters blockers",
            variables=[],
            category="protocol",
        )

        self.register_section(
            name="tdd_protocol",
            content="## TDD Protocol\n\n"
            "Follow RED-GREEN-REFACTOR:\n"
            "1. RED: Write a failing test FIRST. NO production code without a failing test.\n"
            "2. GREEN: Write the MINIMUM code to make the test pass.\n"
            "3. REFACTOR: Clean up while keeping tests green.\n"
            "4. Repeat for each requirement.\n",
            description="Test-Driven Development protocol",
            variables=[],
            category="protocol",
        )

        self.register_section(
            name="two_stage_review",
            content="## Two-Stage Review Protocol\n\n"
            "After implementation, perform reviews IN ORDER:\n"
            "1. **Spec Review** (Stage 1): Did we build WHAT was requested?\n"
            "   - Check each spec requirement is addressed\n"
            "   - Check no extra work beyond spec (YAGNI)\n"
            "   - This stage MUST pass before code quality review\n"
            "2. **Code Quality Review** (Stage 2): Is it WELL-BUILT?\n"
            "   - Code structure, naming, readability\n"
            "   - Test coverage and quality\n"
            "   - File sizes, separation of concerns\n",
            description="Two-stage review: spec compliance THEN code quality",
            variables=[],
            category="protocol",
        )

        self.register_section(
            name="debug_protocol",
            content="## Debug Protocol (4 Phases)\n\n"
            "Phase 1 - Root Cause: Identify the exact error/exception\n"
            "Phase 2 - Pattern: Find similar patterns in recent changes\n"
            "Phase 3 - Hypothesis: Form and test a fix hypothesis\n"
            "Phase 4 - Fix: Apply and verify the fix\n",
            description="Systematic debugging protocol",
            variables=[],
            category="protocol",
        )

        self.register_section(
            name="hashline_edit_protocol",
            content="## Hashline Edit Protocol\n\n"
            "All file edits MUST use HashlineEditTool to prevent stale-line errors.\n\n"
            "1. Read file: `hashline_edit.read_file(path)` → lines annotated as LINE#HASH|content\n"
            "2. Reference lines by their hash when editing\n"
            "3. If hash mismatch → file changed since you read it → re-read and retry\n"
            "4. Use multi_edit for batch atomic operations\n\n"
            "Operations: replace, insert_after, insert_before, delete, multi_edit\n"
            "Every edit is validated against content hash BEFORE applying.\n",
            description="Hashline content-hash-verified editing protocol",
            variables=[],
            category="protocol",
        )

    def _load_builtin_templates(self):
        self._load_superpowers_templates()
        self._load_pipeline_templates()
        self._load_bmad_templates()

    def _load_superpowers_templates(self):
        prompts_dir = self.project_path / ".skills" / "superpowers" / "prompts"
        if prompts_dir.exists():
            for f in prompts_dir.glob("*.md"):
                skill_name = "superpowers"

                action_map = {
                    "implementer-prompt": "superpowers/execute_task",
                    "spec-reviewer-prompt": "superpowers/spec_review",
                    "code-quality-reviewer-prompt": "superpowers/code_quality_review",
                    "debugging-prompt": "superpowers/debug",
                }
                template_name = action_map.get(f.stem, f"superpowers/{f.stem}")

                self.load_prompt_file(
                    str(f),
                    name=template_name,
                    skill=skill_name,
                    description=f"Superpowers prompt for {template_name}",
                    optional_sections=[
                        "quality_gates",
                        "stuck_protocol",
                        "report_format",
                    ],
                )

        self.register_template(
            name="superpowers/tdd_cycle",
            content=(
                "You are in {tdd_phase} phase of TDD for: {task_description}\n\n"
                "## TDD Rules\n\n"
                "RED: Write a FAILING test. No production code.\n"
                "GREEN: Write minimal code to make test pass.\n"
                "REFACTOR: Clean up, keep tests green.\n\n"
                "{tdd_instructions}\n"
            ),
            skill="superpowers",
            description="TDD RED-GREEN-REFACTOR cycle prompt",
            variables=["tdd_phase", "task_description", "tdd_instructions"],
            required_sections=["tdd_protocol"],
        )

        self.register_template(
            name="superpowers/hashline_edit",
            content=(
                "You are editing file: {file_path}\n\n"
                "## Current File Content (Hashline-Annotated)\n\n"
                "{hashline_content}\n\n"
                "## Edit Instructions\n\n"
                "{edit_instructions}\n\n"
                "Use the hash values from the annotated content above to make your edits.\n"
                "Format: reference lines as LINE_NUMBER#HASH.\n"
            ),
            skill="superpowers",
            description="Hashline-verified file editing prompt",
            variables=["file_path", "hashline_content", "edit_instructions"],
            required_sections=["hashline_edit_protocol"],
        )

    def _load_pipeline_templates(self):
        self.register_template(
            name="pipeline/analyze",
            content=(
                "Analyze the following project description and provide:\n"
                "1. Role definitions (type, name, capabilities)\n"
                "2. Task breakdown with dependencies\n"
                "3. Estimated complexity\n\n"
                "Description: {description}\n"
            ),
            skill="multi-agent-pipeline",
            description="Pipeline ANALYZE phase prompt",
            variables=["description"],
            required_sections=["spec_constraints"],
        )

        self.register_template(
            name="pipeline/plan",
            content=(
                "Create detailed execution plan based on analysis.\n\n"
                "Tasks: {tasks_json}\n"
                "Roles: {roles_json}\n"
            ),
            skill="multi-agent-pipeline",
            description="Pipeline PLAN phase prompt",
            variables=["tasks_json", "roles_json"],
            required_sections=["spec_constraints"],
        )

        self.register_template(
            name="pipeline/execute_task",
            content=(
                "Execute task: {task_name}\n\n"
                "Description: {task_description}\n\n"
                "Role: {role_name} ({role_type})\n"
                "Capabilities: {capabilities}\n"
            ),
            skill="multi-agent-pipeline",
            description="Per-task execution prompt",
            variables=[
                "task_name",
                "task_description",
                "role_name",
                "role_type",
                "capabilities",
            ],
            optional_sections=[
                "spec_constraints",
                "previous_artifacts",
                "quality_gates",
            ],
        )

        self.register_template(
            name="pipeline/evolve",
            content=(
                "Evolve specs based on completed work.\n\n"
                "Completed artifacts: {artifacts_summary}\n"
                "PDCA cycle: {pdca_cycle}\n"
            ),
            skill="multi-agent-pipeline",
            description="Pipeline EVOLVE phase prompt",
            variables=["artifacts_summary", "pdca_cycle"],
        )

        self.register_template(
            name="pipeline/verify",
            content=(
                "Verify all specs pass.\n\nArtifacts to verify: {artifacts_list}\n"
            ),
            skill="multi-agent-pipeline",
            description="Pipeline VERIFY phase prompt",
            variables=["artifacts_list"],
        )

    def _load_bmad_templates(self):
        self.register_template(
            name="bmad-evo/clarify",
            content=(
                "You are analyzing requirements for: {task_description}\n\n"
                "Current understanding: {current_understanding}\n\n"
                "Previous answers: {previous_answers}\n\n"
                "Generate clarification questions focusing on:\n"
                "1. Business goals and user needs\n"
                "2. System boundaries and scope\n"
                "3. Quality attributes and constraints\n"
                "4. Integration requirements\n"
                "5. Spec alignment check\n"
            ),
            skill="bmad-evo",
            description="Requirement clarification prompt",
            variables=["task_description", "current_understanding", "previous_answers"],
        )

        self.register_template(
            name="bmad-evo/analysis_report",
            content=(
                "# BMAD-EVO Analysis Report\n\n"
                "## Task: {task}\n\n"
                "- Task Type: {task_type}\n"
                "- Complexity: {complexity_score}/10\n"
                "- Recommended Roles: {recommended_roles}\n"
                "- Estimated Duration: {estimated_duration}\n\n"
                "## Risk Factors\n"
                "{risk_factors}\n\n"
                "## Success Criteria\n"
                "{success_criteria}\n\n"
                "{spec_alignment}\n"
            ),
            skill="bmad-evo",
            description="Formatted analysis report template",
            variables=[
                "task",
                "task_type",
                "complexity_score",
                "recommended_roles",
                "estimated_duration",
                "risk_factors",
                "success_criteria",
                "spec_alignment",
            ],
        )

        self.register_template(
            name="bmad-evo/constraint_report",
            content=(
                "# Generated Constraints (for Spec-Kit)\n\n"
                "{contract_rules}\n"
                "{behavior_rules}\n"
            ),
            skill="bmad-evo",
            description="Constraint generation report template",
            variables=["contract_rules", "behavior_rules"],
        )
