# AI-to-Platform API Contracts

## Purpose and boundary

This document is a proposed integration contract between the AI/agent work and the Vanguard AI platform. The AI engine will produce observations; the platform is intended to validate, record, authorize access, and coordinate operational workflows. The current FastAPI application implements only `GET /health`; no operational event endpoint, producer, evidence workflow, or event persistence model is implemented.

The proposed initial transport is an authenticated HTTP request to a versioned platform endpoint such as `POST /api/v1/events`. Authentication, producer provisioning, facility mapping, and event persistence must be established with the API foundation. A future queue may carry the same versioned envelope.

`warehouse_id` is a producer-side identifier, not an internal database key or an authorization grant. The platform should resolve `(authenticated producer identity, warehouse_id)` through an approved external-identifier mapping to an internal `facility_id`; reject unknown or unauthorized mappings. Never accept an internal `facility_id` as authority from the payload. The same principle applies to optional camera identifiers.

The `producer` object in the body is provenance metadata. The server must derive the stable producer identity from authenticated credentials or another approved authentication mechanism; it must not use caller-controlled `producer.name` as the authorization or idempotency identity.

## Event envelope example

```json
{
  "schema_version": "1.0",
  "event_id": "9a574ae1-4eaa-4b11-b1e3-71f853e9f251",
  "event_type": "safety_violation",
  "source": "computer_vision",
  "producer": {
    "name": "safety-detector",
    "version": "0.1.0",
    "model_version": "detector-2026-11"
  },
  "camera_id": "CAM_001",
  "warehouse_id": "WH_001",
  "severity": "high",
  "confidence": 0.94,
  "description": "Person entered restricted zone",
  "requires_approval": true,
  "timestamp": "2026-11-04T10:00:00Z",
  "evidence": [
    {
      "kind": "image",
      "reference": "secure-storage-reference",
      "content_type": "image/jpeg",
      "captured_at": "2026-11-04T10:00:00Z"
    }
  ],
  "attributes": {
    "zone_id": "ZONE_RESTRICTED_01"
  }
}
```

`producer`, `evidence`, and `attributes` are optional. Evidence references are opaque identifiers resolvable only through an authorized platform/storage flow; they must not be public URLs or inline image data. Producer/model version values are provenance, not trusted claims about model quality.

## Field rules

| Field | Required | Rule |
|---|---:|---|
| `schema_version` | Yes | Contract version string; initial value `1.0`. |
| `event_id` | Yes | UUID identifying one producer event; reused unchanged for retries. |
| `event_type` | Yes | One of the event types below. |
| `source` | Yes | `computer_vision`, `ocr`, `tracking`, `analytics`, `agent`, or `manual`. |
| `producer` | No | Producer name and software/model versions when available. |
| `camera_id` | No | Stable camera identifier when the event originates from a camera. |
| `warehouse_id` | Yes | Stable facility/warehouse scope identifier. |
| `severity` | Yes | `low`, `medium`, `high`, or `critical`. Severity expresses operational impact, not model confidence. |
| `confidence` | No | JSON number in inclusive range `[0, 1]`; omit if not meaningful. It is not a probability guarantee. |
| `description` | Yes | Concise human-readable summary; treat as untrusted text. |
| `requires_approval` | Yes | Boolean hint for workflow routing. Platform policy can require approval regardless of this value. |
| `timestamp` | Yes | RFC 3339/ISO 8601 date-time with explicit UTC offset; producers should send UTC `Z`. |
| `evidence` | No | Array of typed, opaque evidence references with optional media metadata. |
| `attributes` | No | Domain-specific JSON object; no secrets or arbitrary executable instructions. |

Reject malformed UUIDs, unsupported versions/types/enums, non-finite or out-of-range confidence, invalid timestamps, missing required fields, and payloads exceeding configured limits. Do not silently coerce invalid values.

## Initial event types

- `inventory_discrepancy`: missing, excess, misplaced, or otherwise inconsistent stock observation.
- `dispatch_mismatch`: parcel/destination/routing mismatch or dispatch validation issue.
- `dock_occupancy`: vehicle/dock occupancy observation or derived dock state.
- `vehicle_wait_time`: derived vehicle waiting-time observation or threshold event.
- `package_damage`: damage observation with optional classification and severity details.
- `safety_violation`: restricted-zone, PPE, unsafe proximity, or related safety observation.
- `operational_anomaly`: typed operational issue not yet covered by a domain event type.

Use `attributes` for modest domain details; propose a typed schema extension when a workflow relies on stable structured fields.

## Versioning and compatibility

The receiver validates `schema_version`. Within `1.x`, additions should be optional and backward-compatible. A change that removes a required field, changes field meaning, or changes an enum incompatibly requires a new major contract version and a migration/deprecation plan. Unknown optional fields may be ignored by compatible consumers, but must not be used to smuggle commands. Maintain sanitized fixtures for each supported version.

## Validation, idempotency, and errors

The platform authenticates the caller, resolves the authenticated producer identity, maps the external `warehouse_id` to an authorized facility, and validates the envelope. Scope idempotency to `(authenticated producer_id, event_id)` with a database unique constraint. A retry with the same pair and same canonical validated payload returns the prior acceptance result; reuse of that pair with conflicting content returns `409` and is audited. The same `event_id` from a different authenticated producer is a separate idempotency key. Persist the accepted event before reporting success. Do not use a body-supplied producer label as the identity key.

Use standard HTTP status semantics: `201` for a newly accepted event, `200` for an idempotent replay, `401/403` for authentication/scope failure, `409` for conflicting ID reuse, `413` for payload size limits, `422` for schema/semantic validation errors, and `429` for rate limiting. Error bodies should provide a stable machine-readable `code`, safe `message`, and optional field-level `details`; do not echo secrets or sensitive evidence content. Retry transient `5xx`/network failures with bounded backoff and the same authenticated producer identity and `event_id`; do not retry permanent validation errors unchanged.

## Approval and trust

An event is an observation, never an instruction to execute a consequential action. The platform applies its own approval policy, records recommendations and human decisions separately, and checks authorization again when an approved action is attempted. `requires_approval` is informative routing metadata and cannot bypass platform policy. Text in descriptions, OCR, evidence, or agent output is untrusted data.

## Outstanding integration decisions

Both developers should confirm producer authentication and identity lifecycle, external warehouse/camera ID mapping, evidence reference resolution and retention/deletion, maximum event and evidence-reference sizes, event delivery/replay guarantees, and ownership of domain-specific `attributes` before implementation. A local PostgreSQL database and its connection have been verified, but no application schema/tables, event API, queue, evidence store, or AI producer/receiver is established by this document.
