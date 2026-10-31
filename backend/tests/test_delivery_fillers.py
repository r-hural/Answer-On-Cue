from app.features.delivery.fillers import count_words, find_fillers
from app.features.languages.profiles import PROFILES


def test_fillers_tokens_and_bigrams():
    text = "No to jest, yyy, nowy temat, w sumie tak jakby ważny. Eee… wiesz?"
    # no, yyy, "w sumie", "tak jakby", eee, wiesz = 6; "nowy" is not "no"
    assert find_fillers(text, PROFILES["pl"]) == [
        "No",
        "yyy",
        "w sumie",
        "tak jakby",
        "Eee",
        "wiesz",
    ]


def test_bigram_does_not_double_count_unigram():
    # "no więc" is one filler; the "no" inside it is not counted again
    assert find_fillers("no więc tak", PROFILES["pl"]) == ["no więc"]


def test_no_inside_word_not_matched():
    assert find_fillers("nowy nogi generalnym", PROFILES["pl"]) == []


def test_count_words_strips_punctuation():
    # "..." and "-" alone are not words: Ala, ma, kota = 3
    assert count_words("Ala, ma kota ... -") == 3
