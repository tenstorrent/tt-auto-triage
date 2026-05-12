#!/usr/bin/env bash
#
# verify_common.sh — Helpers for the bug-escape verification system.
#
# Completely independent from the detection pipeline (Phases 1-4).
# Provides utilities for: test YAML discovery, SKU flag mapping,
# pruned YAML generation, run polling, and "no tests ran" detection.

if [ -n "${_VERIFY_COMMON_LOADED:-}" ]; then
  return 0
fi
_VERIFY_COMMON_LOADED=1

VERIFY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERIFY_OUTPUT_DIR="$VERIFY_ROOT/output"

# Minimal logging (no dependency on auto-triage libs)
verify_info()  { printf '[verify] %s\n' "$*"; }
verify_warn()  { printf '[verify][WARN] %s\n' "$*" >&2; }
verify_error() { printf '[verify][ERR] %s\n' "$*" >&2; }
verify_die()   { verify_error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# _extract_impl_yaml_path IMPL_FILE
#
# Checks if an impl workflow has a TESTS_YAML_PATH env var.
# Returns the path on stdout, or empty string if not found.
# ---------------------------------------------------------------------------
_extract_impl_yaml_path() {
  local impl_file="$1"
  local tests_yaml_path

  tests_yaml_path=$(grep -E '^\s*TESTS_YAML_PATH:' "$impl_file" 2>/dev/null \
    | head -1 \
    | sed 's/.*TESTS_YAML_PATH:\s*//' \
    | sed 's/^[[:space:]]*//' \
    | sed 's/[[:space:]]*$//')

  if [ -n "$tests_yaml_path" ]; then
    # Strip leading ./
    echo "${tests_yaml_path#./}"
  fi
}

# ---------------------------------------------------------------------------
# _impl_has_inline_test_match IMPL_FILE TEST_JOB
#
# Checks if an impl workflow YAML has an inline test matrix (heredoc or
# strategy.matrix) containing a test entry whose name matches TEST_JOB
# (substring match, stripping arch/runner-label prefixes).
# Returns 0 if match found, 1 otherwise.
# ---------------------------------------------------------------------------
_impl_has_inline_test_match() {
  local impl_file="$1" test_job="$2"

  python3 -c "
import sys, re

test_job = '''$test_job'''
impl_file = '''$impl_file'''

# Read the raw file content
with open(impl_file) as f:
    content = f.read()

# Look for '- name: ...' lines anywhere in the file (covers heredoc inline matrices)
name_pattern = re.compile(r'^\s*-\s*name:\s*(.+)', re.MULTILINE)
names = [m.group(1).strip().strip('\"').strip(\"'\") for m in name_pattern.finditer(content)]

if not names:
    sys.exit(1)

# Try matching: the CI job name is typically '{arch} {runner-label} {test-name}'
# Strip known prefixes to get the bare test name for matching
for name in names:
    # Exact match
    if name == test_job:
        sys.exit(0)
    # test_job ends with the name (e.g. 'blackhole P300-viommu sdpa nightly tests' ends with 'sdpa nightly tests')
    if test_job.endswith(name):
        sys.exit(0)
    # name is contained in test_job
    if name in test_job:
        sys.exit(0)

sys.exit(1)
" 2>/dev/null
}

# ---------------------------------------------------------------------------
# discover_tests_yaml_path_from_main_workflow WF_FILE REPO_DIR TEST_JOB
#
# Fallback discovery: scans the main workflow YAML for 'uses:' references
# to child impl workflows. For each child:
#   1. If child has TESTS_YAML_PATH env var → return that (existing pattern)
#   2. If child has inline test matrix with a name matching TEST_JOB →
#      return the child impl path itself (caller detects impl-mode by
#      checking if path matches .github/workflows/*-impl.yaml)
#
# Returns the path on stdout.
# All info/warn logging goes to stderr to avoid polluting $(…) capture.
# ---------------------------------------------------------------------------
discover_tests_yaml_path_from_main_workflow() {
  local wf_file="$1" repo_dir="$2" test_job="$3"
  local wf_path="$repo_dir/.github/workflows/$wf_file"

  if [ ! -f "$wf_path" ]; then
    verify_warn "Main workflow not found: $wf_path"
    return 1
  fi

  # Pass 1: scan *-impl.yaml children (original behavior)
  local child_impls
  child_impls=$(grep -oP 'uses:\s*\./\.github/workflows/\K[^\s]+' "$wf_path" 2>/dev/null \
    | grep -E '\-impl\.yaml$' || true)

  local child_file child_path tyaml
  while IFS= read -r child_file; do
    [ -n "$child_file" ] || continue
    child_path="$repo_dir/.github/workflows/$child_file"
    [ -f "$child_path" ] || continue

    # Fallback 1: child has TESTS_YAML_PATH
    tyaml=$(_extract_impl_yaml_path "$child_path")
    if [ -n "$tyaml" ]; then
      printf '[verify] discover (fallback1): found TESTS_YAML_PATH=%s in child %s\n' "$tyaml" "$child_file" >&2
      echo "$tyaml"
      return 0
    fi

    # Fallback 2: child has inline test matrix matching test_job
    if _impl_has_inline_test_match "$child_path" "$test_job"; then
      printf '[verify] discover (fallback2): inline test match in %s for job %s\n' "$child_file" "$test_job" >&2
      echo ".github/workflows/$child_file"
      return 0
    fi
  done <<< "$child_impls"

  # Pass 2: find the child workflow whose job KEY in the main YAML matches test_job.
  # Handles patterns like:
  #   jobs:
  #     profiler-regression:
  #       uses: ./.github/workflows/run-profiler-regression.yaml
  # where the job key is the CI display name and there is no *-impl.yaml.
  local matched_child
  matched_child=$(python3 -c "
import sys, re

test_job = '''$test_job'''
wf_path  = '''$wf_path'''

try:
    with open(wf_path) as f:
        content = f.read()
except Exception as e:
    sys.exit(1)

# Parse job-key -> uses mappings with a simple regex (no full YAML parser needed).
# Matches:
#   <key>:\n    ...uses: ./.github/workflows/<file>
# within the jobs: block.  We capture the first 'uses:' that follows each job key.
job_uses_pattern = re.compile(
    r'^(?P<indent>\s{0,4})(?P<key>[A-Za-z0-9_-]+):\s*\n'
    r'(?:(?P=indent)\s[^\n]*\n)*?'
    r'(?P=indent)\s+uses:\s+\./\.github/workflows/(?P<child>\S+)',
    re.MULTILINE
)

for m in job_uses_pattern.finditer(content):
    key   = m.group('key')
    child = m.group('child')
    # Match: job key == test_job, or either is a substring of the other
    if key == test_job or key in test_job or test_job in key:
        print(child)
        sys.exit(0)

sys.exit(1)
" 2>/dev/null || true)

  if [ -n "$matched_child" ]; then
    local matched_path="$repo_dir/.github/workflows/$matched_child"
    if [ -f "$matched_path" ]; then
      # Try TESTS_YAML_PATH in the matched child
      tyaml=$(_extract_impl_yaml_path "$matched_path")
      if [ -n "$tyaml" ]; then
        printf '[verify] discover (fallback3a): TESTS_YAML_PATH=%s in job-key child %s\n' "$tyaml" "$matched_child" >&2
        echo "$tyaml"
        return 0
      fi
      # Return the child itself as an impl-mode path
      printf '[verify] discover (fallback3b): job-key match -> child %s (impl-mode)\n' "$matched_child" >&2
      echo ".github/workflows/$matched_child"
      return 0
    fi
  fi

  verify_warn "No child workflow matched test job '$test_job' in $wf_path"
  return 1
}

# ---------------------------------------------------------------------------
# discover_tests_yaml_path WORKFLOW_FILE REPO_DIR [TEST_JOB]
#
# Finds the TESTS_YAML_PATH for a given workflow by inspecting its -impl.yaml.
# E.g. galaxy-e2e-tests.yaml -> galaxy-e2e-tests-impl.yaml -> ./tests/pipeline_reorg/galaxy_e2e_tests.yaml
# Returns the relative path (from repo root) on stdout.
#
# When the direct -impl.yaml is not found, falls back to scanning child
# workflows referenced by the main workflow YAML (requires TEST_JOB arg).
# ---------------------------------------------------------------------------
discover_tests_yaml_path() {
  local wf_file="$1" repo_dir="$2" test_job="${3:-}"
  local wf_basename impl_path tests_yaml_path

  wf_basename=$(basename "$wf_file" .yaml)
  impl_path="$repo_dir/.github/workflows/${wf_basename}-impl.yaml"

  # Primary path: direct -impl.yaml with TESTS_YAML_PATH
  if [ -f "$impl_path" ]; then
    tests_yaml_path=$(_extract_impl_yaml_path "$impl_path")
    if [ -n "$tests_yaml_path" ]; then
      echo "$tests_yaml_path"
      return 0
    fi
    verify_warn "TESTS_YAML_PATH not found in $impl_path, trying fallback"
  fi

  # Fallback: scan child workflows called by the main workflow
  if [ -n "$test_job" ]; then
    printf '[verify] discover: trying child-workflow fallback for %s\n' "$wf_file" >&2
    discover_tests_yaml_path_from_main_workflow "$wf_file" "$repo_dir" "$test_job"
    return $?
  fi

  verify_warn "Impl workflow not found and no TEST_JOB provided for fallback: $impl_path"
  return 1
}

# ---------------------------------------------------------------------------
# find_test_entry TEST_JOB TESTS_YAML_PATH REPO_DIR
#
# Finds the YAML entry in the test matrix whose 'name' matches TEST_JOB.
# Outputs the full entry as JSON on stdout.
# Requires: python3 + PyYAML (available in CI, and locally via pip).
#
# When TESTS_YAML_IS_IMPL=true, the file is a workflow impl YAML with an
# inline test matrix (heredoc in a generate-matrix step). In that case,
# we extract '- name: ...' blocks from the raw file content instead of
# loading a top-level YAML list.
# ---------------------------------------------------------------------------
find_test_entry() {
  local test_job="$1" tests_yaml_path="$2" repo_dir="$3"
  local full_path="$repo_dir/$tests_yaml_path"

  if [ ! -f "$full_path" ]; then
    verify_warn "Tests YAML not found: $full_path"
    return 1
  fi

  python3 -c "
import yaml, json, sys, re

full_path = '''$full_path'''
target = '''$test_job'''
is_impl = '''${TESTS_YAML_IS_IMPL:-false}'''

def match_entries(tests, target):
    \"\"\"Try to match target against a list of test entry dicts.\"\"\"
    # Pass 1: exact match
    for entry in tests:
        if entry.get('name', '') == target:
            return entry
    # Pass 2: strip GitHub Actions workflow prefix ('workflow / job')
    stripped = target.split(' / ')[-1] if ' / ' in target else target
    for entry in tests:
        if entry.get('name', '') == stripped:
            return entry
    # Pass 3: substring match (prefer longer name matches)
    matches = []
    for entry in tests:
        name = entry.get('name', '')
        if target in name or name in target:
            matches.append((len(name), entry))
    if matches:
        matches.sort(key=lambda x: -x[0])
        return matches[0][1]
    return None

with open(full_path) as f:
    content = f.read()

tests = None

if is_impl == 'true':
    # Impl YAML: extract inline test list from heredoc or embedded YAML.
    # Look for YAML list blocks starting with '- name:' in the file.
    # These are typically inside a bash heredoc like:
    #   all_tests_yaml=\$(cat <<'YAML_EOF'
    #   - name: test1
    #     cmd: ...
    #   - name: test2
    #     cmd: ...
    #   YAML_EOF
    #   )

    # Strategy: find heredoc content between <<'..._EOF' and the EOF marker,
    # then parse it as YAML.
    heredoc_pattern = re.compile(
        r\"\"\"<<\s*['\"]?(\w+_EOF)['\"]?\s*\n(.*?)\n\s*\1\b\"\"\",
        re.DOTALL
    )
    for m in heredoc_pattern.finditer(content):
        block = m.group(2)
        try:
            parsed = yaml.safe_load(block)
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) and 'name' in parsed[0]:
                tests = parsed
                break
        except Exception:
            continue

    # Fallback: try to find any YAML-list-looking block with '- name:' entries
    if tests is None:
        # Collect all lines that look like they belong to a YAML list of test entries
        lines = content.split('\n')
        yaml_blocks = []
        in_block = False
        block_lines = []
        indent = 0
        for line in lines:
            stripped_line = line.lstrip()
            if stripped_line.startswith('- name:'):
                if in_block and block_lines:
                    yaml_blocks.append('\n'.join(block_lines))
                in_block = True
                indent = len(line) - len(stripped_line)
                block_lines = [stripped_line]
            elif in_block:
                cur_indent = len(line) - len(line.lstrip())
                if line.strip() == '' or cur_indent > indent:
                    block_lines.append(stripped_line)
                elif stripped_line.startswith('- name:'):
                    yaml_blocks.append('\n'.join(block_lines))
                    block_lines = [stripped_line]
                else:
                    yaml_blocks.append('\n'.join(block_lines))
                    in_block = False
                    block_lines = []
        if block_lines:
            yaml_blocks.append('\n'.join(block_lines))

        if yaml_blocks:
            full_yaml = '\n'.join(yaml_blocks)
            try:
                tests = yaml.safe_load(full_yaml)
            except Exception:
                pass
else:
    # Standard test matrix YAML (top-level list)
    try:
        tests = yaml.safe_load(content)
    except Exception:
        pass

    # If top-level is a dict (unexpected), try to walk into common matrix paths
    if isinstance(tests, dict):
        for job_key, job_val in tests.get('jobs', {}).items():
            matrix = job_val.get('strategy', {}).get('matrix', {})
            for mk, mv in matrix.items():
                if isinstance(mv, list) and len(mv) > 0 and isinstance(mv[0], dict) and 'name' in mv[0]:
                    tests = mv
                    break
            if isinstance(tests, list):
                break

if not isinstance(tests, list) or len(tests) == 0:
    print('null')
    sys.exit(1)

result = match_entries(tests, target)
if result:
    print(json.dumps(result))
    sys.exit(0)

print('null')
sys.exit(1)
" 2>/dev/null
}

# ---------------------------------------------------------------------------
# derive_sku_flags TEST_ENTRY_JSON WORKFLOW_FILE REPO_DIR
#
# Given a test entry (JSON) and workflow, determine the workflow_dispatch flags
# needed to run only the matching SKU. Outputs space-separated -f flags.
# E.g.: "-f blackhole=true -f wormhole=false"
# ---------------------------------------------------------------------------
derive_sku_flags() {
  local entry_json="$1" wf_file="$2" repo_dir="$3"
  local sku_keys flags=""

  sku_keys=$(echo "$entry_json" | python3 -c "
import json, sys
entry = json.load(sys.stdin)
skus = entry.get('skus', {})
print(' '.join(skus.keys()))
" 2>/dev/null || echo "")

  local wf_path="$repo_dir/.github/workflows/$(basename "$wf_file")"

  # Check what dispatch inputs the workflow supports
  local has_blackhole=false has_wormhole=false
  if [ -f "$wf_path" ]; then
    grep -q 'blackhole:' "$wf_path" 2>/dev/null && has_blackhole=true
    grep -q 'wormhole:' "$wf_path" 2>/dev/null && has_wormhole=true
  fi

  if $has_blackhole || $has_wormhole; then
    local need_bh=false need_wh=false
    for sku in $sku_keys; do
      case "$sku" in
        bh_*) need_bh=true ;;
        wh_*) need_wh=true ;;
      esac
    done

    if $has_blackhole; then
      if $need_bh; then flags="$flags -f blackhole=true"; else flags="$flags -f blackhole=false"; fi
    fi
    if $has_wormhole; then
      if $need_wh; then flags="$flags -f wormhole=true"; else flags="$flags -f wormhole=false"; fi
    fi
  fi

  echo "$flags"
}

# ---------------------------------------------------------------------------
# _build_pruned_yaml_agent TEST_JOB TEST_NAME ENTRY_JSON
#
# Agent-based pruned YAML generation. Calls the LLM API directly to produce
# a correct single-entry YAML that preserves ALL fields from the original
# entry and adjusts the cmd to run only the specific test.
# Returns YAML on stdout. Returns 1 if agent unavailable or call failed.
# ---------------------------------------------------------------------------
_build_pruned_yaml_agent() {
  local test_job="$1" test_name="$2" entry_json="$3"
  local llm_backend="${LLM_BACKEND:-cursor}"
  local active_key=""

  if [ "$llm_backend" = "copilot" ]; then
    active_key="${COPILOT_GITHUB_TOKEN:-}"
  else
    active_key="${CURSOR_API_KEY:-}"
  fi

  if [ -z "$active_key" ]; then
    return 1
  fi

  # Build prompt — use a temp file to avoid ARG_MAX issues with large JSON
  local prompt_file
  prompt_file=$(mktemp)
  cat > "$prompt_file" <<'PROMPT_DELIM'
You are generating a pruned test matrix YAML for CI verification of a bug escape.

Original test entry (JSON):
PROMPT_DELIM
  echo "$entry_json" >> "$prompt_file"
  cat >> "$prompt_file" <<PROMPT_DELIM

Test job display name: ${test_job}
Specific test to run: ${test_name}

Generate a single-entry YAML list that:
1. Preserves ALL fields from the original entry exactly (name, cmd, skus, owner_id, team, model-name, and any other fields present).
2. Modifies ONLY the 'cmd' field:
   - If the test name contains '::' or ends in '.py' (a pytest path), replace cmd with:
       pytest "${test_name}"
     (quote the test name with double-quotes since it may contain brackets)
   - If the test name does NOT look like a pytest path, keep the original cmd unchanged.

Output ONLY valid YAML — no markdown fences, no explanation.
Start with exactly this comment line, then the YAML list:
# Pruned to single test for bug escape verification
PROMPT_DELIM
  # Substitute shell variables in prompt
  sed -i "s|\${test_job}|$test_job|g; s|\${test_name}|$test_name|g" "$prompt_file"

  local prompt
  prompt=$(<"$prompt_file")
  rm -f "$prompt_file"

  # Build JSON payload
  local payload tmpfile http_code
  tmpfile=$(mktemp)
  payload=$(python3 -c "
import json, sys
prompt = sys.stdin.read()
print(json.dumps({
    'model': 'claude-3-5-sonnet',
    'messages': [{'role': 'user', 'content': prompt}],
    'max_tokens': 512
}))
" <<< "$prompt")

  if [ "$llm_backend" = "copilot" ]; then
    http_code=$(curl -s -o "$tmpfile" -w "%{http_code}" \
      -X POST "https://models.inference.ai.azure.com/chat/completions" \
      -H "Authorization: Bearer $active_key" \
      -H "Content-Type: application/json" \
      -d "$payload" 2>/dev/null) || true
  else
    http_code=$(curl -s -o "$tmpfile" -w "%{http_code}" \
      -X POST "https://api.cursor.sh/v1/chat/completions" \
      -H "Authorization: Bearer $active_key" \
      -H "Content-Type: application/json" \
      -d "$payload" 2>/dev/null) || true
  fi

  if [ "$http_code" != "200" ]; then
    verify_warn "_build_pruned_yaml_agent: ${llm_backend} API returned HTTP $http_code"
    rm -f "$tmpfile"
    return 1
  fi

  # Extract and validate YAML from LLM response
  local yaml_content
  yaml_content=$(python3 -c "
import json, sys, re, yaml
try:
    resp = json.load(sys.stdin)
    content = resp['choices'][0]['message']['content'].strip()
    # Strip markdown fences if present
    content = re.sub(r'^\x60\x60\x60(?:yaml)?\s*', '', content)
    content = re.sub(r'\s*\x60\x60\x60\s*$', '', content)
    content = content.strip()
    # Validate: must contain a YAML list with cmd field
    yaml_part = '\n'.join(l for l in content.splitlines() if not l.startswith('#'))
    parsed = yaml.safe_load(yaml_part)
    if not isinstance(parsed, list) or len(parsed) == 0 or 'cmd' not in parsed[0]:
        raise ValueError('invalid YAML structure')
    print(content)
except Exception as e:
    print(f'# agent-error: {e}', file=sys.stderr)
    sys.exit(1)
" < "$tmpfile" 2>/dev/null) || { rm -f "$tmpfile"; return 1; }

  rm -f "$tmpfile"

  if [ -n "$yaml_content" ]; then
    echo "$yaml_content"
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------------------
# build_pruned_yaml TEST_JOB TEST_NAME TEST_ENTRY_JSON
#
# Generates a single-entry YAML list for the pruned test matrix.
# Tries LLM agent first (preserves ALL original fields); falls back to
# a hardcoded Python extractor that handles common field shapes.
# Outputs YAML content on stdout.
# ---------------------------------------------------------------------------
build_pruned_yaml() {
  local test_job="$1" test_name="$2" entry_json="$3"

  # Try agent-based generation first — more robust, handles any workflow shape
  local agent_yaml
  if agent_yaml=$(_build_pruned_yaml_agent "$test_job" "$test_name" "$entry_json" 2>/dev/null); then
    verify_info "build_pruned_yaml: agent-generated pruned YAML"
    echo "$agent_yaml"
    return 0
  fi

  verify_info "build_pruned_yaml: agent unavailable/failed, using Python fallback"

  python3 -c "
import json, sys, yaml, re

entry = json.loads('''$entry_json''')
test_name = '''$test_name'''

# Detect if test_name looks like a valid pytest path
is_pytest_path = ('::' in test_name or
                  test_name.rstrip(']').endswith('.py'))

if is_pytest_path:
    # Build the pytest command — quote if parametrized (contains brackets)
    if '[' in test_name:
        cmd = 'pytest \"' + test_name + '\"'
    else:
        cmd = 'pytest ' + test_name
else:
    # Not a pytest path (e.g. ResNet50 perf pipeline name) — use original cmd if available
    original_cmd = entry.get('cmd', None)
    if original_cmd:
        cmd = original_cmd
    else:
        # Fallback: generate pytest command anyway for backward compat
        if '[' in test_name:
            cmd = 'pytest \"' + test_name + '\"'
        else:
            cmd = 'pytest ' + test_name

pruned = {
    'name': entry.get('name', '$test_job'),
    'cmd': cmd,
    'skus': entry.get('skus', {}),
    'owner_id': entry.get('owner_id', 'UNKNOWN'),
    'team': entry.get('team', 'UNKNOWN'),
}

# Preserve extra fields beyond the standard set
for k, v in entry.items():
    if k not in pruned:
        pruned[k] = v

# Add comment
print('# Pruned to single test for bug escape verification')
print(yaml.dump([pruned], default_flow_style=False).rstrip())
" 2>/dev/null
}
# ---------------------------------------------------------------------------
# poll_run_start RUN_ID START_WAIT_MINUTES
#
# Waits for a GitHub Actions run to leave "queued" status (i.e., transition
# to "in_progress" or "completed"). Polls every 60 seconds.
# Returns 0 if run started within the deadline, 1 if it timed out.
# ---------------------------------------------------------------------------
# build_pruned_impl_workflow_content CONTENT_FILE ENTRY_JSON_FILE
#
# Impl-mode variant: given paths to (a) the full workflow impl YAML and
# (b) a JSON file with the target test entry, replace ONLY the heredoc
# test-list section with a single-entry list. The rest of the workflow is
# preserved as a valid GHA workflow.  Prints the modified content to stdout.
# ---------------------------------------------------------------------------
build_pruned_impl_workflow_content() {
  local content_file="$1" entry_json_file="$2"

  python3 - "$content_file" "$entry_json_file" << 'PYEOF'
import sys, re, json, yaml

content_file   = sys.argv[1]
entry_json_file = sys.argv[2]

with open(content_file) as f:
    content = f.read()

with open(entry_json_file) as f:
    entry = json.load(f)

# Match: all_tests_yaml=$(cat <<'YAML_EOF'  ...content...  YAML_EOF)
heredoc_re = re.compile(
    r'(?P<prefix>all_tests_yaml=\$\(cat <<\s*[\'"]?(?P<marker>[A-Z_]+)[\'"]?\s*\n)'
    r'(?P<body>.*?)'
    r'(?P<suffix>\n[ \t]*(?P=marker)(?:[ \t]*\))?)',
    re.DOTALL
)

m = heredoc_re.search(content)
if not m:
    sys.stderr.write("build_pruned_impl_workflow_content: heredoc not found, returning original\n")
    sys.stdout.write(content)
    sys.exit(0)

# Detect leading indent from first non-blank line of original body
indent = ''
for line in m.group('body').splitlines():
    if line.strip():
        indent = re.match(r'^(\s*)', line).group(1)
        break

entry_yaml = yaml.dump([entry], default_flow_style=False).rstrip()
indented = '\n'.join(indent + line for line in entry_yaml.splitlines())
replacement_body = indent + '# Pruned to single test for bug escape verification\n' + indented

new_content = (content[:m.start()]
               + m.group('prefix')
               + replacement_body
               + m.group('suffix')
               + content[m.end():])
sys.stdout.write(new_content)
PYEOF
}

# ---------------------------------------------------------------------------
poll_run_start() {
  local run_id="$1" start_wait="${2:-240}"
  local deadline=$((SECONDS + start_wait * 60))
  local status

  printf '[verify] Waiting for run %s to start (max_wait=%sm)\n' "$run_id" "$start_wait" >&2

  while [ "$SECONDS" -lt "$deadline" ]; do
    status=$(gh run view "$run_id" --json status --jq '.status' 2>/dev/null || echo "unknown")
    if [ "$status" != "queued" ]; then
      printf '[verify] Run %s left queued state: status=%s\n' "$run_id" "$status" >&2
      return 0
    fi
    printf '[verify]   Run %s: still queued (%dm remaining)\n' "$run_id" "$(( (deadline - SECONDS) / 60 ))" >&2
    sleep 60
  done

  printf '[verify][WARN] Run %s still queued after %sm\n' "$run_id" "$start_wait" >&2
  return 1
}

# ---------------------------------------------------------------------------
# poll_run_completion RUN_ID INTERVAL_SECONDS MAX_WAIT_MINUTES
#
# Polls a GitHub Actions run until it completes or times out.
# Returns the conclusion on stdout.
# ---------------------------------------------------------------------------
poll_run_completion() {
  local run_id="$1" interval="${2:-120}" max_wait="${3:-120}"
  local deadline=$((SECONDS + max_wait * 60))
  local status conclusion

  # Progress messages go to stderr so they don't get captured by $(...)
  printf '[verify] Polling run %s (interval=%ss, max_wait=%sm)\n' "$run_id" "$interval" "$max_wait" >&2

  while [ "$SECONDS" -lt "$deadline" ]; do
    status=$(gh run view "$run_id" --json status --jq '.status' 2>/dev/null || echo "unknown")
    if [ "$status" = "completed" ]; then
      conclusion=$(gh run view "$run_id" --json conclusion --jq '.conclusion' 2>/dev/null || echo "unknown")
      printf '[verify] Run %s completed: %s\n' "$run_id" "$conclusion" >&2
      echo "$conclusion"
      return 0
    fi
    printf '[verify]   Run %s: status=%s (%dm remaining)\n' "$run_id" "$status" "$(( (deadline - SECONDS) / 60 ))" >&2
    sleep "$interval"
  done

  printf '[verify][WARN] Run %s timed out after %sm\n' "$run_id" "$max_wait" >&2
  echo "timed_out"
  return 1
}

# ---------------------------------------------------------------------------
# check_no_tests_ran RUN_ID TEST_JOB
#
# After a run completes, check if the specific job hit "no tests ran".
# Returns 0 (true) if no tests ran, 1 otherwise.
# ---------------------------------------------------------------------------
check_no_tests_ran() {
  local run_id="$1" test_job="$2"

  if gh run view "$run_id" --log 2>/dev/null \
       | grep -F "$test_job" \
       | grep -qE "no tests ran|collected 0 items"; then
    return 0
  fi
  return 1
}

# ---------------------------------------------------------------------------
# check_failure_is_real RUN_ID TEST_JOB EXPECTED_FAILURE_SIG CURSOR_API_KEY
#
# Classifies a job failure as real vs infra/unrelated using an LLM.
# Backend is chosen by LLM_BACKEND env var (default: cursor):
#   cursor  — Cursor AI REST API (api.cursor.sh), requires CURSOR_API_KEY ($4)
#   copilot — GitHub Models API (models.inference.ai.azure.com), requires
#             COPILOT_GITHUB_TOKEN env var
#
# Returns one of: real_failure, infra_failure, unrelated_failure, inconclusive
# on stdout.
#
# Conservative defaults:
#   - If no API key/token available → returns "real_failure" (skip check)
#   - If log fetch fails → returns "real_failure" (don't drop real failures)
#   - If API call fails or response unparseable → returns "inconclusive"
# ---------------------------------------------------------------------------
check_failure_is_real() {
  local run_id="$1" test_job="$2" expected_sig="${3:-}" cursor_api_key="${4:-}"
  local llm_backend="${LLM_BACKEND:-cursor}"

  # Resolve the active API key/token based on backend
  local active_key=""
  if [ "$llm_backend" = "copilot" ]; then
    active_key="${COPILOT_GITHUB_TOKEN:-}"
    if [ -z "$active_key" ]; then
      verify_info "check_failure_is_real: no COPILOT_GITHUB_TOKEN, assuming real_failure"
      echo "real_failure"
      return 0
    fi
  else
    active_key="$cursor_api_key"
    if [ -z "$active_key" ]; then
      verify_info "check_failure_is_real: no CURSOR_API_KEY, assuming real_failure"
      echo "real_failure"
      return 0
    fi
  fi

  # Fetch job logs via GitHub REST API (no gh CLI dependency)
  local log_tail="" job_id=""
  job_id=$(curl -s -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
    "https://api.github.com/repos/${OWNER_REPO}/actions/runs/${run_id}/jobs" 2>/dev/null \
    | python3 -c "
import sys,json
d=json.load(sys.stdin)
test_job_name='${test_job}'
for j in d.get('jobs',[]):
    if test_job_name in j.get('name',''):
        print(j['id'])
        break
" 2>/dev/null) || true

  if [ -n "$job_id" ]; then
    log_tail=$(curl -s -L -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
      "https://api.github.com/repos/${OWNER_REPO}/actions/jobs/${job_id}/logs" 2>/dev/null \
      | tail -200) || true
  fi

  if [ -z "$log_tail" ]; then
    verify_warn "check_failure_is_real: could not fetch logs for run $run_id job '$test_job', assuming real_failure"
    echo "real_failure"
    return 0
  fi

  # Escape the log content for JSON embedding
  local escaped_logs
  escaped_logs=$(printf '%s' "$log_tail" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

  local prompt
  prompt=$(cat <<PROMPT_EOF
You are analyzing a CI job log to determine if a test failure is a real test failure or an infrastructure/unrelated failure.

**Job name:** ${test_job}
**Expected failure signature:** ${expected_sig}

**Last 200 lines of job log:**
${log_tail}

Classify this failure into exactly one category:
- "real_failure": The test actually ran and failed due to a test assertion, timeout in test code, or a product bug. The failure is related to the expected failure signature.
- "infra_failure": The failure is caused by CI infrastructure problems — examples: HTTP 503 proxy errors, git clone/submodule failures, Docker pull failures, runner setup errors, pip install failures, network timeouts, disk space issues.
- "unrelated_failure": The test job failed but for a reason completely unrelated to the expected failure signature — a different test crashed, an import error in unrelated code, a segfault in a different component.

Respond with ONLY a JSON object (no markdown, no explanation outside the JSON):
{"classification": "real_failure" | "infra_failure" | "unrelated_failure", "reason": "brief one-line explanation"}
PROMPT_EOF
)

  # Build the JSON payload for the Cursor API (OpenAI-compatible endpoint)
  local payload
  payload=$(python3 -c "
import json, sys
prompt = sys.stdin.read()
print(json.dumps({
    'model': 'claude-3-5-sonnet',
    'messages': [{'role': 'user', 'content': prompt}],
    'max_tokens': 256
}))
" <<< "$prompt")

  local response http_code
  local tmpfile
  tmpfile=$(mktemp)

  if [ "$llm_backend" = "copilot" ]; then
    http_code=$(curl -s -o "$tmpfile" -w "%{http_code}" \
      -X POST "https://models.inference.ai.azure.com/chat/completions" \
      -H "Authorization: Bearer $active_key" \
      -H "Content-Type: application/json" \
      -d "$payload" 2>/dev/null) || true
  else
    http_code=$(curl -s -o "$tmpfile" -w "%{http_code}" \
      -X POST "https://api.cursor.sh/v1/chat/completions" \
      -H "Authorization: Bearer $active_key" \
      -H "Content-Type: application/json" \
      -d "$payload" 2>/dev/null) || true
  fi

  if [ "$http_code" != "200" ]; then
    verify_warn "check_failure_is_real: ${llm_backend} API returned HTTP $http_code, returning inconclusive"
    rm -f "$tmpfile"
    echo "inconclusive"
    return 0
  fi

  # Extract the classification from the response
  local classification
  classification=$(python3 -c "
import json, sys, re
try:
    resp = json.load(sys.stdin)
    content = resp['choices'][0]['message']['content']
    # Try to parse as JSON directly
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Strip markdown fences if present
        cleaned = re.sub(r'^\`\`\`(?:json)?\s*', '', content.strip())
        cleaned = re.sub(r'\s*\`\`\`$', '', cleaned)
        result = json.loads(cleaned)
    c = result.get('classification', '')
    reason = result.get('reason', 'no reason given')
    if c in ('real_failure', 'infra_failure', 'unrelated_failure'):
        print(f'{c}|{reason}', end='')
    else:
        print('inconclusive|unexpected classification: ' + c, end='')
except Exception as e:
    print('inconclusive|parse error: ' + str(e), end='')
" < "$tmpfile") || true

  rm -f "$tmpfile"

  if [ -z "$classification" ]; then
    verify_warn "check_failure_is_real: empty response from parser, returning inconclusive"
    echo "inconclusive"
    return 0
  fi

  local class_value class_reason
  class_value="${classification%%|*}"
  class_reason="${classification#*|}"

  verify_info "check_failure_is_real: run=$run_id classification=$class_value reason=$class_reason"
  echo "$class_value"
  return 0
}

# ---------------------------------------------------------------------------
# wait_for_run_to_appear BRANCH WORKFLOW_FILE MAX_ATTEMPTS
#
# After dispatching, the run takes a few seconds to appear. Poll until we
# find a queued/in_progress run on the branch, or give up.
# Returns the run ID on stdout.
# ---------------------------------------------------------------------------
wait_for_run_to_appear() {
  local branch="$1" wf_file="$2" max_attempts="${3:-15}"
  local wf_basename run_id attempt

  wf_basename=$(basename "$wf_file")

  for attempt in $(seq 1 "$max_attempts"); do
    run_id=$(gh run list --workflow="$wf_basename" --branch="$branch" \
      --limit=1 --json databaseId,status \
      --jq '.[] | select(.status == "queued" or .status == "in_progress" or .status == "completed") | .databaseId' \
      2>/dev/null || echo "")

    if [ -n "$run_id" ]; then
      echo "$run_id"
      return 0
    fi
    printf '[verify]   Waiting for run to appear on %s (attempt %d/%d)\n' "$branch" "$attempt" "$max_attempts" >&2
    sleep 5
  done

  printf '[verify][WARN] Could not find run for %s on %s after %d attempts\n' "$wf_basename" "$branch" "$max_attempts" >&2
  return 1
}


