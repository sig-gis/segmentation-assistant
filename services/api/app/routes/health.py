from fastapi import APIRouter, Request

from ..schemas import HealthResponse

router = APIRouter()


@router.get("/healthz", response_model=HealthResponse)
async def healthz(request: Request) -> HealthResponse:
    if request.app.state.backend.loaded:
        return HealthResponse(status="ready")
    load_error = getattr(request.app.state, "load_error", None)
    if load_error is not None:
        return HealthResponse(status="error", error=load_error)
    return HealthResponse(status="loading")
