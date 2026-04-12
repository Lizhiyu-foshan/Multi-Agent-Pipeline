"""
BMAD-EVO Skill Adapter

Wraps the real bmad-evo framework (D:/bmad-evo) without modifying it.
References original code via sys.path.
Uses PromptManager for report formatting templates.

Actions:
- analyze: Task type detection + complexity assessment via TaskAnalyzer
- deep_analysis: Full multi-agent workflow via WorkflowOrchestratorV3Final
- clarify: Multi-round requirement clarification
- generate_constraints: Produce constraint rules from analysis (for spec-kit)
- spec_evolution: Analyze existing specs and suggest improvements
- update_for_feedback: Handle new user story / bug, update analysis
"""

import sys
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)

BMAD_ORIGINAL_PATH = Path("D:/bmad-evo")
BMAD_LIB_PATH = BMAD_ORIGINAL_PATH / "lib"
BMAD_V3_PATH = BMAD_ORIGINAL_PATH / "lib" / "v3"
BMAD_AGENTS_PATH = BMAD_ORIGINAL_PATH / "agents"

_pipeline_src = str(Path(__file__).resolve().parents[1] / "src")
if _pipeline_src not in sys.path:
    sys.path.insert(0, _pipeline_src)

_bridge = None


def _ensure_bmad_importable():
    paths = [
        str(BMAD_LIB_PATH),
        str(BMAD_V3_PATH),
        str(BMAD_AGENTS_PATH),
        str(Path(__file__).resolve().parent),
    ]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)


def _get_bridge(project_path: Path):
    global _bridge
    if _bridge is not None:
        return _bridge
    try:
        from model_bridge import ModelBridge, load_bmad_env_config, patch_bmad_modules
        from prompt_pass import PromptPass

        env_mode = load_bmad_env_config(project_path)
        _bridge = ModelBridge(mode=env_mode)
        PromptPassInstance = PromptPass(project_path)
        ModelBridge.set_prompt_pass(PromptPassInstance)
        patched = patch_bmad_modules(_bridge)
        if patched:
            logger.info(f"BMAD modules patched with mode: {_bridge.mode}")
    except Exception as e:
        logger.warning(f"ModelBridge init failed: {e}")
        _bridge = None
    return _bridge
    try:
        from model_bridge import ModelBridge, load_bmad_env_config, patch_bmad_modules

        env_mode = load_bmad_env_config(project_path)
        _bridge = ModelBridge(mode=env_mode)
        patched = patch_bmad_modules(_bridge)
        if patched:
            logger.info(f"BMAD modules patched with mode: {_bridge.mode}")
    except Exception as e:
        logger.warning(f"ModelBridge init failed: {e}")
        _bridge = None
    return _bridge


class Bmad_Evo_Adapter:
    """BMAD-EVO Skill Adapter for Multi-Agent Pipeline"""

    name = "bmad-evo"
    version = "3.1"

    def __init__(self, project_path: str = None):
        self.project_path = Path(project_path) if project_path else Path.cwd()
        self._bmad_available = BMAD_ORIGINAL_PATH.exists()
        if self._bmad_available:
            _ensure_bmad_importable()
            _get_bridge(self.project_path)

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "analyze")
        handlers = {
            "analyze": self._handle_analyze,
            "deep_analysis": self._handle_deep_analysis,
            "clarify": self._handle_clarify,
            "generate_constraints": self._handle_generate_constraints,
            "spec_evolution": self._handle_spec_evolution,
            "update_for_feedback": self._handle_update_for_feedback,
        }
        handler = handlers.get(action, self._handle_analyze)
        return handler(task_description, context)

    def _handle_analyze(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        spec_context = context.get("spec_context", "")
        model_response = context.get("model_response")

        if not self._bmad_available:
            return self._fallback_analysis(task_description, spec_context)

        try:
            from model_bridge import ModelRequestPending
        except ImportError:
            ModelRequestPending = None

        if model_response and context.get("model_request_id"):
            return self._continue_analysis(
                model_response, task_description, spec_context
            )

        try:
            from task_analyzer import TaskAnalyzer
            from model_bridge import ModelBridge

            analyzer = TaskAnalyzer(timeout=120)
            analysis = analyzer.analyze(task_description)

            if ModelBridge._pending_requests:
                latest = ModelBridge._pending_requests[-1]
                ModelBridge._pending_requests = []
                pending_req = {
                    "id": latest["id"],
                    "type": "chat",
                    "prompt": latest["prompt"],
                    "model": latest["model"],
                    "instructions": (
                        "Execute model inference for this prompt, then "
                        "call adapter again with context.model_response "
                        "= <your response> and context.model_request_id = "
                        + latest["id"]
                    ),
                }
                return {
                    "success": False,
                    "pending_model_request": pending_req,
                    "model_request": pending_req,
                }

            enriched = self._enrich_with_spec(analysis.to_dict(), spec_context)

            return {
                "success": True,
                "artifacts": {
                    "analysis_report": self._format_analysis_report(
                        enriched, task_description
                    ),
                    "task_type": enriched.get("task_type", "unknown"),
                    "complexity_score": enriched.get("complexity_score", 0),
                    "recommended_roles": enriched.get("recommended_roles_count", 0),
                    "risk_factors": enriched.get("risk_factors", []),
                    "success_criteria": enriched.get("success_criteria", []),
                    "spec_alignment": enriched.get("spec_alignment", ""),
                },
            }
        except Exception as e:
            if ModelRequestPending and isinstance(e, ModelRequestPending):
                pending_req = {
                    "id": e.request_id,
                    "type": "chat",
                    "prompt": e.prompt,
                    "model": e.model,
                    "instructions": (
                        "Execute model inference for this prompt, then "
                        "call adapter again with context.model_response "
                        "= <your response> and context.model_request_id = "
                        + e.request_id
                    ),
                }
                return {
                    "success": False,
                    "pending_model_request": pending_req,
                    "model_request": pending_req,
                }
            logger.warning(f"BMAD analysis failed, using fallback: {e}")
            return self._fallback_analysis(task_description, spec_context)

    def _continue_analysis(
        self, model_response: str, task_description: str, spec_context: str
    ) -> Dict[str, Any]:
        try:
            import json as _json

            data = _json.loads(model_response)
        except Exception:
            data = {"raw_response": model_response}

        analysis = {
            "task_type": data.get("task_type", "unknown"),
            "complexity_score": data.get("complexity_score", 5),
            "recommended_roles_count": data.get("recommended_roles_count", 3),
            "key_skills": data.get("key_skills", []),
            "estimated_duration": data.get("estimated_duration", "unknown"),
            "risk_factors": data.get("risk_factors", []),
            "success_criteria": data.get("success_criteria", []),
        }

        enriched = self._enrich_with_spec(analysis, spec_context)

        return {
            "success": True,
            "artifacts": {
                "analysis_report": self._format_analysis_report(
                    enriched, task_description
                ),
                "task_type": enriched.get("task_type", "unknown"),
                "complexity_score": enriched.get("complexity_score", 0),
                "recommended_roles": enriched.get("recommended_roles_count", 0),
                "risk_factors": enriched.get("risk_factors", []),
                "success_criteria": enriched.get("success_criteria", []),
                "spec_alignment": enriched.get("spec_alignment", ""),
                "model_response_used": True,
            },
        }

    def _handle_deep_analysis(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        spec_context = context.get("spec_context", "")
        interactive = context.get("interactive", False)

        if not self._bmad_available:
            return self._fallback_analysis(task_description, spec_context)

        try:
            from workflow_orchestrator_v3_final import WorkflowOrchestratorV3Final

            orchestrator = WorkflowOrchestratorV3Final(
                project_path=str(self.project_path),
                interactive=interactive,
                config={
                    "max_iterations": context.get("max_iterations", 3),
                    "pass_threshold": context.get("pass_threshold", 85),
                },
            )

            enriched_task = self._prepend_spec_context(task_description, spec_context)
            result = orchestrator.execute_full_workflow(enriched_task)

            return {
                "success": result.get("success", False),
                "artifacts": {
                    "analysis_report": str(result),
                    "full_result": result,
                },
            }
        except Exception as e:
            logger.warning(f"BMAD deep analysis failed, using fallback: {e}")
            return self._fallback_analysis(task_description, spec_context)

    def _handle_clarify(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        spec_context = context.get("spec_context", "")
        previous_answers = context.get("previous_answers", [])

        clarification_questions = self._generate_clarification_questions(
            task_description, spec_context, previous_answers
        )

        return {
            "success": True,
            "artifacts": {
                "clarification_questions": clarification_questions,
                "current_understanding": self._build_understanding(
                    task_description, previous_answers
                ),
            },
        }

    def _handle_generate_constraints(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        analysis = context.get("analysis", {})
        if not analysis:
            analysis = self._handle_analyze(task_description, context)
            if analysis.get("success"):
                analysis = analysis["artifacts"]

        constraints = self._extract_constraints(analysis, task_description)

        return {
            "success": True,
            "artifacts": {
                "constraints_for_spec": constraints,
                "constraint_report": self._format_constraints(constraints),
            },
        }

    def _handle_spec_evolution(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        evolution_context = context.get("evolution_context", {})
        spec_findings = (
            evolution_context.get("findings", {}) if evolution_context else {}
        )

        suggestions = self._generate_evolution_suggestions(
            task_description, spec_findings
        )

        return {
            "success": True,
            "artifacts": {
                "evolution_suggestions": suggestions,
                "evolution_report": self._format_evolution_report(suggestions),
            },
        }

    def _handle_update_for_feedback(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        feedback_type = context.get("feedback_type", "user_story")
        feedback_content = context.get("feedback_content", task_description)
        existing_analysis = context.get("existing_analysis", {})

        updated = self._update_analysis_with_feedback(
            existing_analysis, feedback_type, feedback_content
        )

        constraint_updates = self._extract_constraints(updated, feedback_content)

        return {
            "success": True,
            "artifacts": {
                "updated_analysis": updated,
                "constraint_updates": constraint_updates,
                "update_report": self._format_feedback_update(updated, feedback_type),
            },
        }

    def _enrich_with_spec(self, analysis: Dict, spec_context: str) -> Dict:
        if spec_context:
            analysis["spec_alignment"] = (
                f"Analysis performed with spec context: {spec_context[:200]}"
            )
        return analysis

    def _prepend_spec_context(self, task: str, spec_context: str) -> str:
        if not spec_context:
            return task
        return f"[SPEC CONTEXT]\n{spec_context}\n[/SPEC CONTEXT]\n\n[ORIGINAL TASK]\n{task}"

    def _generate_clarification_questions(
        self, task: str, spec: str, answers: List
    ) -> List[Dict]:
        questions = []

        if not any(a.get("category") == "goal" for a in answers):
            questions.append(
                {
                    "id": "Q001",
                    "category": "goal",
                    "question": "What is the primary business goal this system must achieve?",
                    "purpose": "Define the system goal for Agent.md",
                }
            )

        if not any(a.get("category") == "users" for a in answers):
            questions.append(
                {
                    "id": "Q002",
                    "category": "users",
                    "question": "Who are the primary users? What are their key workflows?",
                    "purpose": "Define user stories and service boundaries",
                }
            )

        if not any(a.get("category") == "boundaries" for a in answers):
            questions.append(
                {
                    "id": "Q003",
                    "category": "boundaries",
                    "question": "What is explicitly OUT of scope? What should the system NOT do?",
                    "purpose": "Define service boundaries and constraints",
                }
            )

        if not any(a.get("category") == "quality" for a in answers):
            questions.append(
                {
                    "id": "Q004",
                    "category": "quality",
                    "question": "What are the key quality attributes? (performance, security, reliability)",
                    "purpose": "Generate behavior constraints and acceptance scenarios",
                }
            )

        if not any(a.get("category") == "tech" for a in answers):
            questions.append(
                {
                    "id": "Q005",
                    "category": "tech",
                    "question": "Are there existing systems, APIs, or databases that must be integrated?",
                    "purpose": "Define dependencies and contract constraints",
                }
            )

        if spec:
            questions.append(
                {
                    "id": "Q006",
                    "category": "spec_alignment",
                    "question": f"Current spec says: '{spec[:100]}...' - Does this task align or extend it?",
                    "purpose": "Check for spec drift",
                }
            )

        if not any(a.get("category") == "acceptance" for a in answers):
            questions.append(
                {
                    "id": "Q007",
                    "category": "acceptance",
                    "question": "What are the MUST-HAVE acceptance criteria? "
                    "Define WHEN/THEN scenarios BEFORE decomposition.",
                    "purpose": "TDD-inspired: define test scenarios before task breakdown",
                }
            )

        return questions

    def _build_understanding(self, task: str, answers: List) -> str:
        if not answers:
            return f"Initial task: {task}"
        parts = [f"Original task: {task}\n"]
        for a in answers:
            parts.append(f"[{a.get('category', 'general')}] {a.get('question', '')}")
            parts.append(f"  Answer: {a.get('answer', '')}\n")
        return "\n".join(parts)

    def _extract_constraints(self, analysis: Dict, task: str) -> Dict[str, Any]:
        risk_factors = analysis.get("risk_factors", [])
        success_criteria = analysis.get("success_criteria", [])
        complexity = analysis.get("complexity_score", 5)

        contract_rules = []
        behavior_rules = []

        for risk in risk_factors:
            severity = "critical" if complexity >= 8 else "important"
            behavior_rules.append(
                {
                    "rule": f"Mitigate risk: {risk}",
                    "applies_to": ["*"],
                    "details": f"Identified during analysis of: {task[:80]}",
                    "severity": severity,
                }
            )

        for criterion in success_criteria:
            contract_rules.append(
                {
                    "rule": f"Must satisfy: {criterion}",
                    "scope": "global",
                    "details": "Success criterion from requirement analysis",
                    "severity": "critical",
                }
            )

        if complexity >= 7:
            behavior_rules.append(
                {
                    "rule": "All changes require review before merge",
                    "applies_to": ["*"],
                    "details": "High complexity task requires stricter review",
                    "severity": "important",
                }
            )

        if complexity >= 5:
            behavior_rules.append(
                {
                    "rule": "Task decomposition should target 2-5 min per step",
                    "applies_to": ["*"],
                    "details": "Fine-grained tasks with exact file paths. "
                    "Each step should produce a verifiable artifact.",
                    "severity": "minor",
                }
            )

        if success_criteria:
            contract_rules.append(
                {
                    "rule": "Define acceptance scenarios BEFORE implementation",
                    "scope": "global",
                    "details": "TDD-inspired: WHEN/THEN scenarios must exist before code is written",
                    "severity": "important",
                }
            )

        return {
            "format": {"dependency_direction": [], "file_size_limit": {"lines": 300}},
            "contract": contract_rules,
            "behavior": behavior_rules,
        }

    def _generate_evolution_suggestions(self, task: str, findings: Dict) -> List[Dict]:
        suggestions = []

        missing = findings.get("missing_services", [])
        for svc in missing:
            suggestions.append(
                {
                    "id": f"EV-{len(suggestions) + 1:03d}",
                    "type": "create_service",
                    "service": svc.get("service", ""),
                    "description": f"Create spec for service: {svc.get('service', '')} - {svc.get('issue', '')}",
                    "priority": "high",
                }
            )

        incomplete = findings.get("incomplete_specs", [])
        for spec in incomplete:
            suggestions.append(
                {
                    "id": f"EV-{len(suggestions) + 1:03d}",
                    "type": "complete_spec",
                    "service": spec.get("service", ""),
                    "description": f"Complete spec for {spec.get('service', '')}: {spec.get('issue', '')}",
                    "priority": "medium",
                }
            )

        drift = findings.get("drift_indicators", [])
        for d in drift:
            suggestions.append(
                {
                    "id": f"EV-{len(suggestions) + 1:03d}",
                    "type": "fix_drift",
                    "description": f"Address goal drift: {d.get('message', '')}",
                    "priority": "high",
                }
            )

        return suggestions

    def _update_analysis_with_feedback(
        self, existing: Dict, feedback_type: str, content: str
    ) -> Dict:
        updated = dict(existing)
        updated.setdefault("feedback_history", []).append(
            {
                "type": feedback_type,
                "content": content,
            }
        )

        if feedback_type == "bug":
            updated.setdefault("risk_factors", []).append(
                f"Bug reported: {content[:80]}"
            )
            updated.setdefault("success_criteria", []).append(
                f"Bug fix verified: {content[:60]}"
            )
        elif feedback_type == "user_story":
            updated.setdefault("success_criteria", []).append(
                f"User story satisfied: {content[:60]}"
            )
        elif feedback_type == "constraint":
            updated.setdefault("risk_factors", []).append(
                f"New constraint: {content[:80]}"
            )

        return updated

    def _format_analysis_report(self, analysis: Dict, task: str) -> str:
        try:
            from pipeline.prompt_manager import PromptManager

            pm = PromptManager(project_path=str(self.project_path))
            risk_lines = "\n".join(f"- {r}" for r in analysis.get("risk_factors", []))
            criteria_lines = "\n".join(
                f"- {s}" for s in analysis.get("success_criteria", [])
            )
            spec_align = (
                f"\n## Spec Alignment\n{analysis['spec_alignment']}"
                if analysis.get("spec_alignment")
                else ""
            )
            return pm.render(
                "bmad-evo/analysis_report",
                task=task,
                task_type=analysis.get("task_type", "unknown"),
                complexity_score=str(analysis.get("complexity_score", 0)),
                recommended_roles=str(analysis.get("recommended_roles_count", 0)),
                estimated_duration=analysis.get("estimated_duration", "unknown"),
                risk_factors=risk_lines,
                success_criteria=criteria_lines,
                spec_alignment=spec_align,
            )
        except Exception:
            lines = [
                "# BMAD-EVO Analysis Report",
                "",
                f"## Task: {task}",
                "",
                f"- Task Type: {analysis.get('task_type', 'unknown')}",
                f"- Complexity: {analysis.get('complexity_score', 0)}/10",
                f"- Recommended Roles: {analysis.get('recommended_roles_count', 0)}",
                f"- Estimated Duration: {analysis.get('estimated_duration', 'unknown')}",
                "",
                "## Risk Factors",
            ]
            for r in analysis.get("risk_factors", []):
                lines.append(f"- {r}")
            lines.append("")
            lines.append("## Success Criteria")
            for s in analysis.get("success_criteria", []):
                lines.append(f"- {s}")
            if analysis.get("spec_alignment"):
                lines.append(f"\n## Spec Alignment\n{analysis['spec_alignment']}")
            return "\n".join(lines)

    def _format_constraints(self, constraints: Dict) -> str:
        try:
            from pipeline.prompt_manager import PromptManager

            pm = PromptManager(project_path=str(self.project_path))
            contract_lines = "\n".join(
                f"- [CONTRACT] {r['rule']}" for r in constraints.get("contract", [])
            )
            behavior_lines = "\n".join(
                f"- [BEHAVIOR] {r['rule']}" for r in constraints.get("behavior", [])
            )
            return pm.render(
                "bmad-evo/constraint_report",
                contract_rules=contract_lines,
                behavior_rules=behavior_lines,
            )
        except Exception:
            lines = ["# Generated Constraints (for Spec-Kit)", ""]
            for rule in constraints.get("contract", []):
                lines.append(f"- [CONTRACT] {rule['rule']}")
            for rule in constraints.get("behavior", []):
                lines.append(f"- [BEHAVIOR] {rule['rule']}")
            return "\n".join(lines)

    def _format_evolution_report(self, suggestions: List[Dict]) -> str:
        lines = ["# Spec Evolution Suggestions", ""]
        severity_order = {"critical": 0, "important": 1, "minor": 2}
        sorted_suggestions = sorted(
            suggestions,
            key=lambda s: severity_order.get(s.get("severity", "minor"), 9),
        )
        for s in sorted_suggestions:
            sev = s.get("severity", "minor")
            lines.append(
                f"- [{s['id']}] [{sev.upper()}] [{s.get('priority', 'medium')}] "
                f"{s['description']}"
            )
        if sorted_suggestions:
            counts = {}
            for s in sorted_suggestions:
                sev = s.get("severity", "minor")
                counts[sev] = counts.get(sev, 0) + 1
            lines.append("")
            lines.append(
                f"Severity: "
                + " | ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
            )
        return "\n".join(lines)

    def _format_feedback_update(self, analysis: Dict, feedback_type: str) -> str:
        history = analysis.get("feedback_history", [])
        lines = [
            f"# Feedback Update ({feedback_type})",
            "",
            f"Total feedback items: {len(history)}",
        ]
        for h in history:
            lines.append(f"- [{h['type']}] {h['content'][:80]}")
        return "\n".join(lines)

    def _fallback_analysis(self, task: str, spec_context: str) -> Dict[str, Any]:
        return {
            "success": True,
            "artifacts": {
                "analysis_report": f"# BMAD-EVO Analysis (Fallback)\n\nTask: {task}\n\nNote: Original bmad-evo not available at {BMAD_ORIGINAL_PATH}\n\nSpec context: {spec_context[:200] if spec_context else 'N/A'}",
                "task_type": "unknown",
                "complexity_score": 5,
                "recommended_roles": 3,
                "risk_factors": ["Original bmad-evo not available"],
                "success_criteria": [],
            },
        }

    def can_handle(self, task_type: str, context: Dict) -> bool:
        return task_type in [
            "analysis",
            "design",
            "planning",
            "feasibility_study",
            "decision_support",
            "clarification",
            "evolution",
        ]

    def get_status(self) -> Dict[str, Any]:
        bridge_info = {"mode": "none", "patched": []}
        if _bridge:
            bridge_info = {
                "mode": _bridge.mode,
                "patched": getattr(_bridge, "_patched_modules", []),
                "api_configured": bool(_bridge.api_config.get("api_key")),
            }
        return {
            "name": self.name,
            "version": self.version,
            "available": self._bmad_available,
            "bmad_path": str(BMAD_ORIGINAL_PATH)
            if self._bmad_available
            else "not found",
            "bridge": bridge_info,
        }
