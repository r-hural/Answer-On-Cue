from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.db.session import SessionDep
from app.features.coverage import service
from app.features.coverage.schemas import SubjectCoverage, TopicDetail

router = APIRouter(prefix="/coverage")

Layer = Annotated[Literal["materials", "expanded", "all"], Query()]  # "all": no policy filter


@router.get("/subjects/{subject_id}")
def get_subject_coverage(
    subject_id: int, session: SessionDep, layer: Layer = "materials", chapter_id: int | None = None
) -> SubjectCoverage:
    return service.subject_coverage(session, subject_id, layer, chapter_id)


@router.get("/topics/{topic_id}")
def get_topic_coverage(
    topic_id: int, session: SessionDep, layer: Layer = "materials"
) -> TopicDetail:
    return service.topic_detail(session, topic_id, layer)
