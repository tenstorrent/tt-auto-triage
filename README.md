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
   - Invoked via `.github/workflows/triage-create-issues.yaml` (or orchestrated through `.github/workflows/triage-ci.yaml`)
   - Detects jobs that fail deterministically across consecutive runs
   - Drafts issue content from failing logs
   - Creates new issues (or runs in dry-run mode)
   - Avoids duplicate issues for already tracked workflow/job pairs
   - Produces markdown summaries for review

3. **Slack Output Analysis**: Groups error messages from Slack channels into error clusters:
   - Invoked via `.github/actions/slack_output_analysis/action.yml`
   - Fetches error messages from Slack channels
   - Groups similar errors using ML-based similarity matching
   - Holds cluster state in a JSON artifact carried between runs, not in GitHub issues
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
- Cursor API key for issue drafting → Store as `CURSOR_API_KEY`

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
      CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}
```

This stage finds deterministic failures, drafts issue content from logs, and creates issues while preventing duplicates for already tracked workflow/job pairs.

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

### Slack Output Analysis

The `slack_output_analysis` action groups error messages from Slack channels into error clusters
held in a JSON state file.

#### Basic Usage

```yaml
jobs:
  group-slack-errors:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Group Slack errors
        uses: tenstorrent/tt-auto-triage/.github/actions/slack_output_analysis@main
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          slack_token: ${{ secrets.SLACK_BOT_TOKEN }}
          channel_id: ${{ secrets.SLACK_CHANNEL_ID }}
          start_date: "January 1, 2026"
          state_path: ${{ github.workspace }}/grouping-state
```

#### Required Inputs

- `github_token`: GitHub token for reading job metadata (commit hashes, job names) and artifacts
- `slack_token`: Slack Bot Token for fetching messages
- `channel_id`: Slack channel ID to fetch messages from

#### Optional Inputs

- `start_date`: Start date for fetching Slack messages (format: `"January 1, 2026"`, default: `"January 1, 2026"`)
- `end_date`: End date cutoff for fetching messages (format: `"January 31, 2026"`, default: `""` for no cutoff)
- `workflow_file`: Workflow file path for finding previous runs to create incremental report (e.g., `".github/workflows/analyze-ND-failures.yml"`, default: `""`)
- `state_path`: Directory holding the cluster state carried between runs (default: `<workspace>/grouping-state`)

#### Outputs

- `incremental_report_path`: Path to the incremental error report (new entries only)
- `state_path`: Directory containing the updated cluster state and API caches

#### Example: Carrying State Between Runs

Grouping quality depends on the previous run's clusters, so restore the state artifact before the
action and upload it afterwards.

```yaml
jobs:
  group-slack-errors:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read
    steps:
      - uses: actions/checkout@v4

      # Ask for successful runs directly rather than filtering a page of
      # completed ones: a day of failures is enough to push the last success off
      # the page, and not finding it discards the clustering history.
      - name: Find previous grouping state
        id: prev-state
        uses: actions/github-script@v7
        with:
          script: |
            const runs = await github.request('GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs', {
              owner: context.repo.owner,
              repo: context.repo.repo,
              workflow_id: 'your-workflow.yml',
              branch: context.ref.replace('refs/heads/', ''),
              status: 'success',
              exclude_pull_requests: true,
              per_page: 1
            });
            const run = (runs.data.workflow_runs || [])[0];
            if (!run) {
              core.setOutput('has_state', 'false');
              return;
            }
            // Whether the artifact is present is what separates a genuine cold
            // start from a download that must not fail quietly.
            const artifacts = await github.paginate(
              'GET /repos/{owner}/{repo}/actions/runs/{run_id}/artifacts',
              { owner: context.repo.owner, repo: context.repo.repo, run_id: run.id, per_page: 100 }
            );
            const state = artifacts.find(a => a.name === 'grouping-state' && !a.expired);
            core.setOutput('run_id', String(run.id));
            core.setOutput('has_state', state ? 'true' : 'false');

      # No continue-on-error: the step above already confirmed the artifact is
      # there, so a failure here is real and must not be read as a first run.
      # Swallowing it would replace the accumulated clusters with an empty state.
      - name: Download previous grouping state
        if: ${{ steps.prev-state.outputs.has_state == 'true' }}
        uses: actions/download-artifact@v4
        with:
          name: grouping-state
          path: ${{ github.workspace }}/grouping-state
          run-id: ${{ steps.prev-state.outputs.run_id }}
          github-token: ${{ secrets.GITHUB_TOKEN }}

      - name: Group Slack errors
        uses: ./.github/actions/slack_output_analysis
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          slack_token: ${{ secrets.SLACK_BOT_TOKEN }}
          channel_id: ${{ secrets.SLACK_CHANNEL_ID }}
          start_date: ${{ inputs.start_date || 'January 1, 2026' }}
          state_path: ${{ github.workspace }}/grouping-state

      - name: Upload grouping state
        if: ${{ success() }}
        uses: actions/upload-artifact@v4
        with:
          name: grouping-state
          path: grouping-state/
          if-no-files-found: error
          retention-days: 90
```

Two different retention windows are at work here and they are deliberately not the same number.
`retention-days` is how long GitHub keeps the artifact, and it only has to outlive the gap between
one run and the next; 90 days is slack for a schedule that gets paused or disabled, and it keeps the
API caches usable since commit hashes and job names never go stale. The 30 days below is how far
back runs are kept *inside* the state, which is what bounds cluster history. Shortening
`retention-days` to 30 would not prune anything sooner, it would only add a way to lose the state.

Without the restored artifact the action still runs, but every error becomes its own cluster and
grouping quality rebuilds over the following month.

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
2. **Detect Deterministic Failures**: Finds jobs failing for N consecutive runs
3. **Deduplicate Against Open Issues**: Skips workflow/job pairs that are already tracked
4. **Draft Issue Content**: Uses Cursor agent output plus run logs to generate issue title/body
5. **Create Issues**: Opens GitHub issues when `CREATE_ISSUES=true` (or records dry-run results)
6. **Summarize Results**: Produces markdown summary output for auditing

### Slack Output Analysis Pipeline

1. **Fetch Messages**: Downloads error messages from the specified Slack channel
2. **Extract Errors**: Extracts error messages from Slack messages (focuses on non-deterministic errors by default)
3. **Sync Clusters**: Matches each new error against existing cluster centroids using ML-based
   similarity, appending to a cluster or starting a new one. Runs older than 30 days are pruned,
   and clusters left with no runs are dropped.
4. **Generate Reports**: Creates error reports and incremental reports comparing against previous runs

Cluster state lives in `cluster_state.json` inside the `grouping-state` artifact, alongside the
GitHub API caches. The calling workflow restores the previous run's artifact before the action runs
and uploads the updated one afterwards. No GitHub issues are created, read, updated, or closed.

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
- **grouping-state**: Contains `cluster_state.json` and the GitHub API caches, uploaded by the calling workflow

## Contributing

We welcome contributions to tt-auto-triage! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:

- Reporting bugs
- Suggesting features
- Submitting pull requests
- Development guidelines

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for the full license text.

For clarification on how this license applies to hardware, models, and IP, please see [LICENSE_understanding.txt](LICENSE_understanding.txt).
