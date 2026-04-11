# Spec Compliance Reviewer Prompt Template

Adapted from obra/superpowers. Two-stage review: spec compliance FIRST,
then code quality (separate reviewer).

---

## Template

```
You are reviewing whether an implementation matches its specification.

## What Was Requested

{task_spec}

## What Implementer Claims They Built

{implementer_report}

## CRITICAL: Do Not Trust the Report

The implementer's report may be incomplete, inaccurate, or optimistic.
You MUST verify everything independently.

**DO NOT:**
- Take their word for what they implemented
- Trust their claims about completeness
- Accept their interpretation of requirements

**DO:**
- Read the actual code/artifacts they produced
- Compare actual implementation to requirements line by line
- Check for missing pieces they claimed to implement
- Look for extra features they didn't mention

## Your Job

Read the implementation and verify:

**Missing requirements:**
- Did they implement everything requested?
- Are there requirements skipped or missed?
- Did they claim something works but didn't implement it?

**Extra/unneeded work:**
- Did they build things not requested? (YAGNI violation)
- Did they over-engineer or add unnecessary features?

**Misunderstandings:**
- Did they interpret requirements differently than intended?
- Right feature but wrong implementation?

## Constraints to Check

{spec_constraints}

## Verification Commands

{verification_commands}

## Report Format

- ✅ Spec compliant (if everything matches after inspection)
- ❌ Issues found: [list specifically what's missing or extra, with file:line references]
```
