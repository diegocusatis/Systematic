"""Repeatable outcomes preserve per-row values, evidence choices, and history."""
from copy import deepcopy
import sqlite3

import pytest

from review_extractor.service import ReviewService


def source_page(text="Baseline 0. Follow-up 7."):
    return dict(page_number=1, text=text, status="ok", words=[], width=600,
                height=800, rotation=0, ocr=False, error="")


@pytest.fixture
def outcomes(tmp_path):
    service = ReviewService(tmp_path)
    review = service.create_review(dict(name="Outcome review"))["id"]
    study = service.add_item(review, dict(name="Study"))["id"]
    meta = service.add_item(review, dict(name="Score", data_type="number", section="meta_analysis"))["id"]
    paper = service.add_paper(review, dict(title="Synthetic source", processing_status="ready"))["id"]
    service.set_pages(review, paper, [source_page()])
    first = service.add_outcome(review, paper, dict(label="10MWT after treatment", outcome="10MWT", group="Treatment"))["id"]
    second = service.add_outcome(review, paper, dict(label="6MWT after treatment", outcome="6MWT", group="Treatment"))["id"]
    yield service, review, paper, study, meta, first, second
    service.store.close()


def approve(service, review, paper, item, row, value=0, quote="Baseline 0."):
    evidence = service.add_evidence(review, paper, item, dict(page_number=1, quote=quote), row)
    field = service.save_field(review, paper, item, dict(status="approved", value=value, evidence_ids=[evidence["id"]]), row)
    return evidence, field


def test_outcome_rows_keep_values_and_evidence_independent(outcomes):
    service, review, paper, study, meta, first, second = outcomes
    e1, f1 = approve(service, review, paper, meta, first)
    e2, f2 = approve(service, review, paper, meta, second, 7, "Follow-up 7.")
    assert f1["id"] != f2["id"]
    assert service.get_field(review, paper, meta, first)["value"] == 0
    assert service.get_field(review, paper, meta, second)["value"] == 7
    assert service.get_evidence(review, paper, meta, first) == [e1]
    assert service.get_evidence(review, paper, meta, second) == [e2]
    assert [row["item_id"] for row in service.store.list("fields", review_id=review)] == [study]
    with pytest.raises(ValueError, match="outcome row"):
        service.save_field(review, paper, meta, dict(status="approved", value=3, evidence_ids=[e1["id"]]), second)
    assert service.get_field(review, paper, meta, second) == f2
    assert service.evidence_with_locations(review, paper, meta, first)[0]["verification_location"].startswith("Page 1, line 1")


def test_scoped_candidate_decisions_never_change_another_outcome(outcomes):
    service, review, paper, study, meta, first, second = outcomes
    candidate = service.add_candidate(review, paper, meta, dict(page_number=1, quote="Baseline 0.",
        document_version=service.get_paper(review, paper)["document_version"], decision="accepted", notes="10MWT evidence"), first)
    identity = candidate["id"]
    assert candidate["decision"] == "accepted"
    assert service.store.get("suggestions", identity)["decision"] == "pending"
    assert service.get_suggestions(review, paper, meta, second)[0]["decision"] == "pending"
    assert service.get_suggestions(review, paper, meta, second)[0]["notes"] == ""
    rejected = service.decide_suggestion(review, paper, identity, dict(decision="rejected", notes="Different outcome"), second)
    assert service.get_suggestions(review, paper, meta, first)[0]["decision"] == "accepted"
    assert service.get_suggestions(review, paper, meta, second)[0]["decision"] == "rejected"
    assert service.get_field(review, paper, meta, first)["status"] == "unreviewed"
    service.update_evidence(review, paper, rejected["evidence_id"], dict(decision="pending"), second)
    assert service.get_suggestions(review, paper, meta, second)[0]["decision"] == "pending"
    assert service.get_suggestions(review, paper, meta, first)[0]["decision"] == "accepted"
    assert service.get_learning(review)["examples"] == []
    assert all(row["outcome_id"] in {first, second} for row in service.history(review) if row["action"] == "evidence_decided")


def test_outcome_and_item_and_paper_access_are_all_scoped(outcomes):
    service, review, paper, study, meta, first, second = outcomes
    other_review = service.create_review(dict(name="Other"))["id"]
    other_paper = service.add_paper(review, dict(title="Other paper"))["id"]
    with pytest.raises(KeyError):
        service.get_field(other_review, paper, meta, first)
    with pytest.raises(KeyError):
        service.get_field(review, other_paper, meta, first)
    with pytest.raises(ValueError, match="meta-analysis"):
        service.get_field(review, paper, study, first)
    with pytest.raises(ValueError, match="outcome row"):
        service.get_field(review, paper, meta)
    evidence, field = approve(service, review, paper, meta, first)
    with pytest.raises(KeyError):
        service.update_evidence(review, paper, evidence["id"], dict(decision="rejected"), second)
    with pytest.raises(sqlite3.IntegrityError):
        service.store.put("outcome_fields", dict(field, paper_id=other_paper))
    assert service.get_field(review, paper, meta, first) == field


def test_identity_evidence_item_and_source_changes_require_reconsideration(outcomes):
    service, review, paper, study, meta, first, second = outcomes
    e1, _ = approve(service, review, paper, meta, first)
    e2, _ = approve(service, review, paper, meta, second, 7, "Follow-up 7.")
    service.update_outcome(review, paper, first, dict(label="Clearer label", included=False))
    assert not service.get_field(review, paper, meta, first)["needs_reconsideration"]
    service.update_outcome(review, paper, first, dict(time_point="6 months"))
    assert service.get_field(review, paper, meta, first)["needs_reconsideration"]
    assert not service.get_field(review, paper, meta, second)["needs_reconsideration"]
    service.save_field(review, paper, meta, dict(status="approved"), first)
    service.update_evidence(review, paper, e2["id"], dict(decision="rejected"), second)
    assert not service.get_field(review, paper, meta, first)["needs_reconsideration"]
    assert service.get_field(review, paper, meta, second)["needs_reconsideration"]
    service.update_item(review, meta, dict(units="seconds"))
    assert service.get_field(review, paper, meta, first)["needs_reconsideration"]
    service.save_field(review, paper, meta, dict(status="approved"), first)
    approve(service, review, paper, study, None, "Baseline 0.")
    service.set_pages(review, paper, [source_page("Changed source 9.")])
    assert service.get_field(review, paper, meta, first)["needs_reconsideration"]
    assert service.get_field(review, paper, study)["needs_reconsideration"]
    assert service.get_evidence(review, paper, meta, first)[0]["source_stale"]
    with pytest.raises(ValueError, match="source"):
        service.save_field(review, paper, meta, dict(status="approved"), first)


def test_soft_delete_restore_and_restart_preserve_row_history(outcomes):
    service, review, paper, study, meta, first, second = outcomes
    evidence, field = approve(service, review, paper, meta, first)
    service.update_review(review, dict(settings=dict(last_paper_id=paper, last_item_id=meta,
                                                     last_outcome_id=first, last_section="meta_analysis")))
    service.delete_outcome(review, paper, first)
    assert [row["id"] for row in service.list_outcomes(review, paper)] == [second]
    assert len(service.list_outcomes(review, paper, include_deleted=True)) == 2
    with pytest.raises(ValueError, match="removed"):
        service.save_field(review, paper, meta, dict(value=99), first)
    service.restore_outcome(review, paper, first)
    assert service.get_field(review, paper, meta, first) == field
    reopened = ReviewService(service.data_dir)
    try:
        assert reopened.get_field(review, paper, meta, first) == field
        assert reopened.get_evidence(review, paper, meta, first) == [evidence]
        assert reopened.get_review(review)["settings"]["last_outcome_id"] == first
        assert {row["action"] for row in reopened.history(review)} >= {"outcome_deleted", "outcome_restored"}
        reopened.remove_paper(review, paper)
        snapshot = reopened.export_snapshot(review)
        assert snapshot["outcomes"] == snapshot["outcome_fields"] == []
    finally:
        reopened.store.close()


def test_outcome_resume_and_template_metadata_are_validated(outcomes):
    service, review, paper, study, meta, first, second = outcomes
    other_paper = service.add_paper(review, dict(title="Other"))["id"]
    with pytest.raises(KeyError):
        service.update_review(review, dict(settings=dict(last_paper_id=other_paper, last_outcome_id=first)))
    with pytest.raises(ValueError):
        service.update_review(review, dict(settings=dict(last_section="arbitrary")))
    style = {"header": {"fill": "FF0000"}}
    updated = service.update_item(review, meta, dict(template_style=style, source_column=2))
    assert updated["template_style"] == style and updated["source_column"] == 2
    before = deepcopy(updated)
    for change in (dict(section="bad"), dict(source_column=True), dict(template_style=[])):
        with pytest.raises(ValueError):
            service.update_item(review, meta, change)
    assert service._item(review, meta) == before


@pytest.mark.parametrize("heading,value,message", [
    ("n", 0, "positive whole number"),
    ("n", -1, "positive whole number"),
    ("n", 1.5, "positive whole number"),
    ("pre-sd", -0.1, "cannot be negative"),
    ("post-sd", -1, "cannot be negative"),
    ("R", -1.01, "between -1 and 1"),
    ("R", 1.01, "between -1 and 1"),
])
def test_invalid_meta_statistics_are_rejected_before_saving(outcomes, heading, value, message):
    service, review, paper, study, meta, first, second = outcomes
    service.update_item(review, meta, dict(name=heading, export_heading=heading))
    evidence = service.add_evidence(review, paper, meta, dict(page_number=1, quote="Baseline 0."), first)
    before = service.get_field(review, paper, meta, first)
    with pytest.raises(ValueError, match=message):
        service.save_field(review, paper, meta, dict(status="approved", value=value, evidence_ids=[evidence["id"]]), first)
    assert service.get_field(review, paper, meta, first) == before


@pytest.mark.parametrize("heading,value", [
    ("n", 1), ("pre-score", 0), ("post-score", 0), ("pre-sd", 0),
    ("post-sd", 0), ("R", -1), ("R", 0), ("R", 1),
])
def test_meta_statistic_boundaries_remain_valid(outcomes, heading, value):
    service, review, paper, study, meta, first, second = outcomes
    service.update_item(review, meta, dict(name=heading, export_heading=heading))
    evidence, field = approve(service, review, paper, meta, first, value)
    assert field["status"] == "approved" and field["value"] == value


def test_paper_resume_changes_clear_old_row_and_accept_current_paired_row(outcomes):
    service, review, paper, study, meta, first, second = outcomes
    other_paper = service.add_paper(review, dict(title="Other paper"))["id"]
    other_row = service.add_outcome(review, other_paper, dict(label="Other row"))["id"]
    settings = dict(last_paper_id=paper, last_item_id=meta, last_outcome_id=first, last_section="meta_analysis")
    service.update_review(review, dict(settings=settings))
    # An incremental paper change must not validate the previously saved row against the new paper.
    updated = service.update_review(review, dict(settings=dict(last_paper_id=other_paper)))
    assert updated["settings"]["last_outcome_id"] is None
    # The UI saves the current paper and row together, alongside retained settings.
    updated = service.update_review(review, dict(settings={**settings, "last_paper_id": other_paper, "last_outcome_id": other_row}))
    assert updated["settings"]["last_outcome_id"] == other_row
    updated = service.update_review(review, dict(settings={**updated["settings"], "last_paper_id": paper, "last_outcome_id": None}))
    assert updated["settings"]["last_outcome_id"] is None
