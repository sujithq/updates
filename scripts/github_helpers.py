"""Shared GitHub API helpers for issue maintenance."""

import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone


def list_issues_by_label(label):
    """List all open issues with a specific label. Returns list of dicts with number and created_at, sorted by creation date (newest first)."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token or not repo:
        return []

    url = f"https://api.github.com/repos/{repo}/issues?labels={label}&state=open&sort=created&direction=desc&per_page=100"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            issues = json.loads(resp.read().decode("utf-8"))
            return [
                {"number": issue["number"], "created_at": issue["created_at"]}
                for issue in issues
                if "pull_request" not in issue
            ]
    except Exception as e:
        print(f"Failed to list issues: {e}")
        return []


def close_issue(issue_number):
    """Close a GitHub Issue."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token or not repo:
        return False

    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    payload = json.dumps({"state": "closed"}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="PATCH",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"  Closed issue #{issue_number}")
            return True
    except Exception as e:
        print(f"  Failed to close issue #{issue_number}: {e}")
        return False


def close_old_issues(label, keep_days=3):
    """Close old issues with a specific label, keeping only those from the last N days."""
    issues = list_issues_by_label(label)

    if not issues:
        print(f"No open issues found with label '{label}'")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    to_close = []
    issue_times = []

    for issue in issues:
        created_at_str = issue["created_at"]
        if created_at_str.endswith("Z"):
            created_at_str = created_at_str[:-1] + "+00:00"
        issue_times.append((datetime.fromisoformat(created_at_str), issue["number"]))

    issue_times.sort(key=lambda entry: entry[0])

    for created_at, issue_number in issue_times:
        if created_at < cutoff:
            to_close.append(issue_number)
        else:
            break

    if not to_close:
        print(f"Found {len(issues)} open issue(s) with label '{label}', all within {keep_days} days, no cleanup needed")
        return

    print(f"Closing {len(to_close)} old issue(s) with label '{label}' (keeping those from last {keep_days} days)")
    for issue_number in to_close:
        close_issue(issue_number)
