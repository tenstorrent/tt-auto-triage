#!/usr/bin/env python3
"""
Ensure slack_message.json is valid JSON even if the LLM wrapped it in extra tags.

Usage:
    ./sanitize_slack_message.py ./output/slack_message.json
"""

import json
import re
import sys
from pathlib import Path


def normalize_candidates(raw: str):
    candidates = []
    candidates.append(raw)

    # Remove common XML-like wrapper tags the LLM sometimes emits
    stripped = re.sub(r"<\/?content[^>]*>", "", raw)
    stripped = re.sub(r"<parameter[^>]*>", "", stripped)
    candidates.append(stripped)

    # Extract the outermost JSON object if extra text surrounds it
    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidates.append(raw[first_brace : last_brace + 1])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item.strip())
    return unique


def require_type(value, expected_type, path):
    if not isinstance(value, expected_type):
        raise ValueError(f"{path} must be of type {expected_type.__name__}")


def normalize_whitespace(value: str) -> str:
    return value.strip()


def strip_slack_mention(value: str) -> str:
    """Remove leading @ characters so Slack doesn't auto-mention."""
    return value.lstrip("@").strip()


def normalize_person_name(value: str) -> str:
    return strip_slack_mention(normalize_whitespace(value))


def require_string(value, path, allow_empty=False):
    require_type(value, str, path)
    value = value.strip()
    if not allow_empty and not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def normalize_case_value(value):
    """
    Canonicalize case labels to "1".."5".
    Assumes there is a single relevant case digit in the string
    (e.g. "4" or "Case 4").
    """
    normalized = require_string(value, "case")
    digits = re.findall(r"[1-5]", normalized)
    if not digits:
        raise ValueError("case must contain one of: 1, 2, 3, 4, 5")
    return digits[0]


def normalize_identity_fields(person: dict) -> None:
    """Trim whitespace and strip leading @ for names/logins/slack IDs."""
    if "name" in person and person["name"] is not None:
        person["name"] = normalize_person_name(str(person["name"]))
    if "login" in person and person["login"] is not None:
        person["login"] = normalize_whitespace(str(person["login"]))
    if "slack_id" in person and person["slack_id"] is not None:
        person["slack_id"] = normalize_whitespace(str(person["slack_id"]))


def extract_identity(person: dict):
    slack_id = (person.get("slack_id") or "").strip()
    name = normalize_person_name(person.get("name") or person.get("login") or "")
    return slack_id, name.lower() if name else ""


def validate_person(person, path):
    require_type(person, dict, path)
    if "name" not in person:
        raise ValueError(f"{path}.name is required")
    person["name"] = normalize_person_name(require_string(person["name"], f"{path}.name"))
    normalize_identity_fields(person)
    if "slack_id" in person and person["slack_id"] is not None:
        person["slack_id"] = require_string(person["slack_id"], f"{path}.slack_id", allow_empty=True)


def validate_person_list(entries, path):
    require_type(entries, list, path)
    for idx, entry in enumerate(entries):
        validate_person(entry, f"{path}[{idx}]")


def check_no_overlap(relevant_devs, author, approvers, path):
    """Ensure relevant_developers doesn't include author or approvers."""
    author_id, author_name = extract_identity(author)
    
    approver_ids = set()
    approver_names = set()
    if approvers:
        for approver in approvers:
            aid, aname = extract_identity(approver)
            if aid:
                approver_ids.add(aid)
            if aname:
                approver_names.add(aname)
    
    for dev in relevant_devs:
        dev_id, dev_name = extract_identity(dev)
        
        # Check if this developer matches the author
        if (author_id and dev_id and author_id == dev_id) or (author_name and dev_name and author_name == dev_name):
            raise ValueError(f"{path}.relevant_developers contains the commit author ({author_name or author_id}), which is not allowed")
        
        # Check if this developer matches any approver
        if (dev_id and dev_id in approver_ids) or (dev_name and dev_name in approver_names):
            raise ValueError(f"{path}.relevant_developers contains an approver ({dev_name or dev_id}), which is not allowed")


def validate_commit(commit, index):
    path = f"commits[{index}]"
    require_type(commit, dict, path)
    for key in ("hash",):
        if key not in commit:
            raise ValueError(f"{path}.{key} is required")
        require_string(commit[key], f"{path}.{key}")
    if "url" in commit and commit["url"] is not None:
        require_string(commit["url"], f"{path}.url")
    if "author" not in commit:
        raise ValueError(f"{path}.author is required")
    validate_person(commit["author"], f"{path}.author")
    if "approvers" in commit:
        validate_person_list(commit["approvers"], f"{path}.approvers")
    if "relevant_developers" in commit:
        validate_person_list(commit["relevant_developers"], f"{path}.relevant_developers")
        # Check for duplicates within relevant_developers
        seen_ids = set()
        seen_names = set()
        for dev in commit["relevant_developers"]:
            dev_id, dev_name = extract_identity(dev)
            if dev_id and dev_id in seen_ids:
                raise ValueError(f"{path}.relevant_developers contains duplicate entry (slack_id: {dev_id})")
            if dev_name and dev_name in seen_names:
                raise ValueError(f"{path}.relevant_developers contains duplicate entry (name: {dev_name})")
            if dev_id:
                seen_ids.add(dev_id)
            if dev_name:
                seen_names.add(dev_name)
        # Ensure relevant_developers doesn't include author or approvers
        check_no_overlap(
            commit["relevant_developers"],
            commit["author"],
            commit.get("approvers", []),
            path
        )
    if "relevant_files" in commit:
        require_type(commit["relevant_files"], list, f"{path}.relevant_files")
        for file_idx, file_entry in enumerate(commit["relevant_files"]):
            require_string(file_entry, f"{path}.relevant_files[{file_idx}]")


def validate_payload(payload):
    require_type(payload, dict, "root")
    if "case" not in payload:
        raise ValueError("Field 'case' is required at the top level")
    payload["case"] = normalize_case_value(payload["case"])

    for key in ("scenario", "failure_message", "slack_message"):
        if key not in payload:
            raise ValueError(f"Field '{key}' is required at the top level")
        require_string(payload[key], key)
    for key in ("failing_run_url", "failing_run_label"):
        if key in payload:
            require_string(payload[key], key)
    if "commits" not in payload:
        raise ValueError("Field 'commits' is required at the top level (can be an empty array)")
    require_type(payload["commits"], list, "commits")
    
    # Collect all authors and approvers across all commits to check for duplicates in relevant_developers
    all_authors_approvers = set()
    for commit in payload["commits"]:
        author = commit.get("author", {})
        author_id, author_name = extract_identity(author)
        if author_id:
            all_authors_approvers.add(("id", author_id))
        if author_name:
            all_authors_approvers.add(("name", author_name))
        
        approvers = commit.get("approvers", [])
        for approver in approvers:
            aid, aname = extract_identity(approver)
            if aid:
                all_authors_approvers.add(("id", aid))
            if aname:
                all_authors_approvers.add(("name", aname.lower()))
    
    for idx, commit in enumerate(payload["commits"]):
        validate_commit(commit, idx)
    
    # Validate top-level relevant_developers don't overlap with any commit authors/approvers
    if "relevant_developers" in payload:
        validate_person_list(payload["relevant_developers"], "relevant_developers")
        # Check for duplicates within top-level relevant_developers
        seen_ids = set()
        seen_names = set()
        for dev in payload["relevant_developers"]:
            dev_id, dev_name = extract_identity(dev)
            if dev_id and dev_id in seen_ids:
                raise ValueError(f"Top-level relevant_developers contains duplicate entry (slack_id: {dev_id})")
            if dev_name and dev_name in seen_names:
                raise ValueError(f"Top-level relevant_developers contains duplicate entry (name: {dev_name})")
            if dev_id:
                seen_ids.add(dev_id)
            if dev_name:
                seen_names.add(dev_name)
        if payload["commits"]:
            # Check top-level relevant_developers against all commit authors/approvers
            for dev in payload["relevant_developers"]:
                dev_id, dev_name = extract_identity(dev)
                if (dev_id and ("id", dev_id) in all_authors_approvers) or (dev_name and ("name", dev_name.lower()) in all_authors_approvers):
                    raise ValueError(f"Top-level relevant_developers contains a commit author or approver ({dev_name or dev_id}), which is not allowed")
    
    if "relevant_files" in payload:
        require_type(payload["relevant_files"], list, "relevant_files")
        for idx, entry in enumerate(payload["relevant_files"]):
            require_string(entry, f"relevant_files[{idx}]")
    if "notes" in payload and payload["notes"] is not None:
        require_string(payload["notes"], "notes", allow_empty=True)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: sanitize_slack_message.py <slack_message.json>", file=sys.stderr)
        return 2

    target = Path(sys.argv[1])
    if not target.exists():
        print(f"Error: {target} does not exist.", file=sys.stderr)
        return 1

    raw = target.read_text(encoding="utf-8")
    candidates = normalize_candidates(raw)

    last_error = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            validate_payload(parsed)
            target.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"Sanitized Slack message written to {target}")
            return 0
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        except ValueError as exc:
            last_error = exc
            continue

    print(f"Failed to sanitize {target}: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

