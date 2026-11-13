"""Practical suggestions from rule events and already-persisted Phase 6 reports."""

import json

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from aai.db.models import AIReport, AnalysisJob, OperationalEvent, Video
from aai.schemas.operations import OperationalRecommendationListRead, OperationalRecommendationRead
from aai.services.operational_events import EVENT_RULES, _event_query, count_events
from aai.services.operational_queries import OperationalFilters as QueryFilters, completed_job_conditions


def get_recommendations(session: Session, filters: QueryFilters, *, limit: int, offset: int) -> OperationalRecommendationListRead:
    event_total = count_events(session, filters, review_status="pending")
    json_recommendations = func.json_each(AIReport.recommendations).table_valued("key", "value").alias("report_recommendation")
    report_query = select(
        AIReport.job_id.label("job_id"), AIReport.model_name.label("model_name"),
        AIReport.generated_at.label("generated_at"), json_recommendations.c.key.label("recommendation_index"),
        json_recommendations.c.value.label("recommendation_json"),
    ).select_from(AIReport).join(AnalysisJob, AnalysisJob.job_id == AIReport.job_id).join(
        Video, Video.id == AnalysisJob.video_id
    ).join(json_recommendations, true()).where(*completed_job_conditions(filters))
    report_query = report_query.add_columns(Video.id.label("video_id"))
    report_rows = report_query.subquery()
    report_total = int(session.scalar(select(func.count()).select_from(report_rows)) or 0)
    total = event_total + report_total
    items: list[OperationalRecommendationRead] = []

    if offset < event_total:
        event_page = session.execute(_event_query(filters, review_status="pending")
                                     .where(OperationalEvent.event_type.in_(list(EVENT_RULES)))
                                     .order_by(OperationalEvent.occurred_at.desc(), OperationalEvent.event_id)
                                     .offset(offset).limit(limit)).all()
        for row in event_page:
            event, job, video, *_ = row
            rule = EVENT_RULES[event.event_type]
            items.append(OperationalRecommendationRead(
                recommendation_id=f"event:{event.event_id}", priority=_priority(event.severity),
                title=f"Review: {event.title}", description=rule["action"], reason=event.evidence_explanation,
                analysis_job_id=job.job_id, video_id=video.id,
                evidence_references=event.related_detection_ids or [], source="rule_based",
            ))
        if len(items) < limit:
            remaining = limit - len(items)
            report_offset = 0
        else:
            report_offset = -1
            remaining = 0
    else:
        report_offset = offset - event_total
        remaining = limit

    if remaining:
        recommendations = session.execute(
            select(report_rows).order_by(report_rows.c.generated_at.desc(), report_rows.c.job_id,
                                         report_rows.c.recommendation_index)
            .offset(report_offset).limit(remaining)
        ).all()
        for row in recommendations:
            try:
                content = json.loads(row.recommendation_json)
            except (TypeError, json.JSONDecodeError):
                continue
            refs = content.get("evidence", []) if isinstance(content, dict) else []
            items.append(OperationalRecommendationRead(
                recommendation_id=f"ai:{row.job_id}:{row.recommendation_index}", priority="medium",
                title=content.get("title", "AI report recommendation"),
                description=content.get("description", ""), reason=content.get("reason", ""),
                analysis_job_id=row.job_id, video_id=row.video_id,
                evidence_references=[ref["detection_id"] for ref in refs if isinstance(ref, dict) and ref.get("detection_id")],
                source="ai_report",
            ))
    return OperationalRecommendationListRead(items=items, total=total, limit=limit, offset=offset)


def _priority(severity: str) -> str:
    return "high" if severity == "high" else "medium" if severity == "medium" else "low"
