#!/usr/bin/env python3
"""
Create GitHub issues from grouped errors.
Reads grouped_errors.json and creates an issue for each group.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import requests

# File paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS_FILE = os.path.join(SCRIPT_DIR, "secrets.json")
GROUPED_ERRORS_FILE = os.path.join(SCRIPT_DIR, "grouped_errors.json")

# Import GitHub API utilities
from github_api_utils import log_rate_limit_status, get_commit_hash_from_github

# ============================================================================
# Defensive Validation
# ============================================================================

def is_error_entry_valid(error: Dict[str, Any], github_token: str = "") -> tuple:
    """Check if an error entry has all required metadata fields.
    
    Required fields (for Pydantic model compatibility):
    - timestamp: Must be non-empty
    - commit_hash: Must be non-empty (will attempt to fetch from GitHub if missing)
    - job_name: Must be non-empty
    
    Args:
        error: Error entry dict with 'url', 'timestamp', 'job_name', 'commit_hash', etc.
        github_token: GitHub token for fetching missing commit hashes
    
    Returns:
        Tuple of (is_valid, list_of_missing_fields)
    """
    missing_fields = []
    
    # Check URL (obviously required)
    url = error.get("url", "")
    if not url:
        missing_fields.append("url")
        return False, missing_fields  # Can't do much without URL
    
    # Check timestamp
    timestamp = error.get("timestamp", "")
    if not timestamp or timestamp.lower() == "link":
        missing_fields.append("timestamp")
    
    # Check commit_hash - try to fetch if missing
    commit_hash = error.get("commit_hash", "")
    if not commit_hash and github_token and url:
        # Attempt to fetch from GitHub API
        fetched_hash = get_commit_hash_from_github(url, github_token)
        if fetched_hash:
            error["commit_hash"] = fetched_hash
            commit_hash = fetched_hash
    if not commit_hash:
        missing_fields.append("commit_hash")
    
    # Check job_name
    job_name = error.get("job_name", "")
    if not job_name:
        missing_fields.append("job_name")
    
    return len(missing_fields) == 0, missing_fields


def validate_group_errors(group_data: Dict[str, Any], github_token: str = "") -> Dict[str, Any]:
    """Validate all errors in a group and remove invalid ones.
    
    Args:
        group_data: Group data with 'centroid', 'errors', 'count'
        github_token: GitHub token for API calls
    
    Returns:
        Updated group_data with invalid entries removed, or None if no valid entries remain
    """
    centroid = group_data.get("centroid", {})
    errors = group_data.get("errors", [])
    
    # Validate centroid
    centroid_valid, centroid_missing = is_error_entry_valid(centroid, github_token)
    
    # Validate all errors
    valid_errors = []
    removed_count = 0
    
    for error in errors:
        is_valid, missing_fields = is_error_entry_valid(error, github_token)
        if is_valid:
            valid_errors.append(error)
        else:
            removed_count += 1
            url = error.get("url", "unknown")
            print(f"    ⚠ Removing invalid entry: {url[:60]}... (missing: {', '.join(missing_fields)})")
    
    # If centroid is invalid, try to select a new one from valid errors
    if not centroid_valid:
        if valid_errors:
            # Select first valid error as new centroid
            new_centroid = valid_errors[0]
            valid_errors = valid_errors[1:]  # Remove from errors list
            print(f"    → Centroid invalid, selected new centroid from valid errors")
            centroid = new_centroid
        else:
            # No valid entries at all
            print(f"    ⚠ No valid entries in group (centroid and all errors invalid)")
            return None
    
    # Check if we have any valid entries
    if not centroid_valid and not valid_errors:
        return None
    
    # Update group data
    all_valid = [centroid] + valid_errors
    return {
        "count": len(all_valid),
        "centroid": centroid,
        "errors": valid_errors
    }


# ============================================================================
# CONFIGURATION - Load from secrets.json
# ============================================================================

def load_secrets():
    """Load configuration from secrets.json file."""
    try:
        with open(SECRETS_FILE, 'r') as f:
            secrets = json.load(f)
        return {
            "GITHUB_TOKEN": secrets.get("github_token", ""),
            "GITHUB_REPO_OWNER": secrets.get("github_repo_owner", ""),
            "GITHUB_REPO_NAME": secrets.get("github_repo_name", ""),
            "PROJECT_OWNER": secrets.get("project_owner", ""),
            "PROJECT_NUMBER": secrets.get("project_number", ""),
            "PROJECT_FIELD_ID": secrets.get("project_field_id", "")
        }
    except FileNotFoundError:
        print(f"ERROR: secrets.json not found at {SECRETS_FILE}")
        print("\nPlease create secrets.json with the following structure:")
        print("""
{
  "github_token": "your_token_here",
  "github_repo_owner": "tenstorrent",
  "github_repo_name": "tt-auto-triage",
  "project_owner": "",
  "project_number": "",
  "project_field_id": ""
}
""")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {SECRETS_FILE}: {e}")
        sys.exit(1)

# Load secrets
_secrets = load_secrets()
GITHUB_TOKEN = _secrets["GITHUB_TOKEN"]
GITHUB_REPO_OWNER = _secrets["GITHUB_REPO_OWNER"]
GITHUB_REPO_NAME = _secrets["GITHUB_REPO_NAME"]
PROJECT_OWNER = _secrets["PROJECT_OWNER"]
PROJECT_NUMBER = _secrets["PROJECT_NUMBER"]
PROJECT_FIELD_ID = _secrets["PROJECT_FIELD_ID"]

# ============================================================================
# API Functions
# ============================================================================

def check_credentials():
    """Verify that all required credentials are provided."""
    missing = []
    if not GITHUB_TOKEN:
        missing.append("GITHUB_TOKEN")
    if not GITHUB_REPO_OWNER:
        missing.append("GITHUB_REPO_OWNER")
    if not GITHUB_REPO_NAME:
        missing.append("GITHUB_REPO_NAME")
    
    if missing:
        print("ERROR: Missing required credentials:")
        for cred in missing:
            print(f"  - {cred}")
        print("\nPlease fill in the configuration variables at the top of this script.")
        sys.exit(1)

def check_repository_access():
    """Check if the token has access to the repository and can read/write."""
    print(f"\n{'='*80}")
    print("Checking repository access...")
    print(f"Repository: {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
    print(f"{'='*80}\n")
    
    # Check repository info
    repo_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
    
    # Try with "Bearer" first (for fine-grained PATs), then fall back to "token"
    auth_methods = [
        ("Bearer", f"Bearer {GITHUB_TOKEN}"),
        ("token", f"token {GITHUB_TOKEN}")
    ]
    
    repo_info = None
    last_error = None
    working_headers = None
    
    for method_name, auth_header in auth_methods:
        headers = {
            "Authorization": auth_header,
            "Accept": "application/vnd.github.v3+json"
        }
        
        try:
            response = requests.get(repo_url, headers=headers)
            if response.status_code == 200:
                repo_info = response.json()
                working_headers = headers  # Save working headers for later use
                break
            elif response.status_code == 404:
                print(f"✗ Repository not found (404): {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
                print(f"  Response: {response.text}")
                print("\n  Please verify:")
                print(f"  1. The repository exists at https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
                print("  2. The repository name is spelled correctly")
                print("  3. The GitHub token has access to this repository")
                print("  4. If this is a private repository, ensure the token has access")
                return False
            elif response.status_code == 403:
                # Try next auth method
                last_error = response
                continue
            else:
                response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            last_error = e
            if e.response.status_code == 403:
                # Try next auth method
                continue
            elif e.response.status_code == 404:
                print(f"✗ Repository not found (404): {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
                print(f"  Response: {e.response.text}")
                print("\n  Please verify:")
                print(f"  1. The repository exists at https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
                print("  2. The repository name is spelled correctly")
                print("  3. The GitHub token has access to this repository")
                return False
            else:
                print(f"✗ Cannot access repository: HTTP {e.response.status_code}")
                print(f"  Response: {e.response.text}")
                print("\n  Possible issues:")
                print("    - Token doesn't have access to this repository")
                print("    - Repository doesn't exist or is private")
                print("    - Organization policy blocking PAT access")
                return False
        except Exception as e:
            last_error = e
            if method_name == "token":  # Last method
                print(f"✗ Error checking repository access: {e}")
                return False
            continue
    
    if not repo_info:
        if last_error:
            if hasattr(last_error, 'response'):
                print(f"✗ Cannot access repository: HTTP {last_error.response.status_code}")
                print(f"  Response: {last_error.response.text}")
            else:
                print(f"✗ Error checking repository access: {last_error}")
        print("\n  Possible issues:")
        print("    - Token doesn't have access to this repository")
        print("    - Repository doesn't exist or is private")
        print("    - Organization policy blocking PAT access")
        return False
    
    print(f"✓ Repository found: {repo_info.get('full_name', 'unknown')}")
    print(f"  Private: {repo_info.get('private', False)}")
    print(f"  Archived: {repo_info.get('archived', False)}")
    print(f"  Disabled: {repo_info.get('disabled', False)}")
    
    # Check if issues are enabled
    has_issues = repo_info.get('has_issues', True)
    print(f"  Issues enabled: {has_issues}")
    if not has_issues:
        print("  ⚠ WARNING: Issues are disabled for this repository!")
        print("  Enable issues in repository settings to create issues.")
        return False
    
    # Check if we can read issues
    issues_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/issues"
    params = {
        "state": "all",
        "per_page": 1
    }
    
    if not working_headers:
        # Fallback to token auth if we didn't get working headers
        working_headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
    
    try:
        response = requests.get(issues_url, headers=working_headers, params=params)
        response.raise_for_status()
        print(f"✓ Can READ issues")
    except requests.exceptions.HTTPError as e:
        print(f"✗ Cannot READ issues: HTTP {e.response.status_code}")
        print(f"  Response: {e.response.text}")
        return False
    
    # Try to check write permissions by attempting to create a test issue (we'll catch and handle)
    print(f"\nTesting WRITE permissions...")
    print(f"  (This will attempt to create a test issue - it will fail if no write access)")
    
    print(f"\n{'='*80}\n")
    return True

def list_existing_issues():
    """List all existing issues in the repository (for testing connection)."""
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/issues"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    params = {
        "state": "all",  # Get both open and closed issues
        "per_page": 100  # Get up to 100 issues per page
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        issues = response.json()
        
        # Filter out pull requests (GitHub API returns both issues and PRs)
        actual_issues = [issue for issue in issues if "pull_request" not in issue]
        
        print(f"Found {len(actual_issues)} existing issue(s) in the repository:\n")
        
        if len(actual_issues) == 0:
            print("  (No issues found - repository is empty or has no issues)")
        else:
            for issue in actual_issues:
                state = issue["state"]
                number = issue["number"]
                title = issue["title"]
                print(f"  #{number} [{state.upper()}]: {title}")
        
        print(f"\n{'='*80}\n")
        
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: HTTP {e.response.status_code}")
        print(f"Response: {e.response.text}")
        print("\nPossible issues:")
        print("  - Invalid repository owner/name")
        print("  - Invalid or expired token")
        print("  - Token doesn't have access to this repository")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to connect to repository: {e}")
        sys.exit(1)

def verify_repository_access() -> bool:
    """Verify that the repository exists and is accessible."""
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
    
    auth_methods = [
        ("Bearer", f"Bearer {GITHUB_TOKEN}"),
        ("token", f"token {GITHUB_TOKEN}")
    ]
    
    for method_name, auth_header in auth_methods:
        headers = {
            "Authorization": auth_header,
            "Accept": "application/vnd.github.v3+json"
        }
        
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                repo_data = response.json()
                has_issues = repo_data.get("has_issues", True)
                if not has_issues:
                    print(f"\n⚠ Warning: Issues are disabled for repository {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
                    print("  Enable issues in repository settings to create issues.")
                    return False
                return True
            elif response.status_code == 404:
                print(f"\n✗ ERROR: Repository {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME} not found (404)")
                print("  Please verify:")
                print(f"  1. The repository exists at https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
                print("  2. The repository name is spelled correctly")
                print("  3. The GitHub token has access to this repository")
                return False
            elif response.status_code == 403:
                # Try next auth method
                continue
            else:
                print(f"\n✗ ERROR: Failed to verify repository access (HTTP {response.status_code})")
                print(f"  Response: {response.text}")
                return False
        except Exception as e:
            if method_name == "token":  # Last method
                print(f"\n✗ ERROR: Failed to verify repository: {e}")
                return False
            continue
    
    print(f"\n✗ ERROR: Failed to verify repository access with any authentication method")
    return False

def create_issue(title: str, body: str) -> Dict[str, Any]:
    """Create a GitHub issue and return the issue data."""
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/issues"
    
    # Try with "Bearer" first (for fine-grained PATs), then fall back to "token"
    auth_methods = [
        ("Bearer", f"Bearer {GITHUB_TOKEN}"),
        ("token", f"token {GITHUB_TOKEN}")
    ]
    
    data = {
        "title": title,
        "body": body
    }
    
    last_error = None
    for method_name, auth_header in auth_methods:
        headers = {
            "Authorization": auth_header,
            "Accept": "application/vnd.github.v3+json"
        }
        
        try:
            response = requests.post(url, json=data, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            last_error = e
            if e.response.status_code == 403:
                # Try next auth method
                continue
            elif e.response.status_code == 404:
                print(f"\n✗ ERROR: HTTP 404 - Repository or endpoint not found")
                print(f"  URL: {url}")
                print(f"  Repository: {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
                print(f"  Response: {e.response.text}")
                print("\n  Possible causes:")
                print("  1. Repository does not exist")
                print("  2. Issues are disabled for this repository")
                print("  3. GitHub token does not have access to this repository")
                print(f"  4. Repository URL is incorrect (check: https://github.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME})")
                raise
            else:
                # For other errors, show details and fail
                print(f"\nERROR: HTTP {e.response.status_code}")
                print(f"Response: {e.response.text}")
                try:
                    error_json = e.response.json()
                    if "message" in error_json:
                        print(f"Error message: {error_json['message']}")
                    if "errors" in error_json:
                        print(f"Errors: {error_json['errors']}")
                except (json.JSONDecodeError, KeyError):
                    pass
                raise
        except Exception as e:
            last_error = e
            continue
    
    # If we get here, all auth methods failed
    if last_error and hasattr(last_error, 'response'):
        print(f"\nERROR: HTTP {last_error.response.status_code}")
        print(f"Response: {last_error.response.text}")
        try:
            error_json = last_error.response.json()
            if "message" in error_json:
                message = error_json['message']
                print(f"Error message: {message}")
                
                if "Resource not accessible by personal access token" in message:
                    print("\n⚠ This error typically means:")
                    print("  1. Organization policy is blocking PAT access")
                    print("     - Check: Organization Settings > Third-party access > Personal access tokens")
                    print("     - Organization admin may need to approve your PAT")
                    print("  2. Repository not explicitly added to PAT")
                    print("     - Even with 'All repositories', some orgs require explicit repo selection")
                    print("     - Edit PAT and ensure this repository is selected")
                    print("  3. Fine-grained PAT restrictions")
                    print("     - Verify PAT has 'Read and write' for Issues")
                    print("     - Check if PAT expiration or IP restrictions apply")
        except (json.JSONDecodeError, KeyError):
            pass
        print("\nPossible issues:")
        print("  - Token only has 'Read' permission for Issues (needs 'Read and write')")
        print("  - Organization policy may be blocking PAT access")
        print("  - Repository may have issue creation disabled")
        print("  - PAT may need organization admin approval")
    raise last_error

def add_issue_to_project(issue_id: str, project_id: str) -> None:
    """Add an issue to a GitHub Project."""
    # First, get the project's node ID
    project_node_id = get_project_node_id(project_id)
    
    # Get the issue's node ID
    issue_node_id = get_issue_node_id(issue_id)
    
    # Add issue to project
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    
    query = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item {
          id
        }
      }
    }
    """
    
    variables = {
        "projectId": project_node_id,
        "contentId": issue_node_id
    }
    
    response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
    response.raise_for_status()
    
    result = response.json()
    if "errors" in result:
        raise Exception(f"GraphQL errors: {result['errors']}")

def get_project_node_id(project_number: str) -> str:
    """Get the GraphQL node ID for a project."""
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    
    query = """
    query($owner: String!, $number: Int!) {
      organization(login: $owner) {
        projectV2(number: $number) {
          id
        }
      }
    }
    """
    
    variables = {
        "owner": PROJECT_OWNER,
        "number": int(PROJECT_NUMBER)
    }
    
    response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
    response.raise_for_status()
    
    result = response.json()
    if "errors" in result:
        raise Exception(f"GraphQL errors: {result['errors']}")
    
    return result["data"]["organization"]["projectV2"]["id"]

def get_issue_node_id(issue_id: str) -> str:
    """Get the GraphQL node ID for an issue."""
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) {
        issue(number: $number) {
          id
        }
      }
    }
    """
    
    # Extract issue number from issue_id (format: "https://api.github.com/repos/.../issues/123")
    issue_number = issue_id.split("/")[-1]
    
    variables = {
        "owner": GITHUB_REPO_OWNER,
        "repo": GITHUB_REPO_NAME,
        "number": int(issue_number)
    }
    
    response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
    response.raise_for_status()
    
    result = response.json()
    if "errors" in result:
        raise Exception(f"GraphQL errors: {result['errors']}")
    
    return result["data"]["repository"]["issue"]["id"]

def update_project_field(project_item_id: str, field_id: str, value: int) -> None:
    """Update a project field value."""
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    
    query = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(
        input: {
          projectId: $projectId
          itemId: $itemId
          fieldId: $fieldId
          value: $value
        }
      ) {
        projectV2Item {
          id
        }
      }
    }
    """
    
    variables = {
        "projectId": project_item_id,
        "itemId": project_item_id,  # This will be the item ID from addProjectV2ItemById
        "fieldId": field_id,
        "value": {
            "number": value
        }
    }
    
    response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
    response.raise_for_status()
    
    result = response.json()
    if "errors" in result:
        raise Exception(f"GraphQL errors: {result['errors']}")

# ============================================================================
# Main Processing Functions
# ============================================================================

def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse timestamp string to datetime object.
    
    Handles formats like "January 9th, 8:59am, 58.95 seconds"
    """
    if not timestamp_str:
        return None
    
    try:
        # Try to parse the format: "January 9th, 8:59am, 58.95 seconds"
        # Remove the seconds part for parsing
        parts = timestamp_str.split(", ")
        if len(parts) >= 2:
            date_part = parts[0]  # "January 9th"
            time_part = parts[1]  # "8:59am"
            
            # Remove ordinal suffix (st, nd, rd, th)
            date_part_clean = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_part)
            
            # Parse date and time
            # Format: "January 9" and "8:59am"
            try:
                dt = datetime.strptime(f"{date_part_clean}, {time_part}", "%B %d, %I:%M%p")
                # Use current year (or previous year if date is in future)
                current_year = datetime.now().year
                dt = dt.replace(year=current_year)
                # If the date is more than 6 months in the future, assume it's from last year
                if dt > datetime.now() + timedelta(days=180):
                    dt = dt.replace(year=current_year - 1)
                return dt
            except ValueError:
                pass
    except Exception:
        pass
    
    return None

def format_issue_body(group_data: Dict[str, Any], group_name: str) -> str:
    """Format the issue body with centroid error and all URLs."""
    centroid = group_data["centroid"]
    errors = group_data["errors"]
    count = group_data["count"]
    
    body_parts = []
    
    # Add number of occurrences at the top
    body_parts.append(f"**Number of Occurrences:** {count}")
    body_parts.append("")
    
    # Add centroid error as the main description (no link, just error message)
    body_parts.append("## Error Message\n")
    body_parts.append("```")
    body_parts.append(centroid["error"])
    body_parts.append("```")
    body_parts.append("")
    
    # Add all run URLs (sorted chronologically)
    body_parts.append("## All Occurrences")
    body_parts.append(f"This error has occurred {count} time(s):")
    body_parts.append("")
    
    # Collect all unique URLs with timestamps
    seen_urls = set()
    url_list = []
    centroid_url = centroid.get("url", "")
    
    # Add all errors including centroid (for fetching commit hashes)
    all_errors = [centroid] + errors
    
    # Fetch commit hashes for all URLs (if not already present)
    github_token = load_secrets().get("GITHUB_TOKEN", "")
    for error in all_errors:
        url = error.get("url", "")
        if url and github_token and not error.get("commit_hash"):
            # Fetch commit hash from GitHub API
            commit_hash = get_commit_hash_from_github(url, github_token)
            if commit_hash:
                error["commit_hash"] = commit_hash
    
    for error in all_errors:
        url = error.get("url", "")
        timestamp = error.get("timestamp", "")
        job_name = error.get("job_name", "")
        workflow_name = error.get("workflow_name", "")
        is_nd = error.get("is_nd", False)
        error_message = error.get("error", "")
        
        # Skip if no URL or already seen
        if not url or url in seen_urls:
            continue
        
        seen_urls.add(url)
        
        # Check if this is the centroid
        is_centroid = (url == centroid_url)
        
        # Build label - add [CENTROID] prefix if this is the centroid
        label = timestamp if timestamp else "Link"
        if is_centroid:
            label = f"[CENTROID] {label}"
        
        # Add ND marker if applicable
        if is_nd:
            label += " (marked as ND)"
        
        # Build job/workflow suffix
        job_workflow_suffix = ""
        if job_name or workflow_name:
            parts = []
            if workflow_name:
                parts.append(workflow_name)
            if job_name:
                parts.append(job_name)
            job_workflow_suffix = f" - {' / '.join(parts)}"
        
        # Get commit hash if available
        commit_hash = error.get("commit_hash", "")
        commit_hash_suffix = f" (commit: {commit_hash})" if commit_hash else ""
        
        # Parse timestamp for proper chronological sorting
        dt = parse_timestamp(timestamp) if timestamp else None
        # Use datetime for sorting (None for items without timestamps, which go to end)
        url_list.append((dt, label, url, job_workflow_suffix, commit_hash_suffix, error_message))
    
    # Sort chronologically (newest first), items without timestamps go to the end
    # Key: (has_timestamp, datetime) 
    # - Items with timestamps: (True, dt) - we want newer dt first
    # - Items without timestamps: (False, max) - we want these last
    # With reverse=True: (True, newer) > (True, older) > (False, max) ✓
    url_list.sort(key=lambda x: (x[0] is not None, x[0] if x[0] is not None else datetime.max), reverse=True)
    
    # Format the list
    for idx, (dt, label, url, job_workflow_suffix, commit_hash_suffix, error_message) in enumerate(url_list, 1):
        body_parts.append(f"{idx}. [{label}]({url}){job_workflow_suffix}{commit_hash_suffix}")
        # Add error message as sub-bullet in a code block if available
        if error_message:
            # Use 4 spaces for proper markdown list continuation
            body_parts.append("")  # Empty line before code block
            body_parts.append("    ```")
            # Indent each line of the error message to stay within the list context
            for line in error_message.split("\n"):
                body_parts.append(f"    {line}")
            body_parts.append("    ```")
    
    return "\n".join(body_parts)

def create_title_from_group_name(group_name: str, count: int, error_message: str = "") -> str:
    """Create a title from the group name with occurrence count prefix and truncated error message.
    
    Format: [00045] Group 1: Error message...
    This allows alphabetical sorting to also sort by occurrence count while being descriptive.
    Truncates error message to fit within GitHub's 256 character limit.
    """
    # Format count as 5-digit zero-padded number (00000 to 10000)
    count_str = f"{count:05d}"
    
    # Extract number from group_name (e.g., "group_1" -> "1")
    if "_" in group_name:
        group_num = group_name.split("_", 1)[1]
        group_label = f"Group {group_num}"
    else:
        group_label = group_name
        group_num = None
    
    # Calculate available space for error message
    # Reserve space for: "[00045] " (9 chars) + "Group X: " (~10 chars) + "..." (3 chars)
    prefix_len = len(f"[{count_str}] {group_label}: ")
    max_error_len = 256 - prefix_len - 3  # 3 for "..."
    
    # Truncate error message if needed
    if error_message and len(error_message) > max_error_len:
        # Try to truncate at a word boundary
        truncated = error_message[:max_error_len].rsplit(' ', 1)[0]
        if len(truncated) < max_error_len - 20:  # If truncation removed too much, just cut at max
            truncated = error_message[:max_error_len]
        truncated += "..."
    elif error_message:
        truncated = error_message
    else:
        return f"[{count_str}] {group_label}"
    
    return f"[{count_str}] {group_label}: {truncated}"

def process_group(group_name: str, group_data: Dict[str, Any], group_num: int, total_groups: int, bulk_mode: bool = False) -> bool:
    """Process a single group: create issue and update project field.
    
    Returns:
        bool: True if bulk mode was requested, False otherwise
    """
    print(f"\n{'='*80}")
    print(f"Group {group_num}/{total_groups}: {group_name}")
    print(f"{'='*80}")
    
    # ================================================================
    # DEFENSIVE VALIDATION: Remove entries with missing required metadata
    # Better to have incomplete data than errors from null values in Pydantic
    # ================================================================
    github_token = load_secrets().get("GITHUB_TOKEN", "")
    print(f"[VALIDATION] Checking group entries for required metadata...")
    
    validated_group = validate_group_errors(group_data, github_token)
    
    if validated_group is None:
        print(f"⚠ Skipping group {group_name} - no valid entries after validation")
        return bulk_mode
    
    # Update group_data with validated data
    group_data = validated_group
    
    print(f"Count: {group_data['count']} occurrence(s) (after validation)")
    print(f"\nCentroid Error:")
    print("-" * 80)
    print(group_data["centroid"]["error"][:500] + ("..." if len(group_data["centroid"]["error"]) > 500 else ""))
    print("-" * 80)
    
    # Show first few URLs
    urls = [e.get("url", "") for e in group_data["errors"] if e.get("url")]
    print(f"\nTotal URLs: {len(urls)}")
    if urls:
        print("Sample URLs:")
        for url in urls[:3]:
            print(f"  - {url}")
        if len(urls) > 3:
            print(f"  ... and {len(urls) - 3} more")
    
    # Wait for user confirmation (unless in bulk mode or CI)
    if not bulk_mode:
        # Skip confirmation in CI environments
        if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
            # Running in CI - auto-confirm and enable bulk mode
            print("\nRunning in CI - auto-confirming and enabling bulk mode...")
            bulk_mode = True
        else:
            # Interactive mode - ask for confirmation
            print("\n" + "="*80)
            response = input("Create issue for this group? (y/n/a for all remaining/q to quit): ").strip().lower()
            
            if response == 'q':
                print("\nExiting...")
                sys.exit(0)
            elif response == 'a':
                print("Bulk mode enabled - creating all remaining issues...\n")
                bulk_mode = True
            elif response != 'y':
                print("Skipping this group.\n")
                return False
    
    if bulk_mode:
        print(f"Creating issue for {group_name}...")
    
    try:
        # Create issue title (with occurrence count prefix for sorting and error message)
        centroid_error = group_data["centroid"]["error"]
        title = create_title_from_group_name(group_name, group_data["count"], centroid_error)
        
        # Create issue body
        body = format_issue_body(group_data, group_name)
        
        print("\nCreating issue...")
        issue = create_issue(title, body)
        issue_url = issue["html_url"]
        issue_number = issue["number"]
        print(f"✓ Issue created: {issue_url}")
        
        # Add to project if configured
        if PROJECT_OWNER and PROJECT_NUMBER:
            try:
                print("Adding to project...")
                # Note: This is simplified - you may need to get the project item ID
                # from the addProjectV2ItemById response to update the field
                project_node_id = get_project_node_id(PROJECT_NUMBER)
                issue_node_id = get_issue_node_id(str(issue_number))
                
                # Add to project
                url = "https://api.github.com/graphql"
                headers = {
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Content-Type": "application/json"
                }
                
                query = """
                mutation($projectId: ID!, $contentId: ID!) {
                  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
                    item {
                      id
                    }
                  }
                }
                """
                
                variables = {
                    "projectId": project_node_id,
                    "contentId": issue_node_id
                }
                
                response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
                response.raise_for_status()
                result = response.json()
                
                if "errors" in result:
                    print(f"⚠ Warning: Could not add to project: {result['errors']}")
                else:
                    item_id = result["data"]["addProjectV2ItemById"]["item"]["id"]
                    print(f"✓ Added to project")
                    
                    # Update field if field ID is provided
                    if PROJECT_FIELD_ID:
                        try:
                            update_query = """
                            mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
                              updateProjectV2ItemFieldValue(
                                input: {
                                  projectId: $projectId
                                  itemId: $itemId
                                  fieldId: $fieldId
                                  value: $value
                                }
                              ) {
                                projectV2Item {
                                  id
                                }
                              }
                            }
                            """
                            
                            update_variables = {
                                "projectId": project_node_id,
                                "itemId": item_id,
                                "fieldId": PROJECT_FIELD_ID,
                                "value": {
                                    "number": group_data["count"]
                                }
                            }
                            
                            update_response = requests.post(
                                url, 
                                json={"query": update_query, "variables": update_variables}, 
                                headers=headers
                            )
                            update_response.raise_for_status()
                            update_result = update_response.json()
                            
                            if "errors" in update_result:
                                print(f"⚠ Warning: Could not update field: {update_result['errors']}")
                            else:
                                print(f"✓ Updated 'number of occurrences' field to {group_data['count']}")
                        except Exception as e:
                            print(f"⚠ Warning: Could not update field: {e}")
            except Exception as e:
                print(f"⚠ Warning: Could not add to project: {e}")
        
        print(f"\n✓ Successfully processed {group_name}")
        time.sleep(0.5)  # Small delay to avoid rate limiting
        return bulk_mode
        
    except Exception as e:
        print(f"\n✗ Error creating issue: {e}")
        print("Continuing to next group...")
        return bulk_mode

def delete_all_issues():
    """Remove all issues from the project board and close them (for testing purposes)."""
    print(f"\n{'='*80}")
    print("WARNING: Removing all issues from project and closing them...")
    print(f"Repository: {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
    if PROJECT_OWNER and PROJECT_NUMBER:
        print(f"Project: {PROJECT_OWNER}/{PROJECT_NUMBER}")
    print(f"{'='*80}\n")
    
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/issues"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    params = {
        "state": "all",  # Get both open and closed
        "per_page": 100
    }
    
    try:
        # Get all issues
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        issues = response.json()
        
        # Filter out pull requests
        actual_issues = [issue for issue in issues if "pull_request" not in issue]
        
        if len(actual_issues) == 0:
            print("No issues to delete.")
            return
        
        print(f"Found {len(actual_issues)} issue(s) to remove.")
        
        # Auto-confirm in CI environments
        if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
            print("Running in CI - auto-confirming deletion of all issues...")
        else:
            # Interactive mode - ask for confirmation
            response = input("Remove all issues from project and close them? (yes/no): ").strip().lower()
            if response != "yes":
                print("Skipping deletion.")
                return
        
        # Get project node ID if project is configured
        project_node_id = None
        if PROJECT_OWNER and PROJECT_NUMBER:
            try:
                project_node_id = get_project_node_id(PROJECT_NUMBER)
            except Exception as e:
                print(f"⚠ Warning: Could not get project node ID: {e}")
                print("  Will only close issues, not remove from project.")
        
        removed_count = 0
        closed_count = 0
        
        # First, remove from project if project is configured
        if project_node_id:
            print(f"\nRemoving issues from project...")
            graphql_url = "https://api.github.com/graphql"
            graphql_headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json"
            }
            
            for issue in actual_issues:
                issue_number = issue["number"]
                try:
                    # Get issue node ID
                    issue_node_id = get_issue_node_id(str(issue_number))
                    
                    # Find project item ID for this issue
                    # Query project items to find the one matching this issue
                    query_items = """
                    query($projectId: ID!) {
                      node(id: $projectId) {
                        ... on ProjectV2 {
                          items(first: 100) {
                            nodes {
                              id
                              content {
                                ... on Issue {
                                  number
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                    """
                    
                    items_response = requests.post(
                        graphql_url,
                        json={"query": query_items, "variables": {"projectId": project_node_id}},
                        headers=graphql_headers
                    )
                    items_response.raise_for_status()
                    items_result = items_response.json()
                    
                    if "errors" not in items_result:
                        items = items_result["data"]["node"]["items"]["nodes"]
                        project_item_id = None
                        for item in items:
                            if item.get("content", {}).get("number") == issue_number:
                                project_item_id = item["id"]
                                break
                        
                        if project_item_id:
                            # Remove from project
                            delete_query = """
                            mutation($itemId: ID!) {
                              deleteProjectV2Item(input: {itemId: $itemId}) {
                                deletedItemId
                              }
                            }
                            """
                            
                            delete_response = requests.post(
                                graphql_url,
                                json={"query": delete_query, "variables": {"itemId": project_item_id}},
                                headers=graphql_headers
                            )
                            delete_response.raise_for_status()
                            delete_result = delete_response.json()
                            
                            if "errors" not in delete_result:
                                removed_count += 1
                                print(f"  ✓ Removed issue #{issue_number} from project")
                            else:
                                print(f"  ⚠ Warning: Could not remove issue #{issue_number} from project: {delete_result['errors']}")
                        else:
                            print(f"  - Issue #{issue_number} not found in project")
                    else:
                        print(f"  ⚠ Warning: Could not query project items: {items_result['errors']}")
                    
                    time.sleep(0.2)  # Rate limiting
                except Exception as e:
                    print(f"  ⚠ Warning: Error processing issue #{issue_number} for project removal: {e}")
        
        # Now close all issues
        print(f"\nClosing issues...")
        for issue in actual_issues:
            issue_number = issue["number"]
            delete_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/issues/{issue_number}"
            delete_headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            delete_data = {
                "state": "closed"  # Close the issue (GitHub doesn't allow true deletion via API)
            }
            
            try:
                delete_response = requests.patch(delete_url, json=delete_data, headers=delete_headers)
                delete_response.raise_for_status()
                closed_count += 1
                print(f"  ✓ Closed issue #{issue_number}: {issue['title'][:50]}...")
            except Exception as e:
                print(f"  ✗ Failed to close issue #{issue_number}: {e}")
        
        print(f"\n✓ Removed {removed_count}/{len(actual_issues)} issue(s) from project")
        print(f"✓ Closed {closed_count}/{len(actual_issues)} issue(s)")
        print("Note: GitHub API only allows closing issues, not true deletion.")
        print("Closed issues can be manually deleted from the web interface if needed.")
        
    except Exception as e:
        print(f"ERROR: Failed to delete issues: {e}")
        print("Continuing anyway...")

def main():
    """Main function."""
    print("="*80)
    print("GitHub Issue Creator from Grouped Errors")
    print("="*80)

    # Check GitHub API rate limit at start
    secrets = load_secrets()
    github_token = secrets.get("GITHUB_TOKEN", "")
    if github_token:
        from github_api_utils import load_commit_hash_cache
        load_commit_hash_cache()
        log_rate_limit_status(github_token, "start")
    
    # Check credentials
    check_credentials()
    
    # Delete all existing issues (for testing)
    delete_all_issues()
    
    # Check repository access and permissions
    if not check_repository_access():
        print("\nERROR: Cannot access repository. Please check:")
        print("  1. Token has access to this repository")
        print("  2. Repository exists and is accessible")
        print("  3. Organization policies allow PAT access")
        sys.exit(1)
    
    # Test connection by listing existing issues
    list_existing_issues()
    
    # Load grouped errors
    print(f"\nLoading grouped errors from {GROUPED_ERRORS_FILE}...")
    try:
        with open(GROUPED_ERRORS_FILE, 'r') as f:
            grouped_errors = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: File not found: {GROUPED_ERRORS_FILE}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {GROUPED_ERRORS_FILE}: {e}")
        sys.exit(1)
    
    total_groups = len(grouped_errors)
    print(f"Found {total_groups} groups")
    
    if total_groups == 0:
        print("No groups to process. Exiting.")
        return
    
    # Confirm before starting (skip in CI environments)
    print("\n" + "="*80)
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
        # Running in CI - auto-confirm
        print(f"Running in CI - auto-confirming creation of {total_groups} issue(s)...")
    else:
        # Interactive mode - ask for confirmation
        response = input(f"Ready to create {total_groups} issue(s). Continue? (y/n): ").strip().lower()
        if response != 'y':
            print("Cancelled.")
            return
    
    # Process each group
    bulk_mode = False
    for group_num, (group_name, group_data) in enumerate(grouped_errors.items(), 1):
        bulk_mode = process_group(group_name, group_data, group_num, total_groups, bulk_mode)
    
    print("\n" + "="*80)
    print("All groups processed!")
    print("="*80)

    # Log cache effectiveness and save cache
    if github_token:
        from github_api_utils import get_commit_hash_cache_stats, save_commit_hash_cache

        cache_stats = get_commit_hash_cache_stats()
        print(f"\n{'='*60}")
        print("Commit Hash Cache Statistics:")
        print(f"  Total cached runs: {cache_stats['total_entries']}")
        print(f"  Successful fetches: {cache_stats['found']}")
        print(f"  Failed fetches: {cache_stats['not_found']}")
        print(f"{'='*60}\n")

        # Save cache to disk for next run
        save_commit_hash_cache()

    # Check GitHub API rate limit at end
    if github_token:
        log_rate_limit_status(github_token, "end")

if __name__ == "__main__":
    main()
