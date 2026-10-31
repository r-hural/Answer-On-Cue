import pytest

from app.features.rounds.schemas import RoundConfig, Scope
from app.features.rounds.selection import Candidate, InsufficientQuestions, select_standard

_next_id = iter(range(1, 10_000))


def cand(
    topic: int | None,
    style: str = "open",
    difficulty: str = "recall",
    documents: tuple[int, ...] = (),
    last: int | None = None,
    family: int | None = None,
    qid: int | None = None,
) -> Candidate:
    qid = qid or next(_next_id)
    return Candidate(
        question_version_id=qid,
        family_id=family or qid,
        primary_topic_id=topic,
        style=style,
        difficulty=difficulty,
        document_ids=list(documents),
        last_asked_round=last,
    )


def config(count: int, **kwargs) -> RoundConfig:
    kwargs.setdefault("scope", Scope(kind="subject"))
    return RoundConfig(question_count=count, **kwargs)


def ids_by_topic(candidates: list[Candidate]) -> dict[int, int | None]:
    return {c.question_version_id: c.primary_topic_id for c in candidates}


def test_topic_scope_only():
    pool = [cand(1), cand(1), cand(2), cand(2), cand(None)]
    chosen = select_standard(pool, config(2, scope=Scope(kind="topics", topic_ids=[2])), 1)
    topics = ids_by_topic(pool)
    assert {topics[i] for i in chosen} == {2}


def test_document_scope_only():
    a, b, c = cand(1, documents=(10,)), cand(1, documents=(11, 12)), cand(2, documents=(12,))
    pool = [a, b, c, cand(2, documents=())]
    chosen = select_standard(pool, config(2, scope=Scope(kind="documents", document_ids=[12])), 3)
    assert set(chosen) == {b.question_version_id, c.question_version_id}


def test_style_and_difficulty_filters():
    want = cand(1, "short", "apply")
    pool = [want, cand(1, "open", "apply"), cand(1, "short", "recall"), cand(2, "open", "analysis")]
    assert select_standard(pool, config(1, style="short", difficulty="apply"), 5) == [
        want.question_version_id
    ]
    with pytest.raises(InsufficientQuestions):
        select_standard(pool, config(2, style="short", difficulty="apply"), 5)


def test_mixed_means_any():
    pool = [cand(1, "short", "apply"), cand(1, "open", "recall"), cand(2, "open", "analysis")]
    assert len(select_standard(pool, config(3), 9)) == 3


def test_round_robin_across_topics():
    pool = [cand(1) for _ in range(5)] + [cand(2)] + [cand(3) for _ in range(2)]
    topics = ids_by_topic(pool)
    chosen = select_standard(pool, config(6), 4)
    assert [topics[i] for i in chosen] == [1, 2, 3, 1, 3, 1]


def test_no_topic_comes_last():
    pool = [cand(None), cand(7), cand(3)]
    topics = ids_by_topic(pool)
    assert [topics[i] for i in select_standard(pool, config(3), 0)] == [3, 7, None]


def test_never_asked_first_then_longest_ago():
    recent, old, never, older = cand(1, last=1), cand(1, last=3), cand(1), cand(1, last=5)
    pool = [recent, old, never, older]
    for seed in range(5):
        assert select_standard(pool, config(4), seed) == [
            never.question_version_id,
            older.question_version_id,
            old.question_version_id,
            recent.question_version_id,
        ]


def test_one_version_per_family():
    pool = [cand(1, family=900), cand(1, family=900), cand(1, family=901)]
    chosen = select_standard(pool, config(2), 2)
    assert len(chosen) == 2
    families = {c.question_version_id: c.family_id for c in pool}
    assert {families[i] for i in chosen} == {900, 901}
    with pytest.raises(InsufficientQuestions) as error:
        select_standard(pool, config(3), 2)
    assert error.value.available == 2


def test_same_seed_same_order():
    pool = [cand(t) for t in (1, 1, 1, 2, 2, 2, 3, 3)]
    first = select_standard(pool, config(6), 42)
    assert select_standard(list(reversed(pool)), config(6), 42) == first
    orders = {tuple(select_standard(pool, config(6), seed)) for seed in range(20)}
    assert len(orders) > 1  # the seed matters among ties


def test_insufficient_raises_with_available_and_never_fills_from_outside():
    inside = [cand(1, "short"), cand(1, "short", documents=(5,))]
    outside = [cand(2, "short"), cand(1, "open"), cand(None, "short")]
    scope = Scope(kind="topics", topic_ids=[1])
    with pytest.raises(InsufficientQuestions) as error:
        select_standard(inside + outside, config(3, scope=scope, style="short"), 1)
    assert error.value.available == 2
    chosen = select_standard(inside + outside, config(2, scope=scope, style="short"), 1)
    assert set(chosen) == {c.question_version_id for c in inside}
