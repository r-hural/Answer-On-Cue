"""Standard question selection: pure, reproducible from the round's seed."""

import random

from pydantic import BaseModel

from app.features.rounds.schemas import RoundConfig


class InsufficientQuestions(Exception):
    def __init__(self, available: int) -> None:
        super().__init__(f"only {available} eligible questions")
        self.available = available


class Candidate(BaseModel):
    question_version_id: int
    family_id: int
    primary_topic_id: int | None
    style: str
    difficulty: str
    document_ids: list[int]  # documents of the rubric's cited passages
    last_asked_round: int | None  # rounds ago this family was asked (1 = previous), None = never
    objective_id: int | None = None
    source_policy: str = "materials"  # a pool is never filled from the other policy
    chapter_id: int | None = None  # the primary topic's chapter


def eligible(candidates: list[Candidate], config: RoundConfig) -> list[Candidate]:
    """Within the chosen scope, style and difficulty only; at most one version per family."""
    scope = config.scope
    topics, documents = set(scope.topic_ids), set(scope.document_ids)
    by_family: dict[int, Candidate] = {}
    for c in candidates:
        if scope.kind == "topics" and c.primary_topic_id not in topics:
            continue
        if scope.kind == "documents" and not documents.intersection(c.document_ids):
            continue
        if scope.kind == "chapter" and c.chapter_id != scope.chapter_id:
            continue
        if config.style != "mixed" and c.style != config.style:
            continue
        if config.difficulty != "mixed" and c.difficulty != config.difficulty:
            continue
        if config.source_policy != "all" and c.source_policy != config.source_policy:
            continue
        kept = by_family.get(c.family_id)
        if kept is None or c.question_version_id > kept.question_version_id:
            by_family[c.family_id] = c
    return sorted(by_family.values(), key=lambda c: c.question_version_id)


def _freshness(c: Candidate) -> tuple[int, int]:
    # Never asked first, then the longest ago first.
    if c.last_asked_round is None:
        return (0, 0)
    return (1, -c.last_asked_round)


def select_standard(candidates: list[Candidate], config: RoundConfig, seed: int) -> list[int]:
    """question_version_ids in round order; never fills up from outside the chosen scope."""
    pool = eligible(candidates, config)
    if len(pool) < config.question_count:
        raise InsufficientQuestions(len(pool))
    random.Random(seed).shuffle(pool)
    pool.sort(key=_freshness)  # stable: the shuffle breaks ties
    queues: dict[int | None, list[Candidate]] = {}
    for c in pool:
        queues.setdefault(c.primary_topic_id, []).append(c)
    order = sorted(queues, key=lambda t: (t is None, t or 0))
    chosen: list[int] = []
    while len(chosen) < config.question_count:
        for topic in order:
            if queues[topic] and len(chosen) < config.question_count:
                chosen.append(queues[topic].pop(0).question_version_id)
    return chosen
