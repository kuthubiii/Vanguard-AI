"""Tracking agent: deterministic summaries of persisted track associations."""


def summarize_tracking(evidence: dict) -> dict:
    return {
        "source": "persisted_bytetrack_associations",
        "tracking_enabled": evidence["tracking_enabled"],
        "tracked_detection_count": evidence["tracked_detection_count"],
        "unique_track_count": evidence["unique_track_count"],
        "statistics_scope": "Full persisted dataset counts; selected_tracks is a deterministic evidence sample.",
        "selected_track_count": len(evidence["tracks"]),
        "track_class_counts": evidence["track_class_counts"],
        "selected_tracks": evidence["tracks"],
        "evidence_references": [item["detection_id"] for item in evidence["representative_detections"] if item["track_id"] is not None],
        "limitations": ["Tracks summarize sampled observations and do not establish continuous physical presence."]
        if evidence["unique_track_count"] else ["No persisted track associations are available for this analysis."],
    }
