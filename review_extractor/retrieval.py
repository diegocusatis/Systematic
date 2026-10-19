"""Local hybrid retrieval: item intent, source patterns, and ranked passages.

This is a transparent heuristic retriever, not an embedding model or a factual
extractor. Scores order candidates; they are never probabilities of correctness.
"""
from collections import Counter
from functools import lru_cache
import math
import re

import regex

from .keywords import CONCEPTS, normalized
from .search import boxes_for_span, passage_span, search_pages

ENGINE_VERSION = "hybrid-5-neural-candidates"
STOP = set("a an the and or of for in on at to with from by as is are was were be been it its this that these those into per item value values extract extraction report reported information record identify find include including use using please determine study paper article participant participants patient patients subject subjects".split())
RELATED = [
    ("duration", "length", "period"), ("age", "aged", "years old"),
    ("mean", "average", "averaged", "averaging"),
    ("withdrawal", "withdrawn", "dropout", "attrition", "discontinued"),
    ("adverse", "side effects", "tolerability", "complications"),
    ("blinding", "blind", "masked", "masking"),
    ("randomized", "randomised", "randomization", "randomisation", "randomly assigned"),
    ("gait", "walking", "ambulation", "locomotion"),
    ("speed", "velocity", "pace"), ("strength", "force", "power"),
    ("fatigue", "tiredness", "exhaustion"), ("pain", "discomfort", "soreness"),
    ("balance", "postural stability", "equilibrium"),
    ("improvement", "increase", "gain", "recovery"),
    ("follow up", "followup", "subsequent assessment", "retention"),
    ("dose", "dosage", "administered"), ("frequency", "hertz", "hz"),
    ("rehabilitation", "physiotherapy", "physical therapy"),
    ("sex", "gender", "male", "female", "men", "women"),
    ("eligibility", "inclusion", "eligible", "enrollment"),
    ("comparator", "sham", "placebo", "control"),
]


def stem(word):
    # Conservative inflections; no substring matching (age must not match dosage).
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "is")):
        return word[:-1]
    return word


def tokens(text):
    return {stem(word) for word in normalized(text).split() if len(word) > 1 and word not in STOP and not word.isdigit()}


def item_text(item):
    return "\n".join([item.get(key, "") for key in ("name", "export_heading", "definition", "instructions")] + item.get("keywords", []))


def has(text, pattern):
    return bool(regex.search(pattern, text, regex.IGNORECASE))


def intent(item):
    # Headings take precedence: 'age of authors' in a definition is not a byline
    # request when the actual item is Mean age.
    heading = " ".join(item.get(key, "") for key in ("name", "export_heading"))
    source = heading if has(heading, r"\b(author[s]?|byline|citation|age|frequency|hertz|hz|burst)\b") else item_text(item)
    if has(source, r"\b(author[s]?|byline|citation)\b"):
        return "authors"
    if has(source, r"\bage\b"):
        return "age"
    if has(source, r"\b(frequency|hertz|hz|burst)\b"):
        return "frequency"
    return "general"


def candidate(page, start, end, reason, score, kind, value=None):
    source = page["text"]
    return {"page_number": page["page_number"], "start": start, "end": end,
            "quote": source[start:end], "context": source[max(0, start - 350):min(len(source), end + 350)],
            "boxes": boxes_for_span(page, start, end),
            "ocr": bool(page.get("ocr") or any(
                region["start"] <= start and end <= region["end"]
                for region in page.get("ocr_blocks", []))),
            "source_type": "pdf_text", "matched_rules": [], "reasons": [reason],
            "retrieval_methods": [kind], "rank_score": score, "proposed_value": value}


def lines(text):
    return [(match.start(), match.end(), match.group()) for match in re.finditer(r"[^\n]+", text)]


def body_limit(text):
    boundary = regex.search(r"(?im)^\s*(?:abstract\b|summary\b|background\b|introduction\b|keywords\b|references\b|bibliography\b|acknowledg(?:e)?ments?\b)", text)
    return boundary.start() if boundary else len(text)


NAME_WORD = r"(?:\p{Lu}[\p{L}'’\-]+|\p{Lu}\.)"
PERSON = regex.compile(rf"(?<!\p{{L}}){NAME_WORD}(?:\s+(?:(?:de|del|van|von|da|di)\s+)?{NAME_WORD}){{1,4}}(?!\p{{L}})")
NON_NAMES = regex.compile(r"(?i)\b(university|department|institute|hospital|college|school|center|centre|laboratory|journal|press|publisher|copyright|correspondence|corresponding|email|received|accepted|published|editor|acknowledgments?|references|funding|supported|thanks?|study|trial|recovery|effect|effects|stimulation|treatment|patients|participants|clinical|research|analysis|randomized|randomised|controlled|rehabilitation|spinal|injury|magnetic|microsoft|acrobat|adobe|unknown|anonymous)\b")


def person_line(text):
    """Conservative Latin-script byline heuristic; preserve spelling and order."""
    if len(text) > 350 or NON_NAMES.search(text) or has(text, r"https?://|@|\bet al\b|\b(?:19|20)\d{2}\b"):
        return []
    text = regex.sub(r"(?i)\b(?:(?:senior\s+)?member|fellow)\s*,?\s*IEEE\b", "", text)
    names = list(PERSON.finditer(text))
    if not names:
        return []
    residue = PERSON.sub(" ", text)
    residue = regex.sub(r"(?i)\b(?:and|MD|PhD|MSc|BSc|PT|DPT|Dr|Prof|authors?)\b|[\d\W_]+", "", residue)
    if residue:
        return []
    return [match.group().strip() for match in names]


def author_candidates(pages, metadata):
    results = []
    author = metadata.get("Author", "") if isinstance(metadata, dict) else ""
    if isinstance(author, str) and person_line(author):
        results.append({"page_number": None, "start": None, "end": None, "quote": author,
                        "context": "PDF document metadata: Author. This may be stale or describe the file creator. Verify against the visible byline.",
                        "boxes": [], "ocr": False, "source_type": "pdf_metadata", "metadata_key": "Author",
                        "matched_rules": [], "reasons": ["Name-like entries in the PDF Author metadata; not visible page evidence."],
                        "retrieval_methods": ["author_metadata"], "rank_score": 3.0, "proposed_value": None})
    page = next((page for page in pages if page["page_number"] == 1 and page.get("status") == "ok"), None)
    if page is None:
        return results
    source = page["text"]
    limit = min(body_limit(source), 6000)
    rows = lines(source[:limit])
    for index, (start, end, line) in enumerate(rows):
        names = person_line(line)
        if not names:
            continue
        boxes = boxes_for_span(page, start, end)
        if boxes and min(box["top"] for box in boxes) > page.get("height", 1000) * 0.6:
            continue
        nearby = source[end:min(limit, end + 500)]
        affiliation = has(nearby, r"\b(department|university|institute|hospital|college|laboratory)\b")
        metadata_match = bool(author and any(normalized(name) in normalized(author) for name in names))
        near_body = body_limit(source) < len(source) and 0 <= limit - end < 400
        sizes = [word["font_size"] for word in page.get("words", []) if start <= word["start"] < end and word.get("font_size")]
        previous_start = rows[index - 1][0] if index else start
        previous_sizes = [word["font_size"] for word in page.get("words", []) if previous_start <= word["start"] < start and word.get("font_size")]
        below_title = bool(sizes and previous_sizes and max(sizes) < max(previous_sizes))
        name_list = len(names) >= 2 and has(line, r"[,;]|\band\b")
        # Require front-matter structure, not merely capitalization. A lone name
        # without affiliation/metadata is omitted rather than guessed.
        if not (affiliation or metadata_match or (near_body and name_list)) or not (metadata_match or name_list or below_title) or index == 0:
            continue
        reason = "Name-like first-page byline before the article body"
        reason += ", followed by affiliation information." if affiliation else ", immediately before the abstract/body." if near_body else ", corroborated by PDF Author metadata."
        if metadata_match:
            reason += " The name also appears in PDF metadata."
        new = candidate(page, start, end, reason, 9.0 if metadata_match else 8.0, "author_byline", "; ".join(names))
        previous = results[-1] if results else None
        if previous and previous["source_type"] == "pdf_text" and not source[previous["end"]:start].strip():
            new = candidate(page, previous["start"], end, reason, max(previous["rank_score"], new["rank_score"]),
                            "author_byline", previous["proposed_value"] + "; " + new["proposed_value"])
            results[-1] = new
        else:
            results.append(new)
    return results


def numeric_candidates(pages, category):
    results = []
    if category == "age":
        patterns = [
            r"\b(?:mean\s+|median\s+|average\s+)?age(?:d|s)?\b[^.!?\n]{0,40}(?:\n)?[^.!?\n]{0,20}?\b\d{1,3}(?:\.\d+)?\b(?:\s*(?:±|\+/-|to|[-–])\s*\d{1,3}(?:\.\d+)?)?(?:\s*(?:years?|yrs?))?",
            r"\b(?:participants|patients|subjects|sample|cohort|group|men|women|adults)\b[^.!?\n]{0,50}?\b(?:averaged|averaging|were|was)\s+(?:about\s+)?\d{1,3}(?:\.\d+)?\s*(?:years?|yrs?)\b",
        ]
        reason = "Participant-age wording with a numeric value; verify the group and whether this is a mean, median, or range."
    else:
        patterns = [r"\b\d+(?:\.\d+)?\s*(?:k?Hz|hertz)\b"]
        reason = "A reported frequency value and unit; inspect the surrounding protocol and distinguish burst, pulse, and measurement frequencies."
    for page in pages:
        if page.get("status") != "ok":
            continue
        for pattern in patterns:
            for match in regex.finditer(pattern, page["text"], regex.IGNORECASE):
                start, end = passage_span(page["text"], match.start(), match.end())
                quote = page["text"][start:end]
                if category == "frequency" and has(quote, r"\b(sampling|sampled|filter|bandpass|band-pass|oscillation|spectral)\b") and not has(quote, r"\b(stimulation|stimulated|rTMS|iTBS|pulses|delivered)\b"):
                    continue
                if category == "age" and has(quote, r"\b(of (?:the )?(?:device|equipment)|since injury|after injury|follow.up|disease duration)\b") and not has(quote, r"\bage\b"):
                    continue
                # A frequency with two values remains ambiguous. Age candidates
                # deliberately do not guess which table cell is the final value.
                value = match.group() if category == "frequency" else None
                results.append(candidate(page, start, end, reason, 7.0, f"{category}_pattern", value))
    return results


def query_groups(item):
    query, groups = _query_groups(item_text(item))
    return set(query), [[set(phrase) for phrase in family] for family in groups]


@lru_cache(maxsize=256)
def _query_groups(text):
    query = tokens(text)
    groups = []
    vocab = RELATED + [tuple(aliases) + tuple(terms) for aliases, terms in CONCEPTS]
    # Activate a group on a whole phrase, not isolated words inside a phrase.
    context = f" {normalized(text)} "
    for family in vocab:
        if any(f" {normalized(phrase)} " in context for phrase in family):
            alternatives = [tokens(phrase) for phrase in family if tokens(phrase)]
            groups.append(alternatives)
    return frozenset(query), tuple(tuple(frozenset(p) for p in family) for family in groups)


def passage_candidates(pages, item):
    """Rank arbitrary items by term coverage, shared concepts, and rarity.

    No neural semantics are claimed: unfamiliar concepts need descriptive item
    definitions or keywords. Neighboring lines remain available as exact context.
    """
    query, groups = query_groups(item)
    if not query:
        return []
    passages = []
    for page in pages:
        if page.get("status") != "ok":
            continue
        for start, end, line in lines(page["text"]):
            if not line.strip():
                continue
            # Keep table rows/line wrapping intact. Context supplies adjacent
            # column labels, group labels and timing without fabricating a cell.
            passages.append((page, start, end, tokens(line)))
    counts = Counter(token for _, _, _, words in passages for token in words)
    results = []
    for page, start, end, words in passages:
        overlap = query & words
        concept_hits = [group for group in groups if any(phrase <= words for phrase in group)]
        if not overlap and not concept_hits:
            continue
        coverage = len(overlap) / max(1, len(query))
        # A single generic word from a long definition is weak retrieval.
        if not concept_hits and len(overlap) < min(2, len(query)):
            continue
        rarity = sum(math.log(1 + len(passages) / (1 + counts[word])) for word in overlap)
        score = min(5.5, coverage * 2 + min(2, len(concept_hits)) + rarity * 0.15)
        explanation = "Related concept vocabulary in this passage: " + "; ".join(
            " ".join(sorted(next(phrase for phrase in group if phrase <= words))) for group in concept_hits[:3]) if concept_hits else "Item-description term coverage"
        if overlap:
            explanation += ": " + ", ".join(sorted(overlap)[:8])
        explanation += ". Candidate passage; verify its meaning and surrounding context."
        results.append(candidate(page, start, end, explanation, score, "concept_passage"))
    return sorted(results, key=lambda row: (-row["rank_score"], row["page_number"], row["start"]))[:80]


def legacy_search_item(pages, item, metadata=None):
    """Combine ranked candidates without changing editable keywords or evidence."""
    literal = search_pages(pages, item.get("keywords"), item.get("rules"))
    category = intent(item)
    specialized = author_candidates(pages, metadata or {}) if category == "authors" else numeric_candidates(pages, category) if category in {"age", "frequency"} else []
    found = {}

    def merge(row):
        if category == "frequency" and has(row["quote"], r"\b(sampling|sampled|filter|bandpass|band-pass|oscillation|spectral)\b") and not has(row["quote"], r"\b(stimulation|stimulated|rTMS|iTBS|pulses|delivered)\b"):
            return
        key = (row.get("source_type", "pdf_text"), row["page_number"], row["start"], row["end"], row["quote"])
        if key not in found:
            found[key] = row
            return
        old = found[key]
        for field in ("reasons", "retrieval_methods", "matched_rules"):
            old[field] = list(dict.fromkeys(old.get(field, []) + row.get(field, [])))
        old["rank_score"] = max(old["rank_score"], row["rank_score"])
        value = row.get("proposed_value")
        if row.get("multiple_proposed_values") or (value is not None and old.get("proposed_value") is not None and value != old["proposed_value"]):
            old["multiple_proposed_values"] = True
        if old.get("multiple_proposed_values"):
            old["proposed_value"] = None
        elif value is not None:
            old["proposed_value"] = value

    for row in literal["matches"]:
        if category == "authors" and not any(hit["source_type"] == "pdf_text" and hit["page_number"] == row["page_number"] and hit["start"] <= row["start"] and hit["end"] >= row["end"] for hit in specialized):
            continue
        row.update(source_type="pdf_text", reasons=["Literal keyword or explicit rule match; verify relevance to the item."],
                   retrieval_methods=["literal_or_rule"], rank_score=2.0)
        merge(row)
    for row in specialized:
        merge(row)
    if category != "authors":
        for row in passage_candidates(pages, item):
            # Do not produce another card for a line already represented by an
            # exact sentence span; attach its retrieval explanation instead.
            overlapping = next((old for old in found.values() if old["page_number"] == row["page_number"] and old["start"] >= row["start"] and old["end"] <= row["end"]), None)
            if overlapping:
                overlapping["reasons"] = list(dict.fromkeys(overlapping["reasons"] + row["reasons"]))
                overlapping["retrieval_methods"] = list(dict.fromkeys(overlapping["retrieval_methods"] + row["retrieval_methods"]))
            else:
                merge(row)
    return {"matches": sorted(found.values(), key=lambda row: (-row["rank_score"], row["page_number"] or 0, row["start"] or 0)), "errors": literal["errors"]}


def search_item(pages, item, metadata=None, *, semantic=None, chunks=None,
                feedback=None, reranker=None, diagnostics=False):
    from .hybrid.engine import hybrid_search
    legacy = legacy_search_item(pages, item, metadata)
    if intent(item) == "authors":
        # Preserve byline safeguards and metadata separation.
        for rank, row in enumerate(legacy["matches"], 1):
            row.update(rank=rank, rank_score=row["rank_score"] / 10)
        from .hybrid.feedback import apply_feedback
        from .hybrid.models import item_spec
        apply_feedback(item_spec(item),legacy["matches"],semantic,feedback,protected=True)
        return legacy
    return hybrid_search(pages, item, legacy, semantic, chunks, feedback,
                         reranker, diagnostics)
