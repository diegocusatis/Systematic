"""Bounded, local relevance feedback across independent source documents.

Decisions are labels, never source truth or value approval. The service supplies
current review/item/source-scoped examples; this layer also excludes target
documents and duplicate copies, masks variable values, and enforces the same
candidate type/context restrictions as baseline retrieval.
"""
import re

from .chunking import normalize

MAX_ADJUSTMENT = .15
MIN_POSITIVE_DOCUMENTS = 2
SIMILARITY_FLOOR = .45
REPRESENTATION_VERSION = 1

# Retain domain terms that happen to be title-cased, while dropping likely
# names/citations. This is intentionally a conservative heuristic, not NER.
DOMAIN_WORDS = set("the a an in at of for with from on to by and or this that these those "
    "mean median average age aged years participants patients subjects adults cohort group "
    "baseline characteristics table methods results intervention treatment control sham active "
    "stimulation frequency intensity resting motor threshold sample size standard deviation "
    "standard error confidence interval change follow up lower extremity score outcome "
    "randomized randomised recruited enrolled allocated trial study protocol session sessions "
    "number twenty thirty forty fifty sixty seventy eighty ninety one two three four five "
    "six seven eight nine ten fatigue pain strength balance walking rehabilitation "
    "inclusion eligibility criteria sd se ci rmt amt mso hz khz lems wisci ii rtms itbs".split())
NUMBER_WORDS = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)"


def learning_text(text):
    """Mask learning-only numeric values, IDs and likely proper names.

    Exact evidence text, highlight spans and baseline embeddings stay intact.
    Units and scientific labels remain meaningful, including ambiguous ±.
    """
    text = normalize(str(text))
    text = re.sub(r"https?://\S+|\b\S+@\S+\b|\b10\.\d{4,9}/\S+", " [identifier] ", text, flags=re.I)
    text = re.sub(r"\b(?:NCT\d+|ChiCTR[-\w]+|ACTRN\d+|ISRCTN\d+|CTRI/[\d/]+)\b", "[identifier]", text, flags=re.I)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{16,}\b", "[identifier]", text, flags=re.I)
    text = re.sub(r"\b[A-Z][A-Za-z'’-]+(?:\s+(?:[A-Z]\.\s*)?et\s+al\.?|\s+(?:and|&)\s+[A-Z][A-Za-z'’-]+)\b", "[name]", text)
    text = re.sub(r"\b[A-Z][A-Za-z'’-]+(?=\s*,?\s*\(?(?:19|20)\d{2}\b)", "[name]", text)

    def mask_name(match):
        words = match.group().split()
        return " ".join(word if word.lower() in DOMAIN_WORDS else "[name]" for word in words)

    # Multiword name-like sequences are masked even at sentence starts. A lone
    # sentence-initial capital remains since it is usually an ordinary word.
    text = re.sub(r"\b[A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+)+\b", mask_name, text)
    text = re.sub(r"(?<=[a-z,;:] )([A-Z][a-z'’-]+)\b", mask_name, text)
    text = re.sub(rf"\b{NUMBER_WORDS}(?:[- ]{NUMBER_WORDS})*\b", "[number]", text, flags=re.I)
    # Alphanumeric experimental IDs are masked; standardized outcome labels
    # retain their scientific meaning independently of reported values.
    text = re.sub(r"\b(?=[A-Za-z\d_-]*\d)(?=[A-Za-z\d_-]*[A-Za-z])[A-Za-z\d_-]{4,}\b",
                  lambda m: m.group() if m.group().upper() in {"10MWT", "6MWT", "WISCI-II"} else "[identifier]", text)
    text = re.sub(r"(?<![A-Za-z\d])[-−]?\d+(?:[,\.]\d+)*(?![A-Za-z\d])", "[number]", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _independent_examples(feedback, item=None):
    """Exclude target copies and group transitive document aliases.

    Several labels from one paper can describe different valid findings. They
    remain available as examples but contribute at most one similarity per
    source document, preventing repeated clicks/spans/copies from adding weight.
    """
    target = set(feedback.get("target_document_fingerprints") or [])
    target_paper = feedback.get("target_paper_id")
    item = item or {}
    originals = [example for example in feedback.get("examples") or []
                if (not item.get("id") or example.get("item_id") == item["id"])
                and (not item.get("review_id") or example.get("review_id", item["review_id"]) == item["review_id"])]
    # Exclude alias chains too: a PDF duplicate and a reprocessed text duplicate
    # may advertise different pairs of hashes for the same original document.
    while True:
        previous = len(target)
        for example in originals:
            fingerprints = set(example.get("document_fingerprints") or [])
            if fingerprints & target or (target_paper and example.get("paper_id") == target_paper):
                target.update(fingerprints)
        if len(target) == previous: break
    examples = []
    for original in originals:
        example = dict(original)
        fingerprints = set(example.get("document_fingerprints") or [])
        if not fingerprints or fingerprints & target or (target_paper and example.get("paper_id") == target_paper):
            continue
        example["_fingerprints"] = fingerprints
        examples.append(example)
    groups = []
    for index, example in enumerate(examples):
        intersecting = [group for group in groups if any(
            examples[j]["_fingerprints"] & example["_fingerprints"] or
            (example.get("paper_id") and examples[j].get("paper_id") == example["paper_id"])
            for j in group)]
        merged = {index}
        for group in intersecting:
            merged.update(group)
            groups.remove(group)
        groups.append(merged)
    for group_number, group in enumerate(groups):
        for index in group: examples[index]["_group"] = group_number
    # A non-training label can still reveal a duplicate-source alias. Group all
    # records first; then omit pending and contextual rejections from learning.
    usable, seen = [], set()
    for example in examples:
        if example.get("decision") not in {"accepted", "rejected"} or not example.get("quote"):
            continue
        if example["decision"] == "rejected" and example.get("rejection_reason") != "irrelevant":
            continue
        key = example.get("example_id") or (example.get("paper_id"), example["quote"], example["decision"])
        if key in seen: continue
        seen.add(key)
        example["_text"] = learning_text(example["quote"])
        usable.append(example)
    return usable


def _positive_allowed(item, row):
    scores = row.get("scores", {})
    if scores.get("negative_score", 0) > 0:
        return False
    # These are evidence-relevance gates, not value-validity gates: ambiguous
    # participant age summaries can be relevant without permitting a mean/SD.
    typed = item.get("category") in {"age", "frequency", "intensity", "sample_size", "statistic", "trial_id"} or item.get("expected_type") in {"integer", "number_unit", "statistic", "trial_id"}
    # The conservative extractor recognizes some explicit spellings (e.g.
    # "standard deviation = ...") that the compact type heuristic misses.
    # A validated proposal establishes the requested type without weakening any
    # negative or context gate, and its content is never changed here.
    return not typed or scores.get("type_score", 0) > 0 or bool(row.get("value_validation", {}).get("valid"))


def _support(similarity):
    return max(0., min(1., (similarity - SIMILARITY_FLOOR) / (1 - SIMILARITY_FLOOR)))


def apply_feedback(item, candidates, semantic=None, feedback=None, *, protected=False):
    """Annotate and adjust already baseline-ranked rows; return warnings.

    Missing learning signals remain ``None``; measured zero adjustments are 0.
    The baseline score is never recomputed or renormalized by this layer.
    """
    feedback = feedback or {}
    config = feedback.get("config") or {}
    status = "disabled"
    examples = []
    if config.get("enabled"):
        if protected: status = "protected_profile"
        elif item.get("search_mode") == "fast": status = "fast_mode"
        else:
            examples = _independent_examples(feedback, item)
            accepted_groups = {e["_group"] for e in examples if e["decision"] == "accepted"}
            status = "ready" if len(accepted_groups) >= MIN_POSITIVE_DOCUMENTS else "sparse_feedback"
    for row in candidates:
        baseline = row["rank_score"]
        row.setdefault("scores", {}).update(baseline_score=baseline, feedback_score=None)
        row["learning"] = dict(status=status, baseline_score=baseline, feedback_score=None,
            config_version=config.get("version", 1), feedback_revision=config.get("revision", 0),
            epoch=config.get("epoch", 0), representation_version=REPRESENTATION_VERSION,
            eligible_examples=len(examples), influences=[])
    if status != "ready" or not candidates: return []
    if semantic is None or not hasattr(semantic, "similarity_matrix"):
        matrix, warning = None, "Feedback embeddings are not configured; using unchanged baseline ranking."
    else:
        matrix, warning = semantic.similarity_matrix([e["_text"] for e in examples],
            [learning_text(row.get("semantic_context") or row["quote"]) for row in candidates])
    if matrix is None:
        for row in candidates:
            row["learning"].update(status="unavailable", warning=warning)
        return [warning] if warning else []
    for column, row in enumerate(candidates):
        by_group = {"accepted": {}, "rejected": {}}
        for example, similarities in zip(examples, matrix):
            similarity = similarities[column]
            if similarity <= SIMILARITY_FLOOR: continue
            previous = by_group[example["decision"]].get(example["_group"])
            if previous is None or similarity > previous[0]:
                by_group[example["decision"]][example["_group"]] = (similarity, example)
        # Top-two independent sources control influence regardless of label count.
        positive = sorted(by_group["accepted"].values(), key=lambda pair: -pair[0])[:2]
        negative = sorted(by_group["rejected"].values(), key=lambda pair: -pair[0])[:2]
        allowed = _positive_allowed(item, row)
        positive_support = sum(_support(s) for s, _ in positive) / MIN_POSITIVE_DOCUMENTS if allowed else 0.
        negative_support = sum(_support(s) for s, _ in negative) / max(1, len(negative))
        adjustment = MAX_ADJUSTMENT * max(-1., min(1., positive_support - negative_support))
        baseline = row["learning"]["baseline_score"]
        score = round(max(0., min(1., baseline + adjustment)), 6)
        adjustment = round(score - baseline, 6)
        influences = []
        for similarity, example in ((positive if allowed else []) + negative):
            influences.append(dict(example_id=example.get("example_id"), feedback_id=example.get("feedback_id"),
                paper_id=example.get("paper_id"), similarity=round(similarity, 6),
                decision=example["decision"], reason=example.get("rejection_reason") or "accepted evidence"))
        row["learning"].update(status="applied" if allowed else "gated", feedback_score=adjustment,
            influences=influences, positive_support=round(positive_support, 6), negative_support=round(negative_support, 6))
        row["scores"].update(feedback_score=adjustment, combined_score=score)
        row["rank_score"] = score
    candidates.sort(key=lambda row: (-row["rank_score"], row.get("page_number") or 0, row.get("start") or 0))
    for rank, row in enumerate(candidates, 1): row["rank"] = rank
    return []
