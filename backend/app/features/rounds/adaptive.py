"""Adaptive selection: pure, reproducible from the round's seed.

The categories come from the coverage lines, never from re-classified scores: weak lines are
practised most, unpractised ones next, and stronger ones are reviewed now and then.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.features.coverage.rules import CoverageStatus
from app.features.coverage.schemas import SubjectCoverage
from app.features.rounds.schemas import RoundConfig, Selection
from app.features.rounds.selection import (
    Candidate,
    InsufficientQuestions,
    _freshness,
    eligible,
    select_standard,
)

Category = Literal["weak", "unpractised", "review"]
CATEGORIES: tuple[Category, ...] = ("weak", "unpractised", "review")  # also the tie order
ALLOCATION: dict[Category, int] = {"weak": 60, "unpractised": 25, "review": 15}
DIFFICULTY_RANK = {"recall": 0, "apply": 1, "analysis": 2}

LineKey = tuple[Literal["objective", "topic"], int]


@dataclass(frozen=True)
class LineInfo:
    status: CoverageStatus
    assisted: bool
    factual: float | None  # the deciding evidence's factual score
    last_practised_at: datetime | None
    answers: int
    topic_id: int
    order: int  # position in the coverage map (topic order, then line order)


@dataclass(frozen=True)
class WeakTarget:
    """A main answer of the parent round that fell below Good."""

    objective_id: int | None
    topic_id: int | None
    factual: float
    ordinal: int


_NO_LINE = LineInfo("not_practised", False, None, None, 0, 0, 0)


def line_of(c: Candidate) -> LineKey | None:
    """The coverage line a question counts for: its objective, else its topic's whole line."""
    if c.objective_id is not None:
        return ("objective", c.objective_id)
    if c.primary_topic_id is not None:
        return ("topic", c.primary_topic_id)
    return None


def lines_from_coverage(coverage: SubjectCoverage) -> dict[LineKey, LineInfo]:
    lines: dict[LineKey, LineInfo] = {}
    order = 0
    for topic in coverage.topics:
        for line in topic.objectives:
            key: LineKey = (
                ("objective", line.objective_id)
                if line.objective_id is not None
                else ("topic", topic.topic_id)
            )
            lines[key] = LineInfo(
                status=line.status,
                assisted=line.assisted,
                factual=line.deciding.factual if line.deciding is not None else None,
                last_practised_at=line.last_practised_at,
                answers=line.answers,
                topic_id=topic.topic_id,
                order=order,
            )
            order += 1
    return lines


def category(line: LineInfo | None) -> Category:
    """Help-only success is weaker evidence than an independent one."""
    if line is None or line.status == "not_practised":
        return "unpractised"
    if line.status == "needs_work" or (line.status == "improving" and line.assisted):
        return "weak"
    return "review"


def _largest_remainder(n: int, weights: dict[Category, int]) -> dict[Category, int]:
    total = sum(weights.values())
    if total == 0:
        return dict.fromkeys(weights, 0)
    quotas = {c: n * w // total for c, w in weights.items()}
    left = n - sum(quotas.values())
    ranked = sorted(weights, key=lambda c: (-(n * weights[c] % total), CATEGORIES.index(c)))
    for c in ranked[:left]:
        quotas[c] += 1
    return quotas


def allocate(
    n: int, available: dict[Category, int], weights: dict[Category, int] = ALLOCATION
) -> dict[Category, int]:
    """Quotas by weight; a category short of its quota passes the rest on to the others."""
    fixed: dict[Category, int] = {}
    while True:
        rest = {c: weights[c] for c in CATEGORIES if c not in fixed}
        quotas = _largest_remainder(n - sum(fixed.values()), rest)
        over = [c for c in rest if quotas[c] > available.get(c, 0)]
        if not over:
            break
        for c in over:
            fixed[c] = available.get(c, 0)
        if len(fixed) == len(CATEGORIES):
            quotas = {}
            break
    return {c: fixed.get(c, quotas.get(c, 0)) for c in CATEGORIES}


def _oldest_first(at: datetime | None) -> tuple[int, datetime]:
    # Never practised before any date; every datetime compared UTC-aware.
    if at is None:
        return (0, datetime.min.replace(tzinfo=UTC))
    return (1, at if at.tzinfo is not None else at.replace(tzinfo=UTC))


def _line_order(
    cat: Category, lines: dict[LineKey, LineInfo], keys: list[LineKey | None]
) -> list[LineKey | None]:
    def info(key: LineKey | None) -> LineInfo:
        return lines.get(key) or _NO_LINE

    if cat == "weak":
        return sorted(
            keys,
            key=lambda k: (
                info(k).factual if info(k).factual is not None else 0.0,
                _oldest_first(info(k).last_practised_at),
                info(k).order,
            ),
        )
    if cat == "review":
        return sorted(
            keys,
            key=lambda k: (
                info(k).status != "improving",
                _oldest_first(info(k).last_practised_at),
                info(k).order,
            ),
        )

    # Unpractised: round robin over the topics, in the coverage map's order; lines the map
    # does not show (a removed topic, no topic) come last.
    def topic_rank(k: LineKey | None) -> tuple[int, int]:
        line = lines.get(k)
        if line is not None:
            return (0, line.order)
        return (1, k[1] if k is not None else 0)

    by_topic: dict[tuple, list[LineKey | None]] = {}
    for k in sorted(keys, key=topic_rank):
        line = lines.get(k)
        topic = ("map", line.topic_id) if line is not None else ("loose", k)
        by_topic.setdefault(topic, []).append(k)
    ranked = []
    for topic_index, members in enumerate(by_topic.values()):
        for n, k in enumerate(members):
            ranked.append(((n, topic_index), k))
    return [k for _, k in sorted(ranked, key=lambda pair: pair[0])]


def _question_order(
    cat: Category, queue: list[Candidate], config: RoundConfig, rng: random.Random
) -> list[Candidate]:
    rng.shuffle(queue)  # the seed breaks the remaining ties

    def rank(c: Candidate) -> int:
        if config.difficulty != "mixed" or cat == "unpractised":
            return 0
        value = DIFFICULTY_RANK.get(c.difficulty, 1)
        return value if cat == "weak" else -value

    return sorted(queue, key=lambda c: (_freshness(c), rank(c)))


def _round_robin(queues: list[list[Candidate]], quota: int) -> list[Candidate]:
    """One question per line per pass, until the quota is met or the lines run out."""
    chosen: list[Candidate] = []
    while len(chosen) < quota and any(queues):
        for queue in queues:
            if queue and len(chosen) < quota:
                chosen.append(queue.pop(0))
    return chosen


def select_adaptive(
    candidates: list[Candidate],
    lines: dict[LineKey, LineInfo],
    config: RoundConfig,
    seed: int,
) -> list[tuple[int, Selection]]:
    """(question_version_id, why) in round order; the same pool as a standard round."""
    pool = eligible(candidates, config)
    n = config.question_count
    if len(pool) < n:
        raise InsufficientQuestions(len(pool))
    if not any((lines.get(line_of(c)) or _NO_LINE).answers for c in pool):
        # A subject without answers starts with balanced coverage.
        return [(i, "balanced") for i in select_standard(candidates, config, seed)]
    rng = random.Random(seed)
    grouped: dict[Category, dict] = {c: {} for c in CATEGORIES}
    for c in pool:
        key = line_of(c)
        grouped[category(lines.get(key))].setdefault(key, []).append(c)
    quotas = allocate(n, {cat: sum(map(len, grouped[cat].values())) for cat in CATEGORIES})
    chosen: list[tuple[int, Selection]] = []
    for cat in CATEGORIES:
        keys = _line_order(cat, lines, list(grouped[cat]))
        queues = [_question_order(cat, grouped[cat][k], config, rng) for k in keys]
        chosen += [(c.question_version_id, cat) for c in _round_robin(queues, quotas[cat])]
    random.Random(seed).shuffle(chosen)  # the position reveals nothing
    return chosen


def select_extension(
    candidates: list[Candidate],
    targets: list[WeakTarget],
    config: RoundConfig,
    count: int,
    seed: int,
    exclude_families: set[int],
) -> list[int]:
    """New questions on the weak answers' objectives, then their topics; may be fewer."""
    pool = [c for c in eligible(candidates, config) if c.family_id not in exclude_families]
    rng = random.Random(seed)
    rng.shuffle(pool)
    pool.sort(key=_freshness)  # stable: the shuffle breaks ties
    ordered = sorted(targets, key=lambda t: (t.factual, t.ordinal))
    seen: set[tuple[int | None, int | None]] = set()
    queues: list[list[Candidate]] = []
    for target in ordered:
        if (target.objective_id, target.topic_id) in seen:
            continue
        seen.add((target.objective_id, target.topic_id))
        objective = [
            c
            for c in pool
            if target.objective_id is not None and c.objective_id == target.objective_id
        ]
        taken = {c.question_version_id for c in objective}
        topic = [
            c
            for c in pool
            if target.topic_id is not None
            and c.primary_topic_id == target.topic_id
            and c.question_version_id not in taken
        ]
        queues.append(objective + topic)
    chosen: list[int] = []
    used: set[int] = set()
    while len(chosen) < count:
        moved = False
        for queue in queues:
            while queue and queue[0].question_version_id in used:
                queue.pop(0)
            if queue and len(chosen) < count:
                picked = queue.pop(0)
                used.add(picked.question_version_id)
                chosen.append(picked.question_version_id)
                moved = True
        if not moved:
            break
    return chosen
