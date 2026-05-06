# slack_message.jq - Report formatting for Slack messages
# Single source of truth for person, join_people, section_line, commit_entry, etc.
# Used by build_slack_payload.sh for normal (non-cancellation) reports.
#
# Expects --arg run_url, run_label, job_name, workflow_name, auto_fix, allow_pings, --argjson job_owner

def short_hash(h):
  if (h // "") == "" then "unknown" else (h[0:8]) end;

def commit_link(c):
  if (c.url // c.commit_url // "") != "" then
    "<" + (c.url // c.commit_url) + "|" + short_hash(c.hash // c.commit) + ">"
  else short_hash(c.hash // c.commit)
  end;

def person(p; use_slack_id):
  if p == null then
    "Unknown"
  else
    (p.name // p.login // "Unknown") as $display_name
    # Only ping if allow_pings is true AND use_slack_id is true AND slack_id exists.
    # resolve_group_pings.py pre-resolves S-prefixed group IDs to a U-prefixed individual;
    # any remaining S-prefixed IDs are unresolvable and are excluded from pings.
    | (if ($allow_pings == "true") and use_slack_id and (p.slack_id // "") != "" and ((p.slack_id | startswith("S")) | not) then
        # When the name contains "(representing <group>)", preserve that context alongside the ping.
        # Use index() to safely find the suffix — avoids issues with split() when the group name
        # itself contains the literal substring "(representing ".
        "<@" + p.slack_id + ">"
        + (($display_name | index("(representing ")) as $idx
           | if $idx != null then " " + $display_name[$idx:] else "" end)
      else
        $display_name
      end)
  end;

def join_people(arr; use_slack_id):
  arr | map(person(.; use_slack_id)) | join(", ");

def section_line(lbl; txt):
  if (txt // "") != "" then "*\(lbl):* " + txt + "\n" else "" end;

def section_people(lbl; arr; use_slack_id):
  if (arr | type) == "array" and (arr | length) > 0 then
    "*\(lbl):* " + join_people(arr; use_slack_id) + "\n"
  else "" end;

def person_job_owner(p; use_slack_id):
  person(p; use_slack_id)
  + (if (p.is_default_owner // false) then
      " (As a representative for the metalinfra team. Metalinfra was chosen as the default owner as this job has no owner. Please find a suitable owner)."
    else "" end);

def join_job_owners(arr; use_slack_id):
  arr | map(person_job_owner(.; use_slack_id)) | join(", ");

def section_job_owners(lbl; arr; use_slack_id):
  if (arr | type) == "array" and (arr | length) > 0 then
    "*\(lbl):* " + join_job_owners(arr; use_slack_id) + "\n"
  else "" end;

def section_files(lbl; arr):
  if (arr | type) == "array" and (arr | length) > 0 then
    "*\(lbl):*\n```\n" + (arr | join("\n")) + "\n```\n"
  else "" end;

def section_code(lbl; txt):
  if (txt // "") != "" then "*\(lbl):*\n```" + txt + "```\n" else "" end;

def confidence_label(c):
  (c | if type == "string" then (tonumber? // 0) else . end) as $n
  | if $n == 100 then "HIGH CONFIDENCE"
    elif $n > 95 and $n < 100 then "MEDIUM CONFIDENCE"
    else "LOW CONFIDENCE"
    end;

def commit_entry(c):
  "- HASH: " + commit_link(c) + "\n"
  + "  AUTHOR: " + person(c.author; true) + "\n"
  + (if (c.approvers | type) == "array" and (c.approvers | length) > 0 then
      "  APPROVERS: " + join_people(c.approvers; false) + "\n"
    else "" end)
  + (if (c.relevant_developers | type) == "array" and (c.relevant_developers | length) > 0 then
      "  RELEVANT DEVELOPERS: " + join_people(c.relevant_developers; false) + "\n"
    else "" end)
  + (if (c.relevant_files | type) == "array" and (c.relevant_files | length) > 0 then
      "  RELEVANT FILES:\n```\n" + (c.relevant_files | join("\n")) + "\n```\n"
    else "" end)
  + (if (c.confidence // null) != null then
      "  CONFIDENCE: " + confidence_label(c.confidence) + "\n"
    else "" end);

def commits_section(arr):
  if (arr | type) == "array" and (arr | length) > 0 then
    "*COMMITS:*\n" + commit_entry(arr[0]) + "\n"
    + (if (arr | length) > 1 then
        "_" + ((arr | length) - 1 | tostring) + " more commit(s) in full report_\n"
       else "" end)
  else "" end;

# Main expression: build text from slack_message.json
# Input: JSON from slack_message_path; args: run_url, run_label, job_name, workflow_name, auto_fix, allow_pings, job_owner
(.case | tostring) as $case
| (if ((.commits | type) == "array" and (.commits | length) > 0) then true else false end) as $has_commits
| ($case == "4") as $is_case4
|
(
  section_line("FULL REPORT"; "<\($run_url)|\($run_label)>")
  + section_line("FAILING WORKFLOW"; (if (($workflow_name // "") | length) > 0 then $workflow_name else (.workflow_name // "unknown workflow") end))
  + section_line("FAILING JOB"; (if (($job_name // "") | length) > 0 then $job_name else (.failing_job_name // .workflow_name // "unknown job") end))
  + section_line("FAILING TEST"; (.failing_test_name // "unknown test"))
  + section_line("FAILING RUN"; (if (.failing_run_url // "") != "" then "<\(.failing_run_url)|\(.failing_run_label // "latest failing run")>" else "" end))
  + section_line("SCENARIO"; .scenario)
  + section_code("FAILURE MESSAGE"; .failure_message)
  + (if $is_case4 then "" else commits_section(.commits) end)
  + (if $is_case4
     # Case 4: show relevant_developers for context but omit commits section above
     then section_people("RELEVANT DEVELOPERS"; (.relevant_developers // []); true)
     elif ($has_commits and ($case == "5"))
     # Case 5: LLM populates relevant_developers from the top suspect commits
     then section_people("RELEVANT DEVELOPERS"; (.relevant_developers // []); true)
     elif $has_commits
     # Cases 1/2: developers are listed per-commit, not at the top level
     then ""
     # Case 3: LLM emits [] for relevant_developers to suppress pings on non-deterministic failures
     else section_people("RELEVANT DEVELOPERS"; (.relevant_developers // []); true)
    end)
  + (if $is_case4 then section_line("SUMMARY"; .slack_message) else "" end)
  + (if $is_case4 then section_line("NOTE"; "Could not identify a single high-confidence culprit commit.") else "" end)
  + section_line("NOTES"; .notes)
  + (if ($auto_fix // "") != "" then "\n*AUTO-FIX:* Draft PR created -> <\($auto_fix)|link>\n" else "" end)
  + (if (($case == "1") or ($case == "2") or ($case == "4")) and (($job_owner | type) == "array") and (($job_owner | length) > 0)
     # Prepend "\n" so JOB OWNER is visually separated from the previous section,
     # matching the leading-newline convention used by AUTO-FIX above. The trailing
     # gsub("\n{3,}"; "\n\n") collapses any extra newlines that may stack up.
     then "\n" + section_job_owners("JOB OWNER"; $job_owner; true)
     else ""
     end)
  + "\n---\n_DISCLAIMER: This analysis has been done by AI. Do not take the results as absolute truth since it has been inaccurate in the past._"
) | gsub("\n{3,}"; "\n\n")
