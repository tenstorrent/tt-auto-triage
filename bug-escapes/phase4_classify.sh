#!/usr/bin/env bash
set -euo pipefail

# Phase 4: Classify & Output Bug Escapes
#
# For each fix-point entry:
#   1. Compare the fix commit layer vs the test layer
#   2. Same layer → horizontal bug escape
#   3. Fix in lower layer → vertical bug escape
#   4. Write bug-escapes-output.json with full URLs and details
#   5. Print a rich summary to $GITHUB_STEP_SUMMARY (if available)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"

OUTPUT_DIR="$SCRIPT_DIR/output"
FIX_POINTS_INPUT="$OUTPUT_DIR/fix-points.json"
BUG_ESCAPES_OUTPUT="$OUTPUT_DIR/bug-escapes-output.json"

LOOKBACK_DAYS="${LOOKBACK_DAYS:-14}"
MAX_ESCAPES="${MAX_ESCAPES:-999}"

# --- Seen-escapes deduplication cache ---
# Prevents re-reporting the same escape across multiple runs.
# Keyed by "fix_sha|test_name|workflow_basename". TTL = LOOKBACK_DAYS.
SEEN_ESCAPES_FILE="$SCRIPT_DIR/state/seen_escapes.json"
SEEN_ESCAPES_TTL_DAYS="${SEEN_ESCAPES_TTL_DAYS:-${LOOKBACK_DAYS}}"
mkdir -p "$SCRIPT_DIR/state"
echo '{}' > "$SEEN_ESCAPES_FILE"

_restore_seen_escapes_cache() {
  local token="${GITHUB_TOKEN:-${GITHUB_READ_TOKEN:-}}"
  [ -z "$token" ] && return
  local artifact_id
  artifact_id=$(curl -s -H "Authorization: Bearer $token" \
    "https://api.github.com/repos/${AT_OWNER_REPO}/actions/artifacts?name=bug-escapes-seen-escapes-cache&per_page=1" \
    | jq -r '.artifacts[0].id // empty' 2>/dev/null || echo "")
  [ -z "$artifact_id" ] && { log_info "Seen-escapes cache: no prior artifact (first run)"; return; }
  local tmpzip
  tmpzip=$(mktemp --suffix=.zip)
  if curl -s -H "Authorization: Bearer $token" -L \
       "https://api.github.com/repos/${AT_OWNER_REPO}/actions/artifacts/${artifact_id}/zip" \
       -o "$tmpzip" 2>/dev/null && [ -s "$tmpzip" ]; then
    if python3 -c "
import sys, zipfile, pathlib
with zipfile.ZipFile('$tmpzip') as z:
    names = z.namelist()
    target = next((n for n in names if n.endswith('seen_escapes.json')), None)
    if target:
        pathlib.Path('${SEEN_ESCAPES_FILE}.dl').write_bytes(z.read(target))
        sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
      jq -s '.[0] * .[1]' "$SEEN_ESCAPES_FILE" "${SEEN_ESCAPES_FILE}.dl" \
        > "${SEEN_ESCAPES_FILE}.merged" 2>/dev/null \
        && mv "${SEEN_ESCAPES_FILE}.merged" "$SEEN_ESCAPES_FILE" || true
      rm -f "${SEEN_ESCAPES_FILE}.dl"
    fi
  fi
  rm -f "$tmpzip"
}

_evict_stale_escapes() {
  local ttl_s now_s before_count after_count
  ttl_s=$(( SEEN_ESCAPES_TTL_DAYS * 86400 ))
  now_s=$(date -u +%s)
  before_count=$(jq 'length' "$SEEN_ESCAPES_FILE" 2>/dev/null || echo 0)
  jq --argjson now "$now_s" --argjson ttl "$ttl_s" '
    with_entries(
      select(
        (.value | type == "object") and
        ((.value.t // "") != "") and
        (
          (now - ((.value.t | split("T")[0] + "T" + (.value.t | split("T")[1] | split("Z")[0]) | strptime("%Y-%m-%dT%H:%M:%S") | mktime) // 0)) < $ttl
        )
      )
    )
  ' "$SEEN_ESCAPES_FILE" > "${SEEN_ESCAPES_FILE}.tmp" 2>/dev/null \
    && mv "${SEEN_ESCAPES_FILE}.tmp" "$SEEN_ESCAPES_FILE" || true
  after_count=$(jq 'length' "$SEEN_ESCAPES_FILE" 2>/dev/null || echo 0)
  local evicted=$(( before_count - after_count ))
  [ "$evicted" -gt 0 ] && log_info "Seen-escapes cache: evicted $evicted stale entries"
}

_escape_cache_key() {
  printf '%s' "${1}|${2}|$(basename "${3}")"
}

_is_escape_seen() {
  local result
  result=$(jq -r --arg k "$1" '(.[$k] // null) | if type == "object" then "yes" else "no" end' \
    "$SEEN_ESCAPES_FILE" 2>/dev/null || echo "no")
  [ "$result" = "yes" ]
}

_mark_escape_seen() {
  local key="$1" etype="$2" conf="$3" now_ts
  now_ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  jq --arg k "$key" --arg etype "$etype" --arg conf "$conf" --arg t "$now_ts" \
    '. + {($k): {"t": $t, "type": $etype, "confidence": $conf}}' "$SEEN_ESCAPES_FILE" \
    > "${SEEN_ESCAPES_FILE}.tmp" 2>/dev/null \
    && mv "${SEEN_ESCAPES_FILE}.tmp" "$SEEN_ESCAPES_FILE" || true
}

_restore_seen_escapes_cache
_evict_stale_escapes
log_info "Seen-escapes cache loaded: $(jq 'length' "$SEEN_ESCAPES_FILE" 2>/dev/null || echo 0) entries (TTL=${SEEN_ESCAPES_TTL_DAYS}d)"

generated_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
lookback_start=$(date -u -d "-${LOOKBACK_DAYS} days" '+%Y-%m-%d' 2>/dev/null \
  || date -u -v "-${LOOKBACK_DAYS}d" '+%Y-%m-%d' 2>/dev/null \
  || date -u '+%Y-%m-%d')
lookback_end=$(date -u '+%Y-%m-%d')

num_fixpoints=$(jq 'length' "$FIX_POINTS_INPUT")
log_info "Phase 4: classifying $num_fixpoints fix points as bug escapes"

# Build the bug_escapes array
bug_escapes="[]"

for i in $(seq 0 $((num_fixpoints - 1))); do
  fp=$(jq -c ".[$i]" "$FIX_POINTS_INPUT")

  # Skip entries that were filtered out (e.g. flaky, spurious transitions)
  skipped_reason=$(echo "$fp" | jq -r '.skipped_reason // empty' 2>/dev/null || echo "")
  if [ -n "$skipped_reason" ]; then
    skip_test=$(echo "$fp" | jq -r '.failure.test_name // "unknown"')
    log_info "  [$((i+1))] Skipping $skip_test (reason: $skipped_reason)"
    continue
  fi

  # Skip entries where the fix transition was unstable (post-fix stability check failed).
  # These had a spurious pass followed by continued failures — fix attribution is unreliable.
  post_fix_stable=$(echo "$fp" | jq -r '.post_fix_stable // "unknown"')
  if [ "$post_fix_stable" = "false" ]; then
    skip_test=$(echo "$fp" | jq -r '.failure.test_name // "unknown"')
    pf_pass=$(echo "$fp" | jq -r '.post_fix_pass_count // 0')
    pf_fail=$(echo "$fp" | jq -r '.post_fix_fail_count // 0')
    log_warn "  [$((i+1))] Skipping $skip_test — spurious fix transition (post-fix: ${pf_pass} pass, ${pf_fail} fail)"
    continue
  fi

  # Skip low-confidence attributions for pre-existing failures.
  # If the failure streak was already present at the start of the lookback window AND
  # the fix attribution confidence is only "low", we can't reliably say when the regression
  # started or that this commit is the right fix. Suppress it to avoid false positives.
  streak_at_edge=$(echo "$fp" | jq -r '.streak_starts_at_window_edge // false')
  if [ "$streak_at_edge" = "true" ]; then
    # Read confidence from the best candidate fix commit
    best_conf=$(echo "$fp" | jq -r '
      .candidate_fix_commits
      | sort_by(if .confidence == "high" then 0 elif .confidence == "medium" then 1 else 2 end)
      | .[0].confidence // "low"
    ')
    if [ "$best_conf" = "low" ]; then
      skip_test=$(echo "$fp" | jq -r '.failure.test_name // "unknown"')
      log_warn "  [$((i+1))] Skipping $skip_test — pre-existing failure (streak_starts_at_window_edge=true) with low-confidence attribution"
      continue
    else
      log_warn "  [$((i+1))] $(echo "$fp" | jq -r '.failure.test_name // "unknown"'): pre-existing failure (streak_at_edge=true) but keeping — attribution confidence is $best_conf"
    fi
  fi

  # Extract failure info
  test_name=$(echo "$fp" | jq -r '.failure.test_name')
  test_pipeline=$(echo "$fp" | jq -r '.failure.workflow')
  test_job=$(echo "$fp" | jq -r '.failure.job')
  test_layer=$(echo "$fp" | jq -r '.failure.test_layer')
  failure_sig=$(echo "$fp" | jq -r '.failure.failure_signature')
  failing_run_ids_raw=$(echo "$fp" | jq -c '.failure.failing_run_ids')
  last_failing_run_id=$(echo "$fp" | jq '.last_failing_run_id')
  first_passing_run_id=$(echo "$fp" | jq '.first_passing_run_id')
  first_passing_job_id=$(echo "$fp" | jq '.first_passing_job_id // null')

  # Extract flat arrays for backward compat in output
  failing_run_ids=$(echo "$failing_run_ids_raw" | jq -c '[.[] | if type == "object" then .run_id else . end]')

  # Use the highest-confidence fix commit
  fix_commit=$(echo "$fp" | jq -c '
    .candidate_fix_commits
    | sort_by(
        if .confidence == "high" then 0
        elif .confidence == "medium" then 1
        else 2 end
      )
    | .[0]
  ')

  fix_sha=$(echo "$fix_commit" | jq -r '.sha // "unknown"')

  # Validate that fix_sha actually exists in the target repo via GitHub API.
  # This prevents corrupted/truncated SHAs from propagating to verify-commands.sh
  # and causing verification failures downstream.
  if [ "$fix_sha" != "unknown" ] && [ "$fix_sha" != "null" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
    sha_check_status=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/tenstorrent/tt-metal/commits/$fix_sha")
    if [ "$sha_check_status" != "200" ]; then
      skip_test=$(echo "$fp" | jq -r '.failure.test_name // "unknown"')
      log_warn "  [$((i+1))] Invalid fix SHA $fix_sha for $skip_test (HTTP $sha_check_status) — setting to unknown, skipping verification dispatch"
      fix_sha="unknown"
    fi
  fi

  # --- Seen-escapes dedup: skip if already reported in a prior run ---
  escape_key=$(_escape_cache_key "$fix_sha" "$test_name" "$test_pipeline")
  if _is_escape_seen "$escape_key"; then
    log_info "  [$((i+1))] Skipping $test_name — already reported (seen-escapes cache hit: ${fix_sha:0:8})"
    continue
  fi

  fix_layer=$(echo "$fix_commit" | jq -r '.fix_layer // "unknown"')
  fix_message=$(echo "$fix_commit" | jq -r '.message // ""')
  fix_files=$(echo "$fix_commit" | jq -c '.files_changed // []')
  fix_reasoning=$(echo "$fix_commit" | jq -r '.reasoning // ""')
  fix_confidence=$(echo "$fix_commit" | jq -r '.confidence // "low"')
  is_skip=$(echo "$fix_commit" | jq -r '.is_skip_or_disable // false')
  pr_number=$(echo "$fix_commit" | jq -r '.pr_number // empty' 2>/dev/null || echo "")
  pr_url=$(echo "$fix_commit" | jq -r '.pr_url // empty' 2>/dev/null || echo "")
  pr_title=$(echo "$fix_commit" | jq -r '.pr_title // empty' 2>/dev/null || echo "")

  # Build URLs — use job-level deep links when job_id is available
  commit_url="${AT_BASE_URL}/commit/${fix_sha}"

  failing_run_urls="[]"
  for entry_json in $(echo "$failing_run_ids_raw" | jq -c '.[]'); do
    if echo "$entry_json" | jq -e 'type == "object"' >/dev/null 2>&1; then
      rid=$(echo "$entry_json" | jq -r '.run_id')
      jid=$(echo "$entry_json" | jq -r '.job_id // empty')
      if [ -n "$jid" ] && [ "$jid" != "0" ]; then
        failing_run_urls=$(echo "$failing_run_urls" | jq --arg url "${AT_BASE_URL}/actions/runs/${rid}/job/${jid}" '. += [$url]')
      else
        failing_run_urls=$(echo "$failing_run_urls" | jq --arg url "${AT_BASE_URL}/actions/runs/${rid}" '. += [$url]')
      fi
    else
      rid=$(echo "$entry_json" | jq -r '.')
      failing_run_urls=$(echo "$failing_run_urls" | jq --arg url "${AT_BASE_URL}/actions/runs/${rid}" '. += [$url]')
    fi
  done

  # Last failing run — get job_id from the raw data
  last_failing_job_id=$(echo "$failing_run_ids_raw" | jq -r '[.[] | if type == "object" then .job_id else null end] | .[-1] // empty' 2>/dev/null || echo "")
  if [ -n "$last_failing_job_id" ] && [ "$last_failing_job_id" != "0" ] && [ "$last_failing_job_id" != "null" ]; then
    last_failing_run_url="${AT_BASE_URL}/actions/runs/${last_failing_run_id}/job/${last_failing_job_id}"
  else
    last_failing_run_url="${AT_BASE_URL}/actions/runs/${last_failing_run_id}"
  fi

  if [ "$first_passing_job_id" != "null" ] && [ -n "$first_passing_job_id" ] && [ "$first_passing_job_id" != "0" ]; then
    first_passing_run_url="${AT_BASE_URL}/actions/runs/${first_passing_run_id}/job/${first_passing_job_id}"
  else
    first_passing_run_url="${AT_BASE_URL}/actions/runs/${first_passing_run_id}"
  fi

  # Classify the escape
  escape_type=$(be_classify_escape "$test_layer" "$fix_layer")

  if [ "$escape_type" = "unknown" ]; then
    log_warn "  [$((i+1))] $test_name: could not classify (test_layer=$test_layer, fix_layer=$fix_layer) — including as unknown"
  else
    log_info "  [$((i+1))] $test_name: $escape_type escape (test=$test_layer, fix=$fix_layer)"
  fi

  post_fix_stable_val=$(echo "$fp" | jq -r '.post_fix_stable // "unknown"')
  post_fix_pass_val=$(echo "$fp" | jq -r '.post_fix_pass_count // 0')
  post_fix_fail_val=$(echo "$fp" | jq -r '.post_fix_fail_count // 0')
  streak_edge_val=$(echo "$fp" | jq -r '.streak_starts_at_window_edge // false')

  bug_escapes=$(echo "$bug_escapes" | jq \
    --arg type "$escape_type" \
    --arg tn "$test_name" \
    --arg tp "$test_pipeline" \
    --arg tj "$test_job" \
    --arg tl "$test_layer" \
    --arg fs "$failure_sig" \
    --argjson frid "$failing_run_ids" \
    --argjson frurls "$failing_run_urls" \
    --argjson lfr "$last_failing_run_id" \
    --arg lfr_url "$last_failing_run_url" \
    --argjson fpr "$first_passing_run_id" \
    --arg fpr_url "$first_passing_run_url" \
    --arg fsha "$fix_sha" \
    --arg fl "$fix_layer" \
    --arg fm "$fix_message" \
    --argjson ff "$fix_files" \
    --arg notes "$fix_reasoning" \
    --arg conf "$fix_confidence" \
    --arg is_skip "$is_skip" \
    --arg commit_url "$commit_url" \
    --arg pr_num "$pr_number" \
    --arg pr_url "$pr_url" \
    --arg pr_title "$pr_title" \
    --arg pf_stable "$post_fix_stable_val" \
    --argjson pf_pass "$post_fix_pass_val" \
    --argjson pf_fail "$post_fix_fail_val" \
    --argjson streak_edge "$([ "$streak_edge_val" = "true" ] && echo true || echo false)" \
    '. += [{
      "type": $type,
      "test_name": $tn,
      "test_pipeline": $tp,
      "test_job": $tj,
      "test_layer": $tl,
      "failure_signature": $fs,
      "failing_run_ids": $frid,
      "failing_run_urls": $frurls,
      "last_failing_run_id": $lfr,
      "last_failing_run_url": $lfr_url,
      "first_passing_run_id": $fpr,
      "first_passing_run_url": $fpr_url,
      "fix_commit_sha": $fsha,
      "fix_commit_url": $commit_url,
      "fix_commit_layer": $fl,
      "fix_commit_message": $fm,
      "fix_commit_files_changed": $ff,
      "fix_confidence": $conf,
      "is_skip_or_disable": ($is_skip == "true"),
      "post_fix_stable": $pf_stable,
      "post_fix_pass_count": $pf_pass,
      "post_fix_fail_count": $pf_fail,
      "streak_starts_at_window_edge": $streak_edge,
      "pr_number": (if $pr_num == "" then null else ($pr_num | tonumber) end),
      "pr_url": (if $pr_url == "" then null else $pr_url end),
      "pr_title": (if $pr_title == "" then null else $pr_title end),
      "analysis": $notes
    }]')

  # Record in seen-escapes cache so subsequent runs skip this escape
  _mark_escape_seen "$escape_key" "$escape_type" "$fix_confidence"

  current_count=$(echo "$bug_escapes" | jq 'length')
  if [ "$current_count" -ge "$MAX_ESCAPES" ]; then
    log_info "Reached MAX_ESCAPES=$MAX_ESCAPES — stopping classification early"
    break
  fi
done

# Deduplicate: if the same test_name+failure_signature appears multiple times
# (from different workflows or batches), keep only the highest-confidence entry.
before_dedup=$(echo "$bug_escapes" | jq 'length')
bug_escapes=$(echo "$bug_escapes" | jq '
  group_by(.test_name + ":" + .failure_signature) |
  map(
    sort_by(
      if .fix_confidence == "high" then 0
      elif .fix_confidence == "medium" then 1
      else 2 end
    ) | .[0]
  )
')
after_dedup=$(echo "$bug_escapes" | jq 'length')
if [ "$before_dedup" -ne "$after_dedup" ]; then
  removed=$((before_dedup - after_dedup))
  log_info "Deduplication: removed $removed duplicate escape(s) (same test seen in multiple workflows)"
fi

# Write final output — write escapes to a temp file to avoid ARG_MAX limits
# when the accumulated array is large (e.g. 62+ fix points)
_escapes_tmp=$(mktemp)
echo "$bug_escapes" > "$_escapes_tmp"
jq -n \
  --arg gen "$generated_at" \
  --arg window "${lookback_start} to ${lookback_end}" \
  --arg repo "$AT_OWNER_REPO" \
  --slurpfile escapes "$_escapes_tmp" \
  '{
    "generated_at": $gen,
    "lookback_window": $window,
    "repository": $repo,
    "bug_escapes": $escapes[0]
  }' > "$BUG_ESCAPES_OUTPUT"
rm -f "$_escapes_tmp"
log_info "Seen-escapes cache updated: $(jq 'length' "$SEEN_ESCAPES_FILE" 2>/dev/null || echo 0) entries"

# Print summary
total=$(echo "$bug_escapes" | jq 'length')
horizontal=$(echo "$bug_escapes" | jq '[.[] | select(.type == "horizontal")] | length')
vertical=$(echo "$bug_escapes" | jq '[.[] | select(.type == "vertical")] | length')
cross_layer=$(echo "$bug_escapes" | jq '[.[] | select(.type == "cross_layer")] | length')
unknown_count=$(echo "$bug_escapes" | jq '[.[] | select(.type == "unknown")] | length')
skip_count=$(echo "$bug_escapes" | jq '[.[] | select(.is_skip_or_disable == true)] | length')

log_info "Phase 4 done: $total bug escapes (horizontal=$horizontal, vertical=$vertical, cross_layer=${cross_layer:-0}, unknown=$unknown_count, skips=$skip_count)"

# Emit verify-commands.sh for high/medium confidence escapes
verify_script="$OUTPUT_DIR/verify-commands.sh"
echo '#!/usr/bin/env bash' > "$verify_script"
echo 'set -euo pipefail' >> "$verify_script"
echo '# Auto-generated verification dispatch commands' >> "$verify_script"
echo '' >> "$verify_script"

verify_count=0
echo "$bug_escapes" | jq -c '.[]' | while IFS= read -r escape; do
  etype=$(echo "$escape" | jq -r '.type')
  fix_sha=$(echo "$escape" | jq -r '.fix_commit_sha')
  pipeline=$(echo "$escape" | jq -r '.test_pipeline')
  job=$(echo "$escape" | jq -r '.test_job')
  tname=$(echo "$escape" | jq -r '.test_name')

  if [ "$fix_sha" = "unknown" ] || [ "$fix_sha" = "null" ]; then
    continue
  fi

  echo "echo \"Dispatching verification for $tname (fix: ${fix_sha:0:8})\"" >> "$verify_script"
  echo "gh workflow run verify-bug-escape-ci.yaml \\" >> "$verify_script"
  echo "  -R tenstorrent/tt-auto-triage \\" >> "$verify_script"
  echo "  --ref ebanerjee/bug-escapes \\" >> "$verify_script"
  echo "  -f fix-commit-sha=$fix_sha \\" >> "$verify_script"
  echo "  -f test-pipeline=$pipeline \\" >> "$verify_script"
  echo "  -f test-job=\"$job\" \\" >> "$verify_script"
  echo "  -f test-name=\"$tname\"" >> "$verify_script"
  echo "" >> "$verify_script"
done

chmod +x "$verify_script"
log_info "Wrote verify-commands.sh with dispatch commands"

# Write GitHub Actions step summary if available
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "## Bug Escape Detection Report"
    echo ""
    echo "| | |"
    echo "|---|---|"
    echo "| **Repository** | \`$AT_OWNER_REPO\` |"
    echo "| **Generated** | $generated_at |"
    echo "| **Lookback window** | ${lookback_start} to ${lookback_end} |"
    echo "| **Total bug escapes** | **$total** ($horizontal horizontal, $vertical vertical, ${cross_layer:-0} cross-layer, $unknown_count unknown) |"
    if [ "$skip_count" -gt 0 ]; then
      echo "| **Test skips (not real fixes)** | $skip_count |"
    fi
    echo ""

    if [ "$total" -gt 0 ]; then
      echo "---"
      echo ""

      idx=0
      echo "$bug_escapes" | jq -c '.[]' | while IFS= read -r escape; do
        idx=$((idx + 1))

        etype=$(echo "$escape" | jq -r '.type')
        tn=$(echo "$escape" | jq -r '.test_name')
        tj=$(echo "$escape" | jq -r '.test_job')
        tl=$(echo "$escape" | jq -r '.test_layer')
        fl=$(echo "$escape" | jq -r '.fix_commit_layer')
        fs=$(echo "$escape" | jq -r '.failure_signature')
        conf=$(echo "$escape" | jq -r '.fix_confidence')
        analysis=$(echo "$escape" | jq -r '.analysis')
        is_skip_val=$(echo "$escape" | jq -r '.is_skip_or_disable')

        fsha=$(echo "$escape" | jq -r '.fix_commit_sha')
        commit_url=$(echo "$escape" | jq -r '.fix_commit_url')
        fm=$(echo "$escape" | jq -r '.fix_commit_message')
        fpr_url=$(echo "$escape" | jq -r '.first_passing_run_url')
        lfr_url=$(echo "$escape" | jq -r '.last_failing_run_url')

        e_pr_url=$(echo "$escape" | jq -r '.pr_url // empty' 2>/dev/null || echo "")
        e_pr_num=$(echo "$escape" | jq -r '.pr_number // empty' 2>/dev/null || echo "")

        # Type badge
        case "$etype" in
          horizontal) type_badge="Horizontal" ;;
          vertical) type_badge="Vertical" ;;
          cross_layer) type_badge="Cross-Layer" ;;
          *) type_badge="Unknown" ;;
        esac

        # Confidence badge
        case "$conf" in
          high) conf_badge="High" ;;
          medium) conf_badge="Medium" ;;
          *) conf_badge="Low" ;;
        esac

        echo "### ${idx}. ${type_badge} Bug Escape — \`${tj}\`"
        echo ""

        if [ "$is_skip_val" = "true" ]; then
          echo "> **Note**: This commit skipped/disabled the test rather than fixing the root cause."
          echo ""
        fi

        echo "| | |"
        echo "|---|---|"
        echo "| **Escape type** | ${type_badge} (test layer: \`${tl}\`, fix layer: \`${fl}\`) |"
        echo "| **Confidence** | ${conf_badge} |"
        echo "| **Test** | \`${tn}\` |"
        echo "| **Failure signature** | ${fs} |"
        e_streak_edge=$(echo "$escape" | jq -r '.streak_starts_at_window_edge // false')
        if [ "$e_streak_edge" = "true" ]; then
          echo "| **⚠️ Pre-existing failure** | Failure streak began before the lookback window — start date unknown |"
        fi
        echo "| **Fix commit** | [\`${fsha:0:10}\`](${commit_url}) — ${fm} |"

        pf_stable_val=$(echo "$escape" | jq -r '.post_fix_stable // "unknown"')
        pf_pass_val=$(echo "$escape" | jq -r '.post_fix_pass_count // 0')
        pf_fail_val=$(echo "$escape" | jq -r '.post_fix_fail_count // 0')
        pf_total=$((pf_pass_val + pf_fail_val))
        case "$pf_stable_val" in
          "true")    pf_badge="✅ Stable (${pf_pass_val}/${pf_total} pass)" ;;
          "false")   pf_badge="❌ Unstable (${pf_pass_val}/${pf_total} pass)" ;;
          "insufficient_data") pf_badge="⚠️ Insufficient data (${pf_pass_val}p/${pf_fail_val}f)" ;;
          *)         pf_badge="—" ;;
        esac
        echo "| **Post-fix stability** | ${pf_badge} |"

        if [ -n "$e_pr_url" ] && [ -n "$e_pr_num" ]; then
          echo "| **Pull request** | [#${e_pr_num}](${e_pr_url}) |"
        fi

        echo "| **Last failing run** | [View run](${lfr_url}) |"
        echo "| **First passing run** | [View run](${fpr_url}) |"

        # Failing run links
        num_failing=$(echo "$escape" | jq '.failing_run_urls | length')
        if [ "$num_failing" -gt 0 ]; then
          failing_links=""
          fidx=0
          for furl in $(echo "$escape" | jq -r '.failing_run_urls[]'); do
            fidx=$((fidx + 1))
            if [ -n "$failing_links" ]; then
              failing_links="${failing_links}, "
            fi
            failing_links="${failing_links}[Run ${fidx}](${furl})"
          done
          echo "| **All failing runs** | ${failing_links} |"
        fi

        # Files changed
        num_files=$(echo "$escape" | jq '.fix_commit_files_changed | length')
        if [ "$num_files" -gt 0 ]; then
          files_list=$(echo "$escape" | jq -r '[.fix_commit_files_changed[:10][] | "`\(.)`"] | join(", ")')
          if [ "$num_files" -gt 10 ]; then
            files_list="${files_list}, ... (+$((num_files - 10)) more)"
          fi
          echo "| **Files changed** | ${files_list} |"
        fi

        echo ""

        # Analysis section
        if [ -n "$analysis" ] && [ "$analysis" != "null" ]; then
          echo "<details>"
          echo "<summary><b>Analysis</b> (click to expand)</summary>"
          echo ""
          echo "$analysis"
          echo ""
          echo "</details>"
          echo ""
        fi

        echo "---"
        echo ""
      done
    else
      echo "_No bug escapes detected in this window._"
    fi
  } >> "$GITHUB_STEP_SUMMARY"
fi
