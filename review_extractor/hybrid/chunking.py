"""Structure-aware chunks with offsets into untouched page text."""
import hashlib
import html
import re
import unicodedata

from ..search import boxes_for_span

VERSION = "structure-1"
HEADINGS = re.compile(r"^(?:\d+[.\s]+)?(abstract|introduction|background|methods?|materials and methods|participants|intervention|treatment|protocol|stimulation protocol|results|discussion|conclusions?|references|baseline characteristics)\s*[:.]?$", re.I)


def _ocr_region(page, start, end):
    return next((region for region in page.get("ocr_blocks", [])
                 if region["start"] <= start and end <= region["end"]), None)


def _table_rows(text):
    html_rows = list(re.finditer(r"<tr\b[^>]*>.*?</tr\s*>", text, re.I | re.S))
    if html_rows:
        return html_rows
    return [match for match in re.finditer(r"[^\n]+", text)
            if "|" in match.group() and not re.fullmatch(r"[\s|:\-]+", match.group())]


def _plain_table_text(value):
    value = re.sub(r"<[^>]+>", " | ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip(" |")


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"\+\s*/\s*-|\+\s*−", "±", text)
    text = re.sub(r"\brTIVIS\b", "rTMS", text, flags=re.I)
    text = re.sub(r"\b1OMWT\b", "10MWT", text, flags=re.I)
    text = re.sub(r"(?<=\d)\s*H2\b", " Hz", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def make_chunk(page, start, end, section=None, **extra):
    quote = page["text"][start:end]
    identity = f'{page.get("paper_id", "")}:{page["page_number"]}:{start}:{end}:{quote}:{extra.get("table_id")}:{extra.get("table_column")}'
    row = dict(document_id=page.get("paper_id"), chunk_id=hashlib.sha256(identity.encode()).hexdigest()[:24],
        page_number=page["page_number"], start=start, end=end, quote=quote, original_text=quote,
        normalized_text=normalize(quote), context=quote, section=section, paragraph_index=None,
        sentence_index=None, table_id=None, table_row=None, table_column=None,
        boxes=boxes_for_span(page, start, end), ocr=bool(page.get("ocr")),
        ocr_confidence=page.get("ocr_confidence"), source_type="pdf_text")
    row.update(extra)
    return row


def chunk_document(pages):
    chunks, section = [], None
    for page in pages:
        if page.get("status") != "ok": continue
        text = page.get("text", "")
        # Blank lines are reliable paragraph boundaries; single newlines may be PDF wrapping.
        paragraphs = list(re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, re.S))
        for paragraph_index, para in enumerate(paragraphs):
            if (_ocr_region(page, para.start(), para.end()) or {}).get("label") == "table":
                continue
            # Heading lines and complete sentence-ending lines split naturally, while
            # wrapped prose stays intact. Decimal points never split a sentence.
            boundaries = [para.start()]
            for line in re.finditer(r"[^\n]+", para.group()):
                if HEADINGS.match(line.group().strip()) or re.match(r"Table\s+\d+\b", line.group(), re.I):
                    boundaries.extend([para.start()+line.start(), para.start()+line.end()])
            boundaries.extend(para.start()+m.end() for m in re.finditer(r"[.!?](?=\s|$)", para.group()))
            boundaries.append(para.end())
            boundaries = sorted(set(boundaries))
            rows = []
            for left, right in zip(boundaries, boundaries[1:]):
                while left < right and text[left].isspace(): left += 1
                while right > left and text[right-1].isspace(): right -= 1
                if left == right: continue
                heading = HEADINGS.match(text[left:right])
                if heading:
                    section = heading.group(1).title()
                    continue
                row = make_chunk(page, left, right, section, paragraph_index=paragraph_index,
                                 sentence_index=len(rows), kind="sentence")
                region = _ocr_region(page, left, right)
                if region:
                    row["ocr_block_id"] = region.get("block_id")
                    row["ocr_block_label"] = region["label"]
                    row["ocr"] = True
                    row["ocr_confidence"] = None
                rows.append(row)
            for index, row in enumerate(rows):
                neighbors = rows[max(0,index-1):index+2]
                row.update(context=text[neighbors[0]["start"]:neighbors[-1]["end"]],
                    parent_id=f'{page.get("paper_id", "")}:{page["page_number"]}:p{paragraph_index}',
                    previous_chunk_id=rows[index-1]["chunk_id"] if index else None,
                    next_chunk_id=rows[index+1]["chunk_id"] if index+1 < len(rows) else None)
                chunks.append(row)
        for region in page.get("ocr_blocks", []):
            if region.get("label") != "table":
                continue
            raw = text[region["start"]:region["end"]]
            lines = _table_rows(raw)
            if not lines:
                lines = [re.match(r"[\s\S]+", raw)] if raw.strip() else []
            header = _plain_table_text(lines[0].group()) if lines else ""
            for row_index, line in enumerate(lines):
                start, end = region["start"]+line.start(), region["start"]+line.end()
                value = _plain_table_text(line.group())
                row = make_chunk(page, start, end, "Table", kind="ocr_table_row",
                    table_id=f'ocr:{page["page_number"]}:{region.get("block_id")}',
                    table_row=row_index, table_column=0,
                    column_header=header if row_index else None,
                    row_header=value.split("|")[0].strip() if "|" in value else None,
                    table_header_inferred=True, ocr_block_id=region.get("block_id"),
                    ocr_block_label="table", ocr=True, ocr_confidence=None)
                row["semantic_context"] = " | ".join(part for part in (header if row_index else "", value) if part)
                row["context"] = row["semantic_context"]
                chunks.append(row)
        for table in page.get("tables", []):
            for cell in table.get("cells", []):
                if cell.get("start") is None: continue
                row = make_chunk(page, cell["start"], cell["end"], "Table", kind="table_cell",
                    table_id=table["id"], table_row=cell["row"], table_column=cell["column"],
                    table_title=table.get("title"), row_header=cell.get("row_header"),
                    column_header=cell.get("column_header"), table_header_inferred=table.get("headers_inferred", True),
                    cell_value=cell.get("text"), cell_boxes=cell.get("boxes", []))
                row["semantic_context"] = " | ".join(str(x) for x in (table.get("title"),cell.get("row_header"),cell.get("column_header"),cell.get("text")) if x)
                row["context"] = row["semantic_context"]
                chunks.append(row)
    return chunks
