"""
BMAD-EVO Skill Adapter
Design Architect & Project Planner for Multi-Agent Pipeline.

Wraps the real bmad-evo framework (D:/bmad-evo) without modifying it.
References original code via sys.path.

Role: Project designer/architect responsible for:
- Overall project design, construction plan, task decomposition, role assignment
- Post-PDCA cycle evaluation and project document updates
- Requirement refinement into structured design docs and development plans

Actions:
- analyze: Requirement analysis → structured design document with roles + tasks
- plan: Create execution plan with task_graph, dependencies, execution waves
- deep_analysis: Full multi-agent workflow via WorkflowOrchestratorV3Final
- clarify: Multi-round requirement clarification
- generate_constraints: Produce constraint rules from analysis (for spec-kit)
- spec_evolution: Analyze existing specs and suggest improvements
- update_for_feedback: Handle new user story / bug, update analysis
- eval_and_update: Post-PDCA evaluation → update project docs and task list
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
            "plan": self._handle_plan,
            "deep_analysis": self._handle_deep_analysis,
            "clarify": self._handle_clarify,
            "generate_constraints": self._handle_generate_constraints,
            "spec_evolution": self._handle_spec_evolution,
            "update_for_feedback": self._handle_update_for_feedback,
            "eval_and_update": self._handle_eval_and_update,
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

            artifacts = {
                "analysis_report": self._format_analysis_report(
                    enriched, task_description
                ),
                "task_type": enriched.get("task_type", "unknown"),
                "complexity_score": enriched.get("complexity_score", 0),
                "recommended_roles": enriched.get("recommended_roles_count", 0),
                "risk_factors": enriched.get("risk_factors", []),
                "success_criteria": enriched.get("success_criteria", []),
                "spec_alignment": enriched.get("spec_alignment", ""),
            }

            parsed_roles = enriched.get("roles", [])
            parsed_tasks = enriched.get("tasks", [])
            if parsed_roles:
                artifacts["roles"] = parsed_roles
            if parsed_tasks:
                artifacts["tasks"] = parsed_tasks

            return {
                "success": True,
                "artifacts": artifacts,
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

        roles = data.get("roles", [])
        if not roles and isinstance(data.get("recommended_roles"), list):
            roles = data["recommended_roles"]

        tasks = data.get("tasks", [])
        if not tasks and isinstance(data.get("task_breakdown"), list):
            tasks = data["task_breakdown"]

        artifacts = {
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
        }

        if roles:
            artifacts["roles"] = roles
        if tasks:
            artifacts["tasks"] = tasks

        return {
            "success": True,
            "artifacts": artifacts,
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

    def _handle_plan(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create structured execution plan with task_graph from analysis results.

        Expects context to contain analysis artifacts (roles, tasks) from
        the ANALYZE phase. Produces a task_graph with tasks, dependencies,
        and execution waves for the orchestrator to submit.
        """
        spec_context = context.get("spec_context", "")
        model_response = context.get("model_response")

        if model_response and context.get("model_request_id"):
            return self._continue_plan(model_response, task_description, context)

        analysis_artifacts = context.get("previous_artifacts_summary", "")
        if isinstance(analysis_artifacts, dict):
            analysis_artifacts = str(analysis_artifacts)

        roles_data = context.get("roles", [])
        tasks_data = context.get("tasks", [])

        if roles_data or tasks_data:
            task_graph = self._build_task_graph(tasks_data, roles_data)
            return {
                "success": True,
                "artifacts": {
                    "task_graph": task_graph,
                    "plan_report": self._format_plan_report(task_graph),
                    "roles": roles_data,
                    "total_tasks": len(task_graph.get("tasks", [])),
                    "execution_waves": len(task_graph.get("execution_waves", [])),
                },
            }

        plan_prompt = self._build_plan_prompt(
            task_description, analysis_artifacts, roles_data, tasks_data
        )

        if not self._bmad_available:
            return self._fallback_plan(task_description, plan_prompt)

        try:
            from task_analyzer import TaskAnalyzer
            from model_bridge import ModelBridge

            analyzer = TaskAnalyzer(timeout=120)
            analyzer.analyze(plan_prompt)

            if ModelBridge._pending_requests:
                latest = ModelBridge._pending_requests[-1]
                ModelBridge._pending_requests = []
                pending_req = {
                    "id": latest["id"],
                    "type": "chat",
                    "prompt": latest["prompt"],
                    "model": latest["model"],
                    "instructions": (
                        "Generate a structured execution plan as JSON with "
                        "task_graph containing tasks (each with name, description, "
                        "role, dependencies, priority) and execution_waves."
                    ),
                }
                return {
                    "success": False,
                    "pending_model_request": pending_req,
                    "model_request": pending_req,
                }
        except Exception as e:
            logger.warning(f"BMAD plan via model failed: {e}")

        return self._fallback_plan(task_description, plan_prompt)

    def _continue_plan(
        self, model_response: str, task_description: str, context: Dict
    ) -> Dict[str, Any]:
        try:
            import json as _json
            data = _json.loads(model_response)
        except Exception:
            data = {}

        task_graph = data.get("task_graph", {})
        if not task_graph.get("tasks"):
            raw_tasks = data.get("tasks", [])
            roles = data.get("roles", [])
            task_graph = self._build_task_graph(raw_tasks, roles)

        return {
            "success": True,
            "artifacts": {
                "task_graph": task_graph,
                "plan_report": self._format_plan_report(task_graph),
                "roles": data.get("roles", context.get("roles", [])),
                "total_tasks": len(task_graph.get("tasks", [])),
                "execution_waves": len(task_graph.get("execution_waves", [])),
            },
        }

    def _build_task_graph(
        self, tasks: List[Dict], roles: List[Dict]
    ) -> Dict[str, Any]:
        graph_tasks = []
        for i, t in enumerate(tasks):
            if isinstance(t, str):
                t = {"name": t, "description": t}
            graph_tasks.append({
                "name": t.get("name", t.get("title", f"task_{i+1}")),
                "description": t.get("description", t.get("name", "")),
                "role": t.get("role", t.get("role_id", "developer")),
                "priority": t.get("priority", "P2"),
                "depends_on": t.get("depends_on", t.get("dependencies", [])),
                "estimated_effort": t.get("estimated_effort", "medium"),
            })

        waves = self._compute_execution_waves(graph_tasks)
        return {
            "tasks": graph_tasks,
            "execution_waves": waves,
            "total": len(graph_tasks),
        }

    def _compute_execution_waves(self, tasks: List[Dict]) -> List[List[int]]:
        task_names = {t["name"]: i for i, t in enumerate(tasks)}
        remaining = set(range(len(tasks)))
        waves = []

        while remaining:
            wave = []
            for idx in sorted(remaining):
                deps = tasks[idx].get("depends_on", [])
                dep_indices = set()
                for d in deps:
                    if d in task_names:
                        dep_indices.add(task_names[d])
                if not dep_indices.intersection(remaining - {idx}):
                    wave.append(idx)

            if not wave:
                wave = [min(remaining)]

            waves.append(wave)
            remaining -= set(wave)

        return waves

    def _build_plan_prompt(
        self, description: str, analysis: str, roles: List, tasks: List
    ) -> str:
        parts = [
            "Create detailed execution plan based on analysis.",
            f"\nDescription: {description}",
        ]
        if analysis:
            parts.append(f"\nAnalysis Summary: {str(analysis)[:1000]}")
        if roles:
            import json as _json
            parts.append(f"\nRoles: {_json.dumps(roles, ensure_ascii=False)[:500]}")
        if tasks:
            import json as _json
            parts.append(f"\nTasks: {_json.dumps(tasks, ensure_ascii=False)[:1000]}")
        parts.append(
            "\nOutput a JSON object with 'task_graph' containing 'tasks' "
            "(list with name, description, role, priority, depends_on) "
            "and 'execution_waves'."
        )
        return "\n".join(parts)

    def _fallback_plan(self, description: str, prompt: str) -> Dict[str, Any]:
        tasks = [
            {"name": "verify_baseline", "description": "Verify existing tests pass",
             "role": "developer", "priority": "P1", "depends_on": []},
            {"name": "implement_core", "description": "Implement core features",
             "role": "developer", "priority": "P1", "depends_on": ["verify_baseline"]},
            {"name": "write_tests", "description": "Write integration tests",
             "role": "developer", "priority": "P1", "depends_on": ["implement_core"]},
            {"name": "quality_review", "description": "Quality review",
             "role": "reviewer", "priority": "P2", "depends_on": ["write_tests"]},
            {"name": "final_regression", "description": "Final regression test",
             "role": "developer", "priority": "P1", "depends_on": ["quality_review"]},
        ]
        task_graph = self._build_task_graph(tasks, [])
        return {
            "success": True,
            "artifacts": {
                "task_graph": task_graph,
                "plan_report": self._format_plan_report(task_graph),
                "roles": [
                    {"type": "developer", "name": "developer", "capabilities": ["code", "test"]},
                    {"type": "reviewer", "name": "reviewer", "capabilities": ["review", "quality"]},
                ],
                "total_tasks": len(tasks),
                "execution_waves": len(task_graph.get("execution_waves", [])),
            },
        }

    def _format_plan_report(self, task_graph: Dict) -> str:
        tasks = task_graph.get("tasks", [])
        waves = task_graph.get("execution_waves", [])
        lines = [
            "# Execution Plan",
            "",
            f"Total tasks: {len(tasks)}",
            f"Execution waves: {len(waves)}",
            "",
        ]
        for wave_idx, wave in enumerate(waves, 1):
            lines.append(f"## Wave {wave_idx}")
            for task_idx in wave:
                if task_idx < len(tasks):
                    t = tasks[task_idx]
                    lines.append(
                        f"  - [{t.get('role', '?')}] {t.get('name', '?')} "
                        f"(priority: {t.get('priority', 'P2')})"
                    )
            lines.append("")
        return "\n".join(lines)

    def _handle_eval_and_update(
        self, task_description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Post-PDCA cycle evaluation: assess completed work, identify issues,
        update project documents, and generate new/modified tasks.

        Context expects:
        - pdca_cycle: current cycle number
        - task_results: summary of completed/failed tasks
        - issues: list of issues discovered during execution
        - existing_backlog: current backlog items
        - existing_analysis: previous analysis artifacts
        """
        pdca_cycle = context.get("pdca_cycle", 0)
        task_results = context.get("task_results", {})
        issues = context.get("issues", [])
        existing_backlog = context.get("existing_backlog", [])
        existing_analysis = context.get("existing_analysis", {})

        completed = task_results.get("completed", 0)
        failed = task_results.get("failed", 0)
        total = task_results.get("total", 0)
        success_rate = (completed / total * 100) if total > 0 else 0

        updated_analysis = dict(existing_analysis) if existing_analysis else {}
        updated_analysis.setdefault("pdca_history", []).append({
            "cycle": pdca_cycle,
            "completed": completed,
            "failed": failed,
            "success_rate": round(success_rate, 1),
            "issues": [{"name": i.get("name", ""), "error": str(i.get("error", ""))[:200]} for i in issues[:10]],
        })

        new_tasks = []
        for iss in issues:
            new_tasks.append({
                "name": f"fix_{iss.get('name', 'issue')}",
                "description": (
                    f"Fix issue from PDCA cycle {pdca_cycle}: "
                    f"{str(iss.get('error', ''))[:200]}"
                ),
                "role": "developer",
                "priority": "P1",
                "depends_on": [],
                "origin": "pdca_discovery",
            })

        quality_recommendations = []
        if success_rate < 50:
            quality_recommendations.append(
                "Low success rate: consider re-analyzing requirements"
            )
        if failed > completed:
            quality_recommendations.append(
                "More failures than successes: review task decomposition"
            )
        if issues:
            quality_recommendations.append(
                f"{len(issues)} issues found: may need additional test coverage"
            )

        remaining_backlog = [
            item for item in existing_backlog
            if item.get("status") == "pending"
        ]
        for item in remaining_backlog:
            if item.get("name") not in {t["name"] for t in new_tasks}:
                new_tasks.append({
                    "name": item["name"],
                    "description": item.get("description", item["name"]),
                    "role": item.get("role", "developer"),
                    "priority": item.get("priority", "P2"),
                    "depends_on": [],
                    "origin": "backlog",
                })

        eval_report = self._format_eval_report(
            pdca_cycle, completed, failed, total, success_rate,
            issues, new_tasks, quality_recommendations
        )

        return {
            "success": True,
            "artifacts": {
                "updated_analysis": updated_analysis,
                "new_tasks": new_tasks,
                "quality_recommendations": quality_recommendations,
                "eval_report": eval_report,
                "pdca_cycle": pdca_cycle,
                "success_rate": round(success_rate, 1),
                "work_remaining": len(new_tasks),
            },
        }

    def _format_eval_report(
        self, cycle, completed, failed, total, success_rate,
        issues, new_tasks, recommendations
    ) -> str:
        lines = [
            f"# PDCA Evaluation Report - Cycle {cycle}",
            "",
            f"## Results",
            f"- Completed: {completed}/{total} ({success_rate:.0f}%)",
            f"- Failed: {failed}/{total}",
            f"- New tasks generated: {len(new_tasks)}",
            "",
        ]
        if issues:
            lines.append("## Issues")
            for iss in issues[:10]:
                lines.append(f"  - {iss.get('name', '?')}: {str(iss.get('error', ''))[:80]}")
            lines.append("")
        if new_tasks:
            lines.append("## New Tasks")
            for t in new_tasks[:15]:
                origin = t.get("origin", "")
                lines.append(f"  - [{t.get('priority', 'P2')}] {t.get('name', '?')} (from: {origin})")
            lines.append("")
        if recommendations:
            lines.append("## Quality Recommendations")
            for r in recommendations:
                lines.append(f"  - {r}")
        return "\n".join(lines)

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
                "roles": [
                    {"type": "developer", "name": "developer", "capabilities": ["code", "test"]},
                    {"type": "reviewer", "name": "reviewer", "capabilities": ["review", "quality"]},
                ],
                "tasks": [
                    {"name": "analyze_and_implement", "description": task, "role": "developer", "priority": "P1", "depends_on": []},
                ],
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
            "eval_and_update",
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
