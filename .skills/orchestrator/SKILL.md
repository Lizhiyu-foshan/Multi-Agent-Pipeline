# Core Orchestrator Skill

## Version
v2.0

## Description
Top-level orchestrator that evaluates task complexity, selects execution path, dynamically loads skills, manages pipeline lifecycle, and generates final reports. Delegates to PipelineOrchestrator for state machine management.

## Capabilities
- Task complexity evaluation (1-10 scale)
- Execution path selection (simple / complex / auto)
- Skill dynamic loading
- Pipeline lifecycle management
- Report generation

## Integration with PipelineOrchestrator

The Core Orchestrator delegates to PipelineOrchestrator for:
- **IntentGate**: Pre-analysis intent classification in INIT phase
- **Model routing**: QUICK/STANDARD/DEEP/ULTRABRAIN per task
- **ParallelExecutor**: Concurrent task execution
- **PDCA loops**: Plan-Do-Check-Act quality cycles
- **Checkpoint/recovery**: Full state snapshots

## Input
- Task description
- Execution path type (simple / complex / auto)
- Maximum execution time

## Output
- Execution report
- Task checklist
- Time estimates
- Recommendations
- Intent analysis result

## Usage Example

```python
from pipeline import PipelineOrchestrator
from specs.spec_gate import SpecGate

gate = SpecGate(".specs")
orch = PipelineOrchestrator(
    state_dir=".pipeline",
    skills={
        "bmad-evo": Bmad_Evo_Adapter(),
        "superpowers": SuperpowersAdapter(),
        "spec-kit": SpecKitAdapter(),
    },
    spec_gate=gate,
)

pipeline, _ = orch.create_pipeline("Build an e-commerce system")
result = orch.advance(pipeline.id, {"success": True})
```

## Configuration

See `configs/` directory for platform-specific configurations.
