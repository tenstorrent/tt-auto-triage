# slack_message.jq - Report formatting for Slack messages
# Single source of truth for person, join_people, section_line, commit_entry, etc.
# Used by build_slack_payload.sh for normal (non-cancellation) reports.
#
# Expects --arg run_url, run_label, job_name, workflow_name, auto_fix, allow_pings, job_owner_ping

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
    # Only ping if allow_pings is true AND use_slack_id is true AND slack_id exists
    # Groups/subteams (S-prefixed IDs) are never pinged to avoid spamming entire teams
    (if ($allow_pings == "true") and use_slack_id and (p.slack_id // "") != "" and ((p.slack_id | startswith("S")) | not) then
      "<@" + p.slack_id + ">"
    else
      (p.name // p.login // "Unknown")
    end)
  end;

def join_people(arr; use_slack_id):
  arr | map(person(.; use_slack_id)) | join(", ");

# Case 3 (non-deterministic/hardware): do not auto-ping metalinfra; leave RELEVANT DEVELOPERS blank.
def ensure_case3_ping(arr; case_val):
  (arr // []);

def section_line(lbl; txt):
  if (txt // "") != "" then "*\(lbl):* " + txt + "\n" else "" end;

def section_people(lbl; arr; use_slack_id):
  if (arr | type) == "array" and (arr | length) > 0 then
    "*\(lbl):* " + join_people(arr; use_slack_id) + "\n"
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
# Input: JSON from slack_message_path; args: run_url, run_label, job_name, workflow_name, auto_fix, allow_pings, job_owner_ping
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
     # NOTE: ensure_case3_ping is used for multiple cases (3, 4, and others) to keep ping formatting consistent.
     then section_people("RELEVANT DEVELOPERS"; ensure_case3_ping(.relevant_developers; .case); true)
     elif ($has_commits and ($case == "5"))
     then section_people("RELEVANT DEVELOPERS"; ensure_case3_ping(.relevant_developers; .case); true)
     elif $has_commits
     then ""
     else section_people("RELEVANT DEVELOPERS"; ensure_case3_ping(.relevant_developers; .case); true)
    end)
  + (if $is_case4 then section_line("SUMMARY"; .slack_message) else "" end)
  + (if $is_case4 then section_line("NOTE"; "Could not identify a single high-confidence culprit commit.") else "" end)
  + section_line("NOTES"; .notes)
  + (if ($auto_fix // "") != "" then "\n*AUTO-FIX:* Draft PR created -> <\($auto_fix)|link>\n" else "" end)
  + (if ($job_owner_ping != "") and (($case == "1") or ($case == "2") or ($case == "4")) then "\n*JOB OWNER:* " + $job_owner_ping + "\n" else "" end)
  + "\n---\n_DISCLAIMER: This analysis has been done by AI. Do not take the results as absolute truth since it has been inaccurate in the past._"
) | gsub("\n{3,}"; "\n\n")
