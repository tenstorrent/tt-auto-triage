# boundaries/ Module

Finds the first-good and first-bad workflow run boundaries for a failing job, identifying the commit range that introduced the failure.

## Components

### find_boundaries.sh
Main orchestrator. Iterates through workflow runs to find the transition from passing to failing for a specific job name.

### workflow_finder.sh
Resolves a workflow name to its GitHub workflow ID by trying `.yml` and `.yaml` extensions.

**API**: `find_workflow_id(workflow_name)` → prints workflow ID or exits on failure

### run_processor.sh
Paginates through workflow runs and identifies boundary commits.

**API**: `process_workflow_runs(workflow_id, subjob_name, workflow_name)` → writes `subjob_runs.json` with boundary data

### job_matcher.sh
Matches a target job name against the jobs in a workflow run, handling Unicode normalization and partial matching.

**API**: `match_subjob(job_name, subjob_name, workflow_name)` → exit 0 if match found

## Dependencies
- `lib/common.sh`, `lib/config.sh`, `lib/github_api.sh`, `lib/validation.sh`

## Output
Writes `data/subjob_runs.json` containing run history and boundary commits.
