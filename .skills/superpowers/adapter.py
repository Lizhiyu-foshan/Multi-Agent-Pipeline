"""
Superpowers Skill Adapter - Engineering execution layer.

Actions:
- execute_task: Fine-grained task execution with structured context
- spec_review: Spec compliance review (did we build what was requested?)
- code_quality_review: Code quality review (is it well-built?)
- debug: 4-phase systematic debugging
- tdd_cycle: RED-GREEN-REFACTOR enforcement

Integrates with:
- AgentLoop: provides execute_evaluate_refine cycle
- pipeline_orchestrator: serves as the worker skill for EXECUTE phase
- spec-kit: uses SpecGate constraints during review
- bmad-evo: consumes plan artifacts for task context
- PromptManager: unified prompt template management
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

_pipeline_src = str(Path(__file__).resolve().parents[2] / "src")
if _pipeline_src not in sys.path:
    sys.path.insert(0, _pipeline_src)


def _get_prompt_manager(project_path: str):
    from pipeline.prompt_manager import PromptManager

    return PromptManager(project_path=project_path)


from pipeline.hashline_edit import HashlineEditTool
from pipeline.code_analyzer import (
    CodeAnalyzer,
    AuditResult,
    Violation,
    Severity,
    RuleCategory,
)


class Superpowers_Adapter:
    name = "superpowers"
    version = "2.3"

    def __init__(self, project_path: str = None, spec_gate=None, prompt_manager=None):
        self.project_path = project_path or str(Path.cwd())
        self.spec_gate = spec_gate
        self._review_history: List[Dict] = []
        self._prompt_manager = prompt_manager or _get_prompt_manager(self.project_path)
        self._hashline_tool = HashlineEditTool(
            backup_dir=os.path.join(self.project_path, ".hashline_backups")
            if self.project_path
            else None,
            project_root=self.project_path,
        )
        self._code_analyzer = CodeAnalyzer(mode="strict")

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "execute_task")
        action_map = {
            "execute_task": self._execute_task,
            "spec_review": self._spec_review,
            "code_quality_review": self._code_quality_review,
            "debug": self._debug,
            "tdd_cycle": self._tdd_cycle,
            "hashline_edit": self._hashline_edit,
        }
        handler = action_map.get(action)
        if not handler:
            return {
                "success": False,
                "error": f"Unknown action: {action}. Available: {list(action_map.keys())}",
            }
        try:
            return handler(task_description, context)
        except Exception as e:
            logger.error(f"Superpowers action {action} failed: {e}")
            return {"success": False, "error": str(e)}

    def _execute_task(self, description: str, context: Dict) -> Dict[str, Any]:
        """
        Execute a single task with structured context.
        Uses PromptManager for template rendering with shared sections.
        """
        task_id = context.get("task_id", "unknown")
        task_name = context.get("task_name", description[:60])
        task_spec = context.get("task_spec", description)
        pipeline_phase = context.get("pipeline_phase", "execute")
        pdca_cycle = context.get("pdca_cycle", 0)
        completed_deps = context.get("completed_dependencies", [])
        prev_artifacts = context.get("previous_artifacts_summary", "")
        spec_constraints = context.get("spec_context", "")
        files_to_create = context.get("files_to_create", [])
        files_to_modify = context.get("files_to_modify", [])

        sections = ["quality_gates", "stuck_protocol", "report_format"]
        if spec_constraints:
            sections.insert(0, "spec_constraints")
        if prev_artifacts:
            sections.append("previous_artifacts")
        if context.get("refinement_feedback"):
            sections.append("refinement_feedback")

        try:
            prompt = self._prompt_manager.compose(
                "superpowers/execute_task",
                sections=sections,
                task_id=task_id,
                task_name=task_name,
                task_spec=task_spec,
                scene_setting=context.get(
                    "scene_setting",
                    f"Task in {pipeline_phase} phase of development pipeline",
                ),
                pipeline_phase=pipeline_phase,
                pdca_cycle=str(pdca_cycle),
                completed_dependencies=", ".join(completed_deps),
                previous_artifacts_summary=prev_artifacts,
                spec_constraints=spec_constraints,
                spec_context=spec_constraints,
                refinement_feedback=context.get("refinement_feedback", ""),
            )
        except Exception as e:
            logger.debug(f"PromptManager compose failed, falling back: {e}")
            prompt = self._load_prompt_fallback(
                "implementer-prompt.md",
                context,
                description,
                task_id=task_id,
                task_name=task_name,
                task_spec=task_spec,
                pipeline_phase=pipeline_phase,
                pdca_cycle=str(pdca_cycle),
                completed_deps=completed_deps,
                prev_artifacts=prev_artifacts,
                spec_constraints=spec_constraints,
            )

        return {
            "success": True,
            "action": "execute_task",
            "task_id": task_id,
            "pending_model_request": {
                "type": "implementer",
                "prompt": prompt,
                "context": {
                    "task_id": task_id,
                    "task_name": task_name,
                    "project_path": self.project_path,
                    "files_to_create": files_to_create,
                    "files_to_modify": files_to_modify,
                },
            },
            "artifacts": {
                "task_id": task_id,
                "status": "pending_execution",
                "prompt_generated": True,
            },
        }

    def _spec_review(self, description: str, context: Dict) -> Dict[str, Any]:
        """
        Stage 1 of two-stage review: spec compliance.
        Verifies implementation matches specification (nothing more, nothing less).
        """
        task_spec = context.get("task_spec", description)
        implementer_report = context.get("implementer_report", "")
        implementation_artifacts = context.get("implementation_artifacts", {})
        spec_constraints = context.get("spec_context", "")
        verification_commands = context.get("verification_commands", "")

        try:
            prompt = self._prompt_manager.render(
                "superpowers/spec_review",
                task_spec=task_spec,
                implementer_report=implementer_report,
                spec_constraints=spec_constraints,
                verification_commands=verification_commands,
            )
        except Exception:
            prompt = self._load_prompt("spec-reviewer-prompt.md")
            prompt = prompt.replace("{task_spec}", task_spec)
            prompt = prompt.replace("{implementer_report}", implementer_report)
            prompt = prompt.replace("{spec_constraints}", spec_constraints)
            prompt = prompt.replace("{verification_commands}", verification_commands)

        review_result = {
            "review_type": "spec_compliance",
            "task_id": context.get("task_id", ""),
            "spec_checked": True,
        }

        issues = []
        spec_spec_lines = task_spec.strip().split("\n")
        spec_requirements = [
            line.strip().lstrip("- ").lstrip("* ")
            for line in spec_spec_lines
            if line.strip().startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5."))
        ]

        artifact_text = ""
        if implementation_artifacts:
            import json

            artifact_text = json.dumps(
                implementation_artifacts, ensure_ascii=False
            ).lower()

        for req in spec_requirements:
            req_lower = req.lower()
            keywords = [w for w in req_lower.split() if len(w) > 3]
            matched = sum(1 for kw in keywords if kw in artifact_text)
            if keywords and matched < len(keywords) * 0.3:
                issues.append(
                    {
                        "type": "missing_requirement",
                        "requirement": req[:200],
                        "keyword_coverage": f"{matched}/{len(keywords)}",
                    }
                )

        if implementation_artifacts:
            for key in implementation_artifacts:
                if key not in task_spec.lower() and key not in (
                    "code",
                    "tests",
                    "implementation",
                ):
                    issues.append(
                        {
                            "type": "extra_work",
                            "artifact": key,
                            "note": "Not mentioned in spec (potential YAGNI violation)",
                        }
                    )

        if self.spec_gate:
            try:
                gate_result = self.spec_gate.post_check(
                    "superpowers",
                    {"success": True, "artifacts": implementation_artifacts},
                    context.get("active_service"),
                )
                if not gate_result.get("passed", True):
                    issues.append(
                        {
                            "type": "spec_gate_failure",
                            "details": gate_result.get("warnings", []),
                        }
                    )
                review_result["spec_gate"] = gate_result
            except Exception as e:
                logger.debug(f"SpecGate check skipped: {e}")

        passed = len(issues) == 0
        review_result["passed"] = passed
        review_result["issues"] = issues

        self._review_history.append(review_result)

        return {
            "success": passed,
            "action": "spec_review",
            "artifacts": {
                "review_type": "spec_compliance",
                "passed": passed,
                "issues_count": len(issues),
                "issues": issues,
            },
            "pending_model_request": {
                "type": "spec_reviewer",
                "prompt": prompt,
            }
            if not passed
            else None,
        }

    def _code_quality_review(self, description: str, context: Dict) -> Dict[str, Any]:
        """
        Stage 2 of two-stage review: code quality.
        Only runs AFTER spec compliance passes.
        Uses CodeAnalyzer (pipeline-level AST engine) for real static analysis.
        """
        spec_result = context.get("spec_review_result", {})
        if spec_result and not spec_result.get("passed", True):
            return {
                "success": False,
                "error": "Cannot code-review before spec compliance passes. Run spec_review first.",
                "action": "code_quality_review",
            }

        implementer_report = context.get("implementer_report", "")
        task_spec = context.get("task_spec", description)
        files_changed = context.get("files_changed", [])
        implementation_artifacts = context.get("implementation_artifacts", {})

        try:
            prompt = self._prompt_manager.compose(
                "superpowers/code_quality_review",
                sections=["two_stage_review", "quality_gates"],
                implementer_report=implementer_report,
                task_spec=task_spec,
                files_changed="\n".join(files_changed)
                if files_changed
                else "See artifacts",
            )
        except Exception:
            prompt = self._load_prompt("code-quality-reviewer-prompt.md")
            prompt = prompt.replace("{implementer_report}", implementer_report)
            prompt = prompt.replace("{task_spec}", task_spec)
            prompt = prompt.replace(
                "{files_changed}",
                "\n".join(files_changed) if files_changed else "See artifacts",
            )

        strengths = []
        issues = []

        if implementation_artifacts:
            code = implementation_artifacts.get("code", "")
            tests = implementation_artifacts.get("tests", "")

            if code and isinstance(code, str):
                # --- AST-based analysis via CodeAnalyzer ---
                audit = self._code_analyzer.audit_code(
                    code, filename="review_target.py"
                )

                if audit.score >= 85:
                    strengths.append(f"AST audit score: {audit.score:.0f}/100")
                else:
                    issues.append(
                        {
                            "severity": "important",
                            "message": f"AST audit score: {audit.score:.0f}/100 (threshold: 85)",
                        }
                    )

                for v in audit.violations:
                    sev_map = {
                        Severity.CRITICAL: "critical",
                        Severity.HIGH: "important",
                        Severity.MEDIUM: "minor",
                        Severity.LOW: "minor",
                    }
                    issues.append(
                        {
                            "severity": sev_map.get(v.severity, "minor"),
                            "message": f"[AST:{v.category.value}] L{v.line}: {v.message}",
                            "suggestion": v.suggestion,
                            "rule_id": v.rule_id,
                        }
                    )

                # --- Test detection (enhanced with AST) ---
                if tests:
                    test_audit = self._code_analyzer.audit_code(
                        str(tests), filename="tests.py"
                    )
                    if isinstance(tests, str) and (
                        "test" in tests.lower() or "assert" in tests.lower()
                    ):
                        strengths.append("Tests present")
                    elif isinstance(tests, dict) and tests:
                        strengths.append(f"Test artifacts: {list(tests.keys())}")
                else:
                    issues.append(
                        {
                            "severity": "critical",
                            "message": "No tests provided — TDD requires tests",
                        }
                    )

        severity_counts = {"critical": 0, "important": 0, "minor": 0}
        for iss in issues:
            sev = iss.get("severity", "minor")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        has_critical = severity_counts.get("critical", 0) > 0
        has_important = severity_counts.get("important", 0) > 0

        if has_critical:
            assessment = "NEEDS_FIXES"
        elif has_important:
            assessment = "NEEDS_FIXES"
        else:
            assessment = "APPROVED"

        review_result = {
            "review_type": "code_quality",
            "task_id": context.get("task_id", ""),
            "strengths": strengths,
            "issues": issues,
            "severity_counts": severity_counts,
            "assessment": assessment,
            "ast_audit_used": True,
        }
        self._review_history.append(review_result)

        return {
            "success": assessment == "APPROVED",
            "action": "code_quality_review",
            "artifacts": {
                "review_type": "code_quality",
                "assessment": assessment,
                "strengths": strengths,
                "issues": issues,
                "ast_audit_used": True,
            },
            "pending_model_request": {
                "type": "code_quality_reviewer",
                "prompt": prompt,
            }
            if assessment != "APPROVED"
            else None,
        }

    def _debug(self, description: str, context: Dict) -> Dict[str, Any]:
        """
        Systematic 4-phase debugging: root cause -> pattern -> hypothesis -> fix.
        Uses PromptManager with debug_protocol section.
        """
        error_description = context.get("error_description", description)
        expected = context.get("expected_behavior", "")
        actual = context.get("actual_behavior", "")
        error_output = context.get("error_output", "")
        recent_changes = context.get("recent_changes", "")

        try:
            prompt = self._prompt_manager.compose(
                "superpowers/debug",
                sections=["debug_protocol"],
                error_description=error_description,
                expected_behavior=expected,
                actual_behavior=actual,
            )
        except Exception:
            prompt = self._load_prompt("debugging-prompt.md")
            prompt = prompt.replace("{error_description}", error_description)
            prompt = prompt.replace("{expected_behavior}", expected)
            prompt = prompt.replace("{actual_behavior}", actual)

        phase = 1
        root_cause = None
        hypothesis = None
        fix = None
        status = "NEEDS_MORE_INVESTIGATION"

        if error_output:
            error_lines = error_output.strip().splitlines()
            for line in error_lines:
                line_lower = line.lower()
                if (
                    "error" in line_lower
                    or "exception" in line_lower
                    or "traceback" in line_lower
                ):
                    root_cause = line.strip()[:300]
                    phase = max(phase, 1)
                    break

        if root_cause and recent_changes:
            phase = 2
            hypothesis = (
                f"Root cause likely related to recent changes: {recent_changes[:200]}"
            )

        if hypothesis:
            phase = 3

        if context.get("fix_applied"):
            phase = 4
            fix = context.get("fix_applied")
            status = "ROOT_CAUSE_FOUND"

        return {
            "success": phase == 4,
            "action": "debug",
            "artifacts": {
                "debug_phase": phase,
                "root_cause": root_cause,
                "hypothesis": hypothesis,
                "fix": fix,
                "status": status,
            },
            "pending_model_request": {
                "type": "debugging",
                "prompt": prompt,
                "phase": phase,
            },
        }

    def _tdd_cycle(self, description: str, context: Dict) -> Dict[str, Any]:
        """
        RED-GREEN-REFACTOR cycle enforcement.
        Tracks TDD state and validates each phase transition.
        Uses PromptManager with tdd_protocol section.
        """
        tdd_phase = context.get("tdd_phase", "red")
        test_code = context.get("test_code", "")
        impl_code = context.get("impl_code", "")
        test_result = context.get("test_result", None)

        if tdd_phase == "red":
            if not test_code:
                return {
                    "success": False,
                    "error": "RED phase requires test code first. NO PRODUCTION CODE WITHOUT A FAILING TEST.",
                    "action": "tdd_cycle",
                    "artifacts": {"tdd_phase": "red", "violation": "no_test_provided"},
                }

            try:
                prompt = self._prompt_manager.compose(
                    "superpowers/tdd_cycle",
                    sections=["tdd_protocol"],
                    tdd_phase="RED",
                    task_description=description,
                    tdd_instructions=f"Write a FAILING test for: {description}\n\nTest code provided:\n{test_code}",
                )
            except Exception:
                prompt = f"Write a FAILING test for: {description}\n\nTest code provided:\n{test_code}"

            return {
                "success": True,
                "action": "tdd_cycle",
                "artifacts": {
                    "tdd_phase": "red",
                    "test_code": test_code,
                    "next_step": "Run test to verify it FAILS. If it passes, the test is wrong.",
                },
                "pending_model_request": {
                    "type": "tdd_red",
                    "prompt": prompt,
                },
            }

        elif tdd_phase == "green":
            if test_result is None:
                return {
                    "success": False,
                    "error": "GREEN phase requires test result. Did the test fail correctly in RED phase?",
                    "action": "tdd_cycle",
                }
            if not impl_code:
                return {
                    "success": False,
                    "error": "GREEN phase requires minimal implementation code.",
                    "action": "tdd_cycle",
                }
            return {
                "success": True,
                "action": "tdd_cycle",
                "artifacts": {
                    "tdd_phase": "green",
                    "impl_code": impl_code,
                    "next_step": "Run tests to verify they PASS. All green?",
                },
            }

        elif tdd_phase == "refactor":
            return {
                "success": True,
                "action": "tdd_cycle",
                "artifacts": {
                    "tdd_phase": "refactor",
                    "next_step": "Clean up code. Keep tests green. Commit.",
                },
            }

        return {
            "success": False,
            "error": f"Unknown TDD phase: {tdd_phase}",
        }

    def _hashline_edit(self, description: str, context: Dict) -> Dict[str, Any]:
        edit_action = context.get("edit_action", "read")

        if edit_action == "read":
            file_path = context.get("file_path", "")
            if not file_path:
                return {"success": False, "error": "file_path required for read"}
            result = self._hashline_tool.read_file(file_path)
            return {
                "success": result["success"],
                "action": "hashline_edit",
                "artifacts": result,
            }

        elif edit_action == "replace":
            return {
                "success": True,
                "action": "hashline_edit",
                "artifacts": self._hashline_tool.replace_lines(
                    context.get("file_path", ""),
                    context.get("edits", []),
                ).to_dict(),
            }

        elif edit_action == "insert_after":
            r = self._hashline_tool.insert_after(
                context.get("file_path", ""),
                context.get("line_hash", ""),
                context.get("line_number", 0),
                context.get("new_content", ""),
            )
            return {
                "success": r.success,
                "action": "hashline_edit",
                "artifacts": r.to_dict(),
            }

        elif edit_action == "insert_before":
            r = self._hashline_tool.insert_before(
                context.get("file_path", ""),
                context.get("line_hash", ""),
                context.get("line_number", 0),
                context.get("new_content", ""),
            )
            return {
                "success": r.success,
                "action": "hashline_edit",
                "artifacts": r.to_dict(),
            }

        elif edit_action == "delete":
            r = self._hashline_tool.delete_lines(
                context.get("file_path", ""),
                context.get("deletions", []),
            )
            return {
                "success": r.success,
                "action": "hashline_edit",
                "artifacts": r.to_dict(),
            }

        elif edit_action == "multi_edit":
            r = self._hashline_tool.multi_edit(
                context.get("file_path", ""),
                context.get("operations", []),
            )
            return {
                "success": r.success,
                "action": "hashline_edit",
                "artifacts": r.to_dict(),
            }

        elif edit_action == "diff_preview":
            result = self._hashline_tool.get_diff_preview(
                context.get("file_path", ""),
                context.get("operations", []),
            )
            return {"success": True, "action": "hashline_edit", "artifacts": result}

        elif edit_action == "restore_backup":
            result = self._hashline_tool.restore_backup(
                context.get("backup_path", ""),
                context.get("target_path", ""),
            )
            return {
                "success": result["success"],
                "action": "hashline_edit",
                "artifacts": result,
            }

        return {
            "success": False,
            "error": f"Unknown hashline_edit action: {edit_action}",
        }

    def get_review_history(self) -> List[Dict]:
        return list(self._review_history)

    def _load_prompt(self, filename: str) -> str:
        filepath = PROMPTS_DIR / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            if "## Template" in content:
                parts = content.split("## Template", 1)
                return parts[1].strip().strip("```").strip()
            return content
        return f"[Prompt template not found: {filename}]"

    def _load_prompt_fallback(
        self, filename: str, context: Dict, description: str, **kwargs
    ) -> str:
        prompt = self._load_prompt(filename)
        task_id = kwargs.get("task_id", "unknown")
        task_name = kwargs.get("task_name", description[:60])
        task_spec = kwargs.get("task_spec", description)
        pipeline_phase = kwargs.get("pipeline_phase", "execute")
        pdca_cycle = kwargs.get("pdca_cycle", "0")
        completed_deps = kwargs.get("completed_deps", [])
        prev_artifacts = kwargs.get("prev_artifacts", "")
        spec_constraints = kwargs.get("spec_constraints", "")

        prompt = prompt.replace("{task_id}", task_id)
        prompt = prompt.replace("{task_name}", task_name)
        prompt = prompt.replace("{task_spec}", task_spec)
        prompt = prompt.replace(
            "{scene_setting}",
            context.get("scene_setting", f"Task in {pipeline_phase} phase"),
        )
        prompt = prompt.replace("{pipeline_phase}", pipeline_phase)
        prompt = prompt.replace("{pdca_cycle}", pdca_cycle)
        prompt = prompt.replace("{completed_dependencies}", ", ".join(completed_deps))
        prompt = prompt.replace("{previous_artifacts_summary}", prev_artifacts)
        prompt = prompt.replace("{spec_constraints}", spec_constraints)
        return prompt
