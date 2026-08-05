#!/usr/bin/env python3
"""Reuse sentence embeddings for text already encoded in this process.

Matching a new error against the clusters encodes the error and every centroid
together. Centroid text is frozen once its cluster exists, so across a batch the
same centroids are encoded once per new error: N x (C + 1) encodes for C + N
distinct strings. With runs now retained for 30 days, C only grows.

The encoder is deterministic, so returning a previously computed vector gives
the same answer it would have produced. This lives apart from error_similarity
so it can be tested without the sentence-transformer model installed.
"""

from typing import Any, Callable, Dict, List, Sequence

_cache: Dict[str, Any] = {}


def encode_cached(texts: Sequence[str], encode: Callable[[List[str]], Sequence[Any]]) -> List[Any]:
    """Embed texts in order, calling encode only for ones not already known.

    encode is passed the unseen texts as a single batch, and is not called at
    all when everything is cached, so the model is never loaded unnecessarily.
    """
    missing = [text for text in dict.fromkeys(texts) if text not in _cache]
    if missing:
        vectors = encode(missing)
        if len(vectors) != len(missing):
            raise ValueError(f"encoder returned {len(vectors)} vectors for {len(missing)} texts")
        for text, vector in zip(missing, vectors):
            _cache[text] = vector

    return [_cache[text] for text in texts]


def cache_size() -> int:
    """How many distinct texts are held."""
    return len(_cache)


def clear_cache() -> None:
    """Forget every cached embedding."""
    _cache.clear()
