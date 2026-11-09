
from fastapi import FastAPI

from app.api.routes.organizations import router as organizations_router
from app.api.routes.facilities import router as facilities_router
from app.api.routes.auth import router as auth_router


app = FastAPI(
    title="Vanguard AI",
    description="Agentic AI and Computer Vision Operations Platform",
    version="1.0.0",
)


# Authentication API
app.include_router(
    auth_router,
    prefix="/api/v1",
)


# Organization API
app.include_router(
    organizations_router,
    prefix="/api/v1",
)


# Facility API
app.include_router(
    facilities_router,
    prefix="/api/v1",
)


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "application": "Vanguard AI",
        "version": "1.0.0",
    }
