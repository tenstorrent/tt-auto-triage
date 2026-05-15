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
