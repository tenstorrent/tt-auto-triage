#!/usr/bin/env python3
"""
Helper module for comparing error messages using RapidFuzz and Semantic Similarity.
"""

from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

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

def compare_errors(error1: str, error2: str) -> dict:
    """
    Compare two error messages and return similarity scores.
    
    Args:
        error1: First error message
        error2: Second error message
    
    Returns:
        Dictionary with 'rapidfuzz' and 'semantic' scores (0-100)
    """
    # RapidFuzz score
    rapidfuzz_score = fuzz.token_set_ratio(error1, error2)
    
    # Semantic similarity using sentence transformers
    model = get_model()
    embeddings = model.encode([error1, error2])
    semantic_sim = cosine_similarity([embeddings[0]], [embeddings[1]])[0][0]
    semantic_score = semantic_sim * 100
    
    return {
        "rapidfuzz": rapidfuzz_score,
        "semantic": semantic_score
    }

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
    
    # Batch encode the new error and all centroids at once (much faster than N separate encodes)
    model = get_model()
    all_texts = [error_message] + centroids
    all_embeddings = model.encode(all_texts)
    
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

def recalculate_centroid(error_messages: list) -> tuple:
    """
    Recalculate the centroid error from a list of error messages.
    
    Args:
        error_messages: List of error message strings
    
    Returns:
        Tuple of (centroid_index, centroid_message)
        centroid_index is the index of the error closest to the mean embedding
    """
    if not error_messages:
        return None, None
    
    if len(error_messages) == 1:
        return 0, error_messages[0]
    
    # Encode all error messages
    model = get_model()
    embeddings = model.encode(error_messages)
    
    # Calculate mean embedding
    mean_embedding = np.mean(embeddings, axis=0)
    
    # Find error closest to mean
    similarities = cosine_similarity([mean_embedding], embeddings)[0]
    centroid_index = np.argmax(similarities)
    
    return centroid_index, error_messages[centroid_index]
