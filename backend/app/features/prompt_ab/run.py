"""The A/B run: safety checks, the call plan and its budget, the calls.

Reads only an upgraded copy of the given database. The only writes outside the run folder are
the usage rows of the live calls, in the running settings' own database.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import APP_DIR, Settings
from app.db.session import make_engine
from app.features.assessment.schemas import (
    DEFAULT_BAND_BOUNDS,
    DEFAULT_FACTUAL_WEIGHTS,
    AiAssessment,
    AssessmentInput,
)
from app.features.assessment.scoring import classify_band, compute_scores
from app.features.assessment.service import ASSESS_TASK, validate_output
from app.features.assessment.service import MAX_OUTPUT_TOKENS as ASSESS_TOKENS
from app.features.costs.prices import price_for, usd
from app.features.follow_ups.service import FOLLOW_UP_TASK
from app.features.follow_ups.service import MAX_OUTPUT_TOKENS as FOLLOW_UP_TOKENS
from app.features.languages.profiles import PROFILES
from app.features.prompt_ab import english
from app.features.prompt_ab.inputs import AbInputs, Refused, select_inputs
from app.features.prompt_ab.loader import (
    PromptModules,
    current_prompt_modules,
    git_show,
    load_prompt_modules,
    resolve_ref,
)
from app.features.prompt_ab.page import english_findings, write_page
from app.features.prompt_ab.prompt_sets import ProfilePromptSet, PromptSet, prompt_set
from app.features.providers.base import TextGenerator
from app.features.providers.registry import get_text_generator, resolve_role
from app.features.providers.types import Message
from app.features.providers.usage import get_durable_usage_recorder
from app.features.questions import service as questions
from app.features.questions import validation
from app.features.topics.service import MAX_OUTPUT_TOKENS as TOPICS_TOKENS

__all__ = ["MAX_CALLS", "Refused", "RunOptions", "check_database", "copy_database", "run"]

MAX_CALLS = 30
BACKEND = APP_DIR.parent
APP_DATA_MARKERS = ("settings.env", "recordings", "documents", "voice-cache", "backups")
SQLITE_HEADER = b"SQLite format 3\x00"
ENGLISH_SLOTS = questions.slot_specs("mixed", "mixed", 0, 2)


@dataclass(frozen=True)
class RunOptions:
    db: Path
    old_ref: str
    calls: int = MAX_CALLS
    subject_id: int | None = None
    dry_run: bool = False
    out_dir: Path | None = None


@dataclass(frozen=True)
class PlannedCall:
    case: str
    side: str  # "old", "new" (Polish) or "en" (the English sample, new prompts)
    task: str
    role: str  # "generation" or "assessment"


@dataclass
class Cell:
    case: str
    side: str
    task: str
    system: str = ""
    user: str = ""
    called: bool = False
    output: dict[str, Any] | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


GeneratorFactory = Callable[[Settings], tuple[TextGenerator, TextGenerator]]


def live_generators(settings: Settings) -> tuple[TextGenerator, TextGenerator]:
    """(generation, assessment), recording usage like the app; only built for a real run."""
    return (
        get_text_generator(settings, "generation", get_durable_usage_recorder(settings)),
        get_text_generator(settings, "assessment", get_durable_usage_recorder(settings)),
    )


# ---- safety ----


def _running_database(settings: Settings) -> Path | None:
    url = settings.effective_database_url
    prefix = "sqlite:///"
    return Path(url[len(prefix) :]) if url.startswith(prefix) else None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return a.resolve() == b.resolve()


def check_database(path: Path, settings: Settings) -> None:
    """Refuses, before reading anything, a database this tool must not touch."""
    path = Path(path)
    if not path.is_file():
        raise Refused(f"No such database file: {path}")
    with path.open("rb") as file:
        if file.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise Refused(f"Not a SQLite database: {path}")
    running = _running_database(settings)
    if running is not None and _same_file(path, running):
        raise Refused("This is the running app's own database. Use a copy in an empty folder.")
    found = [name for name in APP_DATA_MARKERS if (path.parent / name).exists()]
    if found:
        raise Refused(
            f"{path.parent} looks like an app data folder (it holds {', '.join(found)}). "
            "Copy the database into an empty folder first."
        )
    for suffix in ("-wal", "-journal"):
        if path.with_name(path.name + suffix).exists():
            raise Refused(f"The database is in use ({path.name}{suffix} beside it). Close the app.")


def copy_database(path: Path, dest: Path) -> Path:
    """A consistent copy through the SQLite backup API, upgraded to head; only the copy changes."""
    if dest.exists():
        raise Refused(f"{dest} already exists: the copy goes into a new file only.")
    if dest.parent.exists() and dest.parent.resolve() == Path(path).resolve().parent:
        raise Refused("The copy cannot go into the database's own folder.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        target = sqlite3.connect(dest)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    # Without the url, migrations/env.py would upgrade the running settings' database.
    cfg.cmd_opts = type("Opts", (), {"x": [f"url=sqlite:///{dest.as_posix()}"]})()
    command.upgrade(cfg, "head")
    return dest


# ---- the plan ----


def _polish_cases(inputs: AbInputs) -> list[tuple[str, str, str]]:
    """(case, task, role) of one Polish side."""
    cases = []
    if inputs.extraction is not None:
        cases.append(("extract", "topics.extract_section", "generation"))
    if inputs.bank is not None:
        cases.append(("generate", questions.GENERATE_KIND, "generation"))
        cases.append(("verify", "questions.verify", "generation"))
    for case in inputs.assessments:
        cases.append((f"assess-{case.attempt_id}", ASSESS_TASK, "assessment"))
    if inputs.follow_up is not None:
        cases.append(("follow_up", FOLLOW_UP_TASK, "generation"))
    return cases


ENGLISH_CASES = [
    ("extract", "topics.extract_section", "generation"),
    ("generate", questions.GENERATE_KIND, "generation"),
    ("verify", "questions.verify", "generation"),
    ("assess", ASSESS_TASK, "assessment"),
    ("follow_up", FOLLOW_UP_TASK, "generation"),
]


def plan_calls(inputs: AbInputs) -> list[PlannedCall]:
    plan = [
        PlannedCall(case, side, task, role)
        for side in ("old", "new")
        for case, task, role in _polish_cases(inputs)
    ]
    plan += [PlannedCall(case, "en", task, role) for case, task, role in ENGLISH_CASES]
    return plan


# ---- the calls ----


@dataclass
class Spending:
    limit: int
    made: list[str] = field(default_factory=list)
    usd: float = 0.0


class CountingGenerator:
    """Counts every call against the plan; one call past it is refused before it is made."""

    def __init__(self, inner: TextGenerator, spending: Spending, role: str) -> None:
        self.inner = inner
        self.spending = spending
        self.role = role

    def generate_structured(self, task, system, messages, schema, *, operation_id, **kwargs):
        if len(self.spending.made) >= self.spending.limit:
            raise Refused(f"Call {operation_id} would go past the {self.spending.limit} planned.")
        self.spending.made.append(operation_id)
        response = self.inner.generate_structured(
            task, system, messages, schema, operation_id=operation_id, **kwargs
        )
        provenance = response.provenance
        self.spending.usd += usd(
            price_for(provenance.provider, provenance.model, self.role)[0], response.usage
        )
        return response


def _today(model: BaseModel, schema: type[BaseModel]) -> BaseModel:
    """An old module's output class as today's schema class (the same fields)."""
    return schema.model_validate(model.model_dump())


class _Side:
    """One side's prompts, cells and calls."""

    def __init__(self, side: str, prompts: PromptSet, run: "_Run") -> None:
        self.side = side
        self.prompts = prompts
        self.run = run

    def cell(self, case: str, task: str, system: str, user: str) -> Cell:
        cell = Cell(case=case, side=self.side, task=task, system=system, user=user)
        self.run.cells.append(cell)
        return cell

    def call(
        self,
        cell: Cell,
        generator: TextGenerator | None,
        old_schema: type[BaseModel],
        schema: type[BaseModel],
        max_tokens: int,
    ) -> BaseModel | None:
        if generator is None:
            return None
        cell.called = True
        try:
            response = generator.generate_structured(
                cell.task,
                cell.system,
                [Message(role="user", content=cell.user)],
                old_schema,
                operation_id=f"prompt-ab-{self.run.stamp}:{cell.case}:{self.side}",
                max_output_tokens=max_tokens,
            )
            value = _today(response.value, schema)
        except Refused:
            raise
        except Exception as error:  # a failed or invalid output is that cell's error
            cell.error = f"{type(error).__name__}: {error}"
            return None
        cell.output = value.model_dump(mode="json")
        return value

    def schema(self, module: str, name: str, today: type[BaseModel]) -> type[BaseModel]:
        return getattr(getattr(self.prompts.modules, module), name, today)

    # The four kinds of call.

    def extract(self, case: str, passages: list[tuple[int, str]], generator) -> None:
        from app.features.topics.prompts import SectionTopics

        system, user = self.prompts.extract_section(passages)
        cell = self.cell(case, "topics.extract_section", system, user)
        old = self.schema("topics", "SectionTopics", SectionTopics)
        self.call(cell, generator, old, SectionTopics, TOPICS_TOKENS)

    def bank(
        self,
        title,
        description,
        objectives,
        passages,
        existing,
        slots,
        allowed: set[int],
        generator,
        policy: str = "materials",
    ) -> None:
        from app.features.questions.prompts import GeneratedBatch, Verification

        system, user = self.prompts.generate(
            policy, title, description, objectives, passages, existing, slots
        )
        cell = self.cell("generate", questions.GENERATE_KIND, system, user)
        old = self.schema("questions", "GeneratedBatch", GeneratedBatch)
        batch = self.call(cell, generator, old, GeneratedBatch, questions.MAX_OUTPUT_TOKENS)
        accepted = []
        if batch is not None:
            for question in batch.questions[: len(slots)]:
                result = validation.validate_question(question, allowed, [], policy=policy)
                if result.question is None:
                    cell.notes.append(f"rejected: {result.reason} ({question.text})")
                else:
                    accepted.append(result.question)
            cell.notes.append(f"{len(accepted)} of {len(batch.questions)} pass validation")
        if generator is None:  # a dry run shows the verification prompt of no questions
            accepted_pairs: list = []
        else:
            accepted_pairs = [(q.text, q.rubric) for q in accepted]
        system, user = self.prompts.verify(policy, passages, accepted_pairs)
        cell = self.cell("verify", "questions.verify", system, user)
        if generator is not None and not accepted:
            cell.notes.append("not called: no generated question passed validation")
            return
        old = self.schema("questions", "Verification", Verification)
        self.call(cell, generator, old, Verification, questions.MAX_OUTPUT_TOKENS)

    def assess(self, case: str, inp: AssessmentInput, generator) -> None:
        system, user = self.prompts.assess(inp)
        cell = self.cell(case, ASSESS_TASK, system, user)
        ai = self.call(cell, generator, AiAssessment, AiAssessment, ASSESS_TOKENS)
        if ai is None:
            return
        for error in validate_output(ai, inp):
            cell.notes.append(f"invalid: {error}")
        try:
            scores = compute_scores(ai, inp.question.rubric, DEFAULT_FACTUAL_WEIGHTS)
            band = classify_band(scores.factual, DEFAULT_BAND_BOUNDS)
            cell.notes.append(f"factual {scores.factual}, band {band}")
        except Exception as error:
            cell.notes.append(f"scores: {type(error).__name__}: {error}")

    def follow_up(self, policy, question, rubric, passages, transcript, points, mistakes, gen):
        from app.features.follow_ups.prompts import GeneratedFollowUp

        system, user = self.prompts.follow_up(
            policy, question, rubric, passages, transcript, points, mistakes
        )
        cell = self.cell("follow_up", FOLLOW_UP_TASK, system, user)
        old = self.schema("follow_ups", "GeneratedFollowUp", GeneratedFollowUp)
        self.call(cell, gen, old, GeneratedFollowUp, FOLLOW_UP_TOKENS)


@dataclass
class _Run:
    stamp: str
    cells: list[Cell] = field(default_factory=list)


def _polish_side(side: _Side, inputs: AbInputs, generation, assessment) -> None:
    if inputs.extraction is not None:
        side.extract("extract", inputs.extraction.passages, generation)
    if (bank := inputs.bank) is not None:
        side.bank(
            bank.title,
            bank.description,
            bank.objectives,
            bank.passages,
            bank.existing,
            bank.slots,
            bank.allowed,
            generation,
            bank.policy,
        )
    for case in inputs.assessments:
        side.assess(f"assess-{case.attempt_id}", case.inp, assessment)
    if (f := inputs.follow_up) is not None:
        side.follow_up(
            f.policy,
            f.question.text,
            f.question.rubric,
            f.passages,
            f.transcript,
            f.points,
            f.mistakes,
            generation,
        )


def _english_side(side: _Side, generation, assessment) -> None:
    passages = english.ENGLISH_PASSAGES
    side.extract("extract", passages, generation)
    side.bank(
        english.ENGLISH_TITLE,
        english.ENGLISH_DESCRIPTION,
        english.ENGLISH_OBJECTIVES,
        passages,
        [],
        ENGLISH_SLOTS,
        {pid for pid, _ in passages},
        generation,
    )
    inp = AssessmentInput(
        question=english.ENGLISH_QUESTION,
        transcript=english.ENGLISH_TRANSCRIPT,
        outcome="answered",
    )
    side.assess("assess", inp, assessment)
    side.follow_up(
        "materials",
        english.ENGLISH_QUESTION.text,
        english.ENGLISH_QUESTION.rubric,
        passages,
        english.ENGLISH_TRANSCRIPT,
        english.ENGLISH_POINTS,
        english.ENGLISH_MISTAKES,
        generation,
    )
    for cell in side.run.cells:
        if cell.side == "en" and cell.output is not None:
            cell.notes.append(english_findings(cell.output))


def _models(settings: Settings) -> dict[str, str]:
    return {role: "/".join(resolve_role(settings, role)) for role in ("generation", "assessment")}


def run(
    settings: Settings,
    options: RunOptions,
    *,
    show=git_show,
    generators: GeneratorFactory = live_generators,
    clock=datetime.now,
    allow_same_versions: bool = False,
) -> Path:
    """The page's path. Refused (nothing called) when a safety or budget check fails."""
    if not 1 <= options.calls <= MAX_CALLS:
        raise Refused(f"--calls must be between 1 and {MAX_CALLS}, not {options.calls}.")
    db = Path(options.db)
    check_database(db, settings)
    sha = resolve_ref(options.old_ref)
    old_modules: PromptModules = load_prompt_modules(sha, show=show)
    new_modules = current_prompt_modules()
    polish = PROFILES["pl"]
    old_set = prompt_set(old_modules, polish)
    new_set = ProfilePromptSet(new_modules, polish)
    if old_set.versions() == new_set.versions() and not allow_same_versions:
        raise Refused("The old ref has the same prompt versions: nothing to compare.")

    started = clock()
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(options.out_dir) if options.out_dir else settings.data_dir / "prompt-ab" / stamp
    copy = copy_database(db, out / "source-copy.db")
    engine = make_engine(f"sqlite:///{copy.as_posix()}")
    try:
        with Session(engine) as session:
            inputs = select_inputs(session, options.subject_id)
            plan = plan_calls(inputs)
            if len(plan) > options.calls:
                raise Refused(
                    f"The plan needs {len(plan)} calls, more than the {options.calls} allowed."
                )
            generation = assessment = None
            spending = Spending(limit=len(plan))
            if not options.dry_run:
                live_generation, live_assessment = generators(settings)
                generation = CountingGenerator(live_generation, spending, "generation")
                assessment = CountingGenerator(live_assessment, spending, "assessment")
            state = _Run(stamp)
            _polish_side(_Side("old", old_set, state), inputs, generation, assessment)
            _polish_side(_Side("new", new_set, state), inputs, generation, assessment)
            english_set = ProfilePromptSet(new_modules, PROFILES["en"])
            _english_side(_Side("en", english_set, state), generation, assessment)
    finally:
        engine.dispose()

    result = {
        "old_ref": options.old_ref,
        "old_sha": sha,
        "versions": {"old": old_set.versions(), "new": new_set.versions()},
        "models": _models(settings),
        "date": started.isoformat(timespec="seconds"),
        "dry_run": options.dry_run,
        "calls": {"made": len(spending.made), "planned": len(plan), "limit": options.calls},
        "operation_ids": spending.made,
        "usd": spending.usd,
        "subject": {"id": inputs.subject_id, "name": inputs.subject_name},
        "missing": inputs.missing,
        "plan": [vars(call) for call in plan],
        "inputs": _input_summary(inputs),
        "cells": [vars(cell) for cell in state.cells],
    }
    return write_page(out, result)


def _input_summary(inputs: AbInputs) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if inputs.extraction is not None:
        summary["extract"] = f"document version {inputs.extraction.version_id}, " + (
            f"{len(inputs.extraction.passages)} passages"
        )
    if inputs.bank is not None:
        summary["generate"] = f"topic: {inputs.bank.title}"
        summary["verify"] = f"topic: {inputs.bank.title}"
    for case in inputs.assessments:
        summary[f"assess-{case.attempt_id}"] = {
            "question": case.inp.question.text,
            "transcript": case.inp.transcript or "",
            "stored": case.stored.ai.model_dump(mode="json") if case.stored.ai else None,
            "stored_scores": case.stored.scores.model_dump() if case.stored.scores else None,
            "stored_band": case.stored.band,
        }
    if inputs.follow_up is not None:
        summary["follow_up"] = {
            "question": inputs.follow_up.question.text,
            "transcript": inputs.follow_up.transcript,
        }
    return summary
