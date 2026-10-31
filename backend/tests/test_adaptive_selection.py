from collections import Counter
from datetime import UTC, datetime

import pytest

from app.features.rounds.adaptive import (
    LineInfo,
    WeakTarget,
    allocate,
    category,
    line_of,
    select_adaptive,
    select_extension,
)
from app.features.rounds.schemas import RoundConfig, Scope
from app.features.rounds.selection import Candidate, InsufficientQuestions, select_standard

DAY = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def info(status, *, assisted=False, factual=None, at=None, answers=1, topic=1, order=0):
    return LineInfo(status, assisted, factual, at, answers, topic, order)


def cand(vid, *, topic=1, objective=None, difficulty="recall", style="short", asked=None):
    return Candidate(
        question_version_id=vid,
        family_id=vid,
        primary_topic_id=topic,
        style=style,
        difficulty=difficulty,
        document_ids=[],
        last_asked_round=asked,
        objective_id=objective,
    )


def config(n, **extra) -> RoundConfig:
    extra.setdefault("scope", Scope(kind="subject"))
    return RoundConfig(question_count=n, **extra)


def test_categories():
    assert category(info("needs_work", factual=20)) == "weak"
    assert category(info("improving", assisted=True, factual=70)) == "weak"
    assert category(info("improving", factual=70)) == "review"
    assert category(info("strong", factual=90)) == "review"
    assert category(info("not_practised", answers=0)) == "unpractised"
    assert category(None) == "unpractised"


def test_line_of():
    assert line_of(cand(1, topic=4, objective=9)) == ("objective", 9)
    assert line_of(cand(1, topic=4)) == ("topic", 4)
    assert line_of(cand(1, topic=None)) is None


PLENTY = {"weak": 30, "unpractised": 30, "review": 30}


@pytest.mark.parametrize(
    "n,split", [(10, (6, 3, 1)), (5, (3, 1, 1)), (4, (2, 1, 1)), (2, (1, 1, 0)), (1, (1, 0, 0))]
)
def test_allocate_defaults(n, split):
    quotas = allocate(n, PLENTY)
    assert (quotas["weak"], quotas["unpractised"], quotas["review"]) == split


@pytest.mark.parametrize(
    "available,split",
    [
        ({"weak": 2, "unpractised": 30, "review": 30}, (2, 5, 3)),
        ({"weak": 30, "unpractised": 30, "review": 0}, (7, 3, 0)),
        ({"weak": 0, "unpractised": 12, "review": 0}, (0, 10, 0)),
        ({"weak": 1, "unpractised": 1, "review": 30}, (1, 1, 8)),
    ],
)
def test_allocate_redistributes(available, split):
    quotas = allocate(10, available)
    assert (quotas["weak"], quotas["unpractised"], quotas["review"]) == split
    assert sum(quotas.values()) == 10
    assert all(quotas[c] <= available[c] for c in quotas)


def _mixed_world():
    """8 weak, 8 unpractised and 8 review questions over 6 lines."""
    lines = {
        ("objective", 1): info("needs_work", factual=20, at=DAY, topic=1, order=0),
        ("objective", 2): info("improving", assisted=True, factual=70, at=DAY, topic=1, order=1),
        ("objective", 3): info("not_practised", answers=0, topic=2, order=2),
        ("objective", 4): info("not_practised", answers=0, topic=2, order=3),
        ("objective", 5): info("strong", factual=90, at=DAY, topic=3, order=4),
        ("objective", 6): info("improving", factual=70, at=DAY, topic=3, order=5),
    }
    topic_of = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3}
    pool = [
        cand(10 * objective + n, topic=topic_of[objective], objective=objective)
        for objective in range(1, 7)
        for n in range(4)
    ]
    return pool, lines


def test_select_adaptive_mix():
    pool, lines = _mixed_world()
    chosen = select_adaptive(pool, lines, config(10), seed=7)
    assert Counter(s for _, s in chosen) == {"weak": 6, "unpractised": 3, "review": 1}
    ids = [i for i, _ in chosen]
    assert len(set(ids)) == 10
    assert select_adaptive(pool, lines, config(10), seed=7) == chosen
    assert select_adaptive(pool, lines, config(10), seed=8) != chosen
    by_id = {c.question_version_id: c for c in pool}
    for vid, selection in chosen:
        assert category(lines[line_of(by_id[vid])]) == selection


def test_one_version_per_family():
    pool, lines = _mixed_world()
    twin = cand(999, topic=1, objective=1)
    twin.family_id = 10  # a newer version of family 10
    chosen = select_adaptive([*pool, twin], lines, config(10), seed=1)
    assert 10 not in {i for i, _ in chosen}  # only the family's newest version is eligible


def _weak_lines_world():
    lines = {
        ("objective", 1): info("needs_work", factual=10, at=DAY, order=0),
        ("objective", 2): info("needs_work", factual=40, at=DAY, order=1),
        ("objective", 3): info("needs_work", factual=55, at=DAY, order=2),
        ("objective", 4): info("not_practised", answers=0, topic=2, order=3),
        ("objective", 5): info("strong", factual=90, at=DAY, topic=3, order=4),
    }
    pool = [cand(10 * o + n, objective=o) for o in (1, 2, 3) for n in range(3)]
    pool += [cand(40 + n, topic=2, objective=4) for n in range(3)]
    pool += [cand(50 + n, topic=3, objective=5) for n in range(3)]
    return pool, lines


def test_weak_lines_spread_and_weakest_first():
    pool, lines = _weak_lines_world()
    five = select_adaptive(pool, lines, config(5), seed=3)  # quotas 3/1/1
    weak = sorted(i // 10 for i, s in five if s == "weak")
    assert weak == [1, 2, 3]
    three = select_adaptive(pool, lines, config(3), seed=3)  # quotas 2/1/0
    weak = sorted(i // 10 for i, s in three if s == "weak")
    assert weak == [1, 2]


def test_never_practised_line_comes_first_on_a_tie():
    # A line without a date sorts before any dated line with the same factual.
    weak = {
        ("objective", 1): info("needs_work", factual=10, at=DAY, order=0),
        ("objective", 2): info("needs_work", factual=10, at=None, order=1),
    }
    review = {
        ("objective", 1): info("strong", factual=90, at=DAY, order=0),
        ("objective", 2): info("strong", factual=90, at=None, order=1),
    }
    pool = [cand(1, objective=1), cand(2, objective=2)]
    for seed in range(5):
        assert select_adaptive(pool, weak, config(1), seed=seed) == [(2, "weak")]
        assert select_adaptive(pool, review, config(1), seed=seed) == [(2, "review")]


def test_difficulty_preference():
    weak = {("objective", 1): info("needs_work", factual=10, at=DAY)}
    review = {("objective", 1): info("strong", factual=90, at=DAY)}
    pool = [
        cand(1, objective=1, difficulty="analysis"),
        cand(2, objective=1, difficulty="recall"),
        cand(3, objective=1, difficulty="apply"),
    ]
    for seed in range(5):
        assert select_adaptive(pool, weak, config(1), seed=seed) == [(2, "weak")]
        assert select_adaptive(pool, review, config(1), seed=seed) == [(1, "review")]
        # A fixed difficulty keeps only its own questions.
        assert select_adaptive(pool, weak, config(1, difficulty="apply"), seed=seed) == [
            (3, "weak")
        ]


def test_freshness_first_within_line():
    lines = {("objective", 1): info("needs_work", factual=10, at=DAY)}
    pool = [cand(1, objective=1, asked=1), cand(2, objective=1)]
    for seed in range(5):
        assert select_adaptive(pool, lines, config(1), seed=seed) == [(2, "weak")]


def test_new_subject_is_balanced_like_standard():
    pool, lines = _mixed_world()
    fresh = {
        k: info("not_practised", answers=0, topic=v.topic_id, order=v.order)
        for k, v in lines.items()
    }
    chosen = select_adaptive(pool, fresh, config(6), seed=11)
    assert [i for i, _ in chosen] == select_standard(pool, config(6), 11)
    assert {s for _, s in chosen} == {"balanced"}


def test_scope_respected():
    pool, lines = _mixed_world()
    scope = Scope(kind="topics", topic_ids=[2, 3])
    chosen = select_adaptive(pool, lines, config(6, scope=scope), seed=2)
    by_id = {c.question_version_id: c for c in pool}
    assert all(by_id[i].primary_topic_id in (2, 3) for i, _ in chosen)
    with pytest.raises(InsufficientQuestions) as error:
        select_adaptive(pool, lines, config(17, scope=scope), seed=2)
    assert error.value.available == 16


def test_select_extension():
    o1_topic, t2 = 1, 2
    pool = [
        cand(1, topic=o1_topic, objective=11),
        cand(2, topic=o1_topic, objective=11, asked=2),
        cand(3, topic=o1_topic),  # O1's topic, no objective
        cand(4, topic=t2),
        cand(5, topic=t2, asked=1),
        cand(6, topic=o1_topic, objective=11),  # asked in the parent round
        cand(7, topic=3),  # another topic
        cand(8, topic=o1_topic, objective=11, style="open"),  # outside the style
    ]
    targets = [
        WeakTarget(objective_id=None, topic_id=t2, factual=45, ordinal=1),
        WeakTarget(objective_id=11, topic_id=o1_topic, factual=0, ordinal=3),
    ]
    parent = config(4, style="short")
    three = select_extension(pool, targets, parent, 3, seed=5, exclude_families={6})
    assert three == [1, 4, 2]  # O1, T2, O1: the never-asked question first
    everything = select_extension(pool, targets, parent, 10, seed=5, exclude_families={6})
    assert sorted(everything) == [1, 2, 3, 4, 5]
    scoped = config(4, style="short", scope=Scope(kind="topics", topic_ids=[t2]))
    assert select_extension(pool, targets, scoped, 10, seed=5, exclude_families={6}) == [4, 5]
    assert select_extension(pool, [], parent, 3, seed=5, exclude_families=set()) == []
