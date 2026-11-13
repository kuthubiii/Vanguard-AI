"""Database-aggregated warehouse analytics; no video or detector model loading."""

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from aai.db.models import AIReport, AnalysisJob, Detection, Video
from aai.schemas.operations import (
    CategoryMetric, ConfidenceBucket, JobComparison, MetricSummary, OperationalAnalyticsRead,
    OperationalFilters as OperationalFiltersSchema, OperationalOverviewRead, RecentAnalysis,
    TrackActivity, TrendPoint,
)
from aai.services.operational_queries import (
    OperationalFilters, completed_job_conditions,
)


def _job_scope(filters: OperationalFilters):
    return select(
        AnalysisJob.job_id.label("job_id"), AnalysisJob.video_id.label("video_id"),
        AnalysisJob.created_at.label("created_at"),
    ).where(*completed_job_conditions(filters)).subquery()


def _metrics_from_scope(session: Session, filters: OperationalFilters) -> tuple[int, int, int, int, list[str], int]:
    jobs = _job_scope(filters)
    job_metrics = session.execute(select(
        func.count(distinct(jobs.c.job_id)), func.count(distinct(jobs.c.video_id))
    )).one()
    total_jobs, total_videos = int(job_metrics[0] or 0), int(job_metrics[1] or 0)
    detection_join = Detection.__table__.join(jobs, jobs.c.job_id == Detection.job_id)
    total_detections = int(session.scalar(select(func.count()).select_from(detection_join)) or 0)
    tracks = select(Detection.job_id, Detection.track_id).join(
        jobs, jobs.c.job_id == Detection.job_id
    ).where(Detection.track_id.is_not(None)).distinct().subquery()
    unique_tracks = int(session.scalar(select(func.count()).select_from(tracks)) or 0)
    categories = session.scalars(
        select(Detection.class_name).join(jobs, jobs.c.job_id == Detection.job_id)
        .where(Detection.track_id.is_not(None)).distinct().order_by(Detection.class_name)
    ).all()
    reports = int(session.scalar(
        select(func.count()).select_from(AIReport).join(jobs, jobs.c.job_id == AIReport.job_id)
    ) or 0)
    return total_jobs, total_videos, total_detections, unique_tracks, list(categories), reports


def _category_distribution(session: Session, filters: OperationalFilters) -> list[CategoryMetric]:
    jobs = _job_scope(filters)
    rows = session.execute(
        select(Detection.class_id, Detection.class_name, func.count().label("detection_count"))
        .join(jobs, jobs.c.job_id == Detection.job_id)
        .group_by(Detection.class_id, Detection.class_name)
        .order_by(func.count().desc(), Detection.class_id)
    ).all()
    total = sum(int(row.detection_count) for row in rows)
    track_pairs = select(Detection.class_id, Detection.class_name, Detection.job_id, Detection.track_id)\
        .join(jobs, jobs.c.job_id == Detection.job_id).where(Detection.track_id.is_not(None)).distinct().subquery()
    track_counts = session.execute(
        select(track_pairs.c.class_id, track_pairs.c.class_name, func.count().label("count"))
        .group_by(track_pairs.c.class_id, track_pairs.c.class_name)
    ).all()
    tracks_by_class = {(int(row.class_id), row.class_name): int(row.count) for row in track_counts}
    return [CategoryMetric(
        class_id=int(row.class_id), class_name=row.class_name, detection_count=int(row.detection_count),
        percent_of_detections=round(int(row.detection_count) * 100 / total, 2) if total else 0.0,
        unique_tracked_entities=tracks_by_class.get((int(row.class_id), row.class_name), 0),
    ) for row in rows]


def _detection_trend(session: Session, filters: OperationalFilters) -> list[TrendPoint]:
    jobs = _job_scope(filters)
    bucket = func.strftime("%Y-%m-%d", jobs.c.created_at)
    rows = session.execute(
        select(bucket.label("bucket"), func.count(Detection.detection_id).label("detection_count"),
               func.sum(case((Detection.track_id.is_not(None), 1), else_=0)).label("tracked_count"))
        .select_from(Detection).join(jobs, jobs.c.job_id == Detection.job_id)
        .group_by(bucket).order_by(bucket)
    ).all()
    return [TrendPoint(bucket=row.bucket or "unknown", detection_count=int(row.detection_count or 0),
                       tracked_detection_count=int(row.tracked_count or 0)) for row in rows]


def get_overview(session: Session, filters: OperationalFilters, recent_limit: int = 8) -> OperationalOverviewRead:
    jobs, videos, detections, tracked, categories, reports = _metrics_from_scope(session, filters)
    distribution = _category_distribution(session, filters)
    trend = _detection_trend(session, filters)
    scope = _job_scope(filters)
    detection_counts = select(Detection.job_id, func.count().label("count")).group_by(Detection.job_id).subquery()
    track_counts = select(Detection.job_id, func.count(distinct(Detection.track_id)).label("count"))\
        .where(Detection.track_id.is_not(None)).group_by(Detection.job_id).subquery()
    recent_rows = session.execute(
        select(AnalysisJob, Video, func.coalesce(detection_counts.c.count, 0), func.coalesce(track_counts.c.count, 0))
        .join(scope, scope.c.job_id == AnalysisJob.job_id)
        .join(Video, Video.id == AnalysisJob.video_id)
        .outerjoin(detection_counts, detection_counts.c.job_id == AnalysisJob.job_id)
        .outerjoin(track_counts, track_counts.c.job_id == AnalysisJob.job_id)
        .order_by(AnalysisJob.created_at.desc()).limit(recent_limit)
    ).all()
    recent = [RecentAnalysis(
        analysis_job_id=job.job_id, video_id=video.id, video_filename=video.original_filename,
        status=job.status, detections_count=int(count), unique_tracked_entities=int(track_count),
        created_at=job.created_at,
    ) for job, video, count, track_count in recent_rows]
    pending = _pending_events_count(session, filters)
    return OperationalOverviewRead(
        filters=OperationalFiltersSchema(**filters.__dict__),
        metrics=MetricSummary(total_videos_analysed=videos, completed_analysis_jobs=jobs,
                              total_detected_objects=detections, total_unique_tracked_entities=tracked,
                              tracked_object_categories=categories, available_ai_reports=reports,
                              events_requiring_attention=pending),
        recent_analyses=recent, category_distribution=distribution, detection_trend=trend,
    )


def _confidence_distribution(session: Session, filters: OperationalFilters) -> list[ConfidenceBucket]:
    jobs = _job_scope(filters)
    bucket = case(
        (Detection.confidence < 0.2, "0.0-0.2"),
        (Detection.confidence < 0.4, "0.2-0.4"),
        (Detection.confidence < 0.6, "0.4-0.6"),
        (Detection.confidence < 0.8, "0.6-0.8"),
        else_="0.8-1.0",
    )
    rows = session.execute(
        select(bucket.label("bucket"), func.count().label("count"))
        .join(jobs, jobs.c.job_id == Detection.job_id).group_by(bucket)
    ).all()
    counts = {row.bucket: int(row.count) for row in rows}
    labels = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
    return [ConfidenceBucket(confidence_range=label, detection_count=counts.get(label, 0)) for label in labels]


def get_analytics(session: Session, filters: OperationalFilters, limit: int = 50, offset: int = 0) -> OperationalAnalyticsRead:
    jobs = _job_scope(filters)
    tracks_subquery = (
        select(Detection.job_id.label("job_id"), Detection.track_id.label("track_id"),
               Detection.class_id.label("class_id"), Detection.class_name.label("class_name"),
               func.count().label("observation_count"), func.min(Detection.timestamp_seconds).label("first_seen"),
               func.max(Detection.timestamp_seconds).label("last_seen"))
        .join(jobs, jobs.c.job_id == Detection.job_id)
        .where(Detection.track_id.is_not(None))
        .group_by(Detection.job_id, Detection.track_id, Detection.class_id, Detection.class_name)
    ).subquery()
    tracking_total = int(session.scalar(select(func.count()).select_from(tracks_subquery)) or 0)
    track_rows = session.execute(
        select(tracks_subquery, jobs.c.video_id, Video.original_filename)
        .join(jobs, jobs.c.job_id == tracks_subquery.c.job_id)
        .join(Video, Video.id == jobs.c.video_id)
        .order_by(tracks_subquery.c.observation_count.desc(), jobs.c.created_at.desc(), tracks_subquery.c.track_id)
        .offset(offset).limit(limit)
    ).all()
    tracking = [TrackActivity(
        analysis_job_id=row.job_id, video_id=row.video_id, video_filename=row.original_filename,
        track_id=int(row.track_id), class_id=int(row.class_id), class_name=row.class_name,
        observation_count=int(row.observation_count), first_observed_seconds=float(row.first_seen),
        last_observed_seconds=float(row.last_seen),
    ) for row in track_rows]
    detection_count_subquery = select(Detection.job_id, func.count().label("detection_count")).group_by(Detection.job_id).subquery()
    tracked_count_subquery = select(Detection.job_id, func.count(distinct(Detection.track_id)).label("unique_tracks"))\
        .where(Detection.track_id.is_not(None)).group_by(Detection.job_id).subquery()
    comparison_rows = session.execute(
        select(AnalysisJob, Video, func.coalesce(detection_count_subquery.c.detection_count, 0),
               func.coalesce(tracked_count_subquery.c.unique_tracks, 0))
        .join(jobs, jobs.c.job_id == AnalysisJob.job_id).join(Video, Video.id == AnalysisJob.video_id)
        .outerjoin(detection_count_subquery, detection_count_subquery.c.job_id == AnalysisJob.job_id)
        .outerjoin(tracked_count_subquery, tracked_count_subquery.c.job_id == AnalysisJob.job_id)
        .order_by(AnalysisJob.created_at.desc()).limit(100)
    ).all()
    comparisons = [JobComparison(
        analysis_job_id=job.job_id, video_id=video.id, video_filename=video.original_filename,
        created_at=job.created_at, detection_count=int(count), unique_tracked_entities=int(track_count),
    ) for job, video, count, track_count in comparison_rows]
    return OperationalAnalyticsRead(
        filters=OperationalFiltersSchema(**filters.__dict__), category_distribution=_category_distribution(session, filters),
        confidence_distribution=_confidence_distribution(session, filters), detection_trend=_detection_trend(session, filters),
        tracking_activity=tracking, tracking_total=tracking_total, tracking_limit=limit, tracking_offset=offset,
        job_comparisons=comparisons,
    )


def _pending_events_count(session: Session, filters: OperationalFilters) -> int:
    from aai.services.operational_events import count_events
    return count_events(session, filters, review_status="pending")
