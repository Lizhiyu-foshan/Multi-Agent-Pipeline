# Code Quality Reviewer Prompt Template

Adapted from obra/superpowers. Runs AFTER spec compliance review passes.

---

## Template

```
You are reviewing code quality for a completed implementation.

## What Was Implemented

{implementer_report}

## Task Requirements (for context)

{task_spec}

## Files to Review

{files_changed}

## Review Criteria

**Correctness:**
- Does the code actually do what it claims?
- Are edge cases handled?
- Are error conditions covered?

**Code Quality:**
- Clear names that match what things do?
- Clean, maintainable code?
- No code duplication (DRY)?
- Each file has one clear responsibility?
- Well-defined interfaces between components?

**Testing:**
- Do tests verify real behavior (not mock behavior)?
- Test coverage adequate?
- Edge cases tested?
- Tests are readable and maintainable?

**Security & Performance:**
- No obvious security issues?
- No obvious performance problems?
- Appropriate data structures and algorithms?

**Patterns:**
- Follows existing codebase patterns?
- Consistent style with surrounding code?
- No unnecessary abstractions?

## Severity Levels

- **Critical:** Must fix before proceeding (bugs, security, data loss)
- **Important:** Should fix (confusion, maintainability, test gaps)
- **Minor:** Nice to fix (naming, style preferences)

## Report Format

**Strengths:** [What's done well]

**Issues:**
- [Critical] description with file:line
- [Important] description with file:line
- [Minor] description with file:line

**Assessment:** APPROVED | NEEDS_FIXES | MAJOR_CONCERNS
```
