# Instruction pipelines

## Filter and main passes (`*.fragments`)

Each line is a path **relative to the `auto_triage/` root** (the directory that contains `instructions/` and `lib/`).

- **`filter.fragments`** — files concatenated in order for `filter_triage.sh` (single Copilot call).
- **`main.fragments`** — files concatenated in order for the main `auto_triage.sh` Copilot call.

Lines starting with `#` and blank lines are ignored. To add another analysis hook at filter time (similar to hangs), add a new instruction file under `instructions/` and append its path here.

## Conditional follow-ups (`followups.manifest`)

Runs **after** the main pass, each as its **own** Copilot invocation when the trigger succeeds.

Format: **trigger**, then **any whitespace** (spaces, tabs, etc.), then **instruction path** (the rest of the line, trimmed; can include spaces if your paths ever need them).

```text
<trigger_function>  <instruction_path>
```

- **`trigger_function`** — a bash function name. It is called as `trigger_function "$data_dir"` and must return **0** to run the follow-up. Define triggers in small `lib/*_followup.sh` modules and **source them from `lib/followup_triggers.sh`** so `auto_triage.sh` does not need edits.
- **`instruction_path`** — relative to the `auto_triage/` root; file must exist when the trigger fires.

Add a new row for a new follow-up type (e.g. performance regression). Implement `should_run_myfeature_followup_analysis` and source its file from `followup_triggers.sh`.
