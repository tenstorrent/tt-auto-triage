# Auto-Triage Architecture

## Overview

Regression-analysis is a composite GitHub Action that automatically analyzes CI failures in `tt-metal`, identifies likely root causes, retries deterministic failures on supported hardware, and reports results to Slack.

## Directory Structure

```
regression_analysis/
├── lib/                    # Shared libraries (source-guarded, idempotent)
│   ├── common.sh           # Logging, path helpers, JSON utilities
│   ├── config.sh           # Environment config, directory setup
│   ├── github_api.sh       # GitHub API wrappers (gh CLI)
│   ├── hang_detect.sh      # Hang follow-up trigger (sourced from regression_analysis.sh; see followups.manifest)
│   ├── instructions_pipeline.sh  # Concatenate *.fragments; run followups.manifest
│   ├── slack_api.sh        # Slack message formatting and posting
│   └── validation.sh       # Input validation, SHA parsing, JSON checks
│
├── modules/                # Domain-specific modules
│   ├── analysis/           # LLM-based triage analysis
│   │   └── llm_runner.sh   # Copilot CLI invocation for root-cause analysis
│   ├── auto_fix/           # Auto-fix module (currently disabled)
│   │   └── pr_validator.sh # Validation for auto-fix prerequisites
│   ├── boundaries/         # Failure boundary detection
│   │   ├── find_boundaries.sh   # Main orchestrator for boundary search
│   │   ├── job_matcher.sh       # Job name matching and normalization
│   │   ├── run_processor.sh     # Workflow run pagination and filtering
│   │   └── workflow_finder.sh   # Workflow YAML file resolution
│   ├── commit_data/        # Commit metadata collection
│   │   ├── batch_downloader.sh  # Parallel commit data download
│   │   ├── commit_validator.sh  # Commit JSON schema validation
│   │   ├── download_commits.sh  # Sequential commit download
│   │   └── single_commit.sh     # Single commit metadata fetch
│   ├── logs/               # Log retrieval and parsing
│   │   └── log_parser.sh   # Job log download and error extraction
│   └── retry/              # Deterministic failure retry
│       ├── hardware_checker.sh    # Supported hardware validation
│       ├── result_comparator.sh   # LLM-based error comparison
│       ├── retry_orchestrator.sh  # Retry flow orchestration
│       └── run_trigger.sh         # Job rerun and polling
│
├── scripts/                # Thin wrappers for consistent entry points
│   ├── retry_on_deterministic.sh
│   └── run_auto_fix.sh
│
├── tools/                  # Standalone utility scripts
│   ├── get_changed_files.sh      # List files changed in a commit
│   ├── get_commit_diff.sh        # Get patch diff for a commit
│   ├── list_commits_between.sh   # List commits between two SHAs
│   └── verify_commit_metadata.sh # Validate downloaded commit data
│
├── data/                   # Data files
│   ├── config/             # Runtime config (cancel.json, create_PR_boolean.json)
│   ├── examples/           # Sample data for testing
│   └── templates/          # Slack message templates
│
├── instructions/           # LLM prompt instructions
│   ├── pipelines/          # Manifests: which fragments to concat; conditional follow-ups
│   │   ├── filter.fragments
│   │   ├── main.fragments
│   │   ├── followups.manifest
│   │   └── README.md
│   ├── compare_errors_instructions.txt
│   ├── filter_instructions_for_llm.txt
│   ├── filter_hang_instructions_for_llm.txt
│   ├── instructions_for_llm.txt
│   ├── instructions_footer_for_llm.txt
│   └── hang_stage_instructions_for_llm.txt
│
├── tests/                  # Test suite (mirrors module structure)
│   ├── lib/
│   └── modules/
│
├── regression_analysis.sh          # Main triage entry point
├── filter_triage.sh        # Filter/classify triage results
├── retry_on_deterministic.sh  # Retry entry point
└── run_auto_fix.sh         # Auto-fix entry point (disabled)
```

## Data Flow

```
aggregate-workflow-data.yaml (caller)
        │
        ▼
   action.yml (composite action)
        │
        ├─► find_boundaries.sh ──► workflow_finder → run_processor → job_matcher
        │       Finds first-good / first-bad run boundaries
        │
        ├─► download_data_between_commits.sh ──► batch_downloader / single_commit
        │       Fetches commit metadata, PRs, approvers
        │
        ├─► get_logs.sh / get_annotations.sh ──► log_parser
        │       Downloads job logs and error annotations
        │
        ├─► regression_analysis.sh ──► llm_runner (Copilot analysis)
        │       Root-cause analysis via LLM
        │
        ├─► filter_triage.sh ──► llm_runner (Copilot classification)
        │       Classifies triage output into cases
        │
        ├─► retry_on_deterministic.sh ──► retry_orchestrator
        │       │   ├─► hardware_checker (validate hardware)
        │       │   ├─► run_trigger (rerun job, poll for completion)
        │       │   ├─► result_comparator (compare errors via LLM)
        │       │   └─► slack_api (send retry result)
        │
        └─► slack-report-regression-analysis (separate action)
                Posts final Slack message
```

## Module Dependencies

All modules depend on `lib/` (loaded via source guards to prevent double-loading):

- **common.sh**: Required by everything. Provides logging, path helpers, JSON utilities.
- **config.sh**: Sources common.sh. Provides repo config, directory setup.
- **github_api.sh**: Sources common.sh. Provides `gh_api`, `gh_api_jq`, `gh_api_post`.
- **validation.sh**: Sources common.sh. Provides SHA/URL/JSON validation.
- **slack_api.sh**: Sources common.sh + config.sh. Provides Slack message posting.

Module inter-dependencies:
- `retry_orchestrator.sh` → `run_trigger.sh`, `result_comparator.sh`, `hardware_checker.sh`, `slack_api.sh`
- `find_boundaries.sh` → `workflow_finder.sh`, `run_processor.sh`, `job_matcher.sh`
- `batch_downloader.sh` → `single_commit.sh`, `commit_validator.sh`

## Source Guard Pattern

All library and module files use a guard to prevent double-sourcing:

```bash
if [ -n "${_MODULE_LOADED:-}" ]; then
    return 0
fi
_MODULE_LOADED=1
```

## Testing

Tests live under `tests/` and mirror the module structure. All tests use `testing_lib_files/test_harness.sh` which provides `assert`, `assert_eq`, `assert_fails`, and `test_summary`.

Tests mock external dependencies (`gh`, `copilot`) by defining wrapper functions or creating mock scripts in `$PATH`.

Run all tests: see `.github/workflows/test-regression-analysis-lib.yml`.

## Configuration

Runtime configuration is via environment variables with defaults in `lib/config.sh`:

| Variable | Default | Purpose |
|---|---|---|
| `AT_OWNER` | `tenstorrent` | GitHub org |
| `AT_REPO` | `tt-metal` | GitHub repo |
| `AT_BATCH_SIZE` | `10` | Commits per batch |
| `AT_MAX_BATCHES` | `100` | Max download batches |
| `AT_PER_PAGE` | `100` | GitHub API page size |
| `AT_FAILURE_LIMIT` | `5` | Max failures before cancel |
| `AT_RUN_LIMIT_WITHOUT_SUCCESS` | `20` | Runs to check without a success |
