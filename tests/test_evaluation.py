"""Human-gold metric mechanics; synthetic labels are never a target claim."""
import pytest

from review_extractor.evaluation import evaluate_manifest
from review_extractor.service import ReviewService
from test_retrieval import page


def test_answered_and_pdf_box_ocr_miss_have_correct_denominator(tmp_path):
    service = ReviewService(tmp_path)
    review = service.create_review({"name": "Synthetic evaluation"})["id"]
    field = service.add_item(review, {"name": "Stimulation frequency", "search_mode": "fast"})["id"]
    good = service.add_paper(review, {"title": "Text paper", "processing_status": "ready"})["id"]
    text_page = page("Methods\nrTMS was delivered at 10 Hz.")
    service.set_pages(review, good, [text_page])
    start = text_page["text"].index("rTMS")
    bad = service.add_paper(review, {"title": "Scanned paper", "processing_status": "ready"})["id"]
    service.set_pages(review, bad, [dict(page_number=1, width=100., height=100.,
        text="", words=[], status="ocr_required", error="OCR missing", ocr=False)])
    manifest = dict(schema_version=1, review_id=review, pairs=[
        dict(paper_id=good, item_id=field, split="test", group_id="study-1",
             status="answered", annotator="human reviewer",
             pdf_sha256=service.get_paper(review, good)["sha256"],
             document_version=service.get_paper(review, good)["document_version"],
             gold_spans=[dict(page_number=1, start=start,
                 end=start+len("rTMS was delivered at 10 Hz."),
                 quote="rTMS was delivered at 10 Hz.", kind="prose")]),
        dict(paper_id=bad, item_id=field, split="test", group_id="study-2",
             status="answered", annotator="human reviewer",
             pdf_sha256=service.get_paper(review, bad)["sha256"],
             document_version=service.get_paper(review, bad)["document_version"],
             gold_spans=[dict(page_number=1,
                 box=[10., 10., 50., 30.], kind="ocr")]),
    ])
    report = evaluate_manifest(service, manifest)
    assert report["metrics"]["answered_pairs"] == 2
    assert report["metrics"]["recall_at_3"] == .5
    assert not report["metrics"]["sample_adequate"]
    assert not report["metrics"]["target_met"]
    assert report["failure_counts"] == {"OCR failure": 1}
    assert report["slices"]["kind:ocr"]["recall_at_3"] == 0
    service.store.close()


def test_related_group_cannot_cross_splits(tmp_path):
    service = ReviewService(tmp_path)
    review = service.create_review({"name": "Leakage"})["id"]
    field = service.add_item(review, {"name": "Frequency", "search_mode": "fast"})["id"]
    first = service.add_paper(review, {"title": "A", "processing_status": "ready"})["id"]
    second = service.add_paper(review, {"title": "B", "processing_status": "ready"})["id"]
    pairs = [dict(paper_id=p, item_id=field, split=split, group_id="same-study",
                  status="unlabeled", annotator="reviewer", document_version=0,
                  pdf_sha256=None)
             for p, split in ((first, "train"), (second, "test"))]
    with pytest.raises(ValueError, match="crosses evaluation splits"):
        evaluate_manifest(service, dict(schema_version=1, review_id=review, pairs=pairs))
    service.store.close()
