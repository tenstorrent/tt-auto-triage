#!/usr/bin/env python3
"""
Extract error messages from build_slack_export_with_threads.json

When EXTRACT_ALL_ERRORS is False (default):
- Only includes non-deterministic errors:
  - Errors from cancelled analysis runs (Auto-triage cancelled) that have "FAILURE MESSAGE:"
  - Errors with scenario "Failure likely outside tt-metal" that have "FAILURE MESSAGE:"

When EXTRACT_ALL_ERRORS is True:
- Extracts all error messages from "FAILURE MESSAGE:" field regardless of determinism

All errors are extracted from the "FAILURE MESSAGE:" field as-is, without any truncation or cleanup.
Entries without "FAILURE MESSAGE:" are skipped.

Output format: [error_message, failing_run_url, formatted_timestamp, job_name, workflow_name, is_nd, full_report_link, unix_timestamp]
All fields except error_message can be None if not available.
is_nd is a boolean indicating if the error is marked as non-deterministic (ND).
full_report_link is the URL to the auto-triage workflow run that analyzed this failure.
unix_timestamp is the raw Slack timestamp; the formatted one is for display only.
"""

import json
import os
import sys
import re
import time
from datetime import datetime

from timestamps import format_unix

# Set to True to extract all errors, False to only extract non-deterministic errors
EXTRACT_ALL_ERRORS = True

# Date range filtering (from environment variables)
DATE_RANGE_START = os.environ.get("DATE_RANGE_START", "")
DATE_RANGE_END = os.environ.get("DATE_RANGE_END", "")


def parse_date_to_timestamp(date_str: str) -> float:
    """Convert date string like 'January 1, 2026' to Unix timestamp."""
    if not date_str or not date_str.strip():
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%B %d, %Y")
        return time.mktime(dt.timetuple())
    except ValueError:
        return None


def is_non_deterministic(entry):
    """Check if an entry represents a non-deterministic error."""
    full_text = entry.get("full_text", [])
    scenario = entry.get("scenario", "")

    # Check if it's an auto-triage cancelled message
    if full_text and len(full_text) > 0 and full_text[0] == "Auto-triage cancelled:":
        return True

    # Check if scenario is "Failure likely outside tt-metal"
    if scenario == "Failure likely outside tt-metal":
        return True

    return False


def extract_error_message(entry):
    """Extract error message from failure_message field."""
    failure_message = entry.get("failure_message", "")

    # Return the failure message if it exists and is not empty or just dashes
    if failure_message and failure_message.strip() and failure_message.strip() not in ["---", "-"]:
        return failure_message.strip()

    return None


def extract_failing_run_url(entry):
    """Extract the URL from the failing_run field."""
    failing_run = entry.get("failing_run", "")

    if not failing_run:
        return None

    # Extract URL from parentheses: "Run #123 (description) (https://...)"
    # Pattern matches URLs in parentheses
    url_pattern = r"\(https?://[^\)]+\)"
    match = re.search(url_pattern, failing_run)

    if match:
        # Remove the parentheses
        url = match.group(0)[1:-1]  # Remove first and last character (parentheses)
        return url

    return None


def extract_workflow_and_job_from_full_text(entry):
    """Extract workflow and job names from full_text array when failing_workflow/failing_job are empty.
    
    For ND/cancelled issues, the workflow and job info is in full_text like:
    "Workflow: blackhole-post-commit"
    "Job: blackhole-multi-card-post-commit-tests (P300-viommu) / blackhole P300-viommu CCL APC test"
    
    Returns:
        Tuple of (workflow_name, job_name) - both can be None if not found
    """
    full_text = entry.get("full_text", [])
    if not full_text:
        return None, None
    
    workflow_name = None
    job_name = None
    
    for line in full_text:
        if isinstance(line, str):
            # Look for "Workflow: ..." pattern
            if line.startswith("Workflow:"):
                workflow_name = line.replace("Workflow:", "", 1).strip()
                if not workflow_name:
                    workflow_name = None
            # Look for "Job: ..." pattern
            elif line.startswith("Job:"):
                job_name = line.replace("Job:", "", 1).strip()
                if not job_name:
                    job_name = None
    
    return workflow_name, job_name


def format_timestamp(timestamp_str):
    """Convert Unix timestamp to readable format like 'January 3rd, 5:32pm, 26.43 seconds'."""
    return format_unix(timestamp_str)


def parse_unix_timestamp(timestamp_str):
    """Return the raw Slack timestamp as a float, or None if unusable."""
    try:
        return float(timestamp_str)
    except (ValueError, TypeError):
        return None


def main():
    input_file = "build_slack_export_with_threads.json"
    output_file = "all_errors.json"

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {input_file} not found", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {input_file}: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse date range filters
    start_timestamp = parse_date_to_timestamp(DATE_RANGE_START)
    end_timestamp = parse_date_to_timestamp(DATE_RANGE_END)
    
    if start_timestamp:
        print(f"Date range start: {DATE_RANGE_START}")
    if end_timestamp:
        print(f"Date range end: {DATE_RANGE_END}")

    errors = []
    skipped = 0
    skipped_date_range = 0

    for entry in data:
        # Check date range filter
        timestamp_str = entry.get("timestamp", "")
        if timestamp_str:
            try:
                entry_timestamp = float(timestamp_str)
                if start_timestamp and entry_timestamp < start_timestamp:
                    skipped_date_range += 1
                    continue
                if end_timestamp and entry_timestamp > end_timestamp:
                    skipped_date_range += 1
                    continue
            except (ValueError, TypeError):
                pass  # Keep entries with invalid timestamps
        # Filter based on EXTRACT_ALL_ERRORS flag
        if not EXTRACT_ALL_ERRORS:
            # Only process non-deterministic errors
            if not is_non_deterministic(entry):
                skipped += 1
                continue

        error_msg = extract_error_message(entry)
        if error_msg:
            # Extract failing run URL
            failing_run_url = extract_failing_run_url(entry)
            # Extract and format timestamp
            timestamp_str = entry.get("timestamp", "")
            formatted_timestamp = format_timestamp(timestamp_str) if timestamp_str else None
            unix_timestamp = parse_unix_timestamp(timestamp_str) if timestamp_str else None
            # Extract job and workflow names
            # First try the direct fields
            job_name = entry.get("failing_job", "") or None
            workflow_name = entry.get("failing_workflow", "") or None
            # If they're empty (common for ND/cancelled issues), try parsing from full_text
            if not job_name and not workflow_name:
                parsed_workflow, parsed_job = extract_workflow_and_job_from_full_text(entry)
                if parsed_workflow:
                    workflow_name = parsed_workflow
                if parsed_job:
                    job_name = parsed_job
            # Determine if this is an ND error
            is_nd = is_non_deterministic(entry)
            # Extract full_report_link (URL to the auto-triage workflow run)
            full_report_link = entry.get("full_report_link", "") or None
            # Save as list: [error_message, failing_run_url, formatted_timestamp, job_name, workflow_name, is_nd, full_report_link, unix_timestamp]
            # Use None if URL, timestamp, job, or workflow not found
            errors.append([error_msg, failing_run_url, formatted_timestamp, job_name, workflow_name, is_nd, full_report_link, unix_timestamp])
        else:
            skipped += 1

    # Write output
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(errors, f, indent=2, ensure_ascii=False)

    mode_str = "all" if EXTRACT_ALL_ERRORS else "non-deterministic"
    print(f"Extracted {len(errors)} {mode_str} error messages")
    print(f"Skipped {skipped} entries (no error message or filtered by ND flag)")
    if skipped_date_range > 0:
        print(f"Skipped {skipped_date_range} entries (outside date range)")
    print(f"Output written to {output_file}")


if __name__ == "__main__":
    main()