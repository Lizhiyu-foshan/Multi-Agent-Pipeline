"""
Metrics Aggregator - Cross-project dashboard metrics.

Dashboard v1 metrics:
  1. progress: per-project delivery completion rate
  2. avg_development_duration_hours: mean time from project creation to last promoted delivery
  3. quality_score: gate pass rate (passed / total evaluations)
  4. model_failure_rate: failed deliveries / total deliveries
  5. retry_rate: rolled_back deliveries / total deliveries
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class MetricsAggregator:
    def __init__(self, state_dir: str = None):
        if state_dir is None:
            state_dir = str(Path.cwd() / ".pipeline")
        self._state_dir = state_dir

    def summary(self) -> Dict[str, Any]:
        from .registry import ProjectRegistry

        registry = ProjectRegistry(state_dir=self._state_dir)
        all_projects = registry.list_projects(status="all")
        projects = all_projects.get("artifacts", {}).get("projects", [])

        deliveries = self._load_deliveries()
        audit_events = self._load_audit_events()

        by_status = {
            s: len([p for p in projects if p.get("status") == s])
            for s in (
                "init",
                "active",
                "paused",
                "completed",
                "abandoned",
                "archived",
            )
        }

        active_count = by_status.get("active", 0)
        total_deliveries = len(deliveries)

        progress = self._compute_progress(projects, deliveries)
        avg_duration = self._compute_avg_duration(projects, deliveries)
        quality_score = self._compute_quality(audit_events)
        model_failure_rate = self._compute_failure_rate(deliveries)
        retry_rate = self._compute_retry_rate(deliveries)

        return {
            "success": True,
            "action": "dashboard_summary",
            "artifacts": {
                "total_projects": len(projects),
                "active_projects": active_count,
                "total_deliveries": total_deliveries,
                "progress": progress,
                "avg_development_duration_hours": avg_duration,
                "quality_score": quality_score,
                "model_failure_rate": model_failure_rate,
                "retry_rate": retry_rate,
                "projects_by_status": by_status,
            },
        }

    def _load_deliveries(self) -> List[Dict[str, Any]]:
        deliveries_file = Path(self._state_dir) / "global" / "deliveries.json"
        if not deliveries_file.exists():
            return []
        try:
            with open(str(deliveries_file), "r", encoding="utf-8") as f:
                data = json.load(f)
            return list(data.values())
        except Exception as e:
            logger.error(f"Failed to load deliveries for metrics: {e}")
            return []

    def _load_audit_events(self) -> List[Dict[str, Any]]:
        audit_file = Path(self._state_dir) / "global" / "delivery_audit.jsonl"
        if not audit_file.exists():
            return []
        events = []
        try:
            with open(str(audit_file), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to load audit events for metrics: {e}")
        return events

    def _compute_progress(
        self, projects: List[Dict], deliveries: List[Dict]
    ) -> Dict[str, Any]:
        if not projects:
            return {"avg_completion_rate": 0.0, "per_project": {}}

        per_project = {}
        project_ids = {p.get("project_id") for p in projects}
        for pid in project_ids:
            proj_deliveries = [d for d in deliveries if d.get("project_id") == pid]
            total = len(proj_deliveries)
            completed = len(
                [
                    d
                    for d in proj_deliveries
                    if d.get("status") in ("promoted", "verified")
                ]
            )
            rate = completed / total if total > 0 else 0.0
            per_project[pid] = {
                "total_deliveries": total,
                "completed_deliveries": completed,
                "completion_rate": round(rate, 3),
            }

        rates = [v["completion_rate"] for v in per_project.values()]
        avg_rate = sum(rates) / len(rates) if rates else 0.0

        return {"avg_completion_rate": round(avg_rate, 3), "per_project": per_project}

    def _compute_avg_duration(
        self, projects: List[Dict], deliveries: List[Dict]
    ) -> float:
        durations = []
        for proj in projects:
            created = proj.get("created_at")
            if not created:
                continue
            try:
                created_dt = (
                    datetime.fromisoformat(created)
                    if isinstance(created, str)
                    else created
                )
            except (ValueError, TypeError):
                continue

            proj_id = proj.get("project_id")
            proj_deliveries = [
                d
                for d in deliveries
                if d.get("project_id") == proj_id and d.get("promoted_at")
            ]
            if not proj_deliveries:
                continue

            latest_promoted = None
            for d in proj_deliveries:
                pa = d.get("promoted_at")
                if not pa:
                    continue
                try:
                    pa_dt = datetime.fromisoformat(pa) if isinstance(pa, str) else pa
                except (ValueError, TypeError):
                    continue
                if latest_promoted is None or pa_dt > latest_promoted:
                    latest_promoted = pa_dt

            if latest_promoted:
                hours = (latest_promoted - created_dt).total_seconds() / 3600.0
                durations.append(hours)

        if not durations:
            return 0.0
        return round(sum(durations) / len(durations), 2)

    def _compute_quality(self, audit_events: List[Dict]) -> float:
        gate_events = [
            e
            for e in audit_events
            if e.get("event_type") in ("delivery_promoted", "delivery_verified")
        ]
        if not gate_events:
            return 0.0
        verified = len(
            [e for e in gate_events if e.get("event_type") == "delivery_verified"]
        )
        return round(verified / len(gate_events), 3)

    def _compute_failure_rate(self, deliveries: List[Dict]) -> float:
        if not deliveries:
            return 0.0
        failed = len([d for d in deliveries if d.get("status") == "failed"])
        return round(failed / len(deliveries), 3)

    def _compute_retry_rate(self, deliveries: List[Dict]) -> float:
        if not deliveries:
            return 0.0
        rolled_back = len([d for d in deliveries if d.get("status") == "rolled_back"])
        return round(rolled_back / len(deliveries), 3)
