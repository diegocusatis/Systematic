"""Optional offline cross-encoder for a bounded evidence shortlist."""
import hashlib
import json
import math
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import threading

from .models import item_spec, query_text


class LocalReranker:
    def __init__(self, cache_dir, model=None, encoder=None, enabled=None):
        self.cache_dir = Path(cache_dir)
        self.model_name = model or os.environ.get(
            "SYSTEMATIC_RERANKER_MODEL", "Alibaba-NLP/gte-reranker-modernbert-base")
        self.revision = os.environ.get("SYSTEMATIC_RERANKER_REVISION", "main")
        self.enabled = (os.environ.get("SYSTEMATIC_RERANKER", "").lower() in {"1", "true"}
                        if enabled is None else bool(enabled))
        self.encoder = encoder
        self.error = None
        self.lock = threading.RLock()

    def _load(self):
        if self.encoder is None and self.error is None:
            try:
                from sentence_transformers import CrossEncoder
                self.encoder = CrossEncoder(self.model_name, revision=self.revision,
                    local_files_only=True, trust_remote_code=False)
            except Exception as exc:
                self.error = (f"Local reranker unavailable ({type(exc).__name__}). "
                              "Using the existing ranking; cache the model explicitly and restart.")
        return self.encoder

    def scores(self, query, passages):
        """Return raw model scores; callers fuse ranks, never treat them as probabilities."""
        if not self.enabled or not passages:
            return None, None
        with self.lock:
            if self._load() is None:
                return None, self.error
            keys = [hashlib.sha256(json.dumps(
                [self.model_name, self.revision, query, passage], ensure_ascii=False
            ).encode()).hexdigest() for passage in passages]
            values, missing = {}, {}
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self.cache_dir / "reranker.sqlite3")) as db, db:
                    db.execute("CREATE TABLE IF NOT EXISTS scores (key TEXT PRIMARY KEY, score REAL NOT NULL)")
                    for key, passage in zip(keys, passages):
                        if key in values or key in missing:
                            continue
                        hit = db.execute("SELECT score FROM scores WHERE key=?", (key,)).fetchone()
                        if hit is None:
                            missing[key] = passage
                        else:
                            values[key] = float(hit[0])
                    if missing:
                        raw = self.encoder.predict([(query, passage) for passage in missing.values()],
                            batch_size=8, show_progress_bar=False)
                        if len(raw) != len(missing):
                            raise ValueError("Incomplete reranker batch")
                        for key, score in zip(missing, raw):
                            score = float(score)
                            if not math.isfinite(score):
                                raise ValueError("Non-finite reranker score")
                            values[key] = score
                            db.execute("INSERT OR REPLACE INTO scores VALUES (?,?)", (key, score))
                return [values[key] for key in keys], None
            except Exception as exc:
                return None, f"Local reranking failed ({type(exc).__name__}); using existing ranking."


def passage_context(row):
    """Give the model labels and neighbors without changing the evidence quote."""
    parts = []
    for label, value in (("Section", row.get("section")),
                         ("Table", row.get("table_title")),
                         ("Row", row.get("row_header")),
                         ("Column", row.get("column_header"))):
        if value:
            parts.append(f"{label}: {value}")
    parts.append("Passage: " + str(row.get("context") or row["quote"]))
    return "\n".join(parts)


def shortlist(candidates, limit=80):
    """Union baseline and independent retrieval signals before expensive inference."""
    selected = {}
    for key, count in (("rank_score", 40), ("semantic_score", 24),
                       ("keyword_score", 16), ("type_score", 16)):
        ordered = sorted(candidates, key=lambda row: -float(
            row["rank_score"] if key == "rank_score" else row["scores"].get(key) or 0))
        for row in ordered[:count]:
            if key != "rank_score" and not row["scores"].get(key):
                continue
            selected.setdefault(row["chunk_id"], row)
            if len(selected) >= limit:
                return list(selected.values())
    return list(selected.values())


def rerank_shortlist(item, candidates, reranker, *, limit=80, weight=None):
    """Fuse a bounded cross-encoder rank with the existing, inspectable score."""
    if reranker is None or not reranker.enabled or not candidates:
        return [], None
    considered = shortlist(candidates, limit)
    raw, warning = reranker.scores(query_text(item_spec(item)),
                                   [passage_context(row) for row in considered])
    if raw is None:
        return [], warning
    if weight is None:
        try:
            weight = float(os.environ.get("SYSTEMATIC_RERANKER_WEIGHT", ".35"))
        except ValueError:
            weight = .35
    weight = max(0., min(.5, weight))
    neural_order = sorted(range(len(raw)), key=lambda index: (-raw[index], index))
    neural_rank = {index: rank for rank, index in enumerate(neural_order)}
    denominator = max(1, len(raw)-1)
    for index, row in enumerate(considered):
        score = .5 if len(raw) == 1 or len(set(raw)) == 1 else 1-neural_rank[index]/denominator
        previous = row["rank_score"]
        row["scores"].update(pre_neural_score=previous,
            neural_reranker_raw=round(raw[index], 6), neural_reranker_score=round(score, 6))
        row["rank_score"] = round((1-weight)*previous + weight*score, 6)
        row["scores"]["combined_score"] = row["rank_score"]
        row["reranker_model"] = reranker.model_name
        row["reranker_weight"] = weight
    candidates.sort(key=lambda row: (-row["rank_score"], row.get("page_number") or 0,
                                     row.get("start") or 0))
    for rank, row in enumerate(candidates, 1):
        row["rank"] = rank
    return [row["chunk_id"] for row in considered], None
