from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.jobs import service as jobs
from app.features.jobs.schemas import JobStarted
from app.features.providers.registry import GenerationDep
from app.features.questions import export, reviews, service
from app.features.questions.schemas import (
    GenerateIn,
    QuestionEdit,
    QuestionOut,
    QuestionWithRubric,
    ReviewIn,
    ReviewOut,
)

router = APIRouter(prefix="/questions")

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/generate", status_code=202)
def generate_questions(
    body: GenerateIn,
    session: SessionDep,
    settings: SettingsDep,
    generator: GenerationDep,
    background: BackgroundTasks,
) -> JobStarted:
    job, created = service.start_generation_job(
        session,
        body.subject_id,
        body.topic_ids,
        body.per_topic,
        body.style,
        body.difficulty,
        source_policy=body.source_policy,
        chapter_id=body.chapter_id,
        document_version_id=body.document_version_id,
    )
    if created:
        jobs.schedule(background, job, settings, generator)
    return JobStarted(job_id=job.id)


@router.get("")
def list_questions(
    subject_id: int,
    session: SessionDep,
    topic_id: Annotated[list[int] | None, Query()] = None,
    status: Annotated[list[str] | None, Query()] = None,
    chapter_id: int | None = None,
) -> list[QuestionOut]:
    return service.list_questions(
        session,
        subject_id,
        topic_ids=topic_id,
        statuses=tuple(status or ("active",)),
        chapter_id=chapter_id,
    )


@router.get("/reviews")
def list_reviews(subject_id: int, session: SessionDep) -> list[ReviewOut]:
    return reviews.list_reviews(session, subject_id)


@router.get("/reviews/export")
def export_reviews(subject_id: int, session: SessionDep) -> Response:
    return Response(
        export.reviews_markdown(session, subject_id),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="reviews-subject-{subject_id}.md"'},
    )


@router.get("/{version_id}")
def get_question(version_id: int, session: SessionDep) -> QuestionWithRubric:
    """Review screens only: never call this during an exam (it carries the rubric)."""
    return service.get_question(session, version_id, with_rubric=True, with_changes=True)


@router.put("/{version_id}")
def edit_question(version_id: int, body: QuestionEdit, session: SessionDep) -> QuestionWithRubric:
    return service.edit_question(
        session, version_id, body.text, body.style, body.difficulty, body.rubric
    )


@router.post("/{version_id}/retire")
def retire_question(version_id: int, session: SessionDep) -> QuestionOut:
    return service.retire_question(session, version_id)


@router.put("/{version_id}/review")
def put_review(version_id: int, body: ReviewIn, session: SessionDep) -> ReviewOut:
    return reviews.upsert_review(session, version_id, body)


@router.delete("/{version_id}/review", status_code=204)
def delete_review(version_id: int, session: SessionDep) -> Response:
    reviews.delete_review(session, version_id)
    return Response(status_code=204)


@router.post("/{version_id}/checked")
def check_question(version_id: int, session: SessionDep) -> QuestionWithRubric:
    return service.check_question(session, version_id)


@router.post("/{version_id}/keep", status_code=201)
def keep_question(version_id: int, session: SessionDep) -> QuestionWithRubric:
    return service.keep_question(session, version_id)
