"""Independent retrieval signals and an inspectable normalized heuristic score."""
from .chunking import normalize
from .extractor import contexts, context_matches, requirements, type_score
from .lexical import contains, lexical_scores
from .models import WEIGHTS
from .relevance import evidence_relevance


def score_signals(item, row, semantic=None):
    text = normalize(row.get("semantic_context") or row["quote"])
    exact, fuzzy = lexical_scores(text, item["aliases"])
    # Legacy concept vocabulary is useful, but is not a semantic model score.
    # Recompute concept support from text, never from candidate provenance.
    from ..retrieval import query_groups, tokens
    query, groups = query_groups(item)
    words = tokens(text)
    concept = .55 if any(any(p <= words for p in group) for group in groups) or len(query & words) >= max(1, min(2, len(query))) else 0.
    keyword = max(exact, .3*fuzzy, concept)
    negative = float(any(contains(text, term) for term in item["negative_examples"]))
    relevance = evidence_relevance(item, text)
    negative = max(negative, relevance["penalty"])
    observed = contexts(text)
    required = requirements(item)
    matches = [context_matches(k,v,text,observed[k]) for k,v in required.items()]
    if matches and not all(matches): negative = max(negative, 1-sum(matches)/len(matches))
    if "change" in item.get("name", "").lower() and item["category"] == "statistic" and not contains(text,"change"):
        negative = max(negative, .8)
    nearby, _ = lexical_scores(row.get("context", ""), item["aliases"])
    context = sum(matches)/len(matches) if matches else nearby
    section = float(any(contains(row.get("section") or "", preferred) for preferred in item["preferred_sections"]))
    ocr = row.get("ocr_confidence")
    if ocr is not None: ocr = max(0.,min(1.,float(ocr)))
    elif not row.get("ocr"): ocr = .8
    return dict(semantic_score=semantic, keyword_score=keyword, fuzzy_score=fuzzy,
        type_score=type_score(item,text), section_score=section, context_score=context,
        ocr_score=ocr, negative_score=negative, evidence_kind=relevance["kind"],
        relevance_reason=relevance["reason"])


def rerank_candidates(item, candidates, weights=None):
    weights = dict(WEIGHTS if weights is None else weights)
    for row in candidates:
        scores = row["scores"]
        available = {k:w for k,w in weights.items() if scores[k+"_score"] is not None}
        # Missing is still None in diagnostics; it contributes no evidence and
        # never increases another component's weight, including in fast mode.
        denominator = sum(weights.values()) or 1.
        score = sum(scores[k+"_score"]*w for k,w in available.items()) / denominator
        score = max(0., min(1., score - item["negative_penalty"]*scores["negative_score"]))
        scores["combined_score"] = round(score,6)
        row["rank_score"] = scores["combined_score"]
        row["ranking_weights"] = weights
        row["missing_signals"] = [k for k in weights if scores[k+"_score"] is None]
    candidates.sort(key=lambda r:(-r["rank_score"],r["page_number"] or 0,r["start"] or 0))
    for rank,row in enumerate(candidates,1): row["rank"] = rank
    return candidates
