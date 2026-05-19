# logs/ Module

Retrieves and parses job logs from GitHub Actions.

## Components

### log_parser.sh
Downloads raw job logs via the GitHub API, extracts the relevant failing step output, and identifies error messages.

**API**:
- `sanitize_job_name(name)` → prints filesystem-safe job name
- `find_job_logs(run_id, job_name)` → downloads and parses logs, writes to `logs/`

## Dependencies
- `lib/common.sh`, `lib/github_api.sh`
