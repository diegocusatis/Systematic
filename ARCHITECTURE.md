# Integration contract

Python package: `review_extractor`. Modules: `storage.py` (SQLite Store), `service.py` (ReviewService), `search.py`, `documents.py`, `workbooks.py`, `app.py`, static browser assets, reference headings JSON. All identifiers are UUID strings; all timestamps UTC ISO strings. Dictionary objects below are the public API shapes. Missing optional arrays default to [].

## Records

- Review: id, name, reviewer, schema_version, rule_version, created_at, updated_at, settings (object, including last_paper_id/last_item_id).
- Item: id, review_id, name, export_heading, definition, instructions, data_type (`text`, `number`, `integer`, `boolean`, `date`, `narrative`), allowed_values (array), units, required, position, context (object), keywords (array of strings), rules (array of {pattern, kind:literal|regex, label, capture_group?}), deleted, schema_version, rule_version.
- Paper: id, review_id, title, citation, url, filename, sha256, path, included, processing_status (`pending`, `queued`, `processing`, `ready`, `error`), processing_progress (0-100), processing_error, page_count, search_version, search_status (`stale`, `queued`, `refreshing`, `current`, `partial`, `error`), manual_review_ack, created_at.
- Page: paper_id, page_number (physical 1-based), text, width, height, rotation, words (array of {text,x0,top,x1,bottom,start,end}), status (`ok`, `ocr_required`, `error`), error, ocr (bool). Coordinates use the rotated visible CropBox intersected with MediaBox, translated to a top-left origin; page width/height match PDF.js's default viewport. Text is assembled from words and their line breaks, so start/end index exactly into text.
- Suggestion: id, paper_id, item_id, page_number, start, end, quote, context, boxes (array of {x0,top,x1,bottom}), matched_rules (array of strings), proposed_value (nullable), rule_version, active, decision (`pending`, `accepted`, `rejected`), ocr, created_at. Stable dedup key: paper/item/page/start/end. Unchanged passage decisions survive keyword edits and restart.
- Evidence: id, paper_id, item_id, suggestion_id (nullable), page_number, start/end (nullable), quote, boxes, ocr, decision (`accepted`, `rejected`, `pending`), notes, reviewer, created_at, updated_at. Manual evidence must match page text unless page is unsearchable; unsearchable transcriptions must be explicitly labeled manual with rationale.
- Field: paper_id, item_id, status (`unreviewed`, `approved`, `not_reported`, `not_applicable`, `unclear`, `conflicting`), value (JSON typed scalar or narrative string), value_kind (`reported`, `interpretation`, `calculated`), rationale, calculation_inputs (array/object), notes, evidence_ids (array), observations (array of {id?,group,population,outcome,time_point,value,units,evidence_ids}), needs_reconsideration, reviewer, updated_at, schema_version, rule_version.
- History: id, review_id, paper_id?, item_id?, action, before, after, reviewer, created_at, schema_version, rule_version.

### Repeatable outcome extraction

Items additionally carry `section` (`study` by default, or `meta_analysis`), `worksheet_name`, and optional safe `template_style`/`source_column` metadata. A separate `outcomes` table stores stable row IDs scoped to a review and paper: id, review_id, paper_id, label, outcome, group, time_point, units, notes, included, deleted, position, created_at, updated_at. An outcome row represents one arm, outcome, and assessment time. No outcome rows are inferred or created by template import.

`outcome_fields` holds the same decision/value shape as `fields`, adding `outcome_id`. Its unique key is outcome/item; the stored ID includes paper/outcome/item. Existing study fields retain their original IDs and schema. Outcome evidence uses the existing evidence table with `outcome_id` in the record, independently selected and validated per row. Service checks enforce review, paper, outcome and item ownership before reads and writes. History includes `outcome_id` for row-specific changes. Removal is soft and retains values, evidence and history.

Candidate spans remain shared by paper/item. Outcome-specific suggestion decisions are projected from that row's evidence instead of overwriting the shared suggestion decision. Outcome-specific feedback is excluded from the study-level ranking learner, whose current identity does not distinguish outcome rows. Item definition changes, document replacement, supporting evidence edits and outcome identity changes invalidate affected outcome fields for reconsideration. Label/inclusion changes preserve values.

## Service contract

`ReviewService(data_dir: Path)` exposes `.store`, `.data_dir` and the following synchronous methods. `ValueError` means invalid request and `KeyError` missing record.

`list_reviews()`, `create_review(data)`, `get_review(review_id)`, `update_review(review_id,data)`.
`list_items(review_id, include_deleted=False)`, `add_item(review_id,data)`, `update_item(review_id,item_id,data)`, `reorder_items(review_id,ids)`, `delete_item(review_id,item_id)`, `restore_item(review_id,item_id)`.
`list_papers(review_id)`, `get_paper(review_id,paper_id)`, `add_paper(review_id,data)` (hash duplicate returns existing record with duplicate:true), `update_paper(review_id,paper_id,data)`.
`set_pages(review_id,paper_id,pages)`, `get_pages(review_id,paper_id)`.
`refresh_search(review_id, paper_id=None)` synchronous, sets progress/status persistently, called by explicit manual jobs in `scans.py`. Uploads save pending PDFs; startup only marks interrupted jobs pending/stale and preserves results. Jobs are deduplicated by review/paper; rescans reuse pages and metadata, while explicit reprocess re-extracts the PDF. A completed scan with rule timeouts is `partial`: successful suggestions remain available and final export stays blocked until a complete rescan. Unexpected failures remain `error`. Ordinary literal keywords retain case-insensitive, whitespace-tolerant matching without a regex deadline; custom regex matching has a 50 ms deadline and finishes gathering source spans before quotation/highlight processing. Item edits mark affected papers stale immediately; existing suggestions exposed as stale until refresh. `search_document(review_id,paper_id,query)` returns matching page spans in suggestion shape.
`get_suggestions(review_id,paper_id,item_id)` returns array including past decided suggestions; `decide_suggestion(review_id,paper_id,suggestion_id,data)` accepts {decision,notes?}, creates/updates evidence and preserves history.
`get_evidence(review_id,paper_id,item_id)`; `add_evidence(review_id,paper_id,item_id,data)`; `update_evidence(review_id,paper_id,evidence_id,data)`.
`get_field(review_id,paper_id,item_id)` default unreviewed; `save_field(review_id,paper_id,item_id,data)`.
`snapshot(review_id)` => {review,items (including deleted),papers,pages,suggestions,evidence,fields,history}. Used by export and validation; no SQL in export.

## HTTP API (JSON except uploads/export)

- GET/POST /api/reviews; GET/PATCH /api/reviews/{r}
- GET /api/reference-template => {items:[item inputs],source,example_included:false}
- POST /api/templates/preview (multipart `file`, optional `sheet`, `header_rows` e.g. `1,2`, `mode` = columns|dictionary) => {sheets, sheet, header_rows,mode,items,preview_rows,warnings}; dictionary mapping optional form `mapping` JSON object maps property to 1-based column index.
- POST /api/reviews/{r}/items/import => {items:[item inputs]}; appends user-confirmed mappings.
- GET/POST /api/reviews/{r}/items; GET supports include_deleted=true. PATCH/DELETE /items/{i}; POST /items/{i}/restore; POST /items/reorder {ids:[...]}. All mutations return saved records. Item mutations mark results stale without scheduling a scan.
- GET /api/reviews/{r}/papers; POST same path multipart `files` multiple => {papers:[...]} (saves files awaiting a manual scan).
- GET/PATCH /api/reviews/{r}/papers/{p}; GET /pdf returns original; GET /pages => array; POST /reprocess retries ingestion.
- POST /api/reviews/{r}/refresh?paper_id=optional => {queued:bool, queued_count:int}; no paper_id scans the review, otherwise only that scoped paper; GET /api/reviews/{r}/papers/{p}/search?q=... => array.
- GET /api/reviews/{r}/papers/{p}/suggestions?item_id=... => array; PATCH /suggestions/{s} {decision,notes?}.
- GET/POST /api/reviews/{r}/papers/{p}/items/{i}/evidence; PATCH /api/reviews/{r}/papers/{p}/evidence/{e}.
- GET/PUT /api/reviews/{r}/papers/{p}/items/{i}/field.
- GET /api/reviews/{r}/validation => {issues:[{severity,code,message,paper_id?,item_id?}],can_finalize,counts}.
- GET /api/reviews/{r}/export-preview => {columns:[{id,heading}],rows:[{paper_id,title,values:[...],statuses:[...]}]}.
- GET /api/reviews/{r}/export?draft=true|false => xlsx (409 with validation details when final is invalid).
- GET /api/reviews/{r}/history => array.

- GET/POST /api/reviews/{r}/papers/{p}/outcomes; GET accepts `include_deleted=true`.
- PATCH/DELETE /api/reviews/{r}/papers/{p}/outcomes/{o}; POST same path plus `/restore`.
- Existing suggestions, candidate creation, evidence read/write and field read/write routes accept optional query `outcome_id`. Meta-analysis items require a valid outcome; study items reject outcome context. Cross-row evidence cannot approve a field.
- Export snapshots contain `outcomes` and `outcome_fields`. Export preview adds `worksheets:[{name,row_scope,columns,rows}]`; outcome rows include `outcome_id` and row context. Legacy top-level columns/rows contain study fields only. Required meta-analysis fields are validated only for active, included outcome rows of included papers.
- Focused `view=data|verification|combined` downloads preserve active worksheet groups and their row scopes. Combined emits all data sheets before aligned verification sheets. Cell comments retain paper/item/outcome IDs and arm/time/units without inserting extra data columns. Study/Outcome labels can fall back to explicitly labeled row metadata; this does not approve source evidence.

## Root-owned document module

`process_pdf(path:Path, progress:Callable[[int],None]|None=None) -> list[page dict]`.

## Workbook module interface

`preview_template(content:bytes, sheet:str|None=None, header_rows:str='1', mode:str='columns', mapping:dict|None=None)->dict`.
`validate_snapshot(snapshot:dict)->dict`; `export_preview(snapshot:dict,draft:bool=True)->dict`; `export_workbook(snapshot:dict,draft:bool=True)->bytes`.
Reference headings JSON is `review_extractor/resources/reference_items.json`, array of item inputs; no reference study values are bundled as extraction records.

## Browser

Plain JS ES modules, local CSS and HTML. PDF.js 5.6.205 bundled under `/static/vendor/pdfjs/` with build/pdf.mjs, build/pdf.worker.mjs, web/pdf_viewer.css, cmaps/, standard_fonts/, wasm/. Use a canvas and PDF.js TextLayer; overlay top-left normalized coordinates using displayed viewport width / stored page width (and height analogue); preserve correct physical page at zoom. All page numbers physical, not printed labels. No external fonts/CDNs.

## Hybrid retrieval

`retrieval.search_item(pages, item, metadata, *, semantic=None, chunks=None)` preserves legacy byline and source-span matching while delegating scientific retrieval to `hybrid/`. Chunking, lexical/fuzzy scoring, semantic embeddings, reranking, value extraction and validation are separate components. Exact text, offsets and boxes remain authoritative. `rank_score` is a normalized heuristic, never calibrated confidence. Up to 80 ranked passages are stored and the top 10 displayed by default; user-created candidates and prior decided passages remain visible. Metadata candidates have `source_type=pdf_metadata`, null page/offsets, empty boxes and a metadata key. They may be dismissed but cannot create accepted evidence. Visible candidates have `source_type=pdf_text`.

Startup upgrades the retrieval engine version and reads missing document metadata without re-extracting existing pages. Refresh deduplicates by item, source type, page, offsets, exact quote and table identity, preserving accepted/rejected decisions. Search-affecting item edits bump the rule version; concurrent source/definition changes prevent old results from being marked current. First-page font size is retained for byline detection. SQLite adds a review/paper/item-scoped `feedback` table; JSON records gain optional fields without replacing existing schemas. Semantic and OCR dependencies are optional. See `RETRIEVAL.md` for item fields, scores, local setup, cache keys and limitations.
