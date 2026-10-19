"""Versioned review feedback state, distinct from evidence and value approval."""
import hashlib
import json
import re
import unicodedata

REJECTION_REASONS = {"", "irrelevant", "wrong_arm", "wrong_outcome", "wrong_timepoint", "wrong_statistic", "duplicate"}
MEANING_FIELDS = ("name", "display_name", "export_heading", "definition", "description", "instructions",
                  "data_type", "units", "allowed_values", "context", "expected_type", "expected_units")


def config_for(review):
    return dict({"enabled": False, "version": 1, "epoch": 0, "revision": 0, "excluded_examples": []},
                **review.get("learning", {}))


def item_signature(item):
    data = {key: item.get(key) for key in MEANING_FIELDS}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def document_fingerprints(paper, pages):
    result = []
    if paper.get("sha256"): result.append("pdf:" + paper["sha256"])
    text = "\n".join(p.get("text", "") for p in sorted(pages, key=lambda p: p["page_number"]))
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()
    if text: result.append("text:" + hashlib.sha256(text.encode()).hexdigest())
    return result or ["paper:" + paper["id"]]


def example_id(record, evidence=False):
    return "s:" + record["suggestion_id"] if evidence and record.get("suggestion_id") else ("e:" if evidence else "s:") + record["id"]


def latest_events(events):
    result = {}
    for event in sorted(events, key=lambda e: (e.get("sequence", 0), e.get("created_at", ""), e["id"])):
        key = event.get("example_id") or "s:" + event.get("suggestion_id", "")
        result[key] = event
    return result


def effective_examples(review, items, papers, pages, suggestions, evidence, events):
    config = config_for(review)
    items = {row["id"]: row for row in items}
    papers = {row["id"]: row for row in papers}
    sources = {"s:"+row["id"]: row for row in suggestions}
    sources.update({"e:"+row["id"]: row for row in evidence if not row.get("suggestion_id")})
    linked = {"s:"+row["suggestion_id"]: row for row in evidence if row.get("suggestion_id")}
    pages_by_paper = {}
    for page in pages: pages_by_paper.setdefault(page["paper_id"], []).append(page)
    fingerprints = {key: document_fingerprints(p, pages_by_paper.get(key, [])) for key,p in papers.items()}
    results = []
    for key, event in latest_events(events).items():
        source = sources.get(key)
        item, paper = items.get(event["item_id"]), papers.get(event["paper_id"])
        label = event.get("decision", "accepted" if event.get("accepted") else "rejected")
        reason = ""
        if not source or not item or not paper or item.get("deleted") or paper.get("removed"):
            reason = "Source or item is no longer available"
        elif source.get("decision") != label or label == "pending":
            reason = "Decision is pending or superseded"
        elif event.get("epoch", 0) != config["epoch"]:
            reason = "Learning was reset; explicitly reconsider this example to use it again"
        elif key in config["excluded_examples"]:
            reason = "Excluded by reviewer"
        elif event.get("item_signature", item_signature(event.get("item", {}))) != item_signature(item) or event.get("item_meaning_version", 0) != item.get("learning_version", 0):
            reason = "Item meaning changed; confirm the evidence again"
        elif source.get("source_type") == "pdf_metadata" or source.get("manual") or source.get("start") is None:
            reason = "No verified searchable source span"
        elif source.get("source_stale") or linked.get(key, {}).get("source_stale") or source.get("document_version", 0) != paper.get("document_version", 0) or event.get("candidate", {}).get("document_version", 0) != paper.get("document_version", 0):
            reason = "Source version changed; select evidence from the current document"
        elif event.get("candidate_text") != source.get("quote"):
            reason = "Source quotation changed"
        else:
            page = next((p for p in pages_by_paper.get(paper["id"], []) if p["page_number"] == source["page_number"]), None)
            if not page or page.get("status") != "ok" or page.get("text", "")[source["start"]:source["end"]] != source["quote"]:
                reason = "Source quotation cannot be verified"
        results.append(dict(example_id=key, feedback_id=event["id"], review_id=review["id"], paper_id=event["paper_id"],
            item_id=event["item_id"], quote=event["candidate_text"], decision=label,
            rejection_reason=event.get("rejection_reason", ""), eligible=not reason,
            excluded_reason=reason, excluded=key in config["excluded_examples"],
            page_number=(source or {}).get("page_number"), document_fingerprints=fingerprints.get(event["paper_id"], []),
            origin=(source or {}).get("origin", "algorithm")))
    return results
