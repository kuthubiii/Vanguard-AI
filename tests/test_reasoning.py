"""Unit coverage for local reasoning evidence and failure handling."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aai.db.base import Base
from aai.db.models import AIReport, AnalysisJob, Detection, Video
from aai.schemas.reasoning import AIReportPayload, AskAIRequest
from aai.services.evidence_builder import build_analysis_evidence
from aai.api.routes import reasoning as reasoning_routes
from aai.services.lm_studio_client import (
    LMStudioConnectionError, LMStudioEmptyResponse, LMStudioError, LMStudioInvalidResponse, LMStudioModelMissing,
    LMStudioRequestError, LMStudioTimeout, _chat_completion,
    _base_url, _list_models, chat_json, get_status,
)
from aai.services import reasoning_service
from aai.services.reasoning_service import AIEvidenceValidationError, AISchemaValidationError
from aai.schemas.reasoning import EvidenceReference


def run_immediate(coroutine):
    """Drive mocked async paths without creating a Windows event loop/socket pair."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    coroutine.close()
    raise AssertionError("Mocked coroutine unexpectedly suspended on I/O")


class ReasoningTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vanguard-reasoning-test-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name, 'test.sqlite3').as_posix()}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.Session()
        self.session.add(Video(
            id="video-test", original_filename="warehouse.mp4", stored_filename="stored.mp4",
            storage_key="stored.mp4", file_size=100, content_type="video/mp4", upload_status="uploaded",
        ))
        self.job = AnalysisJob(
            job_id="job-test", video_id="video-test", status="completed", progress_percentage=100,
            current_stage="Completed", frames_processed=24, frames_sampled=24, total_frames=240,
            detections_count=2, confidence_threshold=0.25, sampling_rate_fps=5.0,
            image_size=640, tracking_enabled=True,
        )
        self.session.add(self.job)
        self.session.add_all([
            Detection(detection_id="det-1", job_id="job-test", frame_index=0, timestamp_seconds=0.0,
                      class_id=0, class_name="person", confidence=0.9, bounding_box=[1, 2, 30, 40], track_id=4),
            Detection(detection_id="det-2", job_id="job-test", frame_index=12, timestamp_seconds=1.2,
                      class_id=0, class_name="person", confidence=0.8, bounding_box=[2, 3, 31, 41], track_id=4),
        ])
        self.session.commit()
        # AI service tests run against their isolated database, never the configured project DB.
        from aai.services import reasoning_service
        self.activity_patch = patch.object(reasoning_service, "record_activity", return_value="test-activity")
        self.activity_patch.start()

    def tearDown(self):
        if hasattr(self, "activity_patch"):
            self.activity_patch.stop()
        self.session.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_evidence_is_aggregated_and_traceable(self):
        evidence = build_analysis_evidence(self.session, self.job)
        self.assertEqual(evidence["analysis_job_id"], "job-test")
        self.assertEqual(evidence["video_filename"], "warehouse.mp4")
        self.assertEqual(evidence["detection_count"], 2)
        self.assertEqual(sum(item["count"] for item in evidence["confidence_distribution"]), 2)
        self.assertEqual(evidence["class_counts"][0]["count"], 2)
        self.assertEqual(evidence["unique_track_count"], 1)
        self.assertEqual(evidence["tracks"][0]["track_id"], 4)
        self.assertEqual({row["detection_id"] for row in evidence["representative_detections"]}, {"det-1", "det-2"})
        self.assertIsNone(evidence["video_duration_seconds"])
        self.assertIsNone(evidence["original_fps"])

    def test_empty_detections_are_reported_as_empty_evidence(self):
        self.session.query(Detection).filter(Detection.job_id == "job-test").delete()
        self.session.commit()
        evidence = build_analysis_evidence(self.session, self.job)
        self.assertEqual(evidence["detection_count"], 0)
        self.assertEqual(evidence["unique_track_count"], 0)
        self.assertEqual(evidence["tracks"], [])
        self.assertEqual(evidence["representative_detections"], [])

    def test_large_evidence_sample_is_bounded_and_deterministic(self):
        self.session.add_all([
            Detection(detection_id=f"bulk-{i:03d}", job_id="job-test", frame_index=i + 20,
                      timestamp_seconds=float(i + 2), class_id=2, class_name="car", confidence=0.6,
                      bounding_box=[1, 1, 2, 2], track_id=None)
            for i in range(120)
        ])
        self.session.commit()
        first = build_analysis_evidence(self.session, self.job)
        second = build_analysis_evidence(self.session, self.job)
        self.assertEqual(len(first["representative_detections"]), 25)
        self.assertEqual(first["representative_detections"], second["representative_detections"])
        self.assertEqual(first["detection_count"], 122)

    def test_selection_is_category_diverse_and_full_dataset_stats_remain_complete(self):
        self.session.add_all([
            Detection(
                detection_id=f"mix-{i:03d}", job_id="job-test", frame_index=i + 20,
                timestamp_seconds=float(i + 2), class_id=i % 4, class_name=f"class-{i % 4}",
                confidence=0.5 + ((i * 7) % 49) / 100,
                bounding_box=[1, 1, 2, 2], track_id=i % 60,
            )
            for i in range(240)
        ])
        self.session.commit()
        first = build_analysis_evidence(self.session, self.job)
        second = build_analysis_evidence(self.session, self.job)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first["tracks"]), 50)
        self.assertLessEqual(len(first["representative_detections"]), 25)
        self.assertEqual({item["class_id"] for item in first["tracks"]}, {0, 1, 2, 3})
        self.assertEqual({item["class_id"] for item in first["representative_detections"]}, {0, 1, 2, 3})
        self.assertGreater(len({item["timestamp_seconds"] for item in first["representative_detections"]}), 4)
        self.assertEqual(first["detection_count"], 242)
        self.assertEqual(sum(item["count"] for item in first["class_counts"]), 242)
        self.assertEqual(first["unique_track_count"], 60)
        self.assertEqual(first["tracked_detection_count"], 242)
        self.assertEqual(sum(item["unique_tracks"] for item in first["track_class_counts"]), 60)
        self.assertEqual(first["statistics_scope"], "full_persisted_dataset")
        self.assertEqual(first["evidence_selection"]["representative_detections_included"],
                         len(first["representative_detections"]))
        prompt = reasoning_service._report_user_message(first)
        self.assertLess(len(prompt), 14000)
        self.assertNotIn("bounding_box", prompt)

    def test_selected_high_confidence_detection_is_real_and_referenceable(self):
        self.session.add_all([
            Detection(detection_id=f"confidence-{i}", job_id="job-test", frame_index=i + 20,
                      timestamp_seconds=float(i + 2), class_id=1, class_name="forklift",
                      confidence=0.99 if i == 7 else 0.3, bounding_box=[1, 1, 2, 2], track_id=50 + i)
            for i in range(40)
        ])
        self.session.commit()
        evidence = build_analysis_evidence(self.session, self.job)
        selected = {item["detection_id"]: item for item in evidence["representative_detections"]}
        self.assertIn("confidence-7", selected)
        self.assertEqual(selected["confidence-7"]["confidence"], 0.99)
        self.assertNotIn("bounding_box", selected["confidence-7"])
        self.assertNotIn("frame_index", selected["confidence-7"])

    def test_missing_and_incomplete_jobs_are_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as missing:
            reasoning_routes._get_completed_job(self.session, "missing-job")
        self.assertEqual(missing.exception.status_code, 404)
        self.job.status = "running"
        self.session.commit()
        with self.assertRaises(HTTPException) as incomplete:
            reasoning_routes._get_completed_job(self.session, "job-test")
        self.assertEqual(incomplete.exception.status_code, 409)

    def test_summary_generation_persists_and_reuses_existing_report(self):
        payload = AIReportPayload(summary="Persisted summary", observations=[], recommendations=[], limitations=[])
        generate = AsyncMock(return_value=payload)
        with patch.object(reasoning_routes, "generate_report", new=generate):
            first = run_immediate(reasoning_routes.create_ai_summary("job-test", False, self.session))
            second = run_immediate(reasoning_routes.create_ai_summary("job-test", False, self.session))
        self.assertEqual(first.summary, "Persisted summary")
        self.assertEqual(second.analysis_job_id, "job-test")
        generate.assert_awaited_once()
        self.assertEqual(self.session.query(AIReport).filter_by(job_id="job-test").count(), 1)

    def test_failed_inference_does_not_persist_report_or_change_detections(self):
        from fastapi import HTTPException
        before_detections = self.session.query(Detection).filter_by(job_id="job-test").count()
        with patch.object(reasoning_routes, "generate_report", new=AsyncMock(side_effect=LMStudioTimeout("timeout"))):
            with self.assertRaises(HTTPException) as caught:
                run_immediate(reasoning_routes.create_ai_summary("job-test", False, self.session))
        self.assertEqual(caught.exception.status_code, 504)
        self.assertEqual(caught.exception.detail["code"], "lm_studio_timeout")
        self.assertEqual(self.session.query(AIReport).filter_by(job_id="job-test").count(), 0)
        self.assertEqual(self.session.query(Detection).filter_by(job_id="job-test").count(), before_detections)

    def test_question_answering_uses_selected_job_evidence(self):
        from aai.schemas.reasoning import AskAIResponse
        expected = AskAIResponse(analysis_job_id="job-test", answer="One person class was recorded.",
                                 evidence=[], limitations=[], generated_by="qwen3-4b-instruct-2507")
        with patch.object(reasoning_routes, "answer_question", new=AsyncMock(return_value=expected)):
            result = run_immediate(reasoning_routes.ask_ai("job-test", AskAIRequest(question="What was detected?"), self.session))
        self.assertEqual(result.answer, expected.answer)
        self.assertEqual(result.analysis_job_id, "job-test")

    def test_ai_report_persists_without_changing_detection_rows(self):
        before = self.session.query(Detection).filter_by(job_id="job-test").count()
        report = AIReport(
            report_id="report-test", job_id="job-test", model_name="qwen3-4b-instruct-2507", summary="Evidence summary.",
            observations=[], recommendations=[], limitations=["No video duration stored."],
            evidence=build_analysis_evidence(self.session, self.job), generated_at=datetime.now(timezone.utc),
        )
        self.session.add(report)
        self.session.commit()
        self.assertEqual(self.session.scalar(select(AIReport).where(AIReport.job_id == "job-test")).summary, "Evidence summary.")
        self.assertEqual(self.session.query(Detection).filter_by(job_id="job-test").count(), before)


class LMStudioClientTests(unittest.TestCase):
    def test_models_endpoint_parses_openai_compatible_model_ids(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"object": "list", "data": [{"id": "qwen3-4b-instruct-2507", "object": "model"}]}

        class FakeClient:
            url = None

            async def get(self, url):
                self.url = url
                return FakeResponse()

        client = FakeClient()
        with patch.dict(os.environ, {"LM_STUDIO_BASE_URL": "http://127.0.0.1:1235/v1"}):
            ids = run_immediate(_list_models(client))
        self.assertEqual(ids, ["qwen3-4b-instruct-2507"])
        self.assertEqual(client.url, "http://127.0.0.1:1235/v1/models")

    def test_models_endpoint_rejects_malformed_response(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"models": [{"name": "wrong-shape"}]}

        class FakeClient:
            async def get(self, _url):
                return FakeResponse()

        with self.assertRaises(LMStudioInvalidResponse):
            run_immediate(_list_models(FakeClient()))

    def test_external_base_urls_are_rejected(self):
        with patch.dict(os.environ, {"LM_STUDIO_BASE_URL": "https://example.com/v1"}):
            with self.assertRaises(LMStudioError):
                _base_url()

    def test_connection_and_model_availability(self):
        with patch.dict(os.environ, {"LM_STUDIO_MODEL": "qwen3-4b-instruct-2507"}), \
                patch("aai.services.lm_studio_client._fetch_model_ids", new=AsyncMock(return_value=["qwen3-4b-instruct-2507"])), \
                patch("aai.services.lm_studio_client._probe_inference", new=AsyncMock()):
            status = run_immediate(get_status())
        self.assertTrue(status["connected"])
        self.assertTrue(status["model_available"])
        self.assertTrue(status["inference_ready"])
        self.assertEqual(status["provider"], "lm_studio")
        self.assertEqual(status["model"], "qwen3-4b-instruct-2507")
        self.assertEqual(status["status"], "ready")

    def test_missing_model(self):
        probe = AsyncMock()
        with patch("aai.services.lm_studio_client._fetch_model_ids", new=AsyncMock(return_value=["other:latest"])), \
                patch("aai.services.lm_studio_client._probe_inference", new=probe):
            status = run_immediate(get_status())
        self.assertTrue(status["connected"])
        self.assertFalse(status["model_available"])
        self.assertEqual(status["status"], "model_missing")
        probe.assert_not_awaited()

    def test_unavailable_service(self):
        with patch("aai.services.lm_studio_client._fetch_model_ids", new=AsyncMock(side_effect=LMStudioError("offline"))):
            status = run_immediate(get_status())
        self.assertFalse(status["connected"])
        self.assertFalse(status["inference_ready"])
        self.assertEqual(status["status"], "unavailable")

    def test_model_listing_alone_does_not_report_inference_ready(self):
        with patch("aai.services.lm_studio_client._fetch_model_ids", new=AsyncMock(return_value=["qwen3-4b-instruct-2507"])), \
                patch("aai.services.lm_studio_client._probe_inference", new=AsyncMock(side_effect=LMStudioError("not loaded"))):
            status = run_immediate(get_status())
        self.assertTrue(status["connected"])
        self.assertTrue(status["model_available"])
        self.assertFalse(status["inference_ready"])
        self.assertEqual(status["status"], "inference_unready")

    def test_chat_uses_openai_compatible_response_and_exact_model_id(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {
                    "role": "assistant", "content": 'Here is the result: ```json\n{"summary":"ok"}\n```',
                    "reasoning_content": "private reasoning is ignored",
                }}]}

        class FakeClient:
            def __init__(self):
                self.request = None
                self.url = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, url, *, json):
                self.url = url
                self.request = json
                return FakeResponse()

        fake = FakeClient()
        with patch("aai.services.lm_studio_client.httpx.AsyncClient", return_value=fake), patch(
            "aai.services.lm_studio_client._list_models", new=AsyncMock(return_value=["qwen3-4b-instruct-2507"])
        ):
            result = run_immediate(chat_json("system", "evidence", max_tokens=300))
        self.assertEqual(result, 'Here is the result: ```json\n{"summary":"ok"}\n```')
        self.assertEqual(fake.request["model"], "qwen3-4b-instruct-2507")
        self.assertEqual(fake.url, "http://127.0.0.1:1235/v1/chat/completions")
        self.assertNotIn("response_format", fake.request)
        self.assertEqual(fake.request["temperature"], 0.0)
        self.assertEqual(fake.request["max_tokens"], 300)
        self.assertFalse(fake.request["stream"])

    def test_reasoning_only_message_is_classified_as_empty(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "", "reasoning_content": "not final"}}]}

        class FakeClient:
            async def post(self, *_args, **_kwargs):
                return FakeResponse()

        with self.assertRaises(LMStudioEmptyResponse):
            run_immediate(_chat_completion(FakeClient(), [{"role": "user", "content": "test"}], max_tokens=2))

    def test_http_status_error_is_not_mapped_to_connection_error(self):
        import httpx

        class FakeResponse:
            status_code = 400

            def raise_for_status(self):
                request = httpx.Request("POST", "http://127.0.0.1:1235/v1/chat/completions")
                response = httpx.Response(400, request=request)
                raise httpx.HTTPStatusError("bad request", request=request, response=response)

        class FakeClient:
            async def post(self, *_args, **_kwargs):
                return FakeResponse()

        with self.assertRaises(LMStudioRequestError) as caught:
            run_immediate(_chat_completion(FakeClient(), [{"role": "user", "content": "test"}], max_tokens=2))
        self.assertIsInstance(caught.exception.__cause__, httpx.HTTPStatusError)

    def test_chat_read_timeout_is_classified_and_keeps_original_cause(self):
        import httpx

        class FakeClient:
            async def post(self, *_args, **_kwargs):
                raise httpx.ReadTimeout("model response was idle")

        with self.assertLogs("aai.services.lm_studio_client", level="WARNING") as logs:
            with self.assertRaises(LMStudioTimeout) as caught:
                run_immediate(_chat_completion(FakeClient(), [{"role": "user", "content": "test"}],
                                               max_tokens=900, job_id="job-timeout"))
        self.assertIsInstance(caught.exception.__cause__, httpx.ReadTimeout)
        self.assertIn("stage=chat_completion", logs.output[0])
        self.assertIn("job_id=job-timeout", logs.output[0])
        self.assertIn("exception_class=ReadTimeout", logs.output[0])

    def test_reasoning_error_categories_map_without_generic_unavailable(self):
        from fastapi import HTTPException
        for error, expected_status, expected_code in (
            (LMStudioConnectionError("offline"), 503, "lm_studio_connection_error"),
            (LMStudioRequestError("bad request"), 502, "lm_studio_request_error"),
            (LMStudioTimeout("timeout"), 504, "lm_studio_timeout"),
            (LMStudioInvalidResponse("bad envelope"), 502, "lm_studio_response_parse_error"),
            (LMStudioEmptyResponse("empty"), 502, "lm_studio_empty_response"),
        ):
            with self.subTest(code=expected_code), self.assertRaises(HTTPException) as caught:
                reasoning_routes._raise_reasoning_failure(error)
            self.assertEqual(caught.exception.status_code, expected_status)
            self.assertEqual(caught.exception.detail["code"], expected_code)


class ReasoningServiceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            "analysis_job_id": "job-test", "detection_count": 1,
            "representative_detections": [{"detection_id": "det-1", "track_id": 4, "timestamp_seconds": 1.25,
                                           "class_id": 0, "class_name": "person"}],
        }
        self.activity_patch = patch.object(reasoning_service, "record_activity", return_value="test-activity")
        self.activity_patch.start()

    def tearDown(self):
        self.activity_patch.stop()

    def test_valid_report_response(self):
        payload = {"summary": "One person detection was persisted.", "observations": [], "recommendations": [], "limitations": []}
        call = AsyncMock(return_value=json.dumps(payload))
        with patch.object(reasoning_service, "chat_json", new=call):
            result = run_immediate(reasoning_service.generate_report(self.evidence))
        self.assertIsInstance(result, AIReportPayload)
        self.assertEqual(result.summary, payload["summary"])
        self.assertEqual(call.await_args.kwargs["max_tokens"], 900)
        self.assertEqual(call.await_args.kwargs["job_id"], "job-test")
        self.assertIn("full-dataset aggregates", call.await_args.args[1])
        self.assertIn("selected samples only", call.await_args.args[1])
        self.assertNotIn("bounding_box", call.await_args.args[1])

    def test_reference_requires_matching_detection_track_timestamp_and_class(self):
        good = EvidenceReference(type="detection_or_tracking", detection_id="det-1", track_id=4,
                                 timestamp=1.25, class_id=0, class_name="person")
        reasoning_service._validate_references([[good]], self.evidence)
        mismatches = (
            EvidenceReference(type="detection_or_tracking", detection_id="missing", track_id=4,
                              timestamp=1.25, class_id=0, class_name="person"),
            EvidenceReference(type="detection_or_tracking", detection_id="det-1", track_id=99,
                              timestamp=1.25, class_id=0, class_name="person"),
            EvidenceReference(type="detection_or_tracking", detection_id="det-1", track_id=4,
                              timestamp=1.26, class_id=0, class_name="person"),
            EvidenceReference(type="detection_or_tracking", detection_id="det-1", track_id=4,
                              timestamp=1.25, class_id=1, class_name="person"),
            EvidenceReference(type="detection_or_tracking", detection_id="det-1", track_id=4,
                              timestamp=1.25, class_id=0, class_name="forklift"),
            EvidenceReference(type="detection_or_tracking", detection_id="det-1", track_id=4,
                              timestamp=1.25),
        )
        for reference in mismatches:
            with self.subTest(reference=reference), self.assertRaises(AIEvidenceValidationError):
                reasoning_service._validate_references([[reference]], self.evidence)

    def test_prompt_serialization_is_compact_and_preserves_sample_scope(self):
        evidence = {
            **self.evidence,
            "statistics_scope": "full_persisted_dataset",
            "evidence_selection": {"scope_note": "Aggregate statistics cover the full persisted dataset; arrays are selected samples."},
            "tracks": [],
        }
        user = reasoning_service._report_user_message(evidence)
        self.assertIn("never present sample sizes as dataset totals", user)
        self.assertIn('"detection_id":"det-1"', user)
        self.assertNotIn("bounding_box", user)
        self.assertLess(len(user), 1800)

    def test_invalid_json_is_rejected(self):
        with patch.object(reasoning_service, "chat_json", new=AsyncMock(return_value="not json")):
            with self.assertRaises(LMStudioInvalidResponse):
                run_immediate(reasoning_service.generate_report(self.evidence))

    def test_schema_error_has_distinct_category(self):
        with patch.object(reasoning_service, "chat_json", new=AsyncMock(return_value=json.dumps({"summary": ["wrong type"]}))):
            with self.assertRaises(AISchemaValidationError) as caught:
                run_immediate(reasoning_service.generate_report(self.evidence))
        self.assertEqual(caught.exception.code, "ai_schema_validation_error")
        self.assertEqual(type(caught.exception.__cause__).__name__, "ValidationError")

    def test_json_embedded_in_qwen_prose_is_extracted_from_final_content(self):
        raw = 'Reasoning text that is not used as data.\n```json\n{"summary":"Grounded summary"}\n```'
        with patch.object(reasoning_service, "chat_json", new=AsyncMock(return_value=raw)):
            result = run_immediate(reasoning_service.generate_report(self.evidence))
        self.assertEqual(result.summary, "Grounded summary")

    def test_fabricated_evidence_reference_is_rejected(self):
        payload = {"summary": "Summary", "observations": [{"title": "Unsupported", "description": "Claim",
                   "evidence": [{"type": "detection_or_tracking", "track_id": 88, "timestamp": 9,
                                 "detection_id": "invented", "class_id": 1, "class_name": "forklift"}],
                   "confidence": "low", "limitations": "None"}],
                   "recommendations": [], "limitations": []}
        with patch.object(reasoning_service, "chat_json", new=AsyncMock(return_value=json.dumps(payload))):
            with self.assertRaises(AIEvidenceValidationError):
                run_immediate(reasoning_service.generate_report(self.evidence))

    def test_recommendation_without_evidence_is_rejected(self):
        payload = {"summary": "Summary", "observations": [], "recommendations": [{
            "title": "Take action", "description": "Do something", "reason": "Because",
            "evidence": [], "requires_human_review": True,
        }], "limitations": []}
        with patch.object(reasoning_service, "chat_json", new=AsyncMock(return_value=json.dumps(payload))):
            with self.assertRaises(AIEvidenceValidationError) as caught:
                run_immediate(reasoning_service.generate_report(self.evidence))
        self.assertEqual(caught.exception.code, "ai_evidence_validation_error")

    def test_timeout_and_unavailable_errors_propagate(self):
        for failure in (LMStudioTimeout("timeout"), LMStudioError("unavailable"), LMStudioModelMissing("missing")):
            with self.subTest(failure=failure), patch.object(reasoning_service, "chat_json", new=AsyncMock(side_effect=failure)):
                with self.assertRaises(type(failure)):
                    run_immediate(reasoning_service.generate_report(self.evidence))


if __name__ == "__main__":
    unittest.main()
