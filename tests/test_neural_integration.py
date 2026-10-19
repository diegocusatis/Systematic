"""Offline model adapters and provenance across the neural shortlist."""
from review_extractor.hybrid.neural_reranker import LocalReranker, rerank_shortlist
from review_extractor.hybrid.chunking import chunk_document
from review_extractor.hybrid.semantic import LocalSemantic
from review_extractor.paddle_ocr import page_from_blocks, augment_mixed_page
from review_extractor.retrieval import candidate, search_item
from test_retrieval import item, page


class FakeCrossEncoder:
    def __init__(self):
        self.calls = []

    def predict(self, pairs, **kwargs):
        self.calls.append((list(pairs), kwargs))
        return [3.0 if "therapeutic rTMS" in passage else -2.0 for _, passage in pairs]


def test_paddle_blocks_keep_exact_text_and_real_region_boxes():
    result = dict(width=100., height=100., status="ocr_required", error="No text")
    blocks = [
        dict(block_id=0, block_label="text", block_content="Methods", block_bbox=[10, 10, 80, 30]),
        dict(block_id=1, block_label="table", block_content="10 Hz | 90% RMT", block_bbox=[20, 50, 180, 90]),
        dict(block_id=2, block_label="image", block_content="invented description", block_bbox=[0, 0, 200, 200]),
    ]
    assert page_from_blocks(blocks, result, (200, 200))
    assert result["text"] == "Methods\n\n10 Hz | 90% RMT"
    assert result["ocr"] and result["ocr_confidence"] is None
    assert len(result["ocr_blocks"]) == 2
    for word in result["words"]:
        assert result["text"][word["start"]:word["end"]] == word["text"]
    assert result["words"][1]["x0"] == 10 and result["words"][1]["x1"] == 90


def test_paddle_table_rows_keep_header_context_and_source_offsets():
    result = dict(page_number=1, width=100., height=100., status="ocr_required", error="")
    table = "<table><tr><td>Outcome</td><td>Active group</td></tr><tr><td>LEMS</td><td>4.6 (2.7)</td></tr></table>"
    assert page_from_blocks([dict(block_id=2, block_label="table", block_content=table,
                                  block_bbox=[0, 0, 200, 100])], result, (200, 200))
    rows = [row for row in chunk_document([result]) if row.get("kind") == "ocr_table_row"]
    assert len(rows) == 2
    assert rows[1]["table_row"] == 1 and "Active group" in rows[1]["semantic_context"]
    assert "LEMS" in rows[1]["semantic_context"] and "4.6 (2.7)" in rows[1]["quote"]
    assert result["text"][rows[1]["start"]:rows[1]["end"]] == rows[1]["quote"]
    assert rows[1]["boxes"]


def test_mixed_page_adds_only_uncovered_ocr_region(monkeypatch):
    blocks = [
        dict(block_id=0, block_label="text", block_content="Methods",
             block_bbox=[0, 0, 100, 40]),
        dict(block_id=1, block_label="table", block_content="Outcome | Active\nLEMS | 4.6 (2.7)",
             block_bbox=[0, 80, 200, 160]),
    ]
    monkeypatch.setattr("review_extractor.paddle_ocr._predict", lambda source: (blocks, (200, 200)))
    result = dict(page_number=1, width=100., height=100., text="Methods",
                  words=[dict(text="Methods", start=0, end=7, x0=0., top=0., x1=50., bottom=20.)],
                  status="ok", ocr=False)
    assert augment_mixed_page(None, result)
    assert result["text"].startswith("Methods\n\nOutcome")
    assert result["text"][:7] == "Methods" and not result["ocr"]
    assert len(result["ocr_blocks"]) == 1
    rows = [row for row in chunk_document([result]) if row.get("kind") == "ocr_table_row"]
    assert len(rows) == 2 and all(row["ocr"] for row in rows)
    assert all(result["text"][row["start"]:row["end"]] == row["quote"] for row in rows)
    start = result["ocr_blocks"][0]["start"]
    end = result["ocr_blocks"][0]["end"]
    legacy = candidate(result, start, end, "table text", .5, "keyword")
    assert legacy["ocr"] and result["text"][start:end] == legacy["quote"]


def test_reranker_cache_and_source_provenance(tmp_path):
    model = FakeCrossEncoder()
    reranker = LocalReranker(tmp_path, encoder=model, enabled=True)
    rows = [
        dict(chunk_id="bad", quote="EMG sampling at 10 Hz", context="EMG sampling at 10 Hz",
             page_number=1, start=0, rank_score=.7,
             scores=dict(semantic_score=.4, keyword_score=.6, type_score=.5)),
        dict(chunk_id="good", quote="therapeutic rTMS used 10 Hz", context="therapeutic rTMS used 10 Hz",
             page_number=2, start=0, rank_score=.6,
             scores=dict(semantic_score=.5, keyword_score=.5, type_score=.5)),
    ]
    original_quotes = [row["quote"] for row in rows]
    considered, warning = rerank_shortlist(item("Stimulation frequency"), rows, reranker)
    assert warning is None and set(considered) == {"bad", "good"}
    assert rows[0]["chunk_id"] == "good" and rows[0]["scores"]["neural_reranker_raw"] == 3.
    assert {row["quote"] for row in rows} == set(original_quotes)
    assert len(model.calls) == 1
    assert reranker.scores("same", ["passage"])[1] is None
    calls = len(model.calls)
    assert reranker.scores("same", ["passage"])[1] is None
    assert len(model.calls) == calls


def test_qwen_query_has_instruction_but_document_does_not(tmp_path):
    class FakeEmbedding:
        def __init__(self): self.calls = []
        def encode(self, texts, **kwargs):
            self.calls.append(list(texts))
            return [[1., 0.] for _ in texts]
    encoder = FakeEmbedding()
    semantic = LocalSemantic(tmp_path, model="Qwen/Qwen3-Embedding-0.6B", encoder=encoder)
    similarities, warning = semantic.similarities("stimulation frequency", ["10 Hz rTMS"])
    assert warning is None and similarities == [1.]
    assert encoder.calls[0] == ["10 Hz rTMS"]
    assert encoder.calls[1][0].startswith("Instruct: Find the exact passage")
    assert encoder.calls[1][0].endswith("Query: stimulation frequency")


def test_search_exposes_neural_shortlist_and_preserves_pdf_span(tmp_path):
    model = FakeCrossEncoder()
    reranker = LocalReranker(tmp_path, encoder=model, enabled=True)
    source = [page("Methods\nEMG sampling used 10 Hz.\n\nTherapeutic rTMS used 10 Hz.")]
    result = search_item(source, item("Stimulation frequency", search_mode="fast"),
                         reranker=reranker, diagnostics=True)
    assert result["diagnostics"]["neural_shortlist"]
    assert any(row["scores"].get("neural_reranker_raw") is not None for row in result["matches"])
    assert all(source[0]["text"][row["start"]:row["end"]] == row["quote"]
               for row in result["matches"])
