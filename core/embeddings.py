"""OllamaEmbedder — batch text embedding via Ollama /api/embed (WO-4 P2)."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_GUIDANCE = "Ollama embedder unavailable. Run: ollama pull nomic-embed-text"
_CACHE_MAX = 512  # bounded warm cache


def _load_base_url() -> str:
    """Read llm_url from config/api_keys.json; fall back to localhost."""
    keys_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    try:
        data = json.loads(keys_path.read_text(encoding="utf-8"))
        url = data.get("llm_url", "")
        if url:
            return url.rstrip("/")
    except Exception:
        pass
    return "http://localhost:11434"


def _l2_norm(vec: list[float]) -> list[float]:
    """L2-normalize a vector; return unchanged if norm is 0."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class OllamaEmbedder:
    """Embed text via Ollama /api/embed. Batch, L2-norm, warm cache."""

    def __init__(self, model: str = "nomic-embed-text", base_url: Optional[str] = None) -> None:
        self._model = model
        self._base_url = (base_url or _load_base_url()).rstrip("/")
        self._cache: dict[str, list[float]] = {}  # bounded warm cache
        self._dim: Optional[int] = None

    # ------------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts; L2-norm each; use warm cache for repeats."""
        if not texts:
            return []
        if not self.is_available():
            raise RuntimeError(_GUIDANCE)

        # Split into cached / uncached.
        result: list[Optional[list[float]]] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []
        for i, text in enumerate(texts):
            if text in self._cache:
                result[i] = self._cache[text]
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            resp = requests.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": uncached_texts},
                timeout=30,
            )
            resp.raise_for_status()
            embeddings = resp.json()["embeddings"]
            if len(embeddings) != len(uncached_texts):
                raise RuntimeError(
                    f"Ollama returned {len(embeddings)} embeddings for {len(uncached_texts)} inputs"
                )
            for idx, vec in zip(uncached_indices, embeddings):
                normed = _l2_norm(vec)
                # Evict oldest entries when cache is full.
                if len(self._cache) >= _CACHE_MAX:
                    self._cache.pop(next(iter(self._cache)))
                text = texts[idx]
                self._cache[text] = normed
                result[idx] = normed

        return [r for r in result if r is not None]  # type: ignore[return-value]

    def dim(self) -> int:
        """Return the embedding dimension via a one-token probe (cached)."""
        if self._dim is None:
            vecs = self.embed(["x"])
            self._dim = len(vecs[0])
        return self._dim

    def is_available(self) -> bool:
        """Return True iff Ollama is reachable AND the model is present."""
        try:
            resp = requests.get(f"{self._base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            base_name = self._model.split(":")[0]
            return any(
                m.get("name", "").startswith(base_name) for m in models
            )
        except Exception:
            return False
