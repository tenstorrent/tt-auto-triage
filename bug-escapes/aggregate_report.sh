#!/usr/bin/env bash
#
# aggregate_report.sh — Merge detection + per-escape verification artifacts
# into a single combined report.
#
# Inputs (from the aggregate job in bug-escapes-ci.yaml):
#   ./artifacts/bug-escapes-output/bug-escapes-output.json
#       Produced by the detect job (all escapes, classified).
#   ./artifacts/verification-result-*/verification-result.json
#       Produced by each verify-all matrix leg (one per attempted escape).
#
# Outputs:
#   ./triage/bug-escapes/output/bug-escapes-final-report.json
#   ./triage/bug-escapes/output/bug-escapes-final-report.md
#   Appends to $GITHUB_STEP_SUMMARY if set.
#
# Exit status is ALWAYS 0 — a malformed input never fails the workflow,
# because the whole point of this job is to publish whatever data survived.

set -uo pipefail  # intentionally NOT -e: keep going on missing inputs

ART_DIR="${ART_DIR:-artifacts}"
OUT_DIR="${OUT_DIR:-triage/bug-escapes/output}"
DETECT_FILE="$ART_DIR/bug-escapes-output/bug-escapes-output.json"
FINAL_JSON="$OUT_DIR/bug-escapes-final-report.json"
FINAL_MD="$OUT_DIR/bug-escapes-final-report.md"

AUTO_VERIFY="${AUTO_VERIFY:-false}"
VERIFY_COUNT="${VERIFY_COUNT:-0}"

mkdir -p "$OUT_DIR"

log() { printf '[aggregate] %s\n' "$*"; }

now_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# -------------------------------------------------------------------
# Case 1: detection artifact missing — emit a minimal "nothing to report"
# -------------------------------------------------------------------
if [ ! -f "$DETECT_FILE" ]; then
  log "Detection artifact not found at $DETECT_FILE — writing minimal report"

  jq -n --arg ts "$now_utc" '{
    generated_at: $ts,
    status: "detection_failed_or_missing",
    message: "bug-escapes-output.json was not produced by the detect job",
    bug_escapes: [],
    totals: { total: 0, confirmed: 0, refuted: 0, inconclusive: 0,
              skipped_deadline: 0, not_attempted: 0 }
  }' > "$FINAL_JSON"

  {
    echo "# Bug Escape Detection + Verification Report"
    echo ""
    echo "_Generated $now_utc_"
    echo ""
    echo "**Detection artifact was missing.** The detect job did not produce"
    echo "\`bug-escapes-output.json\`. Nothing to verify or report."
  } > "$FINAL_MD"

  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    cat "$FINAL_MD" >> "$GITHUB_STEP_SUMMARY"
  fi
  exit 0
fi

# -------------------------------------------------------------------
# Collect every verification-result.json into a single JSON array
# -------------------------------------------------------------------
verifications='[]'
found_count=0
for vf in "$ART_DIR"/verification-result-*/verification-result.json; do
  [ -f "$vf" ] || continue
  # Robustly parse — if a result file is malformed, skip it rather than crashing
  if vj=$(jq -c '.' "$vf" 2>/dev/null) && [ -n "$vj" ]; then
    verifications=$(echo "$verifications" | jq --argjson v "$vj" '. += [$v]')
    found_count=$((found_count + 1))
  else
    log "Warning: could not parse $vf — skipping"
  fi
done
log "Collected $found_count verification result(s)"

# -------------------------------------------------------------------
# Join each bug escape with its verification (if any) and categorize.
# Matching key: (fix_commit_sha, test_name) — unique per dedup in Phase 4.
#
# Categories:
#   confirmed          — verify produced a "confirmed" verdict (real bug escape)
#   refuted            — verify produced a "refuted" verdict (false flag)
#   inconclusive       — verify produced inconclusive/timed_out/cancelled result
#   skipped_deadline   — the 6h global budget was already exhausted
#   not_attempted      — escape did not meet the vertical + high filter
# -------------------------------------------------------------------
combined=$(jq -n \
  --slurpfile detect "$DETECT_FILE" \
  --argjson vers "$verifications" \
  --arg ts "$now_utc" \
  --arg auto_verify "$AUTO_VERIFY" \
  --arg verify_count "$VERIFY_COUNT" '
    ($detect[0]) as $d |
    ($vers) as $v |
    ($d.bug_escapes // []) as $escapes |

    # Build a lookup from verification array: key = fix_commit + "|" + test_name
    ($v | map(
      { key: ((.fix_commit // "") + "|" + (.test_name // "")), value: . }
    ) | from_entries) as $vlookup |

    # Decorate each bug escape with its verification status
    ($escapes | map(
      . as $e |
      (($e.fix_commit_sha // "") + "|" + ($e.test_name // "")) as $key |
      ($vlookup[$key] // null) as $vr |
      {
        # pass through core fields
        test_name: $e.test_name,
        test_pipeline: $e.test_pipeline,
        test_job: $e.test_job,
        test_layer: $e.test_layer,
        type: $e.type,
        failure_signature: $e.failure_signature,
        fix_commit_sha: $e.fix_commit_sha,
        fix_commit_url: $e.fix_commit_url,
        fix_commit_layer: $e.fix_commit_layer,
        fix_commit_message: $e.fix_commit_message,
        fix_confidence: $e.fix_confidence,
        is_skip_or_disable: $e.is_skip_or_disable,
        last_failing_run_url: $e.last_failing_run_url,
        first_passing_run_url: $e.first_passing_run_url,
        pr_number: $e.pr_number,
        pr_url: $e.pr_url,
        pr_title: $e.pr_title,

        # Filter decision: did this escape meet the verify criteria?
        was_verification_candidate:
          (($e.type == "vertical") and
           ($e.fix_confidence == "high") and
           ($e.fix_commit_sha != null) and ($e.fix_commit_sha != "unknown")),

        # Join with verification result
        verification: (
          if $vr != null then
            {
              status: (
                (($vr.verdict // "") | ascii_downcase) as $vd |
                if $vd == "confirmed" then "confirmed"
                elif $vd == "refuted" then "refuted"
                elif $vd == "skipped_deadline" then "skipped_deadline"
                elif ($vd | test("inconclusive|timed_out|cancelled|timeout")) then "inconclusive"
                else "inconclusive"
                end
              ),
              verdict: $vr.verdict,
              reason: $vr.reason,
              before_run_id: ($vr.before_run_id // null),
              before_conclusion: ($vr.before_conclusion // null),
              after_run_id: ($vr.after_run_id // null),
              after_conclusion: ($vr.after_conclusion // null),
              timestamp: $vr.timestamp
            }
          else
            # No verification artifact found. Distinguish filtered-out vs
            # candidate-without-artifact (shouldnt happen with if: always uploads).
            {
              status: (
                if (($e.type == "vertical") and
                    ($e.fix_confidence == "high") and
                    ($e.fix_commit_sha != null) and
                    ($e.fix_commit_sha != "unknown")) then
                  "missing_artifact"
                else
                  "not_attempted"
                end
              ),
              verdict: null,
              reason: (
                if (($e.type == "vertical") and
                    ($e.fix_confidence == "high") and
                    ($e.fix_commit_sha != null) and
                    ($e.fix_commit_sha != "unknown")) then
                  "verification was expected but no artifact was found"
                else
                  "did not meet verify filter (needs type=vertical, fix_confidence=high, valid SHA)"
                end
              )
            }
          end
        )
      }
    )) as $decorated |

    # Totals by status
    ($decorated | group_by(.verification.status) | map({
      key: .[0].verification.status, value: (. | length)
    }) | from_entries) as $by_status |

    {
      generated_at: $ts,
      auto_verify_enabled: ($auto_verify == "true"),
      verification_candidates: ($verify_count | tonumber? // 0),
      detect: {
        generated_at: $d.generated_at,
        lookback_window: $d.lookback_window,
        repository: $d.repository
      },
      totals: {
        total: ($decorated | length),
        confirmed: ($by_status.confirmed // 0),
        refuted: ($by_status.refuted // 0),
        inconclusive: ($by_status.inconclusive // 0),
        skipped_deadline: ($by_status.skipped_deadline // 0),
        missing_artifact: ($by_status.missing_artifact // 0),
        not_attempted: ($by_status.not_attempted // 0)
      },
      bug_escapes: $decorated
    }
  ')

# Persist JSON
echo "$combined" > "$FINAL_JSON"
log "Wrote $FINAL_JSON"

# -------------------------------------------------------------------
# Render markdown report
# -------------------------------------------------------------------
total=$(echo "$combined" | jq '.totals.total')
confirmed=$(echo "$combined" | jq '.totals.confirmed')
refuted=$(echo "$combined" | jq '.totals.refuted')
inconclusive=$(echo "$combined" | jq '.totals.inconclusive')
skipped=$(echo "$combined" | jq '.totals.skipped_deadline')
missing=$(echo "$combined" | jq '.totals.missing_artifact')
not_attempted=$(echo "$combined" | jq '.totals.not_attempted')
lookback=$(echo "$combined" | jq -r '.detect.lookback_window // "unknown"')
auto_verify_val=$(echo "$combined" | jq -r '.auto_verify_enabled')

render_section() {
  local title="$1" status_key="$2"
  local rows
  rows=$(echo "$combined" | jq -r --arg k "$status_key" '
    [.bug_escapes[] | select(.verification.status == $k)] as $entries |
    if ($entries | length) == 0 then
      "_(none)_"
    else
      ([
        "| Test | Pipeline / Job | Fix | Before | After | Reason |",
        "|---|---|---|---|---|---|"
      ] + ($entries | map(
        "| `" + (.test_name // "?") + "` " +
        "| " + ((.test_pipeline // "?") | ltrimstr(".github/workflows/")) +
          " / " + (.test_job // "?") + " " +
        "| [`" + ((.fix_commit_sha // "?")[0:10]) + "`](" + (.fix_commit_url // "#") + ")" +
          (if .pr_number then " [#" + (.pr_number | tostring) + "](" + (.pr_url // "#") + ")" else "" end) + " " +
        "| " + (.verification.before_conclusion // "?") +
          (if .verification.before_run_id and (.verification.before_run_id != 0)
           then " ([run](https://github.com/tenstorrent/tt-metal/actions/runs/" + (.verification.before_run_id | tostring) + "))"
           else "" end) + " " +
        "| " + (.verification.after_conclusion // "?") +
          (if .verification.after_run_id and (.verification.after_run_id != 0)
           then " ([run](https://github.com/tenstorrent/tt-metal/actions/runs/" + (.verification.after_run_id | tostring) + "))"
           else "" end) + " " +
        "| " + ((.verification.reason // "") | gsub("\\|"; "\\|")) + " |"
      ))) | join("\n")
    end
  ')
  printf '### %s\n\n%s\n' "$title" "$rows"
}

render_not_attempted() {
  echo "$combined" | jq -r '
    [.bug_escapes[] | select(.verification.status == "not_attempted")] as $entries |
    if ($entries | length) == 0 then "_(none)_"
    else
      ("| Test | Type | Confidence | Fix SHA | Reason |\n" +
       "|---|---|---|---|---|\n" +
       ($entries | map(
         "| `" + (.test_name // "?") + "` " +
         "| " + (.type // "?") + " " +
         "| " + (.fix_confidence // "?") + " " +
         "| `" + ((.fix_commit_sha // "?")[0:10]) + "` " +
         "| " + (.verification.reason // "") + " |"
       ) | join("\n")))
    end
  '
}

{
  echo "# Bug Escape Detection + Verification Report"
  echo ""
  echo "_Generated ${now_utc} — lookback window: ${lookback}_"
  echo ""
  echo "## Summary"
  echo ""
  echo "| | |"
  echo "|---|---|"
  echo "| Total bug escapes found | **$total** |"
  echo "| Auto-verify | $auto_verify_val |"
  echo "| Confirmed real bug escapes | **$confirmed** |"
  echo "| Refuted (false flags) | **$refuted** |"
  echo "| Inconclusive | $inconclusive |"
  echo "| Skipped (deadline) | $skipped |"
  if [ "${missing:-0}" != "0" ]; then
    echo "| Missing verification artifact | $missing |"
  fi
  echo "| Not attempted (filtered out) | $not_attempted |"
  echo ""
  echo "---"
  echo ""
  echo "## Verified results"
  echo ""
  render_section "Confirmed real bug escapes ($confirmed)" "confirmed"
  echo ""
  render_section "Refuted — false flags ($refuted)" "refuted"
  echo ""
  render_section "Inconclusive ($inconclusive)" "inconclusive"
  echo ""
  render_section "Skipped due to 6h deadline ($skipped)" "skipped_deadline"
  if [ "${missing:-0}" != "0" ]; then
    echo ""
    render_section "Missing artifact ($missing)" "missing_artifact"
  fi
  echo ""
  echo "---"
  echo ""
  echo "## Not attempted (did not meet verify filter)"
  echo ""
  echo "Bug escapes found by detection but not verified because they did not"
  echo "match the criteria \`type == vertical AND fix_confidence == high AND"
  echo "fix_commit_sha valid\`."
  echo ""
  render_not_attempted
} > "$FINAL_MD"

log "Wrote $FINAL_MD"

# Step summary (for the GH Actions UI)
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$FINAL_MD" >> "$GITHUB_STEP_SUMMARY"
  log "Appended to GITHUB_STEP_SUMMARY"
fi

log "Done. total=$total confirmed=$confirmed refuted=$refuted inconclusive=$inconclusive skipped=$skipped not_attempted=$not_attempted"
exit 0
