from __future__ import annotations

import hashlib
import math
from typing import Protocol

from openai import AsyncOpenAI, OpenAIError

DIMENSIONS = 1024


class EmbeddingError(Exception):
    pass


class Embedder(Protocol):
    model_id: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed documents."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query in the same representation as documents."""
        ...


class OpenRouterEmbedder:
    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://openrouter.ai/api/v1",
                 timeout: float = 15.0):
        # A short timeout keeps a slow/hung embeddings provider from stalling the
        # run (recall/search are best-effort and degrade to a skip on failure)
        # instead of inheriting the SDK's 600s default.
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model
        self.model_id = f"openrouter:{model}"
        self.dimensions = DIMENSIONS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            # Force float: the SDK defaults to encoding_format="base64", which
            # Google AI Studio (via OpenRouter) rejects with a 200 + empty-data
            # error body, making the SDK raise a bare ValueError we can't catch.
            resp = await self.client.embeddings.create(
                model=self.model, input=texts, dimensions=DIMENSIONS,
                encoding_format="float")
        except OpenAIError as e:
            raise EmbeddingError(f"embedding call failed: {e}") from e
        data = sorted(resp.data, key=lambda d: d.index)
        vectors = [d.embedding for d in data]
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"provider returned {len(vectors)} vectors for {len(texts)} inputs")
        for v in vectors:
            if len(v) != DIMENSIONS:
                raise EmbeddingError(
                    f"provider returned {len(v)}-dim vector, expected {DIMENSIONS}")
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class FakeEmbedder:
    """Deterministic embedder for tests: vectors derived from token hashes so
    that texts sharing words are similar and identical texts match exactly."""

    def __init__(self, dims: int = 32, model_id: str = "fake"):
        self.dims = dims
        self.dimensions = dims
        self.model_id = model_id
        self.calls: list[list[str]] = []

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for tok in text.lower().split():
            h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:4], "big")
            v[h % self.dims] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


def embedder_from_config(api_key: str, model: str) -> Embedder | None:
    return OpenRouterEmbedder(api_key, model) if api_key else None
