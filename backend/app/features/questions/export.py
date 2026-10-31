"""Markdown of the bank for review: question text, rubric, cited passages and the verdicts."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.library import service as library
from app.features.questions import service
from app.features.questions.models import QuestionFamily, QuestionReview, QuestionVersion
from app.features.topics.models import Topic

VERDICT_LABELS = {"ok": "OK", "weak": "Weak", "bad": "Bad"}
NO_TOPIC = "No topic"


def question_lines(session: Session, version_id: int, n: int) -> list[str]:
    question = service.get_question(session, version_id, with_rubric=True)
    lines = [
        f"### {n}. {question.text}",
        "",
        f"style: {question.style} · difficulty: {question.difficulty} · "
        f"status: {question.status} · version {question.version} (id {question.version_id})",
        "",
    ]
    if question.review_note:
        lines += [f"> Note: {question.review_note}", ""]
    locators = {p.id: p.locator for p in question.passages}
    lines.append("Key points:")
    for point in question.rubric.essential_points:
        where = ", ".join(locators.get(i, f"#{i}") for i in point.passage_ids)
        lines.append(f"- ({point.weight:g}) {point.text} — {where}")
    return [*lines, "", f"Model answer: {question.rubric.model_answer}", ""]


def reviews_markdown(session: Session, subject_id: int) -> str:
    """Every reviewed question version of the subject, grouped by topic."""
    subject = library.get_subject(session, subject_id)
    rows = session.execute(
        select(QuestionReview, QuestionFamily.primary_topic_id)
        .join(QuestionVersion, QuestionVersion.id == QuestionReview.question_version_id)
        .join(QuestionFamily, QuestionFamily.id == QuestionVersion.family_id)
        .where(QuestionFamily.subject_id == subject_id)
        .order_by(QuestionVersion.id)
    ).all()
    titles = {
        topic.id: topic.title
        for topic in session.scalars(select(Topic).where(Topic.subject_id == subject_id))
    }
    groups: dict[str, list[QuestionReview]] = {}
    for review, topic_id in rows:
        groups.setdefault(titles.get(topic_id, NO_TOPIC), []).append(review)
    lines = [f"# Question bank review: {subject.name}", "", f"Reviewed questions: {len(rows)}.", ""]
    for title, reviews in groups.items():
        lines += [f"## {title}", ""]
        for n, review in enumerate(reviews, start=1):
            lines += question_lines(session, review.question_version_id, n)
            question = service.get_question(session, review.question_version_id, with_rubric=True)
            for passage in question.passages:
                lines += [f"> **{passage.locator}** (#{passage.id}): {passage.text}", ">"]
            note = f" — {review.note}" if review.note else ""
            lines += ["", f"Review: **{VERDICT_LABELS[review.verdict]}**{note}", ""]
    return "\n".join(lines)
