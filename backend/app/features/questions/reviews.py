"""Verdicts on question versions (ok / weak / bad, with a note)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.questions import service
from app.features.questions.models import QuestionFamily, QuestionReview, QuestionVersion
from app.features.questions.schemas import ReviewIn, ReviewOut


def _out(review: QuestionReview) -> ReviewOut:
    return ReviewOut(
        question_version_id=review.question_version_id,
        verdict=review.verdict,
        note=review.note,
        updated_at=review.updated_at,
    )


def _find(session: Session, version_id: int) -> QuestionReview | None:
    return session.scalar(
        select(QuestionReview).where(QuestionReview.question_version_id == version_id)
    )


def upsert_review(session: Session, version_id: int, body: ReviewIn) -> ReviewOut:
    service.get_question(session, version_id, with_rubric=False)  # 404 for an unknown version
    review = _find(session, version_id)
    if review is None:
        review = QuestionReview(question_version_id=version_id)
        session.add(review)
    review.verdict = body.verdict
    review.note = (body.note or "").strip() or None
    session.commit()
    session.refresh(review)
    return _out(review)


def delete_review(session: Session, version_id: int) -> None:
    review = _find(session, version_id)
    if review is not None:
        session.delete(review)
        session.commit()


def list_reviews(session: Session, subject_id: int) -> list[ReviewOut]:
    rows = session.scalars(
        select(QuestionReview)
        .join(QuestionVersion, QuestionVersion.id == QuestionReview.question_version_id)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .where(QuestionFamily.subject_id == subject_id)
        .order_by(QuestionReview.question_version_id)
    )
    return [_out(review) for review in rows]
