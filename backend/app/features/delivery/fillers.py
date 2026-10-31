import re

from app.features.languages.profiles import LanguageProfile


def tokenize(text: str) -> list[str]:
    """Whitespace tokens with punctuation stripped from both ends; empty ones dropped."""
    tokens = (re.sub(r"^\W+|\W+$", "", raw) for raw in text.split())
    return [token for token in tokens if token]


def count_words(text: str) -> int:
    return len(tokenize(text))


def find_fillers(text: str, profile: LanguageProfile) -> list[str]:
    """Filler candidates in order, as they appear. A bigram counts once and
    consumes its tokens, so they are not also counted as unigrams."""
    tokens = tokenize(text)
    found: list[str] = []
    i = 0
    while i < len(tokens):
        if (
            i + 1 < len(tokens)
            and (tokens[i].casefold(), tokens[i + 1].casefold()) in profile.filler_bigrams
        ):
            found.append(f"{tokens[i]} {tokens[i + 1]}")
            i += 2
            continue
        if profile.filler_unigrams.match(tokens[i].casefold()):
            found.append(tokens[i])
        i += 1
    return found
