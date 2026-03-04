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
- `instructions/instructions_for_llm.txt` — full triage analysis prompt
- `instructions/filter_instructions_for_llm.txt` — classification/filtering prompt
- `instructions/compare_errors_instructions.txt` — error comparison prompt (used by retry module)
