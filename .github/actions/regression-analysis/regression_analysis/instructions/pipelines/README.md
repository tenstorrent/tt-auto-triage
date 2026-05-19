# Instruction pipelines

## `*.fragments` (filter + main)

One path per line, **relative to `regression_analysis/`** (the tree with `instructions/` and `lib/`). Blank lines and `#` comments are ignored.

| File | Used by |
|------|---------|
| `filter.fragments` | `filter_triage.sh` |
| `main.fragments` | `regression_analysis.sh` |

To extend: add an instruction file under `instructions/` and append its path to the right manifest.

## `followups.manifest` (after main pass)

Each matching trigger runs a **separate** Copilot call.

**Format:** `trigger_name` + whitespace + instruction path (rest of line, trimmed).

- **Trigger** — bash function `trigger_name "$data_dir"`; exit **0** to run the follow-up. Define in `lib/*.sh` and **source from `regression_analysis.sh`** (see `hang_detect.sh`).
- **Path** — relative to `regression_analysis/`; file must exist when the trigger fires.

Manifest paths are set in `lib/instructions_pipeline.sh` (`AT_PIPELINE_*`).

**Verification:** see [TESTING.md](TESTING.md).
