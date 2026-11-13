"""Vision agent: deterministic summaries of persisted YOLO detections."""


def summarize_vision(evidence: dict) -> dict:
    return {
        "source": "persisted_yolo_detections",
        "detection_count": evidence["detection_count"],
        "class_counts": evidence["class_counts"],
        "confidence_statistics": evidence["confidence_statistics"],
        "confidence_distribution": evidence["confidence_distribution"],
        "selected_detection_count": len(evidence["representative_detections"]),
        "evidence_references": [item["detection_id"] for item in evidence["representative_detections"]],
        "limitations": ["This agent summarizes existing detections; it does not rerun vision inference."]
        if evidence["detection_count"] else ["No persisted detections are available for this analysis."],
    }
