"""One immutable profile per supported content language. Pure data: no app imports."""

import re
from dataclasses import dataclass
from typing import Literal

LanguageCode = Literal["pl", "en", "de", "fr", "es", "it", "uk"]
SUPPORTED: tuple[LanguageCode, ...] = ("pl", "en", "de", "fr", "es", "it", "uk")

NBSP = " "


@dataclass(frozen=True)
class LanguageProfile:
    code: LanguageCode
    bcp47: str
    name_en: str
    name_native: str
    piper_prefix: str
    default_piper_voice: str
    azure_locale: str
    # Matched against a casefolded token; bigrams hold casefolded tokens.
    filler_unigrams: re.Pattern[str]
    filler_bigrams: frozenset[tuple[str, str]]
    page_label: str
    document_label: str
    intro_label: str
    glossary_prefix: str
    chars_per_token: float
    quotes: tuple[str, str]
    preview_sentence: str
    trial_sentences: tuple[str, ...]
    register_label: str
    unavailable_label: str
    sources_policy_label: str
    paragraph_label: str


PROFILES: dict[LanguageCode, LanguageProfile] = {
    "pl": LanguageProfile(
        code="pl",
        bcp47="pl-PL",
        name_en="Polish",
        name_native="polski",
        piper_prefix="pl_PL",
        default_piper_voice="pl_PL-bass-high",
        azure_locale="pl-PL",
        filler_unigrams=re.compile(
            r"^(?:yyy+|eee+|mmm+|hmm+|yhm|ee|no|jakby|wiesz|znaczy|generalnie)$"
        ),
        filler_bigrams=frozenset({("w", "sumie"), ("tak", "jakby"), ("no", "więc")}),
        page_label="s.",
        document_label="Dokument",
        intro_label="Wstęp",
        glossary_prefix="Słownik terminów: ",
        chars_per_token=3.0,
        quotes=("„", "”"),
        preview_sentence=(
            "Omów przyczyny i skutki rewolucji przemysłowej w Europie w XVIII i XIX wieku."
        ),
        trial_sentences=(
            "Omów przyczyny i skutki rewolucji przemysłowej w Europie w XVIII i XIX wieku.",
            "Jakie znaczenie miała bitwa pod Grunwaldem 15 lipca 1410 roku?",
            "Wyjaśnij różnicę między PKB a PNB i podaj przykład.",
            "Co reguluje art. 415 Kodeksu cywilnego?",
            "Opisz cykl Krebsa i wskaż, gdzie zachodzi w komórce.",
            "Ile wynosi 3/4 z 2 400 zł i jak to obliczyłeś?",
            "Czym jest fotosynteza? Podaj jej ogólne równanie.",
            "Porównaj ustrój Rzeczypospolitej Obojga Narodów z monarchią absolutną we Francji.",
            "Wymień trzy zasady dynamiki Newtona i omów jedną z nich na przykładzie.",
            "Co oznacza skrót RODO i jakie prawa daje osobie, której dane dotyczą?",
            "Jaką rolę pełni enzym DNA-polimeraza podczas replikacji?",
            "Dlaczego 1 września 1939 r. uznaje się za początek II wojny światowej?",
        ),
        register_label="formal spoken exam Polish",
        unavailable_label="niedostępne",
        sources_policy_label="Tylko moje materiały",
        paragraph_label="akapit",
    ),
    "en": LanguageProfile(
        code="en",
        bcp47="en-GB",
        name_en="English",
        name_native="English",
        piper_prefix="en_GB",
        default_piper_voice="en_GB-cori-high",
        azure_locale="en-GB",
        filler_unigrams=re.compile(
            r"^(?:u+m+|u+h+|e+r+m*|h+m+|a+h+|basically|actually|literally)$"
        ),
        filler_bigrams=frozenset({("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of")}),
        page_label="p.",
        document_label="Document",
        intro_label="Introduction",
        glossary_prefix="Glossary: ",
        chars_per_token=4.0,
        quotes=("“", "”"),
        preview_sentence=(
            "Discuss the causes and consequences of the Industrial Revolution in Europe"
            " in the 18th and 19th centuries."
        ),
        trial_sentences=(
            "What was the significance of the Battle of Hastings on 14 October 1066?",
            "Explain the difference between GDP and GNP and give an example.",
            "How much is 3/4 of £2,400, and how did you work it out?",
            "Why is 1 September 1939 regarded as the start of the Second World War,"
            " e.g. in British textbooks?",
        ),
        register_label="formal spoken exam English",
        unavailable_label="unavailable",
        sources_policy_label="Only my materials",
        paragraph_label="paragraph",
    ),
    "de": LanguageProfile(
        code="de",
        bcp47="de-DE",
        name_en="German",
        name_native="Deutsch",
        piper_prefix="de_DE",
        default_piper_voice="de_DE-thorsten-high",
        azure_locale="de-DE",
        filler_unigrams=re.compile(
            r"^(?:ä+h+m*|ö+h+m*|e+h+m+|h+m+|halt|quasi|sozusagen|irgendwie|naja)$"
        ),
        # Casefolded: "weißt".casefold() == "weisst".
        filler_bigrams=frozenset({("na", "ja"), ("ich", "meine"), ("weisst", "du")}),
        page_label="S.",
        document_label="Dokument",
        intro_label="Einleitung",
        glossary_prefix="Fachbegriffe: ",
        chars_per_token=3.5,
        quotes=("„", "“"),
        preview_sentence=(
            "Erläutern Sie die Ursachen und Folgen der industriellen Revolution in Europa"
            " im 18. und 19. Jahrhundert."
        ),
        trial_sentences=(
            "Welche Bedeutung hatte der Westfälische Friede vom 24. Oktober 1648?",
            "Erklären Sie den Unterschied zwischen BIP und BNE und nennen Sie ein Beispiel.",
            "Wie viel sind 3/4 von 2.400 € und wie haben Sie das berechnet?",
            "Was regelt § 823 Abs. 1 BGB?",
        ),
        register_label="formal spoken exam German",
        unavailable_label="nicht verfügbar",
        sources_policy_label="Nur meine Materialien",
        paragraph_label="Absatz",
    ),
    "fr": LanguageProfile(
        code="fr",
        bcp47="fr-FR",
        name_en="French",
        name_native="français",
        piper_prefix="fr_FR",
        default_piper_voice="fr_FR-siwis-medium",
        azure_locale="fr-FR",
        filler_unigrams=re.compile(r"^(?:eu+h+|heu+|bah|ben|hum+|h+m+|voilà|bref)$"),
        filler_bigrams=frozenset({("en", "fait"), ("du", "coup"), ("tu", "vois")}),
        page_label="p.",
        document_label="Document",
        intro_label="Introduction",
        glossary_prefix="Glossaire : ",
        chars_per_token=3.5,
        quotes=("«" + NBSP, NBSP + "»"),
        preview_sentence=(
            "Analysez les causes et les conséquences de la révolution industrielle en Europe"
            " aux XVIIIe et XIXe siècles."
        ),
        trial_sentences=(
            "Expliquez l'importance de la prise de la Bastille le 14 juillet 1789.",
            "Expliquez la différence entre le PIB et le RNB et donnez un exemple.",
            "Calculez les 3/4 de 2 400 € et expliquez votre méthode.",
            "Présentez ce que prévoit l'art. 1240 du Code civil.",
        ),
        register_label="formal spoken exam French",
        unavailable_label="non disponible",
        sources_policy_label="Uniquement mes documents",
        paragraph_label="paragraphe",
    ),
    "es": LanguageProfile(
        code="es",
        bcp47="es-ES",
        name_en="Spanish",
        name_native="español",
        piper_prefix="es_ES",
        default_piper_voice="es_ES-davefx-medium",
        azure_locale="es-ES",
        filler_unigrams=re.compile(r"^(?:e+h+m*|e{2,}|e+m+|m{2,}|pues|bueno|vale)$"),
        filler_bigrams=frozenset({("o", "sea"), ("en", "plan"), ("a", "ver")}),
        page_label="pág.",
        document_label="Documento",
        intro_label="Introducción",
        glossary_prefix="Glosario: ",
        chars_per_token=3.5,
        quotes=("«", "»"),
        preview_sentence=(
            "Explique las causas y consecuencias de la Revolución Industrial en Europa"
            " en los siglos XVIII y XIX."
        ),
        trial_sentences=(
            "¿Qué importancia tuvo la batalla de Bailén del 19 de julio de 1808?",
            "Explique la diferencia entre el PIB y el PNB y ponga un ejemplo.",
            "¿Cuánto son 3/4 de 2.400 € y cómo lo ha calculado?",
            "¿Qué regula el art. 1902 del Código Civil?",
        ),
        register_label="formal spoken exam Spanish",
        unavailable_label="no disponible",
        sources_policy_label="Solo mis materiales",
        paragraph_label="párrafo",
    ),
    "it": LanguageProfile(
        code="it",
        bcp47="it-IT",
        name_en="Italian",
        name_native="italiano",
        piper_prefix="it_IT",
        default_piper_voice="it_IT-serena-high",
        azure_locale="it-IT",
        filler_unigrams=re.compile(
            r"^(?:e+h+m*|e{2,}|e+m+|m{2,}|cioè|tipo|allora|praticamente|insomma|ecco)$"
        ),
        filler_bigrams=frozenset({("nel", "senso"), ("come", "dire"), ("diciamo", "così")}),
        page_label="p.",
        document_label="Documento",
        intro_label="Introduzione",
        glossary_prefix="Glossario: ",
        chars_per_token=3.5,
        quotes=("«", "»"),
        preview_sentence=(
            "Illustri le cause e le conseguenze della rivoluzione industriale in Europa"
            " nel XVIII e XIX secolo."
        ),
        trial_sentences=(
            "Quale importanza ebbe la proclamazione del Regno d'Italia del 17 marzo 1861?",
            "Spieghi la differenza tra PIL e PNL e faccia un esempio.",
            "Quanto fanno i 3/4 di 2.400 € e come li ha calcolati?",
            "Che cosa disciplina l'art. 2043 del Codice civile?",
        ),
        register_label="formal spoken exam Italian",
        unavailable_label="non disponibile",
        sources_policy_label="Solo i miei materiali",
        paragraph_label="paragrafo",
    ),
    "uk": LanguageProfile(
        code="uk",
        bcp47="uk-UA",
        name_en="Ukrainian",
        name_native="українська",
        piper_prefix="uk_UA",
        default_piper_voice="uk_UA-tetiana-high",
        azure_locale="uk-UA",
        # Cyrillic е (U+0435) and м (U+043C).
        filler_unigrams=re.compile(r"^(?:е{2,}|е+м+|м{2,}|ну|типу|короче|значить|власне)$"),
        filler_bigrams=frozenset({("як", "би"), ("так", "сказати"), ("ну", "от")}),
        page_label="с.",
        document_label="Документ",
        intro_label="Вступ",
        glossary_prefix="Словник термінів: ",
        chars_per_token=2.5,
        quotes=("«", "»"),
        preview_sentence=(
            "Розкрийте причини та наслідки промислової революції в Європі у XVIII–XIX століттях."
        ),
        trial_sentences=(
            "Яке значення мав Акт проголошення незалежності України від 24 серпня 1991 р.?",
            "Поясніть різницю між ВВП і ВНП та наведіть приклад.",
            "Скільки становить 3/4 від 2 400 грн і як ви це обчислили?",
            "Що регулює ст. 1166 Цивільного кодексу України?",
        ),
        register_label="formal spoken exam Ukrainian",
        unavailable_label="недоступно",
        sources_policy_label="Лише мої матеріали",
        paragraph_label="абзац",
    ),
}


def get_profile(code: str) -> LanguageProfile:
    try:
        return PROFILES[code]  # type: ignore[index]
    except KeyError:
        raise ValueError(f"Unknown language: {code!r}") from None
