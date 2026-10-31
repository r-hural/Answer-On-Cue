from app.features.voice import verbatim
from app.features.voice.verbatim import compare

INDUSTRY = "Omów przyczyny i skutki rewolucji przemysłowej w Europie w XVIII i XIX wieku."
GDP = "Wyjaśnij różnicę między PKB a PNB i podaj przykład z ostatnich lat w gospodarce polskiej."


def test_exact():
    heard = "omów przyczyny i skutki rewolucji przemysłowej w europie w XVIII i XIX wieku"
    found = compare(INDUSTRY, heard)
    assert found.ok
    assert found.matched == 1.0


def test_numbers_as_words():
    assert compare(
        "Opisz przebieg bitwy pod Grunwaldem 15 lipca 1410 r. i jej skutki dla Polski.",
        "Opisz przebieg bitwy pod Grunwaldem piętnastego lipca tysiąc czterysta dziesiątego "
        "roku i jej skutki dla Polski.",
    ).ok


def test_roman_and_acronyms_as_words():
    assert compare(
        "Porównaj PKB Polski w XX wieku z PKB Czech.",
        "Porównaj pe ka be Polski w dwudziestym wieku z pe ka be Czech",
    ).ok


def test_one_misheard_word():
    found = compare(GDP, GDP.replace("przykład", "przykłady"))
    assert found.ok
    assert found.largest_gap == 1


def test_final_acronym_and_numeral_with_a_period():
    assert compare("Wyjaśnij, czym jest PKB.", "Wyjaśnij, czym jest pe ka be.").ok
    assert compare("Opisz Polskę w wieku XVIII.", "Opisz Polskę w wieku osiemnastym.").ok
    assert compare("Czym są PKB, PNB; i VAT:", "Czym są pe ka be pe en be i wat").ok


def test_short_question_one_misheard_word_fails():
    # Nine tokens: one misheard word leaves 8/9 < MATCH_THRESHOLD (plan default, owner check).
    short = "Omów skutki rewolucji przemysłowej w Europie i w Azji."
    found = compare(short, short.replace("skutki", "skutek"))
    assert len(verbatim.tokens(short)) == 9
    assert not found.ok
    assert found.matched == 8 / 9
    assert found.largest_gap == 1


def test_inserted_sentence_fails():
    assert not compare(INDUSTRY, INDUSTRY + " To jest bardzo ciekawe pytanie.").ok


def test_dropped_clause_fails():
    assert not compare(
        INDUSTRY, "Omów przyczyny rewolucji przemysłowej w Europie w XVIII i XIX wieku."
    ).ok
    longer = (
        "Omów przyczyny rewolucji przemysłowej w Europie, "
        "a następnie oceń ich znaczenie dla współczesnej gospodarki."
    )
    assert not compare(longer, "Omów przyczyny rewolucji przemysłowej w Europie.").ok


def test_answer_instead_of_reading_fails():
    answer = (
        "Rewolucja przemysłowa zaczęła się w Anglii dzięki węglowi, maszynie parowej, "
        "kapitałowi z handlu i taniej sile roboczej ze wsi angielskiej."
    )
    assert len(answer.split()) == 20
    assert not compare(INDUSTRY, answer).ok


def test_diacritics_and_case_ignored():
    assert compare("Źródła", "zrodla").ok


def test_empty_expected():
    assert compare("?!", "").ok
    assert not compare("?!", "dzień dobry").ok


def test_thresholds_pinned():
    assert verbatim.MATCH_THRESHOLD == 0.9
    assert verbatim.MAX_GAP_TOKENS == 2
    assert verbatim.MAX_EXPANSION == 6
