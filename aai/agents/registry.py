"""Stable definitions for the locally available agents."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    description: str
    version: str
    capabilities: tuple[str, ...]


AGENTS = (
    AgentDefinition("vision", "Vision Agent", "Summarizes persisted detector observations and confidence statistics.", "1.0.0", ("persisted_detections", "category_statistics", "confidence_statistics")),
    AgentDefinition("tracking", "Tracking Agent", "Summarizes persisted ByteTrack associations and sampled track coverage.", "1.0.0", ("track_counts", "observed_timestamps", "tracking_limitations")),
    AgentDefinition("reasoning", "Reasoning Agent", "Generates a validated, evidence-grounded report through the configured local LM Studio service.", "1.0.0", ("local_llm", "validated_report", "evidence_references")),
    AgentDefinition("operations", "Operations Agent", "Produces conservative operational summaries for human review from validated analysis outputs.", "1.0.0", ("warehouse_summary", "human_review_recommendations", "capability_limitations")),
)
AGENT_BY_ID = {agent.agent_id: agent for agent in AGENTS}
