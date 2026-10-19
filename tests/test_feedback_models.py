"""Held-out retrieval audit, offline-model integration and feedback leak checks."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from review_extractor.hybrid.feedback import learning_text
from review_extractor.hybrid.semantic import LocalSemantic
from review_extractor.retrieval import search_item
from feedback_quality_fixture import CASES, evaluate_feedback, feedback_context, make_item, source_text
from test_hybrid import Encoder
from test_retrieval import page


def _ranked(rows):
    return [(row["page_number"], row["start"], row["end"], row["rank_score"], row.get("proposed_value")) for row in rows]


def _report(metrics, name):
    destination = os.environ.get("FEEDBACK_EVALUATION_DIR")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")


def test_frozen_fixture_labels_and_papers_are_separate():
    assert len(CASES) == 8
    for case in CASES:
        item = make_item(case)
        feedback = feedback_context(case)
        assert len({e["paper_id"] for e in feedback["examples"]}) == 2
        assert all(e["item_id"] == item["id"] for e in feedback["examples"])
        assert all(e["paper_id"] != feedback["target_paper_id"] for e in feedback["examples"])
        inputs = json.dumps(dict(item=item, feedback=feedback))
        assert all(quote not in inputs for quote in case["good"])
        assert all(quote not in inputs for quote in case["bad"])
        assert not (set(case["train"]) & set(case["good"]))


def test_fast_mode_feedback_audit_is_an_unchanged_baseline(tmp_path):
    encoder = Encoder()
    semantic = LocalSemantic(tmp_path, encoder=encoder)
    metrics = evaluate_feedback(search_item, page, semantic, fast=True)
    assert metrics["summary"]["baseline"] == metrics["summary"]["enabled"]
    assert metrics["improvements"] == metrics["regressions"] == 0
    assert encoder.calls == []
    assert all(row["status"] == "fast_mode" for case in metrics["cases"] for row in case["enabled"]["top_five"])
    _report(metrics, "fast.json")


def test_missing_model_keeps_learning_distinct_from_measured_zero(tmp_path):
    case = CASES[0]
    semantic = LocalSemantic(tmp_path)
    source = [page(source_text(case))]
    baseline = search_item(source, make_item(case), semantic=semantic)
    enabled = search_item(source, make_item(case), semantic=semantic, feedback=feedback_context(case))
    assert _ranked(enabled["matches"]) == _ranked(baseline["matches"])
    assert enabled["warnings"]
    assert all(row["learning"]["status"] == "unavailable" for row in enabled["matches"])
    assert all(row["scores"]["feedback_score"] is None for row in enabled["matches"])


@pytest.mark.parametrize("excluded_by", ["paper_id", "byte_fingerprint", "text_fingerprint"])
def test_target_paper_and_duplicate_copies_cannot_train_heldout_ranking(tmp_path, excluded_by):
    case = CASES[0]
    encoder = Encoder()
    semantic = LocalSemantic(tmp_path, encoder=encoder)
    source = [page(source_text(case))]
    item = make_item(case)
    baseline = search_item(source, item, semantic=semantic)
    feedback = feedback_context(case)
    # There are only two positive source documents. Excluding either must leave
    # sparse feedback, even when a new upload has a different paper ID.
    if excluded_by == "paper_id":
        feedback["examples"][0]["paper_id"] = feedback["target_paper_id"]
    else:
        fingerprint = "same-pdf-bytes" if excluded_by == "byte_fingerprint" else "same-extracted-text"
        feedback["target_document_fingerprints"].append(fingerprint)
        feedback["examples"][0]["document_fingerprints"].append(fingerprint)
    enabled = search_item(source, item, semantic=semantic, feedback=feedback)
    assert _ranked(enabled["matches"]) == _ranked(baseline["matches"])
    assert all(row["learning"]["status"] == "sparse_feedback" for row in enabled["matches"])


def test_paper_ids_values_and_click_count_do_not_change_learned_scores(tmp_path):
    case = CASES[0]
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    source = [page(source_text(case))]
    item = make_item(case)
    original = feedback_context(case)
    changed = deepcopy(original)
    changed["examples"][0]["quote"] = case["train"][0].replace("37.2", "81.9")
    changed["examples"][1]["quote"] = case["train"][1].replace("58.1", "24.7")
    for example in changed["examples"]:
        example["paper_id"] += "-unrelated-identity"
        example["example_id"] += "-other-stable-reference"
        example["feedback_id"] += "-other-event"
    # Even if an upstream caller repeats the same effective decision, it cannot
    # create another independent document or increase the contribution.
    changed["examples"] += deepcopy(changed["examples"])
    assert [learning_text(e["quote"]) for e in original["examples"]] == [learning_text(e["quote"]) for e in changed["examples"][:2]]
    baseline = search_item(source, item, semantic=semantic, feedback=original)
    enabled = search_item(source, item, semantic=semantic, feedback=changed)
    assert _ranked(enabled["matches"]) == _ranked(baseline["matches"])
    assert [row["scores"]["feedback_score"] for row in enabled["matches"]] == [row["scores"]["feedback_score"] for row in baseline["matches"]]


@pytest.mark.semantic
@pytest.mark.skipif(os.environ.get("RUN_SEMANTIC_TESTS") != "1", reason="Set RUN_SEMANTIC_TESTS=1 with a cached local model")
def test_real_offline_model_heldout_quality_and_warm_cache(tmp_path, monkeypatch):
    semantic = LocalSemantic(tmp_path)
    metrics = evaluate_feedback(search_item, page, semantic)
    assert semantic.encoder is not None, semantic.error
    assert all(not case[mode + "_warnings"] for case in metrics["cases"] for mode in ("baseline", "enabled")), metrics
    # Safety invariants are assertions. Relevance outcomes, including regressions,
    # are measured and reported without requiring an artificial perfect result.
    for mode in ("baseline", "enabled"):
        assert metrics["summary"][mode]["incorrect_value_proposals"] == 0, metrics
        assert metrics["summary"][mode]["duplicate_findings_at_5"] == 0, metrics
    for case in metrics["cases"]:
        for row in case["enabled"]["top_five"]:
            assert abs(row["feedback"]) <= .150001
            assert row["score"] == pytest.approx(row["baseline"] + row["feedback"], abs=.000001)
            assert all(influence["paper_id"] != "heldout" for influence in row["influences"])
    _report(dict(metrics, embedding_model=dict(name=semantic.model_name, revision=semantic.revision,
                                               offline=True)), "real-model.json")

    def forbidden(*args, **kwargs):
        raise AssertionError("Warm feedback search re-embedded cached source or example text")

    monkeypatch.setattr(semantic.encoder, "encode", forbidden)
    assert evaluate_feedback(search_item, page, semantic) == metrics
    restarted = LocalSemantic(tmp_path, model=semantic.model_name, encoder=semantic.encoder)
    restarted.revision = semantic.revision
    assert evaluate_feedback(search_item, page, restarted) == metrics
