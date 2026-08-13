#!/usr/bin/env python3
"""Locations of the grouping state that persists between workflow runs.

The state directory is uploaded as a workflow artifact at the end of a run and
restored at the start of the next one, so anything written here survives.
It falls back to the action directory so local runs work without setup.
"""

import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("GROUPING_STATE_DIR") or SCRIPT_DIR

# Error clusters: the list of {centroid_error, failing_runs, run_metadata,
# centroid_metadata} entries that used to be reconstructed from GitHub issues.
CLUSTER_STATE_FILE = os.path.join(STATE_DIR, "cluster_state.json")

# GitHub API response caches.
COMMIT_HASH_CACHE_FILE = os.path.join(STATE_DIR, "commit_hash_cache.json")
JOB_NAME_CACHE_FILE = os.path.join(STATE_DIR, "job_name_cache.json")


def ensure_state_dir() -> str:
    """Create the state directory if it does not exist and return its path."""
    os.makedirs(STATE_DIR, exist_ok=True)
    return STATE_DIR
