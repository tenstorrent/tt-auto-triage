#!/usr/bin/env python3
"""
Utility functions for GitHub API interactions, including rate limit checking.
"""

import atexit
import json
import os
import re
from typing import Dict, Optional

import requests

# Caches live in the state directory so they are carried between runs by the
# state artifact. Writing them next to this file would lose them, since the
# action is checked out fresh every run.
from state_paths import COMMIT_HASH_CACHE_FILE, JOB_NAME_CACHE_FILE, ensure_state_dir

# Module-level cache for commit hashes to avoid redundant API calls
# Maps run_id -> commit_hash (or None if not found)
# Changed from URL to run_id as cache key since multiple jobs share the same run and commit
_commit_hash_cache: Dict[str, Optional[str]] = {}
_commit_cache_loaded = False
_commit_cache_modified = False

# Module-level cache for job names to avoid redundant API calls
# Maps job_id -> job_name (or None if not found)
_job_name_cache: Dict[str, Optional[str]] = {}
_job_cache_loaded = False
_job_cache_modified = False


def load_commit_hash_cache() -> None:
    """Load the commit hash cache from disk if it exists."""
    global _commit_hash_cache, _commit_cache_loaded

    if _commit_cache_loaded:
        return

    if os.path.exists(COMMIT_HASH_CACHE_FILE):
        try:
            with open(COMMIT_HASH_CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                print(f"  ⚠ Warning: Commit hash cache is not a dict (got {type(loaded).__name__}), resetting")
                _commit_hash_cache = {}
            else:
                _commit_hash_cache = loaded
                print(f"  ✓ Loaded commit hash cache with {len(_commit_hash_cache)} entries")
        except (json.JSONDecodeError, Exception) as e:
            print(f"  ⚠ Warning: Could not load commit hash cache: {e}")
            _commit_hash_cache = {}

    _commit_cache_loaded = True


def save_commit_hash_cache() -> None:
    """Save the commit hash cache to disk."""
    global _commit_cache_modified

    if not _commit_cache_modified:
        return

    try:
        ensure_state_dir()
        with open(COMMIT_HASH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_commit_hash_cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠ Warning: Could not save commit hash cache: {e}")


def load_job_name_cache() -> None:
    """Load the job name cache from disk if it exists."""
    global _job_name_cache, _job_cache_loaded

    if _job_cache_loaded:
        return

    if os.path.exists(JOB_NAME_CACHE_FILE):
        try:
            with open(JOB_NAME_CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                print(f"  ⚠ Warning: Job name cache is not a dict (got {type(loaded).__name__}), resetting")
                _job_name_cache = {}
            else:
                _job_name_cache = loaded
                print(f"  ✓ Loaded job name cache with {len(_job_name_cache)} entries")
        except (json.JSONDecodeError, Exception) as e:
            print(f"  ⚠ Warning: Could not load job name cache: {e}")
            _job_name_cache = {}

    _job_cache_loaded = True


def save_job_name_cache() -> None:
    """Save the job name cache to disk."""
    global _job_cache_modified

    if not _job_cache_modified:
        return

    try:
        ensure_state_dir()
        with open(JOB_NAME_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_job_name_cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠ Warning: Could not save job name cache: {e}")


def get_commit_hash_cache_stats() -> Dict[str, int]:
    """Get statistics about the commit hash cache."""
    return {
        "total_entries": len(_commit_hash_cache),
        "found": sum(1 for v in _commit_hash_cache.values() if v is not None),
        "not_found": sum(1 for v in _commit_hash_cache.values() if v is None)
    }


def get_job_name_cache_stats() -> Dict[str, int]:
    """Get statistics about the job name cache."""
    return {
        "total_entries": len(_job_name_cache),
        "found": sum(1 for v in _job_name_cache.values() if v is not None),
        "not_found": sum(1 for v in _job_name_cache.values() if v is None)
    }


def clear_commit_hash_cache() -> None:
    """Clear the commit hash cache."""
    global _commit_hash_cache, _commit_cache_modified
    _commit_hash_cache = {}
    _commit_cache_modified = True


def clear_job_name_cache() -> None:
    """Clear the job name cache."""
    global _job_name_cache, _job_cache_modified
    _job_name_cache = {}
    _job_cache_modified = True


# Register save functions to run at exit
atexit.register(save_commit_hash_cache)
atexit.register(save_job_name_cache)


def check_github_rate_limit(github_token: str) -> Optional[Dict[str, int]]:
    """Check GitHub API rate limit status.
    
    Args:
        github_token: GitHub token for API access
    
    Returns:
        Dictionary with 'remaining', 'limit', 'reset' keys, or None if error
    """
    if not github_token:
        return None
    
    try:
        url = "https://api.github.com/rate_limit"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        core = data.get("resources", {}).get("core", {})
        return {
            "remaining": core.get("remaining", 0),
            "limit": core.get("limit", 5000),
            "reset": core.get("reset", 0)
        }
    except Exception as e:
        print(f"⚠ Warning: Could not check GitHub API rate limit: {e}")
        return None


def log_rate_limit_status(github_token: str, stage: str = "") -> None:
    """Log GitHub API rate limit status.
    
    Args:
        github_token: GitHub token for API access
        stage: Optional stage label (e.g., "start", "end")
    """
    rate_limit = check_github_rate_limit(github_token)
    if rate_limit:
        remaining = rate_limit["remaining"]
        limit = rate_limit["limit"]
        reset = rate_limit["reset"]
        
        # Calculate reset time
        from datetime import datetime
        reset_time = datetime.fromtimestamp(reset) if reset else None
        reset_str = reset_time.strftime("%Y-%m-%d %H:%M:%S") if reset_time else "unknown"
        
        stage_label = f" [{stage}]" if stage else ""
        print(f"\n{'='*60}")
        print(f"GitHub API Rate Limit{stage_label}:")
        print(f"  Remaining: {remaining:,} / {limit:,} ({remaining/limit*100:.1f}%)")
        print(f"  Resets at: {reset_str}")
        print(f"{'='*60}\n")
    else:
        print(f"\n⚠ Warning: Could not check GitHub API rate limit{stage}\n")


def get_commit_hash_from_github(job_url: str, github_token: str, use_cache: bool = True) -> Optional[str]:
    """Fetch full commit hash from GitHub API using job URL.

    Uses a persistent cache (saved to disk) to avoid redundant API calls.
    Cache key is the run ID, not the full job URL, since multiple jobs in the same
    run share the same commit hash.

    Args:
        job_url: GitHub Actions job URL (e.g., https://github.com/owner/repo/actions/runs/RUN_ID/job/JOB_ID)
        github_token: GitHub token for API access
        use_cache: Whether to use the cache (default True)

    Returns:
        Full commit SHA (40 characters) or None if not found
    """
    global _commit_hash_cache, _commit_cache_modified

    if not job_url or not github_token:
        return None

    # Ensure cache is loaded
    load_commit_hash_cache()

    # Extract run ID and repo info from URL
    try:
        # Extract run ID from URL: https://github.com/owner/repo/actions/runs/RUN_ID/job/JOB_ID
        run_match = re.search(r'/actions/runs/(\d+)', job_url)
        if not run_match:
            return None

        run_id = run_match.group(1)

        # Extract repo owner and name from URL
        repo_match = re.search(r'github\.com/([^/]+)/([^/]+)/actions', job_url)
        if not repo_match:
            return None

        repo_owner = repo_match.group(1)
        repo_name = repo_match.group(2)

        # Use "owner/repo/run_id" as cache key to handle cases where same run ID
        # exists in different repositories
        cache_key = f"{repo_owner}/{repo_name}/{run_id}"

    except Exception:
        return None

    # Check cache first
    if use_cache and cache_key in _commit_hash_cache:
        cached = _commit_hash_cache[cache_key]
        # Return cached value (could be None if previous fetch failed)
        return cached

    try:
        # Fetch workflow run details
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/actions/runs/{run_id}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        response = requests.get(url, headers=headers)
        response.raise_for_status()
        run_data = response.json()

        # Get the commit SHA (head_sha is the full commit hash)
        commit_sha = run_data.get("head_sha")
        if commit_sha and len(commit_sha) == 40:
            _commit_hash_cache[cache_key] = commit_sha
            _commit_cache_modified = True
            return commit_sha

        _commit_hash_cache[cache_key] = None
        _commit_cache_modified = True
        return None

    except Exception as e:
        print(f"  ⚠ Warning: Could not fetch commit hash for run {run_id}: {e}")
        _commit_hash_cache[cache_key] = None
        _commit_cache_modified = True
        return None


def get_job_name_from_github(job_url: str, github_token: str, use_cache: bool = True) -> Optional[str]:
    """Fetch job name from GitHub API using job URL.

    Uses a persistent cache (saved to disk) to avoid redundant API calls.

    Args:
        job_url: GitHub Actions job URL (e.g., https://github.com/owner/repo/actions/runs/RUN_ID/job/JOB_ID)
        github_token: GitHub token for API access
        use_cache: Whether to use the cache (default True)

    Returns:
        Job name string, or None if not found
    """
    global _job_name_cache, _job_cache_modified

    if not job_url or not github_token:
        return None

    # Ensure cache is loaded
    load_job_name_cache()

    # Extract job ID and repo info from URL
    try:
        # Extract job ID from URL: https://github.com/owner/repo/actions/runs/RUN_ID/job/JOB_ID
        job_match = re.search(r'/job/(\d+)', job_url)
        if not job_match:
            return None

        job_id = job_match.group(1)

        # Extract repo owner and name from URL
        repo_match = re.search(r'github\.com/([^/]+)/([^/]+)/actions', job_url)
        if not repo_match:
            return None

        repo_owner = repo_match.group(1)
        repo_name = repo_match.group(2)

        # Use "owner/repo/job_id" as cache key to handle cases where same job ID
        # exists in different repositories
        cache_key = f"{repo_owner}/{repo_name}/{job_id}"

    except Exception:
        return None

    # Check cache first
    if use_cache and cache_key in _job_name_cache:
        cached = _job_name_cache[cache_key]
        # Return cached value (could be None if previous fetch failed)
        return cached

    try:
        # Fetch job details from GitHub API
        url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/actions/jobs/{job_id}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        response = requests.get(url, headers=headers)
        response.raise_for_status()
        job_data = response.json()

        # Get the job name
        job_name = job_data.get("name")
        if job_name:
            _job_name_cache[cache_key] = job_name
            _job_cache_modified = True
            return job_name

        _job_name_cache[cache_key] = None
        _job_cache_modified = True
        return None

    except Exception as e:
        print(f"  ⚠ Warning: Could not fetch job name for job {job_id}: {e}")
        _job_name_cache[cache_key] = None
        _job_cache_modified = True
        return None
