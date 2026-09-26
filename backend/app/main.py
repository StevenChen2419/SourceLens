"""FastAPI entry point."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app.config import Settings
from app.api.documents import router as documents_router

settings = Settings()
app = FastAPI(title=settings.app_name)
app.include_router(documents_router)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
