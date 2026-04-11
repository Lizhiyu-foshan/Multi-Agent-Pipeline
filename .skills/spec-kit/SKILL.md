# Spec-Kit Skill

## Version
v2.1

## Based On
[github/spec-kit](https://github.com/github/spec-kit) - Spec-Driven Development toolkit

## Description
Spec-Kit manages the full spec lifecycle for multi-agent development. It provides:
1. **Agent Reasoning Map** (Agent.md) - System-level and service-level context to prevent goal drift
2. **Semantic Constraints** - Format/contract/behavior rules linters cannot enforce
3. **WHEN/THEN Scenarios** - Structured acceptance criteria with status tracking
4. **Self-Evolution** - Spec analysis and improvement suggestions via bmad-evo

## Capabilities
- Initialize `.specs/` directory with Agent.md entry point
- Create/manage service-level reasoning maps
- Define and validate semantic constraints (dependency direction, naming, contracts, behaviors)
- Track WHEN/THEN scenario status (pending/passed/failed)
- Analyze specs for gaps, drift, and incomplete definitions
- Generate evolution suggestions for bmad-evo
- Provide context injection for agents (system goal + constraints + pending scenarios)

## Actions

| Action | Description |
|--------|-------------|
| `init` | Initialize `.specs/` with Agent.md, constraints, evolution log |
| `add_service` | Add service to reasoning map (creates service spec) |
| `add_scenario` | Add WHEN/THEN acceptance scenario |
| `update_scenario` | Update scenario status (pending/passed/failed) |
| `get_context` | Get full agent context (goal + constraints + scenarios) |
| `validate` | Run constraint validation against project files |
| `analyze` | Analyze specs for gaps and drift |
| `evolve` | Generate bmad-evo analysis context for spec evolution |
| `full` | Get complete spec status (default action) |

## Directory Structure

```
.specs/
├── services/           # Service-level reasoning maps
│   ├── auth-service.md
│   └── order-service.md
├── features/           # Feature specifications (spec-kit compatible)
│   └── 001-login/
│       └── spec.md
├── scenarios/          # WHEN/THEN acceptance scenarios
│   ├── auth-service-scenarios.yaml
│   └── order-service-scenarios.yaml
├── constraints/        # Semantic constraints
│   └── constraints.yaml
└── evolution-log.yaml  # Spec evolution history

Agent.md                # System-level entry point (YAML front-matter + Markdown)
```

## Agent.md Format

```yaml
---
system:
  name: "System Name"
  goal: "What this system is meant to achieve"
  version: "0.1.0"
  phase: "development"
services:
  - name: "service-a"
    responsibility: "What this service does"
    boundaries: ["rule 1", "rule 2"]
    spec_file: ".specs/services/service-a.md"
constraints_file: ".specs/constraints/constraints.yaml"
---

# System Reasoning Map
[Detailed system-level description]
```

## Scenario Format

```yaml
- id: "SC-001"
  name: "User login with valid credentials"
  given: "User exists with valid credentials"
  when: "User submits login form"
  then: "JWT token is returned"
  status: "pending"  # pending | passed | failed
  priority: "P1"
```

## Integration with Other Skills

- **bmad-evo**: Provides spec analysis context; receives improvement suggestions
- **superpowers**: Constraints guide code generation; scenarios define test targets
- **multi-agent-pipeline**: Reasoning map feeds task decomposition
- **LifecycleHookRegistry**: 6 lifecycle points (on_pipeline_start, on_task_start, on_task_complete, on_pdca_cycle, on_pipeline_complete, on_error) with chained handlers
- **SpecGate**: Two-stage review framework (spec compliance → quality) with progressive context injection (L1~50/L2~200/L3~1000 tokens)

## Usage Example

```python
adapter = SpecKit_Adapter(project_path=".")

# Initialize specs
result = adapter.execute("Build e-commerce system", {
    "action": "init",
    "system_name": "E-Commerce Platform",
    "system_goal": "A full-featured e-commerce platform with order management"
})

# Add service
result = adapter.execute("", {
    "action": "add_service",
    "service_name": "auth-service",
    "responsibility": "User authentication and session management",
    "boundaries": ["No direct DB access from UI", "Tokens expire in 30min"],
    "capabilities": ["login", "logout", "token-refresh", "password-reset"]
})

# Add scenario
result = adapter.execute("", {
    "action": "add_scenario",
    "service": "auth-service",
    "scenario_id": "SC-001",
    "given": "User with valid credentials",
    "when": "User submits login",
    "then": "JWT token returned and session created",
    "priority": "P1"
})

# Get context for agent
result = adapter.execute("", {"action": "get_context", "service": "auth-service"})
```
