"""
Writing-Skills Meta-Skill Adapter

Generates skill adapters and SKILL.md files from specifications.
This is the meta-skill that creates other skills.

Actions:
- scaffold: Generate a new skill from a specification
- validate: Check if an existing skill follows conventions
- upgrade: Add missing sections/conventions to an existing skill
- generate_adapter: Generate adapter.py from skill spec
- generate_skill_md: Generate SKILL.md from skill spec
- init_deep: Recursively generate AGENTS.md for every directory in the project
"""

import ast
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_pipeline_src = str(Path(__file__).resolve().parents[1] / "src")
if _pipeline_src not in sys.path:
    sys.path.insert(0, _pipeline_src)

ADAPTER_TEMPLATE = '''"""
{skill_name_capitalized} Skill Adapter

{description}

Actions:
{actions_list}
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_pipeline_src = str(Path(__file__).resolve().parents[1] / "src")
if _pipeline_src not in sys.path:
    sys.path.insert(0, _pipeline_src)


class {class_name}(object):
    name = "{skill_name}"
    version = "1.0"

    def __init__(self, project_path: str = None, prompt_manager=None):
        self.project_path = project_path or str(Path.cwd())
        self._prompt_manager = prompt_manager

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "{default_action}")
        action_map = {{
{action_map_entries}
        }}
        handler = action_map.get(action)
        if not handler:
            return {{
                "success": False,
                "error": f"Unknown action: {{action}}. Available: {{list(action_map.keys())}}",
            }}
        try:
            return handler(task_description, context)
        except Exception as e:
            logger.error(f"Action {{action}} failed: {{e}}")
            return {{"success": False, "error": str(e)}}

{action_methods}

    def get_status(self) -> Dict[str, Any]:
        return {{
            "name": self.name,
            "version": self.version,
            "project_path": self.project_path,
        }}
'''

SKILL_MD_TEMPLATE = """# {skill_name}

{description}

## Version
{version}

## Actions

{actions_documentation}

## Integration

{integration_notes}

## Configuration

{configuration}

## Examples

```python
from adapter import {class_name}

adapter = {class_name}(project_path=".")

result = adapter.execute("task description", {{
    "action": "{default_action}",
    # ... context
}})
```
"""

REQUIRED_ADAPTER_METHODS = ["execute", "get_status"]
REQUIRED_SKILL_MD_SECTIONS = ["Actions", "Version", "Integration"]


class WritingSkills_Adapter:
    name = "writing-skills"
    version = "1.1"

    def __init__(self, project_path: str = None, prompt_manager=None):
        self.project_path = project_path or str(Path.cwd())
        self._prompt_manager = prompt_manager

    def execute(self, task_description: str, context: Dict[str, Any]) -> Dict[str, Any]:
        action = context.get("action", "scaffold")
        action_map = {
            "scaffold": self._action_scaffold,
            "validate": self._action_validate,
            "upgrade": self._action_upgrade,
            "generate_adapter": self._action_generate_adapter,
            "generate_skill_md": self._action_generate_skill_md,
            "init_deep": self._action_init_deep,
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
            logger.error(f"Action {action} failed: {e}")
            return {"success": False, "error": str(e)}

    def _resolve_skill_dir(self, skill_name: str) -> Path:
        if not isinstance(skill_name, str) or not skill_name.strip():
            raise ValueError("skill_name required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", skill_name):
            raise ValueError(
                "Invalid skill_name: only letters, numbers, _ and - allowed"
            )

        skills_root = (Path(self.project_path) / ".skills").resolve()
        candidate = (skills_root / skill_name).resolve()
        try:
            candidate.relative_to(skills_root)
        except Exception as e:
            raise ValueError("Invalid skill_name path") from e
        return candidate

    def _action_scaffold(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Scaffold a complete new skill from a specification.

        Expects context:
        - skill_name: Name for the new skill (e.g. "my-ml-tool")
        - skill_description: What the skill does
        - actions: List of {name, description, parameters} dicts
        """
        skill_name = context.get("skill_name", "")
        skill_desc = context.get("skill_description", description)
        actions = context.get("actions", [])

        if not skill_name:
            return {"success": False, "error": "skill_name required in context"}
        if not actions:
            actions = [
                {"name": "execute", "description": "Default execution action"},
            ]

        try:
            skill_dir = self._resolve_skill_dir(skill_name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        if skill_dir.exists():
            return {
                "success": False,
                "error": f"Skill directory already exists: {skill_dir}",
            }

        os.makedirs(str(skill_dir / "prompts"), exist_ok=True)

        adapter_code = self._generate_adapter_code(skill_name, skill_desc, actions)
        adapter_path = skill_dir / "adapter.py"
        with open(str(adapter_path), "w", encoding="utf-8") as f:
            f.write(adapter_code)

        skill_md = self._generate_skill_md_content(
            skill_name, skill_desc, actions, "1.0"
        )
        skill_md_path = skill_dir / "SKILL.md"
        with open(str(skill_md_path), "w", encoding="utf-8") as f:
            f.write(skill_md)

        return {
            "success": True,
            "artifacts": {
                "skill_name": skill_name,
                "skill_dir": str(skill_dir),
                "files_created": ["adapter.py", "SKILL.md", "prompts/"],
                "actions": [a["name"] for a in actions],
            },
        }

    def _action_validate(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate an existing skill follows conventions.

        Checks: adapter.py exists, has required methods, SKILL.md exists,
        has required sections, PromptManager integration.
        """
        skill_name = context.get("skill_name", "")
        if not skill_name:
            return {"success": False, "error": "skill_name required"}

        try:
            skill_dir = self._resolve_skill_dir(skill_name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        issues = []
        warnings = []

        adapter_path = skill_dir / "adapter.py"
        if not adapter_path.exists():
            issues.append(f"adapter.py not found in {skill_dir}")
            return {
                "success": False,
                "artifacts": {
                    "valid": False,
                    "issues": issues,
                    "warnings": warnings,
                },
            }

        with open(str(adapter_path), "r", encoding="utf-8") as f:
            adapter_code = f.read()

        for method in REQUIRED_ADAPTER_METHODS:
            if f"def {method}" not in adapter_code:
                issues.append(f"Missing method: {method}")

        class_match = re.search(r"class (\w+)", adapter_code)
        if not class_match:
            issues.append("No class definition found in adapter.py")
        else:
            class_name = class_match.group(1)
            if "name" not in adapter_code:
                warnings.append("Adapter class should have a 'name' attribute")
            if "version" not in adapter_code:
                warnings.append("Adapter class should have a 'version' attribute")

        if "PromptManager" not in adapter_code:
            warnings.append("No PromptManager integration")

        if "pending_model_request" not in adapter_code:
            warnings.append("No pending_model_request support for prompt-passing")

        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            issues.append("SKILL.md not found")
        else:
            with open(str(skill_md_path), "r", encoding="utf-8") as f:
                md_content = f.read()
            for section in REQUIRED_SKILL_MD_SECTIONS:
                if section not in md_content:
                    issues.append(f"SKILL.md missing section: {section}")

        prompts_dir = skill_dir / "prompts"
        if not prompts_dir.exists():
            warnings.append("No prompts/ directory")

        valid = len(issues) == 0
        return {
            "success": valid,
            "artifacts": {
                "valid": valid,
                "issues": issues,
                "warnings": warnings,
                "skill_name": skill_name,
                "class_name": class_match.group(1) if class_match else None,
            },
        }

    def _action_upgrade(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Upgrade an existing skill to follow conventions.
        Adds missing methods, sections, and integrations.
        """
        skill_name = context.get("skill_name", "")
        if not skill_name:
            return {"success": False, "error": "skill_name required"}

        try:
            skill_dir = self._resolve_skill_dir(skill_name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        if not skill_dir.exists():
            return {"success": False, "error": f"Skill not found: {skill_dir}"}

        validate_result = self._action_validate(description, context)
        issues = validate_result.get("artifacts", {}).get("issues", [])
        warnings = validate_result.get("artifacts", {}).get("warnings", [])

        applied = []

        prompts_dir = skill_dir / "prompts"
        if not prompts_dir.exists():
            os.makedirs(str(prompts_dir), exist_ok=True)
            applied.append("Created prompts/ directory")

        skill_md_path = skill_dir / "SKILL.md"
        if not skill_md_path.exists():
            skill_md = self._generate_skill_md_content(
                skill_name,
                f"Auto-generated SKILL.md for {skill_name}",
                [{"name": "execute", "description": "Default action"}],
                "1.0",
            )
            with open(str(skill_md_path), "w", encoding="utf-8") as f:
                f.write(skill_md)
            applied.append("Created SKILL.md")

        return {
            "success": True,
            "artifacts": {
                "skill_name": skill_name,
                "issues_before": issues,
                "warnings_before": warnings,
                "applied_fixes": applied,
            },
        }

    def _action_init_deep(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Recursively generate AGENTS.md files for every meaningful directory.

        Each AGENTS.md describes:
        - Directory purpose (inferred from file names and docstrings)
        - File inventory with one-line descriptions
        - Key classes and functions (extracted via AST for Python)
        - Dependencies on other directories
        - Parent/child directory relationships

        Expects context:
        - target_dir: root directory to scan (default: project_path)
        - max_depth: maximum recursion depth (default: 5)
        - exclude: list of dir names to skip (default: built-in list)
        - dry_run: if True, don't write files, just return plan
        - project_description: top-level description for root AGENTS.md
        """
        target_dir = context.get("target_dir", self.project_path)
        max_depth = context.get("max_depth", 5)
        dry_run = context.get("dry_run", False)
        project_desc = context.get("project_description", description or "Project")

        default_exclude = {
            "__pycache__",
            ".git",
            "node_modules",
            ".venv",
            "venv",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            ".eggs",
            "*.egg-info",
            "dist",
            "build",
            ".idea",
            ".vs",
            ".pipeline",
            ".hashline_backups",
        }
        user_exclude = set(context.get("exclude", []))
        exclude = default_exclude | user_exclude

        target = Path(target_dir)
        if not target.is_dir():
            return {"success": False, "error": f"Not a directory: {target_dir}"}

        generated = []
        skipped = []
        tree = self._scan_directory_tree(target, exclude, max_depth)

        for dir_path, dir_info in tree.items():
            rel = dir_path.relative_to(target)
            if not dir_info["files"] and not dir_info["subdirs"]:
                skipped.append(str(rel))
                continue

            content = self._generate_agents_md(
                dir_path=dir_path,
                rel_path=rel,
                dir_info=dir_info,
                root_path=target,
                project_desc=project_desc,
            )

            if dry_run:
                generated.append(
                    {
                        "path": str(rel / "AGENTS.md"),
                        "size": len(content),
                        "files_described": len(dir_info["files"]),
                    }
                )
            else:
                agents_path = dir_path / "AGENTS.md"
                with open(str(agents_path), "w", encoding="utf-8") as f:
                    f.write(content)
                generated.append(
                    {
                        "path": str(rel / "AGENTS.md")
                        if str(rel) != "."
                        else "AGENTS.md",
                        "size": len(content),
                        "files_described": len(dir_info["files"]),
                    }
                )

        result = {
            "generated_count": len(generated),
            "skipped_count": len(skipped),
            "generated": generated,
            "dry_run": dry_run,
        }
        if skipped:
            result["skipped_empty"] = skipped[:10]

        return {"success": True, "artifacts": result}

    def _scan_directory_tree(
        self, root: Path, exclude: Set[str], max_depth: int, current_depth: int = 0
    ) -> Dict[Path, Dict]:
        tree = {}
        if current_depth > max_depth:
            return tree

        py_files = []
        other_files = []
        subdirs = []

        try:
            for entry in sorted(root.iterdir()):
                if entry.name in exclude:
                    continue
                if any(entry.name.startswith(p) and p.endswith("*") for p in exclude):
                    continue
                if entry.is_dir():
                    subdirs.append(entry.name)
                elif entry.is_file():
                    if entry.suffix == ".py":
                        py_files.append(entry)
                    elif entry.name not in ("AGENTS.md",):
                        other_files.append(entry)
        except PermissionError:
            return tree

        python_symbols = {}
        for pf in py_files:
            python_symbols[pf.name] = self._extract_python_symbols(pf)

        tree[root] = {
            "files": [f.name for f in py_files + other_files],
            "py_files": [f.name for f in py_files],
            "other_files": [f.name for f in other_files],
            "subdirs": subdirs,
            "python_symbols": python_symbols,
            "depth": current_depth,
        }

        for sd in subdirs:
            child_path = root / sd
            child_tree = self._scan_directory_tree(
                child_path, exclude, max_depth, current_depth + 1
            )
            tree.update(child_tree)

        return tree

    def _extract_python_symbols(self, py_file: Path) -> Dict[str, Any]:
        symbols: Dict[str, Any] = {"classes": [], "functions": [], "docstring": ""}
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            module_doc = ast.get_docstring(tree)
            if module_doc:
                symbols["docstring"] = module_doc.split("\n")[0][:120]

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    methods = [
                        n.name
                        for n in node.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not n.name.startswith("_")
                    ]
                    symbols["classes"].append(
                        {
                            "name": node.name,
                            "methods": methods[:8],
                            "line": node.lineno,
                            "doc": (ast.get_docstring(node) or "").split("\n")[0][:80],
                        }
                    )
                elif isinstance(node, ast.FunctionDef) and not node.name.startswith(
                    "_"
                ):
                    if node.col_offset == 0:
                        symbols["functions"].append(
                            {
                                "name": node.name,
                                "line": node.lineno,
                                "doc": (ast.get_docstring(node) or "").split("\n")[0][
                                    :80
                                ],
                            }
                        )
        except (SyntaxError, Exception):
            pass
        return symbols

    def _generate_agents_md(
        self,
        dir_path: Path,
        rel_path: Path,
        dir_info: Dict,
        root_path: Path,
        project_desc: str,
    ) -> str:
        depth = dir_info["depth"]
        dir_name = dir_path.name
        is_root = rel_path == Path(".")

        title = f"# {project_desc}" if is_root else f"# {dir_name}/"
        lines = [title, ""]

        if is_root:
            lines.append(f"Root directory of **{project_desc}**.")
        else:
            lines.append(f"Subdirectory under `{rel_path.parent}/`.")
        lines.append("")

        # Subdirectories
        if dir_info["subdirs"]:
            lines.append("## Subdirectories")
            lines.append("")
            for sd in sorted(dir_info["subdirs"]):
                child_path = dir_path / sd
                child_agents = child_path / "AGENTS.md"
                link = (
                    f"{sd}/"
                    if not child_agents.exists()
                    else f"[{sd}/]({sd}/AGENTS.md)"
                )
                lines.append(f"- {link}")
            lines.append("")

        # Python files
        if dir_info["py_files"]:
            lines.append("## Python Modules")
            lines.append("")
            py_symbols = dir_info["python_symbols"]

            for fname in sorted(dir_info["py_files"]):
                syms = py_symbols.get(fname, {})
                doc = syms.get("docstring", "")
                desc = f" — {doc}" if doc else ""
                lines.append(f"### `{fname}`{desc}")
                lines.append("")

                for cls in syms.get("classes", []):
                    methods_str = ""
                    if cls["methods"]:
                        methods_str = f" ({', '.join(cls['methods'])})"
                    cls_doc = f": {cls['doc']}" if cls["doc"] else ""
                    lines.append(f"- **`{cls['name']}`**{cls_doc}{methods_str}")

                for fn in syms.get("functions", []):
                    fn_doc = f": {fn['doc']}" if fn["doc"] else ""
                    lines.append(f"- `{fn['name']}()`{fn_doc}")

                if syms.get("classes") or syms.get("functions"):
                    lines.append("")

        # Other files
        if dir_info["other_files"]:
            lines.append("## Other Files")
            lines.append("")
            for f in sorted(dir_info["other_files"]):
                lines.append(f"- `{f}`")
            lines.append("")

        # Parent link
        if not is_root and depth > 0:
            parent_rel = rel_path.parent
            lines.append("---")
            lines.append("")
            parent_link = "../AGENTS.md" if str(parent_rel) != "." else "AGENTS.md"
            lines.append(f"[Back to parent ({parent_rel}/)]({parent_link})")
            lines.append("")

        return "\n".join(lines)

    def _action_generate_adapter(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        skill_name = context.get("skill_name", "")
        skill_desc = context.get("skill_description", description)
        actions = context.get("actions", [])

        if not skill_name:
            return {"success": False, "error": "skill_name required"}

        code = self._generate_adapter_code(skill_name, skill_desc, actions)
        return {
            "success": True,
            "artifacts": {
                "adapter_code": code,
                "skill_name": skill_name,
                "actions": [a["name"] for a in actions] if actions else ["execute"],
            },
        }

    def _action_generate_skill_md(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        skill_name = context.get("skill_name", "")
        skill_desc = context.get("skill_description", description)
        actions = context.get("actions", [])
        version = context.get("version", "1.0")

        if not skill_name:
            return {"success": False, "error": "skill_name required"}

        content = self._generate_skill_md_content(
            skill_name, skill_desc, actions, version
        )
        return {
            "success": True,
            "artifacts": {
                "skill_md_content": content,
                "skill_name": skill_name,
            },
        }

    def _generate_adapter_code(
        self, skill_name: str, description: str, actions: List[Dict]
    ) -> str:
        name_parts = skill_name.replace("-", " ").replace("_", " ").split()
        class_name = "".join(p.capitalize() for p in name_parts) + "_Adapter"
        capitalized = skill_name.replace("-", " ").title()

        default_action = actions[0]["name"] if actions else "execute"
        actions_list_lines = []
        action_map_entries = []
        action_methods = []

        for action in actions:
            aname = action["name"]
            adesc = action.get("description", f"Execute {aname}")
            actions_list_lines.append(f"- {aname}: {adesc}")
            action_map_entries.append(f'            "{aname}": self._action_{aname},')
            params = action.get("parameters", [])
            param_docs = ""
            if params:
                param_docs = "\n        Expects context:\n"
                for p in params:
                    param_docs += f"        - {p}: ...\n"

            action_methods.append(
                f'''    def _action_{aname}(
        self, description: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """{adesc}.{param_docs}"""
        return {{
            "success": True,
            "action": "{aname}",
            "artifacts": {{
                "message": "Action {aname} executed",
            }},
        }}'''
            )

        return ADAPTER_TEMPLATE.format(
            skill_name=skill_name,
            skill_name_capitalized=capitalized,
            class_name=class_name,
            description=description,
            actions_list="\n".join(actions_list_lines),
            default_action=default_action,
            action_map_entries="\n".join(action_map_entries),
            action_methods="\n\n".join(action_methods),
        )

    def _generate_skill_md_content(
        self,
        skill_name: str,
        description: str,
        actions: List[Dict],
        version: str,
    ) -> str:
        name_parts = skill_name.replace("-", " ").replace("_", " ").split()
        class_name = "".join(p.capitalize() for p in name_parts) + "_Adapter"
        default_action = actions[0]["name"] if actions else "execute"

        actions_doc = []
        for a in actions:
            aname = a["name"]
            adesc = a.get("description", "No description")
            params = a.get("parameters", [])
            doc = f"### {aname}\n\n{adesc}"
            if params:
                doc += "\n\nParameters:\n"
                for p in params:
                    doc += f"- `{p}`\n"
            actions_doc.append(doc)

        return SKILL_MD_TEMPLATE.format(
            skill_name=skill_name,
            description=description,
            version=version,
            class_name=class_name,
            default_action=default_action,
            actions_documentation="\n\n".join(actions_doc),
            integration_notes="This skill integrates with the multi-agent pipeline via the standard adapter protocol.",
            configuration="No special configuration required.",
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "project_path": self.project_path,
        }
