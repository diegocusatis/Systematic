"""Feedback isolation, conservative ranking, and cached batch regressions."""
from copy import deepcopy

import pytest

from review_extractor.hybrid.feedback import apply_feedback, learning_text
from review_extractor.hybrid.models import item_spec
from review_extractor.hybrid.semantic import LocalSemantic
from review_extractor.retrieval import search_item
from test_retrieval import item, page


def example(index, quote="Participants had a mean age of 39 years.", **changes):
    return dict(example_id=f"example-{index}", feedback_id=f"feedback-{index}",
        paper_id=f"source-{index}", item_id="item", document_fingerprints=[f"pdf-{index}", f"text-{index}"],
        quote=quote, decision="accepted", rejection_reason=None, **changes)


def feedback(examples=None, **config):
    return dict(config=dict(enabled=True, version=1, epoch=0, revision=3, **config),
        examples=examples if examples is not None else [example(1), example(2)],
        target_document_fingerprints=["target-pdf", "target-text"], target_paper_id="held-out")


class Encoder:
    def __init__(self): self.calls=[]

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return [[1., 0.] for _ in texts]


class DiscoveryEncoder(Encoder):
    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return [[0., 1.] if any(word in text.lower() for word in ("withdrawal", "discontinued", "ceased")) else [1., 0.]
                for text in texts]


def test_matrix_is_one_deduplicated_batch_reused_after_restart(tmp_path):
    encoder = Encoder()
    semantic = LocalSemantic(tmp_path, model="synthetic-feedback", encoder=encoder)
    matrix, warning = semantic.similarity_matrix(["accepted one", "accepted two"], ["candidate one", "candidate two", "accepted one"])
    assert matrix == [[1., 1., 1.], [1., 1., 1.]] and warning is None
    assert len(encoder.calls) == 1 and len(encoder.calls[0][0]) == 4
    assert encoder.calls[0][1]["batch_size"] == 32
    semantic.similarity_matrix(["accepted one", "accepted two"], ["candidate one", "candidate two"])
    assert len(encoder.calls) == 1
    restarted = Encoder()
    semantic = LocalSemantic(tmp_path, model="synthetic-feedback", encoder=restarted)
    semantic.similarity_matrix(["accepted one"], ["candidate two"])
    assert restarted.calls == []
    semantic.similarity_matrix(["new example"], ["candidate two"])
    assert restarted.calls[0][0] == ["new example"]


def test_feedback_cache_connections_close_without_waiting_for_garbage_collection(tmp_path, monkeypatch):
    import sqlite3
    from review_extractor.hybrid import semantic as module
    original = sqlite3.connect
    connections = []
    def tracked(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(module.sqlite3, "connect", tracked)
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    semantic.similarity_matrix(["example"], ["candidate"])
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_masking_numbers_ids_names_preserves_units_and_source():
    text = "Alice Smith reported mean age 47.25 ± 6.8 years at River Hospital; NCT01234567, DOI 10.1234/example. Thirty-two participants."
    original = text[:]
    masked = learning_text(text)
    assert text == original
    assert all(value not in masked for value in ["alice", "smith", "47.25", "6.8", "01234567", "river", "hospital", "thirty"])
    assert "mean age" in masked and "±" in masked and "years" in masked and "participants" in masked
    assert learning_text("Mean age 37 ± 8 years.") == learning_text("Mean age 62 ± 14 years.")
    assert "10mwt" in learning_text("Change in 10MWT was 9.1.")


def test_sparse_disabled_and_target_duplicates_keep_exact_baseline(tmp_path):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    pages = [page("Participants had a mean age of 51 years.\nAge affects rehabilitation outcomes.")]
    spec = item("Mean age")
    baseline = search_item(pages, spec, semantic=semantic)["matches"]
    cases = [({"config": {"enabled": False}}, "disabled"),
        (feedback([example(1)]), "sparse_feedback"),
        (feedback([example(1), dict(example(2), document_fingerprints=["pdf-1", "new-text"])]), "sparse_feedback"),
        (feedback([example(1), dict(example(2), paper_id="source-1")]), "sparse_feedback"),
        (feedback([example(1), dict(example(2), document_fingerprints=["target-pdf"])]), "sparse_feedback")]
    for data, status in cases:
        rows = search_item(pages, spec, semantic=semantic, feedback=data)["matches"]
        assert [(r["quote"],r["rank_score"]) for r in rows] == [(r["quote"],r["rank_score"]) for r in baseline]
        assert all(r["learning"]["status"] == status and r["scores"]["feedback_score"] is None for r in rows)


def test_transitive_target_copies_and_same_paper_never_train_themselves(tmp_path):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    data = feedback([example(1), dict(example(2), document_fingerprints=["target-pdf", "alias-a"]),
        dict(example(3), document_fingerprints=["alias-a", "alias-b"]),
        dict(example(4), paper_id="held-out")])
    rows = search_item([page("Participants had a mean age of 48 years.")], item("Mean age"), semantic=semantic, feedback=data)["matches"]
    assert all(r["learning"]["status"] == "sparse_feedback" and r["learning"]["eligible_examples"] == 1 for r in rows)


def test_nontraining_rejection_can_still_identify_duplicate_training_sources(tmp_path):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    data = feedback([dict(example(1), document_fingerprints=["first-pdf", "first-text"]),
        dict(example(2), document_fingerprints=["second-pdf", "second-text"]),
        dict(example(3), document_fingerprints=["first-pdf", "second-text"],
             decision="rejected", rejection_reason="wrong_arm")])
    rows = search_item([page("Mean age was 49 years.")], item("Mean age"), semantic=semantic, feedback=data)["matches"]
    assert rows and all(r["learning"]["status"] == "sparse_feedback" for r in rows)


def test_foreign_item_feedback_is_ignored_even_if_caller_did_not_prefilter(tmp_path):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    rows = search_item([page("Mean age was 42 years.")], item("Mean age", id="different-item"),
        semantic=semantic, feedback=feedback())["matches"]
    assert rows and all(r["learning"]["status"] == "sparse_feedback" for r in rows)


def test_learning_discovers_unselected_chunks_before_cutoff_and_reuses_cache(tmp_path):
    encoder = DiscoveryEncoder()
    semantic = LocalSemantic(tmp_path, encoder=encoder)
    pages = [page("Three adults ceased participation after discomfort.\nNo special equipment was needed.")]
    spec = item("Tolerability")
    baseline = search_item(pages, spec, semantic=semantic)["matches"]
    assert not any("ceased" in r["quote"] for r in baseline)
    data = feedback([example(1, "Withdrawal was attributed to discomfort."),
                     example(2, "Patients discontinued because of nausea.")])
    rows = search_item(pages, spec, semantic=semantic, feedback=data)["matches"]
    discovered = next(r for r in rows if "ceased" in r["quote"])
    assert "reviewer_examples" in discovered["retrieval_methods"]
    assert discovered["learning"]["feedback_score"] == .15
    assert discovered["scores"]["semantic_score"] == 0.
    assert {i["paper_id"] for i in discovered["learning"]["influences"]} == {"source-1", "source-2"}
    calls = len(encoder.calls)
    search_item(pages, spec, semantic=semantic, feedback=data)
    assert len(encoder.calls) == calls


@pytest.mark.parametrize("name,bad,good", [
    ("Mean age", "The median age was 46 years.", "Participants had a mean age of 43 years."),
    ("Mean age", "Eligible participants were aged between 18 and 70 years.", "Participants were aged 53 ± 8 years."),
    ("Mean age", "Age affects neuroplasticity.", "The average age was 44 years."),
    ("Stimulation frequency", "EMG was sampled at 2000 Hz.", "Stimulation was delivered at 10 Hz."),
    ("Stimulation Intensity", "Rehabilitation intensity was increased.", "Stimulation was delivered at 90% RMT."),
    ("sample_size", "Participants completed 24 treatment sessions.", "Twenty-four participants were randomized."),
    ("SD of LEMS change", "Baseline LEMS mean (SD) was 32.1 (4.2).", "The change in LEMS was 4.6 ± 2.7."),
])
def test_learning_cannot_override_existing_relevance_gates_or_proposals(tmp_path, name, bad, good):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    pages, spec = [page(bad+"\n\n"+good)], item(name)
    before = search_item(pages, spec, semantic=semantic)["matches"]
    after = search_item(pages, spec, semantic=semantic, feedback=feedback([example(1, good), example(2, good)]))["matches"]
    keyed = {r["quote"]: r for r in before}
    for row in after:
        assert row["proposed_value"] == keyed[row["quote"]]["proposed_value"]
        assert row["value_validation"] == keyed[row["quote"]]["value_validation"]
        assert abs(row["learning"]["feedback_score"]) <= .15
        if row["quote"] == bad:
            assert row["learning"]["status"] == "gated" and row["learning"]["feedback_score"] == 0.


@pytest.mark.parametrize("conflict", [
    "Control group at 4 weeks: change in LEMS mean (SD) was 4.6 (2.7).",
    "Active rTMS group at 8 weeks: change in LEMS mean (SD) was 4.6 (2.7).",
    "Active rTMS group at 4 weeks: change in 10MWT mean (SD) was 4.6 (2.7).",
    "Active rTMS group at 4 weeks: change in LEMS mean (SE) was 4.6 (2.7).",
])
def test_wrong_context_still_blocks_proposals_and_positive_feedback(tmp_path, conflict):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    spec = item("SD of LEMS change — intervention group", context={"timepoint": "4 weeks"})
    rows = search_item([page(conflict)], spec, semantic=semantic, feedback=feedback())["matches"]
    assert rows
    assert all(r["proposed_value"] is None and r["learning"]["feedback_score"] == 0. for r in rows)


def test_valid_explicit_statistic_can_supply_type_evidence_without_changing_value(tmp_path):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    text = "The intervention group at 4 weeks had a change in LEMS with standard deviation = 4.1."
    spec = item("SD of LEMS change — intervention group", context={"timepoint": "4 weeks"})
    row = search_item([page(text)], spec, semantic=semantic, feedback=feedback())["matches"][0]
    assert row["scores"]["type_score"] == 0. and row["value_validation"]["valid"]
    assert row["learning"]["status"] == "applied" and row["learning"]["feedback_score"] == .15
    assert row["proposed_value"] == "4.1"


def test_only_irrelevant_rejections_are_negative_and_repeated_examples_do_not_add_weight(tmp_path):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    spec = item_spec(item("Custom evidence"))
    row = dict(quote="A useful candidate.", page_number=1, start=0, rank_score=.4,
               proposed_value=None, scores=dict(negative_score=0.,type_score=0.))
    positives = [example(1), example(2)]
    repeated = positives + [deepcopy(positives[0])] * 5
    ranked = [deepcopy(row)]
    apply_feedback(spec, ranked, semantic, feedback(repeated))
    assert ranked[0]["learning"]["feedback_score"] == .15
    for reason in [None, "wrong_arm", "wrong_timepoint", "wrong_statistic", "wrong_outcome", "duplicate"]:
        ranked = [deepcopy(row)]
        negative = dict(example(3), decision="rejected", rejection_reason=reason)
        apply_feedback(spec, ranked, semantic, feedback(positives+[negative]))
        assert ranked[0]["rank_score"] == .55
        assert len(ranked[0]["learning"]["influences"]) == 2
    ranked = [deepcopy(row)]
    negative = dict(example(3), decision="rejected", rejection_reason="irrelevant")
    apply_feedback(spec, ranked, semantic, feedback(positives+[negative]))
    assert ranked[0]["rank_score"] == .4 and ranked[0]["learning"]["feedback_score"] == 0.
    assert len(ranked[0]["learning"]["influences"]) == 3


def test_unavailable_learning_is_explicit_without_changing_baseline(tmp_path):
    semantic = LocalSemantic(tmp_path, encoder=Encoder())
    spec = item_spec(item("Mean age"))
    row = dict(quote="Mean age was 48 years.", page_number=1, start=0, rank_score=.5,
        scores=dict(negative_score=0., type_score=1.))
    semantic.encoder = None
    semantic.error = "Offline model unavailable"
    warnings = apply_feedback(spec, [row], semantic, feedback())
    assert warnings == ["Offline model unavailable"]
    assert row["rank_score"] == .5 and row["scores"]["feedback_score"] is None
    assert row["learning"]["status"] == "unavailable"


def test_fast_mode_does_not_load_embedding_model_even_with_enough_feedback(tmp_path, monkeypatch):
    semantic = LocalSemantic(tmp_path)
    def fail(): raise AssertionError("Fast mode must not load embeddings")
    monkeypatch.setattr(semantic, "_load", fail)
    rows = search_item([page("Mean age was 41 years.")], item("Mean age", search_mode="fast"),
        semantic=semantic, feedback=feedback())["matches"]
    assert rows and all(r["learning"]["status"] == "fast_mode" for r in rows)


def test_author_profile_does_not_learn_unverified_bylines(tmp_path):
    source = page("A synthetic study\nAlice Example, Bob Sample\nDepartment of Rehabilitation, Example University\nAbstract\nNo names here.")
    rows = search_item([source], item("Authors"), feedback=feedback())["matches"]
    assert rows and all(r["learning"]["status"] == "protected_profile" for r in rows)
