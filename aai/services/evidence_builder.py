"""Deterministic, bounded evidence preparation for local reasoning."""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from time import perf_counter

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from aai.db.models import AnalysisJob, Detection, Video

logger = logging.getLogger(__name__)

MAX_TRACKS = 50
MAX_REPRESENTATIVE_DETECTIONS = 25


def _class_quotas(counts: dict[int, int], limit: int) -> dict[int, int]:
    """Allocate a deterministic, class-diverse sample using proportional seats."""
    quotas = {class_id: 0 for class_id, count in sorted(counts.items()) if count > 0}
    target = min(max(limit, 0), sum(counts.values()))
    while sum(quotas.values()) < target:
        eligible = [class_id for class_id in quotas if quotas[class_id] < counts[class_id]]
        if not eligible:
            break
        # Give every category one seat before allocating proportional extras.
        unrepresented = [class_id for class_id in eligible if quotas[class_id] == 0]
        if unrepresented:
            chosen = max(unrepresented, key=lambda class_id: (counts[class_id], -class_id))
        else:
            chosen = max(
                eligible,
                key=lambda class_id: (counts[class_id] / (quotas[class_id] + 1), -class_id),
            )
        quotas[chosen] += 1
    return quotas


def _select_tracks(all_tracks: list[dict]) -> list[dict]:
    if len(all_tracks) <= MAX_TRACKS:
        return all_tracks

    by_class: dict[int, list[dict]] = defaultdict(list)
    for track in all_tracks:
        by_class[track["class_id"]].append(track)
    quotas = _class_quotas({key: len(value) for key, value in by_class.items()}, MAX_TRACKS)
    selected: list[dict] = []
    for class_id in sorted(by_class):
        candidates = by_class[class_id]
        quota = quotas[class_id]
        if not quota:
            continue
        # Start with the track having the most actual observations.
        first = min(candidates, key=lambda item: (-item["observation_count"],
                                                  -(item["last_observed_seconds"] - item["first_observed_seconds"]),
                                                  item["track_id"]))
        chosen = [first]
        remaining = [item for item in candidates if item["track_id"] != first["track_id"]]
        while remaining and len(chosen) < quota:
            def diversity_key(item: dict) -> tuple[float, int, int]:
                midpoint = (item["first_observed_seconds"] + item["last_observed_seconds"]) / 2
                selected_midpoints = [
                    (track["first_observed_seconds"] + track["last_observed_seconds"]) / 2
                    for track in chosen
                ]
                minimum_distance = min(abs(midpoint - value) for value in selected_midpoints)
                return minimum_distance, item["observation_count"], -item["track_id"]

            next_track = max(remaining, key=diversity_key)
            chosen.append(next_track)
            remaining.remove(next_track)
        selected.extend(chosen)
    return sorted(selected, key=lambda item: (item["class_id"], item["first_observed_seconds"], item["track_id"]))


def _select_detections(rows: list[dict]) -> list[dict]:
    if len(rows) <= MAX_REPRESENTATIVE_DETECTIONS:
        return sorted(rows, key=lambda item: (item["timestamp_seconds"], item["frame_index"], item["detection_id"]))

    by_class: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_class[row["class_id"]].append(row)
    quotas = _class_quotas({key: len(value) for key, value in by_class.items()}, MAX_REPRESENTATIVE_DETECTIONS)
    selected: list[dict] = []
    for class_id in sorted(by_class):
        candidates = by_class[class_id]
        quota = quotas[class_id]
        if not quota:
            continue
        # Preserve the strongest real observation for each selected class.
        first = min(candidates, key=lambda item: (-item["confidence"], item["frame_index"], item["detection_id"]))
        chosen = [first]
        remaining = [item for item in candidates if item["detection_id"] != first["detection_id"]]
        while remaining and len(chosen) < quota:
            next_detection = min(
                remaining,
                key=lambda item: (
                    -min(abs(item["timestamp_seconds"] - selected_item["timestamp_seconds"])
                         for selected_item in chosen),
                    -item["confidence"], item["timestamp_seconds"], item["frame_index"], item["detection_id"],
                ),
            )
            chosen.append(next_detection)
            remaining.remove(next_detection)
        selected.extend(chosen)
    return sorted(selected, key=lambda item: (item["timestamp_seconds"], item["frame_index"], item["detection_id"]))


def build_analysis_evidence(session: Session, job: AnalysisJob) -> dict:
    started = perf_counter()
    video = session.get(Video, job.video_id)
    total_detections = int(session.scalar(
        select(func.count()).select_from(Detection).where(Detection.job_id == job.job_id)
    ) or 0)

    class_rows = session.execute(
        select(Detection.class_id, Detection.class_name, func.count().label("count"),
               func.avg(Detection.confidence).label("mean_confidence"))
        .where(Detection.job_id == job.job_id)
        .group_by(Detection.class_id, Detection.class_name)
        .order_by(func.count().desc(), Detection.class_id)
    ).all()
    confidence = session.execute(
        select(func.min(Detection.confidence), func.avg(Detection.confidence), func.max(Detection.confidence))
        .where(Detection.job_id == job.job_id)
    ).one()
    confidence_bucket = case(
        (Detection.confidence < 0.2, "0.0-0.2"),
        (Detection.confidence < 0.4, "0.2-0.4"),
        (Detection.confidence < 0.6, "0.4-0.6"),
        (Detection.confidence < 0.8, "0.6-0.8"),
        else_="0.8-1.0",
    )
    confidence_distribution_rows = session.execute(
        select(confidence_bucket.label("confidence_range"), func.count().label("count"))
        .where(Detection.job_id == job.job_id)
        .group_by(confidence_bucket)
        .order_by(confidence_bucket)
    ).all()
    tracked_detection_count = int(session.scalar(
        select(func.count()).select_from(Detection).where(
            Detection.job_id == job.job_id, Detection.track_id.is_not(None)
        )
    ) or 0)
    track_rows = session.execute(
        select(Detection.track_id, Detection.class_id, Detection.class_name,
               func.min(Detection.timestamp_seconds).label("first_seen"),
               func.max(Detection.timestamp_seconds).label("last_seen"),
               func.count(Detection.detection_id).label("observations"))
        .where(Detection.job_id == job.job_id, Detection.track_id.is_not(None))
        .group_by(Detection.track_id, Detection.class_id, Detection.class_name)
        .order_by(Detection.track_id, func.count(Detection.detection_id).desc(), Detection.class_id)
    ).all()
    unique_track_count = int(session.scalar(
        select(func.count(func.distinct(Detection.track_id))).where(
            Detection.job_id == job.job_id, Detection.track_id.is_not(None)
        )
    ) or 0)
    tracks_by_id: dict[int, dict] = {}
    for row in track_rows:
        tracks_by_id.setdefault(int(row.track_id), {
            "track_id": int(row.track_id), "class_id": int(row.class_id), "class_name": row.class_name,
            "first_observed_seconds": float(row.first_seen), "last_observed_seconds": float(row.last_seen),
            "observation_count": int(row.observations),
        })
    all_tracks = [tracks_by_id[key] for key in sorted(tracks_by_id)]
    track_class_counts = Counter(track["class_id"] for track in all_tracks)
    track_class_names = {track["class_id"]: track["class_name"] for track in all_tracks}
    tracks = _select_tracks(all_tracks)

    # Only compact metadata for candidate rows is loaded. Boxes and frame indices
    # are omitted from the model evidence: timestamps and IDs remain the citation
    # anchors, while confidence and class retain useful observation context.
    rows = session.execute(
        select(Detection.detection_id, Detection.timestamp_seconds, Detection.class_id,
               Detection.class_name, Detection.confidence, Detection.track_id, Detection.frame_index)
        .where(Detection.job_id == job.job_id)
        .order_by(Detection.frame_index, Detection.detection_id)
    ).all()
    candidates = [{
        "detection_id": row.detection_id,
        "timestamp_seconds": float(row.timestamp_seconds),
        "class_id": int(row.class_id),
        "class_name": row.class_name,
        "confidence": float(row.confidence),
        "track_id": int(row.track_id) if row.track_id is not None else None,
        "frame_index": int(row.frame_index),
    } for row in rows]
    detections = [
        {key: value for key, value in item.items() if key != "frame_index"}
        for item in _select_detections(candidates)
    ]

    evidence = {
        "analysis_job_id": job.job_id,
        "video_id": job.video_id,
        "video_filename": video.original_filename if video else None,
        "analysis_status": job.status,
        # Duration, source FPS and dimensions are not persisted by the current pipeline.
        "video_duration_seconds": None,
        "original_fps": None,
        "resolution": None,
        "total_decoded_frames": job.total_frames,
        "sampled_frames": job.frames_sampled,
        # These aggregates use every persisted detection/tracked detection.
        "detection_count": total_detections,
        "tracked_detection_count": tracked_detection_count,
        "unique_track_count": unique_track_count,
        "track_class_counts": [
            {"class_id": class_id, "class_name": track_class_names[class_id], "unique_tracks": count}
            for class_id, count in sorted(track_class_counts.items())
        ],
        "class_counts": [
            {"class_id": int(row.class_id), "class_name": row.class_name,
             "count": int(row.count), "mean_confidence": round(float(row.mean_confidence), 5)}
            for row in class_rows
        ],
        "confidence_statistics": {
            "min": float(confidence[0]) if confidence[0] is not None else None,
            "mean": float(confidence[1]) if confidence[1] is not None else None,
            "max": float(confidence[2]) if confidence[2] is not None else None,
        },
        "confidence_distribution": [
            {"confidence_range": row.confidence_range, "count": int(row.count)}
            for row in confidence_distribution_rows
        ],
        "tracking_enabled": bool(job.tracking_enabled),
        "statistics_scope": "full_persisted_dataset",
        "evidence_selection": {
            "track_summaries_included": len(tracks),
            "representative_detections_included": len(detections),
            "selection_method": "deterministic class-diverse confidence and timestamp coverage",
            "scope_note": "Aggregate statistics cover the full persisted dataset; tracks and detections below are selected evidence samples.",
        },
        "tracks": tracks,
        "representative_detections": detections,
    }
    evidence_chars = len(json.dumps(evidence, separators=(",", ":"), ensure_ascii=False))
    logger.info(
        "AI_REASONING_PERF stage=evidence_build outcome=success job_id=%s duration_ms=%.1f "
        "selected_tracks=%d selected_detections=%d evidence_chars=%d full_detection_count=%d full_unique_track_count=%d",
        job.job_id, (perf_counter() - started) * 1000, len(tracks), len(detections), evidence_chars,
        total_detections, unique_track_count,
    )
    return evidence


def valid_evidence_reference_tuples(evidence: dict) -> set[tuple[str, int | None, float, str]]:
    return {
        ("detection_or_tracking", item["track_id"], item["timestamp_seconds"], item["detection_id"])
        for item in evidence["representative_detections"]
    }


def valid_evidence_records(evidence: dict) -> dict[str, dict]:
    """Return selected persisted records keyed by ID for strict citation checks."""
    return {item["detection_id"]: item for item in evidence["representative_detections"]}
