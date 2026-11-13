"""Contracts for the searchable activity feed."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ActivityCategory = Literal[
    "VIDEO", "ANALYSIS", "DETECTION", "TRACKING", "REASONING", "AGENT",
    "WORKFLOW", "OPERATIONS", "REVIEW", "SYSTEM", "SECURITY",
]
ActivityStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"]
ActivitySeverity = Literal["INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]


class ActivityLogRead(BaseModel):
    id: str
    category: ActivityCategory
    event_type: str
    title: str
    description: str
    severity: ActivitySeverity
    status: ActivityStatus
    source: str
    video_id: str | None = None
    analysis_job_id: str | None = None
    workflow_id: str | None = None
    agent_id: str | None = None
    operational_event_id: str | None = None
    timestamp: datetime
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = None


class ActivityLogListRead(BaseModel):
    items: list[ActivityLogRead]
    total: int
    limit: int
    offset: int


class ActivitySummaryRead(BaseModel):
    total_events: int
    successful_executions: int
    failed_executions: int
    warning_events: int
    recent_events: int
    recent_window_hours: Literal[24] = 24


class ActivityFiltersRead(BaseModel):
    categories: list[ActivityCategory]
    event_types: list[str]
    severities: list[ActivitySeverity]
    statuses: list[ActivityStatus]
    sources: list[str]


class ActivityLogQuery(BaseModel):
    category: ActivityCategory | None = None
    event_type: str | None = Field(default=None, max_length=96)
    severity: ActivitySeverity | None = None
    status: ActivityStatus | None = None
    source: str | None = Field(default=None, max_length=64)
    date_from: datetime | None = None
    date_to: datetime | None = None
    video_id: str | None = Field(default=None, max_length=36)
    analysis_job_id: str | None = Field(default=None, max_length=36)
    workflow_id: str | None = Field(default=None, max_length=36)
    agent_id: str | None = Field(default=None, max_length=64)
    search: str | None = Field(default=None, max_length=200)
