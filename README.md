# tt-auto-triage

A GitHub Actions-based system for CI triage and signal hygiene: identify likely culprit commits for failing jobs, create and maintain deterministic-failure issues, and sync recurring Slack errors into actionable GitHub issues.

> **Not the same as the n8n "auto-triage" workflow.** There is a separate, n8n-based auto-triage workflow whose logic lives in [`tenstorrent/vulcan-orchestration`](https://github.com/tenstorrent/vulcan-orchestration/tree/main/workflows). This repo, `tt-auto-triage`, is a distinct GitHub Actions-native system — different runtime, different logic, no shared code. Don't look here for the n8n workflow's behavior, and don't look there for this repo's.

For internal usage guides and runbooks, see the [Regression Analysis Confluence page](https://tenstorrent.atlassian.net/wiki/spaces/MI6/pages/1794441312/How+to+Use+Regression+Handling).

## What's Here

Three independent pieces, each usable on its own:

### 1. Regression Analysis

`.github/actions/regression-analysis` — a composite action you attach to a failing CI job. It finds the last successful run and the first failing run, downloads the commits in between, and uses the GitHub Copilot CLI to identify the likely culprit commit and classify the failure into one of [5 cases](#failure-case-categories). It can optionally re-run the job on real hardware to confirm the failure is deterministic before reporting, draft an auto-fix PR for simple cases, and posts results to Slack.

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

### 2. Deterministic Failure Issue Lifecycle

`.github/workflows/triage-create-issues.yaml` — a reusable workflow, typically run on a schedule, that scans recent runs across a target repo and files a tt-metal issue for jobs that are **failing consistently**. "Consistently" is threshold-based and adapts to how often a workflow runs: a job needs `consecutive-failures-high-volume` (default 4) failures in a row to qualify if its workflow runs more than `high-volume-runs-per-day` (default 5) times a day on main, or just `consecutive-failures-low-volume` (default 2) if it's a low-volume workflow — so a noisy, frequently-run pipeline isn't flagged on a couple of flaky runs. Before drafting anything it re-checks that the job hasn't since recovered, then uses the Copilot CLI to draft an issue from the logs and only files it if the draft comes back medium/high confidence and no issue is already tracked for that workflow/job pair.

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

### 3. Slack Output Analysis

`.github/actions/slack_output_analysis` — syncs recurring error messages posted to a Slack channel into GitHub issues, grouping similar errors via text-similarity matching so repeat instances of the same error land on one issue instead of many. Supports an `update` mode (sync new errors since last run) and a `rebuild` mode (recreate all issues from scratch).

```yaml
- uses: actions/checkout@v4
- uses: tenstorrent/tt-auto-triage/.github/actions/slack_output_analysis@main
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    slack_token: ${{ secrets.SLACK_BOT_TOKEN }}
    channel_id: ${{ secrets.SLACK_CHANNEL_ID }}
```

### Bug-Escape Guidance (separate workstream)

Guidance embedded in the regression-analysis LLM instructions (`regression_analysis/instructions/instructions_footer_for_llm.txt`) that flags when a failure indicates missing lower-level test coverage and recommends shift-left additions. This is independent of the issue grouping/maintenance logic above.

## Failure Case Categories

Regression Analysis classifies every failure into one of 5 cases:

- **Case 1**: Deterministic, culprit commit identified
- **Case 2**: Deterministic, but the culprit commit couldn't be identified (expired logs, >100 commits, etc.)
- **Case 3**: Likely outside tt-metal — non-deterministic, infra, or external
- **Case 4**: Deterministic, multiple plausible culprit commits
- **Case 5**: Deterministic, but some commit metadata couldn't be downloaded

## How It Works

**Regression Analysis pipeline:** find last-good/first-bad run boundaries → download the Slack directory (for developer lookups) → LLM filter stage (deterministic? gather commit metadata) → LLM analysis stage (assign confidence, categorize) → optional auto-fix PR (Case 1/2 only) → optional hardware retry to confirm determinism → post to Slack.

**Issue Lifecycle pipeline:** download recent workflow run data → detect jobs on a qualifying consecutive-failure streak (adaptive threshold, see above) → drop pairs that already have an open tracked issue → re-confirm the job hasn't recovered → draft title/body via Copilot CLI, gated on confidence → create the issue (or dry-run) → write a markdown summary.

**Slack Output Analysis pipeline:** fetch channel messages → extract error text → (rebuild mode) group similar errors via similarity matching → create/update/close issues → generate a full and incremental error report.

## Requirements

- GitHub Actions runner with Ubuntu Linux
- GitHub Copilot CLI access (regression-analysis, create-issues)
- Slack Bot Token with appropriate permissions
- GitHub tokens with the scopes noted in each snippet above

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on reporting bugs, suggesting features, and submitting pull requests.

## License

Apache License 2.0 — see [LICENSE](LICENSE). For how it applies to hardware, models, and IP, see [LICENSE_understanding.txt](LICENSE_understanding.txt).
