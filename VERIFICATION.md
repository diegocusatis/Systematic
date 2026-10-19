# Verification record

The tests use visibly labeled synthetic documents, plus a read-only inspection of the user's supplied workbook. No actual research PDF was supplied for factual checking of its example study row. That row is never imported as study evidence.

## Local results (2026-09-14)

**73 tests passed:** 68 document, search, storage, service, workbook, and HTTP tests; 5 real-browser tests using Microsoft Edge on Windows with Python 3.12. The final 72-test integrated run passed, followed by the added mathematical-Unicode selection regression passing independently. The browser tests also download a final workbook and reopen it to verify its values and supporting sheets.

The wheel and source distribution build successfully. The wheel was installed into a separate target folder and its API, reference design, HTML, JavaScript, and bundled PDF.js assets passed a smoke check. `pip check`, Python compilation, and JavaScript syntax checks passed. Two upstream test-client deprecation warnings remain; there were no test failures in these final runs.

The Windows/Linux GitHub Actions workflow is provided but has not been run on GitHub during this local task.

## Requested journeys

| Journey | Verification |
| --- | --- |
| 1. Preserve all 29 reference headings/order | HTTP template preview reads the actual workbook when present; reference JSON is compared with its headings. Workbook tests cover ordinary, merged-header, multi-sheet, and dictionary designs. |
| 2. Add an item in PDF A; make it available in B/C and later D | HTTP workflow uploads A-C, adds an item, checks empty field records and suggestions, then uploads D. Browser workflow adds an item in the reading workspace. |
| 3. Change keywords and refresh existing papers | Search suggestions and paper search versions/status are checked after review-wide edits. Transparent regex tests include timeouts and multiple conflicting captures. |
| 4. Approve, rename and reorder without reassigning evidence/value | HTTP journey saves numeric zero with evidence and a labeled observation, then renames/reorders the item and verifies stable IDs and values. |
| 5. Delete and restore prior work | HTTP workflow checks removal from active items/main export, then restoration of approved values and evidence. |
| 6. Reject a passage; rerun and restart | The same suggestion retains its rejection through new rules, searches, and a new application instance; other papers remain pending. |
| 7. Refresh/restart preserves settings and decisions | New FastAPI/SQLite instances recover review settings, selected IDs, fields and passage decisions. Interrupted PDF processing is resumed on startup. |
| 8. Independent reviews | Changes in a second review do not alter the first. Cross-review item/PDF access is rejected. |
| 9. Suggestion navigation and highlights at zoom | Real-browser tests render bundled PDF.js, inspect selectable source text, navigate physical pages, and compare normalized highlight coordinates across zoom and all four cropped-page rotations. |
| 10. Groups, time points, zero, missingness and conflict | Tests preserve narrative group labels, separate observations, numeric zero, boolean false and dates. Unsupported approvals fail; conflicting evidence requires explicit resolution. |
| 11. Current design drives export | Reopened workbooks match active column names/order and one row per included paper. Further template imports append without position collisions. |
| 12. Trace exported values to sources and decisions | Export sheets are reopened and checked for exact quotations, physical page numbers, stable paper/item IDs, reviewer identity and history. |

## Additional checks

- Formula-like input remains a string; hyperlinks only use web URL schemes.
- Duplicate PDF bytes reuse the existing paper. Image-only pages remain visible; malformed PDFs receive a processing error.
- Every word's offsets reproduce its exact extracted text, and coordinates remain within visible page bounds.
- Invalid import batches leave no partially imported items.
- Local-origin/host restrictions and cross-review boundaries are exercised.
- Application dependencies are pinned, bundled PDF.js has retained licenses, and package builds include local static assets.
- All synthetic export worksheets were rendered and visually inspected; main narratives, source quotations, dictionary fields, and audit metadata remain readable. Native workbook reopening verifies numeric/boolean/date types independently of the rendering tool.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not browser"
$env:RUN_BROWSER_TESTS = "1"
$env:PLAYWRIGHT_CHANNEL = "msedge"
.\.venv\Scripts\python.exe -m pytest -m browser
.\.venv\Scripts\python.exe -m build --no-isolation
```

Omit `PLAYWRIGHT_CHANNEL` when using Playwright's installed Chromium. See README for browser installation. CI runs the non-browser suite on Windows/Linux with Python 3.11/3.12 and the browser suite on Linux.

## Limits of verification

Automated tests establish application behavior on these fixtures, not the scientific correctness of future extractions. Table reading order, unusual fonts, embedded images and previously OCR-processed PDFs still require source inspection. The original PDF is authoritative; pdfplumber reconstructs whitespace between source words. OCR automation and independent reviewer adjudication remain later milestones.

## Concept retrieval update (2026-09-16)

All 89 tests passed with `RUN_BROWSER_TESTS=1` and `PLAYWRIGHT_CHANNEL=msedge`. New cases cover age/frequency statements without literal keywords, related wording for custom items, unrelated passages, group/table context, multiple frequency values, first-page bylines versus citations/references/affiliations/acknowledgments, multiline membership labels, metadata conflicts, metadata acceptance blocking, source geometry, decisions after description edits and restart, and upgrades preserving saved fields and source pages. A real browser verifies explanations, byline highlights and metadata separation. The synthetic PDF and browser screen were rendered and visually checked.

The existing searchable Lin et al. 2023 PDF was also checked locally: the two-line byline returns six candidate names while retaining the exact text and excluding the membership label from proposed names. This spot check is not an estimate of accuracy across research PDFs. Kim & Lee 2020 remains image-only and needs OCR or manual review. No dependencies or models were downloaded.

## Repeatable outcome extraction and template exports (2026-10-16)

The complete local suite passed with `RUN_BROWSER_TESTS=1` and `PLAYWRIGHT_CHANNEL=msedge`: **296 passed, 3 skipped, 2 upstream deprecation warnings**. This includes 26 browser journeys. The three skipped tests require optional cached semantic models. Python compilation and JavaScript syntax checks also passed.

Coverage includes template classification into study and outcome items; independent values and selected source evidence for multiple outcomes per paper; review/paper/outcome ownership boundaries; numerical zero, sample size, standard deviation and correlation validation; inclusion changes; soft removal and restoration; restart persistence; unsaved edits; and delayed responses during rapid context switches. Workbook checks reopen all four sheets, verify current column order and styles, prevent formula injection, and check that verification rows retain the corresponding outcome identity and selected source locations. Mixed study/outcome sections sharing a worksheet name are covered.

All four sheets of a populated synthetic export using the supplied template's styles were rendered and visually inspected. The live application's combined download was also reopened: the two extraction sheets precede their matching verification sheets, with 23 study rows and no invented outcome rows. The supplied template was imported as 22 study items and 8 outcome items after a database backup; its example rows were not imported as evidence. No scientific values were extracted or approved during setup. The 23 uploaded source PDFs still require the review's manual scan step before source selection.
