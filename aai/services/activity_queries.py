"""Indexed, paginated activity feed over supplemental logs and existing records."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import String, case, cast, func, literal, select, union_all
from sqlalchemy.orm import Session

from aai.db.models import (
    AIReport, ActivityLog, AgentExecution, AgentWorkflow, AnalysisJob,
    OperationalEvent, OperationalReview, Video,
)
from aai.schemas.activity import (
    ActivityCategory, ActivityFiltersRead, ActivityLogListRead, ActivityLogQuery,
    ActivityLogRead, ActivitySeverity, ActivityStatus, ActivitySummaryRead,
)


_CATEGORIES: tuple[ActivityCategory, ...] = (
    "VIDEO", "ANALYSIS", "DETECTION", "TRACKING", "REASONING", "AGENT",
    "WORKFLOW", "OPERATIONS", "REVIEW", "SYSTEM", "SECURITY",
)
_SEVERITIES: tuple[ActivitySeverity, ...] = ("INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")
_STATUSES: tuple[ActivityStatus, ...] = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED")


def _row(
    *, id, category, event_type, title, description, severity, status, source,
    timestamp, video_id=None, analysis_job_id=None, workflow_id=None,
    agent_id=None, operational_event_id=None, duration_ms=None, metadata=None,
    error_type=None,
):
    def labeled(value, name):
        expression = value if hasattr(value, "label") else literal(value)
        return expression.label(name)

    return select(
        labeled(id, "id"), labeled(category, "category"), labeled(event_type, "event_type"),
        labeled(title, "title"), labeled(description, "description"), labeled(severity, "severity"),
        labeled(status, "status"), labeled(source, "source"), labeled(video_id, "video_id"),
        labeled(analysis_job_id, "analysis_job_id"), labeled(workflow_id, "workflow_id"),
        labeled(agent_id, "agent_id"), labeled(operational_event_id, "operational_event_id"),
        labeled(timestamp, "timestamp"), cast(literal(duration_ms) if duration_ms is None else duration_ms, String).label("duration_ms"),
        cast(literal(metadata) if metadata is None else metadata, String).label("metadata_json"),
        labeled(error_type, "error_type"),
    )


def _feed_query():
    projections = [
        _row(
            id=ActivityLog.id, category=ActivityLog.category, event_type=ActivityLog.event_type,
            title=ActivityLog.title, description=ActivityLog.description, severity=ActivityLog.severity,
            status=ActivityLog.status, source=ActivityLog.source, timestamp=ActivityLog.created_at,
            video_id=ActivityLog.video_id, analysis_job_id=ActivityLog.analysis_job_id,
            workflow_id=ActivityLog.workflow_id, agent_id=ActivityLog.agent_id,
            operational_event_id=ActivityLog.operational_event_id, duration_ms=ActivityLog.duration_ms,
            metadata=ActivityLog.metadata_json, error_type=ActivityLog.error_type,
        )
    ]

    projections.append(_row(
        id=literal("video-upload:") + Video.id, category=literal("VIDEO"),
        event_type=literal("video.uploaded"), title=literal("Video uploaded"),
        description=literal("A video was persisted in local storage."), severity=literal("SUCCESS"), status=literal("COMPLETED"),
        source=literal("video_upload"), timestamp=Video.created_at, video_id=Video.id,
        metadata=func.json_object(literal("file_size_bytes"), Video.file_size,
                                 literal("content_type"), Video.content_type),
    ))

    projections.append(_row(
        id=literal("analysis-request:") + AnalysisJob.job_id, category=literal("ANALYSIS"),
        event_type=literal("analysis.requested"), title=literal("Video analysis requested"),
        description=literal("An analysis job was accepted by the local backend."),
        severity=literal("INFO"), status=literal("PENDING"), source=literal("analysis_jobs"),
        timestamp=AnalysisJob.created_at, video_id=AnalysisJob.video_id,
        analysis_job_id=AnalysisJob.job_id,
        metadata=func.json_object(literal("sampling_rate_fps"), AnalysisJob.sampling_rate_fps,
                                 literal("confidence_threshold"), AnalysisJob.confidence_threshold,
                                 literal("tracking_enabled"), AnalysisJob.tracking_enabled),
    ))

    projections.append(_row(
        id=literal("analysis-start:") + AnalysisJob.job_id, category=literal("ANALYSIS"),
        event_type=literal("analysis.started"), title=literal("Video analysis started"),
        description=literal("The analysis worker began processing this job."),
        severity=literal("INFO"), status=literal("RUNNING"), source=literal("analysis_worker"),
        timestamp=AnalysisJob.started_at, video_id=AnalysisJob.video_id,
        analysis_job_id=AnalysisJob.job_id,
        metadata=func.json_object(literal("inference_device"), AnalysisJob.inference_device),
    ).where(AnalysisJob.started_at.is_not(None)))

    terminal_status = case((AnalysisJob.status == "completed", "COMPLETED"), else_="FAILED")
    terminal_severity = case((AnalysisJob.status == "completed", "SUCCESS"), else_="ERROR")
    terminal_description = case(
        (AnalysisJob.status == "completed", "Analysis completed and its persisted results are available."),
        else_="Analysis failed. Open the analysis job for its safe error summary.",
    )
    projections.append(_row(
        id=literal("analysis-terminal:") + AnalysisJob.job_id, category=literal("ANALYSIS"),
        event_type=case((AnalysisJob.status == "completed", "analysis.completed"), else_="analysis.failed"),
        title=case((AnalysisJob.status == "completed", "Video analysis completed"), else_="Video analysis failed"),
        description=terminal_description, severity=terminal_severity, status=terminal_status,
        source=literal("analysis_jobs"), timestamp=AnalysisJob.completed_at,
        video_id=AnalysisJob.video_id, analysis_job_id=AnalysisJob.job_id,
        duration_ms=case((AnalysisJob.started_at.is_not(None),
                          (func.julianday(AnalysisJob.completed_at) - func.julianday(AnalysisJob.started_at)) * 86400000),
                         else_=None),
        metadata=func.json_object(literal("frames_processed"), AnalysisJob.frames_processed,
                                 literal("frames_sampled"), AnalysisJob.frames_sampled,
                                 literal("detections_count"), AnalysisJob.detections_count,
                                 literal("inference_device"), AnalysisJob.inference_device),
        error_type=case((AnalysisJob.status == "failed", literal("analysis_failed")), else_=literal(None)),
    ).where(AnalysisJob.completed_at.is_not(None), AnalysisJob.status.in_(("completed", "failed"))))

    projections.append(_row(
        id=literal("ai-report:") + AIReport.report_id, category=literal("REASONING"),
        event_type=literal("reasoning.report.persisted"), title=literal("AI report saved"),
        description=literal("A validated local reasoning report was persisted."),
        severity=literal("SUCCESS"), status=literal("COMPLETED"), source=literal("ai_reports"),
        timestamp=AIReport.generated_at, analysis_job_id=AIReport.job_id,
        metadata=func.json_object(literal("model"), AIReport.model_name,
                                 literal("observation_count"), func.json_array_length(AIReport.observations),
                                 literal("recommendation_count"), func.json_array_length(AIReport.recommendations)),
    ))

    workflow_status = case(
        (AgentWorkflow.status == "completed", "COMPLETED"),
        (AgentWorkflow.status == "failed", "FAILED"),
        (AgentWorkflow.status == "cancelled", "CANCELLED"),
        (AgentWorkflow.status == "pending", "PENDING"),
        else_="RUNNING",
    )
    projections.append(_row(
        id=literal("workflow-created:") + AgentWorkflow.workflow_id, category=literal("WORKFLOW"),
        event_type=literal("workflow.created"), title=literal("Agent workflow created"),
        description=literal("A LangGraph workflow was created for a completed analysis."),
        severity=literal("INFO"), status=literal("PENDING"), source=literal("agent_workflows"),
        timestamp=AgentWorkflow.created_at, analysis_job_id=AgentWorkflow.job_id,
        workflow_id=AgentWorkflow.workflow_id,
    ))
    projections.append(_row(
        id=literal("workflow-started:") + AgentWorkflow.workflow_id, category=literal("WORKFLOW"),
        event_type=literal("workflow.started"), title=literal("Agent workflow started"),
        description=literal("The persisted LangGraph workflow began execution."),
        severity=literal("INFO"), status=literal("RUNNING"), source=literal("agent_workflows"),
        timestamp=AgentWorkflow.started_at, analysis_job_id=AgentWorkflow.job_id,
        workflow_id=AgentWorkflow.workflow_id,
    ).where(AgentWorkflow.started_at.is_not(None)))
    projections.append(_row(
        id=literal("workflow-terminal:") + AgentWorkflow.workflow_id, category=literal("WORKFLOW"),
        event_type=literal("workflow.") + AgentWorkflow.status, title=literal("Agent workflow ") + AgentWorkflow.status,
        description=literal("Workflow status recorded by the persisted LangGraph execution."),
        severity=case((AgentWorkflow.status == "failed", "ERROR"), (AgentWorkflow.status == "cancelled", "WARNING"), else_="SUCCESS"),
        status=workflow_status, source=literal("agent_workflows"),
        timestamp=func.coalesce(AgentWorkflow.completed_at, AgentWorkflow.started_at, AgentWorkflow.created_at),
        analysis_job_id=AgentWorkflow.job_id, workflow_id=AgentWorkflow.workflow_id,
        metadata=func.json_object(literal("current_agent"), AgentWorkflow.current_agent),
    ).where(AgentWorkflow.status.in_(("completed", "failed", "cancelled"))))

    projections.append(_row(
        id=literal("agent-start:") + AgentExecution.execution_id, category=literal("AGENT"),
        event_type=literal("agent.execution.started"), title=literal("Agent execution started"),
        description=AgentExecution.current_task, severity=literal("INFO"), status=literal("RUNNING"),
        source=literal("agent_executions"), timestamp=AgentExecution.started_at,
        analysis_job_id=AgentExecution.job_id, workflow_id=AgentExecution.workflow_id,
        agent_id=AgentExecution.agent_id,
    ).where(AgentExecution.started_at.is_not(None)))
    agent_status = case(
        (AgentExecution.status == "completed", "COMPLETED"),
        (AgentExecution.status == "failed", "FAILED"),
        (AgentExecution.status == "skipped", "SKIPPED"),
        else_="RUNNING",
    )
    projections.append(_row(
        id=literal("agent-terminal:") + AgentExecution.execution_id, category=literal("AGENT"),
        event_type=literal("agent.execution.") + AgentExecution.status,
        title=literal("Agent execution ") + AgentExecution.status,
        description=func.coalesce(AgentExecution.current_task, literal("Agent execution status recorded.")),
        severity=case((AgentExecution.status == "failed", "ERROR"),
                      (AgentExecution.status == "skipped", "WARNING"), else_="SUCCESS"),
        status=agent_status, source=literal("agent_executions"), timestamp=AgentExecution.completed_at,
        analysis_job_id=AgentExecution.job_id, workflow_id=AgentExecution.workflow_id,
        agent_id=AgentExecution.agent_id, duration_ms=AgentExecution.duration_ms,
        error_type=case((AgentExecution.status == "failed", literal("agent_execution_failed")), else_=literal(None)),
    ).where(AgentExecution.completed_at.is_not(None), AgentExecution.status.in_(("completed", "failed", "skipped"))))

    operation_severity = case(
        (OperationalEvent.severity.in_(("high", "medium")), "WARNING"),
        (OperationalEvent.severity == "low", "INFO"), else_="INFO",
    )
    projections.append(_row(
        id=literal("operational-event:") + OperationalEvent.event_id, category=literal("OPERATIONS"),
        event_type=literal("operations.event.generated"), title=OperationalEvent.title,
        description=OperationalEvent.description, severity=operation_severity,
        status=literal("COMPLETED"), source=literal("operational_events"),
        timestamp=OperationalEvent.created_at, video_id=OperationalEvent.video_id,
        analysis_job_id=OperationalEvent.job_id, operational_event_id=OperationalEvent.event_id,
        metadata=func.json_object(literal("event_type"), OperationalEvent.event_type,
                                 literal("source_severity"), OperationalEvent.severity,
                                 literal("rule"), OperationalEvent.rule_basis["rule"]),
    ))
    projections.append(_row(
        id=literal("operational-review:") + OperationalReview.review_id, category=literal("REVIEW"),
        event_type=literal("operations.event.") + OperationalReview.status,
        title=case((OperationalReview.status == "reviewed", "Operational event reviewed"),
                   else_="Operational event dismissed"),
        description=literal("A human review decision was appended to the operational event."),
        severity=literal("INFO"), status=literal("COMPLETED"), source=literal("operational_reviews"),
        timestamp=OperationalReview.reviewed_at,
        operational_event_id=OperationalReview.event_id,
    ))

    return union_all(*projections).subquery("activity_feed")


def _filtered_query(query, filters: ActivityLogQuery):
    feed = query
    statement = select(feed)
    if filters.category:
        statement = statement.where(feed.c.category == filters.category)
    if filters.event_type:
        statement = statement.where(feed.c.event_type == filters.event_type)
    if filters.severity:
        statement = statement.where(feed.c.severity == filters.severity)
    if filters.status:
        statement = statement.where(feed.c.status == filters.status)
    if filters.source:
        statement = statement.where(feed.c.source == filters.source)
    if filters.date_from:
        value = filters.date_from.astimezone(timezone.utc).replace(tzinfo=None) if filters.date_from.tzinfo else filters.date_from
        statement = statement.where(feed.c.timestamp >= value)
    if filters.date_to:
        value = filters.date_to.astimezone(timezone.utc).replace(tzinfo=None) if filters.date_to.tzinfo else filters.date_to
        statement = statement.where(feed.c.timestamp <= value)
    for name in ("video_id", "analysis_job_id", "workflow_id", "agent_id"):
        value = getattr(filters, name)
        if value:
            statement = statement.where(getattr(feed.c, name) == value)
    if filters.search:
        needle = f"%{filters.search.strip()}%"
        statement = statement.where(feed.c.title.ilike(needle) | feed.c.description.ilike(needle))
    return statement


def _read(row) -> ActivityLogRead:
    metadata: Any = row.metadata_json
    try:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    timestamp = row.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return ActivityLogRead(
        id=row.id, category=row.category, event_type=row.event_type, title=row.title,
        description=row.description or "", severity=row.severity, status=row.status,
        source=row.source, video_id=row.video_id, analysis_job_id=row.analysis_job_id,
        workflow_id=row.workflow_id, agent_id=row.agent_id,
        operational_event_id=row.operational_event_id, timestamp=timestamp,
        duration_ms=float(row.duration_ms) if row.duration_ms is not None else None,
        metadata=metadata, error_type=row.error_type,
    )


def list_activity(session: Session, filters: ActivityLogQuery, limit: int, offset: int) -> ActivityLogListRead:
    query = _filtered_query(_feed_query(), filters)
    feed = query.subquery()
    total = int(session.scalar(select(func.count()).select_from(feed)) or 0)
    rows = session.execute(select(feed).order_by(feed.c.timestamp.desc(), feed.c.id.desc()).offset(offset).limit(limit)).all()
    return ActivityLogListRead(items=[_read(row) for row in rows], total=total, limit=limit, offset=offset)


def get_activity(session: Session, event_id: str) -> ActivityLogRead | None:
    feed = _feed_query()
    row = session.execute(select(feed).where(feed.c.id == event_id)).first()
    return _read(row) if row else None


def summarize_activity(session: Session, filters: ActivityLogQuery) -> ActivitySummaryRead:
    feed = _filtered_query(_feed_query(), filters).subquery()
    recent_since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    result = session.execute(select(
        func.count().label("total"),
        func.sum(case((feed.c.status == "COMPLETED", 1), else_=0)).label("success"),
        func.sum(case((feed.c.status == "FAILED", 1), else_=0)).label("failed"),
        func.sum(case((feed.c.severity == "WARNING", 1), else_=0)).label("warning"),
        func.sum(case((feed.c.timestamp >= recent_since, 1), else_=0)).label("recent"),
    ).select_from(feed)).one()
    return ActivitySummaryRead(total_events=int(result.total or 0), successful_executions=int(result.success or 0),
                               failed_executions=int(result.failed or 0), warning_events=int(result.warning or 0),
                               recent_events=int(result.recent or 0))


def get_activity_filters(session: Session) -> ActivityFiltersRead:
    feed = _feed_query()
    event_types = session.scalars(select(feed.c.event_type).distinct().order_by(feed.c.event_type)).all()
    sources = session.scalars(select(feed.c.source).distinct().order_by(feed.c.source)).all()
    return ActivityFiltersRead(categories=list(_CATEGORIES), event_types=list(event_types),
                                severities=list(_SEVERITIES), statuses=list(_STATUSES), sources=list(sources))


def export_activity(session: Session, filters: ActivityLogQuery, limit: int = 5000) -> list[ActivityLogRead]:
    feed = _filtered_query(_feed_query(), filters).subquery()
    rows = session.execute(select(feed).order_by(feed.c.timestamp.desc(), feed.c.id.desc()).limit(limit)).all()
    return [_read(row) for row in rows]
