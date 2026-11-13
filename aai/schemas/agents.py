"""Public contracts for agent registry and workflow execution."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

WorkflowStatus = Literal["pending", "validating", "running", "completed", "failed", "cancelled"]
AgentStatus = Literal["pending", "running", "completed", "failed", "skipped", "idle"]


class AgentRead(BaseModel):
    agent_id: str
    name: str
    description: str
    version: str
    capabilities: list[str]
    enabled: bool
    current_status: AgentStatus
    current_task: str | None = None
    last_execution_at: datetime | None = None
    last_duration_ms: float | None = None
    result_summary: str | None = None
    error_details: dict[str, Any] | None = None


class AgentEnableRequest(BaseModel):
    enabled: bool


class WorkflowCreate(BaseModel):
    job_id: str = Field(min_length=1, max_length=36)


class AgentExecutionRead(BaseModel):
    agent_id: str
    status: AgentStatus
    current_task: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    result_summary: str | None = None
    error_details: dict[str, Any] | None = None


class WorkflowRead(BaseModel):
    workflow_id: str
    job_id: str
    status: WorkflowStatus
    current_agent: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    agent_execution_history: list[AgentExecutionRead]
    result: dict[str, Any] | None = None
    error_details: dict[str, Any] | None = None
