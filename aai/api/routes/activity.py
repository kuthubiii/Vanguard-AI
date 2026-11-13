"""Search, inspect, summarize, and export real Vanguard activity records."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aai.db.session import get_db
from aai.schemas.activity import (
    ActivityCategory, ActivityFiltersRead, ActivityLogListRead, ActivityLogQuery,
    ActivityLogRead, ActivitySeverity, ActivityStatus, ActivitySummaryRead,
)
from aai.services.activity_queries import (
    export_activity, get_activity, get_activity_filters, list_activity, summarize_activity,
)

router = APIRouter(prefix="/activity-logs", tags=["activity logs"])
logger = logging.getLogger(__name__)


def _filters(
    category: ActivityCategory | None = None, event_type: str | None = Query(default=None, max_length=96),
    severity: ActivitySeverity | None = None, status: ActivityStatus | None = None,
    source: str | None = Query(default=None, max_length=64),
    date_from: datetime | None = None, date_to: datetime | None = None,
    video_id: str | None = Query(default=None, max_length=36),
    analysis_job_id: str | None = Query(default=None, max_length=36),
    workflow_id: str | None = Query(default=None, max_length=36),
    agent_id: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200),
) -> ActivityLogQuery:
    if date_from and date_from.tzinfo is not None:
        date_from = date_from.astimezone(timezone.utc).replace(tzinfo=None)
    if date_to and date_to.tzinfo is not None:
        date_to = date_to.astimezone(timezone.utc).replace(tzinfo=None)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, detail={"code": "invalid_date_range", "message": "date_from must be earlier than or equal to date_to."})
    return ActivityLogQuery(
        category=category, event_type=event_type, severity=severity, status=status, source=source,
        date_from=date_from, date_to=date_to, video_id=video_id,
        analysis_job_id=analysis_job_id, workflow_id=workflow_id, agent_id=agent_id, search=search,
    )


def _safe_error(stage: str, error: SQLAlchemyError) -> HTTPException:
    logger.exception("ACTIVITY_QUERY_FAILED stage=%s exception_class=%s", stage, type(error).__name__)
    return HTTPException(500, detail={"code": "activity_query_failed", "message": "Activity records could not be loaded."})


def _csv_cell(value: str) -> str:
    # Prefix potentially executable spreadsheet cells, including leading whitespace.
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def _csv_response(items: list[ActivityLogRead]) -> StreamingResponse:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("id", "category", "event_type", "title", "description", "severity", "status",
                     "source", "timestamp", "duration_ms", "video_id", "analysis_job_id", "workflow_id",
                     "agent_id", "operational_event_id", "error_type", "metadata"))
    for item in items:
        values = (
            item.id, item.category, item.event_type, item.title, item.description, item.severity,
            item.status, item.source, item.timestamp.isoformat(), "" if item.duration_ms is None else str(item.duration_ms),
            item.video_id or "", item.analysis_job_id or "", item.workflow_id or "", item.agent_id or "",
            item.operational_event_id or "", item.error_type or "", json.dumps(item.metadata, ensure_ascii=False),
        )
        writer.writerow([_csv_cell(str(value)) for value in values])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vanguard-activity.csv"',
                 "X-Export-Limit": "5000"},
    )


@router.get("/summary", response_model=ActivitySummaryRead)
def activity_summary(
    category: ActivityCategory | None = None, event_type: str | None = Query(default=None, max_length=96),
    severity: ActivitySeverity | None = None, status: ActivityStatus | None = None,
    source: str | None = Query(default=None, max_length=64), date_from: datetime | None = None,
    date_to: datetime | None = None, video_id: str | None = Query(default=None, max_length=36),
    analysis_job_id: str | None = Query(default=None, max_length=36),
    workflow_id: str | None = Query(default=None, max_length=36), agent_id: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200), db: Session = Depends(get_db),
) -> ActivitySummaryRead:
    filters = _filters(category, event_type, severity, status, source, date_from, date_to,
                       video_id, analysis_job_id, workflow_id, agent_id, search)
    try:
        return summarize_activity(db, filters)
    except SQLAlchemyError as error:
        raise _safe_error("summary", error) from error


@router.get("/filters", response_model=ActivityFiltersRead)
def activity_filters(db: Session = Depends(get_db)) -> ActivityFiltersRead:
    try:
        return get_activity_filters(db)
    except SQLAlchemyError as error:
        raise _safe_error("filters", error) from error


@router.get("/export")
def activity_export(
    category: ActivityCategory | None = None, event_type: str | None = Query(default=None, max_length=96),
    severity: ActivitySeverity | None = None, status: ActivityStatus | None = None,
    source: str | None = Query(default=None, max_length=64), date_from: datetime | None = None,
    date_to: datetime | None = None, video_id: str | None = Query(default=None, max_length=36),
    analysis_job_id: str | None = Query(default=None, max_length=36),
    workflow_id: str | None = Query(default=None, max_length=36), agent_id: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200), db: Session = Depends(get_db),
) -> StreamingResponse:
    filters = _filters(category, event_type, severity, status, source, date_from, date_to,
                       video_id, analysis_job_id, workflow_id, agent_id, search)
    try:
        items = export_activity(db, filters, limit=5000)
        return _csv_response(items)
    except SQLAlchemyError as error:
        raise _safe_error("export", error) from error


@router.get("", response_model=ActivityLogListRead)
def activity_list(
    category: ActivityCategory | None = None, event_type: str | None = Query(default=None, max_length=96),
    severity: ActivitySeverity | None = None, status: ActivityStatus | None = None,
    source: str | None = Query(default=None, max_length=64), date_from: datetime | None = None,
    date_to: datetime | None = None, video_id: str | None = Query(default=None, max_length=36),
    analysis_job_id: str | None = Query(default=None, max_length=36),
    workflow_id: str | None = Query(default=None, max_length=36), agent_id: str | None = Query(default=None, max_length=64),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ActivityLogListRead:
    filters = _filters(category, event_type, severity, status, source, date_from, date_to,
                       video_id, analysis_job_id, workflow_id, agent_id, search)
    try:
        return list_activity(db, filters, limit, offset)
    except SQLAlchemyError as error:
        raise _safe_error("list", error) from error


@router.get("/{event_id}", response_model=ActivityLogRead)
def activity_detail(event_id: str, db: Session = Depends(get_db)) -> ActivityLogRead:
    try:
        item = get_activity(db, event_id)
    except SQLAlchemyError as error:
        raise _safe_error("detail", error) from error
    if item is None:
        raise HTTPException(404, detail={"code": "activity_not_found", "message": "Activity record not found."})
    return item
