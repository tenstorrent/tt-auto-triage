# analysis/ Module

Runs LLM-based root-cause analysis using GitHub Copilot CLI.

## Components

### llm_runner.sh
Invokes the Copilot CLI with instruction files and triage context to produce a structured root-cause analysis.

**API**: `run_llm_analysis(instructions_file, workflow, subjob, ci_mode)` → writes analysis output to `output/`

## Dependencies
- `lib/common.sh`, `lib/config.sh`
- Requires `copilot` CLI to be available in `$PATH`

## Instruction Files
- `instructions/instructions_for_llm.txt` — core main-analysis playbook (Cases, toolbox, final reminders)
- `instructions/instructions_footer_for_llm.txt` — closing “Extra notes”; concatenated after the core playbook for the **main** Copilot pass only
- `instructions/hang_stage_instructions_for_llm.txt` — second Copilot pass (after main): interpret tt-triage and append to `explanation.md`; runs when `should_run_hang_followup_analysis` (`lib/hang_detect.sh`)
- `instructions/filter_instructions_for_llm.txt` — filter-stage playbook (base)
- `instructions/filter_hang_instructions_for_llm.txt` — hang artifact download steps; always concatenated after the filter base in `filter_triage.sh`
- `instructions/compare_errors_instructions.txt` — error comparison prompt (used by retry module)
