# Manual scans and search performance

Uploads now save PDFs without starting text extraction or evidence searches.
Use **Scan all papers** in Review setup or **Scan this paper** in Read & extract.
Both initial scans and rescans require an explicit click. Item edits and engine
updates mark results out of date; reopening preserves saved results and does not
resume interrupted work automatically. Scans in progress are deduplicated by
review/paper, and normal rescans reuse extracted pages and cached embeddings.
The separate reprocess action remains available when PDF extraction must be redone.

## Measured change (2026-09-21)

The reproducible benchmark in `scripts/benchmark_search.py` uses six pages from
the bundled CC0 synthetic PDFs A, B and D and fifteen extraction items. It runs
Fast retrieval twice, including document chunking, excluding PDF extraction,
OCR, database writes and neural-model inference. Python 3.12.14 on this Windows
development computer, with RapidFuzz available, produced:

| Search pass | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| Initial (fresh process) | 1.727 s | 0.262 s | 6.6× |
| Rescan (same process) | 1.685 s | 0.205 s | 8.2× |

Timings vary with the machine and workload. These are search-stage measurements,
not a prediction for processing the user's entire PDF collection. The private
review database and papers were not modified or used as benchmark labels.

The benchmark compared the complete result objects from the old and new code:
source quotations, locations, candidate ordering, component scores, proposed
values and warnings were identical on this workload. Existing synthetic ranking,
feedback and source-traceability tests also remain applicable. This does not
establish human-labeled held-out Recall@3 or the repository's 95% target.

## Changes responsible for the speedup

- Reuse bounded, immutable text tokenization and compiled phrase patterns rather
  than repeatedly normalizing the same passages for every alias and field.
- Cache query concept groups by the full query text, so item edits naturally use
  a new key. Return fresh mutable containers to callers.
- Reuse candidate scores already calculated during retrieval; keep the prior
  behavior for duplicate chunk identities without rescoring ordinary candidates.

No retrieval channel, table handling, fuzzy matching, candidate limit, ranking
weight or optional semantic model was removed or changed. The fuzzy fallback
without RapidFuzz is tested against the previous algorithm too. Caches hold only
derived text calculations in bounded process memory and never reuse review
decisions or approved values across papers.

## Reproduce

Run `python scripts/benchmark_search.py --output outputs/search.json` from the
repository root. To compare against a saved pre-change run, add
`--compare outputs/search-before-unprofiled.json`. The command asserts complete
result equality and reports timings; optional `--profile outputs/search.prof`
records profiling data. Generated results belong in ignored `outputs/`, not Git.

Run `python -m pytest`. Browser journeys use the optional Playwright dependency
with `RUN_BROWSER_TESTS=1` and, on Windows, `PLAYWRIGHT_CHANNEL=msedge`. Tests cover
manual initial scans, selected-paper rescans, retained decisions, restart
recovery, duplicate clicks, failed-search retries, query edits, and matching parity.


## Keyword timeout correction (2026-09-21)

The reported keyword failures were caused by a timeout spanning the lifetime of
an incremental match iterator. A synthetic 80 ms pause between two matches
reproduced the old 50 ms timeout even though matching itself was trivial. In the
reported paper, isolated matching took 0.024 ms for `age`, 0.013 ms for `SCI`, and
0.007 ms for `thoracic`; formatting their quotations/highlights also completed
in under 0.5 ms in that diagnostic run. The particular source of the original
scheduling delay is not established.

Ordinary escaped literal keywords now use the same matching engine, flags and
whitespace handling without the custom-regex timeout. Custom expressions retain
the unchanged 50 ms matching limit, with source spans/captures collected before
quotation and highlight processing. Successful matches before a genuine custom
pattern timeout remain available. No candidate limits or ranking weights changed.

A completed scan with unfinished rule/page searches is labeled `partial`, with
item names, pages and rule labels shown in the interface. Unexpected failures
remain `error`. Partial scans preserve successful suggestions and decisions,
never imply missing evidence, remain blocked from final export, and require a
manual rescan. Restarting does not retry them automatically.

### Full rescan check

The live review was copied through read-only SQLite snapshots into ignored
`outputs/shi-timeout/`. Three real papers and all 29 extraction items were timed
with the existing local semantic model and embedding cache. A captured copy of
the previous matcher used a generous 5-second diagnostic timeout to provide
complete reference results. The old and new matcher were alternated in one
process after warmup, with two measured passes each. Medians:

| Workload | Before | After | Keyword/custom matching before / after |
| --- | ---: | ---: | ---: |
| Affected paper (10 pages) | 6.73 s | 6.48 s | 42.1 / 41.0 ms |
| Comparison paper (11 pages) | 9.06 s | 9.41 s | 59.4 / 63.6 ms |
| Comparison paper (6 pages) | 5.90 s | 5.96 s | 33.4 / 34.2 ms |

There is no consistent full-rescan slowdown across these small samples; the
11-page comparison varied upward slightly. These measurements do not establish
a new speedup. Matching remained a small fraction of total rescan time. All
passes finished without search errors and matched complete candidate content,
source locations, ranking scores, order and decisions against the reference.
Fields and saved evidence also remained unchanged in the isolated runs.

`python scripts/benchmark_rescan.py --help` describes the full-rescan measurement
utility. Its snapshot and outputs can contain private evidence and must remain
outside committed fixtures. `outputs/shi-timeout/interleaved-timings.json` records
the individual alternating measurements locally. These are correctness/parity
and timing checks, not human-labeled accuracy measurements or a Recall@3 claim.

Validation: 213 tests passed with real-browser checks enabled; three optional
model tests were skipped. Regression tests cover delayed highlight processing,
Unicode/whitespace/offset equivalence, genuine pathological custom patterns,
early-match retention, partial-result display, preserved decisions, manual retry,
restart behavior and the final-export gate.
