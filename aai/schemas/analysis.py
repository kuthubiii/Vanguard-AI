"""Analysis job and detection API models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisJobCreate(BaseModel):
    confidence_threshold: float = Field(default=0.25, ge=0.05, le=0.95)
    sampling_rate_fps: float = Field(default=5.0, ge=1.0, le=10)
    image_size: int = Field(default=640, ge=320, le=640, multiple_of=32)
    tracking_enabled: bool = True


class AnalysisJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    video_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress_percentage: int
    current_stage: str
    frames_processed: int
    frames_sampled: int
    total_frames: int | None
    detections_count: int
    error_message: str | None
    confidence_threshold: float
    sampling_rate_fps: float
    image_size: int
    tracking_enabled: bool
    inference_device: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class DetectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    detection_id: str
    job_id: str
    frame_index: int
    timestamp_seconds: float
    class_id: int
    class_name: str
    confidence: float
    bounding_box: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Pixel coordinates [x1, y1, x2, y2] from the top-left of the decoded source frame.",
    )
    track_id: int | None = None


class TrackingEventRead(DetectionRead):
    track_id: int


class TrackSummaryRead(BaseModel):
    track_id: int
    class_id: int
    class_name: str
    first_observed_seconds: float
    last_observed_seconds: float
    observation_count: int
    observed_span_seconds: float | None


class TrackSummaryListRead(BaseModel):
    job_id: str
    total_unique_tracked_objects: int
    total: int
    limit: int
    offset: int
    class_counts: dict[str, int]
    tracks: list[TrackSummaryRead]


class TrackDetailRead(TrackSummaryRead):
    job_id: str
    total_events: int
    limit: int
    offset: int
    events: list[TrackingEventRead]


class TrackingEventListRead(BaseModel):
    job_id: str
    total: int
    limit: int
    offset: int
    events: list[TrackingEventRead]


class AnalysisResultsRead(BaseModel):
    job_id: str
    video_id: str
    total: int
    limit: int
    offset: int
    detections: list[DetectionRead]


class AnalysisJobListRead(BaseModel):
    items: list[AnalysisJobRead]
    total: int
    limit: int
    offset: int
