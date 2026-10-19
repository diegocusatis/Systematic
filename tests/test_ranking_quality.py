"""Answerability, scoring parity, diversity, source preservation and cache tests."""
from copy import deepcopy

import pytest
from reportlab.pdfgen import canvas

from review_extractor.documents import process_pdf
from review_extractor.hybrid.chunking import chunk_document, make_chunk
from review_extractor.hybrid.deduplication import same_finding
from review_extractor.hybrid.extractor import extract_value
from review_extractor.hybrid.models import item_spec
from review_extractor.hybrid.ranker import rerank_candidates, score_signals
from review_extractor.hybrid.semantic import LocalSemantic
from review_extractor.retrieval import search_item
from review_extractor.service import ReviewService
from ranking_quality_fixture import AGE_DISTRACTORS, evaluate
from test_hybrid import Encoder
from test_retrieval import item, page


def test_fixture_retrieval_metrics():
    metrics = evaluate(search_item, page, lambda name: item(name, search_mode="fast"))
    assert metrics["mrr"] == 1, metrics
    assert metrics["recall_at_5"] == 1, metrics
    assert metrics["duplicate_findings_at_5"] == 0, metrics


@pytest.mark.parametrize("text,kind", [
    ("Recruited adults were aged 43.7 ± 9.4 years.", "age_summary"),
    ("The mean age was 42 years.", "mean_age"),
    ("Median age was 47 years.", "median_age"),
    ("Eligible participants were aged 18–70 years.", "eligibility_age"),
    ("Participants were aged 21–68 years.", "age_range"),
    ("Age affected response to therapy.", "age_discussion"),
    ("The mean time since injury was 5 years.", "unrelated"),
    ("Averaging was performed across trials.", "unrelated"),
])
def test_age_evidence_type_does_not_assign_unreported_statistic(text, kind):
    row = search_item([page(text)], item("Mean age", search_mode="fast"))["matches"]
    if row:
        assert row[0]["scores"]["evidence_kind"] == kind
        assert row[0]["proposed_value"] is None
        if kind == "age_summary": assert row[0]["extracted_value"]["statistic"] is None


def test_candidate_source_parity_and_missing_does_not_raise_score():
    spec = item_spec(item("Mean age"))
    chunk = make_chunk(page("The mean age was 41 years."), 0, 25)
    legacy = dict(chunk, retrieval_methods=["concept_passage", "literal_or_rule"])
    assert score_signals(spec, chunk, .2) == score_signals(spec, legacy, .2)
    rows = []
    for semantic in (None, 0., .2):
        row = dict(chunk, scores=score_signals(spec, chunk, semantic))
        rows.append(rerank_candidates(spec, [row])[0])
    assert rows[0]["rank_score"] == rows[1]["rank_score"] < rows[2]["rank_score"]
    assert rows[0]["scores"]["semantic_score"] is None
    assert rows[1]["scores"]["semantic_score"] == 0
    assert rows[0]["missing_signals"] == ["semantic"]
    # Unmeasured OCR likewise cannot increase any other component's weight.
    absent = dict(chunk, ocr=True)
    measured = dict(absent, ocr_confidence=0.)
    a, b = [rerank_candidates(spec,[dict(r,scores=score_signals(spec,r,.2))])[0] for r in (absent,measured)]
    assert a["rank_score"] == b["rank_score"]
    assert a["scores"]["ocr_score"] is None and b["scores"]["ocr_score"] == 0


def test_mixed_candidates_all_get_embeddings_and_warm_cache(tmp_path):
    source = page("Participants were enrolled and\naged 44.3 ± 8.1 years.\n\n" + "\n\n".join(AGE_DISTRACTORS))
    encoder = Encoder()
    semantic = LocalSemantic(tmp_path, encoder=encoder)
    query = item("Mean age", keywords_unused=True)
    query["keywords"] = ["age", "aged"]
    rows = search_item([source], query, semantic=semantic)["matches"]
    assert all(r["scores"]["semantic_score"] is not None for r in rows)
    assert len(encoder.calls) == 2 and encoder.calls[0][1]["batch_size"] == 32
    assert all(r["semantic_basis"]["text"] == r["normalized_text"] for r in rows)
    calls = deepcopy(encoder.calls)
    search_item([source], query, semantic=semantic)
    assert encoder.calls == calls
    restarted = LocalSemantic(tmp_path, encoder=encoder)
    search_item([source], query, semantic=restarted)
    assert encoder.calls == calls
    # A new custom span only encodes previously unseen text; old document texts
    # cannot be present in a later batch.
    changed = dict(query, rules=[dict(kind="regex", pattern="enrolled and\\s+aged")])
    search_item([source], changed, semantic=restarted)
    old_texts = {t for texts,_ in calls for t in texts}
    assert all(t not in old_texts for texts,_ in encoder.calls[len(calls):] for t in texts)


def test_previous_document_batch_cache_is_reused(tmp_path):
    import hashlib
    import json
    import sqlite3
    from review_extractor.hybrid.chunking import VERSION
    encoder = Encoder()
    semantic = LocalSemantic(tmp_path,encoder=encoder)
    texts = ["First document sentence.", "Second document sentence."]
    key = hashlib.sha256(json.dumps([VERSION,semantic.model_name,semantic.revision,texts],ensure_ascii=False).encode()).hexdigest()
    with sqlite3.connect(tmp_path/'embeddings.sqlite3') as db:
        db.execute('CREATE TABLE embeddings (key TEXT PRIMARY KEY, vectors TEXT NOT NULL)')
        db.execute('INSERT INTO embeddings VALUES (?,?)',(key,json.dumps([[1.,0.],[1.,0.]])))
    semantic.reuse_document_cache(texts)
    semantic.similarities('New item',texts)
    assert len(encoder.calls)==1 and encoder.calls[0][0]==['New item']


def test_median_field_prefers_median_and_decision_metadata_cannot_boost():
    source = page("The mean age was 42 years.\n\nThe median age was 47 years.")
    query = item("Median age",search_mode="fast")
    rows = search_item([source],query)["matches"]
    assert 'median' in rows[0]['quote']
    spec = item_spec(query)
    a = deepcopy(rows[0]); b = dict(a, decision='accepted', id='arbitrary-private-id')
    assert score_signals(spec,a,.3)==score_signals(spec,b,.3)


def test_overlap_diversity_retains_distinct_arms_and_cells():
    source = page("The participants were recruited and\naged 44.3 ± 8.1 years.\n\nControl group mean age was 52 years.\n\nActive group mean age was 47 years.")
    rows = search_item([source], item("Mean age", search_mode="fast"))["matches"]
    assert sum("44.3" in r["quote"] for r in rows[:5]) == 1
    assert any("52 years" in r["quote"] for r in rows[:5])
    assert any("47 years" in r["quote"] for r in rows[:5])
    assert any(r.get("duplicate_spans") for r in rows)
    cell = dict(rows[0], table_id="t", table_row=1, table_column=1)
    assert not same_finding(cell, dict(cell, table_column=2))
    assert not same_finding(cell, dict(cell, table_row=2))


def two_column_pdf(path):
    pdf = canvas.Canvas(str(path), pagesize=(612, 792))
    pdf.setFont("Helvetica", 9)
    left = ["Participants were recruited for the trial.", "The enrolled adults were aged 44.3", "plus or minus 8.1 years at enrollment."]
    left += ["The left column continues with clinical details."] * 8
    right = ["Signals were averaged for further analysis."] * 11
    for n,(a,b) in enumerate(zip(left,right)):
        pdf.drawString(45, 720-n*14, a)
        pdf.drawString(320, 720-n*14, b)
    pdf.save()


def test_two_column_pdf_order_chunks_and_highlights(tmp_path):
    path = tmp_path/"columns.pdf"
    two_column_pdf(path)
    source = process_pdf(path)[0]
    assert source["reading_order"]["mode"] == "two_columns"
    assert source["text"].index("left column continues") < source["text"].index("Signals were averaged")
    chunks = chunk_document([source])
    age = next(r for r in chunks if "44.3" in r["quote"])
    assert "8.1" in age["quote"] and "Signals" not in age["quote"]
    assert age["quote"] == source["text"][age["start"]:age["end"]]
    assert all(b["x1"] < 310 for b in age["boxes"])
    for w in source["words"]: assert source["text"][w["start"]:w["end"]] == w["text"]


def test_refresh_and_source_changes_preserve_approved_values_and_history(tmp_path):
    service = ReviewService(tmp_path)
    r = service.create_review({"name":"Synthetic"})["id"]
    i = service.add_item(r, item("Mean age", search_mode="fast"))["id"]
    p = service.add_paper(r, {"title":"Synthetic", "processing_status":"ready"})["id"]
    service.set_pages(r,p,[page("The recruited adults were\naged 44.3 ± 8.1 years.")])
    service.refresh_search(r,p)
    s = service.get_suggestions(r,p,i)[0]
    accepted = service.decide_suggestion(r,p,s["id"],{"decision":"accepted"})
    approved = service.save_field(r,p,i,{"status":"approved", "value":"44.3", "evidence_ids":[accepted["evidence_id"]]})
    before = deepcopy(service.snapshot(r))
    service.refresh_search(r,p)
    after = service.snapshot(r)
    for key in ("fields","evidence","feedback","history"): assert after[key] == before[key]
    old_pages = service.get_pages(r,p)
    service.set_pages(r,p,[page("The recruited adults were aged 44.3 ± 8.1 years.")])
    service.refresh_search(r,p)
    assert service.get_field(r,p,i)["value"] == approved["value"]
    assert service.get_field(r,p,i)["needs_reconsideration"]
    historical = next(row for row in service.get_suggestions(r,p,i) if row["id"] == s["id"])
    assert historical["decision"] == "accepted" and not historical["active"]
    revision = next(h for h in service.snapshot(r)["history"] if h["action"] == "document_source_changed")
    assert revision["before"]["pages"] == old_pages
    service.store.close()


@pytest.mark.parametrize("name,text,context", [
    ("Stimulation frequency", "Control group stimulation used 10 Hz.", {"group":"intervention"}),
    ("Stimulation intensity", "At 8 weeks stimulation used 90% RMT.", {"timepoint":"4 weeks"}),
    ("Sample size", "Control group included n = 24.", {"group":"intervention"}),
    ("SD of LEMS change", "Active group at 4 weeks: change in LEMS SE = 2.1.", {"group":"intervention","timepoint":"4 weeks"}),
])
def test_context_mismatches_block_specialized_proposals(name,text,context):
    extracted = extract_value(item_spec(item(name,context=context)), dict(quote=text,page_number=1))
    assert extracted["value"] is None and extracted["issues"]


def test_long_table_rows_are_not_misread_as_prose_columns(tmp_path):
    path=tmp_path/'table-columns.pdf'
    pdf=canvas.Canvas(str(path),pagesize=(612,792))
    pdf.setFont('Helvetica',9)
    for x in (40,305,570): pdf.line(x,510,x,710)
    for n in range(11): pdf.line(40,510+n*20,570,510+n*20)
    for n in range(10):
        pdf.drawString(45,695-n*20,f'Treatment arm observation number {n}')
        pdf.drawString(315,695-n*20,f'Comparator arm observation number {n}')
    pdf.save()
    source=process_pdf(path)[0]
    assert source['reading_order']['mode']=='native_table'
    assert len(source['tables'][0]['cells'])==20
    for cell in source['tables'][0]['cells']:
        assert source['text'][cell['start']:cell['end']]==cell['text']


def test_suppressed_legacy_decision_keeps_its_reference(tmp_path):
    service=ReviewService(tmp_path)
    r=service.create_review({'name':'Synthetic'})['id']
    i=service.add_item(r,item('Mean age',search_mode='fast'))['id']
    p=service.add_paper(r,{'title':'Synthetic','processing_status':'ready'})['id']
    service.set_pages(r,p,[page('Recruited adults were\naged 44.3 ± 8.1 years.')])
    service.refresh_search(r,p)
    representative=service.get_suggestions(r,p,i)[0]
    alternate=representative['duplicate_spans'][0]
    legacy=dict(representative,**{k:alternate[k] for k in ('chunk_id','start','end','quote')})
    legacy.update(id='legacy-reference',rank_score=.99)
    service.store.put('suggestions',legacy)
    accepted=service.decide_suggestion(r,p,legacy['id'],{'decision':'accepted'})
    service.save_field(r,p,i,{'status':'approved','value':'44.3','evidence_ids':[accepted['evidence_id']]})
    before=deepcopy(service.snapshot(r))
    service.refresh_search(r,p)
    suggestions=service.get_suggestions(r,p,i)
    assert suggestions[0]['active']
    historical=next(s for s in suggestions if s['id']=='legacy-reference')
    assert not historical['active'] and historical['decision']=='accepted'
    after=service.snapshot(r)
    for key in ('evidence','fields','feedback','history'): assert before[key]==after[key]
    service.store.close()
