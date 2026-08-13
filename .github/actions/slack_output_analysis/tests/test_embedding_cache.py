"""Tests for reusing embeddings across a batch of errors.

The encoder is faked, so these check the caching contract rather than the model:
every text gets the vector its encoder produced, in the order asked for, and no
text is encoded twice.
"""

import pytest

import embedding_cache


@pytest.fixture(autouse=True)
def empty_cache():
    embedding_cache.clear_cache()
    yield
    embedding_cache.clear_cache()


class CountingEncoder:
    """Returns a distinct vector per text and records what it was asked for."""

    def __init__(self):
        self.batches = []

    def __call__(self, texts):
        self.batches.append(list(texts))
        return [f"vec:{text}" for text in texts]

    @property
    def encoded(self):
        return [text for batch in self.batches for text in batch]


def test_returns_a_vector_for_every_text_in_order():
    encoder = CountingEncoder()

    result = embedding_cache.encode_cached(["b", "a", "c"], encoder)

    assert result == ["vec:b", "vec:a", "vec:c"]


def test_second_call_encodes_only_what_is_new():
    encoder = CountingEncoder()
    embedding_cache.encode_cached(["centroid1", "centroid2"], encoder)

    result = embedding_cache.encode_cached(["new error", "centroid1", "centroid2"], encoder)

    assert result == ["vec:new error", "vec:centroid1", "vec:centroid2"]
    assert encoder.batches == [["centroid1", "centroid2"], ["new error"]]


def test_a_repeated_text_within_one_call_is_encoded_once():
    encoder = CountingEncoder()

    result = embedding_cache.encode_cached(["same", "other", "same"], encoder)

    assert result == ["vec:same", "vec:other", "vec:same"]
    assert encoder.encoded == ["same", "other"]


def test_a_fully_cached_call_does_not_reach_the_encoder():
    encoder = CountingEncoder()
    embedding_cache.encode_cached(["a", "b"], encoder)

    embedding_cache.encode_cached(["a", "b"], encoder)

    assert encoder.batches == [["a", "b"]]


def test_the_encoder_is_untouched_when_nothing_is_asked_for():
    encoder = CountingEncoder()

    assert embedding_cache.encode_cached([], encoder) == []
    assert encoder.batches == []


def test_a_batch_of_errors_encodes_each_centroid_once():
    """The point of the cache: N errors against C centroids is C + N encodes."""
    encoder = CountingEncoder()
    centroids = [f"centroid{i}" for i in range(5)]

    for i in range(10):
        embedding_cache.encode_cached([f"error{i}"] + centroids, encoder)

    assert len(encoder.encoded) == len(centroids) + 10
    assert sorted(encoder.encoded) == sorted(centroids + [f"error{i}" for i in range(10)])


def test_a_miscounting_encoder_is_rejected_rather_than_misaligned():
    """Pairing vectors with the wrong texts would corrupt every later match."""
    with pytest.raises(ValueError, match="1 vectors for 2 texts"):
        embedding_cache.encode_cached(["a", "b"], lambda batch: ["only one"])


def test_cache_size_counts_distinct_texts():
    encoder = CountingEncoder()

    embedding_cache.encode_cached(["a", "a", "b"], encoder)

    assert embedding_cache.cache_size() == 2
