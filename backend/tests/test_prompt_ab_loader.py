import subprocess
import sys

import pytest
from test_prompts_support import read_snapshot, render_cases, snapshot_names

from app.config import REPO_ROOT
from app.features.languages.profiles import PROFILES
from app.features.prompt_ab.loader import (
    PRIVATE_PREFIX,
    PROMPT_PATHS,
    LoaderError,
    current_prompt_modules,
    load_prompt_modules,
    resolve_ref,
)
from app.features.prompt_ab.prompt_sets import LegacyPromptSet, ProfilePromptSet, prompt_set
from app.features.topics import prompts as topic_prompts

BASE = "ed1f04a"


def _has_commit(ref: str) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{ref}^{{commit}}"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return done.returncode == 0


needs_base = pytest.mark.skipif(not _has_commit(BASE), reason="needs the git history")
needs_git = pytest.mark.skipif(not _has_commit("HEAD"), reason="needs a git checkout")


def _private_modules() -> list[str]:
    return [name for name in sys.modules if name.startswith(PRIVATE_PREFIX)]


@needs_base
def test_loads_old_modules_from_git():
    modules = load_prompt_modules(BASE)
    rendered = render_cases(LegacyPromptSet(modules))
    assert sorted(rendered) == snapshot_names()
    for name, text in rendered.items():
        assert text == read_snapshot(name), name
    assert modules.questions.UNTRUSTED_RULE is modules.topics.UNTRUSTED_RULE
    assert modules.topics is not topic_prompts
    assert modules.questions.UNTRUSTED_RULE is not topic_prompts.UNTRUSTED_RULE
    assert _private_modules() == []


FAKE_SOURCES = {
    "topics": 'UNTRUSTED_RULE = "OLD RULE"\nTOPICS_PROMPT_VERSION = "topics-old"\n',
    "questions": (
        "from app.features.topics.prompts import UNTRUSTED_RULE\n"
        "from app.features.questions.schemas import Rubric\n"
        'QUESTIONS_PROMPT_VERSION = "questions-old"\n'
    ),
    "assessment": (
        "from app.features.topics import prompts as topic_prompts\n"
        'ASSESSMENT_PROMPT_VERSION = "assessment-old"\n'
        "RULE = topic_prompts.UNTRUSTED_RULE\n"
    ),
    "follow_ups": (
        "from app.features.assessment.prompts import RULE\n"
        "from app.features.topics.prompts import UNTRUSTED_RULE\n"
        "import app.features.questions.prompts as question_prompts\n"
        'FOLLOW_UP_PROMPT_VERSION = "follow-up-old"\n'
        "VERSION = question_prompts.QUESTIONS_PROMPT_VERSION\n"
    ),
}


def _fake_show(sources=FAKE_SOURCES):
    asked = []

    def show(ref: str, path: str) -> str:
        assert ref == "abc"
        key = next(k for k, p in PROMPT_PATHS.items() if p == path)
        asked.append(key)
        return sources[key]

    return show, asked


def test_import_rewiring_with_a_fake_show():
    show, asked = _fake_show()
    modules = load_prompt_modules("abc", show=show)
    assert asked == ["topics", "questions", "assessment", "follow_ups"]
    assert modules.questions.UNTRUSTED_RULE == "OLD RULE"
    assert modules.assessment.RULE == "OLD RULE"
    assert modules.follow_ups.RULE == "OLD RULE"
    assert modules.follow_ups.VERSION == "questions-old"
    assert topic_prompts.UNTRUSTED_RULE != "OLD RULE"
    assert modules.questions.Rubric.__module__ == "app.features.questions.schemas"
    assert _private_modules() == []

    broken = {**FAKE_SOURCES, "assessment": "import app.features.no_such_module\n"}
    show, _ = _fake_show(broken)
    with pytest.raises(LoaderError, match="assessment"):
        load_prompt_modules("abc", show=show)
    assert _private_modules() == []

    def failing(ref: str, path: str) -> str:
        raise RuntimeError("no such ref")

    with pytest.raises(LoaderError, match="topics"):
        load_prompt_modules("abc", show=failing)
    assert _private_modules() == []


@needs_git
def test_resolve_ref():
    with pytest.raises(LoaderError):
        resolve_ref("no-such-ref-xyz")
    sha = resolve_ref("HEAD")
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_prompt_set_detection():
    pl = PROFILES["pl"]
    assert isinstance(prompt_set(current_prompt_modules(), pl), ProfilePromptSet)
    if _has_commit(BASE):
        assert isinstance(prompt_set(load_prompt_modules(BASE), pl), LegacyPromptSet)
