# auto_fix/ Module

Validation helpers for the auto-fix feature (currently disabled).

## Components

### pr_validator.sh
Validates prerequisites for triggering an auto-fix PR via Copilot delegate.

**API**:
- `is_auto_fix_enabled(flag_file)` → exit 0 if `create_PR_boolean.json` has `create_PR: true`
- `validate_explanation_file(path)` → exit 0 if file exists and is non-empty
- `validate_workspace(dir)` → exit 0 if directory contains `.git`

## Status
Auto-fix is disabled due to authentication issues. The validation module is in place for when the feature is re-enabled.

## Dependencies
- `lib/common.sh`
