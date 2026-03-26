# Testing hang pipelines and instruction assembly

Use this checklist before merging changes to `*.fragments`, `followups.manifest`, `hang_detect.sh`, or `instructions_pipeline.sh`.

## 1. Static checks

```bash
cd .github/actions/auto-triage/auto_triage
shellcheck auto_triage.sh filter_triage.sh lib/hang_detect.sh lib/instructions_pipeline.sh get_triage_artifacts.sh
```

Fix any new warnings.

## 2. Unit / harness tests (if present)

Run the repo’s auto-triage test workflow or local harness (see `ARCHITECTURE.md` → Testing and `.github/workflows/test-auto-triage-lib.yml`). After edits, re-run so shell helpers still load and any mocked Copilot paths still pass.

## 3. Trigger logic (`should_run_hang_followup_analysis`)

From `auto_triage/`:

```bash
source lib/hang_detect.sh
D=$(mktemp -d)
mkdir -p "$D/hang_triage"
should_run_hang_followup_analysis "$D"; echo $?   # expect 1
echo '[HANG DETECTED] test' > "$D/error_message.txt"
should_run_hang_followup_analysis "$D"; echo $?   # expect 0
```

Repeat with only `hang_triage/triage_output.txt` or `debug_bus_signal_groups.json` (no hang string in `error_message.txt`) — expect **0**.

## 4. Manifest assembly (no Copilot)

```bash
cd .github/actions/auto-triage/auto_triage
source lib/instructions_pipeline.sh
OUT=$(mktemp)
build_instruction_bundle "$OUT" "$PWD" "$AT_PIPELINE_FILTER_FRAGMENTS" && wc -l "$OUT"
build_instruction_bundle "$OUT" "$PWD" "$AT_PIPELINE_MAIN_FRAGMENTS" && wc -l "$OUT"
rm -f "$OUT"
```

Both should succeed; line counts should be non-zero. Intentionally break a path in a manifest and confirm `build_instruction_bundle` errors.

## 5. `followups.manifest` parsing

- Add a junk line (no trigger/path) and confirm `run_instruction_followups` logs a **warn** and continues (run with stubbed `run_llm_analysis` if you add a small test script).
- Use a trigger name that is **not** defined — expect warn “unknown trigger”.

## 6. End-to-end (recommended)

- **Artifact download:** Run `./get_triage_artifacts.sh` with a real failing **tt-metal** job URL that uploaded `triage_output_*` and `debug_bus_signals_*`; confirm files under `data/hang_triage/`.
- **Workflow:** Dispatch or run the composite action on a branch with a hang-like failure (or synthetic `error_message.txt` + copied triage files in the triage workspace). Confirm:
  - Logs show **main** Copilot pass, then **follow-up** log line (`Copilot follow-up: hang_stage_instructions_for_llm.txt` or similar).
  - `output/explanation.md` ends with `## Hardware diagnostics (tt-triage)` when the hang follow-up ran.
  - Artifacts still include `auto-triage-output` / `auto-triage-data` as before.

## 7. Regression: filter-only path

Run **filter** stage only on a normal (non-hang) failure and confirm filter still completes and `filter.fragments` resolves (including `filter_hang_instructions_for_llm.txt` in the bundle without requiring hang artifacts).
