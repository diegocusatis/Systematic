"""One source-location convention for the reading workspace and Excel exports."""
import json
import math


def _text(value):
    if value is None:
        return ''
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def source_location(record, page):
    parts = [f"Page {record['page_number']}"]
    image = record.get("manual") or record.get("ocr") or record.get("ocr_block_label") in {"figure", "image"}
    text, start, end = page.get("text"), record.get("start"), record.get("end")
    if (not image and page.get("status", "ok") == "ok" and isinstance(text, str)
            and isinstance(start, int) and isinstance(end, int) and text[start:end] == record["quote"]):
        first = text.count("\n", 0, start) + 1
        last = text.count("\n", 0, max(start, end - 1)) + 1
        parts.append(f"line {first}" if first == last else f"lines {first}-{last}")
    descriptor = _text(record.get("source_location")).strip()
    if descriptor:
        parts.append(descriptor)
    elif record.get("table_title"):
        parts.append(_text(record["table_title"]))
    elif record.get("table_id"):
        parts.append("table region")  # Internal table IDs are not printed table numbers.
    for key, label in (("row_header", "row"), ("column_header", "column")):
        if record.get(key):
            parts.append(f"{label}: {_text(record[key])}")
    boxes = [box for box in record.get("boxes", []) if all(
        isinstance(box.get(key), (int, float)) and not isinstance(box.get(key), bool) and math.isfinite(box[key])
        for key in ("x0", "top", "x1", "bottom")) and box["x1"] > box["x0"] and box["bottom"] > box["top"]]
    # Text lines are sufficient for ordinary text. Image/table cells additionally
    # retain exact page coordinates when available.
    if boxes and (image or record.get("table_id") or len(parts) == 1):
        left, top = min(box["x0"] for box in boxes), min(box["top"] for box in boxes)
        right, bottom = max(box["x1"] for box in boxes), max(box["bottom"] for box in boxes)
        parts.append(f"selected region x={left:g}-{right:g}, y={top:g}-{bottom:g} PDF units from top-left")
    if len(parts) == 1 or (image and not descriptor and not boxes and not record.get("table_title") and not record.get("row_header") and not record.get("column_header")):
        parts.append("precise region not recorded")
    if image:
        parts.append("manual transcription; no reliable line number" if record.get("manual") else "OCR/image source; no reliable line number")
    return ", ".join(parts)

