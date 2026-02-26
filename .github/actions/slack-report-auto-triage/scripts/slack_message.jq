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
    (if ($allow_pings == "true") and use_slack_id and (p.slack_id // "") != "" then
      # Groups/subteams start with "S" and use <!subteam^ID|@handle> format
      (if (p.slack_id | startswith("S")) then
        "<!subteam^" + p.slack_id + "|@" + (p.name // p.login // "group") + ">"
      else
        "<@" + p.slack_id + ">"
      end)
    else
      (p.name // p.login // "Unknown")
    end)
  end;

def join_people(arr; use_slack_id):
  arr | map(person(.; use_slack_id)) | join(", ");

def ensure_case3_ping(arr; case_val):
  if ((case_val | tostring) == "3") then
    (arr // []) + [{ "name": "metalinfa", "slack_id": "S0985AN7TC5" }]
  else
    (arr // [])
  end;

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
    "*COMMITS:*\n" + (arr | map(commit_entry(.)) | join("\n")) + "\n"
  else "" end;

# Main expression: build text from slack_message.json
# Input: JSON from slack_message_path; args: run_url, run_label, job_name, workflow_name, auto_fix, allow_pings, job_owner_ping
(if ((.commits | type) == "array" and (.commits | length) > 0) then true else false end) as $has_commits
|
(
  section_line("FULL REPORT"; "<\($run_url)|\($run_label)>")
  + section_line("FAILING WORKFLOW"; (if (($workflow_name // "") | length) > 0 then $workflow_name else (.workflow_name // "unknown workflow") end))
  + section_line("FAILING JOB"; (if (($job_name // "") | length) > 0 then $job_name else (.failing_job_name // .workflow_name // "unknown job") end))
  + section_line("FAILING TEST"; (.failing_test_name // "unknown test"))
  + section_line("FAILING RUN"; (if (.failing_run_url // "") != "" then "<\(.failing_run_url)|\(.failing_run_label // "latest failing run")>" else "" end))
  + section_line("SCENARIO"; .scenario)
  + section_code("FAILURE MESSAGE"; .failure_message)
  + commits_section(.commits)
  + (if ($has_commits and ((.case | tostring) == "5"))
     then section_people("RELEVANT DEVELOPERS"; ensure_case3_ping(.relevant_developers; .case); true)
     elif $has_commits
     then ""
     else section_people("RELEVANT DEVELOPERS"; ensure_case3_ping(.relevant_developers; .case); true)
    end)
  + section_line("NOTES"; .notes)
  + (if ($auto_fix // "") != "" then "\n*AUTO-FIX:* Draft PR created -> <\($auto_fix)|link>\n" else "" end)
  + (if ($job_owner_ping != "") and ((.case | tostring) as $c | $c == "1" or $c == "2" or $c == "4") then "\n*JOB OWNER:* " + $job_owner_ping + "\n" else "" end)
  + "\n---\n_DISCLAIMER: This analysis has been done by AI. Do not take the results as absolute truth since it has been inaccurate in the past._"
) | gsub("\n{3,}"; "\n\n")
