"""Held-out, item-level evidence retrieval evaluation on human-anchored spans.

Gold labels live in an explicitly supplied local JSON file, never in workbook
examples, saved suggestions or model output. The CLI reruns retrieval without
reviewer feedback so test decisions cannot leak into the ranking.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random

from .retrieval import ENGINE_VERSION, search_item
from .service import ReviewService


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _reference_hit(candidate, gold):
    if candidate.get("page_number") != gold["page_number"]:
        return False
    if "box" in gold:
        left, top, right, bottom = gold["box"]
        area = (right-left)*(bottom-top)
        for box in candidate.get("boxes") or []:
            overlap = max(0., min(right, box["x1"])-max(left, box["x0"])) * max(
                0., min(bottom, box["bottom"])-max(top, box["top"]))
            box_area = (box["x1"]-box["x0"])*(box["bottom"]-box["top"])
            if overlap >= .5*area and box_area <= max(10000., 5*area):
                return True
        return False
    left = max(candidate.get("start") or 0, gold["start"])
    right = min(candidate.get("end") or 0, gold["end"])
    overlap = max(0, right-left)
    gold_length = gold["end"]-gold["start"]
    candidate_length = (candidate.get("end") or 0)-(candidate.get("start") or 0)
    # A whole-page hit must not conceal poor localization.
    return (overlap >= max(1, math.ceil(gold_length*.5)) and
            candidate_length <= max(500, 5*gold_length))


def _has_gold(rows, golds):
    return any(_reference_hit(row, gold) for row in rows for gold in golds)


def _interval(successes, n):
    if n == 0:
        return None
    z = 1.959963984540054
    p = successes/n
    denominator = 1+z*z/n
    center = (p+z*z/(2*n))/denominator
    radius = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denominator
    return [round(max(0., center-radius), 4), round(min(1., center+radius), 4)]


def _cluster_interval(cases, repetitions=2000, seed=101):
    groups = defaultdict(list)
    for case in cases:
        groups[case["group_id"]].append(case["hit_at_3"])
    if len(groups) < 2:
        return None
    ids = sorted(groups)
    rng = random.Random(seed)
    samples = []
    for _ in range(repetitions):
        drawn = [rng.choice(ids) for _ in ids]
        values = [value for paper_id in drawn for value in groups[paper_id]]
        samples.append(sum(values)/len(values))
    samples.sort()
    return [round(samples[int(.025*(repetitions-1))], 4),
            round(samples[int(.975*(repetitions-1))], 4)]


def _summary(cases):
    n = len(cases)
    if not n:
        return dict(status="not_evaluable", answered_pairs=0, recall_at_1=None,
                    recall_at_3=None, recall_at_5=None, mrr=None,
                    wilson_95=None, paper_cluster_95=None)
    hit = lambda k: sum(case[f"hit_at_{k}"] for case in cases)
    return dict(status="measured", answered_pairs=n,
                recall_at_1=round(hit(1)/n, 4),
                recall_at_3=round(hit(3)/n, 4),
                recall_at_5=round(hit(5)/n, 4),
                mrr=round(sum(case["reciprocal_rank"] for case in cases)/n, 4),
                wilson_95=_interval(hit(3), n),
                paper_cluster_95=_cluster_interval(cases))


def _validate_manifest(manifest):
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("pairs"), list):
        raise ValueError("Gold file requires schema_version 1 and a pairs list")
    groups, papers, keys = {}, {}, set()
    for pair in manifest["pairs"]:
        if not all(pair.get(key) for key in ("paper_id", "item_id", "split", "group_id", "status", "annotator")):
            raise ValueError("Each pair needs paper/item IDs, split, group, status and annotator")
        if pair["status"] not in {"answered", "no_answer", "unlabeled"}:
            raise ValueError("Invalid gold status")
        key = (pair["paper_id"], pair["item_id"])
        if key in keys:
            raise ValueError("Duplicate paper/item gold pair")
        keys.add(key)
        previous = groups.setdefault(pair["group_id"], pair["split"])
        if previous != pair["split"]:
            raise ValueError("Related paper group crosses evaluation splits")
        paper_group = papers.setdefault(pair["paper_id"], (pair["group_id"], pair["split"]))
        if paper_group != (pair["group_id"], pair["split"]):
            raise ValueError("A paper crosses evaluation groups or splits")
        if pair["status"] == "answered" and not pair.get("gold_spans"):
            raise ValueError("Answered pairs require human gold spans")
        if pair["status"] == "answered" and pair["annotator"] == "pending":
            raise ValueError("Answered pairs require an identified human annotator")


def _validate_gold(pair, paper, pages):
    if pair.get("document_version") != paper.get("document_version", 0):
        raise ValueError(f"Source version changed for {pair['paper_id']}; re-anchor gold")
    if pair.get("pdf_sha256") != paper.get("sha256"):
        raise ValueError(f"PDF fingerprint changed for {pair['paper_id']}; re-anchor gold")
    by_number = {page["page_number"]: page for page in pages}
    for gold in pair.get("gold_spans", []):
        page = by_number.get(gold.get("page_number"))
        if page is None:
            raise ValueError(f"Gold page is missing: {pair['paper_id']}")
        if "box" in gold:
            box = gold["box"]
            if (not isinstance(box, list) or len(box) != 4 or
                not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box) or
                not (0 <= box[0] < box[2] <= page["width"] and
                     0 <= box[1] < box[3] <= page["height"])):
                raise ValueError(f"Invalid PDF-anchored gold box: {pair['paper_id']}")
            continue
        start, end = gold.get("start"), gold.get("end")
        if (page is None or not isinstance(start, int) or not isinstance(end, int)
                or start < 0 or start >= end or end > len(page.get("text", ""))
                or page["text"][start:end] != gold.get("quote")):
            raise ValueError(f"Gold span is not anchored to current source text: {pair['paper_id']}")


def evaluate_manifest(service, manifest, split="test"):
    _validate_manifest(manifest)
    if split not in {pair["split"] for pair in manifest["pairs"]}:
        raise ValueError(f"No pairs belong to split {split!r}")
    cases, counts, slices = [], defaultdict(int), defaultdict(list)
    for pair in manifest["pairs"]:
        if pair["split"] != split:
            continue
        paper = service.get_paper(manifest["review_id"], pair["paper_id"])
        item = service._item(manifest["review_id"], pair["item_id"])
        pages = service.get_pages(manifest["review_id"], pair["paper_id"])
        _validate_gold(pair, paper, pages)
        counts[pair["status"]] += 1
        if pair["status"] == "unlabeled":
            continue
        result = search_item(pages, item, paper.get("pdf_metadata"),
                             semantic=service.semantic, reranker=service.reranker,
                             feedback=None, diagnostics=True)
        matches = result["matches"]
        if pair["status"] == "no_answer":
            counts["no_answer_with_suggestions"] += bool(matches)
            continue
        golds = pair["gold_spans"]
        rank = next((index for index, row in enumerate(matches, 1)
                     if _has_gold([row], golds)), None)
        diagnostic = result.get("diagnostics", {})
        pool = diagnostic.get("candidate_pool", [])
        shortlist_ids = set(diagnostic.get("neural_shortlist", []))
        shortlist = [row for row in pool if row.get("chunk_id") in shortlist_ids]
        if rank is None or rank > 3:
            if any(pages[gold["page_number"]-1]["status"] != "ok" for gold in golds):
                failure = "OCR failure"
            elif not _has_gold(pool, golds):
                failure = "candidate generation failure"
            elif shortlist_ids and not _has_gold(shortlist, golds):
                failure = "semantic retrieval failure"
            elif rank is None:
                failure = "candidate deduplication failure"
            else:
                failure = "reranking failure"
        else:
            failure = None
        case = dict(paper_id=pair["paper_id"], item_id=pair["item_id"],
                    group_id=pair["group_id"], first_gold_rank=rank,
                    hit_at_1=bool(rank and rank <= 1), hit_at_3=bool(rank and rank <= 3),
                    hit_at_5=bool(rank and rank <= 5),
                    reciprocal_rank=round(1/rank, 6) if rank else 0.,
                    candidate_pool_hit=_has_gold(pool, golds) if pool else None,
                    neural_shortlist_hit=_has_gold(shortlist, golds) if shortlist_ids else None,
                    failure=failure, warnings=result.get("warnings", []))
        cases.append(case)
        slices["field:"+pair["item_id"]].append(case)
        slices["paper:"+pair["paper_id"]].append(case)
        for kind in sorted({gold.get("kind", "prose") for gold in golds}):
            slices["kind:"+kind].append(case)
        if any(pages[gold["page_number"]-1].get("ocr") for gold in golds):
            slices["ocr"].append(case)
    metrics = _summary(cases)
    policy = manifest.get("adequacy_policy") or {}
    minimum_pairs = int(policy.get("minimum_answered_pairs", 100))
    minimum_papers = int(policy.get("minimum_independent_papers", 20))
    independent_papers = len({case["group_id"] for case in cases})
    adequate = len(cases) >= minimum_pairs and independent_papers >= minimum_papers
    metrics.update(independent_papers=independent_papers,
                   adequacy_policy=dict(minimum_answered_pairs=minimum_pairs,
                                        minimum_independent_papers=minimum_papers),
                   sample_adequate=adequate,
                   target_recall_at_3=.95,
                   target_met=bool(adequate and metrics["recall_at_3"] is not None
                                   and metrics["recall_at_3"] >= .95))
    measured_pools = [case for case in cases if case["candidate_pool_hit"] is not None]
    metrics["candidate_pool_coverage"] = (round(sum(case["candidate_pool_hit"]
        for case in measured_pools)/len(measured_pools), 4) if measured_pools else None)
    measured_shortlists = [case for case in cases if case["neural_shortlist_hit"] is not None]
    metrics["neural_shortlist_coverage"] = (round(sum(case["neural_shortlist_hit"]
        for case in measured_shortlists)/len(measured_shortlists), 4) if measured_shortlists else None)
    return dict(created_at=datetime.now(timezone.utc).isoformat(), split=split,
                review_id=manifest["review_id"], engine_version=ENGINE_VERSION,
                model_config=service.retrieval_model_config(),
                ocr_engine=os.environ.get("SYSTEMATIC_OCR_ENGINE", "tesseract"),
                hardware=platform.platform(),
                gold_sha256=_digest(manifest), metrics=metrics,
                counts=dict(counts), slices={key: _summary(value) for key, value in slices.items()},
                failure_counts={reason: sum(case["failure"] == reason for case in cases)
                                for reason in sorted({case["failure"] for case in cases if case["failure"]})},
                cases=cases)


def make_gold_template(service, review_id):
    """Create an unlabeled worksheet without deriving labels from suggestions."""
    pairs = []
    for paper in service.list_papers(review_id):
        if paper.get("removed") or paper.get("processing_status") != "ready":
            continue
        for item in service.list_items(review_id):
            pairs.append(dict(paper_id=paper["id"], item_id=item["id"],
                paper_title=paper.get("title", ""), item_name=item.get("name", ""),
                split="unassigned", group_id=paper.get("sha256") or paper["id"],
                status="unlabeled", annotator="pending",
                pdf_sha256=paper.get("sha256"),
                document_version=paper.get("document_version", 0), gold_spans=[]))
    return dict(schema_version=1, review_id=review_id,
                adequacy_policy=dict(minimum_answered_pairs=100,
                                     minimum_independent_papers=20), pairs=pairs)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure held-out evidence Recall@3")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--create-template", action="store_true")
    parser.add_argument("--review-id")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", default="test")
    args = parser.parse_args(argv)
    service = ReviewService(args.data_dir)
    try:
        if args.create_template:
            if not args.review_id:
                parser.error("--create-template requires --review-id")
            report = make_gold_template(service, args.review_id)
        else:
            if not args.gold:
                parser.error("evaluation requires --gold")
            manifest = json.loads(args.gold.read_text(encoding="utf-8"))
            report = evaluate_manifest(service, manifest, args.split)
    finally:
        service.store.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report.get("metrics", {"template_pairs": len(report["pairs"])}), indent=2))


if __name__ == "__main__":
    main()
