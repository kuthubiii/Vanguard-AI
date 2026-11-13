"""Persisted video metadata."""

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from aai.db.base import Base


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (Index("uq_videos_upload_idempotency_key", "upload_idempotency_key", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    storage_key: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    upload_idempotency_key: Mapped[str | None] = mapped_column(String(36), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    upload_status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploaded")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'completed', 'failed')", name="ck_analysis_job_status"),
        Index("ix_analysis_jobs_created_at", "created_at"),
        Index(
            "uq_analysis_job_active_video",
            "video_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    progress_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False, default="Queued")
    frames_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    frames_sampled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detections_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.25)
    sampling_rate_fps: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    image_size: Mapped[int] = mapped_column(Integer, nullable=False, default=640)
    tracking_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    inference_device: Mapped[str | None] = mapped_column(String(32), nullable=True)
    annotated_storage_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Detection(Base):
    __tablename__ = "detections"
    __table_args__ = (
        Index("ix_detections_job_frame", "job_id", "frame_index"),
        Index("ix_detections_job_track_frame", "job_id", "track_id", "frame_index"),
    )

    detection_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.job_id", ondelete="CASCADE"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_name: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bounding_box: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    # Nullable for legacy rows and detections that ByteTrack did not associate.
    # Track IDs are scoped by (job_id, track_id), never globally.
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AIReport(Base):
    """Validated reasoning output tied to the immutable source analysis job."""

    __tablename__ = "ai_reports"
    __table_args__ = (Index("uq_ai_reports_job_id", "job_id", unique=True),)

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.job_id", ondelete="CASCADE"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    observations: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    recommendations: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AgentConfig(Base):
    """Persistent enablement for registered workflow agents."""

    __tablename__ = "agent_configs"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AgentWorkflow(Base):
    """Persisted orchestration request and its compact final result."""

    __tablename__ = "agent_workflows"
    __table_args__ = (
        Index("ix_agent_workflows_job_created", "job_id", "created_at"),
        Index("ix_agent_workflows_status", "status"),
        Index("uq_agent_workflows_active_job", "job_id", unique=True,
              sqlite_where=text("status IN ('pending', 'validating', 'running')")),
    )

    workflow_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.job_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    current_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_execution_history: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentExecution(Base):
    """Individual agent execution status and summarized output."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        Index("ix_agent_executions_workflow_sequence", "workflow_id", "sequence"),
        Index("ix_agent_executions_agent_started", "agent_id", "started_at"),
    )

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("agent_workflows.workflow_id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    current_task: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class OperationalEvent(Base):
    """Immutable, evidence-backed rule observation for human review."""

    __tablename__ = "operational_events"
    __table_args__ = (
        Index("uq_operational_events_event_key", "event_key", unique=True),
        Index("ix_operational_events_job_occurred", "job_id", "occurred_at"),
        Index("ix_operational_events_type_severity", "event_type", "severity"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.job_id", ondelete="CASCADE"), nullable=False)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    rule_basis: Mapped[dict] = mapped_column(JSON, nullable=False)
    related_detection_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class OperationalReview(Base):
    """Append-only human review decisions for operational events."""

    __tablename__ = "operational_reviews"
    __table_args__ = (Index("ix_operational_reviews_event_time", "event_id", "reviewed_at"),)

    review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("operational_events.event_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_identifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class ActivityLog(Base):
    """Append-only supplemental lifecycle events not represented by domain rows."""

    __tablename__ = "activity_logs"
    __table_args__ = (
        CheckConstraint("category IN ('VIDEO','ANALYSIS','DETECTION','TRACKING','REASONING','AGENT','WORKFLOW','OPERATIONS','REVIEW','SYSTEM','SECURITY')", name="ck_activity_log_category"),
        CheckConstraint("severity IN ('INFO','SUCCESS','WARNING','ERROR','CRITICAL')", name="ck_activity_log_severity"),
        CheckConstraint("status IN ('PENDING','RUNNING','COMPLETED','FAILED','CANCELLED','SKIPPED')", name="ck_activity_log_status"),
        Index("uq_activity_logs_idempotency_key", "idempotency_key", unique=True),
        Index("ix_activity_logs_created_at", "created_at"),
        Index("ix_activity_logs_category", "category"),
        Index("ix_activity_logs_severity", "severity"),
        Index("ix_activity_logs_status", "status"),
        Index("ix_activity_logs_event_type", "event_type"),
        Index("ix_activity_logs_analysis_job_id", "analysis_job_id"),
        Index("ix_activity_logs_workflow_id", "workflow_id"),
        Index("ix_activity_logs_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    video_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    analysis_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operational_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
