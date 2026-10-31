import os
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_assessment_support import metrics
from test_backup_support import statements_of, subject_with_history, writes_in
from test_coverage_support import make_answer, make_objective_question, make_round
from test_reports_history import count_statements
from test_topics_support import make_subject, make_topic, make_version, upgrade

from app.db.session import SessionLocal
from app.features.attempts.models import AttemptProcessing
from app.features.backup import export
from app.main import create_app

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "export_golden"
ZONE = timezone(timedelta(hours=13))  # Windows has no tz database
KEY = "sk-test-SECRET-0123456789"
SUBJECT = "Zażółć: gęślą jaźń?"


@pytest.fixture
def db(settings):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as session:
        yield session


def fixed_history(db) -> dict:
    """Two finished rounds at fixed UTC times: an answered attempt, a dont_know, an interrupted
    attempt and a follow-up, plus an exam round that is still running."""
    subject = make_subject(db, SUBJECT)
    _, passages = make_version(db, subject.id, count=2, excluded=())
    topic = make_topic(db, subject.id, "Ateny", passages)
    questions = [
        make_objective_question(db, subject.id, topic.id, None, text, passage_ids=(passages[0].id,))
        for text in ("Omów ustrój Aten.", "Czym był ostracyzm?", 'Kim był Perykles, "strateg"?')
    ]
    first = make_round(db, subject.id, at=datetime(2026, 10, 20, 8, 0, 5, tzinfo=UTC))
    make_answer(
        db, first, questions[0], at=first.started_at, band="good", factual=66.0, prep_ms=8000
    )
    make_answer(db, first, questions[1], at=first.started_at, outcome="dont_know", processing=None)
    second = make_round(
        db, subject.id, at=datetime(2026, 10, 21, 21, 30, 0, tzinfo=UTC), timing="exam"
    )
    second.finished_at = datetime(2026, 10, 21, 21, 40, 0, tzinfo=UTC)
    answered = make_answer(
        db, second, questions[0], at=second.started_at, band="weak", factual=20.0, prep_ms=500
    )
    make_answer(
        db, second, questions[0], at=second.started_at, role="followup", kind="initial",
        band="needs_improvement", factual=45.0, ordinal=0,
    )  # fmt: skip
    make_answer(
        db, second, questions[1], at=second.started_at, outcome="interrupted", processing=None
    )
    row = db.query(AttemptProcessing).filter_by(attempt_id=answered.id).one()
    row.transcript_text = 'Pierwsza linia, "cytat".\n\nDruga linia; ąęł.'
    row.metrics_json = metrics(
        preparation_used_ms=500, speech_start_latency_ms=None, total_response_latency_ms=None
    ).model_dump_json()
    db.commit()
    running = make_round(
        db, subject.id, at=datetime(2026, 10, 22, 9, 0, tzinfo=UTC), timing="exam",
        status="active",
    )  # fmt: skip
    return {"subject": subject, "questions": questions, "running": running.id}


def zip_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def test_export_matches_golden(db, settings):
    fixed_history(db)
    done = export.create_export(db, settings, tz=ZONE, now=datetime(2026, 10, 23, tzinfo=UTC))
    assert (done.rounds, done.answers) == (2, 5)
    members = zip_members(settings.data_dir / "exports" / done.name)
    folder = "Zażółć_ gęślą jaźń_"
    first, second = (r[0] for r in db.execute(text("SELECT id FROM round ORDER BY id")).all()[:2])
    assert sorted(members) == sorted(
        [
            f"{folder}/rounds.csv",
            f"{folder}/answers.csv",
            f"{folder}/round-{first}.md",
            f"{folder}/round-{second}.md",
        ]
    )
    actual = {
        "rounds.csv": members[f"{folder}/rounds.csv"],
        "answers.csv": members[f"{folder}/answers.csv"],
        "round.md": members[f"{folder}/round-{second}.md"],
    }
    if os.environ.get("AOC_UPDATE_GOLDEN"):
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for name, data in actual.items():
            (GOLDEN / name).write_bytes(data)
    for name, data in actual.items():
        assert data == (GOLDEN / name).read_bytes(), name
    assert actual["rounds.csv"].startswith(b"\xef\xbb\xbf")
    assert actual["answers.csv"].startswith(b"\xef\xbb\xbf")


def test_missing_values_are_empty_cells(db, settings):
    fixed_history(db)
    files, _, _ = export.render(db, ZONE)
    text_ = files["Zażółć_ gęślą jaźń_/answers.csv"].decode("utf-8-sig")
    lines = text_.splitlines()
    header = lines[0].split(",")
    assert header[:9] == [
        "round_id", "round_started_at", "round_type", "feedback_timing", "source_policy",
        "ordinal", "role", "attempt_kind", "outcome",
    ]  # fmt: skip
    dont_know = next(line for line in lines if ",dont_know," in line)
    assert ",,,,,,,," in dont_know  # band to answer duration are empty, not 0
    assert ",0," not in dont_know


def test_export_excludes_running_rounds_and_secrets(db, settings):
    running = fixed_history(db)["running"]
    keys = f"AOC_OPENAI_API_KEY={KEY}" + chr(10)
    settings.env_file_path.write_text(keys)
    (settings.data_dir / "settings.env").write_text(keys)
    done = export.create_export(db, settings, tz=ZONE, now=datetime(2026, 10, 23, tzinfo=UTC))
    members = zip_members(settings.data_dir / "exports" / done.name)
    for name, data in members.items():
        assert KEY.encode() not in data, name
    assert not [n for n in members if n.endswith(f"round-{running}.md")]
    rounds_csv = members["Zażółć_ gęślą jaźń_/rounds.csv"].decode("utf-8-sig")
    assert len(rounds_csv.splitlines()) == 3  # the header and the two finished rounds
    assert b",active," not in b"".join(members.values())


def test_export_budget(db, settings):
    history = subject_with_history(db, settings, "Historia")

    def call():
        export.render(db, ZONE)

    small = count_statements(None, call)
    for days in range(3, 7):
        at = datetime(2026, 10, days, 9, 0, tzinfo=UTC)
        round_ = make_round(db, history["subject"].id, at=at)
        make_answer(db, round_, history["questions"][0], at=at, band="good", factual=66.0)
    assert count_statements(None, call) == small
    assert writes_in(statements_of(call)) == []


def test_export_download(client, db, settings):
    subject_with_history(db, settings)
    response = client.post("/api/data/exports")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"].startswith("answeroncue-history-") and body["rounds"] == 1
    assert body["answers"] == 2
    download = client.get(f"/api/data/exports/{body['name']}")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert download.content == (settings.data_dir / "exports" / body["name"]).read_bytes()
    foreign = TestClient(create_app(settings), base_url="http://evil.example")
    assert foreign.get(f"/api/data/exports/{body['name']}").status_code == 403
    assert client.get("/api/data/exports/answeroncue-history-x.zip").status_code == 404
    assert client.get("/api/data/exports/..%2Fsettings.env").status_code == 404
    bare = TestClient(create_app(settings))
    assert bare.post("/api/data/exports").status_code == 403


def test_folder_names_are_safe_and_unique():
    used: set[str] = set()
    assert export.folder_name('A<>:"/\\|?*B', 1, used) == "A_________B"
    assert export.folder_name("a_________b", 2, used) == "a_________b-2"
    assert export.folder_name("...", 3, used) == "subject-3"


def test_reserved_windows_names_get_an_underscore():
    used: set[str] = set()
    names = ["CON", "prn", "Aux", "nul.txt", "COM1", "com9.md", "LPT1", "lpt9", "CON .x"]
    assert [export.folder_name(n, i, used) for i, n in enumerate(names)] == [
        "CON_", "prn_", "Aux_", "nul_.txt", "COM1_", "com9_.md", "LPT1_", "lpt9_", "CON _.x",
    ]  # fmt: skip
    assert export.folder_name("COM10", 20, used) == "COM10"
    assert export.folder_name("Console", 21, used) == "Console"
    assert export.folder_name("CONTROL.CON", 22, used) == "CONTROL.CON"


def test_text_cells_never_start_a_formula(db, settings):
    import csv
    import io

    subject = make_subject(db, "Formuły")
    _, passages = make_version(db, subject.id, count=1, excluded=())
    topic = make_topic(db, subject.id, "T", passages)
    texts = ['=HYPERLINK("x")', "+1+1", "-2+3", "@SUM(A1)", "\tTab", "\rCR", "Plain -1"]
    round_ = make_round(db, subject.id, at=datetime(2026, 10, 20, 8, 0, tzinfo=UTC))
    for text_ in texts:
        question = make_objective_question(
            db, subject.id, topic.id, None, text_, passage_ids=(passages[0].id,)
        )
        answered = make_answer(
            db, round_, question, at=round_.started_at, band="weak", factual=-5.0, prep_ms=500
        )
        row = db.query(AttemptProcessing).filter_by(attempt_id=answered.id).one()
        row.transcript_text = text_
    db.commit()
    files, _, _ = export.render(db, ZONE)
    reader = csv.DictReader(io.StringIO(files["Formuły/answers.csv"].decode("utf-8-sig")))
    rows = list(reader)
    expected = [f"'{t}" for t in texts[:-1]] + ["Plain -1"]
    assert [r["question"] for r in rows] == expected
    assert [r["transcript"] for r in rows] == expected
    assert {r["factual"] for r in rows} == {"-5.0"}  # numbers are left as they are
    md = files[f"Formuły/round-{round_.id}.md"].decode("utf-8")
    assert "\n=HYPERLINK" in md  # only the CSV cells are guarded
