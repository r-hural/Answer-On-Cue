import pytest

from app.features.delivery.fillers import find_fillers
from app.features.delivery.metrics import compute_answer_metrics
from app.features.delivery.schemas import ClientEvents
from app.features.languages.profiles import PROFILES
from app.features.recording.schemas import VadResult


def test_polish_fillers_unchanged():
    text = "No więc yyy, w sumie to tak jakby Eee wiesz."
    assert find_fillers(text, PROFILES["pl"]) == [
        "No więc",
        "yyy",
        "w sumie",
        "tak jakby",
        "Eee",
        "wiesz",
    ]


def test_profile_bigrams_casefold_stable():
    for profile in PROFILES.values():
        for bigram in profile.filler_bigrams:
            for token in bigram:
                assert token == token.casefold(), (profile.code, token)


def test_german_fillers_detected_polish_not():
    text = "Ähm, das war halt, weißt du, wichtig. Yyy."
    assert find_fillers(text, PROFILES["de"]) == ["Ähm", "halt", "weißt du"]
    assert find_fillers(text, PROFILES["pl"]) == ["Yyy"]


def test_english_fillers():
    text = "Um, I mean, the treaty was, uh, signed in 1648, you know."
    assert find_fillers(text, PROFILES["en"]) == ["Um", "I mean", "uh", "you know"]
    assert find_fillers("I would like to sort the files", PROFILES["en"]) == []


@pytest.mark.parametrize(
    ("code", "text", "expected", "plain"),
    [
        (
            "fr",
            "Euh, en fait, c'est bref.",
            ["Euh", "en fait", "bref"],
            "La révolution commence en 1789.",
        ),
        ("es", "Eh, o sea, pues no lo sé.", ["Eh", "o sea", "pues"], "La batalla fue en 1808."),
        (
            "it",
            "Ehm, cioè, nel senso che è vero.",
            ["Ehm", "cioè", "nel senso"],
            "Il regno nasce nel 1861.",
        ),
        (
            "uk",
            "Ну, ееее, це було, як би, важливо.",
            ["Ну", "ееее", "як би"],
            "Незалежність проголошено 1991 року.",
        ),
    ],
)
def test_french_spanish_italian_ukrainian_fillers(code, text, expected, plain):
    assert find_fillers(text, PROFILES[code]) == expected
    assert find_fillers(plain, PROFILES[code]) == []


def _events() -> ClientEvents:
    return ClientEvents(
        question_ready_at=0,
        question_shown_at=0,
        prep_end_at=0,
        start_reason="button",
        capture_start_at=0,
        stopped_at=8000,
        pre_roll_ms=0,
    )


def _vad() -> VadResult:
    return VadResult(
        engine="test-vad",
        speech_intervals=[(1000, 7000)],
        speech_ms=6000,
        leading_silence_ms=1000,
        trailing_silence_ms=1000,
        duration_ms=8000,
    )


def test_metrics_use_the_profile():
    metrics = compute_answer_metrics(_events(), _vad(), "Ähm das yyy halt", profile=PROFILES["de"])
    assert metrics.filler_count == 2
    assert metrics.filler_examples == ["Ähm", "halt"]
    with pytest.raises(TypeError):
        compute_answer_metrics(_events(), _vad(), "Ähm das yyy halt")  # type: ignore[call-arg]
