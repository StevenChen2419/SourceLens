"""Non-sensitive frontend capabilities; backend policy remains authoritative."""
from typing import Annotated, Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.access import DEMO_FILENAME, SUGGESTED_QUESTIONS
from app.config import Settings
from app.dependencies import get_settings

router = APIRouter()

class PublicConfiguration(BaseModel):
    mode: Literal["development", "public_demo"]
    can_manage_documents: bool
    max_question_chars: int
    demo_filename: str | None
    suggested_questions: list[str]

@router.get("/api/config", response_model=PublicConfiguration)
def configuration(settings: Annotated[Settings, Depends(get_settings)]) -> PublicConfiguration:
    demo = settings.app_mode == "public_demo"
    return PublicConfiguration(mode=settings.app_mode, can_manage_documents=not demo,
        max_question_chars=settings.demo_max_question_chars if demo else 4000,
        demo_filename=DEMO_FILENAME if demo else None,
        suggested_questions=SUGGESTED_QUESTIONS if demo else [])
