from review_extractor.search import search_pages, validate_rules
import pytest


def page(text):
    return {"page_number": 1, "text": text, "words": [], "status": "ok", "ocr": False}


def test_complete_exact_passage_and_case_whitespace_matching():
    source = "Prior sentence. Treatment group\nuses 20 Hz. Next sentence."
    result = search_pages([page(source)], keywords=["20 hz", "uses 20 Hz"])
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert match["quote"] == "uses 20 Hz."
    assert source[match["start"]:match["end"]] == match["quote"]
    assert match["matched_rules"] == ["20 hz", "uses 20 Hz"]


def test_conflicting_captures_never_choose_first_value():
    result = search_pages([page("Treatment 0 events; control 2 events.")], rules=[
        {"kind": "regex", "pattern": r"(\d+) events", "capture_group": 1}])
    assert len(result["matches"]) == 1
    assert result["matches"][0]["proposed_value"] is None
    assert result["matches"][0]["multiple_proposed_values"]


def test_zero_is_a_transparent_unapproved_capture():
    result = search_pages([page("Treatment 0 events.")], rules=[
        {"kind": "regex", "pattern": r"(?P<count>\d+) events", "capture_group": "count"}])
    assert result["matches"][0]["proposed_value"] == "0"
    assert "decision" not in result["matches"][0]


def test_regex_timeout_is_reported_not_hidden():
    result = search_pages([page("a" * 100000 + "!")], rules=[
        {"kind": "regex", "pattern": "(a+)+$"}])
    assert result["errors"]
    assert result["errors"][0]["page_number"] == 1


def test_invalid_capture_rejected():
    import pytest
    with pytest.raises(ValueError):
        validate_rules([{"kind": "regex", "pattern": "(abc)", "capture_group": 9}])


@pytest.mark.parametrize('custom', [False, True])
def test_slow_highlights_do_not_consume_matching_deadline(monkeypatch, custom):
    """Reproduce the iterator deadline expiring while formatting prior hits."""
    import time
    from review_extractor import search
    original = search.boxes_for_span

    def delayed_boxes(*args):
        time.sleep(.07)  # Longer than the unchanged custom-pattern deadline.
        return original(*args)

    monkeypatch.setattr(search, 'boxes_for_span', delayed_boxes)
    source = page('Mean age was 42. SCI was thoracic. Follow-up age was 43.')
    options = {'rules': [{'kind': 'regex', 'pattern': 'age'}]} if custom else {'keywords': ['age']}
    result = search.search_pages([source], **options)
    assert result['errors'] == []
    assert [row['quote'] for row in result['matches']] == ['Mean age was 42.', 'Follow-up age was 43.']


def test_literals_preserve_unicode_whitespace_substrings_and_offsets():
    # Keep the established matching behavior, including substring matches:
    # this fix must not silently introduce word boundaries or normalization.
    source = 'dosage AGE age. SCI\n\tinjury; sci\u00a0injury. İ i ı I. Literal (a+)+$.'
    result = search_pages([page(source)], ['age', 'sci injury', 'i', '(a+)+$'])
    assert not result['errors']
    import regex
    for keyword in ['age', 'sci injury', 'i', '(a+)+$']:
        pattern = r'\s+'.join(regex.escape(part) for part in keyword.split())
        # The previous matcher with a generous diagnostic timeout is the oracle.
        from review_extractor.search import passage_span
        expected = {passage_span(source, match.start(), match.end()) for match in regex.finditer(pattern, source, regex.I, timeout=5)}
        actual = {(row['start'], row['end']) for row in result['matches'] if keyword in row['matched_rules']}
        assert actual == expected
    assert all(row['quote'] == source[row['start']:row['end']] for row in result['matches'])


def test_timeout_retains_earlier_matches_and_other_rules():
    source = page('age. ' + 'a' * 100000 + '!')
    result = search_pages([source], ['age'], [
        {'kind': 'regex', 'pattern': r'age|(a+)+$', 'label': 'difficult custom pattern'}])
    assert result['errors'][0]['code'] == 'regex_timeout'
    assert result['errors'][0]['rule'] == 'difficult custom pattern'
    first = next(row for row in result['matches'] if row['quote'] == 'age.')
    assert first['matched_rules'] == ['difficult custom pattern', 'age']
