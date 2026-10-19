# Local neural retrieval and evidence evaluation

Systematic keeps PDF text and page coordinates as the source of record. These
models are opt-in, run locally after their weights have been downloaded, and
only change OCR transcription or suggestion ranking. They never approve evidence
or values. Existing reviews, decisions and Excel exports remain usable.

## Models and activation

The base installation still works without neural packages. In the application's
Python environment, install the semantic extra and download the two text models
explicitly while online:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[semantic,ocr]"
.\.venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('Qwen/Qwen3-Embedding-0.6B'); CrossEncoder('Alibaba-NLP/gte-reranker-modernbert-base')"
```

Then launch from the same PowerShell session:

```powershell
$env:SYSTEMATIC_EMBEDDING_MODEL = 'Qwen/Qwen3-Embedding-0.6B'
$env:SYSTEMATIC_RERANKER = '1'
$env:SYSTEMATIC_RERANKER_MODEL = 'Alibaba-NLP/gte-reranker-modernbert-base'
.\.venv\Scripts\python.exe -m review_extractor
```

Choose **Intelligent** for an item to use Qwen embeddings. The reranker also
works on Fast-mode candidates. Both load from local files only during a scan;
missing weights produce a visible warning and leave the existing ranker in use.
The embedding cache is keyed by model, revision and source text. Reranker scores
are cached by model, revision, query and passage. No PDF is sent to a model
service. To try another local cross-encoder, set `SYSTEMATIC_RERANKER_MODEL`
to a cached model ID or immutable local directory. `SYSTEMATIC_RERANKER_WEIGHT`
controls rank fusion (default `0.35`, allowed `0`–`0.5`); tune it on development
papers only.

For PaddleOCR-VL-1.6, follow the [official installation guide](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html)
for this computer's CPU or GPU, then install the optional parser package:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[paddle-ocr]"
$env:SYSTEMATIC_OCR = '1'
$env:SYSTEMATIC_OCR_ENGINE = 'paddle'
```

For a strictly offline run, set `SYSTEMATIC_PADDLE_LAYOUT_DIR` and
`SYSTEMATIC_PADDLE_VL_DIR` to already downloaded, local model directories.
Otherwise an explicit first-time model download can be enabled with
`SYSTEMATIC_PADDLE_ALLOW_DOWNLOAD=1`. By default, PaddleOCR-VL runs when a PDF
page has no useful native text. Its returned region boxes are retained at **block**
precision; Systematic never fabricates word coordinates. Table rows are indexed
with their headers when the OCR output has row structure. If Paddle fails and
Tesseract is installed, Tesseract is tried and the fallback is shown. Reprocess
previously imported scanned PDFs with **Retry PDF processing**, then scan the
paper. Model changes do not automatically rescan private papers.

For a PDF page that has native text but also contains an image-only table or
figure text, set `SYSTEMATIC_OCR_MIXED=1` before reprocessing. The parser adds
only regions without native PDF words. Existing native text offsets stay fixed;
new model text is appended and marked as OCR at the candidate level.

## Measure Recall@3 before choosing defaults

Create a local, unlabeled annotation template for a review:

```powershell
.\.venv\Scripts\python.exe -m review_extractor.evaluation `
  --data-dir .\.review-data --review-id REVIEW_ID --create-template `
  --output .\outputs\gold-template.json
```

For each paper/item pair, a human reviewer sets `split` (`train`,
`development`, or `test`), `status` (`answered`, `no_answer`, or `unlabeled`),
`annotator`, and any `gold_spans`. Related reports and duplicate PDFs must share
one `group_id` and one split. An answered span uses either physical
`page_number`, `start`, `end`, and exact `quote` offsets in the saved page text,
or `page_number` plus a PDF-space `[x0, top, x1, bottom]` `box` for an answer
visible in a page whose OCR failed. Set `kind` to `prose`, `table`, or `ocr`.
Keep human annotations and private PDFs in an ignored local data directory.

Run a frozen test split:

```powershell
.\.venv\Scripts\python.exe -m review_extractor.evaluation `
  --data-dir .\.review-data --gold .\outputs\human-gold.json `
  --split test --output .\outputs\retrieval-test-report.json
```

The report gives item-level Recall@1/3/5, MRR, pool and neural-shortlist
coverage, field/paper/table/OCR slices, 95% intervals, and trace-backed miss
categories. It rejects changed source versions and split leakage. Saved
suggestions, accepted evidence and feedback are excluded from the rerun. The
default adequacy policy requires at least 100 answered pairs across 20
independent paper groups before the report may mark the 95% point target met;
the policy can be predeclared in the gold file. This is a minimum sample gate,
not a guarantee of narrow uncertainty. General document model benchmarks and
the synthetic tests in this repository do not establish Systematic's Recall@3
on your review papers.
