from types import SimpleNamespace as NS

import pytest

from forge.llm.embeddings import (
    DIMENSIONS, EmbeddingError, OpenRouterEmbedder, embedder_from_config,
)


def make_embedder(data):
    """Stub the AsyncOpenAI embeddings endpoint; `data` is the resp.data list."""
    emb = OpenRouterEmbedder(api_key="k", model="m")

    async def create(**kwargs):
        return NS(data=data)

    emb.client = NS(embeddings=NS(create=create))
    return emb


def item(index, vector):
    return NS(index=index, embedding=vector)


async def test_returns_vectors_sorted_by_index():
    emb = make_embedder([
        item(1, [1.0] * DIMENSIONS),
        item(0, [0.0] * DIMENSIONS),
    ])
    vecs = await emb.embed(["a", "b"])
    assert vecs == [[0.0] * DIMENSIONS, [1.0] * DIMENSIONS]


async def test_empty_input_skips_call():
    emb = make_embedder([])
    assert await emb.embed([]) == []


async def test_wrong_vector_count_raises():
    emb = make_embedder([item(0, [0.0] * DIMENSIONS)])  # only 1 for 2 inputs
    with pytest.raises(EmbeddingError, match="1 vectors for 2 inputs"):
        await emb.embed(["a", "b"])


async def test_wrong_vector_dimensions_raises():
    emb = make_embedder([item(0, [0.0] * (DIMENSIONS - 1))])
    with pytest.raises(EmbeddingError, match=f"expected {DIMENSIONS}"):
        await emb.embed(["a"])


def test_embedder_from_config_requires_api_key():
    assert embedder_from_config("", "m") is None
    assert isinstance(embedder_from_config("k", "m"), OpenRouterEmbedder)
