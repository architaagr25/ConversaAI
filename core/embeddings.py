"""
Text to vectors, hosted or local.

Hosted is the default: the local models small enough to run here don't cover
Tagalog. Numbers in results/embedding_comparison.txt. Local stays available for
offline work, but only English and Indonesian can be trusted on it.

Watch the signature guard at the bottom. Vectors from different models aren't
comparable, and searching an index with the wrong one returns plausible garbage
rather than raising.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np

from core.config import settings
from core.timing import track

log = logging.getLogger(__name__)

# The hosted service accepts a list per request. Batching keeps indexing to a
# handful of calls instead of one per chunk, which matters on a throttled tier.
HOSTED_BATCH = 50


class Embedder(ABC):
    """A source of vectors, identified so the wrong one can't query an index."""

    @property
    @abstractmethod
    def signature(self) -> str:
        """Model and size. Stored alongside any index built with it."""

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray: ...

    def encode_query(self, text: str) -> np.ndarray:
        return self.encode([text], is_query=True)[0]

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        return self.encode(texts, is_query=False)


class HostedEmbedder(Embedder):
    """Vectors from the hosted service. Wide language coverage, network cost."""

    def __init__(self) -> None:
        self.model = settings.embedding_model
        self.dims = settings.embedding_dims
        self._client = None

    @property
    def signature(self) -> str:
        return f"gemini:{self.model}:{self.dims}"

    @property
    def dimensions(self) -> int:
        return self.dims

    @property
    def client(self):
        if self._client is None:
            from google import genai

            if not settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY is not set")
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        from google.genai import types

        if not texts:
            return np.zeros((0, self.dims), dtype=np.float32)

        # Questions and passages embed differently; saying which improves matching.
        task = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
        config = types.EmbedContentConfig(
            task_type=task, output_dimensionality=self.dims
        )

        vectors: list[list[float]] = []
        for start in range(0, len(texts), HOSTED_BATCH):
            batch = texts[start : start + HOSTED_BATCH]
            delay = 2.0
            for attempt in range(4):
                try:
                    with track("embed", detail=f"{len(batch)} texts"):
                        response = self.client.models.embed_content(
                            model=self.model, contents=batch, config=config
                        )
                    vectors.extend(e.values for e in response.embeddings)
                    break
                except Exception as exc:
                    throttled = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                    if throttled and attempt < 3:
                        log.warning(
                            "embedding throttled, waiting",
                            extra={"wait_s": delay, "batch": len(batch)},
                        )
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise

        # Normalised here so similarity is a plain dot product downstream.
        return _normalise(np.asarray(vectors, dtype=np.float32))


class LocalEmbedder(Embedder):
    """Vectors computed on this machine. Fast and offline, but weak on Tagalog."""

    def __init__(self) -> None:
        self.model_name = settings.embedding_local_model
        self._model = None
        self._dims: int | None = None

    @property
    def signature(self) -> str:
        return f"local:{self.model_name}:{self.dimensions}"

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(
                model_name=self.model_name, cache_dir=str(settings.cache_path)
            )
        return self._model

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            self._dims = len(next(iter(self.model.embed(["size probe"]))))
        return self._dims

    def encode(self, texts: list[str], is_query: bool = False) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimensions), dtype=np.float32)
        with track("embed_local", detail=f"{len(texts)} texts"):
            array = np.asarray(list(self.model.embed(texts)), dtype=np.float32)
        return _normalise(array)


def _normalise(array: np.ndarray) -> np.ndarray:
    """Scale each vector to unit length so similarity is a dot product."""
    if array.size == 0:
        return array
    lengths = np.linalg.norm(array, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0  # a zero vector would put NaN through the index
    return array / lengths


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """The configured embedder, created once.

    Falls back to local if hosted is unreachable, and complains loudly about it:
    the fallback can't do Tagalog and invalidates any existing index.
    """
    if settings.embedding_provider == "local":
        return LocalEmbedder()

    hosted = HostedEmbedder()
    try:
        hosted.encode(["startup check"], is_query=True)
        return hosted
    except Exception as exc:
        log.error(
            "hosted embeddings unavailable, falling back to the local model. "
            "Tagalog retrieval will not work, and any index built with the "
            "hosted model must be rebuilt before it can be searched.",
            extra={"reason": str(exc)[:160]},
        )
        return LocalEmbedder()


class SignatureMismatch(RuntimeError):
    """Raised when an index is searched with a different model than built it."""


def require_matching(index_signature: str, embedder: Embedder | None = None) -> None:
    """Refuse to search an index built by a different model.

    Without this the search runs and returns results. They're just wrong.
    """
    current = (embedder or get_embedder()).signature
    if index_signature and index_signature != current:
        raise SignatureMismatch(
            f"this index was built with {index_signature!r} but the current "
            f"embedder is {current!r}. Rebuild the index, or set the previous "
            f"model in .env."
        )
