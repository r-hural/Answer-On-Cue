"""One call shape for two generations of the prompt modules.

Every method returns (system, user), or the text of one block.
"""

from typing import Protocol

from app.features.assessment.schemas import AssessmentInput
from app.features.delivery.schemas import AnswerMetrics
from app.features.languages.profiles import LanguageProfile
from app.features.prompt_ab.loader import PromptModules
from app.features.questions.schemas import Rubric

Passages = list[tuple[int, str]]
TopicLines = list[tuple[str, str, str]]


class PromptSet(Protocol):
    modules: PromptModules

    def versions(self) -> dict[str, str]: ...

    def extract_section(self, passages: Passages) -> tuple[str, str]: ...

    def consolidate(self, section_topics: TopicLines) -> tuple[str, str]: ...

    def consolidate_chapter(
        self, section_topics: TopicLines, existing: TopicLines
    ) -> tuple[str, str]: ...

    def generate(
        self,
        policy: str,
        title: str,
        description: str | None,
        objectives: list[str],
        passages: Passages,
        existing: list[str],
        slots: list[tuple[str, str]],
    ) -> tuple[str, str]: ...

    def verify(
        self, policy: str, passages: Passages, questions: list[tuple[str, Rubric]]
    ) -> tuple[str, str]: ...

    def assess(self, inp: AssessmentInput) -> tuple[str, str]: ...

    def delivery(self, metrics: AnswerMetrics | None) -> str: ...

    def follow_up(
        self,
        policy: str,
        question: str,
        rubric: Rubric,
        passages: Passages,
        transcript: str,
        points: list[tuple[str, str, str]],
        mistakes: list[tuple[str, str]],
    ) -> tuple[str, str]: ...


def _versions(modules: PromptModules) -> dict[str, str]:
    return {
        "topics": modules.topics.TOPICS_PROMPT_VERSION,
        "questions": modules.questions.QUESTIONS_PROMPT_VERSION,
        "assessment": modules.assessment.ASSESSMENT_PROMPT_VERSION,
        "follow_ups": modules.follow_ups.FOLLOW_UP_PROMPT_VERSION,
    }


class LegacyPromptSet:
    """The API before content languages: module constants and builders without a profile."""

    def __init__(self, modules: PromptModules) -> None:
        self.modules = modules

    def versions(self) -> dict[str, str]:
        return _versions(self.modules)

    def extract_section(self, passages: Passages) -> tuple[str, str]:
        t = self.modules.topics
        return t.EXTRACT_SECTION_SYSTEM, t.extract_section_message(passages)

    def consolidate(self, section_topics: TopicLines) -> tuple[str, str]:
        t = self.modules.topics
        return t.CONSOLIDATE_SYSTEM, t.consolidate_message(section_topics)

    def consolidate_chapter(
        self, section_topics: TopicLines, existing: TopicLines
    ) -> tuple[str, str]:
        t = self.modules.topics
        return t.CONSOLIDATE_CHAPTER_SYSTEM, t.consolidate_chapter_message(section_topics, existing)

    def generate(self, policy, title, description, objectives, passages, existing, slots):
        q = self.modules.questions
        message = q.generate_message(
            title, description, objectives, passages, existing, slots, policy
        )
        return q.generate_system(policy), message

    def verify(self, policy, passages, questions):
        q = self.modules.questions
        return q.verify_system(policy), q.verify_message(passages, questions, policy)

    def assess(self, inp: AssessmentInput) -> tuple[str, str]:
        a = self.modules.assessment
        return a.assessment_system(inp.question.source_policy), a.user_message(inp)

    def delivery(self, metrics: AnswerMetrics | None) -> str:
        return self.modules.assessment.delivery_block(metrics)

    def follow_up(self, policy, question, rubric, passages, transcript, points, mistakes):
        f = self.modules.follow_ups
        message = f.user_message(question, rubric, passages, transcript, points, mistakes)
        return f.follow_up_system(policy), message


class ProfilePromptSet:
    """The API with content languages: every builder takes the subject's profile."""

    def __init__(self, modules: PromptModules, profile: LanguageProfile) -> None:
        self.modules = modules
        self.profile = profile

    def versions(self) -> dict[str, str]:
        return _versions(self.modules)

    def extract_section(self, passages: Passages) -> tuple[str, str]:
        t, p = self.modules.topics, self.profile
        return t.extract_section_system(profile=p), t.extract_section_message(passages, profile=p)

    def consolidate(self, section_topics: TopicLines) -> tuple[str, str]:
        t, p = self.modules.topics, self.profile
        return t.consolidate_system(profile=p), t.consolidate_message(section_topics, profile=p)

    def consolidate_chapter(
        self, section_topics: TopicLines, existing: TopicLines
    ) -> tuple[str, str]:
        t, p = self.modules.topics, self.profile
        message = t.consolidate_chapter_message(section_topics, existing, profile=p)
        return t.consolidate_chapter_system(profile=p), message

    def generate(self, policy, title, description, objectives, passages, existing, slots):
        q, p = self.modules.questions, self.profile
        message = q.generate_message(
            title, description, objectives, passages, existing, slots, policy, profile=p
        )
        return q.generate_system(policy, profile=p), message

    def verify(self, policy, passages, questions):
        q, p = self.modules.questions, self.profile
        message = q.verify_message(passages, questions, policy, profile=p)
        return q.verify_system(policy, profile=p), message

    def assess(self, inp: AssessmentInput) -> tuple[str, str]:
        a, p = self.modules.assessment, self.profile
        system = a.assessment_system(inp.question.source_policy, profile=p)
        return system, a.user_message(inp, profile=p)

    def delivery(self, metrics: AnswerMetrics | None) -> str:
        return self.modules.assessment.delivery_block(metrics, profile=self.profile)

    def follow_up(self, policy, question, rubric, passages, transcript, points, mistakes):
        f, p = self.modules.follow_ups, self.profile
        message = f.user_message(
            question, rubric, passages, transcript, points, mistakes, profile=p
        )
        return f.follow_up_system(policy, profile=p), message


def prompt_set(modules: PromptModules, profile: LanguageProfile) -> PromptSet:
    """The profile API when the modules have it, else the legacy one (which ignores profile)."""
    if hasattr(modules.topics, "extract_section_system"):
        return ProfilePromptSet(modules, profile)
    return LegacyPromptSet(modules)
