# retry/ Module

Handles deterministic failure retries on supported single-chip hardware.

## Components

### hardware_checker.sh
Validates whether a job name indicates supported single-chip hardware (N150, N300, P100, P100A, P150, P300). Multi-chip systems (Galaxy, T3K, TG, TGG) are not supported for retry.

**API**: `is_supported_retry_hardware(job_name)` → exit 0 if supported

### run_trigger.sh
Re-runs a failed GitHub Actions job and polls for completion.

**API**:
- `rerun_failed_job(run_id, job_id)` → prints new run attempt number
- `wait_for_run_completion(run_id, job_name, timeout_sec, poll_interval)` → prints `success|failure|timed_out`
- `find_job_in_attempt(run_id, attempt, job_name)` → prints job ID

### result_comparator.sh
Compares original and retry error messages using Copilot LLM to determine if they are the same failure.

**API**:
- `run_copilot_error_comparison(root, original_error, retry_error)` → writes `error_comparison.json`
- `get_same_failure(comparison_file)` → prints `true|false`
- `get_retry_error_extracted(comparison_file)` → prints extracted error text
- `determine_retry_result(retry_status, same_failure)` → prints `passed|failed_same|failed_different`

### retry_orchestrator.sh
Orchestrates the full retry flow: validate hardware → trigger rerun → poll → compare errors → notify Slack.

**API**: `run_retry_flow(job_name, job_id, run_id, workflow_name, error_message)` → exit 0 always (failures logged, not fatal)

## Dependencies
- `lib/common.sh`, `lib/config.sh`, `lib/github_api.sh`, `lib/slack_api.sh`

## Usage
Called from `action.yml` via `retry_on_deterministic.sh` when `enable-retry` input is true.
