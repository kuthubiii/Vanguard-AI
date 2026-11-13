"""Explicit capability declarations for evidence supported by current models."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aai.db.models import AIReport, AnalysisJob
from aai.schemas.operations import OperationalAvailabilityRead, CapabilityAvailability
from aai.services.operational_queries import OperationalFilters, completed_job_conditions


def get_availability(session: Session, filters: OperationalFilters) -> OperationalAvailabilityRead:
    ai_available = bool(session.scalar(
        select(func.count()).select_from(AIReport).join(
            AnalysisJob, AnalysisJob.job_id == AIReport.job_id,
        ).where(*completed_job_conditions(filters))
    ) or 0)
    return OperationalAvailabilityRead(
        available_data_sources=[
            "Persisted completed analysis jobs", "YOLO class detections and confidence values",
            "ByteTrack IDs scoped to each analysis job", "Video-relative detection timestamps",
            "Persisted Phase 6 AI reports" if ai_available else "No Phase 6 AI report exists in the selected scope",
        ],
        ai_report_available=ai_available,
        capabilities=[
            CapabilityAvailability(capability="Object-category and detection analytics", status="available",
                                   details="Aggregated from persisted detection rows; no inference is run."),
            CapabilityAvailability(capability="Track observation analytics", status="available",
                                   details="Track IDs are scoped to an analysis job; sparse samples do not prove continuous presence."),
            CapabilityAvailability(capability="Rule-based activity comparisons", status="partially_available",
                                   details="Comparisons require enough completed analyses of the same video with identical inference settings."),
            CapabilityAvailability(capability="AI-generated operational recommendations", status="partially_available",
                                   details="Displayed only from an already persisted validated AI report; no LM Studio request is made."),
            CapabilityAvailability(capability="Package damage assessment", status="requires_custom_model",
                                   details="Generic object detections do not identify damage."),
            CapabilityAvailability(capability="PPE compliance", status="requires_custom_model",
                                   details="No validated PPE-specific detector or rules exist."),
            CapabilityAvailability(capability="Fire and smoke detection", status="requires_custom_model",
                                   details="No validated fire or smoke model exists."),
            CapabilityAvailability(capability="Restricted-zone violations", status="not_implemented",
                                   details="No warehouse zones or location calibration are stored."),
            CapabilityAvailability(capability="Collision detection", status="requires_custom_model",
                                   details="Current sampled boxes and tracks do not validate collision events."),
            CapabilityAvailability(capability="Precise warehouse occupancy", status="not_implemented",
                                   details="No calibrated camera views, physical locations, or occupancy model exist."),
            CapabilityAvailability(capability="Equipment malfunction detection", status="requires_custom_model",
                                   details="No equipment-specific model or validated telemetry source exists."),
        ],
    )
