"""Lazy, single-instance Ultralytics detector with CUDA OOM fallback."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

import numpy as np


class ModelUnavailableError(Exception):
    """Raised when the configured detector cannot be loaded."""


class InferenceError(Exception):
    """Raised when inference fails on both the preferred and fallback devices."""


@dataclass(frozen=True)
class RawDetection:
    class_id: int
    class_name: str
    confidence: float
    bounding_box: list[float]


@dataclass(frozen=True)
class TrackedDetection(RawDetection):
    track_id: int | None


class DetectionEngine:
    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self.model: Any | None = None
        self.device: int | str = "cpu"
        self._load_error: str | None = None
        self._torch: Any | None = None

    @property
    def device_label(self) -> str:
        return f"CUDA {self.device}" if isinstance(self.device, int) else str(self.device).upper()

    def load(self) -> None:
        if self.model is not None:
            return
        if self._load_error is not None:
            raise ModelUnavailableError(self._load_error)

        try:
            import torch
            from ultralytics import YOLO

            self._torch = torch
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            self.device = 0 if torch.cuda.is_available() else "cpu"
            model = YOLO(str(self.model_path))
            if getattr(model, "task", "detect") != "detect":
                raise ModelUnavailableError("The configured weights are not an object-detection model.")
            self.model = model
        except ModelUnavailableError as error:
            self._load_error = str(error)
            raise
        except Exception as error:
            self._load_error = "YOLO weights could not be loaded. Check the configured model file and network availability."
            raise ModelUnavailableError(self._load_error) from error

    def predict(self, frame: np.ndarray, confidence: float, image_size: int) -> list[RawDetection]:
        self.load()
        assert self.model is not None
        try:
            results = self.model.predict(
                source=frame,
                conf=confidence,
                imgsz=image_size,
                device=self.device,
                quantize=16 if self.device != "cpu" else 32,
                batch=1,
                verbose=False,
            )
        except Exception as error:
            if self.device != "cpu" and self._is_cuda_oom(error):
                try:
                    if self._torch is not None:
                        self._torch.cuda.empty_cache()
                    self.model.to("cpu")
                    self.device = "cpu"
                    results = self.model.predict(
                        source=frame,
                        conf=confidence,
                        imgsz=image_size,
                        device="cpu",
                        quantize=32,
                        batch=1,
                        verbose=False,
                    )
                except Exception as cpu_error:
                    raise InferenceError("CUDA ran out of memory and CPU fallback inference failed.") from cpu_error
            else:
                raise InferenceError("YOLO inference failed for a decoded video frame.") from error

        return self._detections(results[0])

    def reset_tracker(self) -> None:
        """Clear Ultralytics tracker state before and after each analysis job."""
        if self.model is None:
            return
        predictor = getattr(self.model, "predictor", None)
        trackers = getattr(predictor, "trackers", ()) if predictor is not None else ()
        for tracker in trackers:
            tracker.reset()

    def track(self, frame: np.ndarray, confidence: float, image_size: int, sampling_rate_fps: float = 5.0) -> list[TrackedDetection]:
        """Run official Ultralytics ByteTrack on one sampled frame, preserving state."""
        self.load()
        assert self.model is not None
        # ByteTrack needs low-score boxes for its second association stage.
        # Apply the user's display/persistence threshold after association.
        tracker_confidence = min(confidence, 0.1)
        try:
            results = self.model.track(
                source=frame,
                conf=tracker_confidence,
                imgsz=image_size,
                device=self.device,
                quantize=16 if self.device != "cpu" else 32,
                batch=1,
                tracker="bytetrack.yaml",
                persist=True,
                verbose=False,
            )
        except Exception as error:
            if self.device != "cpu" and self._is_cuda_oom(error):
                try:
                    if self._torch is not None:
                        self._torch.cuda.empty_cache()
                    self.model.to("cpu")
                    self.device = "cpu"
                    results = self.model.track(
                        source=frame, conf=tracker_confidence, imgsz=image_size, device="cpu",
                        quantize=32, batch=1, tracker="bytetrack.yaml", persist=True, verbose=False,
                    )
                except Exception as cpu_error:
                    raise InferenceError("CUDA ran out of memory and CPU fallback tracking failed.") from cpu_error
            else:
                raise InferenceError("YOLO ByteTrack failed on a sampled video frame.") from error
        # ByteTrack's `track_buffer` counts tracker updates, not source-video
        # frames. Keep lost tracks for about one second on the sampled timeline.
        predictor = getattr(self.model, "predictor", None)
        for tracker in getattr(predictor, "trackers", ()) if predictor is not None else ():
            tracker.max_time_lost = max(1, round(sampling_rate_fps))
        return [detection for detection in self._tracked_detections(results[0]) if detection.confidence >= confidence]

    def _is_cuda_oom(self, error: Exception) -> bool:
        oom_type = getattr(getattr(self._torch, "cuda", None), "OutOfMemoryError", ())
        return (bool(oom_type) and isinstance(error, oom_type)) or "out of memory" in str(error).lower()

    @staticmethod
    def _detections(result: Any) -> list[RawDetection]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        coordinates = boxes.xyxy.detach().cpu().tolist()
        class_ids = boxes.cls.detach().cpu().tolist()
        confidences = boxes.conf.detach().cpu().tolist()
        names = result.names
        detections: list[RawDetection] = []
        for box, raw_class_id, confidence in zip(coordinates, class_ids, confidences, strict=True):
            class_id = int(raw_class_id)
            if isinstance(names, Mapping):
                class_name = str(names.get(class_id, names.get(str(class_id), f"class_{class_id}")))
            else:
                class_name = str(names[class_id])
            detections.append(
                RawDetection(
                    class_id=class_id,
                    class_name=class_name,
                    confidence=float(confidence),
                    bounding_box=[float(value) for value in box[:4]],
                )
            )
        return detections

    @staticmethod
    def _tracked_detections(result: Any) -> list[TrackedDetection]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        coordinates = boxes.xyxy.detach().cpu().tolist()
        class_ids = boxes.cls.detach().cpu().tolist()
        confidences = boxes.conf.detach().cpu().tolist()
        raw_ids = getattr(boxes, "id", None)
        ids = raw_ids.detach().cpu().tolist() if raw_ids is not None else [None] * len(coordinates)
        names = result.names
        detections: list[TrackedDetection] = []
        for box, raw_class_id, confidence, raw_track_id in zip(coordinates, class_ids, confidences, ids, strict=True):
            class_id = int(raw_class_id)
            class_name = str(names.get(class_id, names.get(str(class_id), f"class_{class_id}"))) if isinstance(names, Mapping) else str(names[class_id])
            detections.append(TrackedDetection(
                class_id=class_id,
                class_name=class_name,
                confidence=float(confidence),
                bounding_box=[float(value) for value in box[:4]],
                track_id=int(raw_track_id) if raw_track_id is not None else None,
            ))
        return detections
