"""Agent registry and durable multi-agent workflow APIs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aai.agents.registry import AGENTS, AGENT_BY_ID
from aai.db.models import AgentConfig, AgentExecution, AgentWorkflow, AnalysisJob
from aai.db.session import SessionLocal, get_db
from aai.orchestration.workflow import SEQUENCE, ensure_evidence_valid, execute_workflow
from aai.schemas.agents import AgentEnableRequest, AgentExecutionRead, AgentRead, WorkflowCreate, WorkflowRead
from aai.services.evidence_builder import build_analysis_evidence
from aai.services.activity_logger import record_activity

router = APIRouter(tags=["agent workflows"])
logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task] = {}


def _enabled(db: Session, agent_id: str) -> bool:
    config = db.get(AgentConfig, agent_id)
    return config.enabled if config else True


def _workflow_read(db: Session, workflow: AgentWorkflow) -> WorkflowRead:
    executions = db.scalars(select(AgentExecution).where(AgentExecution.workflow_id == workflow.workflow_id)
                            .order_by(AgentExecution.sequence)).all()
    return WorkflowRead(
        workflow_id=workflow.workflow_id, job_id=workflow.job_id, status=workflow.status,
        current_agent=workflow.current_agent, created_at=workflow.created_at,
        started_at=workflow.started_at, completed_at=workflow.completed_at,
        agent_execution_history=[AgentExecutionRead(
            agent_id=item.agent_id, status=item.status, current_task=item.current_task,
            started_at=item.started_at, completed_at=item.completed_at, duration_ms=item.duration_ms,
            result_summary=item.result_summary, error_details=item.error_details,
        ) for item in executions], result=workflow.result, error_details=workflow.error_details,
    )


@router.get("/agents", response_model=list[AgentRead])
def list_agents(db: Session = Depends(get_db)) -> list[AgentRead]:
    output = []
    for definition in AGENTS:
        latest = db.scalar(select(AgentExecution).where(AgentExecution.agent_id == definition.agent_id)
                           .order_by(AgentExecution.started_at.desc()).limit(1))
        active = db.scalar(select(AgentExecution).join(AgentWorkflow).where(
            AgentExecution.agent_id == definition.agent_id,
            AgentExecution.status == "running", AgentWorkflow.status == "running").limit(1))
        output.append(AgentRead(
            agent_id=definition.agent_id, name=definition.name, description=definition.description,
            version=definition.version, capabilities=list(definition.capabilities), enabled=_enabled(db, definition.agent_id),
            current_status=active.status if active else (latest.status if latest else "idle"),
            current_task=active.current_task if active else None,
            last_execution_at=latest.completed_at if latest else None,
            last_duration_ms=latest.duration_ms if latest else None,
            result_summary=latest.result_summary if latest else None, error_details=latest.error_details if latest else None,
        ))
    return output


@router.get("/agents/{agent_id}", response_model=AgentRead)
def get_agent(agent_id: str, db: Session = Depends(get_db)) -> AgentRead:
    definition = AGENT_BY_ID.get(agent_id)
    if definition is None:
        raise HTTPException(404, detail={"code": "agent_not_found", "message": "Agent not found."})
    return next(item for item in list_agents(db) if item.agent_id == agent_id)


@router.patch("/agents/{agent_id}", response_model=AgentRead)
def set_agent_enabled(agent_id: str, request: AgentEnableRequest, db: Session = Depends(get_db)) -> AgentRead:
    if agent_id not in AGENT_BY_ID:
        raise HTTPException(404, detail={"code": "agent_not_found", "message": "Agent not found."})
    config = db.get(AgentConfig, agent_id)
    previous = config.enabled if config is not None else True
    if config is None:
        config = AgentConfig(agent_id=agent_id, enabled=request.enabled)
        db.add(config)
    else:
        config.enabled = request.enabled
        config.updated_at = datetime.now(timezone.utc)
    db.commit()
    if previous != request.enabled:
        record_activity(category="SYSTEM", event_type="configuration.agent.updated",
                        title="Agent configuration changed",
                        description="An agent was enabled or disabled in the local registry.",
                        severity="INFO", status="COMPLETED", source="agent_configuration",
                        agent_id=agent_id, metadata={"enabled": request.enabled},
                        idempotency_key=f"agent-config:{agent_id}:{config.updated_at.isoformat()}", bind=db.get_bind())
    return get_agent(agent_id, db)


@router.post("/agent-workflows", response_model=WorkflowRead, status_code=202)
async def create_workflow(request: WorkflowCreate, response: Response, db: Session = Depends(get_db)) -> WorkflowRead:
    job = db.get(AnalysisJob, request.job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "analysis_job_not_found", "message": "Analysis job not found."})
    if job.status != "completed":
        raise HTTPException(409, detail={"code": "analysis_not_complete", "message": "Agent workflows require a completed analysis job."})
    active = db.scalar(select(AgentWorkflow).where(
        AgentWorkflow.job_id == job.job_id, AgentWorkflow.status.in_(["pending", "validating", "running"])))
    if active:
        response.status_code = 202
        return _workflow_read(db, active)
    evidence = build_analysis_evidence(db, job)
    validation = ensure_evidence_valid(evidence)
    workflow_id = str(uuid.uuid4())
    workflow = AgentWorkflow(workflow_id=workflow_id, job_id=job.job_id, status="pending",
                             agent_execution_history=[])
    try:
        db.add(workflow)
        db.flush()
        enabled_agents = [agent_id for agent_id in SEQUENCE if _enabled(db, agent_id)]
        if "vision" not in enabled_agents and "tracking" not in enabled_agents:
            raise HTTPException(409, detail={"code": "no_analysis_agents_enabled", "message": "Enable Vision or Tracking to start a workflow."})
        for sequence, agent_id in enumerate(SEQUENCE):
            db.add(AgentExecution(execution_id=str(uuid.uuid4()), workflow_id=workflow_id, job_id=job.job_id,
                                  agent_id=agent_id, sequence=sequence,
                                  status="pending" if agent_id in enabled_agents else "skipped"))
        db.commit()
    except IntegrityError:
        db.rollback()
        active = db.scalar(select(AgentWorkflow).where(
            AgentWorkflow.job_id == job.job_id, AgentWorkflow.status.in_(["pending", "validating", "running"])))
        if active:
            return _workflow_read(db, active)
        raise HTTPException(409, detail={"code": "workflow_conflict", "message": "A workflow for this analysis could not be started."})
    state = {
        "workflow_id": workflow_id, "job_id": job.job_id, "execution_status": "pending", "current_agent": None,
        "detection_summary": None, "tracking_summary": None, "evidence_references": [], "reasoning_result": None,
        "operations_result": None, "validation_result": validation, "agent_execution_history": [],
        "error_details": None, "started_at": None, "completed_at": None,
        "evidence": evidence, "enabled_agents": enabled_agents,
    }
    task = asyncio.create_task(execute_workflow(state), name=f"agent-workflow-{workflow_id}")
    _tasks[workflow_id] = task
    task.add_done_callback(lambda _task, key=workflow_id: _tasks.pop(key, None))
    response.status_code = 202
    return _workflow_read(db, workflow)


@router.get("/agent-workflows", response_model=list[WorkflowRead])
def list_workflows(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> list[WorkflowRead]:
    workflows = db.scalars(select(AgentWorkflow).order_by(AgentWorkflow.created_at.desc()).limit(limit)).all()
    return [_workflow_read(db, workflow) for workflow in workflows]


@router.get("/agent-workflows/{workflow_id}", response_model=WorkflowRead)
def get_workflow(workflow_id: str, db: Session = Depends(get_db)) -> WorkflowRead:
    workflow = db.get(AgentWorkflow, workflow_id)
    if workflow is None:
        raise HTTPException(404, detail={"code": "workflow_not_found", "message": "Agent workflow not found."})
    return _workflow_read(db, workflow)


@router.get("/agent-workflows/{workflow_id}/results", response_model=WorkflowRead)
def get_workflow_results(workflow_id: str, db: Session = Depends(get_db)) -> WorkflowRead:
    result = get_workflow(workflow_id, db)
    if result.status not in {"completed", "failed", "cancelled"}:
        raise HTTPException(409, detail={"code": "workflow_not_complete", "message": "Workflow has not completed."})
    return result


@router.post("/agent-workflows/{workflow_id}/cancel", response_model=WorkflowRead)
def cancel_workflow(workflow_id: str, db: Session = Depends(get_db)) -> WorkflowRead:
    workflow = db.get(AgentWorkflow, workflow_id)
    if workflow is None:
        raise HTTPException(404, detail={"code": "workflow_not_found", "message": "Agent workflow not found."})
    if workflow.status in {"completed", "failed", "cancelled"}:
        return _workflow_read(db, workflow)
    if workflow.current_agent == "reasoning":
        raise HTTPException(409, detail={"code": "workflow_cancellation_unsafe", "message": "The local inference request is active and cannot be safely interrupted."})
    task = _tasks.get(workflow_id)
    now = datetime.now(timezone.utc)
    workflow.status = "cancelled"
    workflow.current_agent = None
    workflow.error_details = {"code": "workflow_cancelled", "message": "The workflow was cancelled before local inference began."}
    workflow.completed_at = now
    for execution in db.scalars(select(AgentExecution).where(
        AgentExecution.workflow_id == workflow_id,
        AgentExecution.status.in_(["pending", "running"]))).all():
        execution.status = "skipped"
        execution.current_task = "Cancelled before execution completed"
        execution.completed_at = now
    db.commit()
    if task is not None:
        task.cancel()
    return _workflow_read(db, workflow)
