"""Was a reading word for word? A tolerant comparison of the question with its transcript.

Numbers, Roman numerals, acronyms and abbreviations may be spoken as words ("1410 r." as
"tysiąc czterysta dziesiątego roku"), so those tokens of the question match any short run of
heard words. Everything else must be heard as written, apart from a word or two misheard.
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.9
MAX_GAP_TOKENS = 2
MAX_EXPANSION = 6  # heard words per flexible token at most ("1410" → four words)
ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "r.",
        "w.",
        "art.",
        "np.",
        "tzw.",
        "ok.",
        "m.in.",
        "itd.",
        "itp.",
        "ul.",
        "nr",
        "pkt.",
        "ust.",
        "wg",
        "tys.",
        "mln",
        "mld",
        "proc.",
    }
)

_ROMAN = re.compile(r"[IVXLCDM]+")
_ACRONYM = re.compile(r"[A-ZĄĆĘŁŃÓŚŹŻ]{2,6}")
_NON_WORD = re.compile(r"\W+")
# Punctuation around a word, except the dot an abbreviation keeps.
_EDGE = "\"'„”“«»()[]{},;:!?…–—-"


@dataclass(frozen=True)
class Match:
    ok: bool
    matched: float  # share of the question's tokens heard (or spoken as words)
    extra: float  # heard tokens not in the question, as a share of the question's tokens
    largest_gap: int  # the longest run of differing tokens


def _normalise(text: str) -> str:
    text = text.replace("ł", "l").replace("Ł", "L")  # NFKD does not decompose them
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return _NON_WORD.sub(" ", text.casefold())


def tokens(text: str) -> list[str]:
    return _normalise(text).split()


def _flexible(word: str) -> bool:
    core = word.strip(_EDGE)
    low = core.lower()
    bare = core.rstrip("." + _EDGE)  # "PKB." or "XVIII." at the end of a sentence
    return (
        any(c.isdigit() for c in core)
        or bool(_ROMAN.fullmatch(bare))
        or bool(_ACRONYM.fullmatch(bare))
        or low in ABBREVIATIONS
        or low.rstrip(".") in ABBREVIATIONS
    )


def _expected(text: str) -> tuple[list[str], list[bool]]:
    """The question's tokens and whether each may be spoken differently."""
    found: list[str] = []
    flexible: list[bool] = []
    for word in text.split():
        parts = tokens(word)
        found += parts
        flexible += [_flexible(word)] * len(parts)
    return found, flexible


def compare(expected: str, heard: str) -> Match:
    want, flexible = _expected(expected)
    got = tokens(heard)
    if not want:
        ok = not got
        return Match(ok, 1.0 if ok else 0.0, float(len(got)), len(got))
    matched = extra = gap = 0
    opcodes = SequenceMatcher(None, want, got, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in opcodes:
        spoken, said = i2 - i1, j2 - j1
        all_flexible = spoken > 0 and all(flexible[i1:i2])
        if tag == "equal":
            matched += spoken
        elif tag == "replace" and all_flexible and said <= MAX_EXPANSION * spoken:
            matched += spoken
        elif tag == "delete" and spoken == 1 and all_flexible:
            matched += 1
        else:
            extra += said
            gap = max(gap, spoken, said)
    share = matched / len(want)
    surplus = extra / len(want)
    ok = (
        share >= MATCH_THRESHOLD and surplus <= 1 - MATCH_THRESHOLD + 1e-9 and gap <= MAX_GAP_TOKENS
    )
    return Match(ok, share, surplus, gap)
