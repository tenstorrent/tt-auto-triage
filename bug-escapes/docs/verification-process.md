# Bug Escape Verification Process

When the pipeline flags a commit as the fix for a bug escape, this process
verifies whether the attribution is correct by running the failing test on
both sides of the commit.

## Inputs

From `bug-escapes-output.json`, each bug escape provides:

- `fix_commit_sha` — the commit the pipeline believes fixed the failure
- `test_pipeline` — the workflow file (e.g. `.github/workflows/galaxy-e2e-tests.yaml`)
- `test_job` — the specific job name (e.g. `BH Galaxy CCL tests`)
- `test_name` — the full pytest path including parametrization

**Verify `test_pipeline` before using it.** If the field is absent or stale, look it up
from Snowflake rather than guessing:

```sql
SELECT DISTINCT j.NAME, j.GITHUB_JOB_LINK
FROM TTDATASF.SW_TEST.CICD_TEST t
JOIN TTDATASF.SW_TEST.CICD_JOB j ON t.CICD_JOB_ID = j.CICD_JOB_ID
WHERE t.TEST_CASE_ID = <test_case_id>
  AND j.JOB_START_TS >= DATEADD('day', -30, CURRENT_TIMESTAMP())
LIMIT 5;
```

The `j.NAME` field (e.g. `models-unit-tests / Qwen3-32B unit tests (Galaxy) [wh_galaxy_perf]`)
tells you both the workflow and the SKU. Cross-reference against the GitHub workflows list
(`GET /repos/tenstorrent/tt-metal/actions/workflows`) by name to get the workflow file and ID.
**Never guess the workflow from memory.**

## Steps

### 1. Identify the parent commit

```bash
PARENT=$(git rev-parse ${FIX_COMMIT}^)
```

### 2. Create two branches

```bash
git checkout -b ebanerjee/verify-before $PARENT
git checkout -b ebanerjee/verify-after  $FIX_COMMIT
```

### 3. Prune the test matrix on both branches

The test YAML file is discovered from the impl workflow
(`*-impl.yaml` → `TESTS_YAML_PATH` env var → the YAML file).

Replace its contents with only the relevant test group, narrowing
the pytest command to the specific parametrized test:

```yaml
- name: <test_job name>
  cmd: pytest "<test_file>::<test_function>[<parametrization>]"
  skus:
    <matching_sku>:
      timeout: 40
  owner_id: <original owner>
  team: <original team>
```

**This step is mandatory — do not skip it.** Dispatching the full workflow wastes
runner time and may run dozens of unrelated tests on expensive Galaxy hardware.
Always prune to the single failing test before dispatching.

**IMPORTANT**: Use the full bracket node ID in the pytest path, NOT `-k`.
The `-k` flag interprets `=` signs and `-` as Python expression operators,
which silently deselects parametrized tests whose IDs contain those
characters (e.g. `num_devices=4-num_links=1-...`). This causes `collected
1 item / no tests ran` with no obvious error. The bracket syntax
(`::test_func[param=val-param2=val2]`) works correctly and must be quoted
to protect the brackets from shell expansion.

Commit this change on both branches and push.

### 4. Dispatch both workflow runs

```bash
gh workflow run <test_pipeline> --ref ebanerjee/verify-before \
  -f blackhole=true -f wormhole=false   # adjust SKU flags as needed

gh workflow run <test_pipeline> --ref ebanerjee/verify-after \
  -f blackhole=true -f wormhole=false
```

The SKU flags depend on which `skus:` the test group uses:
- `bh_galaxy` → `blackhole=true, wormhole=false`
- `wh_galaxy` → `wormhole=true, blackhole=false`
- both → `blackhole=true, wormhole=true`

### 5. Wait and check results

```bash
gh run view <before_run_id> --json status,conclusion
gh run view <after_run_id> --json status,conclusion
```

Expected outcomes for a correct attribution:
- **BEFORE** (parent of fix): `conclusion: failure`
- **AFTER** (fix commit): `conclusion: success`

### 6. Interpret results

| BEFORE | AFTER | Meaning |
|--------|-------|---------|
| fail   | pass  | Correct attribution — this commit fixed the bug |
| fail   | fail  | Wrong attribution — the fix was a different commit |
| pass   | pass  | Test is flaky or the failure window was misidentified |
| pass   | fail  | Inverted — something else is wrong |

### 7. Cleanup

```bash
git push origin --delete ebanerjee/verify-before ebanerjee/verify-after
git branch -D ebanerjee/verify-before ebanerjee/verify-after
```

## Finding the Fix Commit When Snowflake Has No Intermediate Data

The pipeline outputs `last_failing_sha` and `first_passing_sha`. The fix commit is somewhere
between them. Often there are multiple commits in that range with no CI runs in Snowflake.

**Step 1 — Check Snowflake for intermediate runs first (free oracle):**

```sql
SELECT p.GIT_COMMIT_HASH, p.PIPELINE_START_TS, t.SUCCESS
FROM TTDATASF.SW_TEST.CICD_TEST t
JOIN TTDATASF.SW_TEST.CICD_JOB j ON t.CICD_JOB_ID = j.CICD_JOB_ID
JOIN TTDATASF.SW_TEST.CICD_PIPELINE p ON j.CICD_PIPELINE_ID = p.CICD_PIPELINE_ID
WHERE t.TEST_CASE_ID = <test_case_id>
  AND p.PIPELINE_START_TS BETWEEN '<last_fail_ts>' AND '<first_pass_ts>'
ORDER BY p.PIPELINE_START_TS ASC;
```

If this returns intermediate runs, they narrow the window without any hardware cost.

**Step 2 — If no intermediate data, binary search with manual dispatches:**

1. `GET /repos/tenstorrent/tt-metal/compare/{last_failing}...{first_passing}` → list of N commits
2. Pick the commit at position N/2 (middle). Create a branch at that SHA. Push it.
3. Dispatch the pruned verification workflow on that branch (Step 3-4 above).
4. If PASS → fix is in the first half. If FAIL → fix is in the second half.
5. Repeat on the narrowed range. O(log N) dispatches total.

**What NOT to use for finding fix commits:**

The `bisect-dispatch.yaml` workflow finds **breaking** commits — it takes `good` (old, passing)
and `bad` (new, failing) and binary-searches for the first bad commit. This is the **wrong
direction** for finding fix commits. Do not use the bisect workflow for fix commit search;
use the binary search approach above with individual pruned dispatches.

---

## Known Pitfalls

### 1. Wrong workflow selection

**Error**: Guessing or remembering the workflow name without verifying.

**Fix**: Always look up the job name from Snowflake (`CICD_JOB.NAME`) for a recent run of the
test, then find the corresponding workflow file. See the "Verify `test_pipeline`" note in
the Inputs section.

### 2. Skipping test matrix pruning

**Error**: Dispatching the full workflow instead of pruning to the failing test only.

**Fix**: Step 3 (prune test matrix YAML) is mandatory before every verification dispatch.
Skipping it wastes compute on unrelated tests and makes results harder to interpret.

### 3. Confusing breaking commit vs. fix commit

**Error**: The bisect workflow finds the commit that *broke* the test. The bug escape campaign
looks for the commit that *fixed* the test. These are different commits in different directions.

**Fix**: Bisect workflow → breaking commit (good=old, bad=new). Fix commit search → binary
search on the `last_failing...first_passing` range using pruned verification dispatches.

---

## Future Automation

This could become a Phase 5 in the pipeline:

1. For each bug escape with a `fix_commit_sha`, automatically:
   - Create ephemeral branches at `fix^` and `fix`
   - Prune the test matrix YAML programmatically
   - Dispatch both workflow runs via `gh workflow run`
   - Poll until both complete
   - Compare conclusions and annotate the bug escape with
     `verification: "confirmed"` or `verification: "unconfirmed"`
   - Delete the ephemeral branches

2. Key implementation details:
   - Discovering the test YAML: parse `*-impl.yaml` for `TESTS_YAML_PATH`
   - Matching test group to job name: the `name:` field in the YAML
     matches the job display name in GitHub Actions
   - SKU detection: parse the `skus:` keys from the matching test group
   - Parametrization: use the full bracket node ID in the pytest path
     (NOT `-k`, which breaks on `=` and `-` in parametrized IDs)
   - Branch naming: use a unique prefix to avoid collisions
     (e.g. `verify-${escape_hash}-before/after`)
   - Timeout: set a maximum wait time for verification runs
