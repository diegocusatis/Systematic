"""Collapse overlapping views of one finding, never distinct locations/cells."""
import re
from .chunking import normalize
from .extractor import contexts


def same_finding(a, b):
    if a["page_number"] != b["page_number"]: return False
    if a.get("table_id") or b.get("table_id"):
        return (a.get("table_id"), a.get("table_row"), a.get("table_column")) == (b.get("table_id"), b.get("table_row"), b.get("table_column"))
    overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
    if overlap <= 0 or overlap / max(1, min(a["end"]-a["start"], b["end"]-b["start"])) < .8: return False
    texts = [normalize(r["quote"]).lower() for r in (a,b)]
    # Extra measurements or context labels indicate potentially distinct findings.
    ca, cb = [contexts(t) for t in texts]
    if all(r.get("scores", {}).get("evidence_kind") == "age_discussion" for r in (a,b)):
        return texts[0] in texts[1] or texts[1] in texts[0]
    age_pattern = r"\bage[ds]?\s*(?:was|of|[=:])?\s*(\d+(?:\.\d+)?(?:\s*[±�–-]\s*\d+(?:\.\d+)?)?\s*years)"
    ages = [re.findall(age_pattern, t) for t in texts]
    if ages[0] and ages[0] == ages[1] and all(r.get("scores", {}).get("evidence_kind", "").startswith(("age_", "mean_age", "median_age", "eligibility_age")) for r in (a,b)):
        # Other numbers in a broader PDF span can be study size, citations or
        # interleaved timing. Compare age findings, while retaining arm identity.
        return not (ca["group"] and cb["group"] and ca["group"] != cb["group"]) and max(len(ca["group"]),len(cb["group"])) <= 1
    numbers = [re.findall(r"\d+(?:\.\d+)?", t) for t in texts]
    if numbers[0] != numbers[1]: return False
    if any(ca[k] and cb[k] and ca[k] != cb[k] for k in ca): return False
    if any(len(c[k]) > 1 for c in (ca,cb) for k in c): return False
    return True


def deduplicate(ranked):
    kept = []
    for row in ranked:
        representative = next((other for other in kept if same_finding(row, other)), None)
        if representative is None:
            kept.append(row)
        else:
            representative.setdefault("duplicate_spans", []).append({k: row.get(k) for k in
                ("chunk_id", "page_number", "start", "end", "quote", "rank_score", "scores", "retrieval_methods")})
            for field in ("retrieval_methods", "reasons", "matched_rules"):
                representative[field] = list(dict.fromkeys(representative.get(field, []) + row.get(field, [])))
    for rank, row in enumerate(kept, 1): row["rank"] = rank
    return kept
