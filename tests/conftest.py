"""Unit/browser tests stay deterministic regardless of a developer's model cache.

Real local model integration is separately opt-in with RUN_SEMANTIC_TESTS=1.
Synthetic encoder tests still exercise batching, persistence and invalidation.
"""
import pytest
from review_extractor.hybrid.semantic import LocalSemantic


@pytest.fixture(autouse=True)
def deterministic_semantic_backend(request, monkeypatch):
    if request.node.get_closest_marker("semantic"):
        return
    def load(self):
        if self.encoder is None:
            self.error = self.error or "Local embeddings disabled for deterministic test; using fast retrieval."
        return self.encoder
    monkeypatch.setattr(LocalSemantic, "_load", load)
