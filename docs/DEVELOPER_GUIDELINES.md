# Developer Guidelines

## Current implementation status

The backend currently implements the FastAPI application and `GET /health`, Pydantic Settings, the SQLAlchemy declarative base, engine/session management, and verified local PostgreSQL connectivity. `backend/tests/test_database.py` contains three test functions. Operational ORM models, migrations, business APIs, evidence workflows, agents, and approval APIs remain unimplemented. See `docs/PROJECT_ROADMAP.md` for the current plan.

## Organization and naming

- Keep platform application code under `backend/app/`; keep model and inference implementation under `ai_engine/`; keep agent workflows under `agents/`; keep browser UI under `frontend/`.
- Prefer domain-focused modules and small files with one clear responsibility. Avoid importing model implementation into backend services.
- Python modules/functions use `snake_case`, classes use `PascalCase`, and constants use `UPPER_SNAKE_CASE`. TypeScript components/types use `PascalCase`; variables/functions use `camelCase`.
- Put API request/response models in schemas and persistence mappings in models. Do not reuse database models as public API payloads.
- Add directories when their first real module is ready; avoid placeholder-only directory trees.

## Branches and commits

- Shared branches: `main`, `dev/ai-engine`, and `dev/platform`.
- AI work proceeds on `dev/ai-engine`; platform/application work proceeds on `dev/platform`.
- Do not commit directly to `main`. Use topic branches from the relevant development branch and reviewed pull requests.
- Use concise imperative commit subjects with an area prefix, for example `docs: define event contract` or `backend: add event validation`.
- Keep commits focused and explain behavior changes and migration effects in pull requests. Do not rewrite shared history or push without explicit direction.

## Testing and review

- Add focused unit tests for business rules and schema validation; use integration tests for database behavior and API boundaries.
- Keep tests deterministic and independent of external model downloads, real camera feeds, and production credentials.
- Mark tests requiring local services clearly and provide safe setup instructions.
- Review security, authorization, auditability, failure/retry behavior, and contract compatibility alongside functionality.
- Run relevant checks before proposing a change and report exactly what was run and its outcome.
- The configured backend test directory is `backend/tests/`; run its tests from `backend/` with `python -m pytest`. Keep local PostgreSQL integration checks explicit and do not put credentials in test fixtures or output.

## API and AI contract changes

- Treat `docs/API_CONTRACTS.md` as the cross-team integration agreement.
- Include a schema version and representative sanitized fixture when changing event fields or semantics.
- Prefer additive changes; document compatibility and deprecation before removing fields or changing meanings.
- Keep model outputs and agent recommendations explicitly distinct from user-approved actions.

## Configuration and data protection

- Commit placeholder-only `.env.example` files, never `.env` files, passwords, API keys, tokens, private URLs, or production data.
- Use environment variables or an approved secrets store for runtime secrets. Rotate a secret if it is accidentally exposed.
- Keep raw datasets and model weights out of Git. Use approved, access-controlled storage and document dataset provenance and permitted use.
- Do not log credentials, access tokens, sensitive OCR content, or public evidence links.
- Clearly label synthetic/simulated data and avoid facial recognition or emotion inference for employee monitoring.

Create the Python 3.12 backend environment locally when it is absent; `.venv/` is ignored by Git and must not be committed:

```powershell
cd D:\Vanguard_AI\backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Keep the real `backend/.env` local. Settings may read `DATABASE_URL` from the process environment or that ignored file; never print it, include it in command output, or overwrite an existing local configuration.
