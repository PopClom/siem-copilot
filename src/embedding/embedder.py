"""
embedding/embedder.py
---------------------
Wraps a local sentence-transformers model and exposes a simple batch API.

Design decisions
----------------
* The model is loaded lazily on first use so that importing this module
  never triggers a large download.
* Batch size is taken from config; you can tune it per device.
* The module returns plain Python lists (list[list[float]]) rather than
  NumPy arrays to keep the public API dependency-free.
* For BGE models we prepend the recommended query/passage prefix
  automatically.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config.settings import EmbeddingConfig
from src.models import EventWindow

logger = logging.getLogger(__name__)

# Lazy import — only pulled in when embed() is called
_sentence_transformers: Optional[object] = None


def _get_st_module():
    global _sentence_transformers
    if _sentence_transformers is None:
        try:
            import sentence_transformers as st
            _sentence_transformers = st
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            ) from exc
    return _sentence_transformers


# ---------------------------------------------------------------------------
# Embedder class
# ---------------------------------------------------------------------------

class Embedder:
    """
    Thin wrapper around a sentence-transformers model.

    Usage
    -----
        embedder = Embedder(config.embedding)
        vectors = embedder.embed_windows(windows)
    """

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self._model = None

    # ------------------------------------------------------------------
    # Lazy model loading
    # ------------------------------------------------------------------

    @property
    def model(self):
        if self._model is None:
            st = _get_st_module()
            logger.info(
                "Loading embedding model '%s' on %s …",
                self.config.model, self.config.device,
            )
            self._model = st.SentenceTransformer(  # type: ignore[attr-defined]
                self.config.model,
                device=self.config.device,
            )
            logger.info("Embedding model loaded. Dimension: %d", self.dimension)
        return self._model

    @property
    def dimension(self) -> int:
        return self.model.get_embedding_dimension()

    # ------------------------------------------------------------------
    # BGE instruction prefix
    # ------------------------------------------------------------------

    _BGE_PASSAGE_PREFIX = "Represent this security log window for retrieval: "

    def _maybe_add_prefix(self, texts: list[str]) -> list[str]:
        """BGE models recommend a passage prefix for indexing."""
        if "bge" in self.config.model.lower():
            return [self._BGE_PASSAGE_PREFIX + t for t in texts]
        return texts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of strings in batches.
        Returns a list of float vectors (one per input text).
        """
        if not texts:
            return []

        prefixed = self._maybe_add_prefix(texts)

        logger.debug("Embedding %d texts (batch_size=%d) …", len(texts), self.config.batch_size)

        vectors = self.model.encode(
            prefixed,
            batch_size=self.config.batch_size,
            show_progress_bar=len(texts) > 200,
            convert_to_numpy=True,
            normalize_embeddings=True,   # cosine similarity works best with L2-normalised vecs
        )

        return vectors.tolist()

    def embed_windows(self, windows: list[EventWindow]) -> list[list[float]]:
        """
        Convenience wrapper: embed the aggregated_text of each window.
        Skips windows with empty text and logs a warning.
        """
        texts: list[str] = []
        valid_indices: list[int] = []

        for i, w in enumerate(windows):
            if w.aggregated_text.strip():
                texts.append(w.aggregated_text)
                valid_indices.append(i)
            else:
                logger.warning("Window %s has empty aggregated_text; skipping.", w.id)

        if not texts:
            return [[] for _ in windows]

        vectors = self.embed_texts(texts)

        # Re-align with original window list (empty vector for skipped windows)
        result = [[] for _ in windows]
        for idx, vec in zip(valid_indices, vectors):
            result[idx] = vec

        return result
