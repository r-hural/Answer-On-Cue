"""Audio retention: which recordings may lose their WAV file, and deleting exactly those.

Only the file goes (and `audio_deleted_at` is set); the row stays with its duration and VAD,
and so do transcripts, metrics, scores, coverage and trends. Never selected: the audio of an
unresolved answer, and under "Until the answer is assessed" that of a given-up answer.
"""

import hashlib
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.recording.models import Recording
from app.features.rounds.models import Attempt, Round, RoundItem

logger = logging.getLogger(__name__)

Scope = Literal["policy", "older_than", "all", "subject"]

SWEEP_INTERVAL = timedelta(hours=1)
UNLINKED_GRACE = timedelta(hours=24)
POLICY_DAYS = {"30d": 30, "90d": 90}
UNRESOLVED = frozenset({"processing", "failed"})
KEPT_UNTIL_ASSESSED = frozenset({"given_up"})  # never assessed: kept under the "assessed" policy
CHUNK = 500

_last_sweep: dict[str, datetime] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class AudioSelection:
    recording_ids: list[int]
    bytes: int
    kept_unresolved: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def data_file(settings: Settings, stored_path: str) -> Path | None:
    """The file behind a stored path, only when it lies in data/recordings or data/documents.

    A row restored from a foreign backup could name any path; nothing is read or deleted
    outside the two folders.
    """
    if not stored_path:
        return None
    root = settings.data_dir.resolve()
    path = (root / stored_path).resolve()
    if any(path.is_relative_to(root / folder) for folder in ("recordings", "documents")):
        return path
    return None


def fingerprint(ids: Iterable[int]) -> str:
    """Identifies a selection, so a confirmation only applies to what was shown."""
    joined = ",".join(str(i) for i in sorted(ids))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def select_recordings(
    session: Session,
    *,
    scope: Scope,
    policy: str | None = None,
    days: int | None = None,
    subject_id: int | None = None,
    now: datetime,
) -> AudioSelection:
    """Read only, one statement whatever the data size.

    Never selected: the audio of an answer still processing or failed and not resolved (its
    retry needs it), and an unlinked recording (mic check, orphan) younger than 24 hours. Under
    the "assessed" policy a given-up answer, which is never assessed, keeps its audio too (not
    counted as kept unresolved); every other scope and policy selects it as usual.
    """
    matches, min_age = _min_age(scope, policy, days)
    if not matches:
        return AudioSelection([], 0, 0)
    rows = session.execute(
        select(
            Recording.id,
            Recording.size_bytes,
            Recording.created_at,
            Attempt.id,
            Attempt.outcome,
            Attempt.processing_status,
            AttemptProcessing.status,
            Round.subject_id,
        )
        .select_from(Recording)
        .outerjoin(Attempt, Attempt.recording_id == Recording.id)
        .outerjoin(AttemptProcessing, AttemptProcessing.attempt_id == Attempt.id)
        .outerjoin(RoundItem, RoundItem.id == Attempt.round_item_id)
        .outerjoin(Round, Round.id == RoundItem.round_id)
        .where(Recording.audio_deleted_at.is_(None))
        .order_by(Recording.id)
    ).all()
    ids: list[int] = []
    size = 0
    kept = 0
    for rec_id, rec_bytes, created, attempt_id, outcome, status, proc_status, owner in rows:
        age = now - _utc(created)
        if scope == "subject" and owner != subject_id:
            continue
        if min_age is not None and age < min_age:
            continue
        if attempt_id is None:
            if age < UNLINKED_GRACE:
                continue
        elif outcome == "technical_failure" or UNRESOLVED & {status, proc_status}:
            kept += 1
            continue
        elif scope == "policy" and policy == "assessed" and outcome in KEPT_UNTIL_ASSESSED:
            continue
        ids.append(rec_id)
        size += rec_bytes
    return AudioSelection(ids, size, kept)


def _min_age(scope: Scope, policy: str | None, days: int | None) -> tuple[bool, timedelta | None]:
    """(whether anything can match, the minimum age a recording needs; None = any age)."""
    if scope == "policy":
        if policy == "assessed":
            return True, None
        if policy in POLICY_DAYS:
            return True, timedelta(days=POLICY_DAYS[policy])
        return False, None  # forever, or unknown
    if scope == "older_than":
        if days is None or days < 0:
            return False, None
        return True, timedelta(days=days)
    return True, None


def delete_audio(
    session: Session, settings: Settings, ids: Iterable[int], now: datetime
) -> tuple[int, int, int]:
    """Deletes the files of these recordings; (deleted, bytes, skipped_in_use).

    A file another program holds open (Windows) is skipped and left unmarked, so it is found
    again next time.
    """
    wanted = list(dict.fromkeys(ids))
    deleted = freed = skipped = 0
    for start in range(0, len(wanted), CHUNK):
        rows = session.scalars(
            select(Recording).where(
                Recording.id.in_(wanted[start : start + CHUNK]),
                Recording.audio_deleted_at.is_(None),
            )
        ).all()
        for recording in rows:
            try:
                path = data_file(settings, recording.stored_path)
                if path is not None:
                    path.unlink(missing_ok=True)
            except OSError:
                skipped += 1
                continue
            recording.audio_deleted_at = now
            deleted += 1
            freed += recording.size_bytes
    session.commit()
    return deleted, freed, skipped


def sweep_if_due(settings: Settings, now: datetime | None = None) -> None:
    """Applies the retention policy at most once an hour per data folder; never raises.

    Runs in its own short transaction, so the caller must hold no uncommitted write.
    """
    policy = settings.audio_retention
    if policy == "forever":
        return
    now = now or datetime.now(UTC)
    key = str(settings.data_dir)
    with _lock:
        last = _last_sweep.get(key)
        if last is not None and now - last < SWEEP_INTERVAL:
            return
        _last_sweep[key] = now
    try:
        with SessionLocal(settings.effective_database_url) as session:
            selection = select_recordings(session, scope="policy", policy=policy, now=now)
            if selection.recording_ids:
                delete_audio(session, settings, selection.recording_ids, now)
    except Exception:
        logger.warning("Audio retention sweep failed", exc_info=True)
