"""Lazy offline sentence embeddings with content-addressed persistent caching."""
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import threading

from .chunking import VERSION

_MODELS = {}
_MODEL_LOCK = threading.RLock()


class LocalSemantic:
    def __init__(self, cache_dir, model=None, encoder=None):
        self.cache_dir = Path(cache_dir)
        self.model_name = model or os.environ.get("SYSTEMATIC_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        self.revision = os.environ.get("SYSTEMATIC_EMBEDDING_REVISION", "main")
        self.encoder = encoder
        self.error = None
        self.lock = threading.RLock()
        self.memory = {}
        self.migrated = set()

    def _load(self):
        if self.encoder is None and self.error is None:
            try:
                from sentence_transformers import SentenceTransformer
                with _MODEL_LOCK:
                    key = (self.model_name, self.revision)
                    if key not in _MODELS:
                        _MODELS[key] = SentenceTransformer(self.model_name, revision=self.revision,
                            local_files_only=True, trust_remote_code=False)
                    self.encoder = _MODELS[key]
                config = getattr(getattr(self.encoder[0], "auto_model", None), "config", None)
                self.revision = getattr(config, "_commit_hash", None) or self.revision
            except Exception as exc:
                self.error = f"Local embeddings unavailable ({type(exc).__name__}). Using fast retrieval. Install the semantic extra and cache a model, then restart."
        return self.encoder

    def _vectors(self, texts):
        # Cache individual texts so a new legacy/custom span never invalidates
        # the document batch. Encode all cache misses together, not per excerpt.
        keys = [hashlib.sha256(json.dumps([VERSION, self.model_name, self.revision, t], ensure_ascii=False).encode()).hexdigest() for t in texts]
        vectors, missing = {}, {}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.cache_dir / "embeddings.sqlite3")) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS text_embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
            for key, text in zip(keys, texts):
                if key in vectors or key in missing: continue
                if key in self.memory:
                    vectors[key] = self.memory[key]
                    continue
                hit = db.execute("SELECT vector FROM text_embeddings WHERE key=?", (key,)).fetchone()
                if hit: vectors[key] = json.loads(hit[0])
                else: missing[key] = text
            if missing:
                encoded = self.encoder.encode(list(missing.values()), batch_size=32, normalize_embeddings=True, show_progress_bar=False)
                encoded = encoded.tolist() if hasattr(encoded, "tolist") else encoded
                if len(encoded) != len(missing): raise ValueError("Incomplete embedding batch")
                for key, vector in zip(missing, encoded):
                    vectors[key] = vector
                    db.execute("INSERT OR REPLACE INTO text_embeddings VALUES (?,?)", (key, json.dumps(vector, allow_nan=False)))
        self.memory.update(vectors)
        while len(self.memory) > 8192: self.memory.pop(next(iter(self.memory)))
        return [vectors[k] for k in keys]

    def reuse_document_cache(self, texts):
        """Reuse pre-upgrade whole-document batches without running inference."""
        with self.lock:
            path = self.cache_dir / "embeddings.sqlite3"
            if not path.exists() or self._load() is None: return
            identity = [VERSION, self.model_name, self.revision, texts]
            key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            if key in self.migrated: return
            with closing(sqlite3.connect(path)) as db, db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='embeddings'").fetchone():
                    hit = db.execute("SELECT vectors FROM embeddings WHERE key=?", (key,)).fetchone()
                    if hit:
                        vectors = json.loads(hit[0])
                        if len(vectors) != len(texts): return
                        db.execute("CREATE TABLE IF NOT EXISTS text_embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
                        for text, vector in zip(texts, vectors):
                            text_key = hashlib.sha256(json.dumps(identity[:3]+[text], ensure_ascii=False).encode()).hexdigest()
                            db.execute("INSERT OR IGNORE INTO text_embeddings VALUES (?,?)", (text_key,json.dumps(vector,allow_nan=False)))
            self.migrated.add(key)
            if len(self.migrated) > 64: self.migrated.pop()

    def similarities(self, query, texts):
        with self.lock:
            if not texts: return [], None
            if self._load() is None: return None, self.error
            try:
                documents = self._vectors(texts)
                # Qwen3 embedding models are trained with an instruction on the
                # query side only. Keep source passages unchanged for provenance.
                if "qwen3-embedding" in self.model_name.lower():
                    query = ("Instruct: Find the exact passage in a scientific paper "
                             "that supports this systematic-review extraction field.\nQuery: " + query)
                vector = self._vectors([query])[0]
                return [max(0., min(1., sum(a*b for a,b in zip(vector, row)))) for row in documents], None
            except Exception as exc:
                return None, f"Embedding indexing failed ({type(exc).__name__}); using fast retrieval."

    def similarity_matrix(self, queries, texts):
        """Compare feedback examples in one cached batch, never per candidate.

        Learning supplies its own masked representations. These cache entries do
        not replace the exact document/query embeddings used by base retrieval.
        """
        with self.lock:
            if not queries: return [], None
            if not texts: return [[] for _ in queries], None
            if self._load() is None: return None, self.error
            try:
                vectors = self._vectors(list(queries) + list(texts))
                documents = vectors[len(queries):]
                return [[max(0., min(1., sum(a*b for a,b in zip(query, row))))
                         for row in documents] for query in vectors[:len(queries)]], None
            except Exception as exc:
                return None, f"Feedback embeddings unavailable ({type(exc).__name__}); using unchanged baseline ranking."
