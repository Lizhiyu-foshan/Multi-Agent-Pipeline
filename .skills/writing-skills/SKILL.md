# writing-skills

Meta-skill for generating, validating, upgrading skill adapters and SKILL.md files, plus recursive AGENTS.md generation with AST symbol extraction.

## Version
1.1

## Actions

### scaffold
Generate a complete new skill from a specification.

Parameters:
- `skill_name` - Name for the new skill
- `skill_description` - What the skill does
- `actions` - List of action dicts with name, description, parameters

### validate
Check if an existing skill follows conventions.

Parameters:
- `skill_name` - Skill to validate

### upgrade
Add missing sections and conventions to an existing skill.

Parameters:
- `skill_name` - Skill to upgrade

### generate_adapter
Generate adapter.py code from a skill specification.

Parameters:
- `skill_name`, `skill_description`, `actions`

### generate_skill_md
Generate SKILL.md content from a skill specification.

Parameters:
- `skill_name`, `skill_description`, `actions`, `version`

### init_deep
Recursively generate AGENTS.md for every directory in a project, with AST symbol extraction.

Parameters:
- `target_dir` - Root directory to scan
- `project_description` - Project name/description for root AGENTS.md
- `max_depth` - Maximum recursion depth (default 4)
- `exclude` - List of directory names to skip
- `dry_run` - If true, plan without writing files

Generated AGENTS.md includes:
- AST-extracted symbols (classes, methods, functions, docstrings)
- Subdirectory links
- Parent navigation
- File inventory

## Integration

This skill is used by the multi-agent pipeline to dynamically create new skills during execution. Generated skills follow the standard adapter protocol with `execute()` method and PromptManager integration.

## Configuration

No special configuration required. Works with the standard skill loading mechanism.
