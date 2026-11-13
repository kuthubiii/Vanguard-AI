"""Service health endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health", summary="Check API availability")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "vanguard-ai-api"}
