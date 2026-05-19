# commit_data/ Module

Downloads and validates commit metadata for the identified failure range.

## Components

### single_commit.sh
Fetches metadata for a single commit: author, associated PR, approvers, changed files.

**API**: `download_single_commit(commit_sha)` → writes commit JSON to data directory

### download_commits.sh
Sequential commit download for small ranges.

**API**: `download_commit_range(start_sha, end_sha)` → downloads all commits in range

### batch_downloader.sh
Parallel batch download for large commit ranges. Includes caching to avoid redundant API calls.

**API**: `download_commits_batch(start_sha, end_sha)` → parallel download with configurable batch size

### commit_validator.sh
Validates the schema and completeness of downloaded commit JSON files.

**API**: `validate_commit_data(data_dir)` → exit 0 if all commit files are valid

## Dependencies
- `lib/common.sh`, `lib/config.sh`, `lib/github_api.sh`, `lib/validation.sh`
