"""Agent summaries and LangGraph workflow behavior over isolated SQLite data."""

import tempfile
import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aai.db.base import Base
from aai.db.models import AIReport, AgentConfig, AgentExecution, AgentWorkflow, AnalysisJob, Detection, Video
from aai.orchestration import workflow as orchestration
from aai.services.evidence_builder import build_analysis_evidence


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vanguard-agents-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name, 'agents.sqlite3').as_posix()}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.db.add(Video(id="video", original_filename="warehouse.mp4", stored_filename="stored.mp4",
                          storage_key="stored.mp4", file_size=100, content_type="video/mp4", upload_status="uploaded"))
        self.job = AnalysisJob(job_id="job", video_id="video", status="completed", progress_percentage=100,
                               current_stage="Completed", frames_processed=1, frames_sampled=1, total_frames=1,
                               detections_count=2, confidence_threshold=0.25, sampling_rate_fps=5,
                               image_size=640, tracking_enabled=True)
        self.db.add(self.job)
        self.db.add_all([
            Detection(detection_id="d1", job_id="job", frame_index=0, timestamp_seconds=0.0, class_id=0,
                      class_name="person", confidence=0.95, bounding_box=[0, 0, 10, 10], track_id=5),
            Detection(detection_id="d2", job_id="job", frame_index=1, timestamp_seconds=0.2, class_id=0,
                      class_name="person", confidence=0.8, bounding_box=[1, 1, 11, 11], track_id=5),
        ])
        self.workflow = AgentWorkflow(workflow_id="wf", job_id="job", status="pending", agent_execution_history=[])
        self.db.add(self.workflow)
        for seq, agent in enumerate(orchestration.SEQUENCE):
            self.db.add(AgentExecution(execution_id=f"ex-{agent}", workflow_id="wf", job_id="job",
                                       agent_id=agent, sequence=seq, status="pending"))
        self.db.commit()

    async def asyncTearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    async def _run(self, enabled, evidence=None):
        evidence = evidence or build_analysis_evidence(self.db, self.job)
        # Release SQLite's read transaction before the graph opens write sessions.
        self.db.close()
        state = {
            "workflow_id": "wf", "job_id": "job", "execution_status": "pending", "current_agent": None,
            "detection_summary": None, "tracking_summary": None, "evidence_references": [],
            "reasoning_result": None, "operations_result": None,
            "validation_result": {"valid": True}, "agent_execution_history": [], "error_details": None,
            "started_at": None, "completed_at": None,
            "evidence": evidence, "enabled_agents": enabled,
        }
        with patch.object(orchestration, "SessionLocal", self.Session):
            return await asyncio.wait_for(orchestration.build_workflow_graph().ainvoke(state), timeout=8)

    async def test_sequential_graph_summarizes_only_persisted_records(self):
        result = await self._run(["vision", "tracking", "operations"])
        self.assertEqual(result["detection_summary"]["detection_count"], 2)
        self.assertEqual(result["tracking_summary"]["unique_track_count"], 1)
        self.assertIsNone(result["reasoning_result"])
        self.assertEqual(result["validation_result"], {"valid": True})
        self.assertEqual([entry["agent_id"] for entry in result["agent_execution_history"]],
                         ["vision", "tracking", "operations"])
        rows = self.db.scalars(select(AgentExecution).order_by(AgentExecution.sequence)).all()
        self.assertEqual([row.status for row in rows], ["completed", "completed", "pending", "completed"])
        self.assertEqual(result["operations_result"]["risk_assessment"], "unavailable_without_dedicated_validated_models")

    async def test_disabled_optional_agents_are_conditionally_skipped(self):
        result = await self._run(["vision", "tracking"])
        self.assertIsNone(result["reasoning_result"])
        self.assertIsNone(result["operations_result"])
        self.assertEqual([entry["agent_id"] for entry in result["agent_execution_history"]],
                         ["vision", "tracking", "operations"])
        self.assertEqual(result["agent_execution_history"][-1]["status"], "skipped")

    async def test_reasoning_failure_propagates_without_claiming_completion(self):
        from aai.agents import reasoning_agent
        with patch.object(orchestration, "SessionLocal", self.Session), \
             patch.object(reasoning_agent, "run_reasoning", new=AsyncMock(side_effect=TimeoutError("local inference timeout"))):
            with self.assertRaises(TimeoutError):
                await self._run(["vision", "tracking", "reasoning", "operations"])
        row = self.db.scalar(select(AgentExecution).where(AgentExecution.agent_id == "reasoning"))
        self.assertEqual(row.status, "failed")
        workflow = self.db.get(AgentWorkflow, "wf")
        self.assertEqual(workflow.status, "failed")
        self.assertIsNone(workflow.result)

    async def test_workflow_start_is_idempotent_for_an_active_job(self):
        from fastapi import Response
        from aai.api.routes.agents import create_workflow, get_workflow
        from aai.schemas.agents import WorkflowCreate
        self.db.add(AgentConfig(agent_id="reasoning", enabled=False))
        self.db.query(AgentExecution).filter_by(workflow_id="wf").delete()
        self.db.delete(self.workflow)
        self.db.commit()
        with patch.object(orchestration, "SessionLocal", self.Session):
            first = await create_workflow(WorkflowCreate(job_id="job"), Response(), self.db)
            self.db.close()
            second = await create_workflow(WorkflowCreate(job_id="job"), Response(), self.db)
            self.assertEqual(first.workflow_id, second.workflow_id)
            self.assertEqual(self.db.query(AgentWorkflow).filter_by(job_id="job").count(), 1)
            self.db.close()
            task = __import__("aai.api.routes.agents", fromlist=["_tasks"])._tasks[first.workflow_id]
            await task
            completed = get_workflow(first.workflow_id, self.db)
            self.assertEqual(completed.status, "completed")
            self.assertIsNotNone(completed.result)

    async def test_reasoning_agent_persists_and_reuses_phase6_report(self):
        from aai.agents import reasoning_agent
        from aai.schemas.reasoning import AIReportPayload
        payload = AIReportPayload(summary="Persisted evidence summary", observations=[], recommendations=[], limitations=[])
        evidence = build_analysis_evidence(self.db, self.job)
        self.db.close()
        with patch.object(reasoning_agent, "SessionLocal", self.Session), \
             patch.object(reasoning_agent, "generate_report", new=AsyncMock(return_value=payload)) as generate:
            first = await reasoning_agent.run_reasoning("job", evidence)
            second = await reasoning_agent.run_reasoning("job", evidence)
        self.assertEqual(first["summary"], "Persisted evidence summary")
        self.assertEqual(second["summary"], "Persisted evidence summary")
        self.assertEqual(generate.await_count, 1)
        self.assertIsNotNone(self.db.scalar(select(AIReport).where(AIReport.job_id == "job")))

    async def test_pending_workflow_can_be_cancelled_before_agents_start(self):
        from fastapi import Response
        from aai.api.routes import agents as agent_routes
        from aai.schemas.agents import WorkflowCreate
        self.db.query(AgentExecution).filter_by(workflow_id="wf").delete()
        self.db.delete(self.workflow)
        self.db.add(AgentConfig(agent_id="reasoning", enabled=False))
        self.db.commit()
        with patch.object(orchestration, "SessionLocal", self.Session):
            started = await agent_routes.create_workflow(WorkflowCreate(job_id="job"), Response(), self.db)
            self.db.close()
            cancelled = agent_routes.cancel_workflow(started.workflow_id, self.db)
            self.assertEqual(cancelled.status, "cancelled")
            task = agent_routes._tasks[started.workflow_id]
            with self.assertRaises(asyncio.CancelledError):
                await task
        rows = self.db.scalars(select(AgentExecution).where(AgentExecution.workflow_id == started.workflow_id)).all()
        self.assertTrue(all(row.status == "skipped" for row in rows))


if __name__ == "__main__":
    unittest.main()
