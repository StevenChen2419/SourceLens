"""FastAPI entry point."""

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import Settings
from app.access import DemoRuntime, PublicAccessMiddleware
from app.dependencies import get_settings
from app.api.configuration import router as configuration_router
from app.api.documents import router as documents_router
from app.api.retrieval import router as retrieval_router
from app.api.answers import router as answers_router

class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


def health() -> HealthResponse:
    """Process health only: never call Azure from a platform probe."""
    return HealthResponse()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # Fail fast on invalid production configuration.
    application = FastAPI(title=settings.app_name,
        docs_url=None if settings.app_mode == "public_demo" else "/docs",
        redoc_url=None if settings.app_mode == "public_demo" else "/redoc",
        openapi_url=None if settings.app_mode == "public_demo" else "/openapi.json")
    application.dependency_overrides[get_settings] = lambda: settings
    application.state.demo_runtime = DemoRuntime(settings)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"] if settings.app_mode == "public_demo" else ["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    application.add_middleware(PublicAccessMiddleware, settings=settings, runtime=application.state.demo_runtime)
    application.include_router(configuration_router)
    application.include_router(documents_router)
    application.include_router(retrieval_router)
    application.include_router(answers_router)
    application.get("/health", response_model=HealthResponse)(health)
    return application


app = create_app()
