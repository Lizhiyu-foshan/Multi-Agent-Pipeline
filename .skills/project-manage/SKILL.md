# Project-Manage

Multi-project governance skill for MAP framework.

## Actions

| Action | Description |
|--------|-------------|
| `project_init` | Initialize project (new/clone/local mode) |
| `project_get` | Get project details |
| `project_list` | List projects (filter by status) |
| `project_update` | Update project metadata |
| `project_pause` | Pause project |
| `project_resume` | Resume paused project |
| `project_archive` | Archive project |
| `project_delete` | Delete project |
| `pack_activate` | Activate constraint pack |
| `ingest_external_changes` | Ingest external changes |
| `drift_check` | Check constraint drift |
| `evaluate_gates` | Evaluate gates |
| `deliver_local` | Local delivery |
| `deliver_github` | GitHub PR delivery (Phase 3) |
| `dashboard_summary` | Cross-project dashboard |

## Project Lifecycle

```
INIT -> ACTIVE -> PAUSED -> COMPLETED -> ARCHIVED
                 \-> ABANDONED -> ARCHIVED
```

## Delivery State Machine

```
DRAFT -> STAGED -> GATE_PASSED -> APPROVED -> PROMOTED -> VERIFIED
```
