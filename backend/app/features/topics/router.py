from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends

from app.config import Settings, get_settings
from app.db.session import SessionDep
from app.features.jobs import service as jobs
from app.features.jobs.schemas import JobStarted
from app.features.providers.registry import GenerationDep
from app.features.topics import service
from app.features.topics.schemas import (
    ExtractIn,
    MergeIn,
    SplitIn,
    TermOut,
    TopicDeleted,
    TopicOut,
    TopicPatch,
)

router = APIRouter(prefix="/topics")

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/extract", status_code=202)
def extract_topics(
    body: ExtractIn,
    session: SessionDep,
    settings: SettingsDep,
    generator: GenerationDep,
    background: BackgroundTasks,
) -> JobStarted:
    job = service.start_extraction(session, body.document_version_id)
    jobs.schedule(background, job, settings, generator)
    return JobStarted(job_id=job.id)


@router.get("")
def list_topics(
    subject_id: int,
    session: SessionDep,
    include_removed: bool = False,
    chapter_id: int | None = None,
) -> list[TopicOut]:
    return service.topic_tree(session, subject_id, include_removed, chapter_id)


@router.get("/terms")
def list_terms(subject_id: int, session: SessionDep) -> list[TermOut]:
    return service.list_terms(session, subject_id)


@router.post("/merge")
def merge_topics(body: MergeIn, session: SessionDep) -> TopicOut:
    return service.merge_topics(session, body.topic_ids, body.title, body.description)


@router.patch("/{topic_id}")
def update_topic(topic_id: int, body: TopicPatch, session: SessionDep) -> TopicOut:
    return service.update_topic(
        session, topic_id, body.title, body.description, body.model_fields_set
    )


@router.post("/{topic_id}/split")
def split_topic(topic_id: int, body: SplitIn, session: SessionDep) -> list[TopicOut]:
    return service.split_topic(session, topic_id, body.parts)


@router.delete("/{topic_id}")
def delete_topic(topic_id: int, session: SessionDep) -> TopicDeleted:
    return service.remove_topic(session, topic_id)
