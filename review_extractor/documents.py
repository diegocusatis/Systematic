"""PDF text and coordinates, with explicit per-page processing limitations.

Native text is preferred; optional local OCR handles unsearchable pages. No
network calls are performed. Source tokens and offsets remain authoritative.
"""

from pathlib import Path
from typing import Callable
import os

import pdfplumber
from .ocr import ocr_page
from .tables import extract_tables
from .layout import column_order


def read_pdf_metadata(path: Path) -> dict:
    """Read document-level fields separately from physical-page text.

    Metadata can describe the file creator rather than the paper authors. It is
    retained verbatim for a labeled candidate, never given a fake page location.
    """
    with pdfplumber.open(path) as pdf:
        return {key: value for key, value in pdf.metadata.items()
                if key in {"Author", "Title", "Subject"} and isinstance(value, str) and value.strip()}


def process_pdf(path: Path, progress: Callable[[int], None] | None = None) -> list[dict]:
    """Read every physical page; coordinates are in the visible, rotated CropBox.

    pdfplumber's coordinates already incorporate the PDF's rotation. Cropping and
    translating their origin makes them correspond to PDF.js's default viewport.
    Whitespace is reconstructed between words; the words themselves stay intact.
    Image-only pages and individual failures remain represented in the result.
    """
    pages = []
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        if not total:
            raise ValueError("The PDF contains no pages.")
        for number, page in enumerate(pdf.pages, 1):
            result = {
                "page_number": number, "text": "", "width": float(page.width),
                "height": float(page.height), "rotation": page.rotation,
                "words": [], "status": "ok", "error": "", "ocr": False,
                "engine_version": f"pdfplumber {pdfplumber.__version__}",
            }
            try:
                media, crop = page.mediabox, page.cropbox
                visible = (max(media[0], crop[0]), max(media[1], crop[1]),
                           min(media[2], crop[2]), min(media[3], crop[3]))
                if visible[2] <= visible[0] or visible[3] <= visible[1]:
                    raise ValueError("PDF has an invalid visible page boundary.")
                source = page if tuple(page.bbox) == visible else page.crop(visible)
                result.update(width=float(source.width), height=float(source.height))
                directions = {
                    0: {},
                    90: {"line_dir_rotated": "rtl", "char_dir_rotated": "ttb"},
                    180: {"line_dir": "btt", "char_dir": "rtl"},
                    270: {"line_dir_rotated": "ltr", "char_dir_rotated": "btt"},
                }.get(page.rotation, {})
                words = source.extract_words(x_tolerance=3, y_tolerance=3,
                                             keep_blank_chars=False,
                                             use_text_flow=False, **directions)
                detected_tables = None
                try:
                    detected_tables = source.find_tables()
                except Exception as exc:
                    result["table_warning"] = f"Table layout unavailable: {type(exc).__name__}"
                if detected_tables:
                    # Table gutters are not prose columns. Keep native row order.
                    result["reading_order"] = {"mode": "native_table", "version": "columns-1"}
                else:
                    words, result["reading_order"] = column_order(words, float(source.width), page.rotation)
                fragments, offset, previous = [], 0, None
                for word in words:
                    text = word["text"]
                    if not text:
                        continue
                    if previous is not None:
                        line_axis = "x0" if page.rotation in (90, 270) else "top"
                        separator = "\n" if abs(word[line_axis] - previous[line_axis]) > 3 else " "
                        if word.get("layout_block") != previous.get("layout_block"):
                            separator = "\n\n"
                        fragments.append(separator)
                        offset += len(separator)
                    token = {
                        "text": text, "start": offset, "end": offset + len(text),
                        "x0": float(word["x0"] - visible[0]),
                        "top": float(word["top"] - visible[1]),
                        "x1": float(word["x1"] - visible[0]),
                        "bottom": float(word["bottom"] - visible[1]),
                    }
                    if "layout_block" in word: token["layout_block"] = word["layout_block"]
                    if number == 1:
                        # First-page typography helps distinguish a title from a
                        # byline without altering reconstructed text or offsets.
                        sizes = [float(char["size"]) for char in source.chars
                                 if abs(char["top"] - word["top"]) < 2
                                 and word["x0"] - 1 <= char["x0"] < word["x1"]]
                        if sizes:
                            token["font_size"] = max(sizes)
                    result["words"].append(token)
                    fragments.append(text)
                    offset += len(text)
                    previous = word
                result["text"] = "".join(fragments)
                if not result["text"].strip():
                    result["status"] = "ocr_required"
                    result["error"] = "No searchable text. OCR or a manual review of the original page is required."
                elif len(result["text"].strip()) < 20 and source.images:
                    result["status"] = "ocr_required"
                    result["error"] = "Very little text accompanies page images; inspect the original for unsearchable content."
                elif source.images:
                    result["warning"] = "Page contains images. Words inside figures or scanned regions may not be searchable."
                if result["status"] == "ocr_required":
                    ocr_page(source, result)
                if result["status"] == "ok" and not result["ocr"]:
                    try:
                        result["tables"] = extract_tables(source, result, visible, detected_tables)
                    except Exception as exc:
                        result["table_warning"] = f"Table layout unavailable: {type(exc).__name__}"
                    if (source.images and os.environ.get("SYSTEMATIC_OCR", "").lower() in {"1", "true"}
                            and os.environ.get("SYSTEMATIC_OCR_ENGINE", "").lower() == "paddle"
                            and os.environ.get("SYSTEMATIC_OCR_MIXED", "").lower() in {"1", "true"}):
                        try:
                            from .paddle_ocr import augment_mixed_page
                            augment_mixed_page(source, result)
                        except Exception as exc:
                            result["warning"] = (result.get("warning", "") + " " +
                                f"Mixed-page OCR unavailable: {type(exc).__name__}: {exc}").strip()
            except Exception as exc:  # one damaged page must not hide other pages
                result["status"] = "error"
                result["error"] = f"Text extraction failed: {type(exc).__name__}: {exc}"
            pages.append(result)
            if progress:
                progress(round(number * 100 / total))
            page.close()
    return pages
