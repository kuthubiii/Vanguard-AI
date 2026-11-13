"""Explicit sequential LangGraph for persisted warehouse analysis evidence."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from time import perf_counter
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from sqlalchemy import select

from aai.agents.operations_agent import summarize_operations
from aai.agents.tracking_agent import summarize_tracking
from aai.agents.vision_agent import summarize_vision
from aai.db.models import AgentExecution, AgentWorkflow
from aai.db.session import SessionLocal
from aai.orchestration.state import WorkflowState
from aai.services.evidence_builder import valid_evidence_records

logger = logging.getLogger(__name__)
SEQUENCE = ("vision", "tracking", "reasoning", "operations")


def build_workflow_graph():
    graph = StateGraph(WorkflowState)

    def persist(workflow_id: str, agent_id: str, status: str, task: str | None = None,
                result: dict | None = None, error: dict | None = None,
                start: datetime | None = None, duration_ms: float | None = None) -> None:
        with SessionLocal() as db:
            execution = db.scalar(select(AgentExecution).where(
                AgentExecution.workflow_id == workflow_id, AgentExecution.agent_id == agent_id
            ))
            if execution:
                execution.status = status
                execution.current_task = task
                if result is not None:
                    execution.result = result
                    execution.result_summary = _summary(agent_id, result)
                if error is not None:
                    execution.error_details = error
                if start is not None:
                    execution.started_at = start
                if status in {"completed", "failed", "skipped"}:
                    execution.completed_at = datetime.now(timezone.utc)
                    execution.duration_ms = duration_ms
                db.commit()

    def start_node(agent_id: str, task: str, worker: Callable):
        async def node(state: WorkflowState) -> dict:
            if agent_id not in state["enabled_agents"]:
                persist(state["workflow_id"], agent_id, "skipped", "Disabled in agent registry")
                entry = {"agent_id": agent_id, "status": "skipped", "current_task": "Disabled in agent registry"}
                return {"agent_execution_history": state["agent_execution_history"] + [entry],
                        "current_agent": agent_id, "execution_status": "running"}
            began = datetime.now(timezone.utc)
            tick = perf_counter()
            with SessionLocal() as db:
                workflow = db.get(AgentWorkflow, state["workflow_id"])
                if workflow:
                    workflow.status = "running"
                    workflow.current_agent = agent_id
                    db.commit()
            persist(state["workflow_id"], agent_id, "running", task, start=began)
            try:
                result = await worker(state)
                elapsed = (perf_counter() - tick) * 1000
                persist(state["workflow_id"], agent_id, "completed", task, result=result, start=began, duration_ms=elapsed)
                entry = {"agent_id": agent_id, "status": "completed", "current_task": task,
                         "started_at": began, "completed_at": datetime.now(timezone.utc),
                         "duration_ms": elapsed, "result_summary": _summary(agent_id, result)}
                update: dict[str, Any] = {"agent_execution_history": state["agent_execution_history"] + [entry],
                                          "current_agent": agent_id, "execution_status": "running"}
                if agent_id == "vision":
                    update.update(detection_summary=result, evidence_references=result["evidence_references"])
                elif agent_id == "tracking":
                    update["tracking_summary"] = result
                elif agent_id == "reasoning":
                    update["reasoning_result"] = result
                    update["validation_result"] = {"valid": True, "evidence_reference_count": _reference_count(result)}
                elif agent_id == "operations":
                    update["operations_result"] = result
                return update
            except Exception as error:
                details = {"code": getattr(error, "code", "agent_execution_failed"),
                           "message": _safe_error(error), "exception_class": type(error).__name__}
                persist(state["workflow_id"], agent_id, "failed", task, error=details, start=began,
                        duration_ms=(perf_counter() - tick) * 1000)
                entry = {"agent_id": agent_id, "status": "failed", "current_task": task,
                         "started_at": began, "completed_at": datetime.now(timezone.utc),
                         "duration_ms": (perf_counter() - tick) * 1000, "error_details": details}
                with SessionLocal() as db:
                    workflow = db.get(AgentWorkflow, state["workflow_id"])
                    if workflow:
                        workflow.status = "failed"
                        workflow.error_details = details
                        workflow.agent_execution_history = _json_history(state["agent_execution_history"] + [entry])
                        workflow.completed_at = datetime.now(timezone.utc)
                        db.commit()
                raise
        return node

    async def vision(state: WorkflowState):
        return summarize_vision(state["evidence"])

    async def tracking(state: WorkflowState):
        return summarize_tracking(state["evidence"])

    async def reasoning(state: WorkflowState):
        from aai.agents.reasoning_agent import run_reasoning
        return await run_reasoning(state["job_id"], state["evidence"])

    async def operations(state: WorkflowState):
        result = summarize_operations(state["detection_summary"] or _empty_vision(state["evidence"]),
                                      state["tracking_summary"] or summarize_tracking(state["evidence"]),
                                      state["reasoning_result"])
        available = set(valid_evidence_records(state["evidence"]))
        if not set(result["evidence_references"]).issubset(available):
            raise ValueError("Operations output cited evidence that is not present in the persisted analysis sample.")
        return result

    workers = {"vision": vision, "tracking": tracking, "reasoning": reasoning, "operations": operations}
    tasks = {"vision": "Summarize persisted YOLO detections", "tracking": "Summarize persisted track associations",
             "reasoning": "Generate and persist validated local AI report", "operations": "Prepare conservative operational summary"}
    for agent_id in SEQUENCE:
        graph.add_node(agent_id, start_node(agent_id, tasks[agent_id], workers[agent_id]))
    graph.set_entry_point("vision")
    def after_vision(state: WorkflowState) -> str:
        return "tracking" if "tracking" in state["enabled_agents"] else "reasoning"
    def after_tracking(state: WorkflowState) -> str:
        return "reasoning" if "reasoning" in state["enabled_agents"] else "operations"
    def after_reasoning(_state: WorkflowState) -> str:
        return "operations"
    graph.add_conditional_edges("vision", after_vision, {"tracking": "tracking", "reasoning": "reasoning"})
    graph.add_conditional_edges("tracking", after_tracking, {"reasoning": "reasoning", "operations": "operations"})
    graph.add_conditional_edges("reasoning", after_reasoning, {"operations": "operations"})
    graph.add_edge("operations", END)
    return graph.compile()


def _empty_vision(evidence: dict) -> dict:
    return summarize_vision(evidence)


def _reference_count(report: dict) -> int:
    return sum(len(item.get("evidence", [])) for item in report.get("observations", []) + report.get("recommendations", []))


def _summary(agent_id: str, result: dict) -> str:
    if agent_id == "vision":
        return f"{result['detection_count']} detections across {len(result['class_counts'])} categories"
    if agent_id == "tracking":
        return f"{result['unique_track_count']} unique tracks from {result['tracked_detection_count']} tracked detections"
    if agent_id == "reasoning":
        return result["summary"][:500]
    return f"{len(result['observations'])} observations; risk assessment: {result['risk_assessment']}"


def _safe_error(error: Exception) -> str:
    # Exception messages from configured local services are user-readable; avoid
    # returning URLs, prompts, or provider response bodies in workflow status.
    code = getattr(error, "code", "")
    if str(code).startswith("lm_studio"):
        return str(error)
    return "Agent execution failed. Check the backend log for the stage and exception class."


def _json_history(entries: list[dict]) -> list[dict]:
    return [{key: value.isoformat() if isinstance(value, (datetime, date)) else value
             for key, value in entry.items()} for entry in entries]


async def execute_workflow(initial_state: WorkflowState) -> dict:
    started = datetime.now(timezone.utc)
    with SessionLocal() as db:
        workflow = db.get(AgentWorkflow, initial_state["workflow_id"])
        if workflow:
            workflow.status = "validating"
            workflow.started_at = started
            db.commit()
    try:
        final_state = await build_workflow_graph().ainvoke(initial_state)
        with SessionLocal() as db:
            workflow = db.get(AgentWorkflow, initial_state["workflow_id"])
            if workflow:
                workflow.status = "completed"
                workflow.current_agent = None
                workflow.agent_execution_history = _json_history(final_state["agent_execution_history"])
                workflow.result = {
                    "detection_summary": final_state["detection_summary"],
                    "tracking_summary": final_state["tracking_summary"],
                    "reasoning_result": final_state["reasoning_result"],
                    "operations_result": final_state["operations_result"],
                    "validation_result": final_state["validation_result"],
                    "evidence_references": final_state["evidence_references"],
                }
                workflow.completed_at = datetime.now(timezone.utc)
                db.commit()
        return final_state
    except Exception as error:
        logger.exception("AGENT_WORKFLOW_FAILED workflow_id=%s job_id=%s exception_class=%s",
                         initial_state["workflow_id"], initial_state["job_id"], type(error).__name__)
        with SessionLocal() as db:
            workflow = db.get(AgentWorkflow, initial_state["workflow_id"])
            if workflow and workflow.status not in {"failed", "cancelled"}:
                workflow.status = "failed"
                workflow.error_details = {"code": getattr(error, "code", "workflow_failed"),
                                          "message": _safe_error(error), "exception_class": type(error).__name__}
                workflow.completed_at = datetime.now(timezone.utc)
                db.commit()
        raise


def ensure_evidence_valid(evidence: dict) -> dict:
    records = valid_evidence_records(evidence)
    return {"valid": True, "selected_detection_count": len(records),
            "track_ids": sorted({item["track_id"] for item in records.values() if item["track_id"] is not None})}


def recover_interrupted_workflows() -> int:
    """Mark persisted in-flight workflows failed after an application restart."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        interrupted = db.scalars(select(AgentWorkflow).where(
            AgentWorkflow.status.in_(["pending", "validating", "running"]))).all()
        for workflow in interrupted:
            workflow.status = "failed"
            workflow.current_agent = None
            workflow.error_details = {
                "code": "workflow_interrupted_by_restart",
                "message": "The application restarted before this workflow completed.",
            }
            workflow.completed_at = now
            for execution in db.scalars(select(AgentExecution).where(
                AgentExecution.workflow_id == workflow.workflow_id,
                AgentExecution.status.in_(["pending", "running"]))).all():
                execution.status = "failed" if execution.started_at else "skipped"
                execution.error_details = workflow.error_details if execution.status == "failed" else None
                execution.completed_at = now
        db.commit()
        if interrupted:
            logger.warning("AGENT_WORKFLOW_RECOVERY interrupted_count=%d", len(interrupted))
        return len(interrupted)
