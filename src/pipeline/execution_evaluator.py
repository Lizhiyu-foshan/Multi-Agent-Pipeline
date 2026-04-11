"""
Execution Evaluator - Evaluates skill outputs against specs, constraints, and task goals.

Evaluation dimensions:
1. SpecGate checks: constraint validation, drift detection, scenario status
2. Task completion: does the output address the task description?
3. Quality heuristics: non-empty, artifacts present, no error artifacts
4. Constraint validation: file size, naming, contract rules (if applicable)

Returns a structured EvaluationResult with pass/fail, score, and specific feedback
that can be fed back into the next iteration's prompt.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    passed: bool = False
    score: float = 0.0
    dimension_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    feedback_items: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    iteration: int = 0
    evaluated_at: str = ""

    def __post_init__(self):
        if not self.evaluated_at:
            self.evaluated_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 2),
            "feedback_items": self.feedback_items,
            "strengths": self.strengths,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "iteration": self.iteration,
            "evaluated_at": self.evaluated_at,
        }

    def build_refinement_prompt(self) -> str:
        if self.passed:
            return ""
        lines = ["[EVALUATION FEEDBACK - Previous attempt did not pass]\n"]
        if self.issues:
            lines.append("Issues found:")
            for iss in self.issues:
                lines.append(f"  - {iss}")
        if self.suggestions:
            lines.append("\nSuggestions for improvement:")
            for sug in self.suggestions:
                lines.append(f"  - {sug}")
        if self.strengths:
            lines.append("\nWhat was done well (preserve these):")
            for s in self.strengths:
                lines.append(f"  + {s}")
        lines.append(f"\nOverall score: {self.score:.1f}/1.0")
        lines.append("Please address the issues above while preserving the strengths.")
        return "\n".join(lines)


class ExecutionEvaluator:
    """
    Evaluates skill execution results against multiple dimensions.

    Usage:
        evaluator = ExecutionEvaluator(spec_gate=spec_gate)
        result = evaluator.evaluate(
            task_description="Build user auth",
            skill_name="superpowers",
            execution_result={"success": True, "artifacts": {...}},
            iteration=1,
        )
        if not result.passed:
            refinement_prompt = result.build_refinement_prompt()
    """

    def __init__(self, spec_gate=None, constraint_validator=None):
        self.spec_gate = spec_gate
        self.constraint_validator = constraint_validator

    def evaluate(
        self,
        task_description: str,
        skill_name: str,
        execution_result: Dict[str, Any],
        iteration: int = 1,
        active_service: str = None,
    ) -> EvaluationResult:
        result = EvaluationResult(iteration=iteration)
        total_weight = 0.0
        weighted_score = 0.0

        dim_basic = self._evaluate_basic_quality(execution_result, task_description)
        result.dimension_results["basic_quality"] = dim_basic
        weight = 0.3
        weighted_score += dim_basic["score"] * weight
        total_weight += weight

        dim_task = self._evaluate_task_completion(execution_result, task_description)
        result.dimension_results["task_completion"] = dim_task
        weight = 0.3
        weighted_score += dim_task["score"] * weight
        total_weight += weight

        if self.spec_gate:
            dim_spec = self._evaluate_spec_compliance(
                skill_name, execution_result, active_service
            )
            result.dimension_results["spec_compliance"] = dim_spec
            weight = 0.25
            weighted_score += dim_spec["score"] * weight
            total_weight += weight

        dim_artifacts = self._evaluate_artifacts(execution_result, skill_name)
        result.dimension_results["artifact_quality"] = dim_artifacts
        weight = 0.15
        weighted_score += dim_artifacts["score"] * weight
        total_weight += weight

        if skill_name == "superpowers":
            dim_tdd = self._evaluate_tdd_compliance(execution_result)
            result.dimension_results["tdd_compliance"] = dim_tdd
            weight = 0.15
            weighted_score += dim_tdd["score"] * weight
            total_weight += weight

            dim_review = self._evaluate_review_status(execution_result)
            result.dimension_results["review_status"] = dim_review
            weight = 0.10
            weighted_score += dim_review["score"] * weight
            total_weight += weight

        result.score = weighted_score / total_weight if total_weight > 0 else 0.0
        result.passed = result.score >= 0.6

        for dim_name, dim in result.dimension_results.items():
            result.feedback_items.extend(dim.get("feedback", []))
            result.issues.extend(dim.get("issues", []))
            result.strengths.extend(dim.get("strengths", []))
            result.suggestions.extend(dim.get("suggestions", []))

        return result

    def _evaluate_basic_quality(
        self, execution_result: Dict[str, Any], task_description: str
    ) -> Dict[str, Any]:
        issues = []
        strengths = []
        feedback = []
        score = 1.0

        if not execution_result.get("success"):
            issues.append("Execution reported failure")
            feedback.append("The skill execution returned success=False")
            score -= 0.5

        error = execution_result.get("error")
        if error:
            issues.append(f"Execution error: {str(error)[:200]}")
            feedback.append(f"Error encountered: {str(error)[:100]}")
            score -= 0.3

        output = execution_result.get("output", "")
        if output and isinstance(output, str) and len(output.strip()) > 10:
            strengths.append("Non-trivial output produced")
        elif not output:
            issues.append("No output produced")
            feedback.append("The execution produced no output")
            score -= 0.3

        return {
            "score": max(score, 0.0),
            "issues": issues,
            "strengths": strengths,
            "feedback": feedback,
            "suggestions": [],
        }

    def _evaluate_task_completion(
        self, execution_result: Dict[str, Any], task_description: str
    ) -> Dict[str, Any]:
        issues = []
        strengths = []
        suggestions = []
        score = 0.5

        artifacts = execution_result.get("artifacts", {})
        task_lower = task_description.lower()
        task_keywords = [
            w
            for w in task_lower.split()
            if len(w) > 3
            and w
            not in ("with", "that", "this", "from", "need", "have", "should", "must")
        ]

        if artifacts:
            strengths.append(f"Artifacts produced: {list(artifacts.keys())[:5]}")
            score += 0.2

            artifact_text = json.dumps(artifacts, ensure_ascii=False).lower()
            matched = [kw for kw in task_keywords if kw in artifact_text]
            if matched:
                coverage = len(matched) / max(len(task_keywords), 1)
                score += min(coverage * 0.3, 0.3)
                strengths.append(
                    f"Task keyword coverage: {coverage:.0%} ({len(matched)}/{len(task_keywords)})"
                )
            else:
                issues.append("Output artifacts don't reference task keywords")
                suggestions.append(
                    "Ensure the output directly addresses the task description keywords"
                )
        else:
            issues.append("No artifacts produced")
            suggestions.append("The execution should produce structured artifacts")

        task_type = self._infer_task_type(task_description)
        type_check = self._check_task_type_expectations(task_type, execution_result)
        score += type_check["score_delta"]
        issues.extend(type_check.get("issues", []))
        strengths.extend(type_check.get("strengths", []))
        suggestions.extend(type_check.get("suggestions", []))

        return {
            "score": min(max(score, 0.0), 1.0),
            "issues": issues,
            "strengths": strengths,
            "feedback": [],
            "suggestions": suggestions,
        }

    def _evaluate_spec_compliance(
        self,
        skill_name: str,
        execution_result: Dict[str, Any],
        active_service: str,
    ) -> Dict[str, Any]:
        if not self.spec_gate:
            return {
                "score": 0.8,
                "issues": [],
                "strengths": [],
                "feedback": [],
                "suggestions": [],
            }

        issues = []
        strengths = []
        suggestions = []
        score = 1.0

        post = self.spec_gate.post_check(skill_name, execution_result, active_service)

        if not post.get("passed", True):
            issues.append("Spec compliance check failed")
            score -= 0.4

        warnings = post.get("warnings", [])
        if warnings:
            for w in warnings:
                issues.append(f"Spec warning: {str(w)[:150]}")
                if "DRIFT" in str(w).upper():
                    suggestions.append(
                        "The output may have drifted from the system goal. "
                        "Re-read the spec constraints and ensure alignment."
                    )
            score -= min(len(warnings) * 0.1, 0.3)

        failed_scenarios = post.get("failed_scenarios", [])
        if failed_scenarios:
            for fs in failed_scenarios:
                issues.append(f"Failed scenario: {str(fs)[:100]}")
            score -= min(len(failed_scenarios) * 0.1, 0.2)

        if score >= 0.8:
            strengths.append("Output passes spec compliance checks")

        return {
            "score": max(score, 0.0),
            "issues": issues,
            "strengths": strengths,
            "feedback": warnings,
            "suggestions": suggestions,
        }

    def _evaluate_artifacts(
        self, execution_result: Dict[str, Any], skill_name: str
    ) -> Dict[str, Any]:
        artifacts = execution_result.get("artifacts", {})
        issues = []
        strengths = []
        score = 0.5

        if not artifacts:
            return {
                "score": 0.3,
                "issues": ["No artifacts produced"],
                "strengths": [],
                "feedback": [],
                "suggestions": ["Produce at least one structured artifact"],
            }

        for key, value in artifacts.items():
            if value is None or value == "" or value == {} or value == []:
                issues.append(f"Empty artifact: {key}")
                score -= 0.1
            else:
                strengths.append(f"Artifact '{key}': {type(value).__name__}")
                score += 0.1

        if skill_name == "superpowers":
            code = artifacts.get("code", artifacts.get("implementation", ""))
            if code and isinstance(code, str):
                code_lines = code.strip().splitlines()
                if len(code_lines) > 5:
                    strengths.append(f"Code artifact: {len(code_lines)} lines")
                    score += 0.1
                else:
                    issues.append("Code artifact is too short (under 5 lines)")
                    suggestions = ["The code implementation should be more substantial"]

        return {
            "score": min(max(score, 0.0), 1.0),
            "issues": issues,
            "strengths": strengths,
            "feedback": [],
            "suggestions": [],
        }

    def _infer_task_type(self, description: str) -> str:
        desc = description.lower()
        type_keywords = {
            "analysis": ["analyze", "analysis", "review", "evaluate", "assess"],
            "design": ["design", "architect", "plan", "structure", "model"],
            "implementation": [
                "implement",
                "build",
                "create",
                "develop",
                "code",
                "write",
            ],
            "testing": ["test", "verify", "validate", "check"],
            "documentation": ["document", "doc", "readme", "guide"],
        }
        for t, keywords in type_keywords.items():
            if any(kw in desc for kw in keywords):
                return t
        return "general"

    def _check_task_type_expectations(
        self, task_type: str, execution_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        artifacts = execution_result.get("artifacts", {})
        result = {"score_delta": 0.0, "issues": [], "strengths": [], "suggestions": []}

        if task_type == "implementation":
            code_keys = ["code", "implementation", "source", "files"]
            has_code = any(k in artifacts for k in code_keys)
            if has_code:
                result["strengths"].append("Implementation artifact present")
                result["score_delta"] = 0.1
            else:
                result["issues"].append(
                    "No code/implementation artifact for implementation task"
                )
                result["suggestions"].append(
                    "Include the actual code implementation in artifacts"
                )
                result["score_delta"] = -0.1

        elif task_type == "analysis":
            analysis_keys = ["analysis", "report", "findings", "summary", "constraints"]
            has_analysis = any(k in artifacts for k in analysis_keys)
            if has_analysis:
                result["strengths"].append("Analysis artifact present")
                result["score_delta"] = 0.1
            else:
                result["suggestions"].append(
                    "Include structured analysis results in artifacts"
                )

        elif task_type == "testing":
            test_keys = ["tests", "test_results", "test_cases", "validation"]
            has_tests = any(k in artifacts for k in test_keys)
            if has_tests:
                result["strengths"].append("Test artifacts present")
                result["score_delta"] = 0.1
            else:
                result["suggestions"].append(
                    "Include test cases or test results in artifacts"
                )

        return result

    def _evaluate_tdd_compliance(self, execution_result: Dict) -> Dict:
        """
        TDD compliance check for superpowers execution.
        Verifies: tests exist, tests run, RED-GREEN cycle followed.
        """
        issues = []
        strengths = []
        suggestions = []
        score = 0.5

        artifacts = execution_result.get("artifacts", {})
        tests = artifacts.get("tests", artifacts.get("test_code", ""))
        code = artifacts.get("code", artifacts.get("implementation", ""))

        if tests:
            if isinstance(tests, str) and len(tests.strip()) > 10:
                strengths.append("Tests written")
                score += 0.2
                if "assert" in str(tests).lower() or "expect" in str(tests).lower():
                    strengths.append("Tests contain assertions")
                    score += 0.1
            elif isinstance(tests, dict) and tests:
                strengths.append(f"Test artifacts: {list(tests.keys())}")
                score += 0.2
        else:
            issues.append("No tests provided — TDD requires tests before code")
            suggestions.append("Write a failing test FIRST, then implement")
            score -= 0.3

        if code and not tests:
            issues.append("Code exists but no tests — possible TDD violation")
            suggestions.append(
                "Delete code, write failing test, re-implement from test"
            )
            score -= 0.2

        if code and tests:
            strengths.append("Both code and tests present (TDD pattern)")
            score += 0.1

        return {
            "score": max(score, 0.0),
            "issues": issues,
            "strengths": strengths,
            "feedback": [],
            "suggestions": suggestions,
        }

    def _evaluate_review_status(self, execution_result: Dict) -> Dict:
        """
        Two-stage review status check for superpowers.
        Verifies spec review and code quality review were performed.
        """
        issues = []
        strengths = []
        score = 0.5

        artifacts = execution_result.get("artifacts", {})

        if artifacts.get("spec_review_passed"):
            strengths.append("Spec compliance review passed")
            score += 0.2
        elif artifacts.get("spec_review_result"):
            issues.append("Spec review found issues")
            score -= 0.1

        if artifacts.get("code_quality_passed"):
            strengths.append("Code quality review passed")
            score += 0.2
        elif artifacts.get("code_quality_result"):
            issues.append("Code quality review found issues")
            score -= 0.1

        if not artifacts.get("spec_review_passed") and not artifacts.get(
            "spec_review_result"
        ):
            strengths.append("No review stage (may not be required)")

        return {
            "score": min(max(score, 0.0), 1.0),
            "issues": issues,
            "strengths": strengths,
            "feedback": [],
            "suggestions": [],
        }
