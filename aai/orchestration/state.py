"""Compact typed LangGraph state; media and bulk records remain in SQLite/storage."""

from datetime import datetime
from typing import Any, TypedDict


class WorkflowState(TypedDict):
    workflow_id: str
    job_id: str
    execution_status: str
    current_agent: str | None
    detection_summary: dict[str, Any] | None
    tracking_summary: dict[str, Any] | None
    evidence_references: list[str]
    reasoning_result: dict[str, Any] | None
    operations_result: dict[str, Any] | None
    validation_result: dict[str, Any] | None
    agent_execution_history: list[dict[str, Any]]
    error_details: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
    evidence: dict[str, Any]
    enabled_agents: list[str]
