"""The A/B run with fakes: the planned calls exactly, an escaped page, the copy only."""

import html
import json
from datetime import datetime

import pytest
from test_prompt_ab_support import (
    INJECTION,
    build_source,
    disk_show,
    fake_generators,
    needs_git,
    sha256,
)
from test_topics_support import make_subject, upgrade

from app.db.session import SessionLocal
from app.features.languages.models import ContentLanguage
from app.features.prompt_ab.page import english_findings
from app.features.prompt_ab.run import Refused, RunOptions, run

pytestmark = needs_git


@pytest.fixture
def source(client, settings, tmp_path):
    return build_source(client, settings, tmp_path / "src" / "answeroncue.db")


def go(settings, tmp_path, db, factory=None, **options):
    factory = factory or fake_generators()
    options.setdefault("out_dir", tmp_path / "out")
    page = run(
        settings,
        RunOptions(db=db, old_ref="HEAD", **options),
        show=disk_show,
        generators=factory,
        clock=lambda: datetime(2026, 10, 25, 12, 0, 0),
        allow_same_versions=True,
    )
    return page, factory


def calls_of(factory) -> list[dict]:
    return factory.generation.calls + factory.assessment.calls


def results(page) -> dict:
    return json.loads((page.parent / "results.json").read_text(encoding="utf-8"))


def test_dry_run_makes_no_call(settings, tmp_path, source):
    page, factory = go(settings, tmp_path, source, dry_run=True)
    assert page.name == "index.html" and page.exists()
    assert calls_of(factory) == [] and factory.built == []
    text = page.read_text(encoding="utf-8")
    assert "0 made / 19 planned / 30 limit" in text
    found = results(page)
    old = [c for c in found["cells"] if c["side"] == "old"]
    new = [c for c in found["cells"] if c["side"] == "new"]
    assert len(old) == len(new) == 7
    assert all(c["system"] and c["user"] for c in old + new)
    assert "Source policy: " in next(c["user"] for c in new if c["case"] == "generate")


def test_full_run_makes_exactly_the_planned_calls(settings, tmp_path, source):
    page, factory = go(settings, tmp_path, source)
    calls = calls_of(factory)
    assert len(calls) == 19
    ids = [c["operation_id"] for c in calls]
    assert sum(i.endswith(":old") for i in ids) == 7
    assert sum(i.endswith(":new") for i in ids) == 7
    assert sum(i.endswith(":en") for i in ids) == 5
    assert all(i.startswith("prompt-ab-20261025-120000:") for i in ids)
    found = results(page)
    assert found["calls"] == {"made": 19, "planned": 19, "limit": 30}
    assert len(found["operation_ids"]) == 19
    for cell in found["cells"]:
        assert cell["error"] is None, cell
        assert cell["output"] is not None, cell["case"]
    text = page.read_text(encoding="utf-8")
    assert text.count("<th>Stored result</th>") == 3
    assert "<th>Old</th><th>New</th>" in text


def test_budget_refused_before_any_call(settings, tmp_path, source):
    factory = fake_generators()
    with pytest.raises(Refused, match="19 calls"):
        go(settings, tmp_path, source, factory, calls=10)
    with pytest.raises(Refused, match="between 1 and 30"):
        go(settings, tmp_path, source, factory, calls=31)
    with pytest.raises(Refused):
        go(settings, tmp_path, source, factory, calls=0)
    assert calls_of(factory) == [] and factory.built == []


def test_page_escapes_content(settings, tmp_path, source):
    page, _ = go(settings, tmp_path, source)
    text = page.read_text(encoding="utf-8")
    assert INJECTION not in text
    assert html.escape(INJECTION) in text
    assert "<script" not in text


def test_missing_inputs_shrink_the_plan(client, settings, tmp_path):
    db = build_source(client, settings, tmp_path / "src" / "answeroncue.db", answers=0)
    page, factory = go(settings, tmp_path, db)
    ids = [c["operation_id"] for c in calls_of(factory)]
    assert sum(i.endswith(":old") for i in ids) == 3  # extract, generate, verify
    assert sum(i.endswith(":new") for i in ids) == 3
    found = results(page)
    assert found["calls"]["planned"] == 6 + 5
    assert found["missing"]["assessments"] == "no assessed answers"
    assert "no assessed answers" in page.read_text(encoding="utf-8")


def test_non_polish_subject_refused(client, settings, tmp_path):
    upgrade(settings.effective_database_url)
    with SessionLocal(settings.effective_database_url) as db:
        db.get(ContentLanguage, "en").enabled = True
        subject = make_subject(db, "History")
        subject.language = "en"
        db.commit()
        subject_id = subject.id
    db_path = build_source(client, settings, tmp_path / "src" / "answeroncue.db", answers=0)
    factory = fake_generators()
    with pytest.raises(Refused, match="not Polish"):
        go(settings, tmp_path, db_path, factory, subject_id=subject_id)
    assert calls_of(factory) == []


def test_english_check():
    assert english_findings({"text": "A clean English answer."}) == "English throughout: yes"
    assert english_findings({"text": "The answer jest here."}).startswith("English throughout: no")
    assert "ł" in english_findings({"text": "Wrocław"})


def test_the_copy_not_the_source(settings, tmp_path, source):
    before = sha256(source)
    go(settings, tmp_path, source)
    assert sha256(source) == before


def test_old_prompts_identical_refused(settings, tmp_path, source):
    factory = fake_generators()
    with pytest.raises(Refused, match="same prompt versions"):
        run(
            settings,
            RunOptions(db=source, old_ref="HEAD", out_dir=tmp_path / "out"),
            show=disk_show,
            generators=factory,
        )
    assert calls_of(factory) == []
