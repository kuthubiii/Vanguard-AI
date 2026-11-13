"""Read-only operational analytics and explicit human review APIs."""

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aai.db.session import get_db
from aai.schemas.operations import (
    OperationalAnalyticsRead, OperationalAvailabilityRead, OperationalEventListRead,
    OperationalEventRead, OperationalOverviewRead, OperationalRecommendationListRead,
    OperationalReviewRequest, RiskIndicatorListRead,
)
from aai.services.operational_analytics import get_analytics, get_overview
from aai.services.operational_availability import get_availability
from aai.services.operational_events import add_review, get_event, list_events, list_risks, refresh_rule_events
from aai.services.operational_queries import OperationalFilters
from aai.services.operational_recommendations import get_recommendations

router = APIRouter(prefix="/operations", tags=["operational intelligence"])
logger = logging.getLogger(__name__)


def _filters(analysis_job_id: str | None, video_id: str | None,
             date_from: datetime | None, date_to: datetime | None) -> OperationalFilters:
    def utc_naive(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    date_from = utc_naive(date_from)
    date_to = utc_naive(date_to)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, detail={"code": "invalid_date_range", "message": "date_from must be earlier than or equal to date_to."})
    return OperationalFilters(analysis_job_id=analysis_job_id, video_id=video_id,
                              date_from=date_from, date_to=date_to)


def _safe_database_error(stage: str, error: SQLAlchemyError) -> HTTPException:
    logger.exception("OPERATIONAL_INTELLIGENCE_FAILED stage=%s exception_class=%s", stage, type(error).__name__)
    return HTTPException(500, detail={"code": "operational_query_failed", "message": "Operational intelligence data could not be loaded."})


@router.get("/overview", response_model=OperationalOverviewRead)
def operations_overview(
    analysis_job_id: str | None = None, video_id: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    db: Session = Depends(get_db),
) -> OperationalOverviewRead:
    filters = _filters(analysis_job_id, video_id, date_from, date_to)
    try:
        refresh_rule_events(db, filters)
        return get_overview(db, filters)
    except SQLAlchemyError as error:
        db.rollback()
        raise _safe_database_error("overview", error) from error


@router.get("/analytics", response_model=OperationalAnalyticsRead)
def operations_analytics(
    analysis_job_id: str | None = None, video_id: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> OperationalAnalyticsRead:
    filters = _filters(analysis_job_id, video_id, date_from, date_to)
    try:
        return get_analytics(db, filters, limit, offset)
    except SQLAlchemyError as error:
        raise _safe_database_error("analytics", error) from error


@router.get("/events", response_model=OperationalEventListRead)
def operations_events(
    analysis_job_id: str | None = None, video_id: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    event_type: str | None = Query(default=None, max_length=64),
    severity: str | None = Query(default=None, pattern="^(informational|low|medium|high)$"),
    review_status: str | None = Query(default=None, pattern="^(pending|reviewed|dismissed)$"),
    limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> OperationalEventListRead:
    filters = _filters(analysis_job_id, video_id, date_from, date_to)
    try:
        refresh_rule_events(db, filters)
        return list_events(db, filters, limit=limit, offset=offset, review_status=review_status,
                           event_type=event_type, severity=severity)
    except SQLAlchemyError as error:
        db.rollback()
        raise _safe_database_error("events", error) from error


@router.get("/events/{event_id}", response_model=OperationalEventRead)
def operational_event(event_id: str, db: Session = Depends(get_db)) -> OperationalEventRead:
    event = get_event(db, event_id)
    if event is None:
        raise HTTPException(404, detail={"code": "operational_event_not_found", "message": "Operational event not found."})
    return event


@router.patch("/events/{event_id}/review", response_model=OperationalEventRead)
def review_operational_event(
    event_id: str, request: OperationalReviewRequest, db: Session = Depends(get_db),
) -> OperationalEventRead:
    event = add_review(db, event_id, request.status, request.reviewer_identifier, request.note)
    if event is None:
        raise HTTPException(404, detail={"code": "operational_event_not_found", "message": "Operational event not found."})
    # Review rows are the audit source; the central feed projects them directly.
    return event


@router.get("/risks", response_model=RiskIndicatorListRead)
def operational_risks(
    analysis_job_id: str | None = None, video_id: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    severity: str | None = Query(default=None, pattern="^(informational|low|medium|high)$"),
    review_status: str | None = Query(default=None, pattern="^(pending|reviewed|dismissed)$"),
    limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> RiskIndicatorListRead:
    filters = _filters(analysis_job_id, video_id, date_from, date_to)
    try:
        refresh_rule_events(db, filters)
        return list_risks(db, filters, limit=limit, offset=offset, severity=severity, review_status=review_status)
    except SQLAlchemyError as error:
        db.rollback()
        raise _safe_database_error("risks", error) from error


@router.get("/recommendations", response_model=OperationalRecommendationListRead)
def operational_recommendations(
    analysis_job_id: str | None = None, video_id: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=250), offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> OperationalRecommendationListRead:
    filters = _filters(analysis_job_id, video_id, date_from, date_to)
    try:
        refresh_rule_events(db, filters)
        return get_recommendations(db, filters, limit=limit, offset=offset)
    except SQLAlchemyError as error:
        db.rollback()
        raise _safe_database_error("recommendations", error) from error


@router.get("/availability", response_model=OperationalAvailabilityRead)
def operational_availability(
    analysis_job_id: str | None = None, video_id: str | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    db: Session = Depends(get_db),
) -> OperationalAvailabilityRead:
    filters = _filters(analysis_job_id, video_id, date_from, date_to)
    try:
        return get_availability(db, filters)
    except SQLAlchemyError as error:
        raise _safe_database_error("availability", error) from error
