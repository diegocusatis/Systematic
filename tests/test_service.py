"""Core invariants that must survive UI changes and persistent re-searching."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import threading

import pytest

from review_extractor.service import ReviewService
from review_extractor.search import search_pages, validate_rules


def page(text, number=1, status="ok"):
    words = []
    cursor = 0
    for index, word in enumerate(text.split()):
        start = text.index(word, cursor)
        words.append({"text": word, "start": start, "end": start + len(word),
                      "x0": index * 25, "x1": index * 25 + 20, "top": 10, "bottom": 20})
        cursor = start + len(word)
    return {"page_number": number, "text": text, "words": words, "width": 800, "height": 600,
            "rotation": 0, "status": status, "ocr": False, "error": ""}


@pytest.fixture
def core(tmp_path):
    service = ReviewService(tmp_path)
    review = service.create_review({"name": "Core review", "reviewer": "Researcher"})
    item = service.add_item(review["id"], {"name": "Events", "data_type": "integer", "keywords": ["adverse events"]})
    paper = service.add_paper(review["id"], {"title": "Synthetic paper", "processing_status": "ready"})
    service.set_pages(review["id"], paper["id"], [page("Treatment had 0 adverse events. Control had 2 adverse events.")])
    yield service, review["id"], paper["id"], item["id"]
    service.store.close()


def accepted(core, **kwargs):
    service, review, paper, item = core
    return service.add_evidence(review, paper, item, {"page_number": 1, "quote": "Treatment had 0 adverse events.", **kwargs})


def test_export_snapshot_is_current_without_loading_retrieval_audit(core, monkeypatch):
    service, review, paper_id, item = core
    evidence = accepted(core)
    original = service.store.list
    def guarded(table, **filters):
        assert table not in {'suggestions', 'feedback', 'history', 'pages'}
        return original(table, **filters)
    monkeypatch.setattr(service.store, 'list', guarded)
    snapshot = service.export_snapshot(review)
    assert snapshot['evidence'][0]['id'] == evidence['id']
    assert 'words' not in snapshot['pages'][0]
    assert snapshot['pages'][0]['text'].startswith('Treatment had 0')
    changed = dict(evidence, decision='rejected')
    service.store.put('evidence', changed)
    assert service.export_snapshot(review)['evidence'][0]['decision'] == 'rejected'
    other = service.create_review({'name': 'Other'})
    assert service.export_snapshot(other['id'])['evidence'] == []


def test_reading_locations_match_verification_and_do_not_change_evidence(core):
    from review_extractor.workbooks import verification_preview
    service, review, paper_id, item = core
    record = accepted(core)
    before = service.get_evidence(review, paper_id, item)
    displayed = service.evidence_with_locations(review, paper_id, item)[0]
    assert displayed['verification_location'] == 'Page 1, line 1'
    exported = verification_preview(service.export_snapshot(review), include_accepted=True)
    assert exported['rows'][0]['values'][0] == displayed['verification_location']
    assert service.get_evidence(review, paper_id, item) == before
    other = service.create_review({'name': 'Other'})
    with pytest.raises(KeyError):
        service.evidence_with_locations(other['id'], paper_id, item)
    service.update_evidence(review, paper_id, record['id'], {'source_location': 'Table 1, Treatment row'})
    assert 'Table 1, Treatment row' in service.evidence_with_locations(review, paper_id, item)[0]['verification_location']


def test_manual_image_location_on_searchable_page_is_retained_without_reapproval(core):
    service, review, paper, item = core
    with pytest.raises(ValueError, match='precise source location'):
        accepted(core, quote='0 in the graph', manual=True, rationale='Transcribed from a graph.')
    evidence = accepted(core, quote='0 in the graph', manual=True, rationale='Transcribed from a graph.',
                        source_location='within Figure 1, panel B, Treatment bar')
    assert evidence['start'] is None and evidence['source_location'].startswith('within Figure 1')
    field = service.save_field(review, paper, item, {'status': 'approved', 'value': 0, 'evidence_ids': [evidence['id']]})
    service.update_evidence(review, paper, evidence['id'], {'source_location': 'within Figure 1, panel B, left Treatment bar'})
    assert service.get_field(review, paper, item) == field
    assert service.get_evidence(review, paper, item)[0]['source_location'].endswith('left Treatment bar')
    with pytest.raises(ValueError, match='precise source location'):
        service.update_evidence(review, paper, evidence['id'], {'source_location': ''})


def test_partial_search_retains_results_decisions_and_requires_retry(core, monkeypatch):
    from review_extractor import service as module
    service, review, paper, item = core
    service.refresh_search(review, paper)
    candidate = service.get_suggestions(review, paper, item)[0]
    service.decide_suggestion(review, paper, candidate['id'], {'decision': 'rejected'})
    field = service.get_field(review, paper, item)
    original = module.search_item

    def incomplete(*args, **kwargs):
        result = original(*args, **kwargs)
        result['errors'].append({'page_number': 1, 'rule': 'custom pattern', 'code': 'regex_timeout', 'error': 'Timed out'})
        return result

    monkeypatch.setattr(module, 'search_item', incomplete)
    service.refresh_search(review, paper)
    current = service.get_paper(review, paper)
    assert current['search_status'] == 'partial'
    assert current['search_errors'][0]['item_id'] == item
    assert current['search_progress'] == 100
    matches = service.get_suggestions(review, paper, item)
    assert matches and next(m for m in matches if m['id'] == candidate['id'])['decision'] == 'rejected'
    assert service.get_field(review, paper, item) == field
    monkeypatch.setattr(module, 'search_item', original)
    service.refresh_search(review, paper)
    assert service.get_paper(review, paper)['search_status'] == 'current'
    assert service.get_paper(review, paper)['search_errors'] == []


def test_exact_passages_dedup_capture_and_full_document():
    pages = [page("Treatment had 0 adverse events. Control had 2 adverse events."),
             page("Follow-up: week 6.", 2), page("", 3, "ocr_required")]
    found = search_pages(pages, ["adverse", "adverse events", "FOLLOW-UP"],
                         [{"kind": "regex", "pattern": r"(\d+) adverse events", "capture_group": 1, "label": "event count"}])
    assert len(found["matches"]) == 3
    first = found["matches"][0]
    assert first["quote"] == "Treatment had 0 adverse events."
    assert pages[0]["text"][first["start"]:first["end"]] == first["quote"]
    assert first["proposed_value"] == "0"
    assert set(first["matched_rules"]) == {"adverse", "adverse events", "event count"}
    assert first["boxes"]
    multiple = search_pages([page("Treatment 1 event and control 2 event.")], rules=[
        {"kind": "regex", "pattern": r"(\d+) event", "capture_group": "1"}])
    assert multiple["matches"][0]["proposed_value"] is None


def test_regex_timeout_is_explicit_and_invalid_groups_are_rejected():
    with pytest.raises(ValueError, match="Invalid regex"):
        validate_rules([{"kind": "regex", "pattern": "("}])
    with pytest.raises(ValueError, match="Capture group"):
        validate_rules([{"kind": "regex", "pattern": "word", "capture_group": []}])
    found = search_pages([page("a" * 30000 + "!")], rules=[{"kind": "regex", "pattern": "(a+)+$"}])
    assert found["errors"] and "timed out" in found["errors"][0]["error"]
    assert found["matches"] == []


def test_zero_evidence_and_approval_are_separate_and_withdrawal_preserves_history(core):
    service, review, paper, item = core
    service.refresh_search(review)
    suggestion = service.get_suggestions(review, paper, item)[0]
    decision = service.decide_suggestion(review, paper, suggestion["id"], {"decision": "accepted"})
    assert service.get_field(review, paper, item)["status"] == "unreviewed"
    evidence = service.get_evidence(review, paper, item)[0]
    assert evidence["schema_version"] == service.get_review(review)["schema_version"]
    approved = service.save_field(review, paper, item, {"status": "approved", "value": 0, "evidence_ids": [decision["evidence_id"]]})
    assert approved["value"] == 0
    service.update_evidence(review, paper, evidence["id"], {"decision": "rejected"})
    field = service.get_field(review, paper, item)
    assert field["value"] == 0 and field["needs_reconsideration"]
    with pytest.raises(ValueError, match="supporting evidence"):
        service.save_field(review, paper, item, {"status": "approved"})
    history = service.snapshot(review)["history"]
    assert any(event["action"] == "evidence_changed_reconsideration" and event["before"]["value"] == 0 for event in history)


def test_scoping_is_enforced_for_references_and_database_rows(core):
    service, review, paper, item = core
    evidence = accepted(core)
    other_review = service.create_review({"name": "Independent"})["id"]
    other_item = service.add_item(other_review, {"name": "Other"})["id"]
    other_paper = service.add_paper(other_review, {"title": "Other"})["id"]
    for action in [lambda: service.get_field(other_review, paper, item),
                   lambda: service.get_evidence(review, other_paper, item),
                   lambda: service.update_evidence(other_review, other_paper, evidence["id"], {"decision": "accepted"})]:
        with pytest.raises(KeyError):
            action()
    with pytest.raises(ValueError, match="belong"):
        service.save_field(other_review, other_paper, other_item, {"status": "approved", "value": "claimed", "evidence_ids": [evidence["id"]]})
    forged = dict(service.get_field(review, paper, item), review_id=other_review)
    with pytest.raises(sqlite3.IntegrityError):
        service.store.put("fields", forged)


def test_unchanged_rejection_survives_rule_removal_reintroduction_and_restart(core, tmp_path):
    service, review, paper, item = core
    service.refresh_search(review)
    first = service.get_suggestions(review, paper, item)[0]
    service.decide_suggestion(review, paper, first["id"], {"decision": "rejected", "notes": "Not applicable to this field"})
    service.update_item(review, item, {"keywords": ["unmatched"]})
    assert service.get_paper(review, paper)["search_status"] == "stale"
    assert all(row["stale"] for row in service.get_suggestions(review, paper, item))
    service.refresh_search(review)
    assert service.get_suggestions(review, paper, item)[0]["active"] is False
    assert service.get_field(review, paper, item)["status"] == "unreviewed"
    service.update_item(review, item, {"keywords": [], "rules": [{"kind": "regex", "pattern": "adverse events"}]})
    service.refresh_search(review)
    reopened = ReviewService(tmp_path)
    try:
        rows = reopened.get_suggestions(review, paper, item)
        assert len(rows) == 2 and len({row["id"] for row in rows}) == 2
        assert next(row for row in rows if row["id"] == first["id"])["decision"] == "rejected"
    finally:
        reopened.store.close()


def test_edit_during_refresh_keeps_results_visibly_stale(core, monkeypatch):
    import review_extractor.service as module
    service, review, paper, item = core
    entered, proceed = threading.Event(), threading.Event()
    real_search = module.search_item
    def delayed(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return real_search(*args, **kwargs)
    monkeypatch.setattr(module, "search_item", delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.refresh_search, review, paper)
        assert entered.wait(5)
        assert service.get_paper(review, paper)["search_status"] == "refreshing"
        service.update_item(review, item, {"keywords": ["control"]})
        proceed.set()
        future.result(timeout=5)
    assert service.get_paper(review, paper)["search_status"] == "stale"
    assert all(row["stale"] for row in service.get_suggestions(review, paper, item))
    monkeypatch.setattr(module, "search_item", real_search)
    service.refresh_search(review, paper)
    assert service.get_paper(review, paper)["search_status"] == "current"


def test_removing_paper_during_refresh_does_not_restore_it(core, monkeypatch):
    import review_extractor.service as module
    service, review, paper, item = core
    service.refresh_search(review)
    entered, proceed = threading.Event(), threading.Event()
    original = module.search_item
    def delayed(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'search_item', delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(service.refresh_search, review, paper)
        assert entered.wait(5)
        service.remove_paper(review, paper)
        proceed.set()
        pending.result(timeout=5)
    assert service.list_papers(review) == []
    assert all(not row['active'] for row in service.store.list('suggestions', review_id=review, paper_id=paper))


def test_manual_transcription_and_repeated_text_need_provenance(core):
    service, review, paper, item = core
    service.set_pages(review, paper, [page("Repeated. Repeated."), page("", 2, "ocr_required")])
    with pytest.raises(ValueError, match="more than once"):
        service.add_evidence(review, paper, item, {"page_number": 1, "quote": "Repeated."})
    exact = service.add_evidence(review, paper, item, {"page_number": 1, "quote": "Repeated.", "start": 10, "end": 19, "ocr": True})
    assert exact["start"] == 10 and exact["ocr"]
    with pytest.raises(ValueError, match="manual"):
        service.add_evidence(review, paper, item, {"page_number": 2, "quote": "Transcribed from image"})
    manual = service.add_evidence(review, paper, item, {"page_number": 2, "quote": "0 events", "manual": True,
                                                     "rationale": "Read the image of the table in original PDF."})
    assert manual["start"] is None and manual["manual"] and manual["rationale"]


def test_material_change_and_document_reprocessing_cannot_silently_reapprove(core):
    service, review, paper, item = core
    evidence = accepted(core)
    service.save_field(review, paper, item, {"status": "approved", "value": 0, "evidence_ids": [evidence["id"]]})
    service.update_item(review, item, {"name": "Renamed", "export_heading": "New heading"})
    assert not service.get_field(review, paper, item)["needs_reconsideration"]
    service.update_item(review, item, {"definition": "Count serious events only"})
    assert service.get_field(review, paper, item)["needs_reconsideration"]
    service.set_pages(review, paper, [page("The source text changed after reprocessing.")])
    with pytest.raises(ValueError, match="no longer matches"):
        service.save_field(review, paper, item, {"status": "approved"})
    assert service.get_field(review, paper, item)["value"] == 0


@pytest.mark.parametrize("change", [{"value": True}, {"value": float("inf")}, {"evidence_ids": [{}]},
                                     {"status": []}, {"observations": [{"id": [], "group": "A"}]}])
def test_invalid_approval_payloads_fail_without_mutating_state(core, change):
    service, review, paper, item = core
    evidence = accepted(core)
    data = {"status": "approved", "value": 0, "evidence_ids": [evidence["id"]], **change}
    before = deepcopy(service.get_field(review, paper, item))
    with pytest.raises(ValueError):
        service.save_field(review, paper, item, data)
    assert service.get_field(review, paper, item) == before


def test_calculations_missingness_and_distinct_observations(core):
    service, review, paper, item = core
    evidence = accepted(core)
    data = {"status": "approved", "value": 0, "value_kind": "calculated", "evidence_ids": [evidence["id"]]}
    with pytest.raises(ValueError, match="rationale"):
        service.save_field(review, paper, item, data)
    with pytest.raises(ValueError, match="inputs"):
        service.save_field(review, paper, item, dict(data, rationale="Sum per-group event counts"))
    saved = service.save_field(review, paper, item, dict(data, rationale="Sum treatment event counts", calculation_inputs={"treatment": 0}))
    assert saved["value"] == 0 and saved["calculation_inputs"] == {"treatment": 0}
    service.update_item(review, item, {"data_type": "narrative"})
    observations = [{"group": group, "time_point": time, "value": value, "units": "events", "evidence_ids": [evidence["id"]]}
                    for group, time, value in [("Treatment", "week 3", 0), ("Control", "week 3", 2), ("Treatment", "week 6", 1)]]
    narrative = service.save_field(review, paper, item, {"status": "approved", "value": "Treatment week 3: 0; control week 3: 2; treatment week 6: 1.",
                                                       "value_kind": "reported", "observations": observations})
    assert len(narrative["observations"]) == 3
    assert len({row["id"] for row in narrative["observations"]}) == 3
    missing = service.save_field(review, paper, item, {"status": "not_reported", "notes": "Checked full document including all tables."})
    assert missing["value"] is None and missing["status"] == "not_reported"


def test_restoring_deleted_column_maintains_unique_positions_and_prior_values(core):
    service, review, paper, item = core
    second = service.add_item(review, {"name": "Second", "position": 0})["id"]
    third = service.add_item(review, {"name": "Third", "position": 0})["id"]
    assert [row["id"] for row in service.list_items(review)] == [item, second, third]
    service.delete_item(review, second)
    service.reorder_items(review, [third, item])
    service.restore_item(review, second)
    assert [row["id"] for row in service.list_items(review)] == [third, second, item]
    assert [row["position"] for row in service.list_items(review)] == [0, 1, 2]


def test_empty_search_and_malformed_reorder_still_validate_scope(core):
    service, review, paper, item = core
    other = service.create_review({"name": "Other review"})["id"]
    with pytest.raises(KeyError):
        service.search_document(other, paper, " ")
    with pytest.raises(ValueError):
        service.reorder_items(review, [{"id": item}])


def test_geometry_changes_preserve_old_evidence_and_require_source_reselection(core):
    service, review, paper, item = core
    service.refresh_search(review)
    suggestion = service.get_suggestions(review, paper, item)[0]
    evidence_id = service.decide_suggestion(review, paper, suggestion["id"], {"decision": "accepted"})["evidence_id"]
    service.save_field(review, paper, item, {"status": "approved", "value": 0, "evidence_ids": [evidence_id]})
    before = service.get_evidence(review, paper, item)[0]
    pages = service.get_pages(review, paper)
    for word in pages[0]["words"]:
        word["top"] += 50
        word["bottom"] += 50
    service.set_pages(review, paper, pages)
    stale = service.get_evidence(review, paper, item)[0]
    assert stale["source_stale"] and stale["boxes"] == before["boxes"]
    assert service.get_field(review, paper, item)["needs_reconsideration"]
    with pytest.raises(ValueError, match="Refresh suggestions"):
        service.decide_suggestion(review, paper, suggestion["id"], {"decision": "accepted"})
    service.refresh_search(review)
    service.decide_suggestion(review, paper, suggestion["id"], {"decision": "accepted"})
    renewed = service.get_evidence(review, paper, item)[0]
    assert renewed["id"] == evidence_id and not renewed["source_stale"]
    assert renewed["boxes"][0]["top"] == before["boxes"][0]["top"] + 50
    field = service.save_field(review, paper, item, {"status": "approved"})
    assert not field["needs_reconsideration"] and field["value"] == 0


@pytest.mark.parametrize("edit", ["delete", "reprocess"])
def test_delete_or_reprocess_during_refresh_cannot_publish_current_old_results(core, monkeypatch, edit):
    import review_extractor.service as module
    service, review, paper, item = core
    entered, proceed = threading.Event(), threading.Event()
    real_search = module.search_item
    def delayed(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return real_search(*args, **kwargs)
    monkeypatch.setattr(module, "search_item", delayed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.refresh_search, review, paper)
        assert entered.wait(5)
        if edit == "delete":
            service.delete_item(review, item)
        else:
            service.set_pages(review, paper, [page("Replacement document wording.")])
        proceed.set()
        future.result(timeout=5)
    assert service.get_paper(review, paper)["search_status"] == "stale"
    if edit == "delete":
        assert not any(row["active"] for row in service.snapshot(review)["suggestions"])
    else:
        assert all(row["stale"] for row in service.get_suggestions(review, paper, item))


def test_notes_only_save_cannot_silently_clear_material_reconsideration(core):
    service, review, paper, item = core
    evidence = accepted(core)
    service.save_field(review, paper, item, {"status": "approved", "value": 0, "evidence_ids": [evidence["id"]]})
    service.update_item(review, item, {"definition": "Only serious adverse events"})
    field = service.save_field(review, paper, item, {"notes": "Need to inspect the changed definition."})
    assert field["needs_reconsideration"]
    field = service.save_field(review, paper, item, {"status": "approved", "notes": "Rechecked against the new definition."})
    assert not field["needs_reconsideration"]
