"""Streaming OpenCV video decoding with source-frame timestamps."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


class VideoDecodeError(Exception):
    """Safe, user-facing failure opening or decoding a source video."""


@dataclass(frozen=True)
class VideoMetadata:
    fps: float
    width: int
    height: int
    total_frames: int | None


@dataclass(frozen=True)
class DecodedFrame:
    frame_index: int
    timestamp_seconds: float
    image: np.ndarray


class VideoDecoder:
    def __init__(self, video_path: Path) -> None:
        self.capture = cv2.VideoCapture(str(video_path))
        if not self.capture.isOpened():
            self.capture.release()
            raise VideoDecodeError("OpenCV could not open this video. The file may be corrupt or use an unsupported codec.")

        fps = float(self.capture.get(cv2.CAP_PROP_FPS))
        raw_total = float(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.metadata = VideoMetadata(
            fps=fps if fps > 0 else 0.0,
            width=width,
            height=height,
            total_frames=int(raw_total) if raw_total > 0 else None,
        )
        self._has_frames = False

    def __enter__(self) -> "VideoDecoder":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.capture.release()

    def __iter__(self) -> Iterator[DecodedFrame]:
        frame_index = 0
        previous_timestamp = -1.0
        while True:
            ok, frame = self.capture.read()
            if not ok:
                if not self._has_frames:
                    raise VideoDecodeError("OpenCV opened the file but could not decode any video frames.")
                break

            self._has_frames = True
            if frame is None or frame.size == 0:
                raise VideoDecodeError("The video contains an unreadable frame.")

            if self.metadata.fps > 0:
                timestamp = frame_index / self.metadata.fps
            else:
                timestamp = float(self.capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
                if timestamp < 0 or (frame_index > 0 and timestamp <= previous_timestamp):
                    raise VideoDecodeError("The video does not provide a usable frame rate or source timestamps.")

            if frame_index == 0 and (self.metadata.width <= 0 or self.metadata.height <= 0):
                self.metadata = VideoMetadata(
                    fps=self.metadata.fps,
                    width=int(frame.shape[1]),
                    height=int(frame.shape[0]),
                    total_frames=self.metadata.total_frames,
                )
            previous_timestamp = timestamp
            yield DecodedFrame(frame_index, timestamp, frame)
            frame_index += 1
        if self.metadata.total_frames is not None and frame_index < self.metadata.total_frames:
            raise VideoDecodeError("Video decoding stopped before the reported frame count was reached.")
