"""Failure-isolated writes for supplemental, non-duplicative activity events."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker

from aai.db.models import ActivityLog
from aai.db.session import SessionLocal

logger = logging.getLogger(__name__)
MAX_METADATA_BYTES = 8192
_CATEGORIES = {"VIDEO", "ANALYSIS", "DETECTION", "TRACKING", "REASONING", "AGENT", "WORKFLOW", "OPERATIONS", "REVIEW", "SYSTEM", "SECURITY"}
_STATUSES = {"PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED"}
_SEVERITIES = {"INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
_SENSITIVE_KEY = re.compile(r"(secret|password|token|api.?key|authorization|cookie|prompt|reasoning|chain.?of.?thought|video.?content|evidence.?payload)", re.I)


def sanitize_metadata(value: Any, *, depth: int = 0) -> Any:
    """Keep small structured diagnostics while dropping private or bulky values."""
    if depth > 5:
        return "[depth-limited]"
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:50]:
            name = str(key)[:80]
            if _SENSITIVE_KEY.search(name):
                continue
            result[name] = sanitize_metadata(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize_metadata(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:512]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:256]


def _bounded_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    clean = sanitize_metadata(value or {})
    encoded = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= MAX_METADATA_BYTES:
        return clean
    return {"truncated": True, "original_keys": list(clean)[:50]}


def record_activity(
    *, category: str, event_type: str, title: str, description: str,
    severity: str = "INFO", status: str = "COMPLETED", source: str,
    video_id: str | None = None, analysis_job_id: str | None = None,
    workflow_id: str | None = None, agent_id: str | None = None,
    operational_event_id: str | None = None, duration_ms: float | None = None,
    metadata: dict[str, Any] | None = None, error_type: str | None = None,
    idempotency_key: str | None = None, timestamp: datetime | None = None,
    bind=None,
) -> str | None:
    """Insert one real event in a short independent transaction.

    The source operation remains authoritative if this best-effort audit write
    fails. Failures go to the ordinary application logger and are not retried.
    """
    try:
        category = category.upper()
        status = status.upper()
        severity = severity.upper()
        if category not in _CATEGORIES or status not in _STATUSES or severity not in _SEVERITIES:
            raise ValueError("Invalid activity taxonomy value")
        if idempotency_key is not None and len(idempotency_key) > 160:
            raise ValueError("Activity idempotency key exceeds 160 characters")
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)) if idempotency_key else str(uuid.uuid4())
        values = {
            "id": event_id, "idempotency_key": idempotency_key, "category": category,
            "event_type": event_type[:96], "title": title[:200], "description": description[:2000],
            "severity": severity, "status": status, "source": source[:64],
            "video_id": video_id, "analysis_job_id": analysis_job_id, "workflow_id": workflow_id,
            "agent_id": agent_id, "operational_event_id": operational_event_id,
            "duration_ms": max(0.0, float(duration_ms)) if duration_ms is not None else None,
            "metadata_json": _bounded_metadata(metadata), "error_type": error_type[:128] if error_type else None,
            "created_at": timestamp or datetime.now(timezone.utc),
        }
        factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False) if bind is not None else SessionLocal
        with factory() as session:
            statement = sqlite_insert(ActivityLog).values(**values)
            if idempotency_key:
                statement = statement.on_conflict_do_nothing(index_elements=[ActivityLog.idempotency_key])
            session.execute(statement)
            session.commit()
        return event_id
    except Exception as error:
        logger.exception("ACTIVITY_LOG_WRITE_FAILED event_type=%s source=%s exception_class=%s",
                         event_type[:96], source[:64], type(error).__name__)
        return None
