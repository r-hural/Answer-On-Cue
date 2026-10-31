import ast
import re
from pathlib import Path

import pytest

from app.features.assessment import prompts as assessment_prompts
from app.features.languages import profiles
from app.features.languages.profiles import PROFILES, SUPPORTED, get_profile
from app.features.questions import prompts as question_prompts
from app.features.voice import service as voice_service
from app.features.voice import trial as voice_trial

NBSP = " "

BCP47 = {
    "pl": "pl-PL",
    "en": "en-GB",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
    "it": "it-IT",
    "uk": "uk-UA",
}

EXPECTED = {
    "pl": {
        "name_en": "Polish",
        "name_native": "polski",
        "piper_prefix": "pl_PL",
        "default_piper_voice": "pl_PL-bass-high",
        "page_label": "s.",
        "paragraph_label": "akapit",
        "document_label": "Dokument",
        "intro_label": "Wstęp",
        "glossary_prefix": "Słownik terminów: ",
        "chars_per_token": 3.0,
        "quotes": ("„", "”"),
        "register_label": "formal spoken exam Polish",
        "unavailable_label": "niedostępne",
        "sources_policy_label": "Tylko moje materiały",
    },
    "en": {
        "name_en": "English",
        "name_native": "English",
        "piper_prefix": "en_GB",
        "default_piper_voice": "en_GB-cori-high",
        "page_label": "p.",
        "paragraph_label": "paragraph",
        "document_label": "Document",
        "intro_label": "Introduction",
        "glossary_prefix": "Glossary: ",
        "chars_per_token": 4.0,
        "quotes": ("“", "”"),
        "register_label": "formal spoken exam English",
        "unavailable_label": "unavailable",
        "sources_policy_label": "Only my materials",
    },
    "de": {
        "name_en": "German",
        "name_native": "Deutsch",
        "piper_prefix": "de_DE",
        "default_piper_voice": "de_DE-thorsten-high",
        "page_label": "S.",
        "paragraph_label": "Absatz",
        "document_label": "Dokument",
        "intro_label": "Einleitung",
        "glossary_prefix": "Fachbegriffe: ",
        "chars_per_token": 3.5,
        "quotes": ("„", "“"),
        "register_label": "formal spoken exam German",
        "unavailable_label": "nicht verfügbar",
        "sources_policy_label": "Nur meine Materialien",
    },
    "fr": {
        "name_en": "French",
        "name_native": "français",
        "piper_prefix": "fr_FR",
        "default_piper_voice": "fr_FR-siwis-medium",
        "page_label": "p.",
        "paragraph_label": "paragraphe",
        "document_label": "Document",
        "intro_label": "Introduction",
        "glossary_prefix": "Glossaire : ",
        "chars_per_token": 3.5,
        "quotes": ("«" + NBSP, NBSP + "»"),
        "register_label": "formal spoken exam French",
        "unavailable_label": "non disponible",
        "sources_policy_label": "Uniquement mes documents",
    },
    "es": {
        "name_en": "Spanish",
        "name_native": "español",
        "piper_prefix": "es_ES",
        "default_piper_voice": "es_ES-davefx-medium",
        "page_label": "pág.",
        "paragraph_label": "párrafo",
        "document_label": "Documento",
        "intro_label": "Introducción",
        "glossary_prefix": "Glosario: ",
        "chars_per_token": 3.5,
        "quotes": ("«", "»"),
        "register_label": "formal spoken exam Spanish",
        "unavailable_label": "no disponible",
        "sources_policy_label": "Solo mis materiales",
    },
    "it": {
        "name_en": "Italian",
        "name_native": "italiano",
        "piper_prefix": "it_IT",
        "default_piper_voice": "it_IT-serena-high",
        "page_label": "p.",
        "paragraph_label": "paragrafo",
        "document_label": "Documento",
        "intro_label": "Introduzione",
        "glossary_prefix": "Glossario: ",
        "chars_per_token": 3.5,
        "quotes": ("«", "»"),
        "register_label": "formal spoken exam Italian",
        "unavailable_label": "non disponibile",
        "sources_policy_label": "Solo i miei materiali",
    },
    "uk": {
        "name_en": "Ukrainian",
        "name_native": "українська",
        "piper_prefix": "uk_UA",
        "default_piper_voice": "uk_UA-tetiana-high",
        "page_label": "с.",
        "paragraph_label": "абзац",
        "document_label": "Документ",
        "intro_label": "Вступ",
        "glossary_prefix": "Словник термінів: ",
        "chars_per_token": 2.5,
        "quotes": ("«", "»"),
        "register_label": "formal spoken exam Ukrainian",
        "unavailable_label": "недоступно",
        "sources_policy_label": "Лише мої матеріали",
    },
}

FILLERS = {
    "pl": (
        r"^(?:yyy+|eee+|mmm+|hmm+|yhm|ee|no|jakby|wiesz|znaczy|generalnie)$",
        {("w", "sumie"), ("tak", "jakby"), ("no", "więc")},
    ),
    "en": (
        r"^(?:u+m+|u+h+|e+r+m*|h+m+|a+h+|basically|actually|literally)$",
        {("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of")},
    ),
    "de": (
        r"^(?:ä+h+m*|ö+h+m*|e+h+m+|h+m+|halt|quasi|sozusagen|irgendwie|naja)$",
        {("na", "ja"), ("ich", "meine"), ("weisst", "du")},
    ),
    "fr": (
        r"^(?:eu+h+|heu+|bah|ben|hum+|h+m+|voilà|bref)$",
        {("en", "fait"), ("du", "coup"), ("tu", "vois")},
    ),
    "es": (
        r"^(?:e+h+m*|e{2,}|e+m+|m{2,}|pues|bueno|vale)$",
        {("o", "sea"), ("en", "plan"), ("a", "ver")},
    ),
    "it": (
        r"^(?:e+h+m*|e{2,}|e+m+|m{2,}|cioè|tipo|allora|praticamente|insomma|ecco)$",
        {("nel", "senso"), ("come", "dire"), ("diciamo", "così")},
    ),
    "uk": (
        "^(?:е{2,}|е+м+|м{2,}|ну|типу|короче|значить|власне)$",
        {("як", "би"), ("так", "сказати"), ("ну", "от")},
    ),
}

SENTENCES = {
    "en": (
        "Discuss the causes and consequences of the Industrial Revolution in Europe in the 18th"
        " and 19th centuries.",
        (
            "What was the significance of the Battle of Hastings on 14 October 1066?",
            "Explain the difference between GDP and GNP and give an example.",
            "How much is 3/4 of £2,400, and how did you work it out?",
            "Why is 1 September 1939 regarded as the start of the Second World War, e.g. in"
            " British textbooks?",
        ),
    ),
    "de": (
        "Erläutern Sie die Ursachen und Folgen der industriellen Revolution in Europa im 18. und"
        " 19. Jahrhundert.",
        (
            "Welche Bedeutung hatte der Westfälische Friede vom 24. Oktober 1648?",
            "Erklären Sie den Unterschied zwischen BIP und BNE und nennen Sie ein Beispiel.",
            "Wie viel sind 3/4 von 2.400 € und wie haben Sie das berechnet?",
            "Was regelt § 823 Abs. 1 BGB?",
        ),
    ),
    "fr": (
        "Analysez les causes et les conséquences de la révolution industrielle en Europe aux"
        " XVIIIe et XIXe siècles.",
        (
            "Expliquez l'importance de la prise de la Bastille le 14 juillet 1789.",
            "Expliquez la différence entre le PIB et le RNB et donnez un exemple.",
            "Calculez les 3/4 de 2 400 € et expliquez votre méthode.",
            "Présentez ce que prévoit l'art. 1240 du Code civil.",
        ),
    ),
    "es": (
        "Explique las causas y consecuencias de la Revolución Industrial en Europa en los siglos"
        " XVIII y XIX.",
        (
            "¿Qué importancia tuvo la batalla de Bailén del 19 de julio de 1808?",
            "Explique la diferencia entre el PIB y el PNB y ponga un ejemplo.",
            "¿Cuánto son 3/4 de 2.400 € y cómo lo ha calculado?",
            "¿Qué regula el art. 1902 del Código Civil?",
        ),
    ),
    "it": (
        "Illustri le cause e le conseguenze della rivoluzione industriale in Europa nel XVIII e"
        " XIX secolo.",
        (
            "Quale importanza ebbe la proclamazione del Regno d'Italia del 17 marzo 1861?",
            "Spieghi la differenza tra PIL e PNL e faccia un esempio.",
            "Quanto fanno i 3/4 di 2.400 € e come li ha calcolati?",
            "Che cosa disciplina l'art. 2043 del Codice civile?",
        ),
    ),
    "uk": (
        "Розкрийте причини та наслідки промислової революції в Європі у XVIII–XIX століттях.",
        (
            "Яке значення мав Акт проголошення незалежності України від 24 серпня 1991 р.?",
            "Поясніть різницю між ВВП і ВНП та наведіть приклад.",
            "Скільки становить 3/4 від 2 400 грн і як ви це обчислили?",
            "Що регулює ст. 1166 Цивільного кодексу України?",
        ),
    ),
}

ABBREVIATIONS = ("GDP", "BIP", "PIB", "PIL", "ВВП", "art.", "Abs.", "ст.", "e.g.")


def test_supported_order_and_keys():
    assert SUPPORTED == ("pl", "en", "de", "fr", "es", "it", "uk")
    assert list(PROFILES) == list(SUPPORTED)
    for code, profile in PROFILES.items():
        assert profile.code == code
        assert profile.bcp47 == BCP47[code]
        assert profile.azure_locale == profile.bcp47
        assert profile.piper_prefix == BCP47[code].replace("-", "_")
    assert PROFILES["en"].bcp47 == "en-GB"
    assert PROFILES["en"].piper_prefix == "en_GB"


def test_get_profile_unknown():
    with pytest.raises(ValueError):
        get_profile("xx")
    with pytest.raises(ValueError):
        get_profile("PL")
    assert get_profile("de").name_native == "Deutsch"


def test_polish_profile_is_copied_byte_for_byte():
    pl = PROFILES["pl"]
    assert pl.preview_sentence == voice_service.SAMPLE_TEXT
    assert pl.trial_sentences == voice_trial.SAMPLE_QUESTIONS
    assert pl.unavailable_label == assessment_prompts.UNAVAILABLE
    assert pl.sources_policy_label == question_prompts.POLICY_NAMES["materials"]
    assert pl.filler_unigrams.pattern == (
        r"^(?:yyy+|eee+|mmm+|hmm+|yhm|ee|no|jakby|wiesz|znaczy|generalnie)$"
    )
    assert pl.filler_bigrams == frozenset({("w", "sumie"), ("tak", "jakby"), ("no", "więc")})
    assert pl.glossary_prefix == "Słownik terminów: "
    assert pl.page_label == "s."
    assert pl.paragraph_label == "akapit"
    assert pl.document_label == "Dokument"
    assert pl.quotes == ("„", "”")
    assert pl.chars_per_token == 3.0
    assert pl.register_label == "formal spoken exam Polish"
    assert pl.default_piper_voice == "pl_PL-bass-high"


@pytest.mark.parametrize("code", SUPPORTED)
def test_profile_values_match_the_plan(code):
    profile = PROFILES[code]
    for field, value in EXPECTED[code].items():
        assert getattr(profile, field) == value, field
    assert type(profile.chars_per_token) is float
    pattern, bigrams = FILLERS[code]
    assert profile.filler_unigrams.pattern == pattern
    assert isinstance(profile.filler_bigrams, frozenset)
    assert profile.filler_bigrams == frozenset(bigrams)
    if code != "pl":
        preview, trial = SENTENCES[code]
        assert profile.preview_sentence == preview
        assert profile.trial_sentences == trial


def test_default_piper_voices_are_medium_or_high():
    for profile in PROFILES.values():
        assert profile.default_piper_voice.startswith(profile.piper_prefix + "-")
        assert profile.default_piper_voice.endswith(("-medium", "-high"))


def test_trial_sentences_cover_numbers_dates_abbreviations():
    for code, profile in PROFILES.items():
        if code == "pl":
            continue
        sentences = profile.trial_sentences
        assert len(sentences) == 4
        assert any(re.search(r"\b\d{4}\b", s) for s in sentences), code
        assert any("3/4" in s for s in sentences), code
        assert any(a in s for s in sentences for a in ABBREVIATIONS), code
        assert profile.preview_sentence
        assert profile.preview_sentence != voice_service.SAMPLE_TEXT


def test_profiles_module_is_pure():
    tree = ast.parse(Path(profiles.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert imported
    assert not [name for name in imported if name == "app" or name.startswith("app.")]
    assert set(imported) <= {"re", "dataclasses", "typing"}
