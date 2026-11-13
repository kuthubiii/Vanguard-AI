"""Temporary-database coverage for Phase 8 metrics, rules, reviews, and recommendations."""

import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aai.db.base import Base
from aai.db.models import AIReport, AnalysisJob, Detection, OperationalEvent, OperationalReview, Video
from aai.services.operational_analytics import get_analytics, get_overview
from aai.services.operational_availability import get_availability
from aai.services.operational_events import add_review, list_events, list_risks, refresh_rule_events
from aai.services.operational_queries import OperationalFilters
from aai.services.operational_recommendations import get_recommendations


class OperationalIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vanguard-operations-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name, 'operations.sqlite3').as_posix()}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def _video(self, video_id="video", filename="warehouse.mp4"):
        video = Video(id=video_id, original_filename=filename, stored_filename=f"{video_id}.mp4",
                      storage_key=f"{video_id}.mp4", file_size=1000, content_type="video/mp4",
                      upload_status="uploaded")
        self.db.add(video)
        return video

    def _job(self, job_id, video_id="video", at=None, *, status="completed", threshold=0.25, sampling=5.0):
        job = AnalysisJob(
            job_id=job_id, video_id=video_id, status=status, progress_percentage=100 if status == "completed" else 0,
            current_stage="Completed" if status == "completed" else status, frames_processed=20,
            frames_sampled=10, total_frames=20, detections_count=0, confidence_threshold=threshold,
            sampling_rate_fps=sampling, image_size=640, tracking_enabled=True,
            created_at=at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.db.add(job)
        return job

    def _detections(self, job_id, count, *, class_id=0, class_name="person", track_id=None,
                    start_frame=0, one_frame=False):
        rows = []
        for index in range(count):
            rows.append(Detection(
                detection_id=str(uuid.uuid4()), job_id=job_id,
                frame_index=start_frame if one_frame else start_frame + index,
                timestamp_seconds=(start_frame if one_frame else start_frame + index) / 5,
                class_id=class_id, class_name=class_name,
                confidence=0.3 + (index % 7) * 0.1, bounding_box=[0, 0, 10, 10], track_id=track_id,
            ))
        self.db.add_all(rows)
        return rows

    def test_overview_counts_persisted_rows_and_scopes_tracks_per_job(self):
        self._video("v1", "a.mp4")
        self._video("v2", "b.mp4")
        self._job("j1", "v1", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self._job("j2", "v2", datetime(2026, 1, 2, tzinfo=timezone.utc))
        self._job("failed", "v2", datetime(2026, 1, 2, tzinfo=timezone.utc), status="failed")
        self._detections("j1", 2, track_id=5)
        self._detections("j1", 1, track_id=None, class_id=28, class_name="suitcase")
        self._detections("j2", 2, track_id=5, class_id=7, class_name="truck")
        self._detections("failed", 50, track_id=9)
        self.db.add(AIReport(report_id="report", job_id="j1", model_name="local", summary="real",
                             observations=[], recommendations=[], limitations=[], evidence={}))
        self.db.commit()

        result = get_overview(self.db, OperationalFilters())
        self.assertEqual(result.metrics.completed_analysis_jobs, 2)
        self.assertEqual(result.metrics.total_videos_analysed, 2)
        self.assertEqual(result.metrics.total_detected_objects, 5)
        self.assertEqual(result.metrics.total_unique_tracked_entities, 2)
        self.assertEqual(result.metrics.tracked_object_categories, ["person", "truck"])
        self.assertEqual(result.metrics.available_ai_reports, 1)
        self.assertEqual(result.metrics.events_requiring_attention, 0)

    def test_date_and_job_filters_apply_to_analytics_and_pagination(self):
        self._video()
        self._job("j1", at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self._job("j2", at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        self._detections("j1", 3, track_id=1)
        self._detections("j2", 2, track_id=1)
        self.db.commit()
        filtered = get_overview(self.db, OperationalFilters(
            date_from=datetime(2026, 1, 2, tzinfo=timezone.utc),
            date_to=datetime(2026, 1, 2, 23, 59, tzinfo=timezone.utc),
        ))
        self.assertEqual(filtered.metrics.completed_analysis_jobs, 1)
        self.assertEqual(filtered.metrics.total_detected_objects, 2)
        page = get_analytics(self.db, OperationalFilters(), limit=1, offset=0)
        next_page = get_analytics(self.db, OperationalFilters(), limit=1, offset=1)
        self.assertEqual(page.tracking_total, 2)
        self.assertEqual(len(page.tracking_activity), 1)
        self.assertEqual(len(next_page.tracking_activity), 1)
        self.assertNotEqual(page.tracking_activity[0].analysis_job_id, next_page.tracking_activity[0].analysis_job_id)
        job_filter = get_analytics(self.db, OperationalFilters(analysis_job_id="j1"), limit=20, offset=0)
        self.assertEqual(job_filter.job_comparisons[0].analysis_job_id, "j1")

    def _seed_rule_analyses(self):
        self._video()
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index, count in enumerate((10, 12, 10)):
            job_id = f"base-{index}"
            self._job(job_id, at=at + timedelta(days=index))
            self._detections(job_id, count, track_id=None, start_frame=index * 100)
        target = self._job("target", at=at + timedelta(days=4))
        self._detections(target.job_id, 20, track_id=None, start_frame=1000, one_frame=True)
        self._detections(target.job_id, 30, class_id=28, class_name="suitcase", track_id=7,
                         start_frame=1000, one_frame=True)
        self.db.commit()

    def test_rule_events_are_evidence_backed_and_idempotent(self):
        self._seed_rule_analyses()
        with patch.dict(os.environ, {
            "OPERATIONS_MIN_BASELINE_JOBS": "2",
            "OPERATIONS_HIGH_ACTIVITY_RATIO": "2",
            "OPERATIONS_CATEGORY_SHARE_SHIFT": "0.2",
            "OPERATIONS_FRAME_CONCENTRATION_MIN": "8",
            "OPERATIONS_REPEATED_TRACK_MIN_OBSERVATIONS": "30",
        }):
            created = refresh_rule_events(self.db, OperationalFilters())
            event_rows = self.db.scalars(select(OperationalEvent)).all()
            again = refresh_rule_events(self.db, OperationalFilters())
        self.assertGreater(created, 0)
        self.assertEqual(again, 0)
        types = {event.event_type for event in event_rows}
        self.assertTrue({"high_detection_activity", "category_distribution_change",
                         "frame_observation_concentration", "repeated_track_observations"}.issubset(types))
        detection_ids = {row.detection_id for row in self.db.scalars(select(Detection)).all()}
        for event in event_rows:
            self.assertTrue(set(event.related_detection_ids).issubset(detection_ids))
            self.assertTrue(event.rule_basis["rule"].endswith("_v1"))
        risks = list_risks(self.db, OperationalFilters(), limit=50, offset=0)
        self.assertEqual(risks.total, len(event_rows))
        self.assertTrue(all(item.requires_human_review for item in risks.items))

    def test_reviews_append_history_and_filter_events_without_overwriting_evidence(self):
        self._seed_rule_analyses()
        with patch.dict(os.environ, {"OPERATIONS_FRAME_CONCENTRATION_MIN": "8"}):
            refresh_rule_events(self.db, OperationalFilters())
        event = self.db.scalar(select(OperationalEvent).order_by(OperationalEvent.event_id).limit(1))
        title, ids = event.title, list(event.related_detection_ids)
        reviewed = add_review(self.db, event.event_id, "reviewed", "operator-1", "Checked the referenced frames.")
        self.assertEqual(reviewed.review_status, "reviewed")
        self.assertEqual(reviewed.reviewer_identifier, "operator-1")
        add_review(self.db, event.event_id, "dismissed", "operator-2", "No further action.")
        dismissed = list_events(self.db, OperationalFilters(), limit=100, offset=0, review_status="dismissed")
        self.assertTrue(any(item.event_id == event.event_id for item in dismissed.items))
        self.assertEqual(self.db.query(OperationalReview).filter_by(event_id=event.event_id).count(), 2)
        unchanged = self.db.get(OperationalEvent, event.event_id)
        self.assertEqual(unchanged.title, title)
        self.assertEqual(unchanged.related_detection_ids, ids)

    def test_rule_recommendations_do_not_require_ai_and_use_saved_ai_when_present(self):
        self._seed_rule_analyses()
        with patch.dict(os.environ, {"OPERATIONS_FRAME_CONCENTRATION_MIN": "8"}):
            refresh_rule_events(self.db, OperationalFilters())
        rule_recs = get_recommendations(self.db, OperationalFilters(), limit=100, offset=0)
        self.assertGreater(rule_recs.total, 0)
        self.assertTrue(any(item.source == "rule_based" for item in rule_recs.items))
        self.db.add(AIReport(
            report_id="saved-report", job_id="target", model_name="qwen", summary="persisted",
            observations=[], limitations=[], evidence={},
            recommendations=[{"title": "Review recorded observation", "description": "Check the saved evidence.",
                              "reason": "Existing validated report.", "requires_human_review": True,
                              "evidence": [{"detection_id": self.db.scalar(select(Detection.detection_id).where(Detection.job_id == "target")),
                                           "track_id": None, "timestamp": 1.0, "type": "detection_or_tracking"}]}],
        ))
        self.db.commit()
        combined = get_recommendations(self.db, OperationalFilters(), limit=100, offset=0)
        self.assertTrue(any(item.source == "ai_report" and item.analysis_job_id == "target" for item in combined.items))

    def test_empty_database_returns_zero_metrics_and_capabilities_are_explicit(self):
        overview = get_overview(self.db, OperationalFilters())
        analytics = get_analytics(self.db, OperationalFilters(), limit=10, offset=0)
        events_added = refresh_rule_events(self.db, OperationalFilters())
        recommendations = get_recommendations(self.db, OperationalFilters(), limit=10, offset=0)
        availability = get_availability(self.db, OperationalFilters())
        self.assertEqual(overview.metrics.total_detected_objects, 0)
        self.assertEqual(overview.metrics.total_unique_tracked_entities, 0)
        self.assertEqual(analytics.confidence_distribution[0].detection_count, 0)
        self.assertEqual(events_added, 0)
        self.assertEqual(recommendations.items, [])
        self.assertFalse(availability.ai_report_available)
        unavailable = {item.capability: item.status for item in availability.capabilities}
        self.assertEqual(unavailable["PPE compliance"], "requires_custom_model")
        self.assertEqual(unavailable["Precise warehouse occupancy"], "not_implemented")


if __name__ == "__main__":
    unittest.main()
