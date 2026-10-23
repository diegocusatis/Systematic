# Review Extractor

A local Python application for extracting traceable data from full-text research PDFs using an editable Excel design. FastAPI serves the browser, SQLite stores progress, pdfplumber extracts text, openpyxl handles workbooks, and bundled PDF.js displays original papers. No paid API, cloud account, Node.js, or frontend server is required.

Search now ranks scientific evidence using aliases, OCR-tolerant fuzzy matching,
value patterns, sections, context and optional cached local sentence embeddings.
The reader shows the top 10 candidates by default with source locations, relevance scores and
expandable ranking details. Statistical proposals stay ambiguous when arm,
outcome, timing or statistic labels are unclear. See [RETRIEVAL.md](RETRIEVAL.md)
for optional local model/OCR installation, item settings, architecture and limits.

The [retrieval master specification](RETRIEVAL_MASTER_SPEC.md) defines the next
development target: human-labeled, held-out Recall@3 of at least 95%.
The [repository assessment and implementation prompts](RETRIEVAL_ROADMAP.md)
start with a benchmark harness and separate the OCR, field/corpus, candidate
generation, reranking, and feedback work. This target has not been demonstrated.

Optional local PaddleOCR-VL-1.6, Qwen3 embeddings and cross-encoder reranking,
plus the human-gold evaluation command, are documented in
[NEURAL_RETRIEVAL.md](NEURAL_RETRIEVAL.md). These models require separate local
weights; the base installation and existing retrieval remain available.

## Install and launch on Windows

**For lab users:** double-click **Systematic-Setup.exe** from the repository or
the Windows release download. Setup checks for supported 64-bit Python, finds
your previous Systematic automatically, carries over its reviews, extraction
templates, accepted evidence and verification records (checked record by
record), installs the app, and creates **Start Systematic** on your desktop. Use that shortcut after setup. See
[Windows setup, updates, and recovery](WINDOWS_SETUP.md) for the complete guide.

Setup stores reviews permanently in `%LOCALAPPDATA%\ReviewExtractor`, separate
from the application. Updates verify copied reviews and PDF checksums and keep
the previous data as a backup. Original template/export workbooks can also be
selected for preservation. Conflicting copies of the same review require
reconciliation; setup does not silently choose between them.

The following terminal commands remain available for development/manual use:

Install **Python 3.11 or newer** (3.12 recommended). Open PowerShell in this repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m review_extractor
```

Using the environment's executable avoids PowerShell activation-policy problems. If your installation provides `python` instead of `py`, use `python -m venv .venv` for the first command. The launcher opens [http://127.0.0.1:8765](http://127.0.0.1:8765). Leave the terminal running; press Ctrl+C to stop.

This development checkout also has a prepared `.venv`, so it can be launched immediately with `.\.venv\Scripts\python.exe -m review_extractor`.

On macOS/Linux, use `python3 -m venv .venv` and `.venv/bin/python` in place of the Windows executable.

Installation downloads packages. **Normal use is fully offline after installation.** To install on an offline computer, prepare a wheelhouse on another computer with the same OS and Python version:

```powershell
python -m pip wheel -r requirements.txt --wheel-dir wheelhouse
python -m pip wheel --no-deps . --wheel-dir wheelhouse
# On the offline computer, inside its virtual environment:
python -m pip install --no-index --find-links wheelhouse review-extractor
python -m review_extractor
```

## Three sections

### 1. Review setup and PDF upload

Create a named review and set the reviewer identity. Reopen a review from the selector. The bundled reference design preserves the exact 29 headings from `Systematic Example.xlsx`, Sheet1 A1:AC1. Its example row is **not** an extracted study and is never automatically imported.

Use the bundled headings or upload an `.xlsx` template. Preview the worksheet, header rows, or data dictionary; correct headings and mappings before importing. Import appends items to the selected review. There is no 29-column limit. Each item has a stable ID independent of its display name and position.

A worksheet named **Meta Analysis** is recognized as a repeatable outcome design. Its columns become items in the optional meta-analysis section; other worksheets default to study extraction. Check or change each item's section in the template preview or item editor. Only headings and safe formatting are imported. Study rows in the template never become source evidence, approved values, or uploaded papers.

The reference design includes editable starter keywords derived from the headings. These are search aids, not verified definitions or extracted values. Most fields default to narrative text to preserve groups and context; Sessions / week defaults to number, and Total sessions / Total pulses to integer, following the example's numeric cell types. You can change any type and decide which fields are required.

Upload multiple PDFs, then click **Scan all papers** in Review setup when you want the initial scan. Uploading only saves the files. **Scan this paper** in Read & extract scans or rescans just the selected paper. Scans reuse extracted pages unless you explicitly choose to reprocess a PDF in Paper details. Processing/search status, physical page counts, SHA-256 duplicate detection, and OCR/manual-review warnings remain visible. Edit a paper's title, citation, article URL, and inclusion. Duplicates are checked within each review; independent reviews may contain the same paper.

**Remove** beside **Open paper** takes a PDF out of the active review, validation and extraction export rows after confirmation. The original file is not deleted. Its stored copy and decision history are retained; uploading the same PDF again restores the paper and saved work.

### 2. Full-text search, evidence review, and extraction

Select a paper and extraction item. **Edit extraction items** is available in the reading workspace. Add, rename, reorder, delete, restore, or edit definitions, instructions, types, units, keywords, synonyms, and advanced rules. Changes persist for the entire review, including existing and later papers. Deleted items retain their internal work and history.

Search combines item-aware patterns, ranked concept-related passages, editable literal keywords, and optional timed regular expressions. The item name, export heading, definition, instructions and keyword clues all guide retrieval. Author/citation items look for first-page bylines; age and frequency items look for reported measurements even without a literal keyword. Other items use related vocabulary, word inflections, term coverage and document term rarity to rank passages. Changing the item description or rules marks suggestions out of date; saved results remain available until you manually scan again. Uploads, edits, restored papers, engine updates, and reopening the app never start a search automatically. Repeated scan clicks while a paper is queued or running do not add duplicate work. Searching preserves passage decisions and approved values. If a custom search pattern times out, the app labels the scan incomplete and lists the affected extraction item, page, and rule. Successful suggestions remain available, but incomplete searches do not establish missing evidence and block final export. Ordinary keywords are not subject to the custom-pattern timeout. Correct a problematic custom pattern and manually scan the paper again. Material definition/type changes require reconsideration.

Each candidate shows its exact extracted source text, physical page, available highlight boxes, surrounding context and a short match explanation. Multiple numeric captures never select one value silently. Author metadata appears separately with no page number and cannot be accepted as page evidence: inspect the PDF and select the visible byline. The metadata may name a file creator or disagree with the byline.

Fast retrieval runs locally with the base dependencies. Intelligent mode adds optional, locally cached sentence embeddings; no PDFs are uploaded. Search returns up to 80 ranked candidates per item, showing the top 10 initially alongside user-created candidates and saved decisions. Switching papers or items resets the expanded list to this default. Ordering scores are heuristic relevance, not confidence probabilities. Detailed definitions, aliases, examples and explicit group/outcome/timepoint context improve retrieval. Removing a keyword preserves your list but does not disable other retrieval signals. See RETRIEVAL.md for model setup and score tuning.

Author detection is conservative: it uses name-like text, first-page position, an abstract/body boundary, nearby affiliations, typography where available, or metadata corroboration. It handles some multiline bylines and membership labels. Unusual names, scripts, initials, consortium authors, cover pages and complex layouts can be missed. Membership labels are excluded from proposed names while the exact quotation remains unchanged. Age candidates retain table/line context without guessing group-to-column mappings; frequency candidates require inspection to distinguish pulse and burst values. Full-document manual search remains literal.

The original PDF viewer supports physical page navigation, zoom, selectable text, matched-passage overlays, and full-document search. There is also an extracted-text view. Click a suggestion to navigate to its page and highlight. Always check the original PDF when the reconstructed text's reading order is ambiguous.

Accepted suggestions stay at the top of the reading list. In **Approve value**, the full accepted passages and their automatically derived source locations appear above the final-value box. Accepted sources are selected by default for a new decision; uncheck any that do not support the value. Entering a value does not save it until **Save extraction decision** is pressed. Existing saved values and supporting selections are retained when reopening.

There are **two separate decisions**:

1. Accept or reject a passage as evidence. Rejection applies only to that item in that paper, never to the shared keyword or other papers. Manual selections can add further supporting passages.
2. Edit and approve the final extraction value, or explicitly select not reported, not applicable, unclear, or conflicting evidence. No matches leave a field unreviewed. Numeric zero and negative findings are retained.

Values can be reported, interpreted, or calculated. Interpretations/calculations require a rationale; calculations retain their inputs. The advanced observation editor preserves groups, populations, outcomes, time points, units, and evidence separately. The app never averages repeated values or chooses a winner. A discrepancy can be resolved through an approved narrative retaining both supporting sources.

#### Optional meta-analysis outcomes

In **Read & extract**, switch from **Study extraction** to **Meta-analysis outcomes**. Add one outcome row for each study arm, outcome measure, and assessment time you want to extract. Record its arm, time point and units, then use the same evidence review and value approval controls for that row's items. You can add multiple rows for a study, or leave this section unused. Different outcome measures and assessment times remain separate; the app does not pool them or calculate a meta-analysis.

Each outcome row has its own stable ID, evidence decisions and final values. Accepting a source for one row does not approve it for another. Exclude a row from export without losing its work, or remove and restore it. Changes to a row's outcome, arm, time or units flag its existing decisions for reconsideration. Search candidates are shared for the same paper and item, but outcome-specific accept/reject decisions do not feed the study-level learning model.

The template's **n**, **pre-score**, **pre-sd**, **post-score**, **post-sd** and **R** columns are separate numeric fields. Identify the analyzed sample and selected outcome/time explicitly. Document any interpretation or calculation using the existing value-origin, rationale and calculation-input controls; missing statistics remain unreviewed until you make an explicit decision.

Only select not reported after inspecting relevant full-text content. Image-only pages require manual review or the optional local Tesseract OCR setup. Manual transcription must be explicitly identified and explained. External PDFs may already contain OCR text; automated detection of its origin is unreliable, so mark evidence provenance when known.

Review decisions persist. Resume uses the review's saved paper/item selections. Saving and errors are visible. Save edited forms before switching papers or views.

Keywords are filled in automatically for new items and imported template headings using a local vocabulary of related terms. For unfamiliar items, heading phrases provide the starting list. In **Edit extraction items**, edit one phrase per line and save; removed terms stay removed, even after restarting. **Add suggested keywords** merges fresh suggestions with your current list when you want them. Existing empty lists receive a one-time initialization unless their history shows a prior keyword edit. Dictionary templates retain supplied keywords; a literal `[]` explicitly requests an empty list. These search aids do not approve evidence or fill final values.

### 3. Excel export

Preview study sheets with one row per included paper and meta-analysis sheets with one row per included outcome record. Meta-analysis can have zero, one, or many rows for each paper. Saved final values take priority; when none is saved, current accepted source passages appear verbatim. Pending/rejected passages never fill cells. Source acceptance remains distinct from final value approval, and exporting changes no decisions. Other cells stay blank; missingness is never inferred. Attention is summarized as one count.

Three buttons download real `.xlsx` files: **Data workbook**, **Verification workbook**, and **Combined workbook**. Uploaded templates default to all worksheets, keeping each sheet’s items separate even when their headings match. You can instead preview a selected worksheet when its header rows or dictionary mappings differ. Empty worksheets have no items and are flagged in preview. Item editing retains a Worksheet assignment; legacy items belong to Data.

Data downloads preserve each extraction worksheet name and column order. Verification downloads have one matching location worksheet per data worksheet. Combined downloads contain exactly 2N tabs for N active extraction sheets: all data sheets first, then all verification sheets in the same order. Verification names use `Verify - ` and are shortened with unique suffixes when required by Excel’s 31-character limit. The preview has a selector for each worksheet. Cell notes preserve paper/item IDs, source locations, content type and original field status without adding extra worksheets; workbook properties describe conventions. The active design determines column order. Numeric final values retain their types; untrusted text is always literal text. These focused downloads exclude the large retrieval audit; the complete history and search records remain in the local review. The original audit exports remain available through the draft/final API.

Verification cells identify the sources of the corresponding final value or accepted passages. Locations use physical PDF page numbers and 1-based lines in extracted text, which can differ from printed PDF lines. Images, OCR and manual transcriptions use saved figure/table/panel/row/column descriptions and selected PDF coordinates. Missing precise details are flagged rather than guessed. Add a descriptor in the evidence dialog or use **Edit source location**; no additional export approval is required. Explicit missingness and empty data cells have blank verification cells.

For the two-sheet **Systematic Review / Meta Analysis** design, the combined file contains those two sheets followed by **Verify - Systematic Review** and **Verify - Meta Analysis**. The verification copies have matching headings and row order, including repeated outcomes. Cell notes retain stable paper, item and outcome IDs and the outcome's arm/time/units, so two results from the same paper remain distinguishable. Reviewer-entered study/outcome labels are identified as context rather than PDF evidence. Imported header formatting and column widths are preserved where available.

Preview and refresh read only the current design, fields, accepted evidence and source text/status. They do not load search candidates, learning examples, PDF geometry or history. Decision history loads when its panel is opened. Every refresh reads current records; no stale preview cache is used.

## Storage and backup

An existing checkout database at `.review-data/reviews.sqlite3` is reopened automatically, even when launched from a different working directory. Explicit `--data-dir` or `REVIEW_EXTRACTOR_DATA_DIR` settings take precedence. For new installations, the Windows default is `%LOCALAPPDATA%\ReviewExtractor`. Other systems: `~/.local/share/ReviewExtractor`. Override it with:

```powershell
.\.venv\Scripts\python.exe -m review_extractor --data-dir .\review-data --port 8765 --no-browser
```

`REVIEW_EXTRACTOR_DATA_DIR` is also supported. Copy the **entire data directory while the app is stopped** to back up PDFs, extracted pages, settings, values, evidence and history together. Review data is ignored by Git. Interrupted processing/search waits for a manual scan after restart; saved results are retained and failed scans can be retried. Closing the app cancels scans that have not started; the active paper finishes before shutdown.

The launcher binds to loopback and rejects cross-site writes. This is a single-user desktop service without network authentication, encryption at rest, or a shared-workgroup server. Do not expose its port publicly. SQLite plus the original PDFs is the complete editable backup; Excel is an export.

## Demonstration and verification

`demo/` contains original CC0 synthetic papers A-D, an image-only page, conflicting counts, distinct group/time observations, and a cropped/rotated geometry fixture. All are labeled synthetic. The example workbook was inspected for format; no source research PDFs were supplied for factual verification of its study row.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -m "not browser"
# Optional browser tests (developer verification only):
.\.venv\Scripts\python.exe -m playwright install chromium
$env:RUN_BROWSER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m browser
```

To test with installed Microsoft Edge instead, set `$env:PLAYWRIGHT_CHANNEL = "msedge"`. End users do not need Playwright. GitHub Actions runs Python tests on Windows/Linux and browser tests on Linux. See `VERIFICATION.md` for the exercised journeys and measured results.

## Organization

- `review_extractor/app.py`: HTTP, uploads, background scheduling, and local serving.
- `storage.py`, `service.py`: persistent entities, review decisions, versions and history.
- `scans.py`: manual, deduplicated background scans and restart recovery without automatic searching.
- `documents.py`, `search.py`, `retrieval.py`: document processing, literal search and item-aware concept retrieval.
- `workbooks.py`: template preview, validation, safe dynamic Excel export.
- `static/`: browser interface and local PDF.js.
- `tests/`: document, service, workbook, HTTP and browser journey tests.

`IMPLEMENTATION_PLAN.md` records initial assumptions; `ARCHITECTURE.md` describes interfaces; `AGENTS.md` guides development. Dependencies are pinned. PDF.js licenses remain in the vendor folder; `THIRD_PARTY_NOTICES.md` records its version.

## Limitations and later milestones

- Search combines lexical, fuzzy, pattern, context and optional local semantic signals. Proposals and relevance scores remain unapproved heuristics. Reviewers decide whether a passage supports a value.
- Optional local Tesseract OCR supports pages without usable text; it needs a separately installed executable. Image-only failures are flagged. Mixed text/image pages may contain unsearchable figures. Multi-column layout, handwriting, complex tables and embedded images require inspection.
- Intelligent search uses optional local Sentence Transformers with an explicit initial model download. Searching uses cached model files offline. No external AI calls or credentials are needed.
- Independent second-reviewer extraction, blinding, reconciliation and adjudication are later milestones. The current reviewer label and history support one local reviewer.
- One record represents one PDF by default. Related publications and study families require researcher judgment; duplicate detection finds identical file bytes only.
- Excel has intrinsic cell and row-size limits. Oversized main cells are flagged before final export. Long quotes and audit records in supporting sheets use numbered continuation rows so their wording can be reconstructed without truncation. Row heights are capped at Excel's limit; use the formula bar for very long cells. Preserve the database/PDF backup as the complete record.


## Manual candidates and reviewer learning

Highlight text in the PDF or extracted-text view and choose **Create candidate
for this item**. Save it pending or accept it as evidence; approve the final value
separately. User-created candidates and their stable references survive refresh.

Open **Reviewer learning** to enable optional local learning, manage examples or
reset learned adjustments. Intelligent search needs accepted examples for the
same item from at least two independent other source documents. The current
paper and duplicate copies are excluded. Refresh suggestions to apply changes;
**Reviewer feedback influence** shows the baseline score, bounded adjustment and
source examples. Learning never approves values or overrides context validation.

See [FEEDBACK_LEARNING.md](FEEDBACK_LEARNING.md) for usage, preservation rules,
measured gains and limitations. The initial eight-case audit improved one SD
passage from fifth to third but did not improve aggregate MRR or recall@5.
