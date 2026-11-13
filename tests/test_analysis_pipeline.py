"""Mocked-inference tests for job lifecycle, decoding, persistence, and output."""

import os
import shutil
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from aai.api.routes.analysis import get_analysis_track, get_analysis_tracks, get_analysis_tracking_events, get_annotated_video, get_analysis_results
from aai.db.base import Base
from aai.db.models import AnalysisJob, Detection, Video
from aai.schemas.analysis import AnalysisJobCreate
from aai.services.analysis_jobs import create_analysis_job
from aai.services.analysis_processor import (
    _RenderedDetection,
    _annotation_label,
    _class_track_color,
    _draw_detections,
    _interpolate_tracked_detections,
    _layout_annotation_label,
)
from aai.services.analysis_worker import AnalysisWorker
from aai.services.detection_engine import DetectionEngine, ModelUnavailableError, RawDetection, TrackedDetection
from aai.services.video_decoder import VideoDecoder


class FakeQueue:
    def __init__(self):
        self.job_ids = []

    def can_accept(self):
        return True

    def submit(self, job_id):
        self.job_ids.append(job_id)


class FakeDetectionEngine:
    device_label = "CPU"
    next_id = 1

    def load(self):
        return None

    def predict(self, frame, confidence, image_size):
        return [RawDetection(0, "person", 0.91, [2.0, 2.0, 22.0, 26.0])]

    def track(self, frame, confidence, image_size, sampling_rate_fps=5.0):
        return [TrackedDetection(0, "person", 0.91, [2.0, 2.0, 22.0, 26.0], self.next_id)]

    def reset_tracker(self):
        self.next_id = 1


class FailingDetectionEngine:
    device_label = "CUDA 0"

    def load(self):
        raise ModelUnavailableError("Configured model weights could not be loaded.")


class FakeTensor:
    def __init__(self, data):
        self.data = data

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.data


class AnalysisPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vanguard-analysis-test-")
        self.root = Path(self.temp.name)
        self.videos_dir = self.root / "videos"
        self.processed_dir = self.root / "processed"
        self.env_patch = patch.dict(
            os.environ,
            {
                "VANGUARD_VIDEO_STORAGE_DIR": str(self.videos_dir),
                "VANGUARD_PROCESSED_VIDEO_DIR": str(self.processed_dir),
            },
        )
        self.env_patch.start()
        self.transcoder_patch = patch(
            "aai.services.analysis_processor._transcode_browser_video",
            side_effect=lambda source, destination: shutil.copyfile(source, destination),
        )
        self.transcoder_patch.start()
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.SessionLocal()
        self.video_id = str(uuid.uuid4())
        self.video_path = self.videos_dir / f"{uuid.uuid4().hex}.mp4"
        self._write_video(self.video_path)
        self.video = Video(
            id=self.video_id,
            original_filename="four-frames.mp4",
            stored_filename=self.video_path.name,
            storage_key=self.video_path.name,
            file_size=self.video_path.stat().st_size,
            content_type="video/mp4",
            upload_status="uploaded",
        )
        self.session.add(self.video)
        self.session.commit()

    def tearDown(self):
        self.transcoder_patch.stop()
        self.session.close()
        self.engine.dispose()
        self.env_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def _write_video(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 2.0, (32, 32))
        if not writer.isOpened():
            raise RuntimeError("OpenCV mp4v writer is unavailable")
        for index in range(4):
            frame = np.full((32, 32, 3), index * 20, dtype=np.uint8)
            writer.write(frame)
        writer.release()

    def _job(self, **overrides):
        values = {
            "job_id": str(uuid.uuid4()),
            "video_id": self.video_id,
            "status": "queued",
            "current_stage": "Queued",
            "confidence_threshold": 0.25,
            "sampling_rate_fps": 1.0,
            "image_size": 640,
        }
        values.update(overrides)
        job = AnalysisJob(**values)
        self.session.add(job)
        self.session.commit()
        return job

    def test_job_creation_rejects_missing_video_and_duplicate_active_job(self):
        worker = FakeQueue()
        first = create_analysis_job(self.session, self.video_id, AnalysisJobCreate(), worker)
        self.assertEqual(first.status, "queued")
        self.assertEqual(worker.job_ids, [first.job_id])

        with self.assertRaises(HTTPException) as duplicate:
            create_analysis_job(self.session, self.video_id, AnalysisJobCreate(), worker)
        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual(duplicate.exception.detail["job_id"], first.job_id)

        with self.assertRaises(HTTPException) as missing:
            create_analysis_job(self.session, "not-a-video", AnalysisJobCreate(), worker)
        self.assertEqual(missing.exception.status_code, 404)

    def test_opencv_decoder_streams_frames_with_source_timestamps(self):
        with VideoDecoder(self.video_path) as decoder:
            self.assertEqual(decoder.metadata.width, 32)
            self.assertEqual(decoder.metadata.height, 32)
            self.assertAlmostEqual(decoder.metadata.fps, 2.0, places=1)
            frames = list(decoder)
        self.assertEqual([frame.frame_index for frame in frames], [0, 1, 2, 3])
        self.assertEqual([frame.timestamp_seconds for frame in frames], [0.0, 0.5, 1.0, 1.5])

    def test_mocked_worker_completes_saves_results_and_annotated_video(self):
        job = self._job()
        worker = AnalysisWorker(self.SessionLocal, detector_factory=FakeDetectionEngine)
        worker._engine = FakeDetectionEngine()
        worker._process_job(job.job_id)

        self.session.expire_all()
        completed = self.session.get(AnalysisJob, job.job_id)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.progress_percentage, 100)
        self.assertEqual(completed.frames_processed, 4)
        self.assertEqual(completed.frames_sampled, 2)
        self.assertEqual(completed.detections_count, 2)
        self.assertEqual(completed.inference_device, "CPU")
        self.assertEqual(self.session.scalar(select(Detection).where(Detection.job_id == job.job_id)).class_name, "person")
        tracked = self.session.scalars(select(Detection).where(Detection.job_id == job.job_id)).all()
        self.assertEqual([event.track_id for event in tracked], [1, 1])

        results = get_analysis_results(job.job_id, self.session, limit=100, offset=0)
        self.assertEqual(results.total, 2)
        self.assertEqual(results.detections[0].bounding_box, [2.0, 2.0, 22.0, 26.0])
        annotated = get_annotated_video(job.job_id, self.session)
        self.assertTrue(Path(annotated.path).is_file())
        capture = cv2.VideoCapture(str(annotated.path))
        self.assertTrue(capture.isOpened())
        self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), 32)
        self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)), 32)
        self.assertEqual(int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), 4)
        capture.release()

        summaries = get_analysis_tracks(job.job_id, self.session, limit=100, offset=0)
        self.assertEqual(summaries.total_unique_tracked_objects, 1)
        self.assertEqual(summaries.class_counts, {"person": 1})
        self.assertEqual(summaries.tracks[0].observation_count, 2)
        self.assertAlmostEqual(summaries.tracks[0].observed_span_seconds, 1.0)
        events = get_analysis_tracking_events(job.job_id, self.session, limit=100, offset=0)
        self.assertEqual(events.total, 2)
        detail = get_analysis_track(job.job_id, 1, self.session, limit=100, offset=0)
        self.assertEqual(detail.total_events, 2)

    def test_worker_interpolates_only_matching_sampled_tracks_without_persisting_them(self):
        job = self._job(sampling_rate_fps=1.0, tracking_enabled=True)
        worker = AnalysisWorker(self.SessionLocal, detector_factory=FakeDetectionEngine)
        worker._engine = FakeDetectionEngine()
        with patch("aai.services.analysis_processor._draw_detections", wraps=_draw_detections) as draw:
            worker._process_job(job.job_id)
        interpolated = [
            detection
            for call in draw.call_args_list
            for detection in call.args[1]
            if getattr(detection, "interpolated", False)
        ]
        self.assertEqual(len(interpolated), 1)
        self.assertIsNone(interpolated[0].confidence)
        persisted = self.session.scalars(select(Detection).where(Detection.job_id == job.job_id)).all()
        self.assertEqual(len(persisted), 2)

    def test_official_track_call_uses_bytetrack_and_persistent_state(self):
        class MockBoxes:
            xyxy = FakeTensor([[1.0, 2.0, 10.0, 12.0]])
            cls = FakeTensor([0])
            conf = FakeTensor([0.9])
            id = FakeTensor([7])

            def __len__(self):
                return 1

        class MockResult:
            boxes = MockBoxes()
            names = {0: "person"}

        class MockModel:
            task = "detect"

            def __init__(self):
                from types import SimpleNamespace
                self.predictor = SimpleNamespace(trackers=[SimpleNamespace()])

            def track(self, **kwargs):
                self.kwargs = kwargs
                return [MockResult()]

        engine = DetectionEngine(Path("unused.pt"))
        engine.model = MockModel()
        tracked = engine.track(np.zeros((16, 16, 3), dtype=np.uint8), 0.25, 640, sampling_rate_fps=5)
        self.assertEqual(tracked[0].track_id, 7)
        self.assertEqual(engine.model.kwargs["tracker"], "bytetrack.yaml")
        self.assertTrue(engine.model.kwargs["persist"])
        self.assertEqual(engine.model.kwargs["conf"], 0.1)
        self.assertEqual(engine.model.predictor.trackers[0].max_time_lost, 5)

    def test_sampling_defaults_to_five_fps_and_is_bounded_to_ten(self):
        self.assertEqual(AnalysisJobCreate().sampling_rate_fps, 5.0)
        self.assertEqual(AnalysisJobCreate(sampling_rate_fps=10).sampling_rate_fps, 10.0)
        with self.assertRaises(ValueError):
            AnalysisJobCreate(sampling_rate_fps=0.5)

    def test_interpolation_requires_same_real_track_and_has_no_confidence(self):
        before = [TrackedDetection(0, "person", 0.91, [0, 0, 10, 10], 4)]
        after = [TrackedDetection(0, "person", 0.83, [10, 20, 30, 40], 4)]
        midpoint = _interpolate_tracked_detections(before, after, 0.5)
        self.assertEqual(len(midpoint), 1)
        self.assertEqual(midpoint[0].bounding_box, [5.0, 10.0, 20.0, 25.0])
        self.assertIsNone(midpoint[0].confidence)
        self.assertTrue(midpoint[0].interpolated)
        self.assertEqual(_interpolate_tracked_detections(before, [TrackedDetection(0, "person", 0.83, [10, 20, 30, 40], 9)], 0.5), [])

    def test_annotation_renderer_draws_readable_label_without_changing_detection(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        detection = TrackedDetection(0, "person", 0.964, [120, 100, 400, 800], 4)
        _draw_detections(frame, [detection])
        self.assertFalse(np.array_equal(frame, np.zeros_like(frame)))
        self.assertEqual(detection.confidence, 0.964)

    def test_actual_predicted_and_interpolated_labels_are_distinct_and_do_not_fabricate_scores(self):
        observed = TrackedDetection(0, "person", 0.964, [20, 20, 100, 120], 4)
        predicted = _RenderedDetection(0, "person", None, [20, 20, 100, 120], 4, predicted=True)
        interpolated = _RenderedDetection(0, "person", None, [20, 20, 100, 120], 4, interpolated=True)
        self.assertEqual(_annotation_label(observed), "PERSON | ID 04 | 96.4%")
        self.assertEqual(_annotation_label(predicted), "PERSON | ID 04 | PREDICTED")
        self.assertEqual(_annotation_label(interpolated), "PERSON | ID 04 | INTERPOLATED")
        self.assertNotIn("%", _annotation_label(predicted))
        self.assertNotIn("%", _annotation_label(interpolated))

    def test_observed_prediction_and_interpolation_have_distinct_box_styles(self):
        box = [20, 20, 100, 60]
        variants = (
            TrackedDetection(0, "person", 0.9, box, 4),
            _RenderedDetection(0, "person", None, box, 4, predicted=True),
            _RenderedDetection(0, "person", None, box, 4, interpolated=True),
        )
        coverage = []
        for detection in variants:
            frame = np.zeros((100, 140, 3), dtype=np.uint8)
            _draw_detections(frame, [detection])
            coverage.append(int(np.count_nonzero(np.any(frame[60, 20:101] != 0, axis=1))))
        self.assertGreater(coverage[0], coverage[1])
        self.assertGreater(coverage[1], 0)
        self.assertNotEqual(coverage[1], coverage[2])

    def test_class_track_colors_are_stable_and_track_specific(self):
        self.assertEqual(_class_track_color(0, 4), _class_track_color(0, 4))
        self.assertNotEqual(_class_track_color(0, 1), _class_track_color(0, 2))
        self.assertNotEqual(_class_track_color(0, 1), _class_track_color(1, 1))

    def test_label_layout_stays_in_frame_and_avoids_existing_label_overlap(self):
        box = (0, 0, 40, 40)
        first = _layout_annotation_label(box, 120, 30, 240, 140, [], [box])
        second = _layout_annotation_label(box, 120, 30, 240, 140, [first], [box])
        for rect in (first, second):
            self.assertGreaterEqual(rect[0], 0)
            self.assertGreaterEqual(rect[1], 0)
            self.assertLessEqual(rect[2], 240)
            self.assertLessEqual(rect[3], 140)
        self.assertFalse(
            first[0] < second[2] and first[2] > second[0]
            and first[1] < second[3] and first[3] > second[1]
        )

    def test_empty_tracker_results_are_not_assigned_fake_ids(self):
        class EmptyBoxes:
            id = None

            def __len__(self):
                return 0

        class EmptyResult:
            boxes = EmptyBoxes()
            names = {0: "person"}

        self.assertEqual(DetectionEngine._tracked_detections(EmptyResult()), [])

    def test_real_bytetrack_keeps_ids_through_empty_frame_and_resets_job_ids(self):
        from ultralytics.trackers.byte_tracker import BYTETracker
        from ultralytics.utils import IterableSimpleNamespace, YAML
        from ultralytics.utils.checks import check_yaml

        class TrackerBoxes:
            def __init__(self, center_x):
                self.xywh = np.array([[center_x, 20, 10, 10]], dtype=np.float32) if center_x is not None else np.empty((0, 4), dtype=np.float32)
                self.conf = np.array([0.9], dtype=np.float32) if center_x is not None else np.empty((0,), dtype=np.float32)
                self.cls = np.array([0], dtype=np.float32) if center_x is not None else np.empty((0,), dtype=np.float32)

            def __len__(self):
                return len(self.conf)

            def __getitem__(self, mask):
                selected = object.__new__(TrackerBoxes)
                selected.xywh = self.xywh[mask]
                selected.conf = self.conf[mask]
                selected.cls = self.cls[mask]
                return selected

        tracker = BYTETracker(IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml"))))
        first = tracker.update(TrackerBoxes(20))
        moved = tracker.update(TrackerBoxes(21))
        self.assertEqual(int(first[0, 4]), int(moved[0, 4]))
        empty = tracker.update(TrackerBoxes(None))
        self.assertEqual(empty.size, 0)
        self.assertEqual(len(tracker.lost_stracks), 1)
        reacquired = tracker.update(TrackerBoxes(22))
        self.assertEqual(int(reacquired[0, 4]), int(first[0, 4]))

        tracker.update(TrackerBoxes(100))
        new_track = tracker.update(TrackerBoxes(101))
        self.assertEqual(int(new_track[0, 4]), 2)
        tracker.reset()
        reset_track = tracker.update(TrackerBoxes(20))
        self.assertEqual(int(reset_track[0, 4]), 1)

    def test_existing_phase4_detection_table_gets_additive_track_id_migration(self):
        from aai.db import session as database

        legacy_engine = create_engine("sqlite://")
        try:
            with legacy_engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE detections (detection_id VARCHAR(36) PRIMARY KEY, job_id VARCHAR(36) NOT NULL, "
                    "frame_index INTEGER NOT NULL, timestamp_seconds FLOAT NOT NULL, class_id INTEGER NOT NULL, "
                    "class_name VARCHAR(128) NOT NULL, confidence FLOAT NOT NULL, bounding_box JSON NOT NULL)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE videos (id VARCHAR(36) PRIMARY KEY, original_filename VARCHAR(255) NOT NULL, "
                    "stored_filename VARCHAR(48) NOT NULL UNIQUE, storage_key VARCHAR(48) NOT NULL UNIQUE, "
                    "file_size INTEGER NOT NULL, content_type VARCHAR(64) NOT NULL, upload_status VARCHAR(16) NOT NULL, "
                    "created_at DATETIME NOT NULL)"
                )
            with patch("aai.db.session.engine", legacy_engine):
                database.init_database()
            self.assertIn("track_id", {column["name"] for column in inspect(legacy_engine).get_columns("detections")})
            self.assertIn("ix_detections_job_track_frame", {index["name"] for index in inspect(legacy_engine).get_indexes("detections")})
            self.assertIn("upload_idempotency_key", {column["name"] for column in inspect(legacy_engine).get_columns("videos")})
            self.assertIn("uq_videos_upload_idempotency_key", {index["name"] for index in inspect(legacy_engine).get_indexes("videos")})
        finally:
            legacy_engine.dispose()

    def test_tracker_state_is_reset_before_and_after_each_job(self):
        engine = FakeDetectionEngine()
        worker = AnalysisWorker(self.SessionLocal, detector_factory=lambda: engine)
        job = self._job()
        worker._process_job(job.job_id)
        self.assertEqual(engine.next_id, 1)
        self.assertEqual(self.session.scalar(select(Detection).where(Detection.job_id == job.job_id)).track_id, 1)

    def test_worker_failure_is_persisted_and_partial_detections_are_removed(self):
        job = self._job()
        self.session.add(
            Detection(
                detection_id=str(uuid.uuid4()),
                job_id=job.job_id,
                frame_index=0,
                timestamp_seconds=0,
                class_id=0,
                class_name="person",
                confidence=0.9,
                bounding_box=[1, 1, 2, 2],
            )
        )
        self.session.commit()
        job.detections_count = 1
        self.session.commit()

        worker = AnalysisWorker(self.SessionLocal, detector_factory=FailingDetectionEngine)
        worker._engine = FailingDetectionEngine()
        worker._process_job(job.job_id)

        self.session.expire_all()
        failed = self.session.get(AnalysisJob, job.job_id)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_message, "Configured model weights could not be loaded.")
        self.assertEqual(failed.detections_count, 0)
        self.assertEqual(self.session.scalar(select(Detection).where(Detection.job_id == job.job_id)), None)

    def test_restart_recovery_fails_interrupted_jobs_and_cleans_partial_outputs(self):
        queued = self._job(status="queued")
        second_video_id = str(uuid.uuid4())
        second_path = self.videos_dir / f"{uuid.uuid4().hex}.mp4"
        second_path.write_bytes(self.video_path.read_bytes())
        second_video = Video(
            id=second_video_id,
            original_filename="another-video.mp4",
            stored_filename=second_path.name,
            storage_key=second_path.name,
            file_size=second_path.stat().st_size,
            content_type="video/mp4",
            upload_status="uploaded",
        )
        self.session.add(second_video)
        self.session.commit()
        running = self._job(status="running", current_stage="Detecting", video_id=second_video_id)
        completed_file = self.processed_dir / f"{uuid.uuid4()}.mp4"
        completed_file.parent.mkdir(parents=True, exist_ok=True)
        completed_file.write_bytes(b"completed")
        completed = self._job(status="completed", annotated_storage_key=completed_file.name)
        orphan_file = self.processed_dir / f"{uuid.uuid4()}.mp4"
        orphan_file.write_bytes(b"orphan")
        partial_file = self.processed_dir / f".{uuid.uuid4()}.partial.mp4"
        partial_file.write_bytes(b"partial")

        worker = AnalysisWorker(self.SessionLocal, detector_factory=FakeDetectionEngine)
        worker._recover_interrupted_jobs()

        self.session.expire_all()
        self.assertEqual(self.session.get(AnalysisJob, queued.job_id).status, "failed")
        self.assertEqual(self.session.get(AnalysisJob, running.job_id).status, "failed")
        self.assertEqual(self.session.get(AnalysisJob, completed.job_id).status, "completed")
        self.assertTrue(completed_file.exists())
        self.assertFalse(orphan_file.exists())
        self.assertFalse(partial_file.exists())

    def test_detection_engine_serializes_boxes_from_mocked_ultralytics_result(self):
        class MockBoxes:
            xyxy = FakeTensor([[1.5, 2.5, 10.0, 12.0]])
            cls = FakeTensor([0])
            conf = FakeTensor([0.875])

            def __len__(self):
                return 1

        class MockResult:
            boxes = MockBoxes()
            names = {0: "person"}

        parsed = DetectionEngine._detections(MockResult())
        self.assertEqual(parsed, [RawDetection(0, "person", 0.875, [1.5, 2.5, 10.0, 12.0])])


if __name__ == "__main__":
    unittest.main()
