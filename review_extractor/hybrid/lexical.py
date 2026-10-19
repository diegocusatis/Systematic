"""Bounded fuzzy support cannot outweigh exact scientific aliases."""
from difflib import SequenceMatcher
from functools import lru_cache
import re
from .chunking import normalize

try:
    from rapidfuzz.fuzz import ratio as _ratio
except ImportError:
    def _ratio(a, b): return SequenceMatcher(None, a, b).ratio() * 100


@lru_cache(maxsize=8192)
def _terms(text):
    # Immutable, bounded text-only cache: safe across reviews and item edits.
    return tuple(re.findall(r"[\w%]+", normalize(text).lower().replace("_", " ")))


def terms(text):
    return list(_terms(text))


@lru_cache(maxsize=8192)
def _joined(text):
    return " ".join(_terms(text))


@lru_cache(maxsize=2048)
def _phrase_pattern(phrase):
    return re.compile(r"(?<!\w)" + re.escape(_joined(phrase)) + r"(?!\w)")


def contains(text, phrase):
    return bool(phrase and _phrase_pattern(phrase).search(_joined(text)))


def lexical_scores(text, aliases):
    words = _terms(text)
    exact, fuzzy = 0., 0.
    for alias in aliases:
        tokens = _terms(alias)
        if not tokens: continue
        if contains(text, alias): exact = 1.; continue
        # Short abbreviations require an exact match: SD must not fuzzy-match SE.
        if len("".join(tokens)) < 5: continue
        width = len(tokens)
        phrase = " ".join(tokens)
        for i in range(max(0, len(words)-width+1)):
            similarity = _ratio(phrase, " ".join(words[i:i+width])) / 100
            if similarity >= .84: fuzzy = max(fuzzy, similarity)
    return exact, fuzzy
