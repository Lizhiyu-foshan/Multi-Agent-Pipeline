"""
IntentGate - Intent analysis gate for PipelineOrchestrator INIT phase.

Before a pipeline enters ANALYZE, IntentGate examines the user's description
to extract structured intent. This prevents misrouting and ensures the pipeline
starts with a clear, validated understanding of what the user wants.

Core responsibilities:
1. Classify intent type (build, fix, refactor, test, analyze, configure, etc.)
2. Extract key entities (files, modules, APIs, services mentioned)
3. Detect ambiguity (vague descriptions, conflicting signals)
4. Estimate scope and complexity class
5. Validate prerequisites (does the project exist? are referenced files real?)
6. Produce a structured IntentResult that flows into ANALYZE

Design principles:
- No external AI calls (prompt-passing protocol)
- Deterministic keyword + pattern analysis
- Ambiguity triggers a human clarification loop before ANALYZE
- Integrates with SpecGate lifecycle hooks (on_intent_resolved point)
- Results stored as pipeline artifacts for downstream consumption
"""

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    BUILD = "build"
    FIX = "fix"
    REFACTOR = "refactor"
    TEST = "test"
    ANALYZE = "analyze"
    CONFIGURE = "configure"
    DOCUMENT = "document"
    DEPLOY = "deploy"
    REVIEW = "review"
    MIGRATE = "migrate"
    OPTIMIZE = "optimize"
    UNKNOWN = "unknown"


class ComplexityClass(str, Enum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    CRITICAL = "critical"


class AmbiguityLevel(str, Enum):
    CLEAR = "clear"
    SLIGHT = "slight"
    MODERATE = "moderate"
    HIGH = "high"


@dataclass
class IntentResult:
    intent_type: IntentType
    complexity_class: ComplexityClass
    ambiguity_level: AmbiguityLevel
    confidence: float
    description: str
    entities: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    suggested_skills: List[str] = field(default_factory=list)
    suggested_roles: List[str] = field(default_factory=list)
    clarification_questions: List[str] = field(default_factory=list)
    prerequisites_met: bool = True
    prerequisite_issues: List[str] = field(default_factory=list)
    scope_indicators: Dict[str, Any] = field(default_factory=dict)
    needs_clarification: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_type": self.intent_type.value,
            "complexity_class": self.complexity_class.value,
            "ambiguity_level": self.ambiguity_level.value,
            "confidence": self.confidence,
            "description": self.description,
            "entities": self.entities,
            "keywords": self.keywords,
            "suggested_skills": self.suggested_skills,
            "suggested_roles": self.suggested_roles,
            "clarification_questions": self.clarification_questions,
            "prerequisites_met": self.prerequisites_met,
            "prerequisite_issues": self.prerequisite_issues,
            "scope_indicators": self.scope_indicators,
            "needs_clarification": self.needs_clarification,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntentResult":
        return cls(
            intent_type=IntentType(data.get("intent_type", "unknown")),
            complexity_class=ComplexityClass(data.get("complexity_class", "moderate")),
            ambiguity_level=AmbiguityLevel(data.get("ambiguity_level", "clear")),
            confidence=data.get("confidence", 0.5),
            description=data.get("description", ""),
            entities=data.get("entities", []),
            keywords=data.get("keywords", []),
            suggested_skills=data.get("suggested_skills", []),
            suggested_roles=data.get("suggested_roles", []),
            clarification_questions=data.get("clarification_questions", []),
            prerequisites_met=data.get("prerequisites_met", True),
            prerequisite_issues=data.get("prerequisite_issues", []),
            scope_indicators=data.get("scope_indicators", {}),
            needs_clarification=data.get("needs_clarification", False),
        )


_INTENT_PATTERNS: Dict[IntentType, List[str]] = {
    IntentType.BUILD: [
        "build",
        "create",
        "implement",
        "add",
        "develop",
        "new feature",
        "construct",
        "write",
        "generate",
        "scaffold",
        "set up",
        "setup",
        "init",
        "bootstrap",
    ],
    IntentType.FIX: [
        "fix",
        "bug",
        "error",
        "issue",
        "problem",
        "broken",
        "crash",
        "debug",
        "patch",
        "resolve",
        "repair",
        "correct",
        "hotfix",
        "failure",
        "exception",
        "traceback",
    ],
    IntentType.REFACTOR: [
        "refactor",
        "restructure",
        "reorganize",
        "clean up",
        "cleanup",
        "rewrite",
        "redesign",
        "modernize",
        "simplify",
        "consolidate",
        "extract",
        "move",
        "rename",
    ],
    IntentType.TEST: [
        "test",
        "testing",
        "unit test",
        "integration test",
        "coverage",
        "spec",
        "bdd",
        "tdd",
        "qa",
        "validate",
        "verify tests",
        "e2e test",
        "regression",
    ],
    IntentType.ANALYZE: [
        "analyze",
        "analysis",
        "audit",
        "review",
        "inspect",
        "examine",
        "investigate",
        "assess",
        "evaluate",
        "report on",
        "study",
        "understand",
        "diagnose",
    ],
    IntentType.CONFIGURE: [
        "config",
        "configure",
        "setup",
        "settings",
        "environment",
        "deploy config",
        "env var",
        "environment variable",
        "ci/cd",
        "pipeline config",
    ],
    IntentType.DOCUMENT: [
        "document",
        "documentation",
        "readme",
        "doc",
        "guide",
        "tutorial",
        "explain",
        "describe",
        "comment",
        "annotate",
        "api doc",
    ],
    IntentType.DEPLOY: [
        "deploy",
        "release",
        "publish",
        "ship",
        "rollout",
        "launch",
        "production",
        "staging",
        "ci",
        "cd",
        "pipeline",
    ],
    IntentType.REVIEW: [
        "code review",
        "pr review",
        "pull request",
        "review",
        "check quality",
        "lint",
        "style check",
        "security review",
        "performance review",
    ],
    IntentType.MIGRATE: [
        "migrate",
        "migration",
        "upgrade",
        "port",
        "convert",
        "transition",
        "move to",
        "adopt",
    ],
    IntentType.OPTIMIZE: [
        "optimize",
        "performance",
        "speed up",
        "slow",
        "fast",
        "efficient",
        "bottleneck",
        "latency",
        "throughput",
        "memory",
        "cache",
        "parallel",
    ],
}

_COMPLEXITY_INDICATORS = {
    ComplexityClass.CRITICAL: [
        "critical",
        "security",
        "auth",
        "authentication",
        "encryption",
        "database migration",
        "breaking change",
        "production issue",
        "emergency",
        "data loss",
        "compliance",
    ],
    ComplexityClass.TRIVIAL: [
        "rename",
        "format",
        "typo",
        "whitespace",
        "comment",
        "single line",
        "one line",
        "trivial",
    ],
    ComplexityClass.SIMPLE: [
        "simple",
        "quick",
        "small",
        "single file",
        "minor",
        "easy",
        "straightforward",
    ],
    ComplexityClass.COMPLEX: [
        "complex",
        "architecture",
        "redesign",
        "multiple services",
        "integration",
        "distributed",
        "microservice",
        "system",
        "end-to-end",
        "full stack",
    ],
}

_ENTITY_PATTERNS = [
    (
        r"(?:src/|lib/|app/|pkg/|cmd/|test/|tests/|spec/|docs/|config/|scripts/)"
        r"[\w/.-]+\.(?:py|js|ts|go|rs|java|rb|yaml|yml|json|toml|md)",
        "file_path",
    ),
    (
        r"\b(?:README|CHANGELOG|Makefile|Dockerfile|docker-compose|\.env)\b",
        "config_file",
    ),
    (r"\b(?:API|REST|GraphQL|gRPC|HTTP|WebSocket|SDK)\b", "api"),
    (r"\b(?:database|DB|SQL|NoSQL|Redis|Postgres|MongoDB|SQLite)\b", "database"),
    (r"\b(?:Docker|Kubernetes?|K8s|Terraform|Ansible|Helm)\b", "infra"),
    (r"\b(?:JWT|OAuth|SSO|token|credential|password)\b", "auth"),
    (r"\b(?:test|spec|mock|fixture|stub)\b", "test"),
]


def _extract_entities(text: str) -> List[str]:
    entities = []
    seen = set()
    for pattern, _category in _ENTITY_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            m_lower = m.lower()
            if m_lower not in seen:
                seen.add(m_lower)
                entities.append(m)
    return entities


def _classify_intent(text: str) -> tuple:
    text_lower = text.lower()
    scores: Dict[IntentType, int] = {}
    matched_keywords: Dict[IntentType, List[str]] = {}

    for intent_type, keywords in _INTENT_PATTERNS.items():
        score = 0
        hits = []
        for kw in keywords:
            count = text_lower.count(kw)
            if count > 0:
                score += count
                hits.append(kw)
        if score > 0:
            scores[intent_type] = score
            matched_keywords[intent_type] = hits

    if not scores:
        return IntentType.UNKNOWN, 0.0, [], []

    sorted_intents = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_type, best_score = sorted_intents[0]

    total_score = sum(scores.values())
    confidence = min(best_score / max(total_score, 1), 1.0)

    if len(sorted_intents) > 1:
        second_score = sorted_intents[1][1]
        ratio = best_score / max(second_score, 1)
        confidence = min(confidence * ratio, 1.0)

    all_keywords = []
    for kw_list in matched_keywords.values():
        all_keywords.extend(kw_list)

    return (
        best_type,
        round(confidence, 3),
        all_keywords[:10],
        matched_keywords.get(best_type, []),
    )


def _classify_complexity(text: str) -> ComplexityClass:
    text_lower = text.lower()

    for cx_class, indicators in _COMPLEXITY_INDICATORS.items():
        for indicator in indicators:
            if indicator in text_lower:
                return cx_class

    word_count = len(text.split())
    if word_count < 5:
        return ComplexityClass.TRIVIAL
    elif word_count < 15:
        return ComplexityClass.SIMPLE
    elif word_count > 100:
        return ComplexityClass.COMPLEX

    return ComplexityClass.MODERATE


def _assess_ambiguity(
    text: str, intent_type: IntentType, confidence: float
) -> AmbiguityLevel:
    if intent_type == IntentType.UNKNOWN:
        return AmbiguityLevel.HIGH
    if confidence < 0.3:
        return AmbiguityLevel.HIGH
    if confidence < 0.5:
        return AmbiguityLevel.MODERATE

    text_lower = text.lower()
    vague_words = [
        "something",
        "stuff",
        "things",
        "some",
        "maybe",
        "perhaps",
        "kind of",
        "sort of",
    ]
    vague_count = sum(1 for w in vague_words if w in text_lower)

    if vague_count >= 2:
        return AmbiguityLevel.MODERATE
    elif vague_count >= 1:
        return AmbiguityLevel.SLIGHT

    if len(text.strip()) < 10:
        return AmbiguityLevel.MODERATE

    return AmbiguityLevel.CLEAR


def _generate_clarification_questions(
    text: str,
    intent_type: IntentType,
    ambiguity: AmbiguityLevel,
    entities: List[str],
) -> List[str]:
    questions = []

    if intent_type == IntentType.UNKNOWN:
        questions.append(
            "What would you like to accomplish? (build, fix, refactor, test, analyze, etc.)"
        )
        return questions

    if ambiguity in (AmbiguityLevel.MODERATE, AmbiguityLevel.HIGH):
        type_label = intent_type.value.replace("_", " ")
        questions.append(f"Confirm: you want to {type_label} — is that correct?")

    if not entities and intent_type in (
        IntentType.BUILD,
        IntentType.FIX,
        IntentType.REFACTOR,
        IntentType.TEST,
        IntentType.OPTIMIZE,
    ):
        questions.append("Which files or modules are involved?")

    if intent_type == IntentType.BUILD and "test" not in text.lower():
        questions.append("Should tests be included?")

    if (
        intent_type == IntentType.FIX
        and "error" not in text.lower()
        and "bug" not in text.lower()
    ):
        questions.append("What is the specific error or unexpected behavior?")

    return questions[:5]


def _suggest_skills(intent_type: IntentType) -> List[str]:
    mapping = {
        IntentType.BUILD: ["bmad-evo", "superpowers"],
        IntentType.FIX: ["superpowers"],
        IntentType.REFACTOR: ["bmad-evo", "superpowers"],
        IntentType.TEST: ["superpowers"],
        IntentType.ANALYZE: ["bmad-evo"],
        IntentType.CONFIGURE: ["superpowers"],
        IntentType.DOCUMENT: ["superpowers"],
        IntentType.DEPLOY: ["superpowers"],
        IntentType.REVIEW: ["bmad-evo", "superpowers"],
        IntentType.MIGRATE: ["bmad-evo", "superpowers"],
        IntentType.OPTIMIZE: ["bmad-evo", "superpowers"],
        IntentType.UNKNOWN: ["bmad-evo"],
    }
    return mapping.get(intent_type, ["bmad-evo"])


def _suggest_roles(intent_type: IntentType, complexity: ComplexityClass) -> List[str]:
    base_roles = {
        IntentType.BUILD: ["architect", "developer"],
        IntentType.FIX: ["developer", "tester"],
        IntentType.REFACTOR: ["architect", "developer"],
        IntentType.TEST: ["developer", "tester"],
        IntentType.ANALYZE: ["analyst"],
        IntentType.CONFIGURE: ["developer"],
        IntentType.DOCUMENT: ["developer"],
        IntentType.DEPLOY: ["developer"],
        IntentType.REVIEW: ["analyst", "reviewer"],
        IntentType.MIGRATE: ["architect", "developer"],
        IntentType.OPTIMIZE: ["architect", "developer"],
        IntentType.UNKNOWN: ["analyst"],
    }
    roles = list(base_roles.get(intent_type, ["analyst"]))

    if complexity in (ComplexityClass.COMPLEX, ComplexityClass.CRITICAL):
        if "architect" not in roles:
            roles.insert(0, "architect")
    if complexity == ComplexityClass.CRITICAL:
        if "tester" not in roles:
            roles.append("tester")

    return roles


def _check_prerequisites(text: str, project_path: str = None) -> tuple:
    issues = []
    file_refs = re.findall(
        r"(?:src/|lib/|app/|\.py|\.js|\.ts|\.go|\.rs|\.java|\.rb)"
        r"[\w/.-]*",
        text,
    )

    if project_path and file_refs:
        for ref in file_refs[:5]:
            full = os.path.join(project_path, ref)
            if not os.path.exists(full):
                norm = ref.replace("/", os.sep)
                full = os.path.join(project_path, norm)
                if not os.path.exists(full):
                    issues.append(f"Referenced file not found: {ref}")

    return len(issues) == 0, issues


class IntentGate:
    """
    Pre-analysis intent classification gate.

    Usage:
        gate = IntentGate()
        result = gate.analyze("Build a REST API for user authentication with JWT")
        if result.needs_clarification:
            # Ask user the clarification_questions before proceeding
        else:
            # Pass result to ANALYZE phase
    """

    def __init__(self, project_path: str = None):
        self.project_path = project_path
        self._custom_rules: List[Dict[str, Any]] = []

    def add_rule(
        self, pattern: str, intent_type: IntentType, priority: int = 0
    ) -> None:
        self._custom_rules.append(
            {
                "pattern": pattern,
                "intent_type": intent_type,
                "priority": priority,
            }
        )
        self._custom_rules.sort(key=lambda r: r["priority"], reverse=True)

    def analyze(self, description: str, context: Dict[str, Any] = None) -> IntentResult:
        """
        Analyze a pipeline description to extract structured intent.

        Args:
            description: The user's pipeline description text
            context: Optional context (e.g., project_path override)

        Returns:
            IntentResult with classified intent, entities, suggestions,
            and clarification questions if ambiguous.
        """
        if not description or not description.strip():
            return IntentResult(
                intent_type=IntentType.UNKNOWN,
                complexity_class=ComplexityClass.TRIVIAL,
                ambiguity_level=AmbiguityLevel.HIGH,
                confidence=0.0,
                description=description or "",
                needs_clarification=True,
                clarification_questions=[
                    "Please provide a description of what you want to do."
                ],
            )

        intent_type, confidence, all_keywords, primary_keywords = _classify_intent(
            description
        )

        custom_hit = self._apply_custom_rules(description)
        if custom_hit:
            intent_type = custom_hit
            confidence = min(confidence + 0.2, 1.0)

        complexity = _classify_complexity(description)
        entities = _extract_entities(description)
        ambiguity = _assess_ambiguity(description, intent_type, confidence)
        questions = _generate_clarification_questions(
            description, intent_type, ambiguity, entities
        )
        skills = _suggest_skills(intent_type)
        roles = _suggest_roles(intent_type, complexity)

        proj_path = self.project_path
        if context and "project_path" in context:
            proj_path = context["project_path"]
        prereqs_ok, prereq_issues = _check_prerequisites(description, proj_path)

        scope = self._extract_scope(description)

        needs_clarification = (
            ambiguity in (AmbiguityLevel.MODERATE, AmbiguityLevel.HIGH)
            or intent_type == IntentType.UNKNOWN
            or len(questions) > 0
            and ambiguity != AmbiguityLevel.CLEAR
            or not prereqs_ok
        )

        return IntentResult(
            intent_type=intent_type,
            complexity_class=complexity,
            ambiguity_level=ambiguity,
            confidence=confidence,
            description=description,
            entities=entities,
            keywords=all_keywords,
            suggested_skills=skills,
            suggested_roles=roles,
            clarification_questions=questions,
            prerequisites_met=prereqs_ok,
            prerequisite_issues=prereq_issues,
            scope_indicators=scope,
            needs_clarification=needs_clarification,
        )

    def _apply_custom_rules(self, text: str) -> Optional[IntentType]:
        text_lower = text.lower()
        for rule in self._custom_rules:
            if re.search(rule["pattern"], text_lower):
                return rule["intent_type"]
        return None

    def _extract_scope(self, text: str) -> Dict[str, Any]:
        text_lower = text.lower()
        return {
            "word_count": len(text.split()),
            "has_file_refs": bool(re.search(r"\.\w{1,4}\b", text)),
            "has_urls": bool(re.search(r"https?://", text)),
            "mentions_multiple": any(
                w in text_lower
                for w in ("multiple", "several", "various", "all", "entire", "whole")
            ),
            "mentions_single": any(
                w in text_lower for w in ("single", "one", "just", "only", "specific")
            ),
        }
