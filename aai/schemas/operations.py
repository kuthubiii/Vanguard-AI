"""Operational intelligence API contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ReviewStatus = Literal["pending", "reviewed", "dismissed"]
Severity = Literal["informational", "low", "medium", "high"]


class OperationalFilters(BaseModel):
    analysis_job_id: str | None = None
    video_id: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


class MetricSummary(BaseModel):
    total_videos_analysed: int
    completed_analysis_jobs: int
    total_detected_objects: int
    total_unique_tracked_entities: int
    tracked_object_categories: list[str]
    available_ai_reports: int
    events_requiring_attention: int


class RecentAnalysis(BaseModel):
    analysis_job_id: str
    video_id: str
    video_filename: str
    status: str
    detections_count: int
    unique_tracked_entities: int
    created_at: datetime


class CategoryMetric(BaseModel):
    class_id: int
    class_name: str
    detection_count: int
    percent_of_detections: float
    unique_tracked_entities: int


class TrendPoint(BaseModel):
    bucket: str
    detection_count: int
    tracked_detection_count: int


class ConfidenceBucket(BaseModel):
    confidence_range: str
    detection_count: int


class TrackActivity(BaseModel):
    analysis_job_id: str
    video_id: str
    video_filename: str
    track_id: int
    class_id: int
    class_name: str
    observation_count: int
    first_observed_seconds: float
    last_observed_seconds: float


class JobComparison(BaseModel):
    analysis_job_id: str
    video_id: str
    video_filename: str
    created_at: datetime
    detection_count: int
    unique_tracked_entities: int


class OperationalOverviewRead(BaseModel):
    filters: OperationalFilters
    metrics: MetricSummary
    recent_analyses: list[RecentAnalysis]
    category_distribution: list[CategoryMetric]
    detection_trend: list[TrendPoint]


class OperationalAnalyticsRead(BaseModel):
    filters: OperationalFilters
    category_distribution: list[CategoryMetric]
    confidence_distribution: list[ConfidenceBucket]
    detection_trend: list[TrendPoint]
    tracking_activity: list[TrackActivity]
    tracking_total: int
    tracking_limit: int
    tracking_offset: int
    job_comparisons: list[JobComparison]


class EventEvidence(BaseModel):
    detection_ids: list[str] = Field(default_factory=list)
    timestamp_seconds: float | None = None
    class_name: str | None = None


class OperationalEventRead(BaseModel):
    event_id: str
    event_type: str
    severity: Severity
    title: str
    description: str
    analysis_job_id: str
    video_id: str
    event_timestamp: datetime
    evidence_explanation: str
    rule_basis: dict[str, Any]
    evidence: EventEvidence
    review_status: ReviewStatus
    reviewed_at: datetime | None = None
    reviewer_identifier: str | None = None
    review_note: str | None = None


class OperationalEventListRead(BaseModel):
    items: list[OperationalEventRead]
    total: int
    limit: int
    offset: int


class OperationalReviewRequest(BaseModel):
    status: Literal["reviewed", "dismissed"]
    reviewer_identifier: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class RiskIndicatorRead(BaseModel):
    event_id: str
    risk_category: str
    severity: Severity
    title: str
    evidence_summary: str
    analysis_job_id: str
    video_id: str
    related_detection_ids: list[str]
    rule_triggered: dict[str, Any]
    suggested_next_action: str
    requires_human_review: Literal[True] = True
    review_status: ReviewStatus


class RiskIndicatorListRead(BaseModel):
    items: list[RiskIndicatorRead]
    total: int
    limit: int
    offset: int


class OperationalRecommendationRead(BaseModel):
    recommendation_id: str
    priority: Literal["high", "medium", "low"]
    title: str
    description: str
    reason: str
    analysis_job_id: str | None = None
    video_id: str | None = None
    evidence_references: list[str] = Field(default_factory=list)
    source: Literal["rule_based", "ai_report"]
    requires_human_review: Literal[True] = True


class OperationalRecommendationListRead(BaseModel):
    items: list[OperationalRecommendationRead]
    total: int
    limit: int
    offset: int


class CapabilityAvailability(BaseModel):
    capability: str
    status: Literal["available", "partially_available", "not_implemented", "requires_custom_model"]
    details: str


class OperationalAvailabilityRead(BaseModel):
    available_data_sources: list[str]
    capabilities: list[CapabilityAvailability]
    ai_report_available: bool
    ai_runtime_required_for_basic_analytics: Literal[False] = False
