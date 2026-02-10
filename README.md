# tt-auto-triage

A GitHub Actions-based system that automatically triages failing CI/CD pipeline jobs using AI (GitHub Copilot CLI) to identify root causes, categorize failures, and notify relevant developers via Slack.

## Overview

This repository provides two main capabilities:

1. **Auto-Triage**: AI-powered analysis of failing GitHub Actions workflows that:
   - Identifies the last successful run and first failing run
   - Downloads commit metadata between those boundaries
   - Uses GitHub Copilot CLI (LLM) to analyze code changes and determine root causes
   - Categorizes failures into 5 distinct cases
   - Identifies relevant developers (codeowners, commit authors)
   - Optionally creates auto-fix PRs for simple fixes
   - Sends formatted Slack notifications with triage results

2. **Slack Output Analysis**: Syncs error messages from Slack channels to GitHub issues:
   - Fetches error messages from Slack channels
   - Extracts and groups similar errors using ML-based similarity matching
   - Creates or updates GitHub issues from Slack messages
   - Generates error reports and incremental reports

## Bare Minimum Quickstart

### Auto-Triage (Minimal Setup)

**Prerequisites:**
- GitHub Personal Access Token with `copilot` scope → Store as `COPILOT_PAT` secret
- Slack Bot Token → Store as `SLACK_BOT_TOKEN` secret  
- Slack Channel ID → Store as `SLACK_CHANNEL_ID` secret

**Minimal workflow:**

```yaml
- uses: actions/checkout@v4
- uses: tenstorrent-metal/tt-auto-triage/.github/actions/auto-triage@main
  with:
    workflow-name: "ci.yml"
    job-name: "test-job"
    copilot-pat: ${{ secrets.COPILOT_PAT }}
  env:
    SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
    SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
```

That's it! The action will analyze the failure and send results to Slack.

### Slack Output Analysis (Minimal Setup)

**Prerequisites:**
- GitHub Personal Access Token → Store as `GITHUB_TOKEN` secret
- Slack Bot Token → Store as `SLACK_BOT_TOKEN` secret
- Slack Channel ID → Store as `SLACK_CHANNEL_ID` secret

**Minimal workflow:**

```yaml
- uses: actions/checkout@v4
- uses: tenstorrent-metal/tt-auto-triage/.github/actions/slack_output_analysis@main
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    slack_token: ${{ secrets.SLACK_BOT_TOKEN }}
    channel_id: ${{ secrets.SLACK_CHANNEL_ID }}
```

This will sync errors from Slack to GitHub issues using default settings.

---

## Failure Case Categories

The auto-triage system categorizes failures into 5 cases:

- **Case 1**: Deterministic failure with identified commit - A specific commit clearly explains the failure
- **Case 2**: Deterministic failure but commit unknown - Failure is deterministic but the exact commit cannot be identified (expired logs, >100 commits, etc.)
- **Case 3**: Failure likely outside tt-metal - Non-deterministic, infrastructure-related, or external issues
- **Case 4**: Deterministic failure with multiple plausible commits - Multiple commits could plausibly cause the failure
- **Case 5**: Deterministic failure with incomplete commit metadata - Failure is deterministic but some commit metadata couldn't be downloaded

## Quickstart

### Auto-Triage

The `auto-triage` action analyzes failing GitHub Actions workflows and produces triage reports.

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

      - name: Run auto-triage
        uses: tenstorrent-metal/tt-auto-triage/.github/actions/auto-triage@main
        with:
          workflow-name: "your-workflow.yml"
          job-name: "your-job-name"
          copilot-pat: ${{ secrets.COPILOT_PAT }}
        env:
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
```

#### Required Inputs

- `workflow-name`: The workflow file name to inspect (e.g., `"ci.yml"`)
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
- `explanation.md`: Detailed markdown report in `.auto_triage/output/explanation.md`
- `slack_message.json`: Formatted Slack message payload in `.auto_triage/output/slack_message.json`
- Artifacts: Auto-triage data and output are uploaded as workflow artifacts

#### Example: Triggering on Workflow Failure

```yaml
name: Auto Triage on Failure

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

      - name: Run auto-triage
        uses: tenstorrent-metal/tt-auto-triage/.github/actions/auto-triage@main
        with:
          workflow-name: "ci.yml"
          job-name: ${{ github.event.workflow_run.jobs[0].name }}
          copilot-pat: ${{ secrets.COPILOT_PAT }}
        env:
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
```

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
        uses: tenstorrent-metal/tt-auto-triage/.github/actions/slack_output_analysis@main
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

### Auto-Triage Pipeline

1. **Find Boundaries**: Identifies the last successful run and first failing run for the specified workflow/job
2. **Download Slack Directory**: Fetches Slack user/group directory for developer lookups
3. **Filter Stage**: Uses LLM to determine deterministic failures and gather commit metadata
4. **Analysis Stage**: Uses LLM to analyze commits, assign confidence scores, and categorize the failure
5. **Auto-Fix (Optional)**: Attempts to create a draft PR for simple fixes (Case 1/2 only)
6. **Retry Logic (Optional)**: Re-runs deterministic failures on supported hardware to confirm determinism
7. **Slack Notification**: Formats and sends triage results to Slack

### Slack Output Analysis Pipeline

1. **Fetch Messages**: Downloads error messages from the specified Slack channel
2. **Extract Errors**: Extracts error messages from Slack messages (focuses on non-deterministic errors by default)
3. **Group Similar Errors**: Uses ML-based similarity matching to group related errors (rebuild mode only)
4. **Create/Update Issues**: Creates new GitHub issues or updates existing ones with new error occurrences
5. **Generate Reports**: Creates error reports and incremental reports comparing against previous runs

## Requirements

- GitHub Actions runner with Ubuntu Linux
- GitHub Copilot CLI access (for auto-triage)
- Slack Bot Token with appropriate permissions
- GitHub Personal Access Token with required scopes

## Artifacts

Both actions produce artifacts that can be downloaded from workflow runs:

- **auto-triage-data**: Contains commit metadata, boundary information, and intermediate analysis data
- **auto-triage-output**: Contains the final `explanation.md` and `slack_message.json` files
- **error-report**: Contains the error report JSON (slack_output_analysis)
- **incremental-error-report**: Contains incremental error report comparing against previous run

## Contributing

This repository is designed for use with the `tt-metal` repository. When making changes:

1. Test changes in a fork or test repository first
2. Ensure all scripts have proper error handling
3. Update documentation for any new inputs/outputs
4. Follow the existing code style and structure

## License

See LICENSE file for details.
