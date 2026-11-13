"""Conservative warehouse synthesis; no actions are executed."""


def summarize_operations(vision: dict, tracking: dict, reasoning: dict | None) -> dict:
    observations = []
    if vision["detection_count"]:
        observations.append(f"Persisted analysis contains {vision['detection_count']} detections across {len(vision['class_counts'])} detected categories.")
    if tracking["unique_track_count"]:
        observations.append(f"Persisted tracking contains {tracking['unique_track_count']} unique sampled track IDs.")
    limitations = [
        "Safety violations and package damage are unavailable without dedicated validated models.",
        "Sparse sampled detections do not establish continuous presence, movement, or operational events.",
        "No warehouse operation is executed by this agent; recommendations require human approval.",
    ]
    evidence_references = sorted(set(vision["evidence_references"] + tracking["evidence_references"]))
    return {
        "observations": observations,
        "evidence_references": evidence_references,
        "reasoning_summary": reasoning["summary"] if reasoning else None,
        "risk_assessment": "unavailable_without_dedicated_validated_models",
        "recommendations": [
            {"title": "Review sampled analysis evidence", "evidence_references": evidence_references[:12],
             "requires_human_review": True}
        ] if observations else [],
        "limitations": limitations,
    }
