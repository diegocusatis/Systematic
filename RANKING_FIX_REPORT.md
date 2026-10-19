# Evidence ranking correction

The existing Lin et al. 2023 → Mean age failure was reproduced from the stored
review, before editing, using the cached offline MiniLM model. The accepted
page-5 excerpt ranked sixth (0.230769); generic age discussion ranked first and
second (0.523077 each). The same stored source now places that exact excerpt
first (0.706872). Its original suggestion ID and accepted decision survived the
actual saved-search refresh. Acceptance was only an evaluation label; search
receives pages and item definitions, never evidence decisions or approved values.
No paper identity, private ID, or target numeric value was added to retrieval code.

## Cause and correction

1. Legacy literal/rule, age-pattern and concept excerpts have their own exact
   spans and chunk IDs. The old engine embedded only canonical document chunks,
   then looked up legacy scores by exact ID. Most line excerpts therefore got
   null semantic scores, while sentence candidates had measured scores.
2. Reranking divided by available weights. Omitting semantic weight .35 changed
   the denominator from 1.0 to .65, inflating legacy matches. Generic age text had
   keyword=1, context=1 and layout=.8: (.20+.10+.04)/.65 = .523077.
3. Mean age had no specialized profile. “aged” lacked a lexical alias in the
   stored item; its .55 concept signal gave (.11+.04)/.65 = .230769. Age, aged,
   mean and averaging expanded recall without distinguishing answerability.
4. Canonical sentences and legacy lines could both occupy leading positions.
   Real page geometry also showed alternating left/right-column lines in stored
   text, making some canonical sentences semantically incoherent.

The corrected engine batches embeddings for every candidate's actual normalized
text (or labeled table context). Per-text persistent caching reuses document
vectors across items, process restarts and new custom spans; legacy batch caches
are migrated without inference. Keyword/concept scoring is independent of
candidate source. The fixed weight denominator cannot reward missing semantic
or OCR signals; null remains distinct from measured zero. Diagnostics expose
component scores, missing signals, embedding text/ID and grouped alternate spans.

Age answerability signals distinguish reported summaries, explicit means,
medians, ranges/eligibility, general discussion, and unrelated averaging or time
since injury. Unlabeled summaries remain useful evidence without automatic mean
or SD interpretation. The stored PDF actually contains the replacement character
“�” in the age excerpt; it is retained exactly, not silently repaired to “±”.

The profile audit also covers recording versus treatment frequency, stimulation
intensity versus exercise intensity, participant versus procedure counts, and
SD/change versus baseline, SE and other outcomes. Explicit requested context
restrictions now block specialized proposals across profiles, not only statistics.
Existing conservative statistical requirements remain intact.

## Component comparison

Scores are heuristics, not probabilities. S=semantic, K=keyword, T=type,
Sec=section, C=context, Q=OCR/layout, N=negative signal. Fixed weights are
.35/.20/.20/.10/.10/.05; penalty is .35×N. Fuzzy scores in these rows are zero.

| Passage | Rank before → after | Total before → after | S before → after | K before → after | T before → after | Sec | C before → after | Q | N before → after |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Page 5 participant-age excerpt | 6 → 1 | .230769 → .706872 | null → .562492 | .55 → 1 | 0 → .85 | 0 | 0 → 1 | .8 | 0 → 0 |
| Page 2 “affected by age” | 1 → 3 | .523077 → .095842 | null → .302405 | 1 → 1 | 0 → 0 | 0 | 1 → 1 | .8 | 0 → 1 |
| Page 9 “affected by age” | 2 → 4 | .523077 → .085526 | null → .272932 | 1 → 1 | 0 → 0 | 0 | 1 → 1 | .8 | 0 → 1 |
| Page 5 age range/eligibility excerpt | 7 → 2 | .230769 → .188250 | null → .280715 | .55 → 1 | 0 → .15 | 0 | 0 → 1 | .8 | 0 → .8 |

The broader interleaved version of the age finding is grouped with rank 1.
Six overlapping views were grouped in this search. Alternate exact spans remain
in diagnostics; historical decided cards remain accessible after active results.
Range/discussion results remain visible at low scores; ranking never infers that
an item is not reported. The fifth result has zero score and is not useful evidence.

## PDF structure and preservation

The actual stored page-5 words place left-column text near x=49–300 and right-column
text beginning near x=312. The new detector finds a gutter at x=306 supported by
14 lines. In a read-only re-extraction of the actual PDF, the participant sentence
is contiguous in the left column; four highlight boxes end at x≤300.017. Words
and the damaged glyph are preserved, and source offsets round-trip to word text.

Column-aware order is applied on import or explicit reprocessing, with blank-line
block boundaries for chunking. Rotated/unsupported layouts retain native order;
ruled-table pages retain row order and separately indexed cells. Highlight boxes
cannot bridge a large horizontal gap. A source replacement archives the old text
and word-coordinate map in history and marks dependent evidence/fields for
reconsideration without deleting values or decisions.

No stored pages were reprocessed for this fix. After a consistent SQLite backup,
all item searches for the affected paper were refreshed in the existing database.
Exact before/after comparisons confirmed unchanged item definitions, stored pages,
evidence, fields, feedback and history. The original accepted suggestion ID is
still the first active result. The application, Python launcher and Excel export
workflow are retained; no Node.js runtime dependency was introduced.

## Retrieval evaluation

Fixtures are original synthetic text/PDFs with different values and wording from
the private source. Labels are kept outside query construction. Seven queries
contain 14 distinct relevant findings, with distractors for age discussion,
eligibility, median, averaging, time since injury, recording frequency, exercise
intensity, procedure counts, baseline, wrong outcome and SE.

| Scenario | Relevant ranks, Fast | Relevant ranks, offline MiniLM |
|---|---|---|
| Age: unlabeled dispersion and comparison cohort | 2, 1 | 2, 1 |
| Age: explicit mean and participants averaged | 1, 3 | 1, 2 |
| Age: damaged separator and separate cohort | 2, 1 | 2, 1 |
| Frequency | 1, 2 | 1, 2 |
| Intensity | 1, 2 | 1, 2 |
| Sample size | 1, 2 | 1, 2 |
| SD of LEMS change | 1, 2 | 2, 1 |

Both modes: precision@1=1.0, MRR=1.0, recall@5=1.0 (14/14 findings), and zero
duplicate labeled findings in the top five. Separate mechanism tests cover
wrapped/overlapping spans, repeated values in different arms, table-cell identity,
missing versus zero components, source parity, historical decision references,
source revisions, exact highlights and wrong-context proposal abstention.
Unlabeled SD/change evidence may rank well while its value proposal stays blocked.

A warm search of the actual stored document completed in 1.31 seconds with the
encoder replaced by a function that raises on any inference; no warning or
inference occurred. Refreshing all items for the affected paper took 57.28 seconds.
These are local smoke measurements, not a general performance benchmark.

## Validation

Final full integration run: **136 passed, 0 skipped, 0 failed**, in 58.48 seconds.
This includes 10 Edge browser tests and 2 actual offline-model integrations.
Two existing FastAPI/Starlette deprecation warnings remain.

Command (project virtual environment):

```powershell
$env:RUN_BROWSER_TESTS = '1'
$env:PLAYWRIGHT_CHANNEL = 'msedge'
$env:RUN_SEMANTIC_TESTS = '1'
.venv/Scripts/python -m pytest -q --basetemp=tmp/pytest-ranking-final-02 -p no:cacheprovider
```

The fresh temp path/cache setting only avoids the host's inaccessible shared test
folders; it does not alter test selection or application behavior.

The initial deterministic regression run passed 102 tests with 10 opt-in tests
skipped. A subsequent full run passed 134 tests, including Edge and the real model.
Two additional preservation/layout tests were then added. Verification encountered
an unrelated Windows sandbox account lock (1909), inaccessible shared pytest temp
folders, and a host/browser timeout; reruns use a fresh ignored workspace temp
folder rather than modifying existing temp-folder permissions.

Private audit files and the consistent database backup are under
`tmp/ranking-audit/` (ignored by Git): `before.json`, `after.json`, `metrics.json`,
`refresh-verification.json`, and `review-before-refresh.sqlite3`. No private PDF,
database, workbook or generated test artifact was added to tracked source.

## Changed files

- `review_extractor/hybrid/engine.py`, `semantic.py`, `ranker.py`: uniform scoring,
  inspectable provenance and batched persistent vector reuse.
- `review_extractor/hybrid/models.py`, `extractor.py`, new `relevance.py`:
  age intent, field-specific answerability and proposal context safeguards.
- New `review_extractor/hybrid/deduplication.py`: overlapping finding groups with
  protected arm/outcome/value/cell identities and retained alternate references.
- `review_extractor/documents.py`, new `layout.py`, `search.py`, `tables.py`:
  conservative two-column order, table protection and highlight boundaries.
- `review_extractor/service.py`, `retrieval.py`, `static/app.js`: versioned refresh,
  source-history snapshots, historical-result ordering and visible score details.
- New `tests/ranking_quality_fixture.py`, `tests/test_ranking_quality.py`;
  updated `tests/test_hybrid_models.py`, `tests/test_browser.py`: synthetic quality,
  cache, preservation, browser/export and real-model checks.
- `RETRIEVAL.md`, this report: current scoring/cache behavior and evaluation.

## Remaining limits

This is evidence of the specific correction, not a claim of clinical extraction
accuracy. The seven hand-built scenarios are small and used during development;
they are not an independently labeled held-out corpus. MiniLM remains a general
English model, heuristics remain English-oriented, and unrelated demographic
summaries or complex discourse can still confuse retrieval. Age-value proposals
conservatively abstain even on explicit means pending reviewer interpretation.

The stored source still has column interleaving and damaged font characters;
ranking works without rewriting it. Explicit reprocessing improves supported
geometry but changes offsets and requires source reconsideration. Mixed layouts,
rotated text, figures, borderless/merged tables and more than two columns are not
fully solved. Some distinct-but-low-relevance passages remain in the top five
when the paper contains only one useful finding. More representative labeled
papers and a blinded evaluation are required to quantify generalization.
