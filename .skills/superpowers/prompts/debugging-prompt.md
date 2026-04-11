# Systematic Debugging Prompt Template

Adapted from obra/superpowers 4-phase root cause process.

---

## Template

```
You are debugging an issue. Follow the 4-phase systematic process.

## The Issue

{error_description}

## What Was Expected

{expected_behavior}

## What Actually Happened

{actual_behavior}

## THE IRON LAW: NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST

If you haven't completed Phase 1, you cannot propose fixes.

## Phase 1: Root Cause Investigation

1. **Read Error Messages Carefully**
   - Stack traces, line numbers, error codes
   - They often contain the exact solution

2. **Reproduce Consistently**
   - Can you trigger it reliably?
   - Exact steps? Every time?

3. **Check Recent Changes**
   - What changed that could cause this?
   - Git diff, recent commits

4. **Trace Data Flow**
   - Where does the bad value originate?
   - What passed it in?
   - Trace backward to the source

## Phase 2: Pattern Analysis

1. Find working examples in the same codebase
2. Compare working vs broken — list every difference
3. Understand dependencies and assumptions

## Phase 3: Hypothesis and Testing

1. Form SINGLE hypothesis: "I think X is the root cause because Y"
2. Make SMALLEST possible change to test
3. One variable at a time
4. Verify before continuing

## Phase 4: Implementation

1. Create failing test case that reproduces the bug
2. Implement single fix addressing root cause
3. Verify fix works
4. No other tests broken

## If 3+ Fixes Failed

STOP. Question the architecture:
- Is this pattern fundamentally sound?
- Are we fixing symptoms instead of root cause?
- Should we refactor vs continue fixing?

Report back for architectural discussion.

## Report Format

**Phase completed:** 1 | 2 | 3 | 4
**Root cause:** [or "not yet identified"]
**Hypothesis:** [if applicable]
**Fix:** [if applicable]
**Verification:** [test results]
**Status:** ROOT_CAUSE_FOUND | NEEDS_MORE_INVESTIGATION | ARCHITECTURAL_CONCERN
```
