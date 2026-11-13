"""Adapter to existing reasoning and AIReport persistence."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from aai.core.config import settings
from aai.db.models import AIReport
from aai.db.session import SessionLocal
from aai.schemas.reasoning import AIReportPayload
from aai.services.reasoning_service import (
    AIEvidenceValidationError, AISchemaValidationError, REPORT_LOCK, _validate_references, generate_report,
)


async def run_reasoning(job_id: str, evidence: dict) -> dict:
    async with REPORT_LOCK:
        with SessionLocal() as db:
            report = db.scalar(select(AIReport).where(AIReport.job_id == job_id))
            if report is not None:
                try:
                    payload = AIReportPayload.model_validate({
                        "summary": report.summary, "observations": report.observations,
                        "recommendations": report.recommendations, "limitations": report.limitations,
                    })
                    references = [item.evidence for item in payload.observations]
                    for item in payload.recommendations:
                        references.append(item.evidence)
                except ValidationError as error:
                    raise AISchemaValidationError("The saved AI report no longer matches the required report schema.") from error
                _validate_references(references, evidence)
                return _report_dict(report)
        factory_options = getattr(SessionLocal, "kw", {})
        activity_bind = factory_options.get("bind") if isinstance(factory_options, dict) else None
        payload = await generate_report(evidence, activity_bind=activity_bind)
        with SessionLocal() as db:
            report = AIReport(report_id=str(uuid.uuid4()), job_id=job_id)
            report.model_name = settings.lm_studio_model
            report.summary = payload.summary
            report.observations = [item.model_dump(mode="json") for item in payload.observations]
            report.recommendations = [item.model_dump(mode="json") for item in payload.recommendations]
            report.limitations = payload.limitations
            report.evidence = evidence
            report.generated_at = datetime.now(timezone.utc)
            db.add(report)
            try:
                db.commit()
                db.refresh(report)
            except IntegrityError:
                db.rollback()
                existing = db.scalar(select(AIReport).where(AIReport.job_id == job_id))
                if existing is None:
                    raise
                return _report_dict(existing)
            return _report_dict(report)


def _report_dict(report: AIReport) -> dict:
    return {
        "analysis_job_id": report.job_id, "generated_by": report.model_name,
        "summary": report.summary, "observations": report.observations,
        "recommendations": report.recommendations, "limitations": report.limitations,
        "evidence": report.evidence,
    }
