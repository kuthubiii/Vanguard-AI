"""Focused activity-feed and safe logging tests on isolated SQLite databases."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from aai.api.routes.activity import _csv_cell, _csv_response
from aai.db.base import Base
from aai.db.models import (
    ActivityLog, AgentExecution, AgentWorkflow, AIReport, AnalysisJob,
    OperationalEvent, OperationalReview, Video,
)
from aai.schemas.activity import ActivityLogQuery
from aai.services.activity_logger import record_activity, sanitize_metadata
from aai.services.activity_queries import (
    export_activity, get_activity, get_activity_filters, list_activity, summarize_activity,
)


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vanguard-activity-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name, 'activity.sqlite3').as_posix()}")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        self.db.add(Video(id="video-a", original_filename="dock.mp4", stored_filename="stored.mp4",
                          storage_key="stored.mp4", file_size=1200, content_type="video/mp4", upload_status="uploaded",
                          created_at=now - timedelta(hours=1)))
        self.db.add(AnalysisJob(job_id="job-a", video_id="video-a", status="completed", progress_percentage=100,
                                current_stage="Completed", frames_processed=20, frames_sampled=5, detections_count=4,
                                confidence_threshold=0.25, sampling_rate_fps=5, image_size=640, tracking_enabled=True,
                                created_at=now - timedelta(minutes=20), started_at=now - timedelta(minutes=19), completed_at=now - timedelta(minutes=18)))
        self.db.add(AIReport(report_id="report-a", job_id="job-a", model_name="qwen-local", summary="summary",
                             observations=[], recommendations=[], limitations=[], evidence={}, generated_at=now - timedelta(minutes=15)))
        self.db.add(AgentWorkflow(workflow_id="workflow-a", job_id="job-a", status="completed",
                                  agent_execution_history=[], created_at=now - timedelta(minutes=10),
                                  started_at=now - timedelta(minutes=9), completed_at=now - timedelta(minutes=8)))
        self.db.add(AgentExecution(execution_id="execution-a", workflow_id="workflow-a", job_id="job-a",
                                   agent_id="vision", sequence=0, status="completed", current_task="summarize persisted detections",
                                   started_at=now - timedelta(minutes=9), completed_at=now - timedelta(minutes=8), duration_ms=123.4))
        self.db.add(OperationalEvent(event_id="ops-a", event_key="rule:v1", job_id="job-a", video_id="video-a",
                                     event_type="frame_observation_concentration", severity="informational",
                                     title="Sample frame concentration", description="Stored detections share a sampled frame.",
                                     evidence_explanation="Rule basis", rule_basis={"rule": "frame_v1"},
                                     related_detection_ids=[], occurred_at=now, created_at=now))
        self.db.add(OperationalReview(review_id="review-a", event_id="ops-a", status="reviewed",
                                      reviewer_identifier="operator", note="Checked in source video", reviewed_at=now))
        self.db.commit()
        self.created = now

    def tearDown(self):
        self.db.close(); self.engine.dispose(); self.temp.cleanup()

    def test_creation_is_idempotent_append_only_and_timezone_aware(self):
        key = "test:system:startup"
        first = record_activity(category="SYSTEM", event_type="system.application.started", title="Started",
                                description="Backend started", source="test", severity="SUCCESS",
                                status="COMPLETED", metadata={"api_key": "private", "count": 3},
                                idempotency_key=key, timestamp=self.created, bind=self.engine)
        second = record_activity(category="SYSTEM", event_type="system.application.started", title="Started",
                                 description="Backend started", source="test", severity="SUCCESS",
                                 status="COMPLETED", idempotency_key=key, timestamp=self.created, bind=self.engine)
        self.assertEqual(first, second)
        rows = self.db.scalars(select(ActivityLog).where(ActivityLog.idempotency_key == key)).all()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("api_key", rows[0].metadata_json)
        read = get_activity(self.db, first)
        self.assertIsNotNone(read)
        self.assertEqual(read.timestamp.tzinfo, timezone.utc)

    def test_taxonomy_and_failure_isolation(self):
        self.assertIsNone(record_activity(category="NOT_A_CATEGORY", event_type="invalid", title="bad",
                                          description="bad", source="test", bind=self.engine))
        self.assertIsNone(record_activity(category="SYSTEM", event_type="system.test", title="bad",
                                          description="bad", source="test", bind=object()))
        self.assertEqual(self.db.scalar(select(ActivityLog).where(ActivityLog.event_type == "invalid")), None)

    def test_existing_persisted_sources_are_projected_without_duplicate_rows(self):
        page = list_activity(self.db, ActivityLogQuery(), 100, 0)
        types = {item.event_type for item in page.items}
        self.assertIn("video.uploaded", types)
        self.assertIn("analysis.requested", types)
        self.assertIn("analysis.started", types)
        self.assertIn("analysis.completed", types)
        self.assertIn("reasoning.report.persisted", types)
        self.assertIn("workflow.created", types)
        self.assertIn("workflow.completed", types)
        self.assertIn("agent.execution.started", types)
        self.assertIn("agent.execution.completed", types)
        self.assertIn("operations.event.generated", types)
        self.assertIn("operations.event.reviewed", types)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ActivityLog)), 0)
        review = next(item for item in page.items if item.event_type == "operations.event.reviewed")
        self.assertEqual(review.operational_event_id, "ops-a")
        self.assertEqual(review.status, "COMPLETED")

    def test_filter_search_and_pagination(self):
        record_activity(category="SYSTEM", event_type="system.worker.failed", title="Worker startup failed",
                        description="Worker could not start", source="worker", severity="ERROR", status="FAILED",
                        error_type="RuntimeError", bind=self.engine)
        query = ActivityLogQuery(category="SYSTEM", severity="ERROR", search="worker")
        page = list_activity(self.db, query, 1, 0)
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0].status, "FAILED")
        self.assertEqual(page.items[0].error_type, "RuntimeError")
        self.assertEqual(list_activity(self.db, query, 1, 1).items, [])

    def test_summary_is_filter_aware_and_recent_window_is_explicit(self):
        record_activity(category="SYSTEM", event_type="system.failed", title="Failed operation",
                        description="Failure", source="test", severity="WARNING", status="FAILED", bind=self.engine)
        summary = summarize_activity(self.db, ActivityLogQuery())
        self.assertEqual(summary.recent_window_hours, 24)
        self.assertGreaterEqual(summary.total_events, 9)
        self.assertGreaterEqual(summary.successful_executions, 1)
        self.assertEqual(summary.failed_executions, 1)
        self.assertEqual(summary.warning_events, 1)

    def test_filters_and_bounded_csv_export(self):
        options = get_activity_filters(self.db)
        self.assertIn("VIDEO", options.categories)
        self.assertIn("analysis.completed", options.event_types)
        exported = export_activity(self.db, ActivityLogQuery(), limit=5)
        self.assertEqual(len(exported), 5)
        response = _csv_response(exported)
        self.assertEqual(response.media_type, "text/csv; charset=utf-8")
        self.assertIn("X-Export-Limit", response.headers)
        self.assertEqual(_csv_cell("=1+1"), "'=1+1")
        self.assertEqual(_csv_cell("safe title"), "safe title")

    def test_metadata_redacts_private_keys_and_bounds_values(self):
        clean = sanitize_metadata({"prompt": "private", "nested": {"access_token": "private", "duration": 3},
                                   "label": "x" * 900})
        self.assertNotIn("prompt", clean)
        self.assertNotIn("access_token", clean["nested"])
        self.assertEqual(clean["nested"]["duration"], 3)
        self.assertEqual(len(clean["label"]), 512)


if __name__ == "__main__":
    unittest.main()
