"""Offline PaddleOCR-VL page adapter with honest block-level PDF geometry."""
import math
import os
from pathlib import Path
import threading

_PIPELINE = None
_LOCK = threading.RLock()


def _box(raw, sx, sy, width, height):
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x0, top, x1, bottom = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x0, top, x1, bottom)):
        return None
    x0, x1 = sorted((max(0., min(width, x0*sx)), max(0., min(width, x1*sx))))
    top, bottom = sorted((max(0., min(height, top*sy)), max(0., min(height, bottom*sy))))
    if x0 >= x1 or top >= bottom:
        return None
    return dict(x0=x0, top=top, x1=x1, bottom=bottom)


def page_from_blocks(blocks, result, image_size):
    """Keep recognized text verbatim and link each span to a real page region."""
    width, height = image_size
    sx, sy = result["width"]/width, result["height"]/height
    words, fragments, regions, offset = [], [], [], 0
    for block in blocks:
        if not isinstance(block, dict):
            continue
        label = str(block.get("block_label") or "").lower()
        if label in {"image", "chart", "seal"}:
            continue
        content = block.get("block_content")
        if not isinstance(content, str) or not content.strip():
            continue
        box = _box(block.get("block_bbox"), sx, sy, result["width"], result["height"])
        if box is None:
            continue
        if fragments:
            fragments.append("\n\n")
            offset += 2
        start = offset
        fragments.append(content)
        offset += len(content)
        # The parser exposes region boxes, not word boxes. Passage highlights
        # cover the region; token geometry is never fabricated.
        words.append(dict(text=content, start=start, end=offset, **box))
        regions.append(dict(start=start, end=offset, label=label,
                            block_id=block.get("block_id"), box=box))
    if not words:
        return False
    result.update(text="".join(fragments), words=words, ocr_blocks=regions,
                  ocr=True, ocr_confidence=None, status="ok", error="",
                  engine_version="PaddleOCR-VL-1.6",
                  warning="Model OCR: check numbers and table headers against the original PDF; highlights cover whole regions.")
    return True


def _predict(source):
    global _PIPELINE
    layout_dir = os.environ.get("SYSTEMATIC_PADDLE_LAYOUT_DIR")
    vl_dir = os.environ.get("SYSTEMATIC_PADDLE_VL_DIR")
    allow_download = os.environ.get("SYSTEMATIC_PADDLE_ALLOW_DOWNLOAD", "").lower() in {"1", "true"}
    if not allow_download and not (layout_dir and vl_dir and Path(layout_dir).is_dir() and Path(vl_dir).is_dir()):
        raise RuntimeError("Set local PaddleOCR layout/VL model directories or explicitly allow model download")
    from paddleocr import PaddleOCRVL
    import numpy as np
    with _LOCK:
        if _PIPELINE is None:
            options = dict(pipeline_version="v1.6", use_layout_detection=True,
                           use_chart_recognition=False, format_block_content=True)
            if layout_dir:
                options["layout_detection_model_dir"] = layout_dir
            if vl_dir:
                options["vl_rec_model_dir"] = vl_dir
            _PIPELINE = PaddleOCRVL(**options)
        image = source.to_image(resolution=200).original
        predictions = list(_PIPELINE.predict(input=np.asarray(image)))
    if len(predictions) != 1:
        raise ValueError("PaddleOCR returned an unexpected page count")
    payload = predictions[0].json
    if "res" in payload:
        payload = payload["res"]
    return payload.get("parsing_res_list", []), (image.width, image.height)


def ocr_page(source, result):
    blocks, image_size = _predict(source)
    if not page_from_blocks(blocks, result, image_size):
        raise ValueError("PaddleOCR returned no located text regions")


def augment_mixed_page(source, result):
    """Append image-only regions without moving any existing native text offset."""
    blocks, image_size = _predict(source)
    sx, sy = result["width"]/image_size[0], result["height"]/image_size[1]
    native_words = list(result.get("words", []))
    selected = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        box = _box(block.get("block_bbox"), sx, sy, result["width"], result["height"])
        if box is None:
            continue
        # The native text layer already represents a region if a word center
        # lies inside it. This avoids duplicating ordinary PDF text and tables.
        covered = any(box["x0"] <= (word["x0"]+word["x1"])/2 <= box["x1"] and
                      box["top"] <= (word["top"]+word["bottom"])/2 <= box["bottom"]
                      for word in native_words)
        if not covered:
            selected.append(block)
    parsed = dict(width=result["width"], height=result["height"],
                  status="ocr_required", error="")
    if not page_from_blocks(selected, parsed, image_size):
        return False
    separator = "\n\n" if result.get("text") else ""
    offset = len(result.get("text", ""))+len(separator)
    result["text"] = result.get("text", "")+separator+parsed["text"]
    result["words"].extend(dict(word, start=word["start"]+offset,
                                end=word["end"]+offset) for word in parsed["words"])
    result["ocr_blocks"] = [dict(region, start=region["start"]+offset,
                                 end=region["end"]+offset) for region in parsed["ocr_blocks"]]
    result["mixed_ocr"] = True
    result["mixed_ocr_engine"] = "PaddleOCR-VL-1.6"
    result["warning"] = (result.get("warning", "")+" " if result.get("warning") else "") + \
        "Image-only regions were transcribed by PaddleOCR-VL; verify them against the PDF."
    return True
