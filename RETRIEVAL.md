# Hybrid evidence retrieval

## Assessment before the upgrade

The application is a local Python/FastAPI server with a plain JavaScript browser
and bundled PDF.js. `documents.py` ingests PDFs with pdfplumber, reconstructing
text from positioned words. Physical pages, word boxes and source offsets are
already retained. Previously, image-only pages were marked `ocr_required`; there
was no OCR engine, OCR confidence, section index, or table structure.

`search.py` provides timeout-bounded literal/regex matching. `retrieval.py` adds
concept vocabulary and specialized author/age/frequency patterns. Previously it
ranked mostly line-sized passages without fuzzy matching, embeddings, independently
scored signals, or quantitative context validation.

SQLite JSON records separately store reviews, items, papers, pages, suggestions,
evidence, approved fields, observations, and history. Stable review-scoped IDs,
source verification, background search, decision history, PDF navigation, and
manual value approval are reused. Item definitions drive active Excel columns;
openpyxl exports approved values and supporting audit sheets. Example workbook
rows remain excluded from evidence. Excel is not the internal data store.

## Implementation and files

- `review_extractor/hybrid/models.py`: compatible item profiles and score weights.
- `hybrid/chunking.py`: conservative normalization, sentence/neighborhood chunks,
  section/paragraph/sentence identity, table cells and header context.
- `hybrid/lexical.py`: exact aliases and bounded fuzzy support (RapidFuzz when
  installed; standard-library SequenceMatcher fallback).
- `hybrid/semantic.py`: lazy, offline Sentence Transformers; batched normalized
  vectors and persistent SQLite cache, bounded in-memory reuse, shared model.
- `hybrid/ranker.py`: independently inspectable component scores and penalties.
- `hybrid/extractor.py`: separate value extraction and validation. Statistics
  retain outcome, arm, timepoint, statistic, unit, original passage and page.
- `hybrid/engine.py`: candidate generation, reranking, extraction and validation.
- `retrieval.py`: compatible entry point; keeps byline/metadata safeguards and
  existing source spans/regex matching available.
- `documents.py`, `tables.py`, `ocr.py`: native text first, best-effort ruled-table
  cells with geometry; opt-in local OCR for pages needing OCR.
- `service.py`, `storage.py`: richer validated item fields, document-level chunk
  reuse, source-aware refresh, immutable feedback on accept/reject transitions,
  evidence proposal/provenance metadata. Existing databases migrate additively.
- `static/app.js`, `static/index.html`: top 10 candidates, expand-all, relevance,
  page/section/table location, collapsible score debug and quantitative context,
  ambiguity notices, optional retrieval settings and search mode in item editor.
- `workbooks.py`: adds retrieval audit and feedback sheets using existing safe
  text-cell export. Main values still require separate approval.
- `pyproject.toml`: optional semantic and OCR extras. No Node.js runtime.
- `tests/test_hybrid.py`, `tests/test_browser.py`: scientific ranking, provenance,
  cache invalidation/restart, feedback, table/OCR adapters and browser workflow.
- `tests/conftest.py`, `tests/test_hybrid_models.py`: deterministic default tests
  and a separate opt-in integration test using the actual cached local model.

Pipeline: native PDF text / optional OCR → immutable source pages → normalized
structure → lexical/fuzzy/pattern/semantic candidates → weighted reranking →
conservative extraction → validation → human evidence decision → separate value
approval → SQLite → Excel.

## Local installation

The base installation works without model downloads. In the item editor, choose
Fast for lexical/fuzzy/pattern retrieval, or Intelligent to add local embeddings.
Intelligent falls back visibly to Fast when its model is unavailable. First
indexing runs in the existing background search job; reading cached results does
not run OCR or inference again.

Install optional dependencies in your environment:

```powershell
python -m pip install -e ".[semantic,ocr]"
```

Download the default lightweight model once, explicitly (this downloads model
files, not papers; application searches never contact a cloud service):

```powershell
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
python -m review_extractor
```

Alternatively set `SYSTEMATIC_EMBEDDING_MODEL` to a locally cached model ID or
directory. `SYSTEMATIC_EMBEDDING_REVISION` selects a pinned revision (default
`main`; a resolved Hub commit is included in the vector cache identity when
available). Use immutable local model directories or change the revision when
replacing local weights. Models load lazily with `local_files_only=True` and
`trust_remote_code=False`. Sentence Transformers selects an available device;
CPU is supported. Batches contain 32 chunks. Cache keys include chunker version, model/revision and each normalized text.
All cache misses are encoded together in batches of 32; query vectors use the same
content-addressed store. Legacy whole-document batches are migrated without
inference when available. Paper changes encode only changed text; item edits reuse
document vectors. Every candidate, including legacy excerpts, is scored on its own
text (or explicitly labeled table context).

For OCR, install the Tesseract executable separately and then enable:

```powershell
$env:SYSTEMATIC_OCR = '1'
# Set only if Tesseract is not on PATH:
$env:SYSTEMATIC_TESSERACT = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
$env:SYSTEMATIC_OCR_LANGUAGE = 'eng'
python -m review_extractor
```

Use Retry PDF processing for previously imported scans or to add table geometry
to PDFs imported before this upgrade. OCR operates locally at
200 dpi with a 90-second per-page timeout. It retains token confidence, geometry,
and untouched recognized text. Failed OCR remains explicitly unsearchable.
Existing native pages are not OCR'd on searches or item edits.

## Item and score tuning

Names/definitions/keywords keep working. Optional fields: `display_name`,
`description`, `aliases`, `positive_examples`, `negative_examples`,
`expected_type`, `expected_units`, `preferred_sections`, `search_mode`,
`negative_penalty`. The existing Definition field supplies description unless an
explicit API description is present. Defaults cover participant age, stimulation frequency and
intensity, sample size, statistics and trial IDs; legacy concepts cover other
items. Context can specify `outcome`, `group`, and `time_point` / `timepoint`.

Example context for SD of LEMS change:

```json
{"outcome":"LEMS","group":"intervention","time_point":"4 weeks"}
```

Weights in `hybrid/models.py`: semantic .35, keyword .20, expected type .20,
section .10, context .10, OCR/layout .05. The denominator is fixed across candidates. Missing semantic/OCR signals remain
null and contribute no evidence; they never increase another component’s weight. Native text uses a .8 layout-quality prior;
OCR uses measured confidence when available. Fuzzy relevance contributes at most
.3 to the keyword component. Negative evidence subtracts up to .35 by default.
Scores are heuristic relevance, never probabilities of correctness.

The top 10 are displayed by default; additional candidates (up to 80 ranked passages)
and historical decided passages are accessible. Accepting evidence never approves
a value. Unclear / ambiguous and Not reported remain explicit reviewer choices.
No-match results never infer missingness.

## Conservative limits and next improvement

- MiniLM is a general English sentence model, not a validated biomedical reranker.
  Scientific aliases/patterns/context checks supplement it. Evaluate biomedical
  models on labeled papers before changing the default.
- Ruled tables use first-row/first-column header heuristics. These associations
  are marked inferred and require confirmation before numeric proposals. Merged,
  hierarchical, borderless, rotated and scanned tables need manual interpretation.
  Cell evidence spans may include intervening text in PDF reading order; exact
  cell boxes and header context are retained separately.
- Paragraphs use blank-line boundaries; native PDF text often loses those. Section
  headings and sentences are heuristic and English-oriented. A sustained central gutter supports two-column reading order on native,
  unrotated prose pages. Table pages retain row order and cell geometry. Complex
  layouts, abbreviations and cross-page context still need manual review.
- Statistical extraction abstains unless outcome, group, timepoint and statistic
  are explicitly established within the candidate. Bare ± is not assumed to mean
  SD. This intentionally reduces proposals when labels are distributed across
  prose. It does not autonomously resolve a table's header relationships.
- Only high-value numeric patterns are implemented. Unfamiliar items still
  retrieve evidence via definitions/aliases/semantics but may have no proposal.
- Embedding cache has bounded RAM use but no disk eviction yet. Fuzzy fallback is
  slower without RapidFuzz. There is no model training or calibrated confidence.

Recommended next step: assemble a reviewer-labeled benchmark of representative
papers, including merged tables and multiple treatment arms. Measure recall@5,
wrong-arm/outcome/statistic proposals and cold/warm latency, then tune weights and
add an explicit table-header confirmation workflow before introducing a reranker.

## Verification

The pre-upgrade baseline was 87 passed / 8 opt-in browser tests skipped.
New coverage includes all four requested ranking comparisons; wrong outcome,
arm, timepoint and statistic abstention; table cell/header geometry; Unicode/OCR
normalization with unchanged offsets; repeated equal values across arms;
embedding batching, cache reuse, source/model invalidation and restart; review
scope; immutable feedback and export; and top-10/debug/acceptance/settings in
the browser. OCR is tested using synthetic Tesseract output, not a live binary.

Run the deterministic suite:

```powershell
python -m pytest
```

Run all tests including an installed browser and cached MiniLM:

```powershell
$env:RUN_BROWSER_TESTS = '1'
$env:PLAYWRIGHT_CHANNEL = 'msedge'
$env:RUN_SEMANTIC_TESTS = '1'
python -m pytest
```

The semantic integration checks all four comparisons using actual vectors and
fails if a warm query re-embeds either the document or query. Default tests use
deterministic fast retrieval/synthetic encoders, so optional models do not change
unit-test expectations. No test downloads a model or uses private papers.

Verified on this Windows / Python 3.12 checkout: **112 passed**, including all
9 Edge browser tests and the actual offline MiniLM integration, in 46.12 seconds.
Two pre-existing FastAPI/Starlette deprecation warnings remain. `pip check` reports
no broken requirements. Sentence Transformers 5.7.0, RapidFuzz 3.14.6 and
pytesseract 0.3.13 were installed in the project environment; MiniLM was downloaded
and then exercised offline. The Tesseract executable was not installed or tested.

A synthetic 20-page / 200-passage timing check on this machine took 8.06 seconds
for cold model loading/indexing and 0.571 seconds for a cached search of one item.
This is a small synthetic smoke benchmark, not a clinical-paper performance claim.

API references: [Sentence Transformers](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html),
[RapidFuzz](https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html),
[pytesseract](https://pypi.org/project/pytesseract/).


## Evidence-ranking correction (hybrid-3)

See [RANKING_FIX_REPORT.md](RANKING_FIX_REPORT.md) for the reproduced failure,
component-level before/after comparison, fixture metrics, checks, and limits.
Candidate provenance and saved decisions are never relevance signals. Overlapping
views are grouped after ranking; alternate spans stay in score diagnostics and
previously decided suggestions retain their IDs and remain accessible. Distinct
arms, outcomes, measurements and table cells are protected from collapsing.

Age retrieval distinguishes explicit means, unlabeled summaries, medians,
eligibility/ranges, discussion, and unrelated averaging. It deliberately makes no
automatic age-value proposal: a useful age passage does not establish mean/SD.
Wrong requested arm, outcome or timepoint blocks specialized value proposals.

Import/reprocessing uses the column detector; searching does not rewrite stored
source pages. Reprocessing archives the previous text and coordinate map in
history and marks affected evidence/fields for reconsideration. Existing evidence
and approved values remain stored.


## Reviewer feedback (hybrid-4-feedback)

Manual source selections can now become pending user-created candidates or
accepted evidence. Effective feedback comes from the latest versioned decision
for each stable source reference, including manual evidence. Optional local
learning applies a separately reported adjustment bounded by ±0.15 before the
candidate cutoff and deduplication. It requires at least two independent accepted
source documents outside the target paper and reuses cached embeddings.

The baseline remains unchanged with learning disabled, sparse feedback, Fast
mode, unavailable models or protected author retrieval. Context/type constraints
and value approval remain authoritative. Review controls support exclusion,
reset and source navigation. See [FEEDBACK_LEARNING.md](FEEDBACK_LEARNING.md) for
implementation, held-out metrics and exact limits; aggregate retrieval improvement
has not been demonstrated by the initial small synthetic audit.
