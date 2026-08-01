#!/usr/bin/env python3
"""
Group similar errors using semantic similarity and RapidFuzz.
Reads errors from errors.json and outputs grouped errors.
"""

import json
import os
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from error_similarity import RAPIDFUZZ_THRESHOLD, SEMANTIC_THRESHOLD

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ERRORS_FILE = os.path.join(SCRIPT_DIR, "all_errors.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "grouped_errors.json")


def parse_error_item(item: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Parse an error item into message and metadata.

    Args:
        item: A bare error string, or a list of the form
            [message, url, timestamp, job_name, workflow_name, is_nd,
             full_report_link, unix_timestamp], truncated at any point.

    Returns:
        Tuple of (error_message, metadata_dict) or None if invalid
    """
    if isinstance(item, str):
        item = [item]
    if not isinstance(item, list) or not item:
        return None

    def field(index: int, default: Any = None) -> Any:
        value = item[index] if len(item) > index else None
        return default if value is None or value == "" else value

    message = item[0]
    return message, {
        "error": message,
        "url": field(1, ""),
        "timestamp": field(2, ""),
        "job_name": field(3, ""),
        "workflow_name": field(4, ""),
        "is_nd": field(5, False),
        "unix_timestamp": field(7),
    }


def load_errors(errors_file: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Load and parse errors from file.
    
    Returns:
        Tuple of (error_messages, errors_with_metadata)
    """
    print(f"Loading errors from {errors_file}...")
    with open(errors_file, 'r') as f:
        data = json.load(f)
        # Handle both ["error1", "error2"] and {"errors": ["error1", "error2"]}
        raw_errors = data if isinstance(data, list) else data.get('errors', data.get('error', []))
    
    errors_with_metadata = []
    error_messages = []
    
    for item in raw_errors:
        result = parse_error_item(item)
        if result is not None:
            message, metadata = result
            error_messages.append(message)
            errors_with_metadata.append(metadata)
    
    print(f"Found {len(error_messages)} errors")
    return error_messages, errors_with_metadata


def cluster_errors(error_messages: List[str], semantic_threshold: float, rapidfuzz_threshold: float) -> Tuple[List[List[int]], np.ndarray]:
    """Cluster errors using semantic similarity and RapidFuzz.
    
    Returns:
        Tuple of (groups, embeddings) where groups is a list of lists of indices
    """
    # Encode all error messages once
    print("Encoding errors...")
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = model.encode(error_messages)
    
    # Compute semantic similarity matrix
    print("Computing semantic similarities...")
    semantic_matrix = cosine_similarity(embeddings) * 100
    
    # Use strict centroid-based clustering: all errors must be similar to the centroid
    # This prevents transitive chaining where A->B->C groups dissimilar A and C together
    print("Clustering errors using strict centroid-based approach...")
    visited = set()
    groups = []
    
    for i in range(len(error_messages)):
        if i in visited:
            continue
        
        # Start a new group with this error as the centroid
        centroid_idx = i
        current_group = [i]
        visited.add(i)
        
        # Find all unvisited errors that are similar to THIS centroid
        # Only add errors that are directly similar to the centroid, not transitively
        for j in range(len(error_messages)):
            if j in visited:
                continue
            
            # Check similarity to the centroid (not to other group members)
            semantic_score = semantic_matrix[i][j]  # Use precomputed matrix
            rapidfuzz_score = fuzz.token_set_ratio(error_messages[centroid_idx], error_messages[j])
            
            if semantic_score >= semantic_threshold and rapidfuzz_score >= rapidfuzz_threshold:
                current_group.append(j)
                visited.add(j)
        
        groups.append(current_group)
    
    # Sort groups by size (descending order)
    groups.sort(key=len, reverse=True)
    
    return groups, embeddings


def build_grouped_errors(groups: List[List[int]], errors_with_metadata: List[Dict[str, Any]], embeddings: np.ndarray) -> Dict[str, Any]:
    """Build the grouped errors dictionary with centroids.
    
    Returns:
        Dictionary mapping group names to group data
    """
    # Convert groups to error lists (preserve error + URL + timestamp format)
    # For each group, find the centroid and reorder so the closest error is first
    print("Finding centroid errors for each group...")
    grouped_errors = {}
    
    for group_idx, group in enumerate(groups, 1):
        group_name = f"group_{group_idx}"
        
        if len(group) == 1:
            # Single error group - centroid is the same as the single error
            centroid_error = errors_with_metadata[group[0]]
            group_errors = [errors_with_metadata[group[0]]]
        else:
            # Calculate centroid of embeddings for this group
            group_embeddings = embeddings[group]
            centroid = np.mean(group_embeddings, axis=0)
            
            # Find the error closest to the centroid
            # Calculate cosine similarity between centroid and each error in the group
            centroid_similarities = cosine_similarity([centroid], group_embeddings)[0]
            closest_idx = np.argmax(centroid_similarities)
            
            # Store the centroid error
            centroid_error = errors_with_metadata[group[closest_idx]]
            
            # Reorder group so closest error is first
            reordered_group = [group[closest_idx]] + [group[i] for i in range(len(group)) if i != closest_idx]
            group_errors = [errors_with_metadata[i] for i in reordered_group]
        
        grouped_errors[group_name] = {
            "count": len(group_errors),
            "centroid": centroid_error,
            "errors": group_errors
        }
    
    return grouped_errors


def main():
    """Main function to group similar errors."""
    # Load errors
    error_messages, errors_with_metadata = load_errors(ERRORS_FILE)
    
    if not error_messages:
        print("No errors found. Exiting.")
        return
    
    # Cluster errors
    groups, embeddings = cluster_errors(error_messages, SEMANTIC_THRESHOLD, RAPIDFUZZ_THRESHOLD)
    
    # Build grouped errors
    grouped_errors = build_grouped_errors(groups, errors_with_metadata, embeddings)
    
    # Output results
    print(f"\nFound {len(groups)} groups:")
    for group_idx, group in enumerate(groups, 1):
        print(f"  Group {group_idx}: {len(group)} error(s)")
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(grouped_errors, f, indent=2)
    
    print(f"\nResults saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
