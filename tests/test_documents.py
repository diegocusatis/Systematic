from pathlib import Path

import pytest

from review_extractor.documents import process_pdf


DEMO = Path(__file__).resolve().parents[1] / "demo"


def test_all_pages_offsets_and_image_only_detection():
    progress = []
    pages = process_pdf(DEMO / "study-C.pdf", progress.append)
    assert len(pages) == 3
    assert [page["status"] for page in pages] == ["ok", "ok", "ocr_required"]
    assert pages[-1]["ocr"] is False  # OCR required is not OCR-derived text
    assert progress[-1] == 100
    assert "Physical PDF page 1 | Printed label 9" in pages[0]["text"]
    for page in pages:
        for word in page["words"]:
            assert page["text"][word["start"]:word["end"]] == word["text"]
            assert 0 <= word["x0"] < word["x1"] <= page["width"]
            assert 0 <= word["top"] < word["bottom"] <= page["height"]


def test_rotated_cropbox_coordinates_and_wording():
    pages = process_pdf(DEMO / "geometry-cropped-rotated.pdf")
    for page, rotation in zip(pages, (0, 90, 180, 270)):
        assert page["rotation"] == rotation
        assert page["text"] == "Geometry marker 20 Hz"
        assert (page["width"], page["height"]) == ((480, 650) if rotation % 180 == 0 else (650, 480))
        for word in page["words"]:
            assert 0 <= word["x0"] < word["x1"] <= page["width"]
            assert 0 <= word["top"] < word["bottom"] <= page["height"]


def test_malformed_pdf_is_an_explicit_failure(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF- invalid PDF")
    with pytest.raises(Exception):
        process_pdf(path)
