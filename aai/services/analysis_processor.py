"""Single-video sampled detection/tracking and annotated-video processing."""

import math
import logging
import os
import re
import struct
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
from sqlalchemy.orm import Session

from aai.core.config import settings
from aai.db.models import AnalysisJob, Video
from aai.services.analysis_results import create_annotated_paths, persist_detection_batch
from aai.services.detection_engine import DetectionEngine, InferenceError
from aai.services.video_decoder import VideoDecodeError, VideoDecoder
from aai.services.video_storage import safe_storage_path
from aai.services.activity_logger import record_activity


class AnalysisProcessingError(Exception):
    """Safe processing error that can be persisted for the user."""


logger = logging.getLogger(__name__)
MAX_INTERPOLATION_BUFFER_BYTES = 192 * 1024 * 1024


@dataclass(frozen=True)
class _RenderedDetection:
    class_id: int
    class_name: str
    confidence: float | None
    bounding_box: list[float]
    track_id: int | None
    interpolated: bool = False
    predicted: bool = False


def _make_writer(path: Path, fps: float, width: int, height: int) -> cv2.VideoWriter:
    # This is an intermediate only. OpenCV's MP4V output is transcoded to H.264
    # before it is exposed through the annotated-video endpoint.
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if writer.isOpened():
        return writer
    writer.release()
    raise AnalysisProcessingError("OpenCV could not create an intermediate MP4 video.")


def _transcode_browser_video(source: Path, destination: Path) -> None:
    ffmpeg = settings.ffmpeg_path
    setup = "Install FFmpeg with an H.264 encoder, then set VANGUARD_FFMPEG_PATH to its executable (or add it to PATH)."
    if ffmpeg is None or not ffmpeg.is_file():
        raise AnalysisProcessingError(f"Browser-compatible H.264 video output requires FFmpeg. {setup}")

    try:
        encoders = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise AnalysisProcessingError(f"FFmpeg could not be started. {setup}") from error
    available_text = f"{encoders.stdout}\n{encoders.stderr}"
    if encoders.returncode != 0:
        raise AnalysisProcessingError(f"FFmpeg could not list its encoders. {setup}")

    # Prefer portable software, then commonly available hardware encoders.
    encoder = next(
        (name for name in ("libx264", "h264_nvenc", "h264_amf", "h264_qsv", "h264_mf", "h264_d3d12va")
         if re.search(rf"(?m)^\s*V\S+\s+{re.escape(name)}(?:\s|$)", available_text)),
        None,
    )
    if encoder is None:
        raise AnalysisProcessingError(f"FFmpeg is installed but has no supported H.264 encoder. {setup}")

    try:
        result = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-y", "-i", str(source), "-map", "0:v:0", "-an",
                "-c:v", encoder, "-pix_fmt", "yuv420p", "-b:v", "6M", "-movflags", "+faststart",
                str(destination),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise AnalysisProcessingError(f"FFmpeg H.264 encoding could not be started. {setup}") from error
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise AnalysisProcessingError(f"FFmpeg could not encode the annotated video as H.264. {setup}")

    capture = cv2.VideoCapture(str(destination))
    try:
        opened = capture.isOpened()
        ok, _frame = capture.read() if opened else (False, None)
        fourcc_value = int(capture.get(cv2.CAP_PROP_FOURCC)) if opened else 0
        fourcc = struct.pack("<I", fourcc_value).decode("ascii", errors="ignore").lower()
    finally:
        capture.release()
    if not ok or fourcc not in {"avc1", "h264"}:
        destination.unlink(missing_ok=True)
        raise AnalysisProcessingError("FFmpeg output failed H.264 browser-compatibility validation.")


_CLASS_COLORS = (
    (117, 211, 83), (230, 190, 76), (86, 196, 224), (180, 126, 238),
    (92, 160, 244), (214, 135, 108), (114, 215, 178), (202, 205, 92),
)


def _class_track_color(class_id: int, track_id: int | None) -> tuple[int, int, int]:
    """Return a repeatable class hue and track-specific brightness variant."""
    color = _CLASS_COLORS[class_id % len(_CLASS_COLORS)]
    if track_id is None:
        return color
    brightness = (0.78, 0.9, 1.0, 1.12, 1.24)[track_id % 5]
    return tuple(min(255, round(channel * brightness)) for channel in color)


def _annotation_label(detection) -> str:
    track_id = getattr(detection, "track_id", None)
    parts = [detection.class_name.upper()]
    if track_id is not None:
        parts.append(f"ID {track_id:02d}")
    if getattr(detection, "predicted", False) or getattr(detection, "is_predicted", False):
        parts.append("PREDICTED")
    elif getattr(detection, "interpolated", False):
        parts.append("INTERPOLATED")
    else:
        confidence = getattr(detection, "confidence", None)
        if confidence is not None:
            parts.append(f"{confidence * 100:.1f}%")
    return " | ".join(parts)


def _wrap_annotation_label(parts: list[str], max_width: int, font_scale: float, thickness: int):
    """Fit all label tokens inside the frame, wrapping and shrinking as needed."""
    scale = font_scale
    while scale > 0.2:
        lines: list[str] = []
        current = ""
        for part in parts:
            candidate = f"{current} | {part}" if current else part
            if cv2.getTextSize(candidate, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = part
                if cv2.getTextSize(current, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] > max_width:
                    current = ""
                    for char in part:
                        candidate = current + char
                        suffix = "..." if len(candidate) < len(part) else ""
                        if cv2.getTextSize(candidate + suffix, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] > max_width:
                            break
                        current = candidate
                    current = current.rstrip(" |") + ("..." if len(current) < len(part) else "")
        if current:
            lines.append(current)
        if all(cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0][0] <= max_width for line in lines):
            return lines, scale
        scale -= 0.04
    return ["OBJECT"], 0.2


def _overlaps(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]


def _layout_annotation_label(
    box: tuple[int, int, int, int],
    label_width: int,
    label_height: int,
    frame_width: int,
    frame_height: int,
    placed_labels: list[tuple[int, int, int, int]],
    other_boxes: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    margin = min(3, max(0, (min(frame_width, frame_height) - 1) // 2))
    max_left = max(margin, frame_width - margin - label_width)
    max_top = max(margin, frame_height - margin - label_height)
    x1, y1, x2, y2 = box
    left_candidates = {margin, max_left, min(max_left, max(margin, x1)), min(max_left, max(margin, x2 - label_width))}
    preferred_top = y1 - label_height - 2
    top_candidates = {margin, max_top, min(max_top, max(margin, preferred_top)), min(max_top, max(margin, y2 + 3))}
    step = max(1, label_height + 2)
    for offset in range(1, 5):
        top_candidates.add(min(max_top, max(margin, preferred_top - offset * step)))
        top_candidates.add(min(max_top, max(margin, y2 + 3 + offset * step)))
    candidates = [
        (left, top, left + label_width, top + label_height)
        for top in top_candidates
        for left in left_candidates
    ]
    def cost(rect: tuple[int, int, int, int]):
        label_collisions = sum(_overlaps(rect, other) for other in placed_labels)
        box_collisions = sum(_overlaps(rect, other) for other in other_boxes if other != box)
        displacement = abs(rect[0] - x1) + abs(rect[1] - preferred_top)
        return label_collisions, box_collisions, displacement
    return min(candidates, key=cost)


def _draw_dashed_line(frame, start, end, color, thickness: int, dash: int, gap: int) -> None:
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    dx, dy = (x2 - x1) / length, (y2 - y1) / length
    position = 0.0
    while position < length:
        end_position = min(length, position + dash)
        p1 = (round(x1 + dx * position), round(y1 + dy * position))
        p2 = (round(x1 + dx * end_position), round(y1 + dy * end_position))
        cv2.line(frame, p1, p2, color, thickness, cv2.LINE_AA)
        position = end_position + gap


def _draw_detection_box(frame, box, color, thickness: int, state: str) -> None:
    x1, y1, x2, y2 = box
    if state == "observed":
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)
        return
    dash, gap = (18, 8) if state == "predicted" else (7, 6)
    _draw_dashed_line(frame, (x1, y1), (x2, y1), color, thickness, dash, gap)
    _draw_dashed_line(frame, (x2, y1), (x2, y2), color, thickness, dash, gap)
    _draw_dashed_line(frame, (x2, y2), (x1, y2), color, thickness, dash, gap)
    _draw_dashed_line(frame, (x1, y2), (x1, y1), color, thickness, dash, gap)


def _draw_detections(frame, detections) -> None:
    height, width = frame.shape[:2]
    thickness = max(2, min(3, round(height / 540)))
    font_scale = max(0.28, min(0.82, height / 1320))
    font_thickness = max(1, round(font_scale * 2))
    padding = max(3, round(height / 180))
    margin = min(3, max(0, (min(width, height) - 1) // 2))
    placed_labels: list[tuple[int, int, int, int]] = []
    boxes: list[tuple[int, int, int, int]] = []
    normalized = []
    for detection in detections:
        x1, y1, x2, y2 = detection.bounding_box
        box = (
            max(0, min(width - 1, int(round(x1)))),
            max(0, min(height - 1, int(round(y1)))),
            max(0, min(width - 1, int(round(x2)))),
            max(0, min(height - 1, int(round(y2)))),
        )
        boxes.append(box)
        normalized.append((detection, box))

    for detection, box in sorted(normalized, key=lambda item: (item[1][1], item[1][0])):
        x1, y1, x2, y2 = box
        is_predicted = bool(getattr(detection, "predicted", False) or getattr(detection, "is_predicted", False))
        is_interpolated = bool(getattr(detection, "interpolated", False)) and not is_predicted
        state = "predicted" if is_predicted else "interpolated" if is_interpolated else "observed"
        color = _class_track_color(detection.class_id, getattr(detection, "track_id", None))
        if state == "interpolated":
            color = tuple(round(channel * 0.58 + 92) for channel in color)
        _draw_detection_box(frame, box, color, thickness, state)

        parts = _annotation_label(detection).split(" | ")
        max_text_width = max(1, width - margin * 2 - padding * 2 - 4)
        label_lines, scale = _wrap_annotation_label(parts, max_text_width, font_scale, font_thickness)
        metrics = [cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, scale, font_thickness) for line in label_lines]
        text_width = max((size[0][0] for size in metrics), default=1)
        text_height = sum(size[0][1] + size[1] for size in metrics) + max(0, len(metrics) - 1) * 2
        label_width = min(width - margin * 2, text_width + padding * 2 + 4)
        label_height = min(height - margin * 2, text_height + padding * 2)
        rect = _layout_annotation_label(
            box, label_width, label_height, width, height, placed_labels, boxes,
        )
        left, top, right, bottom = rect
        cv2.rectangle(frame, (left, top), (right, bottom), (20, 26, 23), -1)
        cv2.rectangle(frame, (left, top), (min(right, left + 3), bottom), color, -1)
        text_y = top + padding
        for line, ((line_width, line_height), baseline) in zip(label_lines, metrics, strict=True):
            text_y += line_height
            text_x = min(right - padding - line_width, max(left + padding + 4, left + (label_width - line_width) // 2))
            cv2.putText(frame, line, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, scale, (245, 249, 246), font_thickness, cv2.LINE_AA)
            text_y += baseline + 2
        placed_labels.append(rect)


def _interpolate_tracked_detections(previous, current, fraction: float) -> list[_RenderedDetection]:
    """Interpolate display boxes only for tracks observed at both endpoints."""
    previous_by_id = {
        item.track_id: item
        for item in previous
        if item.track_id is not None
    }
    interpolated: list[_RenderedDetection] = []
    for item in current:
        start = previous_by_id.get(item.track_id) if item.track_id is not None else None
        if start is None or start.class_id != item.class_id:
            continue
        box = [
            float(a + (b - a) * fraction)
            for a, b in zip(start.bounding_box, item.bounding_box, strict=True)
        ]
        interpolated.append(_RenderedDetection(
            class_id=item.class_id,
            class_name=item.class_name,
            confidence=None,
            bounding_box=box,
            track_id=item.track_id,
            interpolated=True,
        ))
    return interpolated


def process_video_analysis(session: Session, job: AnalysisJob, video: Video, engine: DetectionEngine) -> None:
    processing_started = time.monotonic()
    try:
        source_path = safe_storage_path(settings.video_storage_dir, video.storage_key)
    except ValueError as error:
        raise VideoDecodeError("The uploaded video has an invalid storage reference.") from error
    if not source_path.is_file():
        raise VideoDecodeError("The uploaded video file is missing from local storage.")

    temp_output, final_output, output_key = create_annotated_paths(job.job_id)
    transcode_output = final_output.with_name(f".{final_output.stem}.transcoding.mp4")
    writer: cv2.VideoWriter | None = None
    detection_batch: list[dict] = []
    frames_processed = 0
    frames_sampled = 0
    # ByteTrack sees only sampled inference frames. IDs and lost-track buffers
    # therefore describe this sampled timeline, not every source frame.
    detection_total = 0
    next_sample_timestamp = 0.0
    last_persist_at = time.monotonic()
    pending_frames = []
    pending_bytes = 0
    interpolation_disabled_for_interval = False
    previous_sample_timestamp: float | None = None
    previous_sample_detections = []
    detection_started_at: float | None = None
    tracking_started_at: float | None = None
    detection_activity_completed = False
    tracking_activity_completed = False

    try:
        engine.load()
        job.current_stage = "Decoding video"
        session.commit()

        with VideoDecoder(source_path) as decoder:
            metadata = decoder.metadata
            job.total_frames = metadata.total_frames
            output_fps = metadata.fps if metadata.fps > 0 else 25.0
            job.current_stage = "Tracking sampled frames and generating annotated video" if job.tracking_enabled else "Detecting sampled frames and generating annotated video"
            session.commit()

            for decoded in decoder:
                if writer is None:
                    frame_height, frame_width = decoded.image.shape[:2]
                    if frame_width <= 0 or frame_height <= 0:
                        raise VideoDecodeError("OpenCV could not determine the video frame dimensions.")
                    writer = _make_writer(temp_output, output_fps, frame_width, frame_height)

                sampled_detections = []
                interval = 1.0 / job.sampling_rate_fps
                is_sample_frame = decoded.timestamp_seconds + 1e-9 >= next_sample_timestamp
                if is_sample_frame:
                    if detection_started_at is None:
                        detection_started_at = time.monotonic()
                        record_activity(category="DETECTION", event_type="detection.started",
                                        title="YOLO detection started", description="Sampled-frame object detection began for this analysis.",
                                        severity="INFO", status="RUNNING", source="analysis_processor",
                                        video_id=video.id, analysis_job_id=job.job_id,
                                        metadata={"sampling_rate_fps": job.sampling_rate_fps,
                                                  "confidence_threshold": job.confidence_threshold,
                                                  "image_size": job.image_size},
                                        idempotency_key=f"analysis:{job.job_id}:detection:started", bind=session.get_bind())
                    if job.tracking_enabled and tracking_started_at is None:
                        tracking_started_at = time.monotonic()
                        record_activity(category="TRACKING", event_type="tracking.started",
                                        title="ByteTrack tracking started", description="Tracking began alongside sampled-frame detection.",
                                        severity="INFO", status="RUNNING", source="analysis_processor",
                                        video_id=video.id, analysis_job_id=job.job_id,
                                        idempotency_key=f"analysis:{job.job_id}:tracking:started", bind=session.get_bind())
                    try:
                        sampled_detections = (
                            engine.track(decoded.image, job.confidence_threshold, job.image_size, job.sampling_rate_fps)
                            if job.tracking_enabled
                            else engine.predict(decoded.image, job.confidence_threshold, job.image_size)
                        )
                    except InferenceError:
                        raise
                    except Exception as error:
                        raise AnalysisProcessingError("Object detection failed on a sampled frame.") from error

                    frames_sampled += 1
                    detection_total += len(sampled_detections)
                    for detection in sampled_detections:
                        detection_batch.append(
                            {
                                "detection_id": str(uuid.uuid4()),
                                "job_id": job.job_id,
                                "frame_index": decoded.frame_index,
                                "timestamp_seconds": float(decoded.timestamp_seconds),
                                "class_id": detection.class_id,
                                "class_name": detection.class_name,
                                "confidence": detection.confidence,
                                "bounding_box": detection.bounding_box,
                                "track_id": detection.track_id,
                            }
                        )
                    # Hold at most one sampling interval so same-ID endpoint
                    # observations can be linearly interpolated for display.
                    # Interpolated boxes never enter the database and carry no
                    # invented confidence score.
                    if previous_sample_timestamp is not None and pending_frames:
                        if job.tracking_enabled and not interpolation_disabled_for_interval:
                            span = decoded.timestamp_seconds - previous_sample_timestamp
                            for pending in pending_frames:
                                fraction = (pending.timestamp_seconds - previous_sample_timestamp) / span if span > 0 else 0.0
                                rendered = _interpolate_tracked_detections(
                                    previous_sample_detections,
                                    sampled_detections,
                                    fraction,
                                )
                                _draw_detections(pending.image, rendered)
                                writer.write(pending.image)
                        else:
                            for pending in pending_frames:
                                writer.write(pending.image)
                    pending_frames.clear()
                    pending_bytes = 0
                    interpolation_disabled_for_interval = False

                    _draw_detections(decoded.image, sampled_detections)
                    writer.write(decoded.image)
                    previous_sample_timestamp = decoded.timestamp_seconds
                    previous_sample_detections = sampled_detections if job.tracking_enabled else []
                    next_sample_timestamp += interval
                    while next_sample_timestamp <= decoded.timestamp_seconds + 1e-9:
                        next_sample_timestamp += interval
                elif job.tracking_enabled and not interpolation_disabled_for_interval:
                    if pending_bytes + decoded.image.nbytes > MAX_INTERPOLATION_BUFFER_BYTES:
                        # On unusually large or high-FPS input, prefer a
                        # bounded-memory raw interval over buffering without limit.
                        for pending in pending_frames:
                            writer.write(pending.image)
                        pending_frames.clear()
                        pending_bytes = 0
                        writer.write(decoded.image)
                        interpolation_disabled_for_interval = True
                    else:
                        pending_frames.append(decoded)
                        pending_bytes += decoded.image.nbytes
                else:
                    writer.write(decoded.image)
                frames_processed += 1
                now = time.monotonic()
                if frames_processed % 16 == 0 or now - last_persist_at >= 1.0:
                    _persist_progress(
                        session,
                        job,
                        detection_batch,
                        frames_processed,
                        frames_sampled,
                        detection_total,
                        engine.device_label,
                    )
                    detection_batch.clear()
                    last_persist_at = now

            if frames_processed == 0:
                raise VideoDecodeError("No decodable frames were found in the uploaded video.")
            # The tail has no later observation to interpolate toward. Preserve
            # the original frames without extrapolating stale tracks.
            for pending in pending_frames:
                writer.write(pending.image)
            pending_frames.clear()

        if writer is not None:
            writer.release()
            writer = None
        persist_detection_batch(session, job.job_id, detection_batch)
        detection_batch.clear()
        session.commit()

        aggregate_metadata = {"frames_processed": frames_processed, "frames_sampled": frames_sampled,
                              "detection_count": detection_total, "inference_device": engine.device_label}
        if detection_started_at is not None:
            record_activity(category="DETECTION", event_type="detection.completed",
                            title="YOLO detection completed", description="Sampled-frame detections were persisted for this analysis.",
                            severity="SUCCESS", status="COMPLETED", source="analysis_processor",
                            video_id=video.id, analysis_job_id=job.job_id,
                            duration_ms=(time.monotonic() - detection_started_at) * 1000,
                            metadata=aggregate_metadata, idempotency_key=f"analysis:{job.job_id}:detection:completed", bind=session.get_bind())
            detection_activity_completed = True
        if tracking_started_at is not None:
            record_activity(category="TRACKING", event_type="tracking.completed",
                            title="ByteTrack tracking completed", description="Tracker associations were persisted with sampled detections.",
                            severity="SUCCESS", status="COMPLETED", source="analysis_processor",
                            video_id=video.id, analysis_job_id=job.job_id,
                            duration_ms=(time.monotonic() - tracking_started_at) * 1000,
                            metadata=aggregate_metadata, idempotency_key=f"analysis:{job.job_id}:tracking:completed", bind=session.get_bind())
            tracking_activity_completed = True

        job.current_stage = "Saving results"
        job.frames_processed = frames_processed
        job.frames_sampled = frames_sampled
        job.detections_count = detection_total
        job.inference_device = engine.device_label
        job.progress_percentage = _progress(frames_processed, job.total_frames)
        session.commit()

        if not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise AnalysisProcessingError("OpenCV did not produce an annotated video file.")
        job.current_stage = "Encoding browser-compatible H.264 video"
        session.commit()
        _transcode_browser_video(temp_output, transcode_output)
        os.replace(transcode_output, final_output)
        temp_output.unlink(missing_ok=True)
        job.annotated_storage_key = output_key
        session.commit()
        elapsed = max(time.monotonic() - processing_started, 1e-9)
        logger.info(
            "Analysis %s processed %d decoded frames and %d sampled frames in %.2fs "
            "(%.2f output FPS, %.2f inference FPS; device=%s)",
            job.job_id,
            frames_processed,
            frames_sampled,
            elapsed,
            frames_processed / elapsed,
            frames_sampled / elapsed,
            engine.device_label,
        )
    except Exception as error:
        if detection_started_at is not None and not detection_activity_completed:
            record_activity(category="DETECTION", event_type="detection.failed",
                            title="YOLO detection failed", description="Sampled-frame detection did not finish and persist successfully.",
                            severity="ERROR", status="FAILED", source="analysis_processor",
                            video_id=video.id, analysis_job_id=job.job_id,
                            duration_ms=(time.monotonic() - detection_started_at) * 1000,
                            error_type=type(error).__name__, idempotency_key=f"analysis:{job.job_id}:detection:failed", bind=session.get_bind())
        if tracking_started_at is not None and not tracking_activity_completed:
            record_activity(category="TRACKING", event_type="tracking.failed",
                            title="ByteTrack tracking failed", description="Tracker processing did not finish and persist successfully.",
                            severity="ERROR", status="FAILED", source="analysis_processor",
                            video_id=video.id, analysis_job_id=job.job_id,
                            duration_ms=(time.monotonic() - tracking_started_at) * 1000,
                            error_type=type(error).__name__, idempotency_key=f"analysis:{job.job_id}:tracking:failed", bind=session.get_bind())
        if writer is not None:
            writer.release()
        temp_output.unlink(missing_ok=True)
        transcode_output.unlink(missing_ok=True)
        final_output.unlink(missing_ok=True)
        raise


def _progress(frames_processed: int, total_frames: int | None) -> int:
    if not total_frames or total_frames <= 0:
        return 0
    return min(99, max(0, math.floor(frames_processed * 100 / total_frames)))


def _persist_progress(
    session: Session,
    job: AnalysisJob,
    detection_batch: list[dict],
    frames_processed: int,
    frames_sampled: int,
    detection_total: int,
    inference_device: str,
) -> None:
    persist_detection_batch(session, job.job_id, detection_batch)
    job.frames_processed = frames_processed
    job.frames_sampled = frames_sampled
    job.detections_count = detection_total
    job.inference_device = inference_device
    job.progress_percentage = _progress(frames_processed, job.total_frames)
    session.commit()
