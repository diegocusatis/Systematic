"""Candidate generation → ranking → extraction → validation (never approval)."""
from .chunking import chunk_document, make_chunk
from .extractor import extract_value, validate_value
from .models import item_spec, query_text
from .ranker import rerank_candidates, score_signals
from .deduplication import deduplicate
from .feedback import apply_feedback
from .neural_reranker import rerank_shortlist, shortlist


def retrieve_candidates(item, chunks, semantic=None, *, include_all=False):
    similarities, warning = (None, None)
    if item["search_mode"] == "intelligent":
        if semantic is not None:
            similarities, warning = semantic.similarities(query_text(item), [c.get("semantic_context") or c["normalized_text"] for c in chunks])
        else: warning = "Local embeddings are not configured; using fast retrieval."
    result = []
    for index, chunk in enumerate(chunks):
        row = dict(chunk)
        score = similarities[index] if similarities is not None else None
        row["scores"] = score_signals(item,row,score)
        s = row["scores"]
        if include_all or s["keyword_score"] or s["type_score"] or (score is not None and score >= .30):
            row.setdefault("retrieval_methods", ["hybrid"])
            row.setdefault("matched_rules", [])
            row.setdefault("reasons", [])
            row["search_mode_used"] = "intelligent" if similarities is not None else "fast"
            result.append(row)
    return result, warning


def hybrid_search(pages, item, legacy, semantic=None, chunks=None, feedback=None,
                  reranker=None, diagnostics=False):
    spec = item_spec(item)
    chunks = chunk_document(pages) if chunks is None else chunks
    by_page = {p["page_number"]: p for p in pages}
    # Preserve legacy source spans and explicit regex captures. Prefer structured
    # sentence spans for new matches, without discarding past evidence decisions.
    combined = [dict(c) for c in chunks]
    for old in legacy["matches"]:
        if old.get("source_type") == "pdf_metadata": continue
        overlapping = [c for c in combined if c["page_number"] == old["page_number"] and
            not c.get("table_id") and c["start"] <= old["start"] and c["end"] >= old["end"]]
        row = make_chunk(by_page[old["page_number"]],old["start"],old["end"])
        if overlapping:
            row.update({k:v for k,v in overlapping[0].items() if k not in {"start","end","quote","original_text","normalized_text","boxes","chunk_id"}})
        row.update(old)
        if overlapping: row["sentence_context"] = overlapping[0]["context"]
        # Equal spans represent one passage; table cells retain their own context.
        combined = [c for c in combined if c.get("table_id") or (c["page_number"],c["start"],c["end"]) != (row["page_number"],row["start"],row["end"])]
        combined.append(row)
    # Every exact candidate text gets the same scoring path. The per-text cache
    # reuses document vectors across items, including newly encountered excerpts.
    if spec["search_mode"] == "intelligent" and hasattr(semantic, "reuse_document_cache"):
        semantic.reuse_document_cache([c.get("semantic_context") or c["normalized_text"] for c in chunks])
    retrieved, warning = retrieve_candidates(spec,combined,semantic,include_all=True)
    signals = {r["chunk_id"]: r["scores"] for r in retrieved}
    candidates = []
    # Reuse scores already computed for each passage. Preserve the previous
    # chunk-id lookup semantics if callers supply duplicate identities.
    for row, scored in zip(combined, retrieved):
        shared = signals[row["chunk_id"]]
        if not row.get("retrieval_methods"):
            row["scores"] = dict(shared)
        elif scored["scores"]["semantic_score"] == shared["semantic_score"]:
            row["scores"] = dict(scored["scores"])
        else:
            row["scores"] = score_signals(spec, row, shared["semantic_score"])
        scores = row["scores"]
        row["semantic_basis"] = dict(chunk_id=row["chunk_id"], text=row.get("semantic_context") or row["normalized_text"],
                                     available=scores["semantic_score"] is not None)
        # Retain unselected chunks until feedback has compared the whole source.
        # A relevant reviewer example can discover vocabulary absent from the
        # item's initial aliases, without changing baseline selection otherwise.
        row["_baseline_selected"] = bool(scores["keyword_score"] or scores["type_score"] or
            (scores["semantic_score"] or 0) >= .30 or row.get("retrieval_methods"))
        row.setdefault("retrieval_methods", ["hybrid"])
        row.setdefault("matched_rules", [])
        row.setdefault("reasons", [])
        row["search_mode_used"] = "fast" if warning or spec["search_mode"] == "fast" else "intelligent"
        if warning: row["retrieval_warning"] = warning
        extracted = extract_value(spec,row)
        validation = validate_value(spec,extracted)
        row.update(extracted_value=extracted, value_validation=validation)
        # A specialized quantitative extractor takes precedence over naked regex
        # captures. Generic user-defined captures remain reviewable as before.
        if spec["category"] != "general" or spec["expected_type"] in {"number_unit","statistic","trial_id"}:
            row["proposed_value"] = extracted["value"] if validation["valid"] else None
            row["multiple_proposed_values"] = len(extracted["detected_values"]) > 1
        else: row.setdefault("proposed_value", None)
        candidates.append(row)
    ranked = rerank_candidates(spec,candidates)
    feedback_warnings = apply_feedback(spec,ranked,semantic,feedback)
    expanded = set()
    if reranker is not None and reranker.enabled and reranker._load() is not None:
        expanded = {row["chunk_id"] for row in shortlist(ranked)}
    selected = []
    baseline_pool = []
    for row in ranked:
        was_selected = row.pop("_baseline_selected")
        learned = (row["scores"]["feedback_score"] or 0) >= .02
        if was_selected or learned:
            baseline_pool.append(row)
        if was_selected or learned or row["chunk_id"] in expanded:
            if learned and not was_selected: row["retrieval_methods"].append("reviewer_examples")
            if not was_selected and not learned: row["retrieval_methods"].append("neural_pool")
            selected.append(row)
    unique = deduplicate(selected)
    considered, neural_warning = rerank_shortlist(spec, unique, reranker)
    result = dict(matches=unique[:80], errors=legacy["errors"],
                  warnings=([warning] if warning else []) + feedback_warnings +
                           ([neural_warning] if neural_warning else []))
    if diagnostics:
        def locations(rows):
            return [{key: row.get(key) for key in
                     ("chunk_id", "page_number", "start", "end", "quote", "boxes", "table_id")}
                    for row in rows]
        result["diagnostics"] = dict(chunk_inventory=len(combined),
            baseline_pool=locations(baseline_pool), candidate_pool=locations(selected),
            neural_shortlist=considered, final=locations(result["matches"]))
    return result
