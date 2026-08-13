#!/usr/bin/env python3
"""
Helper module for comparing error messages using RapidFuzz and Semantic Similarity.
"""

from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from embedding_cache import encode_cached

# Matching thresholds, shared by every stage of the pipeline so that grouping,
# syncing, and reporting cannot disagree about what counts as the same error.
# Both must be cleared for a match. They are high on purpose: errors that share
# boilerplate ("TT_THROW @ path1: init failed" vs "TT_THROW @ path2: timeout")
# score deceptively well on either metric alone.
SEMANTIC_THRESHOLD = 85.0
RAPIDFUZZ_THRESHOLD = 70.0

# Load model once (cached)
_model = None

def get_model():
    """Get or create the sentence transformer model."""
    global _model
    if _model is None:
        _model = SentenceTransformer('all-MiniLM-L6-v2')
    return _model

def find_best_matching_centroid(error_message: str, centroids: list, rapidfuzz_threshold: float = RAPIDFUZZ_THRESHOLD, semantic_threshold: float = SEMANTIC_THRESHOLD) -> tuple:
    """
    Find the best matching centroid for an error message.
    
    Args:
        error_message: The error message to match
        centroids: List of centroid error strings
        rapidfuzz_threshold: Minimum RapidFuzz score (0-100)
        semantic_threshold: Minimum semantic score (0-100)
    
    Returns:
        Tuple of (best_index, best_scores) or (None, None) if no match found
        best_scores is a dict with 'rapidfuzz' and 'semantic' keys
    """
    if not centroids:
        return None, None
    
    # One batch for the new error and every centroid, reusing any embedding
    # already computed this run. Centroid text never changes, so across a batch
    # of errors each centroid is encoded once instead of once per error.
    all_texts = [error_message] + centroids
    all_embeddings = encode_cached(all_texts, lambda batch: get_model().encode(batch))
    
    # Split embeddings: first is the new error, rest are centroids
    error_embedding = all_embeddings[0:1]  # Keep as 2D array for cosine_similarity
    centroid_embeddings = all_embeddings[1:]
    
    # Compute all semantic similarities at once
    semantic_scores = cosine_similarity(error_embedding, centroid_embeddings)[0] * 100
    
    best_index = None
    best_scores = None
    best_combined_score = -1
    
    for idx, centroid in enumerate(centroids):
        semantic_score = semantic_scores[idx]
        rapidfuzz_score = fuzz.token_set_ratio(error_message, centroid)
        
        # Check if both thresholds are met
        if rapidfuzz_score >= rapidfuzz_threshold and semantic_score >= semantic_threshold:
            # Use combined score (weighted average) to find best match
            combined = (rapidfuzz_score * 0.4 + semantic_score * 0.6)
            
            if combined > best_combined_score:
                best_combined_score = combined
                best_index = idx
                best_scores = {"rapidfuzz": rapidfuzz_score, "semantic": semantic_score}
    
    return best_index, best_scores
