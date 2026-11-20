from fastapi import APIRouter


router = APIRouter()


@router.get("/api/v1/agent/health", include_in_schema=False)
async def health_check() -> dict[str, str]:
    """Health-check mínimo sin configuración, métricas ni datos operativos."""
    return {"status": "ok"}
