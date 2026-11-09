# Project Roadmap

This roadmap distinguishes the existing foundation from proposed work. Completion must be verified against working software; a listed capability is not evidence that it has been implemented.

## Implemented foundation

- FastAPI application with `GET /health`.
- Pydantic Settings configuration, SQLAlchemy declarative `Base`, engine, session factory, and database session dependency.
- Local PostgreSQL connection to `vanguard_ai` verified successfully.
- Three database-related test functions in `backend/tests/test_database.py`.
- A proposed database design and initial AI event contract are documented; neither represents an implemented schema or API.
- A backend virtual environment is local development state. Create it locally with Python 3.12; it is excluded from Git.

No operational ORM models, Alembic configuration/migrations, application tables, business APIs, evidence management, agent workflows, human approval APIs, dashboards, computer-vision pipelines, or multi-agent orchestration are implemented.

## Phase 1 — Schema and migration foundation (next)

- Review and approve `DATABASE_DESIGN.md`, facility isolation, event idempotency, approval policy, and evidence retention/deletion policies.
- Agree on SQLAlchemy model conventions and configure Alembic without applying an unreviewed migration.
- Add the first operational models and reviewed migration only after the schema design is approved.
- Add focused tests for constraints, facility scoping, and migration behavior; keep PostgreSQL tests explicitly identified as integration tests.

## Phase 2 — Platform API and shared contracts

- Implement versioned API conventions, authentication/authorization foundation, safe error responses, and event intake.
- Validate and persist versioned AI events with producer identity, idempotency, facility authorization, and audit provenance.
- Publish shared schema examples and sanitized integration fixtures for both developers.

## Phase 3 — AI and agent integration

- Develop individual CV/OCR/tracking workflows and evaluate them with approved datasets.
- Add bounded agent orchestration and structured recommendations with provenance.
- Integrate one workflow end-to-end using simulated/sample inputs before real operational data.

## Phase 4 — Operator interface and approval workflows

- Build responsive event, protected evidence-reference, recommendation, and approval views.
- Enforce approval and authorization server-side and record decisions in the audit trail.
- Add scoped live updates when the API lifecycle is established.

## Phase 5 — Workflow expansion and analytics

- Extend validated patterns across inventory, dispatch, docks, damage, and safety.
- Add operational analytics, explainability views, monitoring, retention, and performance work.
- Evaluate security, reliability, and operational readiness before deployment.

## Delivery gates

Each phase should have reviewed acceptance criteria, contract updates where needed, tests appropriate to its boundaries, and a pull request against the integration branch. Do not train/download models or ingest production data without an explicit dataset and privacy plan.
