"""Persistent detection-result and annotated-video storage helpers."""

import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aai.core.config import settings
from aai.db.models import AnalysisJob, Detection
from aai.schemas.analysis import DetectionRead, TrackSummaryRead, TrackingEventRead


def create_annotated_paths(job_id: str) -> tuple[Path, Path, str]:
    safe_id = str(uuid.UUID(job_id))
    root = settings.processed_video_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_id}.mp4"
    return root / f".{safe_id}.partial.mp4", root / filename, filename


def annotated_video_path(job: AnalysisJob) -> Path:
    if not job.annotated_storage_key or job.annotated_storage_key != f"{uuid.UUID(job.job_id)}.mp4":
        raise ValueError("Annotated video reference is invalid")
    root = settings.processed_video_dir.resolve()
    path = root / job.annotated_storage_key
    if path.parent.resolve() != root:
        raise ValueError("Annotated video path escaped its storage directory")
    return path


def remove_annotated_video(job_id: str) -> None:
    try:
        _partial, final_path, _key = create_annotated_paths(job_id)
        final_path.unlink(missing_ok=True)
        _partial.unlink(missing_ok=True)
    except (ValueError, OSError):
        return


def persist_detection_batch(session: Session, job_id: str, batch: list[dict]) -> None:
    if batch:
        session.add_all(Detection(**event) for event in batch)
        session.flush()
        job = session.get(AnalysisJob, job_id)
        if job is not None:
            job.detections_count += len(batch)


def get_detection_page(session: Session, job_id: str, limit: int, offset: int) -> tuple[int, list[DetectionRead]]:
    total = session.scalar(select(func.count()).select_from(Detection).where(Detection.job_id == job_id)) or 0
    events = session.scalars(
        select(Detection)
        .where(Detection.job_id == job_id)
        .order_by(Detection.frame_index, Detection.detection_id)
        .offset(offset)
        .limit(limit)
    ).all()
    return total, [DetectionRead.model_validate(event) for event in events]


def get_tracking_event_page(session: Session, job_id: str, limit: int, offset: int) -> tuple[int, list[TrackingEventRead]]:
    query = select(Detection).where(Detection.job_id == job_id, Detection.track_id.is_not(None))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    events = session.scalars(
        query.order_by(Detection.frame_index, Detection.detection_id).offset(offset).limit(limit)
    ).all()
    return total, [TrackingEventRead.model_validate(event) for event in events]


def get_track_summaries(session: Session, job_id: str) -> tuple[list[TrackSummaryRead], dict[str, int]]:
    grouped = session.execute(
        select(
            Detection.track_id,
            func.min(Detection.timestamp_seconds),
            func.max(Detection.timestamp_seconds),
            func.count(Detection.detection_id),
        )
        .where(Detection.job_id == job_id, Detection.track_id.is_not(None))
        .group_by(Detection.track_id)
        .order_by(Detection.track_id)
    ).all()
    class_groups = session.execute(
        select(
            Detection.track_id,
            Detection.class_id,
            Detection.class_name,
            func.count(Detection.detection_id).label("observations"),
        )
        .where(Detection.job_id == job_id, Detection.track_id.is_not(None))
        .group_by(Detection.track_id, Detection.class_id, Detection.class_name)
        .order_by(Detection.track_id, func.count(Detection.detection_id).desc(), Detection.class_id)
    ).all()
    representative_by_track = {}
    for track_id, class_id, class_name, _observations in class_groups:
        representative_by_track.setdefault(track_id, (class_id, class_name))

    summaries: list[TrackSummaryRead] = []
    class_counts: dict[str, int] = {}
    for track_id, first, last, observations in grouped:
        representative = representative_by_track.get(track_id)
        if representative is None:
            continue
        class_id, class_name = representative
        class_counts[class_name] = class_counts.get(class_name, 0) + 1
        summaries.append(TrackSummaryRead(
            track_id=track_id,
            class_id=class_id,
            class_name=class_name,
            first_observed_seconds=float(first),
            last_observed_seconds=float(last),
            observation_count=observations,
            observed_span_seconds=max(0.0, float(last) - float(first)) if observations >= 2 else None,
        ))
    return summaries, class_counts
