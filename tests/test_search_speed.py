"""Cached matching must preserve the previous matching semantics."""
from difflib import SequenceMatcher
import re

import pytest

from review_extractor.hybrid import lexical
from review_extractor.hybrid.chunking import normalize
from review_extractor.retrieval import query_groups


def old_terms(text):
    return re.findall(r'[\w%]+', normalize(text).lower().replace('_', ' '))


def old_contains(text, phrase):
    return bool(phrase and re.search(r'(?<!\w)' + re.escape(' '.join(old_terms(phrase))) + r'(?!\w)', ' '.join(old_terms(text))))


def old_scores(text, aliases):
    words = old_terms(text)
    exact, fuzzy = 0., 0.
    for alias in aliases:
        tokens = old_terms(alias)
        if not tokens:
            continue
        if old_contains(text, alias):
            exact = 1.
            continue
        if len(''.join(tokens)) < 5:
            continue
        for i in range(max(0, len(words) - len(tokens) + 1)):
            similarity = lexical._ratio(' '.join(tokens), ' '.join(words[i:i+len(tokens)])) / 100
            if similarity >= .84:
                fuzzy = max(fuzzy, similarity)
    return exact, fuzzy


@pytest.mark.parametrize('fallback', [False, True])
def test_cached_lexical_matches_reference_in_both_backends(monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(lexical, '_ratio', lambda a, b: SequenceMatcher(None, a, b).ratio() * 100)
    texts = ['The dosage was adjusted, not participant age.', 'SD = 3.1; SE = 0.5.',
             'Participants received repetitive stimu1ation at 10 H2.',
             'Motor thresh-\nold was 90% RMT.', 'Ｃontrol_group: 43 +/- 9 years.',
             '', 'Symbols ± % and punctuated WISCI-II.', 'Repeated repeated words.']
    aliases = ['age', 'SD', 'SE', 'stimulation', 'motor threshold', 'RMT', 'control group',
               '43 ± 9', '10 Hz', 'WISCI-II', '', '!!!', 'repeated words']
    for text in texts:
        for phrase in aliases:
            assert lexical.contains(text, phrase) == old_contains(text, phrase)
        assert lexical.lexical_scores(text, aliases) == old_scores(text, aliases)
        assert lexical.lexical_scores(text, list(reversed(aliases))) == old_scores(text, list(reversed(aliases)))


def test_cached_queries_follow_edits_and_cannot_be_mutated():
    item = {'name': 'Mean age', 'keywords': ['age']}
    expected = query_groups(item)
    query, groups = query_groups(item)
    query.clear()
    groups[0][0].clear()
    assert query_groups(item) == expected
    item['name'] = 'Stimulation frequency'
    item['keywords'] = ['Hz']
    assert query_groups(item) != expected
    assert 'hz' in query_groups(item)[0]
