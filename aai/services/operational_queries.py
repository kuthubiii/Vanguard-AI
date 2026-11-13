"""Shared, index-friendly filters for completed analysis records."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select

from aai.db.models import AnalysisJob, Detection, Video


@dataclass(frozen=True)
class OperationalFilters:
    analysis_job_id: str | None = None
    video_id: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


def completed_job_conditions(filters: OperationalFilters) -> list[Any]:
    conditions: list[Any] = [AnalysisJob.status == "completed"]
    if filters.analysis_job_id:
        conditions.append(AnalysisJob.job_id == filters.analysis_job_id)
    if filters.video_id:
        conditions.append(AnalysisJob.video_id == filters.video_id)
    if filters.date_from:
        conditions.append(AnalysisJob.created_at >= filters.date_from)
    if filters.date_to:
        conditions.append(AnalysisJob.created_at <= filters.date_to)
    return conditions


def completed_jobs_query(filters: OperationalFilters) -> Select:
    return (
        select(AnalysisJob, Video)
        .join(Video, Video.id == AnalysisJob.video_id)
        .where(*completed_job_conditions(filters))
    )


def with_detection_scope(query: Select, filters: OperationalFilters) -> Select:
    return query.join(AnalysisJob, AnalysisJob.job_id == Detection.job_id).where(*completed_job_conditions(filters))
