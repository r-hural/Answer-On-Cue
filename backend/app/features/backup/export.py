"""Export of the learning history: CSV and Markdown per subject, in a zip.

Only stored values are written (bands and scores come from the stored assessment, never
re-classified), a missing value is an empty cell, never 0, and rounds that are still running are
left out, so an exam round's withheld results cannot leak.
"""

import csv
import io
import json
import os
import re
import zipfile
from datetime import UTC, datetime, tzinfo
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.features.attempts.models import AttemptProcessing
from app.features.backup import storage
from app.features.backup.schemas import ExportOut
from app.features.library.models import Subject
from app.features.questions.models import QuestionVersion
from app.features.rounds.models import Attempt, Round, RoundItem

SHOWN_STATUSES = ("finished", "abandoned")
FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Names Windows keeps for devices, also with an extension ("CON", "nul.txt", "Com1.md").
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
}
# A spreadsheet runs a cell that starts with one of these as a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

ROUND_COLUMNS = [
    "round_id",
    "round_started_at",
    "round_finished_at",
    "round_type",
    "status",
    "feedback_timing",
    "source_policy",
    "questions",
    "answers",
]
ANSWER_COLUMNS = [
    "round_id",
    "round_started_at",
    "round_type",
    "feedback_timing",
    "source_policy",
    "ordinal",
    "role",
    "attempt_kind",
    "outcome",
    "band",
    "factual",
    "language",
    "total",
    "preparation_used_ms",
    "speech_start_latency_ms",
    "total_response_latency_ms",
    "answer_duration_ms",
    "question",
    "transcript",
]


def folder_name(name: str, subject_id: int, used: set[str]) -> str:
    """The subject's name as a folder name.

    Letters of every language stay; forbidden characters go."""
    cleaned = FORBIDDEN_CHARS.sub("_", name).strip(" .") or f"subject-{subject_id}"
    stem, dot, rest = cleaned.partition(".")
    if stem.rstrip(" ").upper() in RESERVED_NAMES:
        cleaned = f"{stem}_{dot}{rest}"
    if cleaned.casefold() in used:
        cleaned = f"{cleaned}-{subject_id}"
    used.add(cleaned.casefold())
    return cleaned


def _local(value: datetime | None, tz: tzinfo | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz).isoformat(sep=" ", timespec="seconds")


def _loads(raw: str | None) -> dict:
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _cell(value) -> str:
    return "" if value is None else str(value)


def _text(value: str) -> str:
    """A learner's or an author's text for a CSV cell, never run as a formula by a spreadsheet."""
    return f"'{value}" if value.startswith(FORMULA_STARTS) else value


def _csv(columns: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def _markdown(subject: str, round_: dict, entries: list[dict]) -> bytes:
    lines = [
        f"# {subject} — round {round_['id']}",
        "",
        f"- Started: {round_['started']}",
        f"- Type: {round_['type']} ({round_['status']})",
        f"- Feedback: {round_['timing']}",
        f"- Sources: {round_['policy']}",
    ]
    for entry in entries:
        heading = "Follow-up" if entry["role"] == "followup" else "Question"
        lines += ["", f"## {entry['ordinal']}. {heading}", "", entry["question"]]
        for attempt in entry["attempts"]:
            facts = [attempt["kind"], attempt["outcome"]]
            if attempt["band"]:
                facts.append(f"band: {attempt['band']}")
            if attempt["factual"] != "":
                facts.append(f"factual: {attempt['factual']}")
            lines += ["", "**" + " · ".join(facts) + "**"]
            if attempt["transcript"]:
                lines += [
                    "",
                    *[f"> {line}" if line else ">" for line in attempt["transcript"].split("\n")],
                ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def render(session: Session, tz: tzinfo | None = None) -> tuple[dict[str, bytes], int, int]:
    """(files by path, rounds, answers). A constant number of statements."""
    rounds = session.execute(
        select(
            Round.id,
            Round.subject_id,
            Subject.name,
            Round.round_type,
            Round.status,
            Round.started_at,
            Round.finished_at,
            Round.config_json,
        )
        .join(Subject, Subject.id == Round.subject_id)
        .where(Round.status.in_(SHOWN_STATUSES))
        .order_by(Subject.id, Round.started_at, Round.id)
    ).all()
    items = session.execute(
        select(
            RoundItem.round_id,
            RoundItem.ordinal,
            RoundItem.role,
            QuestionVersion.text,
            Attempt.id,
            Attempt.attempt_kind,
            Attempt.outcome,
            AttemptProcessing.transcript_text,
            AttemptProcessing.metrics_json,
            AttemptProcessing.assessment_json,
        )
        .select_from(RoundItem)
        .join(Round, Round.id == RoundItem.round_id)
        .join(QuestionVersion, QuestionVersion.id == RoundItem.question_version_id)
        .outerjoin(Attempt, Attempt.round_item_id == RoundItem.id)
        .outerjoin(AttemptProcessing, AttemptProcessing.attempt_id == Attempt.id)
        .where(Round.status.in_(SHOWN_STATUSES))
        .order_by(RoundItem.round_id, RoundItem.ordinal, RoundItem.role.desc(), Attempt.id)
    ).all()

    by_round: dict[int, list[dict]] = {}
    for (
        round_id,
        ordinal,
        role,
        question,
        attempt_id,
        kind,
        outcome,
        transcript,
        metrics,
        result,
    ) in items:
        entries = by_round.setdefault(round_id, [])
        if not entries or (entries[-1]["ordinal"], entries[-1]["role"]) != (ordinal + 1, role):
            entries.append(
                {"ordinal": ordinal + 1, "role": role, "question": question, "attempts": []}
            )
        if attempt_id is None or outcome == "pending":
            continue
        # A given-up answer is never scored, even when an orphaned run stored a result.
        assessment = {} if outcome == "given_up" else _loads(result)
        scores = assessment.get("scores") or {}
        measured = _loads(metrics)
        entries[-1]["attempts"].append(
            {
                "kind": kind,
                "outcome": outcome,
                "band": _cell(assessment.get("band")),
                "factual": _cell(scores.get("factual")),
                "language": _cell(scores.get("language")),
                "total": _cell(scores.get("total")),
                "prep": _cell(measured.get("preparation_used_ms")),
                "start": _cell(measured.get("speech_start_latency_ms")),
                "response": _cell(measured.get("total_response_latency_ms")),
                "duration": _cell(measured.get("answer_duration_ms")),
                "transcript": transcript or "",
            }
        )

    files: dict[str, bytes] = {}
    used: set[str] = set()
    folders: dict[int, str] = {}
    per_subject: dict[int, tuple[list[list[str]], list[list[str]]]] = {}
    answer_total = 0
    for round_id, subject_id, subject, round_type, status, started, finished, config in rounds:
        if subject_id not in folders:
            folders[subject_id] = folder_name(subject, subject_id, used)
            per_subject[subject_id] = ([], [])
        folder = folders[subject_id]
        settings_ = _loads(config)
        timing = settings_.get("feedback_timing", "practice")
        policy = settings_.get("source_policy", "materials")
        started_local = _local(started, tz)
        entries = by_round.get(round_id, [])
        answered = [a for e in entries for a in e["attempts"]]
        answer_total += len(answered)
        round_rows, answer_rows = per_subject[subject_id]
        round_rows.append(
            [
                str(round_id),
                started_local,
                _local(finished, tz),
                round_type,
                status,
                timing,
                policy,
                str(sum(1 for e in entries if e["role"] == "main")),
                str(len(answered)),
            ]
        )
        for entry in entries:
            for a in entry["attempts"]:
                answer_rows.append(
                    [
                        str(round_id),
                        started_local,
                        round_type,
                        timing,
                        policy,
                        str(entry["ordinal"]),
                        entry["role"],
                        a["kind"],
                        a["outcome"],
                        a["band"],
                        a["factual"],
                        a["language"],
                        a["total"],
                        a["prep"],
                        a["start"],
                        a["response"],
                        a["duration"],
                        _text(entry["question"]),
                        _text(a["transcript"]),
                    ]
                )
        files[f"{folder}/round-{round_id}.md"] = _markdown(
            subject,
            {
                "id": round_id,
                "started": started_local,
                "type": round_type,
                "status": status,
                "timing": timing,
                "policy": policy,
            },
            entries,
        )
    for subject_id, (round_rows, answer_rows) in per_subject.items():
        files[f"{folders[subject_id]}/rounds.csv"] = _csv(ROUND_COLUMNS, round_rows)
        files[f"{folders[subject_id]}/answers.csv"] = _csv(ANSWER_COLUMNS, answer_rows)
    return files, len(rounds), answer_total


def create_export(
    session: Session, settings: Settings, *, tz: tzinfo | None = None, now: datetime
) -> ExportOut:
    files, rounds, answers = render(session, tz)
    folder = settings.data_dir / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    name, _ = storage.free_name(folder, "answeroncue-history", now)
    target = folder / name
    part = folder / f"{name}.part"
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(files):
                zf.writestr(path, files[path])
        os.replace(part, target)
    finally:
        Path(part).unlink(missing_ok=True)
    return ExportOut(name=name, bytes=target.stat().st_size, rounds=rounds, answers=answers)
