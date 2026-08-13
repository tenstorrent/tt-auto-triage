#!/usr/bin/env python3
"""Read, write, and prune the error cluster state.

This holds the data that used to live in GitHub issues. The entry shape is
unchanged from the old issue dump so the report generator did not need to learn
a new format:

    {
      "centroid_error": "...",
      "failing_runs": ["https://github.com/.../job/123", ...],
      "run_metadata": {"<url>": {"job_name", "workflow_name", "is_nd",
                                 "commit_hash", "error_message",
                                 "timestamp", "unix_timestamp"}},
      "centroid_metadata": {"url", "commit_hash", "timestamp", "unix_timestamp"}
    }
"""

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from state_paths import CLUSTER_STATE_FILE, ensure_state_dir
from timestamps import resolve_unix

# Runs older than this are dropped from the state, and clusters left with no
# runs are dropped entirely.
RETENTION_DAYS = int(os.environ.get("STATE_RETENTION_DAYS", "30"))


def load_cluster_state(path: str = CLUSTER_STATE_FILE) -> List[Dict[str, Any]]:
    """Load the cluster state, returning an empty list when starting cold."""
    if not os.path.exists(path):
        print(f"  No existing cluster state at {path} - starting from empty state")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"  ⚠ Warning: cluster state is not valid JSON ({e}) - starting from empty state")
        return []

    if not isinstance(data, list):
        print(f"  ⚠ Warning: cluster state is {type(data).__name__}, expected list - starting from empty state")
        return []

    total_runs = sum(len(entry.get("failing_runs", [])) for entry in data)
    print(f"  ✓ Loaded {len(data)} cluster(s) with {total_runs} run(s) from {path}")
    return data


def save_cluster_state(entries: List[Dict[str, Any]], path: str = CLUSTER_STATE_FILE) -> None:
    """Write the cluster state, creating the containing directory if needed."""
    ensure_state_dir()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    total_runs = sum(len(entry.get("failing_runs", [])) for entry in entries)
    print(f"  ✓ Saved {len(entries)} cluster(s) with {total_runs} run(s) to {path}")


def run_unix(entry: Dict[str, Any], url: str) -> Optional[float]:
    """Unix timestamp for one run in a cluster, or None if it cannot be resolved."""
    meta = entry.get("run_metadata", {}).get(url, {})
    return resolve_unix(meta.get("unix_timestamp"), meta.get("timestamp", ""))


def newest_run_unix(entry: Dict[str, Any]) -> Optional[float]:
    """Timestamp of the most recent run in a cluster."""
    stamps = [t for t in (run_unix(entry, u) for u in entry.get("failing_runs", [])) if t is not None]
    return max(stamps) if stamps else None


def oldest_run_url(entry: Dict[str, Any]) -> Optional[str]:
    """URL of the earliest run in a cluster, preferring runs we can date."""
    dated = [(run_unix(entry, u), u) for u in entry.get("failing_runs", [])]
    dated = [(t, u) for t, u in dated if t is not None]
    if dated:
        return min(dated)[1]

    failing_runs = entry.get("failing_runs", [])
    return failing_runs[0] if failing_runs else None


def set_centroid_metadata(entry: Dict[str, Any], url: str) -> None:
    """Point a cluster's centroid metadata at a specific run."""
    meta = entry.get("run_metadata", {}).get(url, {})
    entry["centroid_metadata"] = {
        "url": url,
        "commit_hash": meta.get("commit_hash", ""),
        "timestamp": meta.get("timestamp", ""),
        "unix_timestamp": meta.get("unix_timestamp"),
    }


def refresh_centroid_metadata(entry: Dict[str, Any]) -> None:
    """Ensure centroid metadata points at a run that still exists.

    The centroid error text stays frozen once a cluster is created. Only the
    run it points at moves, and only when that run has been pruned away.
    """
    failing_runs = entry.get("failing_runs", [])
    if not failing_runs:
        entry["centroid_metadata"] = {}
        return

    current = (entry.get("centroid_metadata") or {}).get("url")
    if current and current in failing_runs:
        # Keep metadata in sync with any newly fetched commit hash.
        set_centroid_metadata(entry, current)
        return

    replacement = oldest_run_url(entry)
    if replacement:
        set_centroid_metadata(entry, replacement)


def prune_old_runs(
    entries: List[Dict[str, Any]],
    max_age_days: int = RETENTION_DAYS,
    now_unix: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Drop runs older than the retention window and clusters left empty.

    Runs whose timestamp cannot be resolved are kept, on the grounds that
    deleting data because we failed to parse it is worse than keeping it.

    Returns the surviving entries, the number of runs removed, and the number
    of clusters dropped.
    """
    if now_unix is None:
        now_unix = time.time()
    cutoff = now_unix - (max_age_days * 86400)

    surviving: List[Dict[str, Any]] = []
    removed_runs = 0
    dropped_clusters = 0

    for entry in entries:
        failing_runs = entry.get("failing_runs", [])
        run_metadata = entry.get("run_metadata", {})

        kept_urls = []
        for url in failing_runs:
            stamp = run_unix(entry, url)
            if stamp is not None and stamp < cutoff:
                removed_runs += 1
                continue
            kept_urls.append(url)

        if not kept_urls:
            dropped_clusters += 1
            continue

        entry["failing_runs"] = kept_urls
        entry["run_metadata"] = {u: m for u, m in run_metadata.items() if u in kept_urls}
        refresh_centroid_metadata(entry)
        surviving.append(entry)

    if removed_runs or dropped_clusters:
        print(
            f"  Pruned {removed_runs} run(s) older than {max_age_days} days "
            f"and dropped {dropped_clusters} empty cluster(s)"
        )

    return surviving, removed_runs, dropped_clusters


def all_urls(entries: List[Dict[str, Any]]) -> set:
    """Every run URL currently tracked across all clusters."""
    urls = set()
    for entry in entries:
        for url in entry.get("failing_runs", []):
            if url:
                urls.add(url)
    return urls
