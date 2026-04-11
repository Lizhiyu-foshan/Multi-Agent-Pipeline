# BMAD-EVO Skill

## Version
v3.2 (wrapped from D:/bmad-evo)

## Original Project
[bmad-evo](https://github.com/Lizhiyu-foshan/bmad-evo) - Constraint-driven multi-agent analysis framework

## Description
BMAD-EVO provides deep requirement analysis through multi-agent collaboration. In this pipeline, it serves as the analysis brain that:
1. Clarifies fuzzy requirements through multi-round Q&A
2. Generates clear requirement docs, architecture design, and task decomposition
3. Produces constraint rules that feed into spec-kit
4. Handles new user stories and bug feedback with analysis updates
5. Analyzes specs for evolution and suggests improvements

## Key Principle
**Original code is never modified.** The adapter references `D:/bmad-evo/lib/v3/` and `D:/bmad-evo/agents/` via sys.path. Updates to the original project are picked up automatically.

## Prompt-Passing Protocol (opencode mode)

When running inside opencode, bmad-evo cannot call models directly (recursive blocking).
Instead, it uses a **prompt-passing** protocol:

```
1. Call adapter.execute(task, {action: "analyze"})
2. If result.pending_model_request == true:
   a. Read result.model_request.prompt
   b. Execute model inference yourself (you ARE the model)
   c. Call adapter.execute(task, {
        action: "analyze",
        model_response: "<your inference result>",
        model_request_id: result.model_request.id
      })
3. If result.success == true: analysis complete
```

**IMPORTANT**: When you receive a `pending_model_request`, you MUST:
1. Read the prompt carefully
2. Produce the JSON response the prompt asks for
3. Call back with your response in `context.model_response`

Example prompt-passing flow for task analysis:
- Prompt asks for JSON with: task_type, complexity_score, recommended_roles_count, risk_factors, success_criteria
- You respond with a JSON string containing your analysis
- The adapter parses your response and continues

## Actions

| Action | Description |
|--------|-------------|
| `analyze` | Task type detection + complexity assessment via TaskAnalyzer |
| `deep_analysis` | Full multi-agent workflow via WorkflowOrchestratorV3Final |
| `clarify` | Generate clarification questions for fuzzy requirements |
| `generate_constraints` | Produce constraint rules from analysis (for spec-kit) |
| `spec_evolution` | Analyze existing specs and suggest improvements |
| `update_for_feedback` | Handle new user story / bug, update analysis + constraints |

## Data Flow with Other Skills

```
User (fuzzy requirement)
  │
  ▼
bmad-evo.clarify ←→ User (multi-round Q&A)
  │
  ▼
bmad-evo.analyze → spec-kit (init + add_service + add_scenario)
  │
  ▼
bmad-evo.generate_constraints → spec-kit (constraint_validator)
  │
  ▼
bmad-evo.deep_analysis → multi-agent-pipeline (task decomposition)
  │
  ▼
[Implementation via superpowers]
  │
  ▼
bmad-evo.spec_evolution ← spec-kit (analysis findings)
  │
  ▼
bmad-evo.update_for_feedback ← User (new story / bug / constraint)
  │
  ▼
spec-kit (update specs + constraints + scenarios)
```

## Spec Context Integration

When SpecGate injects `spec_context`, bmad-evo prepends it to the task:

```
[SPEC CONTEXT]
[ANCHOR] Goal: E-commerce platform | Phase: development | Services: order-service, auth-service
[SERVICE:order-service] Responsibility: Order processing | Boundaries: ...
[CONSTRAINTS] - API endpoints must return consistent error format | ...
[SCENARIOS] Pending: SC-001(P1): WHEN user submits order THEN order created
[/SPEC CONTEXT]

[ORIGINAL TASK]
Analyze the order cancellation feature
```

This ensures bmad-evo analysis is grounded in existing specs.

## Constraint Generation

`generate_constraints` produces output compatible with spec-kit's `constraints.yaml`:

```yaml
format:
  dependency_direction: [...]
  file_size_limit: {lines: 300}
contract:
  - rule: "Must satisfy: All orders require inventory check"
    scope: global
behavior:
  - rule: "Mitigate risk: Payment gateway timeout"
    applies_to: ["*"]
```

## Usage Example

```python
adapter = Bmad_Evo_Adapter(project_path=".")

# Clarify fuzzy requirements
result = adapter.execute("build a shopping system", {
    "action": "clarify",
    "previous_answers": [
        {"category": "goal", "answer": "Sell products online with delivery tracking"}
    ]
})

# Analyze with spec context
result = adapter.execute("Implement order cancellation", {
    "action": "analyze",
    "spec_context": "[ANCHOR] Goal: E-commerce..."
})

# Generate constraints for spec-kit
result = adapter.execute("Order system needs payment integration", {
    "action": "generate_constraints",
    "analysis": previous_analysis
})

# Handle bug feedback
result = adapter.execute("Order cancellation doesn't refund payment", {
    "action": "update_for_feedback",
    "feedback_type": "bug",
    "existing_analysis": previous_analysis
})

# Spec evolution analysis
result = adapter.execute("Review spec completeness", {
    "action": "spec_evolution",
    "evolution_context": {"findings": {...}}
})
```

## Fallback Behavior

If `D:/bmad-evo` is not available, the adapter falls back to local analysis logic that still produces compatible output structures, ensuring the pipeline doesn't break.
