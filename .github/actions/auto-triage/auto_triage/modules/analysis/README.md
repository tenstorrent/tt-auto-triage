# analysis/ Module

Runs LLM-based root-cause analysis using GitHub Copilot CLI.

## Components

### llm_runner.sh
Invokes the Copilot CLI with instruction files and triage context to produce a structured root-cause analysis.

**API**: `run_llm_analysis(instructions_file, workflow, subjob, ci_mode)` → writes analysis output to `output/`

## Dependencies
- `lib/common.sh`, `lib/config.sh`
- Requires `copilot` CLI to be available in `$PATH`

## Instruction pipelines

Order and conditional passes are driven by manifests under `instructions/pipelines/` (see `instructions/pipelines/README.md`):

- **`filter.fragments`** — paths concatenated for the filter-stage Copilot call (`build_instruction_bundle` in `lib/instructions_pipeline.sh`).
- **`main.fragments`** — paths concatenated for the main Copilot call.
- **`followups.manifest`** — `trigger_function` then whitespace then instruction path (rest of line); each matching trigger runs an extra `run_llm_analysis`. Triggers are defined in `lib/*` and sourced from `lib/followup_triggers.sh`.

## Instruction files (fragments)

- `instructions/instructions_for_llm.txt` — core main-analysis playbook
- `instructions/instructions_footer_for_llm.txt` — closing “Extra notes” (listed in `main.fragments`)
- `instructions/hang_stage_instructions_for_llm.txt` — hang follow-up pass (listed in `followups.manifest`)
- `instructions/filter_instructions_for_llm.txt` — filter-stage base (listed in `filter.fragments`)
- `instructions/filter_hang_instructions_for_llm.txt` — hang steps in filter (listed in `filter.fragments`)
- `instructions/compare_errors_instructions.txt` — error comparison prompt (retry module)
