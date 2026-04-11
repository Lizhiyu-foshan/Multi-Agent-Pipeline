# Superpowers Skill - Engineering Execution Layer

## Version
v2.3

## Description
Superpowers is the engineering execution layer, adapted from [obra/superpowers](https://github.com/obra/superpowers) (145K+ stars). It provides structured task execution, two-stage review (with AST-based CodeAnalyzer), TDD enforcement, systematic debugging, and hash-verified file editing — the concepts not covered by bmad-evo, spec-kit, and multi-agent-pipeline.

## What's Different From Upstream

Superpowers has 14 skills. We don't reimplement them all — only the concepts our system was missing:

| Superpowers Skill | Our Equivalent | Status |
|---|---|---|
| brainstorming | bmad-evo clarify/analyze | Already covered |
| writing-plans | bmad-evo plan + pipeline PLAN | Already covered |
| executing-plans | pipeline EXECUTE + AgentLoop | Already covered |
| subagent-driven-development | **superpowers execute_task** | **Implemented** |
| test-driven-development | **superpowers tdd_cycle** | **Implemented** |
| systematic-debugging | **superpowers debug** | **Implemented** |
| requesting-code-review | **superpowers spec_review + code_quality_review** | **Implemented** (split into 2 stages) |
| verification-before-completion | SpecGate post_check | Already covered |
| finishing-a-development-branch | pipeline CHECK/DECIDE | Already covered |
| dispatching-parallel-agents | ParallelExecutor | **Implemented** |
| using-git-worktrees | WorktreeManager | **Implemented** |
| writing-skills | writing-skills meta-skill | **Implemented** |
| using-superpowers | - | Meta-skill |
| receiving-code-review | AgentLoop feedback | Already covered |

## Actions

| Action | Description |
|--------|-------------|
| `execute_task` | Fine-grained task execution with structured implementer prompt |
| `spec_review` | Stage 1 review: spec compliance (did we build what was requested?) |
| `code_quality_review` | Stage 2 review: code quality via CodeAnalyzer AST engine (is it well-built?) |
| `debug` | 4-phase systematic debugging (root cause → pattern → hypothesis → fix) |
| `tdd_cycle` | RED-GREEN-REFACTOR cycle enforcement |
| `hashline_edit` | Hash-verified file editing with 8 sub-actions (OMO-inspired) |

## Two-Stage Review

The core innovation from Superpowers: **separate spec compliance from code quality**.

```
implementer executes task
    ↓
spec reviewer: "Did they build WHAT was asked?" (missing? extra? misunderstood?)
    ↓ (passes)
code quality reviewer: "Is it WELL-BUILT?" — uses CodeAnalyzer AST engine
    ↓ (passes: score ≥ 85/100)
task complete
```

`code_quality_review` uses the pipeline-level `CodeAnalyzer` for AST-based analysis:
- 12 rules: null_check, type_annotation, exception_flow, io_safety, hardcoded_secret, naming, function_length, cyclomatic_complexity, documentation, pseudo_ai, bare_except, debug_print
- 3 modes: fast (AST-only), strict (AST+regex), regex_only
- Scoring: 0-100, threshold 85

## HashlineEditTool

Content-hash-verified file editing, inspired by OMO's Hashline Edit Tool:

| Sub-action | Description |
|------------|-------------|
| `read_file` | Read file with hash-annotated lines |
| `replace_lines` | Replace lines (hash-verified) |
| `insert_after` | Insert line after target |
| `insert_before` | Insert line before target |
| `delete_lines` | Delete lines (hash-verified) |
| `multi_edit` | Multiple operations in one call |
| `diff_preview` | Preview changes without applying |
| `restore` | Restore from backup |

## TDD Enforcement

```
RED   → Write failing test (MANDATORY — no code without failing test)
        ↓
GREEN → Write minimal code to pass (YAGNI — nothing extra)
        ↓
REFACTOR → Clean up while keeping tests green
        ↓
COMMIT
```

## Prompt Templates

All prompt templates live in `.skills/superpowers/prompts/`:

| Template | Purpose |
|----------|---------|
| `implementer-prompt.md` | Structured context for task execution |
| `spec-reviewer-prompt.md` | Spec compliance review instructions |
| `code-quality-reviewer-prompt.md` | Code quality review instructions |
| `debugging-prompt.md` | 4-phase systematic debugging process |

## Integration Points

- **AgentLoop**: superpowers is called as the skill_execute_fn in iteration loops
- **pipeline_orchestrator**: dispatches to superpowers for developer/tester roles
- **CodeAnalyzer**: pipeline-level AST engine used in code_quality_review
- **ExecutionEvaluator**: spec_review and code_quality_review enrich evaluation dimensions
- **spec-kit**: SpecGate constraints are checked during spec_review
- **PromptManager**: `hashline_edit_protocol` section + `superpowers/hashline_edit` template
