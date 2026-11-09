# Vanguard AI

### SEE. THINK. PREDICT. ACT.

An Agentic AI and Computer Vision platform for warehouse and logistics operations.

## Project Overview

Vanguard AI aims to unify warehouse intelligence through computer vision, AI agents, operational analytics and human-approved decision-making.

## Core Modules (Planned)

- Inventory Intelligence
- Parcel and Dispatch Intelligence
- Loading Dock Intelligence
- Package Damage Intelligence
- Warehouse Safety Intelligence

## Current Implementation

### Backend Foundation

- FastAPI application and health endpoint.
- PostgreSQL database integration.
- SQLAlchemy 2.0 models.
- Alembic database migrations.
- Organization and facility management foundations.
- User, role and permission models.
- RBAC permission catalogs and seed services.
- Argon2id password hashing.
- JWT access and refresh token generation.
- Login API with successful authentication testing.
- Authentication dependency with active-user validation.
- Automated database and authentication dependency tests.

### Security Work in Progress

- Persistent refresh-token storage and rotation.
- Invitation-based user onboarding.
- Email verification.
- Permission enforcement.
- Organization and facility tenant isolation.
- Expanded security testing.

**Important:** The login API works, but authentication and authorization are not yet production-ready. Organization and facility business routes still require access-control enforcement.

## Technology Stack

### Backend
- Python 3.12
- FastAPI
- SQLAlchemy
- PostgreSQL
- Alembic
- Pytest
- PyJWT
- Argon2id

### Frontend (Planned)
- React
- TypeScript
- Vite
- Tailwind CSS

### AI and Computer Vision (Planned)
- YOLO
- OpenCV
- PaddleOCR
- LangGraph
- Ollama

## Architecture

The project follows a modular monolith architecture initially, with separate logical modules for the backend, AI engine, agent orchestration and frontend.

## Project Structure

```text
Vanguard_AI/
├── frontend/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── database/
│   │   ├── models/
│   │   ├── schemas/
│   │   └── services/
│   ├── alembic/
│   ├── tests/
│   └── requirements.txt
├── ai_engine/
├── agents/
├── datasets/
├── docs/
├── scripts/
└── README.md
```

## Development Status

The backend foundation, database infrastructure, initial RBAC foundation and login flow have been implemented.

Refresh-token lifecycle management, authorization enforcement, operational modules, computer vision, agent orchestration and frontend development remain in progress.

## Security

- Never commit `.env` files or credentials.
- Keep virtual environments and generated model artifacts out of Git.
- Use `.env.example` for configuration templates.
- Require authentication, authorization and tenant isolation before exposing business APIs.

## Project Repository

[GitHub: Vanguard AI](https://github.com/kuthubiii/Vanguard-AI)