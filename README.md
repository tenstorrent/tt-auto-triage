# tt-auto-triage

A GitHub Actions-based system for CI triage and signal hygiene: identify likely culprit commits for failing jobs, create and maintain deterministic-failure issues, and sync recurring Slack errors into actionable GitHub issues.

## Overview

This repository provides the following capabilities:

1. **Regression Analysis**: AI-powered analysis of failing GitHub Actions workflows that:
   - Invoked via `.github/actions/regression-analysis/action.yml`
   - Identifies the last successful run and first failing run
   - Downloads commit metadata between those boundaries
   - Uses GitHub Copilot CLI (LLM) to analyze code changes and determine root causes
   - Categorizes failures into 5 distinct cases
   - Identifies relevant developers (codeowners, commit authors)
   - Optionally creates auto-fix PRs for simple fixes
   - Sends formatted Slack notifications with triage results
2. **Deterministic Failure Issue Lifecycle**: Workflow-driven issue management for persistent CI regressions:
   - Invoked via `.github/workflows/triage-create-issues.yaml` (reusable workflow, runs `python3 -m tools.ci.create_issues`)
   - Detects jobs that fail deterministically across consecutive runs, with an adaptive streak threshold: high-volume workflows (more main-branch runs per day than `high-volume-runs-per-day`) require more consecutive failures before an issue is filed than low-volume workflows, so noisy/frequent pipelines don't get flagged prematurely
   - Re-checks each candidate against the latest run before spending time on it, in case it recovered since the last data snapshot
   - Drafts issue title/body from failing logs using the GitHub Copilot CLI, and only files an issue when the drafted result is medium/high confidence
   - Creates new issues (or runs in dry-run mode via `CREATE_ISSUES=false`)
   - Avoids duplicate issues for already tracked workflow/job pairs (tracked via metadata markers in the issue body)
   - Produces markdown summaries for review and an `issues.json` artifact

3. **Slack Output Analysis**: Syncs error messages from Slack channels to GitHub issues:
   - Invoked via `.github/actions/slack_output_analysis/action.yml`
   - Fetches error messages from Slack channels
   - Extracts errors and generates reports
   - Groups similar errors for analysis/reporting in rebuild mode
   - Creates, updates, and closes GitHub issues in sync flows
   - Generates error reports and incremental reports

4. **Bug-Escape Guidance (Separate Workstream)**:
   - Invoked as guidance in `.github/actions/regression-analysis/regression_analysis/instructions/instructions_footer_for_llm.txt`
   - Documents when failures indicate missing lower-level coverage
   - Recommends shift-left test additions independently of issue grouping/maintenance logic

## Documentation

For internal usage guides and runbooks, see the [Regression Analysis Confluence page](https://tenstorrent.atlassian.net/wiki/spaces/MI6/pages/1794441312/How+to+Use+Regression+Handling).

## Quickstart

### Regression Analysis (Minimal Setup)

**Prerequisites:**
- GitHub Personal Access Token with `copilot` scope → Store as `COPILOT_PAT` secret
- Slack Bot Token → Store as `SLACK_BOT_TOKEN` secret  
- Slack Channel ID → Store as `SLACK_CHANNEL_ID` secret

**Minimal workflow:**

```yaml
- uses: actions/checkout@v4
- uses: tenstorrent/tt-auto-triage/.github/actions/regression-analysis@main
  with:
    workflow-name: "galaxy-quick"
    job-name: "test-job"
    copilot-pat: ${{ secrets.COPILOT_PAT }}
  env:
    SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
    SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
```

That's it. The action will analyze the failure, classify it, and send results to Slack.

### Deterministic Failure Issue Lifecycle (Minimal Setup)

**Prerequisites:**
- Token with read access to workflow runs/artifacts → Store as `AGGREGATE_READ_TOKEN`
- Token with write access to issue repo → Store as `ISSUE_WRITE_TOKEN`
- GitHub PAT with `copilot` scope for issue drafting → Store as `COPILOT_PAT`

**Minimal workflow (reusable workflow call):**

```yaml
jobs:
  create-issues:
    uses: tenstorrent/tt-auto-triage/.github/workflows/triage-create-issues.yaml@main
    with:
      issue-repo: "your-org/ci-issues"
      target-repo: "tenstorrent/tt-metal"
      max-issues: 5
    secrets:
      AGGREGATE_READ_TOKEN: ${{ secrets.AGGREGATE_READ_TOKEN }}
      ISSUE_WRITE_TOKEN: ${{ secrets.ISSUE_WRITE_TOKEN }}
      COPILOT_PAT: ${{ secrets.COPILOT_PAT }}
```

This stage finds deterministically-failing jobs (based on consecutive-failure streaks, see [Deterministic Failure Issue Lifecycle](#deterministic-failure-issue-lifecycle) below), drafts issue content from logs, and creates issues while preventing duplicates for already tracked workflow/job pairs.

### Slack Output Analysis (Minimal Setup)

**Prerequisites:**
- GitHub Personal Access Token → Store as `GITHUB_TOKEN` secret
- Slack Bot Token → Store as `SLACK_BOT_TOKEN` secret
- Slack Channel ID → Store as `SLACK_CHANNEL_ID` secret

**Minimal workflow:**

```yaml
- uses: actions/checkout@v4
- uses: tenstorrent/tt-auto-triage/.github/actions/slack_output_analysis@main
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    slack_token: ${{ secrets.SLACK_BOT_TOKEN }}
    channel_id: ${{ secrets.SLACK_CHANNEL_ID }}
```

This will sync errors from Slack to GitHub issues using default settings.

---

## Failure Case Categories

The regression-analysis system categorizes failures into 5 cases:

- **Case 1**: Deterministic failure with identified commit - A specific commit clearly explains the failure
- **Case 2**: Deterministic failure but commit unknown - Failure is deterministic but the exact commit cannot be identified (expired logs, >100 commits, etc.)
- **Case 3**: Failure likely outside tt-metal - Non-deterministic, infrastructure-related, or external issues
- **Case 4**: Deterministic failure with multiple plausible commits - Multiple commits could plausibly cause the failure
- **Case 5**: Deterministic failure with incomplete commit metadata - Failure is deterministic but some commit metadata couldn't be downloaded

## Usage

### Regression Analysis

The `regression-analysis` action analyzes failing GitHub Actions workflows and produces triage reports.

#### Basic Usage

Add the action to your workflow file:

```yaml
jobs:
  triage-failure:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read
      issues: write
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Run regression-analysis
        uses: tenstorrent/tt-auto-triage/.github/actions/regression-analysis@main
        with:
          workflow-name: "your-workflow"
          job-name: "your-job-name"
          copilot-pat: ${{ secrets.COPILOT_PAT }}
        env:
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
```

#### Required Inputs

- `workflow-name`: The workflow name to inspect, without file extension (e.g., `"ci"`)
- `job-name`: The job/subjob name within the workflow (e.g., `"test-job"`)
- `copilot-pat`: Personal Access Token for GitHub/Copilot authentication (requires `copilot` scope)

#### Optional Inputs

- `slack-test-only`: Skip analysis and only send a test Slack message (default: `"false"`)
- `slack_ts`: Slack message timestamp for threading replies (default: `""`)
- `allow-pings`: Whether to allow pinging users/groups in Slack messages (default: `"false"`)
- `send-slack-message`: Whether to send a Slack message (default: `"true"`)
- `enable-retry`: Automatically retry Case 1/4 failures on supported hardware to confirm determinism (default: `"true"`)
- `cutoff-commit`: Optional commit SHA to ignore all runs on commits newer than this one

#### Required Environment Variables

- `SLACK_BOT_TOKEN`: Slack Bot Token for sending notifications (required if `send-slack-message` is `true`)
- `SLACK_CHANNEL_ID`: Slack Channel ID to post messages to (required if `send-slack-message` is `true`)

#### Required Permissions

The workflow needs the following permissions:
- `contents: read` - To read repository contents and commit history
- `actions: read` - To read workflow run information
- `actions: write` - To trigger retry runs (if `enable-retry` is `true`)
- `issues: write` - To create/update issues (if auto-fix is enabled)

#### Outputs

The action produces:
- `explanation.md`: Detailed markdown report in `.regression_analysis/output/explanation.md`
- `slack_message.json`: Formatted Slack message payload in `.regression_analysis/output/slack_message.json`
- Artifacts: Regression analysis data and output are uploaded as workflow artifacts

#### Example: Triggering on Workflow Failure

```yaml
name: Regression Analysis on Failure

on:
  workflow_run:
    workflows: ["CI Tests"]
    types:
      - completed

jobs:
  triage:
    if: ${{ github.event.workflow_run.conclusion == 'failure' }}
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read
      issues: write
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.workflow_run.head_branch }}

      - name: Run regression-analysis
        uses: tenstorrent/tt-auto-triage/.github/actions/regression-analysis@main
        with:
          workflow-name: "ci"
          job-name: ${{ github.event.workflow_run.jobs[0].name }}
          copilot-pat: ${{ secrets.COPILOT_PAT }}
        env:
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
```

### Deterministic Failure Issue Lifecycle

The `triage-create-issues.yaml` reusable workflow scans recent CI runs for jobs that keep failing in the same way and opens a tracking issue in `issue-repo` once a job crosses its consecutive-failure threshold.

#### Basic Usage

```yaml
jobs:
  create-issues:
    uses: tenstorrent/tt-auto-triage/.github/workflows/triage-create-issues.yaml@main
    with:
      issue-repo: "tenstorrent/tt-metal"
      target-repo: "tenstorrent/tt-metal"
    secrets:
      AGGREGATE_READ_TOKEN: ${{ secrets.AGGREGATE_READ_TOKEN }}
      ISSUE_WRITE_TOKEN: ${{ secrets.ISSUE_WRITE_TOKEN }}
      COPILOT_PAT: ${{ secrets.COPILOT_PAT }}
```

#### Inputs

- `issue-repo`: Repository to create tracking issues in (default: `tenstorrent/tt-metal`)
- `target-repo`: Repository to read workflow run data from (default: `tenstorrent/tt-metal`)
- `max-issues`: Maximum number of issues to create in one run, `0` = unlimited (default: `0`)
- `workflow-filter`: Comma-separated substrings to restrict which workflow names are considered; empty means all workflows (default: `""`)
- `llm-backend`: LLM backend used to draft issue content — only `"copilot"` is currently supported (default: `"copilot"`)
- `consecutive-failures-high-volume`: Consecutive failures required before filing an issue for a **high-volume** workflow (default: `4`)
- `consecutive-failures-low-volume`: Consecutive failures required before filing an issue for a **low-volume** workflow (default: `2`)
- `high-volume-runs-per-day`: Strict cutoff (`>`) on main-branch runs in the last 24h used to classify a workflow as high-volume vs. low-volume (default: `5`)

The high/low-volume split exists so that "failing consistently" means something different depending on how often a workflow runs: a workflow that runs dozens of times a day needs a longer failure streak to rule out flakiness before an issue is opened, while a workflow that only runs a couple of times a day can be flagged after just a couple of failures in a row.

#### Required Secrets

- `AGGREGATE_READ_TOKEN`: Token with read access to workflow runs/artifacts in `target-repo`
- `ISSUE_WRITE_TOKEN`: Token with write access to create issues in `issue-repo`
- `COPILOT_PAT`: GitHub PAT with `copilot` scope, used to draft issue titles/bodies from failing logs

#### How It Decides to File an Issue

For each job whose recent-run streak meets the consecutive-failure threshold above:

1. Re-checks the job's latest run on main to confirm it hasn't since recovered (the failure-streak data can be a stale snapshot)
2. Downloads logs for that job and drafts an issue title/body with the Copilot CLI agent
3. Skips the job if the agent determines the failure isn't deterministic, or returns low confidence — only `medium`/`high` confidence drafts result in an issue
4. Skips the job if an issue is already open for that workflow/job pair (tracked via metadata markers embedded in the issue body)
5. Creates the issue (or, with `CREATE_ISSUES=false`, just records what *would* have been created — this is the workflow's dry-run mode)

Set `max-issues` to cap how many issues a single run can create, and `workflow-filter` to scope the scan to specific workflows (e.g. `"Blackhole,ops-unit-tests"`).

#### Outputs

- `summary.md`: Markdown summary of created/skipped candidates, published to the job's step summary
- `issues.json`: Artifact listing newly created and pre-existing tracked issue URLs

### Slack Output Analysis

The `slack_output_analysis` action syncs error messages from Slack channels to GitHub issues.

#### Basic Usage

```yaml
jobs:
  sync-slack-errors:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Sync Slack errors to GitHub issues
        uses: tenstorrent/tt-auto-triage/.github/actions/slack_output_analysis@main
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          slack_token: ${{ secrets.SLACK_BOT_TOKEN }}
          channel_id: ${{ secrets.SLACK_CHANNEL_ID }}
          update_mode: "update"
          start_date: "January 1, 2026"
```

#### Required Inputs

- `github_token`: GitHub Personal Access Token for creating/updating issues
- `slack_token`: Slack Bot Token for fetching messages
- `channel_id`: Slack channel ID to fetch messages from

#### Optional Inputs

- `update_mode`: Mode to use - `"update"` to sync new errors (default) or `"rebuild"` to recreate all issues from scratch
- `start_date`: Start date for fetching Slack messages (format: `"January 1, 2026"`, default: `"January 1, 2026"`)
- `end_date`: End date cutoff for fetching messages (format: `"January 31, 2026"`, default: `""` for no cutoff)
- `workflow_file`: Workflow file path for finding previous runs to create incremental report (e.g., `".github/workflows/analyze-ND-failures.yml"`, default: `""`)

#### Outputs

- `incremental_report_path`: Path to the incremental error report (new entries only)

#### Example: Manual Workflow Dispatch

```yaml
name: Sync Slack Errors to GitHub

on:
  workflow_dispatch:
    inputs:
      github_token:
        description: 'GitHub Personal Access Token'
        required: true
        type: string
      slack_token:
        description: 'Slack Bot Token'
        required: true
        type: string
      channel_id:
        description: 'Slack channel ID'
        required: true
        type: string
      update_mode:
        description: 'Mode: update or rebuild'
        required: false
        default: 'update'
        type: choice
        options:
          - update
          - rebuild
      start_date:
        description: 'Start date (format: January 1, 2026)'
        required: false
        default: 'January 1, 2026'
        type: string

jobs:
  sync-slack-errors:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Sync Slack errors to GitHub issues
        uses: ./.github/actions/slack_output_analysis
        with:
          github_token: ${{ inputs.github_token }}
          slack_token: ${{ inputs.slack_token }}
          channel_id: ${{ inputs.channel_id }}
          update_mode: ${{ inputs.update_mode || 'update' }}
          start_date: ${{ inputs.start_date || 'January 1, 2026' }}
```

## How It Works

### Regression Analysis Pipeline

1. **Find Boundaries**: Identifies the last successful run and first failing run for the specified workflow/job
2. **Download Slack Directory**: Fetches Slack user/group directory for developer lookups
3. **Filter Stage**: Uses LLM to determine deterministic failures and gather commit metadata
4. **Analysis Stage**: Uses LLM to analyze commits, assign confidence scores, and categorize the failure
5. **Auto-Fix (Optional)**: Attempts to create a draft PR for simple fixes (Case 1/2 only)
6. **Retry Logic (Optional)**: Re-runs deterministic failures on supported hardware to confirm determinism
7. **Slack Notification**: Formats and sends triage results to Slack

### Deterministic Failure Issue Lifecycle Pipeline

1. **Download Workflow Data**: Reads recent workflow runs and artifacts for the target repository
2. **Detect Consistent Failures**: Finds jobs failing for N consecutive runs, where N adapts to the workflow's run volume (see [Inputs](#inputs) above)
3. **Deduplicate Against Open Issues**: Skips workflow/job pairs that are already tracked
4. **Confirm Freshness**: Re-checks each remaining candidate against the latest run before spending log-download/LLM cost on it, in case it already recovered
5. **Draft Issue Content**: Uses the GitHub Copilot CLI agent plus run logs to generate issue title/body, and gate on medium/high confidence
6. **Create Issues**: Opens GitHub issues when `CREATE_ISSUES=true` (or records dry-run results)
7. **Summarize Results**: Produces markdown summary output for auditing

### Slack Output Analysis Pipeline

1. **Fetch Messages**: Downloads error messages from the specified Slack channel
2. **Extract Errors**: Extracts error messages from Slack messages (focuses on non-deterministic errors by default)
3. **Group Similar Errors (Rebuild Mode)**: Uses ML-based similarity matching for grouped analysis/reporting
4. **Issue Sync**: Creates/updates issues in update mode, recreates issues in rebuild mode, and applies close/cleanup logic during sync
5. **Generate Reports**: Creates error reports and incremental reports comparing against previous runs

### Bug-Escape Guidance (Separate Workstream)

This is intentionally separate from issue grouping and issue maintenance workflows. It focuses on identifying likely bug escapes and proposing shift-left test coverage improvements in regression-analysis outputs.

## Requirements

- GitHub Actions runner with Ubuntu Linux
- GitHub Copilot CLI access (for regression-analysis)
- Slack Bot Token with appropriate permissions
- GitHub Personal Access Token with required scopes

## Artifacts

Both actions produce artifacts that can be downloaded from workflow runs:

- **regression-analysis-data**: Contains commit metadata, boundary information, and intermediate analysis data
- **regression-analysis-output**: Contains the final `explanation.md` and `slack_message.json` files
- **error-report**: Contains the error report JSON (slack_output_analysis)
- **incremental-error-report**: Contains incremental error report comparing against previous run

## Contributing

We welcome contributions to tt-auto-triage! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Reporting bugs
- Suggesting features
- Submitting pull requests
- Development guidelines

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for the full license text.

For clarification on how this license applies to hardware, models, and IP, please see [LICENSE_understanding.txt](LICENSE_understanding.txt).
