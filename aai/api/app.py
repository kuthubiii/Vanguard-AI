"""FastAPI application factory and development API configuration."""

from contextlib import asynccontextmanager
import logging
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from aai.api.routes.health import router as health_router
from aai.api.routes.analysis import router as analysis_router
from aai.api.routes.videos import router as videos_router
from aai.api.routes.reasoning import router as reasoning_router
from aai.api.routes.agents import router as agents_router
from aai.api.routes.operations import router as operations_router
from aai.api.routes.activity import router as activity_router
from aai.api.middleware import UploadRequestSizeLimit
from aai.core.config import settings
from aai.db.session import init_database
from aai.services.video_storage import MAX_VIDEO_SIZE_BYTES
from aai.services.analysis_worker import analysis_worker
from aai.orchestration.workflow import recover_interrupted_workflows
from aai.services.activity_logger import record_activity

logger = logging.getLogger(__name__)
_INSTANCE_ID = str(uuid.uuid4())


@asynccontextmanager
async def lifespan(_application: FastAPI):
    settings.video_storage_dir.mkdir(parents=True, exist_ok=True)
    settings.processed_video_dir.mkdir(parents=True, exist_ok=True)
    try:
        init_database()
        recover_interrupted_workflows()
        record_activity(category="SYSTEM", event_type="system.database_migration.completed",
                        title="Database schema ready", description="Database initialization and additive migrations completed.",
                        severity="SUCCESS", status="COMPLETED", source="database_startup",
                        idempotency_key=f"system:{_INSTANCE_ID}:database-ready")
    except Exception:
        logger.exception("Database schema migration failed; analysis worker will not start")
        raise
    # Database initialization is complete before the worker can open sessions.
    try:
        analysis_worker.start()
    except Exception as error:
        record_activity(category="SYSTEM", event_type="system.analysis_worker.failed",
                        title="Analysis worker failed to start", description="The local analysis worker could not start.",
                        severity="ERROR", status="FAILED", source="application_lifecycle",
                        error_type=type(error).__name__, idempotency_key=f"system:{_INSTANCE_ID}:worker-failed")
        logger.exception("Analysis worker startup failed")
        raise
    record_activity(category="SYSTEM", event_type="system.analysis_worker.started",
                    title="Analysis worker started", description="The single local analysis worker is accepting queued jobs.",
                    severity="SUCCESS", status="COMPLETED", source="application_lifecycle",
                    idempotency_key=f"system:{_INSTANCE_ID}:worker-started")
    record_activity(category="SYSTEM", event_type="system.application.started",
                    title="Vanguard AI backend started", description="The API and analysis worker started successfully.",
                    severity="SUCCESS", status="COMPLETED", source="application_lifecycle",
                    metadata={"worker_running": True}, idempotency_key=f"system:{_INSTANCE_ID}:started")
    try:
        yield
    finally:
        try:
            analysis_worker.stop()
        except Exception as error:
            record_activity(category="SYSTEM", event_type="system.analysis_worker.stop_failed",
                            title="Analysis worker shutdown failed", description="The local worker did not stop cleanly.",
                            severity="ERROR", status="FAILED", source="application_lifecycle",
                            error_type=type(error).__name__, idempotency_key=f"system:{_INSTANCE_ID}:worker-stop-failed")
            logger.exception("Analysis worker shutdown failed")
            raise
        record_activity(category="SYSTEM", event_type="system.analysis_worker.stopped",
                        title="Analysis worker stopped", description="The local analysis worker stopped with the API lifespan.",
                        severity="INFO", status="COMPLETED", source="application_lifecycle",
                        idempotency_key=f"system:{_INSTANCE_ID}:worker-stopped")
        record_activity(category="SYSTEM", event_type="system.application.stopped",
                        title="Vanguard AI backend stopped", description="The API lifespan stopped the analysis worker.",
                        severity="INFO", status="COMPLETED", source="application_lifecycle",
                        idempotency_key=f"system:{_INSTANCE_ID}:stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Vanguard AI API",
        description="Local API foundation for Vanguard AI.",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Allow a small multipart envelope in addition to the maximum file bytes.
    application.add_middleware(
        UploadRequestSizeLimit,
        max_request_bytes=MAX_VIDEO_SIZE_BYTES + 1024 * 1024,
    )
    # Add CORS last so its outer layer also decorates early 413 responses.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router, prefix="/api")
    application.include_router(videos_router, prefix="/api")
    application.include_router(analysis_router, prefix="/api")
    application.include_router(reasoning_router, prefix="/api")
    application.include_router(agents_router, prefix="/api")
    application.include_router(operations_router, prefix="/api")
    application.include_router(activity_router, prefix="/api")
    # Serve a separately built Vite bundle when present; development can still
    # run Vite on its own port, and no packaging/build orchestration is implied.
    if settings.frontend_dist.is_dir():
        application.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
    return application


app = create_app()
