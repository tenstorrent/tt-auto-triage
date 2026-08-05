#!/usr/bin/env python3
"""Fold newly seen errors from all_errors.json into the persisted cluster state.

Each new error is compared against the existing cluster centroids. A match is
appended to that cluster; anything unmatched starts a new cluster with itself as
the centroid. Centroid text is frozen once a cluster exists, so a cluster's
identity does not drift as members are added.

This writes only to cluster_state.json. It creates no GitHub issues, reads no
issue bodies, and touches no project boards.
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cluster_state import (
    RETENTION_DAYS,
    all_urls,
    load_cluster_state,
    prune_old_runs,
    refresh_centroid_metadata,
    save_cluster_state,
    set_centroid_metadata,
)
from error_similarity import RAPIDFUZZ_THRESHOLD, SEMANTIC_THRESHOLD, find_best_matching_centroid
from github_api_utils import (
    get_commit_hash_from_github,
    log_rate_limit_status,
    read_counters,
    reset_read_counters,
)
from state_paths import SCRIPT_DIR
from timestamps import resolve_unix

ALL_ERRORS_FILE = os.path.join(SCRIPT_DIR, "all_errors.json")
SECRETS_FILE = os.path.join(SCRIPT_DIR, "secrets.json")

# Date range filtering (from environment variables)
DATE_RANGE_START = os.environ.get("DATE_RANGE_START", "")
DATE_RANGE_END = os.environ.get("DATE_RANGE_END", "")


def load_secrets() -> Dict[str, str]:
    """Load configuration from secrets.json, falling back to the environment."""
    secrets = {}
    try:
        with open(SECRETS_FILE, "r") as f:
            secrets = json.load(f)
    except FileNotFoundError:
        print(f"⚠ Warning: secrets.json not found at {SECRETS_FILE}, using environment variables")
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {SECRETS_FILE}: {e}")
        sys.exit(1)

    return {"GITHUB_TOKEN": secrets.get("github_token", "") or os.environ.get("GITHUB_TOKEN", "")}


def parse_date_to_datetime(date_str: str) -> Optional[datetime]:
    """Convert a date string like 'January 1, 2026' to a datetime."""
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(date_str.strip(), "%B %d, %Y")
    except ValueError:
        return None


# ============================================================================
# Defensive validation - drop entries with missing required metadata
# ============================================================================

def is_entry_valid(
    url: str,
    run_metadata: Dict[str, Dict[str, Any]],
    all_timestamps: Dict[str, str],
    github_token: str = "",
) -> Tuple[bool, List[str]]:
    """Check that an entry carries the fields the Pydantic model requires.

    Missing commit hashes are fetched from GitHub before giving up, since that
    is the one field we can recover after the fact.
    """
    missing_fields = []
    meta = run_metadata.get(url, {})

    timestamp = all_timestamps.get(url, "") or meta.get("timestamp", "")
    if not timestamp or timestamp.lower() == "link":
        missing_fields.append("timestamp")

    commit_hash = meta.get("commit_hash", "")
    if not commit_hash and github_token:
        fetched_hash = get_commit_hash_from_github(url, github_token)
        if fetched_hash:
            meta["commit_hash"] = fetched_hash
            commit_hash = fetched_hash
    if not commit_hash:
        missing_fields.append("commit_hash")

    if not meta.get("job_name", ""):
        missing_fields.append("job_name")

    return len(missing_fields) == 0, missing_fields


def validate_and_cleanup_entries(
    failing_runs: List[str],
    run_metadata: Dict[str, Dict[str, Any]],
    all_timestamps: Dict[str, str],
    github_token: str = "",
) -> Tuple[List[str], Dict[str, Dict[str, Any]], List[str]]:
    """Remove runs with missing required metadata.

    Better to hold incomplete data than to fail the database insert on nulls.

    Returns the surviving URLs, the pruned metadata, and the removed URLs.
    """
    valid_urls = []
    removed_urls = []

    for url in failing_runs:
        is_valid, missing_fields = is_entry_valid(url, run_metadata, all_timestamps, github_token)
        if is_valid:
            valid_urls.append(url)
        else:
            removed_urls.append(url)
            print(f"    ⚠ Removing entry with missing metadata: {url[:60]}...")
            print(f"      Missing fields: {', '.join(missing_fields)}")

    updated_run_metadata = {u: m for u, m in run_metadata.items() if u not in removed_urls}
    return valid_urls, updated_run_metadata, removed_urls


# ============================================================================
# Cluster assignment
# ============================================================================

def build_run_metadata(error_entry: List, commit_hash: Optional[str]) -> Dict[str, Any]:
    """Build the per-run metadata record stored against a URL."""
    return {
        "job_name": error_entry[3] if len(error_entry) > 3 and error_entry[3] else "",
        "workflow_name": error_entry[4] if len(error_entry) > 4 and error_entry[4] else "",
        "is_nd": error_entry[5] if len(error_entry) > 5 and error_entry[5] is not None else False,
        "commit_hash": commit_hash or "",
        "error_message": error_entry[0],
        "timestamp": error_entry[2] if len(error_entry) > 2 else "",
        "unix_timestamp": error_entry[7] if len(error_entry) > 7 else None,
    }


def process_new_error(
    error_entry: List,
    clusters: List[Dict[str, Any]],
    all_timestamps: Dict[str, str],
    github_token: str,
) -> Tuple[bool, bool]:
    """Add one error to an existing cluster or start a new one.

    Returns (changed, created_new_cluster).
    """
    error_message = error_entry[0]
    url = error_entry[1]
    timestamp = error_entry[2] if len(error_entry) > 2 else ""

    commit_hash = get_commit_hash_from_github(url, github_token) if github_token else None
    metadata = build_run_metadata(error_entry, commit_hash)

    if timestamp:
        all_timestamps[url] = timestamp

    is_valid, missing_fields = is_entry_valid(url, {url: metadata}, all_timestamps, github_token)
    if not is_valid:
        print(f"  ⚠ Skipping error with missing metadata: {', '.join(missing_fields)}")
        return False, False

    centroids = [entry["centroid_error"] for entry in clusters]
    best_idx, best_scores = find_best_matching_centroid(
        error_message,
        centroids,
        rapidfuzz_threshold=RAPIDFUZZ_THRESHOLD,
        semantic_threshold=SEMANTIC_THRESHOLD,
    )

    if best_idx is not None:
        entry = clusters[best_idx]
        if url in entry.get("failing_runs", []):
            print(f"  ⚠ URL already present in this cluster, skipping duplicate")
            return False, False

        print(
            f"  Matched existing cluster "
            f"(RapidFuzz: {best_scores['rapidfuzz']:.1f}, Semantic: {best_scores['semantic']:.1f})"
        )
        # Appended rather than sorted: the duplicate check above already keeps
        # this unique, and sorting made the list lexicographic, which decides
        # oldest_run_url for a cluster whose runs cannot be dated. Discovery
        # order is at least roughly chronological; URL order means nothing.
        entry.setdefault("failing_runs", []).append(url)
        entry.setdefault("run_metadata", {})[url] = metadata
        refresh_centroid_metadata(entry)
        return True, False

    print(f"  No match - starting a new cluster")
    new_entry = {
        "centroid_error": error_message,
        "failing_runs": [url],
        "run_metadata": {url: metadata},
    }
    set_centroid_metadata(new_entry, url)
    clusters.append(new_entry)
    return True, True


def select_new_errors(
    all_errors: List[List],
    existing_urls: set,
    date_range_start: Optional[datetime] = None,
    date_range_end: Optional[datetime] = None,
    now_unix: Optional[float] = None,
) -> Tuple[List[List], Dict[str, int]]:
    """Pick out the errors worth processing, with a tally of what was dropped."""
    if now_unix is None:
        now_unix = time.time()
    retention_cutoff = now_unix - (RETENTION_DAYS * 86400)

    new_errors: List[List] = []
    counts = {"no_url": 0, "already_tracked": 0, "too_old": 0, "outside_date_range": 0}

    for error_entry in all_errors:
        url = error_entry[1] if len(error_entry) > 1 else None
        if not url:
            counts["no_url"] += 1
            continue
        if url in existing_urls:
            counts["already_tracked"] += 1
            continue

        unix_ts = resolve_unix(
            error_entry[7] if len(error_entry) > 7 else None,
            error_entry[2] if len(error_entry) > 2 else "",
        )

        # Without this, a wide Slack fetch window would re-add the same runs
        # that were just pruned, and the state would never shrink.
        if unix_ts is not None and unix_ts < retention_cutoff:
            counts["too_old"] += 1
            continue

        if (date_range_start or date_range_end) and unix_ts is not None:
            entry_dt = datetime.fromtimestamp(unix_ts)
            if date_range_start and entry_dt < date_range_start:
                counts["outside_date_range"] += 1
                continue
            if date_range_end and entry_dt > date_range_end:
                counts["outside_date_range"] += 1
                continue

        new_errors.append(error_entry)

    return new_errors, counts


def validate_existing_clusters(
    clusters: List[Dict[str, Any]],
    all_timestamps: Dict[str, str],
    github_token: str,
) -> Tuple[List[Dict[str, Any]], int, int, int]:
    """Clean the loaded state before adding to it.

    Drops runs with unusable metadata, removes URLs that appear in more than one
    cluster, and discards clusters left with nothing.
    """
    surviving: List[Dict[str, Any]] = []
    seen_urls: set = set()
    removed_invalid = 0
    removed_duplicate = 0
    dropped_clusters = 0

    for entry in clusters:
        failing_runs = entry.get("failing_runs", [])
        run_metadata = entry.get("run_metadata", {})
        if not failing_runs:
            dropped_clusters += 1
            continue

        valid_urls, validated_metadata, removed = validate_and_cleanup_entries(
            failing_runs, run_metadata, all_timestamps, github_token
        )
        removed_invalid += len(removed)

        deduplicated = []
        for url in valid_urls:
            if url in seen_urls:
                removed_duplicate += 1
                validated_metadata.pop(url, None)
            else:
                deduplicated.append(url)
                seen_urls.add(url)

        if not deduplicated:
            dropped_clusters += 1
            continue

        entry["failing_runs"] = deduplicated
        entry["run_metadata"] = validated_metadata
        refresh_centroid_metadata(entry)
        surviving.append(entry)

    return surviving, removed_invalid, removed_duplicate, dropped_clusters


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 80)
    print("Syncing new errors into cluster state")
    print("=" * 80)

    github_token = load_secrets()["GITHUB_TOKEN"]
    if github_token:
        from github_api_utils import github_token_is_valid, load_commit_hash_cache

        # Every error needs a commit hash, and every commit hash comes from the
        # API. Continuing with a rejected token would drop the entire batch and
        # still exit zero, so the failure has to surface here.
        if not github_token_is_valid(github_token):
            print("ERROR: the GitHub token was rejected by the API (401).")
            print("  Commit hashes cannot be fetched, so every error would be dropped.")
            print("  Refusing to run rather than reporting success having stored nothing.")
            print("  Check whether the token supplied to the action has expired or been revoked.")
            sys.exit(1)

        load_commit_hash_cache()
        log_rate_limit_status(github_token, "start")
    else:
        print("ERROR: no GitHub token available, so no commit hashes can be fetched")
        print("  and every error would be dropped. Refusing to run.")
        sys.exit(1)

    print(f"\nLoading errors from {ALL_ERRORS_FILE}...")
    try:
        with open(ALL_ERRORS_FILE, "r", encoding="utf-8") as f:
            all_errors = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: File not found: {ALL_ERRORS_FILE}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {ALL_ERRORS_FILE}: {e}")
        sys.exit(1)
    print(f"Found {len(all_errors)} error(s)")

    print(f"\nLoading cluster state...")
    clusters = load_cluster_state()

    print(f"\nPruning runs older than {RETENTION_DAYS} days...")
    clusters, pruned_runs, pruned_clusters = prune_old_runs(clusters)

    # Timestamps come from the fresh Slack export first, then from state for
    # runs that predate the current window.
    all_timestamps: Dict[str, str] = {}
    for error_entry in all_errors:
        if len(error_entry) > 2 and error_entry[1]:
            all_timestamps[error_entry[1]] = error_entry[2]
    for entry in clusters:
        for url, meta in entry.get("run_metadata", {}).items():
            if url not in all_timestamps and meta.get("timestamp"):
                all_timestamps[url] = meta["timestamp"]

    print(f"\n{'=' * 80}")
    print("Validating existing clusters...")
    print(f"{'=' * 80}")
    clusters, removed_invalid, removed_duplicate, dropped_clusters = validate_existing_clusters(
        clusters, all_timestamps, github_token
    )
    print(f"\nValidation summary:")
    print(f"  Clusters remaining: {len(clusters)}")
    print(f"  Invalid entries removed: {removed_invalid}")
    print(f"  Duplicate entries removed: {removed_duplicate}")
    print(f"  Clusters dropped (no entries left): {dropped_clusters}")

    existing_urls = all_urls(clusters)
    print(f"\nTracking {len(existing_urls)} URL(s) across {len(clusters)} cluster(s)")

    date_range_start = parse_date_to_datetime(DATE_RANGE_START)
    date_range_end = parse_date_to_datetime(DATE_RANGE_END)
    if date_range_start:
        print(f"Date range start: {DATE_RANGE_START}")
    if date_range_end:
        print(f"Date range end: {DATE_RANGE_END}")

    print(f"\nFiltering errors to find new ones...")
    new_errors, counts = select_new_errors(all_errors, existing_urls, date_range_start, date_range_end)

    print(f"  Total errors in all_errors.json: {len(all_errors)}")
    print(f"  Skipped (no URL): {counts['no_url']}")
    print(f"  Skipped (already tracked): {counts['already_tracked']}")
    if counts["too_old"]:
        print(f"  Skipped (older than {RETENTION_DAYS} days): {counts['too_old']}")
    if counts["outside_date_range"]:
        print(f"  Skipped (outside date range): {counts['outside_date_range']}")
    print(f"  New errors to process: {len(new_errors)}")

    new_clusters = 0
    appended = 0
    if new_errors:
        print(f"\n{'=' * 80}")
        print("Processing new errors...")
        print(f"{'=' * 80}")
        # So the guard below judges this batch rather than reads made while
        # validating clusters that were already stored.
        reset_read_counters()
        for idx, error_entry in enumerate(new_errors, 1):
            print(f"\n[{idx}/{len(new_errors)}] {error_entry[1]}")
            changed, created = process_new_error(error_entry, clusters, all_timestamps, github_token)
            if changed:
                existing_urls.add(error_entry[1])
                if created:
                    new_clusters += 1
                else:
                    appended += 1
    else:
        print(f"\nNo new errors to process.")

    # Storing none of a batch is only worth failing over when a retry could do
    # better. What matters is why: an API that was not answering will answer
    # later, so saving now would record a state where none of this batch was
    # ever seen and lose the chance. Messages missing a job name will never
    # improve, and a quiet cycle whose only error is malformed is not evidence
    # of anything, so failing on it would just stop the report and the upload
    # over one bad record.
    if new_errors and new_clusters == 0 and appended == 0:
        unreachable = read_counters()["unreachable"]
        print(f"\nAll {len(new_errors)} new error(s) were dropped, none were stored.")
        if unreachable:
            print(f"ERROR: {unreachable} API read(s) failed for reasons that may not persist.")
            print("  Leaving the cluster state untouched so the next run can retry them.")
            print("  The warnings above name the reads that failed.")
            sys.exit(1)
        print("  Every drop was a settled answer rather than a failed read, so a retry")
        print("  would drop them again. Continuing with the state otherwise unchanged.")
        print("  The skip reasons above say which metadata was missing.")

    print(f"\n{'=' * 80}")
    print("Saving cluster state...")
    print(f"{'=' * 80}")
    save_cluster_state(clusters)

    if github_token:
        from github_api_utils import get_commit_hash_cache_stats, save_commit_hash_cache

        cache_stats = get_commit_hash_cache_stats()
        print(f"\nCommit hash cache: {cache_stats['total_entries']} run(s) cached "
              f"({cache_stats['found']} found, {cache_stats['not_found']} missing)")
        save_commit_hash_cache()

    print(f"\n{'=' * 80}")
    print("Summary:")
    print(f"  New clusters created: {new_clusters}")
    print(f"  Errors added to existing clusters: {appended}")
    print(f"  Runs pruned (older than {RETENTION_DAYS} days): {pruned_runs}")
    print(f"  Clusters pruned (no recent runs): {pruned_clusters}")
    print(f"  Total clusters in state: {len(clusters)}")
    print(f"  Total runs in state: {sum(len(c.get('failing_runs', [])) for c in clusters)}")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
