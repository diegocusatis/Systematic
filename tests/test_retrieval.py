"""Behavioral tests for concepts, exact source provenance and false positives."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from review_extractor.app import create_app
from review_extractor.documents import process_pdf, read_pdf_metadata
from review_extractor.retrieval import ENGINE_VERSION, search_item
from review_extractor.service import ReviewService
from retrieval_fixture import concept_pdf


def page(text, number=1):
    words = []
    cursor = 0
    for line_number, line in enumerate(text.splitlines()):
        for word_number, word in enumerate(line.split()):
            start = text.index(word, cursor)
            cursor = start + len(word)
            words.append(dict(text=word, start=start, end=cursor, x0=30 + word_number * 55,
                              x1=80 + word_number * 55, top=40 + line_number * 15, bottom=52 + line_number * 15))
    return dict(page_number=number, text=text, words=words, status="ok", width=900, height=900, ocr=False)


def item(name, **kwargs):
    return dict(name=name, keywords=[], rules=[], **kwargs)


def test_age_and_frequency_without_literal_keywords_keep_values_ambiguous():
    source = page("Treatment group at baseline\nParticipants averaged 42 years; control participants averaged 47 years.\nPulses were delivered at 20 Hz; sham used 1 Hz.")
    ages = search_item([source], item("Mean age"))["matches"]
    age = next(row for row in ages if "age_pattern" in row["retrieval_methods"])
    assert "42 years" in age["quote"] and "47 years" in age["quote"]
    assert "baseline" in age["context"] and age["proposed_value"] is None
    frequencies = search_item([source], item("Stimulation frequency"))["matches"]
    frequency = next(row for row in frequencies if "frequency_pattern" in row["retrieval_methods"])
    assert frequency["proposed_value"] is None and frequency["multiple_proposed_values"]
    for row in (age, frequency):
        assert row["quote"] == source["text"][row["start"]:row["end"]]
        assert row["boxes"] and row["reasons"] and row["source_type"] == "pdf_text"


def test_general_concept_fallback_uses_definition_instructions_and_editable_clues():
    source = page("Exhaustion persisted during the subsequent assessment.\nThe building was painted blue.")
    for description in ({"definition": "Fatigue at follow-up"}, {"instructions": "Find fatigue at follow-up"}):
        results = search_item([source], item("Custom observation", **description))["matches"]
        assert len(results) == 1 and "Exhaustion" in results[0]["quote"]
        assert "concept_passage" in results[0]["retrieval_methods"]
    custom = item("Custom observation")
    custom["keywords"] = ["fatigue"]
    before = deepcopy(custom)
    assert search_item([source], custom)["matches"]
    assert custom == before
    assert search_item([source], item("Electrode impedance"))["matches"] == []


def test_equipment_frequency_and_injury_duration_are_not_specialist_candidates():
    source = page("The recording system sampled at 1000 Hz.\nParticipants were 3 years after injury.\nDosage was 20 mg.")
    for name in ("Mean age", "Stimulation frequency"):
        rows = search_item([source], item(name))["matches"]
        assert not any(method.endswith("_pattern") for row in rows for method in row["retrieval_methods"])


def test_byline_excludes_references_citations_affiliations_and_acknowledgments():
    first = page("A synthetic stimulation trial\nAlice Example, Bob Sample\nDepartment of Rehabilitation, Example University\nAbstract\nThe authors cited Carol Reference and David Citation (2010).\nAcknowledgments\nEvelyn Helper")
    last = page("References\nCarol Reference, David Citation\nExample University", 2)
    author_item = item("Author")
    author_item["keywords"] = ["Authors"]
    rows = search_item([first, last], author_item, {"Author": "Morgan Example"})["matches"]
    visible = [row for row in rows if row["source_type"] == "pdf_text"]
    assert len(visible) == 1
    assert visible[0]["quote"] == "Alice Example, Bob Sample"
    assert visible[0]["proposed_value"] == "Alice Example; Bob Sample"
    metadata = next(row for row in rows if row["source_type"] == "pdf_metadata")
    assert metadata["quote"] == "Morgan Example" and metadata["page_number"] is None
    assert metadata["start"] is None and not metadata["boxes"]
    assert "Carol" not in " ".join(row["quote"] for row in rows)


def test_author_abstains_for_unsupported_names_and_software_metadata():
    rows = search_item([page("Unidentified heading\nAlice Example\nAbstract\nNo author information.")], item("Author"), {"Author": "Adobe Acrobat"})["matches"]
    assert rows == []
    scan = dict(page(""), status="ocr_required")
    rows = search_item([scan], item("Authors"), {"Author": "Alice Example"})["matches"]
    assert len(rows) == 1 and rows[0]["source_type"] == "pdf_metadata"


def test_multiline_byline_with_membership_labels_before_abstract():
    source = page("A Synthetic Stimulation Study\nAlice Example, Senior Member, IEEE, Bob Sample,\nClara Demo, David P. Fiction, and Emily Test\nAbstract\nParticipants received treatment.")
    rows = search_item([source], item("Author"))["matches"]
    assert len(rows) == 1
    assert rows[0]['quote'] == 'Alice Example, Senior Member, IEEE, Bob Sample,\nClara Demo, David P. Fiction, and Emily Test'
    assert rows[0]['proposed_value'] == 'Alice Example; Bob Sample; Clara Demo; David P. Fiction; Emily Test'
    assert 'IEEE' not in rows[0]['proposed_value']


def test_table_age_retains_both_columns_and_context():
    source = page("Baseline characteristics\nTreatment Control\nAge (years)\n42 +/- 8 47 +/- 9\nSex male female")
    matches = search_item([source], item("Mean age"))["matches"]
    hit = next(row for row in matches if 'age_pattern' in row['retrieval_methods'])
    assert '42 +/- 8 47 +/- 9' in hit['quote']
    assert 'Treatment Control' in hit['context']
    assert hit['proposed_value'] is None


def test_real_pdf_geometry_and_metadata_remain_distinct(tmp_path):
    path = tmp_path / "concepts.pdf"
    path.write_bytes(concept_pdf())
    pages = process_pdf(path)
    metadata = read_pdf_metadata(path)
    assert metadata["Author"] == "Morgan Example"
    rows = search_item(pages, item("Authors"), metadata)["matches"]
    visible = next(row for row in rows if row["source_type"] == "pdf_text")
    assert visible["quote"] == "Alice Example, Bob Sample"
    assert pages[0]["text"][visible["start"]:visible["end"]] == visible["quote"]
    assert all(90 < box["top"] < 115 for box in visible["boxes"])
    assert "Morgan Example" not in pages[0]["text"]


def test_metadata_cannot_be_accepted_as_page_evidence_and_edits_await_retrieval(tmp_path):
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        r = client.post('/api/reviews', json={"name": "Concept test"}).json()["id"]
        i = client.post(f'/api/reviews/{r}/items', json=item("Authors")).json()["id"]
        p = client.post(f'/api/reviews/{r}/papers', files={"files": ("concepts.pdf", concept_pdf(), "application/pdf")}).json()["papers"][0]["id"]
        client.post(f'/api/reviews/{r}/refresh')
        scope = f'/api/reviews/{r}/papers/{p}'
        rows = client.get(scope + '/suggestions', params={"item_id": i}).json()
        metadata = next(row for row in rows if row['source_type'] == 'pdf_metadata')
        assert client.patch(scope + '/suggestions/' + metadata['id'], json={"decision": "accepted"}).status_code == 422
        assert client.get(scope + f'/items/{i}/evidence').json() == []
        client.patch(scope + '/suggestions/' + metadata['id'], json={"decision": "rejected"})
        visible = next(row for row in rows if row['source_type'] == 'pdf_text')
        accepted = client.patch(scope + '/suggestions/' + visible['id'], json={"decision": "accepted"}).json()
        field_path = scope + f'/items/{i}/field'
        assert client.get(field_path).json()['status'] == 'unreviewed'
        approved = client.put(field_path, json={"status": "approved", "value": "Alice Example; Bob Sample", "evidence_ids": [accepted['evidence_id']]}).json()
        assert approved['status'] == 'approved'
        previous = client.get(f'/api/reviews/{r}').json()['rule_version']
        client.patch(f'/api/reviews/{r}/items/{i}', json={"name": "Author names"})
        assert client.get(f'/api/reviews/{r}').json()['rule_version'] > previous
        rows = client.get(scope + '/suggestions', params={"item_id": i}).json()
        assert next(row for row in rows if row['id'] == visible['id'])['decision'] == 'accepted'
        assert next(row for row in rows if row['id'] == metadata['id'])['decision'] == 'rejected'
        assert client.get(field_path).json() == approved
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        assert client.get(field_path).json() == approved


def test_engine_upgrade_preserves_existing_values_and_source_spans(tmp_path):
    service = ReviewService(tmp_path)
    r = service.create_review({"name": "Legacy"})['id']
    i = service.add_item(r, item("Mean age"))['id']
    p = service.add_paper(r, {"title": "Legacy", "processing_status": "ready"})['id']
    service.set_pages(r, p, [page("Participants averaged 42 years.")])
    before = service.get_field(r, p, i)
    source = service.get_pages(r, p)
    service.store.close()
    with TestClient(create_app(tmp_path, run_background=False)) as client:
        service = client.app.state.service
        assert service.get_field(r, p, i) == before
        assert service.get_pages(r, p) == source
        assert not service.get_suggestions(r, p, i)
        client.post(f"/api/reviews/{r}/refresh")
        assert service.get_suggestions(r, p, i)
        assert service.get_paper(r, p)['retrieval_engine_version'] == ENGINE_VERSION
