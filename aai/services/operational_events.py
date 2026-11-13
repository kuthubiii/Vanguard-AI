"""Deterministic operational rules, persisted events, review history, and risks."""

from __future__ import annotations

import uuid
import threading
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from aai.core.config import settings
from aai.db.models import AnalysisJob, Detection, OperationalEvent, OperationalReview, Video
from aai.schemas.operations import (
    EventEvidence, OperationalEventListRead, OperationalEventRead,
    RiskIndicatorListRead, RiskIndicatorRead,
)
from aai.services.operational_queries import OperationalFilters, completed_job_conditions


EVENT_RULES = {
    "high_detection_activity": {
        "severity": "medium", "risk_category": "detection_activity_variation",
        "action": "Review the source video and compare it with same-video analyses using the same inference settings.",
    },
    "low_detection_activity": {
        "severity": "low", "risk_category": "detection_activity_variation",
        "action": "Confirm that the sampled analysis completed as expected and review the source video if the low count is unexpected.",
    },
    "category_distribution_change": {
        "severity": "low", "risk_category": "object_category_mix_change",
        "action": "Review the cited detections and verify the category change in the source video.",
    },
    "frame_observation_concentration": {
        "severity": "informational", "risk_category": "sampled_frame_observation_concentration",
        "action": "Review the cited sampled frame; this indicator does not represent occupancy or a warehouse zone.",
    },
    "repeated_track_observations": {
        "severity": "informational", "risk_category": "repeated_sampled_track_observations",
        "action": "Review the sampled track observations; they do not establish continuous physical presence.",
    },
}

_REFRESH_LOCK = threading.RLock()


def _baseline_filters(filters: OperationalFilters, video_id: str) -> OperationalFilters:
    return OperationalFilters(video_id=video_id, date_from=filters.date_from, date_to=filters.date_to)


def _matched_baseline_query(target: AnalysisJob, filters: OperationalFilters):
    return select(AnalysisJob).where(
        *_job_baseline_conditions(filters, target.video_id),
        AnalysisJob.job_id != target.job_id,
        AnalysisJob.confidence_threshold == target.confidence_threshold,
        AnalysisJob.sampling_rate_fps == target.sampling_rate_fps,
        AnalysisJob.image_size == target.image_size,
        AnalysisJob.tracking_enabled == target.tracking_enabled,
    ).order_by(AnalysisJob.created_at.desc()).limit(250)


def _job_baseline_conditions(filters: OperationalFilters, video_id: str) -> list[Any]:
    baseline = _baseline_filters(filters, video_id)
    return completed_job_conditions(baseline)


def _detection_count(session: Session, job_id: str) -> int:
    return int(session.scalar(select(func.count()).select_from(Detection).where(Detection.job_id == job_id)) or 0)


def _detection_ids(session: Session, job_id: str, *, class_id: int | None = None,
                   frame_index: int | None = None, track_id: int | None = None,
                   limit: int = 8) -> list[str]:
    query = select(Detection.detection_id).where(Detection.job_id == job_id)
    if class_id is not None:
        query = query.where(Detection.class_id == class_id)
    if frame_index is not None:
        query = query.where(Detection.frame_index == frame_index)
    if track_id is not None:
        query = query.where(Detection.track_id == track_id)
    return list(session.scalars(query.order_by(Detection.confidence.desc(), Detection.frame_index,
                                              Detection.detection_id).limit(limit)).all())


def _add_event(session: Session, *, event_key: str, job: AnalysisJob, event_type: str, severity: str,
               title: str, description: str, evidence_explanation: str, rule_basis: dict,
               detection_ids: list[str], occurred_at: datetime) -> None:
    session.execute(sqlite_insert(OperationalEvent).values(
        event_id=str(uuid.uuid4()), event_key=event_key, job_id=job.job_id, video_id=job.video_id,
        event_type=event_type, severity=severity, title=title, description=description,
        evidence_explanation=evidence_explanation, rule_basis=rule_basis,
        related_detection_ids=detection_ids, occurred_at=occurred_at,
        created_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing(index_elements=[OperationalEvent.event_key]))


def refresh_rule_events(session: Session, filters: OperationalFilters) -> int:
    # Several dashboard panels load concurrently and may request rule refreshes
    # at once. Serialize the idempotent materialization within this process.
    with _REFRESH_LOCK:
        return _refresh_rule_events(session, filters)


def _refresh_rule_events(session: Session, filters: OperationalFilters) -> int:
    """Materialize newly detected rule events without duplicating existing events.

    Baselines only compare the same video and identical inference settings. If a
    comparable baseline is too small, no high/low or category-shift event is emitted.
    """
    targets = session.scalars(
        select(AnalysisJob).where(*completed_job_conditions(filters)).order_by(AnalysisJob.created_at)
    ).all()
    before = int(session.scalar(select(func.count()).select_from(OperationalEvent)) or 0)
    min_baseline = settings.operations_min_baseline_jobs
    high_ratio = settings.operations_high_activity_ratio
    low_ratio = settings.operations_low_activity_ratio
    category_shift = settings.operations_category_share_shift
    min_class = settings.operations_category_min_detections

    for job in targets:
        current_count = _detection_count(session, job.job_id)
        peers = session.scalars(_matched_baseline_query(job, filters)).all()
        if len(peers) >= min_baseline:
            peer_ids = [peer.job_id for peer in peers]
            peer_counts = [int(row.count) for row in session.execute(
                select(AnalysisJob.job_id, func.count(Detection.detection_id).label("count"))
                .outerjoin(Detection, Detection.job_id == AnalysisJob.job_id)
                .where(AnalysisJob.job_id.in_(peer_ids)).group_by(AnalysisJob.job_id)
            ).all()]
            baseline = median(peer_counts)
            if baseline > 0 and current_count >= settings.operations_category_min_detections:
                if current_count >= baseline * high_ratio:
                    ratio = round(current_count / baseline, 3)
                    severity = "high" if ratio >= high_ratio * 2 else "medium"
                    ids = _detection_ids(session, job.job_id, limit=8)
                    _add_event(session, event_key=f"high_detection_activity:v1:{job.job_id}", job=job,
                               event_type="high_detection_activity", severity=severity,
                               title="Detection activity is above comparable analyses",
                               description=f"This completed analysis contains {current_count} persisted detections; comparable analyses of the same video and inference settings had a median of {baseline:.1f}.",
                               evidence_explanation="The comparison uses persisted detection rows and excludes the current analysis from its same-video, same-settings baseline.",
                               rule_basis={"rule": "high_detection_activity_v1", "threshold_ratio": high_ratio,
                                           "observed_count": current_count, "baseline_median": round(baseline, 3),
                                           "baseline_job_count": len(peers), "comparison_scope": "same_video_same_inference_settings"},
                               detection_ids=ids, occurred_at=job.created_at)
                elif current_count <= baseline * low_ratio:
                    ids = _detection_ids(session, job.job_id, limit=8)
                    _add_event(session, event_key=f"low_detection_activity:v1:{job.job_id}", job=job,
                               event_type="low_detection_activity", severity="low",
                               title="Detection activity is below comparable analyses",
                               description=f"This completed analysis contains {current_count} persisted detections; comparable analyses of the same video and inference settings had a median of {baseline:.1f}.",
                               evidence_explanation="The comparison uses persisted detection rows and excludes the current analysis from its same-video, same-settings baseline.",
                               rule_basis={"rule": "low_detection_activity_v1", "threshold_ratio": low_ratio,
                                           "observed_count": current_count, "baseline_median": round(baseline, 3),
                                           "baseline_job_count": len(peers), "comparison_scope": "same_video_same_inference_settings"},
                               detection_ids=ids, occurred_at=job.created_at)

            if len(peers) >= min_baseline and current_count > 0:
                peer_totals = {row.job_id: int(row.count) for row in session.execute(
                    select(Detection.job_id, func.count().label("count"))
                    .where(Detection.job_id.in_(peer_ids)).group_by(Detection.job_id)
                ).all()}
                peer_class_rows = session.execute(
                    select(Detection.job_id, Detection.class_id, Detection.class_name, func.count().label("count"))
                    .where(Detection.job_id.in_(peer_ids)).group_by(
                        Detection.job_id, Detection.class_id, Detection.class_name
                    )
                ).all()
                class_shares: dict[tuple[int, str], dict[str, float]] = {}
                for peer_row in peer_class_rows:
                    total = peer_totals.get(peer_row.job_id, 0)
                    if total:
                        class_shares.setdefault((int(peer_row.class_id), peer_row.class_name), {})[peer_row.job_id] = int(peer_row.count) / total
                current_class_rows = session.execute(
                    select(Detection.class_id, Detection.class_name, func.count().label("count"))
                    .where(Detection.job_id == job.job_id).group_by(Detection.class_id, Detection.class_name)
                ).all()
                for row in current_class_rows:
                    observed = int(row.count)
                    if observed < min_class:
                        continue
                    key = (int(row.class_id), row.class_name)
                    observed_share = observed / current_count
                    peer_shares = [class_shares.get(key, {}).get(peer_id, 0.0) for peer_id in peer_ids]
                    baseline_share = median(peer_shares)
                    difference = abs(observed_share - baseline_share)
                    if difference < category_shift:
                        continue
                    ids = _detection_ids(session, job.job_id, class_id=int(row.class_id), limit=8)
                    _add_event(session, event_key=f"category_distribution_change:v1:{job.job_id}:{row.class_id}",
                               job=job, event_type="category_distribution_change", severity="low",
                               title=f"Observed share for {row.class_name} differs from comparable analyses",
                               description=f"{row.class_name} accounts for {observed_share:.1%} of detections in this analysis versus {baseline_share:.1%} across comparable analyses.",
                               evidence_explanation="Shares use persisted detections, and the baseline is limited to other completed analyses of the same video with identical inference settings.",
                               rule_basis={"rule": "category_distribution_change_v1", "minimum_share_difference": category_shift,
                                           "observed_class_count": observed, "observed_share": round(observed_share, 5),
                                           "baseline_share": round(baseline_share, 5), "baseline_job_count": len(peers),
                                           "class_id": int(row.class_id), "class_name": row.class_name},
                               detection_ids=ids, occurred_at=job.created_at)

        frame = session.execute(
            select(Detection.frame_index, func.min(Detection.timestamp_seconds).label("timestamp"),
                   func.count().label("count"))
            .where(Detection.job_id == job.job_id)
            .group_by(Detection.frame_index)
            .order_by(func.count().desc(), Detection.frame_index)
            .limit(1)
        ).first()
        frame_threshold = settings.operations_frame_concentration_min
        if frame and int(frame.count) >= frame_threshold:
            ids = _detection_ids(session, job.job_id, frame_index=int(frame.frame_index), limit=10)
            timestamp = float(frame.timestamp or 0.0)
            _add_event(session, event_key=f"frame_observation_concentration:v1:{job.job_id}:{frame.frame_index}",
                       job=job, event_type="frame_observation_concentration", severity="informational",
                       title="Multiple detector observations occur in one sampled frame",
                       description=f"{int(frame.count)} persisted detections were recorded in sampled frame {int(frame.frame_index)} at {timestamp:.2f} seconds.",
                       evidence_explanation="This is a detector-observation count from one sampled frame. It is not a warehouse occupancy estimate, location, zone, or confirmed incident.",
                       rule_basis={"rule": "frame_observation_concentration_v1", "minimum_detections_in_frame": frame_threshold,
                                   "observed_detection_count": int(frame.count), "frame_index": int(frame.frame_index),
                                   "timestamp_seconds": timestamp},
                       detection_ids=ids, occurred_at=_event_time(job.created_at, timestamp))

        repeated_threshold = settings.operations_repeated_track_min_observations
        repeated_tracks = session.execute(
            select(Detection.track_id, Detection.class_id, Detection.class_name,
                   func.count().label("count"), func.min(Detection.timestamp_seconds).label("first_seen"),
                   func.max(Detection.timestamp_seconds).label("last_seen"))
            .where(Detection.job_id == job.job_id, Detection.track_id.is_not(None))
            .group_by(Detection.track_id, Detection.class_id, Detection.class_name)
            .having(func.count() >= repeated_threshold)
            .order_by(func.count().desc(), Detection.track_id)
            .limit(20)
        ).all()
        for track in repeated_tracks:
            track_id = int(track.track_id)
            ids = _detection_ids(session, job.job_id, track_id=track_id, limit=8)
            _add_event(session, event_key=f"repeated_track_observations:v1:{job.job_id}:{track_id}",
                       job=job, event_type="repeated_track_observations", severity="informational",
                       title=f"Track {track_id} has repeated sampled observations",
                       description=f"The tracker associated {int(track.count)} persisted sampled detections with track {track_id} ({track.class_name}).",
                       evidence_explanation="Track IDs are scoped to this analysis job. Sparse sampled observations do not establish continuous physical presence or movement.",
                       rule_basis={"rule": "repeated_track_observations_v1", "minimum_observations": repeated_threshold,
                                   "observed_observations": int(track.count), "track_id": track_id,
                                   "first_observed_seconds": float(track.first_seen), "last_observed_seconds": float(track.last_seen),
                                   "timestamp_seconds": float(track.last_seen), "class_name": track.class_name},
                       detection_ids=ids, occurred_at=_event_time(job.created_at, float(track.last_seen)))

    session.commit()
    after = int(session.scalar(select(func.count()).select_from(OperationalEvent)) or 0)
    return max(0, after - before)


def _event_time(job_created_at: datetime, seconds: float) -> datetime:
    created = job_created_at.replace(tzinfo=timezone.utc) if job_created_at.tzinfo is None else job_created_at
    return created + timedelta(seconds=max(0.0, seconds))


def _latest_review_query():
    return select(
        OperationalReview.event_id.label("event_id"), OperationalReview.status.label("status"),
        OperationalReview.reviewer_identifier.label("reviewer_identifier"),
        OperationalReview.note.label("note"), OperationalReview.reviewed_at.label("reviewed_at"),
        func.row_number().over(partition_by=OperationalReview.event_id,
                               order_by=(OperationalReview.reviewed_at.desc(), OperationalReview.review_id.desc())).label("rank"),
    ).subquery()


def _event_query(filters: OperationalFilters, *, review_status: str | None = None,
                 event_type: str | None = None, severity: str | None = None):
    latest = _latest_review_query()
    status_expr = func.coalesce(latest.c.status, "pending")
    query = select(OperationalEvent, AnalysisJob, Video, latest.c.status, latest.c.reviewer_identifier,
                   latest.c.note, latest.c.reviewed_at).join(
        AnalysisJob, AnalysisJob.job_id == OperationalEvent.job_id
    ).join(Video, Video.id == OperationalEvent.video_id).outerjoin(
        latest, and_(latest.c.event_id == OperationalEvent.event_id, latest.c.rank == 1)
    ).where(*completed_job_conditions(filters))
    if review_status:
        query = query.where(status_expr == review_status)
    if event_type:
        query = query.where(OperationalEvent.event_type == event_type)
    if severity:
        query = query.where(OperationalEvent.severity == severity)
    return query


def _event_read(row) -> OperationalEventRead:
    event, job, video, review_status, reviewer, note, reviewed_at = row
    basis = event.rule_basis or {}
    return OperationalEventRead(
        event_id=event.event_id, event_type=event.event_type, severity=event.severity,
        title=event.title, description=event.description, analysis_job_id=job.job_id,
        video_id=video.id, event_timestamp=_event_time(event.occurred_at, 0),
        evidence_explanation=event.evidence_explanation, rule_basis=basis,
        evidence=EventEvidence(detection_ids=event.related_detection_ids or [],
                               timestamp_seconds=basis.get("timestamp_seconds"),
                               class_name=basis.get("class_name")),
        review_status=review_status or "pending", reviewed_at=reviewed_at,
        reviewer_identifier=reviewer, review_note=note,
    )


def count_events(session: Session, filters: OperationalFilters, review_status: str | None = None,
                 event_type: str | None = None, severity: str | None = None) -> int:
    query = _event_query(filters, review_status=review_status, event_type=event_type, severity=severity)
    return int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)


def list_events(session: Session, filters: OperationalFilters, *, limit: int, offset: int,
                review_status: str | None = None, event_type: str | None = None,
                severity: str | None = None) -> OperationalEventListRead:
    query = _event_query(filters, review_status=review_status, event_type=event_type, severity=severity)
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = session.execute(query.order_by(OperationalEvent.occurred_at.desc(), OperationalEvent.event_id)
                           .offset(offset).limit(limit)).all()
    return OperationalEventListRead(items=[_event_read(row) for row in rows], total=total, limit=limit, offset=offset)


def get_event(session: Session, event_id: str) -> OperationalEventRead | None:
    row = session.execute(_event_query(OperationalFilters()).where(OperationalEvent.event_id == event_id)).first()
    return _event_read(row) if row else None


def add_review(session: Session, event_id: str, status: str, reviewer_identifier: str | None,
               note: str | None) -> OperationalEventRead | None:
    event = session.get(OperationalEvent, event_id)
    if event is None:
        return None
    session.add(OperationalReview(
        review_id=str(uuid.uuid4()), event_id=event_id, status=status,
        reviewer_identifier=reviewer_identifier, note=note, reviewed_at=datetime.now(timezone.utc),
    ))
    session.commit()
    return get_event(session, event_id)


def list_risks(session: Session, filters: OperationalFilters, *, limit: int, offset: int,
               severity: str | None = None, review_status: str | None = None) -> RiskIndicatorListRead:
    query = _event_query(filters, review_status=review_status, severity=severity)
    recognized = list(EVENT_RULES)
    query = query.where(OperationalEvent.event_type.in_(recognized))
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = session.execute(query.order_by(
        case((OperationalEvent.severity == "high", 4), (OperationalEvent.severity == "medium", 3),
             (OperationalEvent.severity == "low", 2), else_=1).desc(),
        OperationalEvent.occurred_at.desc(),
    ).offset(offset).limit(limit)).all()
    items = []
    for row in rows:
        event_read = _event_read(row)
        rule = EVENT_RULES[event_read.event_type]
        items.append(RiskIndicatorRead(
            event_id=event_read.event_id, risk_category=rule["risk_category"], severity=event_read.severity,
            title=event_read.title, evidence_summary=event_read.evidence_explanation,
            analysis_job_id=event_read.analysis_job_id, video_id=event_read.video_id,
            related_detection_ids=event_read.evidence.detection_ids, rule_triggered=event_read.rule_basis,
            suggested_next_action=rule["action"], review_status=event_read.review_status,
        ))
    return RiskIndicatorListRead(items=items, total=total, limit=limit, offset=offset)
