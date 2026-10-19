"""Optional local OCR adapters; no repeated OCR during item searching."""
import os


def ocr_page(source, result):
    if os.environ.get("SYSTEMATIC_OCR", "").lower() not in {"1", "true"}: return
    paddle_failure = None
    engine = os.environ.get("SYSTEMATIC_OCR_ENGINE", "tesseract").lower()
    if engine not in {"tesseract", "paddle"}:
        result["error"] += " Unsupported OCR engine; use tesseract or paddle."
        return
    if engine == "paddle":
        try:
            from .paddle_ocr import ocr_page as paddle_page
            paddle_page(source, result)
            return
        except Exception as exc:
            paddle_failure = f"PaddleOCR-VL unavailable or failed: {type(exc).__name__}: {exc}"
            result["error"] += " " + paddle_failure
            # A local Tesseract install can still rescue the page.
    try:
        import pytesseract
        if os.environ.get("SYSTEMATIC_TESSERACT"):
            pytesseract.pytesseract.tesseract_cmd = os.environ["SYSTEMATIC_TESSERACT"]
        image = source.to_image(resolution=200).original
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT,
            lang=os.environ.get("SYSTEMATIC_OCR_LANGUAGE", "eng"), timeout=90)
        words, fragments, offset, previous = [], [], 0, None
        sx, sy = result["width"] / image.width, result["height"] / image.height
        for i, text in enumerate(data["text"]):
            if not text.strip(): continue
            line = tuple(data[key][i] for key in ("block_num", "par_num", "line_num"))
            if words:
                separator = "\n\n" if previous[:2] != line[:2] else "\n" if previous != line else " "
                fragments.append(separator); offset += len(separator)
            left, top = data["left"][i]*sx, data["top"][i]*sy
            confidence = max(0., min(1., float(data["conf"][i])/100))
            words.append(dict(text=text,start=offset,end=offset+len(text),x0=left,top=top,
                x1=left+data["width"][i]*sx,bottom=top+data["height"][i]*sy,ocr_confidence=confidence))
            fragments.append(text); offset += len(text); previous = line
        if words:
            result.update(text="".join(fragments),words=words,ocr=True,status="ok",error="",
                ocr_confidence=sum(w["ocr_confidence"] for w in words)/len(words),
                engine_version=f"Tesseract {pytesseract.get_tesseract_version()}",
                warning="OCR-derived text: verify symbols, numbers and reading order against the PDF.")
            if paddle_failure:
                result["warning"] += " " + paddle_failure + " Tesseract was used instead."
    except Exception as exc:
        result["error"] += f" Local OCR unavailable or failed: {type(exc).__name__}: {exc}"
