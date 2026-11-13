"""Validated local reasoning requests and responses."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceReference(BaseModel):
    type: Literal["detection_or_tracking"]
    track_id: int | None = None
    timestamp: float = Field(ge=0)
    detection_id: str
    # Optional to keep already-persisted Phase 6 reports readable. New model
    # responses are required by reasoning_service to return both values.
    class_id: int | None = None
    class_name: str | None = None


class AIObservation(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1500)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=12)
    confidence: Literal["high", "medium", "low"]
    limitations: str = Field(max_length=1000)


class AIRecommendation(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1200)
    reason: str = Field(min_length=1, max_length=1200)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=12)
    requires_human_review: Literal[True] = True


class AIReportPayload(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    observations: list[AIObservation] = Field(default_factory=list, max_length=30)
    recommendations: list[AIRecommendation] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class AIReportRead(AIReportPayload):
    model_config = ConfigDict(from_attributes=True)

    analysis_job_id: str
    generated_by: str
    generated_at: datetime
    evidence: dict


class AskAIRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AskAIResponse(BaseModel):
    analysis_job_id: str
    answer: str = Field(min_length=1, max_length=4000)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    generated_by: str


class AIStatusRead(BaseModel):
    provider: Literal["lm_studio"] = "lm_studio"
    endpoint: str
    model: str
    connected: bool
    model_available: bool
    inference_ready: bool
    status: Literal["ready", "unavailable", "model_missing", "inference_unready"]
    message: str
