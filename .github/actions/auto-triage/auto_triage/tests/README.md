# Test Suite

## Structure

Tests mirror the source directory structure:

```
tests/
├── lib/                          # Tests for shared libraries
│   ├── common_test.sh
│   ├── config_test.sh
│   ├── github_api_test.sh
│   ├── slack_api_test.sh
│   └── validation_test.sh
└── modules/                      # Tests for domain modules
    ├── analysis/
    │   ├── auto_triage_test.sh
    │   ├── filter_triage_test.sh
    │   └── llm_runner_test.sh
    ├── auto_fix/
    │   └── pr_validator_test.sh
    ├── boundaries/
    │   ├── job_matcher_test.sh
    │   ├── run_processor_test.sh
    │   └── workflow_finder_test.sh
    ├── commit_data/
    │   ├── batch_downloader_test.sh
    │   ├── commit_validator_test.sh
    │   ├── download_commits_test.sh
    │   └── single_commit_test.sh
    ├── logs/
    │   ├── get_annotations_test.sh
    │   └── log_parser_test.sh
    └── retry/
        ├── hardware_checker_test.sh
        ├── result_comparator_test.sh
        ├── retry_orchestrator_test.sh
        └── run_trigger_test.sh
```

## Test Harness

All tests source `testing_lib_files/test_harness.sh` which provides:

- `assert "description" <command>` — pass if command exits 0
- `assert_eq "description" "actual" "expected"` — pass if strings match
- `assert_fails "description" <command>` — pass if command exits non-zero
- `test_summary` — prints pass/fail counts, exits non-zero if any failed

## Mocking

Tests mock external dependencies by shadowing commands:

- **`gh`**: Defined as a function that pattern-matches API paths and returns canned JSON
- **`copilot`**: Defined as a function that writes expected output files
- **`jq`**: Real `jq` is used (not mocked)

## Running Tests

Individual test:
```bash
cd .github/actions/auto-triage/auto_triage
bash tests/modules/retry/hardware_checker_test.sh
```

All tests via CI: see `.github/workflows/test-auto-triage-lib.yml`

## CI Integration

The workflow `test-auto-triage-lib.yml` runs all `*_test.sh` files matching:
- `tests/lib/*_test.sh`
- `tests/modules/**/*_test.sh`
