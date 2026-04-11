"""
CodeAnalyzer - Pipeline-level shared code analysis service.

Provides AST-based static analysis for all skills. Inspired by bmad-evo's
ast_auditor but fully self-contained (no bmad-evo dependency).

Architecture:
- CodeAnalyzer: public facade with audit_code / audit_file / audit_directory
- _PythonASTVisitor: internal ast.NodeVisitor collecting violations
- _RegexChecker: internal regex-based checks (strict mode supplement)

Usage from any skill:
    analyzer = CodeAnalyzer(strict_mode=True)
    result = analyzer.audit_code(source_code, filename="module.py")
    if not result.is_passing:
        for v in result.violations:
            print(f"[{v.severity}] {v.message} at line {v.line}")
"""

import ast
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class Severity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuleCategory(Enum):
    NULL_CHECK = "null_check"
    EXCEPTION_FLOW = "exception_flow"
    IO_SAFETY = "io_safety"
    HARDCODED_SECRET = "hardcoded_secret"
    TYPE_ANNOTATION = "type_annotation"
    DEBUG_CODE = "debug_code"
    NAMING = "naming"
    FUNCTION_LENGTH = "function_length"
    CYCLOMATIC_COMPLEXITY = "cyclomatic_complexity"
    DOCUMENTATION = "documentation"
    CODE_SMELL = "code_smell"
    PSEUDO_AI = "pseudo_ai"
    CUSTOM = "custom"


@dataclass
class Violation:
    rule_id: str
    category: RuleCategory
    severity: Severity
    message: str
    line: int
    column: int = 0
    file: str = ""
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "file": self.file,
            "suggestion": self.suggestion,
        }


@dataclass
class AuditResult:
    file: str
    language: str
    score: float
    violations: List[Violation] = field(default_factory=list)
    execution_time_ms: float = 0.0
    lines_of_code: int = 0

    @property
    def is_passing(self) -> bool:
        has_blocking = any(
            v.severity in (Severity.HIGH, Severity.CRITICAL) for v in self.violations
        )
        return self.score >= 85 and not has_blocking

    @property
    def violation_counts_by_severity(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for v in self.violations:
            counts[v.severity.value] += 1
        return dict(counts)

    @property
    def violation_counts_by_category(self) -> Dict[str, int]:
        counts: Dict[str, int] = defaultdict(int)
        for v in self.violations:
            counts[v.category.value] += 1
        return dict(counts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "language": self.language,
            "score": self.score,
            "is_passing": self.is_passing,
            "violations": [v.to_dict() for v in self.violations],
            "violation_counts_by_severity": self.violation_counts_by_severity,
            "violation_counts_by_category": self.violation_counts_by_category,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "lines_of_code": self.lines_of_code,
        }

    def summary(self) -> str:
        status = "PASS" if self.is_passing else "FAIL"
        lines = [
            f"[{status}] {self.file} — score: {self.score:.0f}/100, "
            f"violations: {len(self.violations)} "
            f"({self.violation_counts_by_severity})",
        ]
        for v in self.violations:
            lines.append(
                f"  [{v.severity.value:8s}] {v.category.value:20s} "
                f"L{v.line:4d}: {v.message}"
            )
        return "\n".join(lines)


_SEVERITY_WEIGHTS = {
    Severity.LOW: 1,
    Severity.MEDIUM: 3,
    Severity.HIGH: 5,
    Severity.CRITICAL: 10,
}

_PASS_THRESHOLD = 85.0

_SECRET_PATTERNS = [
    (r"api[_\-]?key", "API key"),
    (r"secret[_\-]?key", "secret key"),
    (r"password", "password"),
    (r"access[_\-]?token", "access token"),
    (r"credential", "credential"),
]

_PLACEHOLDER_VALUES = {
    "xxx",
    "your_key_here",
    "change_me",
    "placeholder",
    "replace_me",
    "insert_here",
    "todo",
}

_IO_FUNCS = {"open", "read", "write", "readlines", "writelines"}
_NETWORK_FUNCS = {"get", "post", "put", "delete", "request", "urlopen"}

_AI_FUNC_PATTERNS = [
    r"(?i)^ai[_\-]",
    r"(?i)^gpt[_\-]",
    r"(?i)^llm[_\-]",
    r"(?i)^generate",
    r"(?i)^analyze[_\-]",
    r"(?i)^call[_\-]ai",
    r"(?i)^ask[_\-]ai",
]

_AI_EXCLUDE_PATTERNS = [
    r"(?i)^create[_\-]?default",
    r"(?i)^load[_\-]?default",
    r"(?i)^format[_\-]",
    r"(?i)^build[_\-]",
]


class _PythonASTVisitor(ast.NodeVisitor):
    def __init__(self, filename: str, enabled_rules: Optional[Set[str]] = None):
        self.filename = filename
        self.enabled_rules = enabled_rules
        self.violations: List[Violation] = []
        self._current_function: Optional[str] = None

    def _rule_enabled(self, rule_id: str) -> bool:
        if self.enabled_rules is None:
            return True
        return rule_id in self.enabled_rules

    def _add(
        self,
        rule_id: str,
        category: RuleCategory,
        severity: Severity,
        message: str,
        line: int,
        suggestion: str = "",
    ):
        if self._rule_enabled(rule_id):
            self.violations.append(
                Violation(
                    rule_id=rule_id,
                    category=category,
                    severity=severity,
                    message=message,
                    line=line,
                    file=self.filename,
                    suggestion=suggestion,
                )
            )

    # ---- Null check ----

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old = self._current_function
        self._current_function = node.name
        self._check_null_checks(node)
        self._check_type_annotations(node)
        self._check_naming(node)
        self._check_function_length(node)
        self._check_complexity(node)
        self._check_docstring(node)
        self._check_pseudo_ai(node)
        self.generic_visit(node)
        self._current_function = old

    def _check_null_checks(self, node: ast.FunctionDef):
        args = node.args.args + node.args.posonlyargs + node.args.kwonlyargs
        for arg in args:
            if arg.arg in ("self", "cls"):
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Subscript):
                    if isinstance(child.value, ast.Name) and child.value.id == arg.arg:
                        if not self._has_null_check(node, arg.arg):
                            self._add(
                                "NULL_CHECK",
                                RuleCategory.NULL_CHECK,
                                Severity.HIGH,
                                f"Parameter '{arg.arg}' used without null check",
                                node.lineno,
                                f"Add: if {arg.arg} is None: raise ValueError(...)",
                            )
                            break

    def _has_null_check(self, func: ast.FunctionDef, param: str) -> bool:
        for node in ast.walk(func):
            if isinstance(node, ast.If):
                test = node.test
                if isinstance(test, ast.Compare):
                    if isinstance(test.left, ast.Name) and test.left.id == param:
                        return True
                if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                    if isinstance(test.operand, ast.Name) and test.operand.id == param:
                        return True
        return False

    # ---- Type annotations ----

    def _check_type_annotations(self, node: ast.FunctionDef):
        if node.returns is None and node.name != "__init__":
            self._add(
                "TYPE_RETURN",
                RuleCategory.TYPE_ANNOTATION,
                Severity.LOW,
                f"Function '{node.name}' missing return type annotation",
                node.lineno,
                "Add: -> ReturnType",
            )
        for arg in node.args.args + node.args.posonlyargs:
            if arg.arg not in ("self", "cls") and arg.annotation is None:
                self._add(
                    "TYPE_PARAM",
                    RuleCategory.TYPE_ANNOTATION,
                    Severity.LOW,
                    f"Parameter '{arg.arg}' missing type annotation",
                    node.lineno,
                    f"Add type: {arg.arg}: SomeType",
                )

    # ---- Exception flow ----

    def visit_Try(self, node: ast.Try):
        if not node.handlers:
            self._add(
                "EXCEPTION_NO_HANDLER",
                RuleCategory.EXCEPTION_FLOW,
                Severity.MEDIUM,
                "try block without except handler",
                node.lineno,
                "Add except handler or use context manager",
            )
        for handler in node.handlers:
            if handler.type is None:
                self._add(
                    "BARE_EXCEPT",
                    RuleCategory.EXCEPTION_FLOW,
                    Severity.MEDIUM,
                    "Bare 'except:' catches all exceptions",
                    handler.lineno,
                    "Use 'except Exception:' or specific types",
                )
        self.generic_visit(node)

    # ---- IO / Network safety ----

    def visit_Call(self, node: ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in _IO_FUNCS:
            self._add(
                "IO_SAFETY",
                RuleCategory.IO_SAFETY,
                Severity.HIGH,
                f"IO operation '{func_name}' should have exception handling",
                node.lineno,
                "Wrap in try-except for IOError/OSError",
            )

        if func_name in _NETWORK_FUNCS:
            self._add(
                "NETWORK_SAFETY",
                RuleCategory.IO_SAFETY,
                Severity.HIGH,
                f"Network operation '{func_name}' should have exception handling",
                node.lineno,
                "Wrap in try-except for ConnectionError/Timeout",
            )

        self.generic_visit(node)

    # ---- Hardcoded secrets ----

    def visit_Assign(self, node: ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                name_lower = target.id.lower()
                for pattern, secret_type in _SECRET_PATTERNS:
                    if re.search(pattern, name_lower):
                        if isinstance(node.value, ast.Constant) and isinstance(
                            node.value.value, str
                        ):
                            val = node.value.value
                            if val and val.lower() not in _PLACEHOLDER_VALUES:
                                self._add(
                                    "HARDCODED_SECRET",
                                    RuleCategory.HARDCODED_SECRET,
                                    Severity.CRITICAL,
                                    f"Hardcoded {secret_type} in '{target.id}'",
                                    node.lineno,
                                    f"Use: os.getenv('{target.id.upper()}')",
                                )
        self.generic_visit(node)

    # ---- Naming conventions ----

    def _check_naming(self, node: ast.FunctionDef):
        if not re.match(r"^[a-z_][a-z0-9_]*$", node.name):
            self._add(
                "NAMING_FUNCTION",
                RuleCategory.NAMING,
                Severity.MEDIUM,
                f"Function '{node.name}' not in snake_case",
                node.lineno,
                "Use lowercase + underscores: my_function",
            )

    def visit_ClassDef(self, node: ast.ClassDef):
        if not re.match(r"^[A-Z][a-zA-Z0-9]*$", node.name):
            self._add(
                "NAMING_CLASS",
                RuleCategory.NAMING,
                Severity.MEDIUM,
                f"Class '{node.name}' not in PascalCase",
                node.lineno,
                "Use PascalCase: MyClass",
            )
        self.generic_visit(node)

    # ---- Function length ----

    def _check_function_length(self, node: ast.FunctionDef):
        end = getattr(node, "end_lineno", None) or node.lineno
        length = end - node.lineno + 1
        if length > 100:
            self._add(
                "FUNCTION_TOO_LONG",
                RuleCategory.FUNCTION_LENGTH,
                Severity.HIGH,
                f"Function '{node.name}' is {length} lines (max 100)",
                node.lineno,
                "Break into smaller functions",
            )
        elif length > 50:
            self._add(
                "FUNCTION_LONG",
                RuleCategory.FUNCTION_LENGTH,
                Severity.MEDIUM,
                f"Function '{node.name}' is {length} lines",
                node.lineno,
                "Consider splitting",
            )

    # ---- Cyclomatic complexity ----

    def _check_complexity(self, node: ast.FunctionDef):
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        if complexity > 15:
            self._add(
                "COMPLEXITY_HIGH",
                RuleCategory.CYCLOMATIC_COMPLEXITY,
                Severity.HIGH,
                f"Function '{node.name}' complexity={complexity} (max 15)",
                node.lineno,
                "Simplify branching logic",
            )
        elif complexity > 10:
            self._add(
                "COMPLEXITY_MEDIUM",
                RuleCategory.CYCLOMATIC_COMPLEXITY,
                Severity.MEDIUM,
                f"Function '{node.name}' complexity={complexity}",
                node.lineno,
                "Consider simplifying",
            )

    # ---- Documentation ----

    def _check_docstring(self, node: ast.FunctionDef):
        doc = ast.get_docstring(node)
        if not doc or len(doc) < 10:
            self._add(
                "MISSING_DOCSTRING",
                RuleCategory.DOCUMENTATION,
                Severity.LOW,
                f"Function '{node.name}' missing docstring",
                node.lineno,
                "Add a docstring",
            )

    # ---- Pseudo-AI detection ----

    def _check_pseudo_ai(self, node: ast.FunctionDef):
        name = node.name
        excluded = any(re.search(p, name) for p in _AI_EXCLUDE_PATTERNS)
        if excluded:
            return
        is_ai = any(re.search(p, name) for p in _AI_FUNC_PATTERNS)
        if not is_ai:
            return
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value:
                if isinstance(child.value, ast.Dict):
                    self._add(
                        "PSEUDO_AI_HARDCODED",
                        RuleCategory.PSEUDO_AI,
                        Severity.CRITICAL,
                        f"AI function '{name}' returns hardcoded dict",
                        node.lineno,
                        "Replace with actual AI/model call",
                    )
                    return


class _RegexChecker:
    def __init__(self, filename: str, enabled_rules: Optional[Set[str]] = None):
        self.filename = filename
        self.enabled_rules = enabled_rules

    def _rule_enabled(self, rule_id: str) -> bool:
        if self.enabled_rules is None:
            return True
        return rule_id in self.enabled_rules

    def check(self, source: str) -> List[Violation]:
        violations = []
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if self._rule_enabled("DEBUG_PRINT"):
                if re.match(r"^\s*print\s*\(", line):
                    violations.append(
                        Violation(
                            rule_id="DEBUG_PRINT",
                            category=RuleCategory.DEBUG_CODE,
                            severity=Severity.LOW,
                            message="print() statement in code",
                            line=i,
                            file=self.filename,
                            suggestion="Use logging module",
                        )
                    )
            if self._rule_enabled("TODO"):
                if "#" in line and re.search(r"\bTODO\b", line, re.IGNORECASE):
                    violations.append(
                        Violation(
                            rule_id="TODO",
                            category=RuleCategory.CODE_SMELL,
                            severity=Severity.LOW,
                            message="TODO comment found",
                            line=i,
                            file=self.filename,
                        )
                    )
        return violations


def _calculate_score(violations: List[Violation], loc: int) -> float:
    if loc == 0:
        return 100.0
    total_penalty = sum(_SEVERITY_WEIGHTS[v.severity] for v in violations)
    max_penalty = loc * 0.5
    normalized = min(total_penalty / max(max_penalty, 1), 1.0)
    return max(0.0, min(100.0, 100 * (1 - normalized)))


class CodeAnalyzer:
    """
    Pipeline-level shared code analysis service.

    Modes:
    - "fast": AST-only checks (fastest, zero false positives)
    - "strict": AST + regex checks (comprehensive)
    - "regex_only": regex-only (backward compatible)

    Rule filtering:
    - enabled_rules: set of rule_id strings to enable (None = all)
    """

    def __init__(
        self,
        mode: str = "strict",
        enabled_rules: Optional[Set[str]] = None,
        pass_threshold: float = _PASS_THRESHOLD,
    ):
        self.mode = mode
        self.enabled_rules = enabled_rules
        self.pass_threshold = pass_threshold

    def audit_code(
        self,
        source_code: str,
        filename: str = "<string>",
        language: str = "python",
    ) -> AuditResult:
        start = time.time()

        if language != "python":
            return AuditResult(
                file=filename,
                language=language,
                score=100.0,
                execution_time_ms=(time.time() - start) * 1000,
            )

        violations: List[Violation] = []
        loc = 0

        if self.mode in ("fast", "strict"):
            try:
                tree = ast.parse(source_code, filename=filename)
                loc = sum(
                    1
                    for line in source_code.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
                visitor = _PythonASTVisitor(filename, self.enabled_rules)
                visitor.visit(tree)
                violations.extend(visitor.violations)
            except SyntaxError as e:
                violations.append(
                    Violation(
                        rule_id="SYNTAX_ERROR",
                        category=RuleCategory.CODE_SMELL,
                        severity=Severity.CRITICAL,
                        message=f"Syntax error: {e.msg}",
                        line=e.lineno or 0,
                        file=filename,
                    )
                )
                return AuditResult(
                    file=filename,
                    language="python",
                    score=0,
                    violations=violations,
                    execution_time_ms=(time.time() - start) * 1000,
                )

        if self.mode in ("strict", "regex_only"):
            regex_checker = _RegexChecker(filename, self.enabled_rules)
            violations.extend(regex_checker.check(source_code))

        score = _calculate_score(violations, loc)

        return AuditResult(
            file=filename,
            language="python",
            score=score,
            violations=violations,
            execution_time_ms=(time.time() - start) * 1000,
            lines_of_code=loc,
        )

    def audit_file(self, file_path: str) -> AuditResult:
        path = Path(file_path)
        if not path.exists():
            return AuditResult(
                file=file_path,
                language="unknown",
                score=0,
                violations=[
                    Violation(
                        rule_id="FILE_NOT_FOUND",
                        category=RuleCategory.CODE_SMELL,
                        severity=Severity.CRITICAL,
                        message=f"File not found: {file_path}",
                        line=0,
                        file=file_path,
                    )
                ],
            )

        ext_map = {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
        }
        language = ext_map.get(path.suffix.lower(), "python")

        try:
            source = path.read_text(encoding="utf-8")
        except Exception as e:
            return AuditResult(
                file=file_path,
                language=language,
                score=0,
                violations=[
                    Violation(
                        rule_id="READ_ERROR",
                        category=RuleCategory.CODE_SMELL,
                        severity=Severity.CRITICAL,
                        message=f"Cannot read file: {e}",
                        line=0,
                        file=file_path,
                    )
                ],
            )

        return self.audit_code(source, filename=str(path), language=language)

    def audit_directory(
        self, dir_path: str, pattern: str = "*.py"
    ) -> List[AuditResult]:
        path = Path(dir_path)
        if not path.is_dir():
            return []
        results = []
        for fp in path.rglob(pattern):
            if fp.is_file() and "__pycache__" not in str(fp):
                results.append(self.audit_file(str(fp)))
        return results

    def quick_check(self, source_code: str) -> Dict[str, Any]:
        result = self.audit_code(source_code)
        return {
            "passing": result.is_passing,
            "score": result.score,
            "violation_count": len(result.violations),
            "has_critical": any(
                v.severity == Severity.CRITICAL for v in result.violations
            ),
            "categories": result.violation_counts_by_category,
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "enabled_rules": list(self.enabled_rules) if self.enabled_rules else "all",
            "pass_threshold": self.pass_threshold,
        }
