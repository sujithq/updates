#!/usr/bin/env python3
"""
Breaking Changes Tracker

Scans feed data for deprecations, breaking changes, and migration deadlines.
Uses keyword heuristics to find candidates, then Foundry AI to classify severity
and extract deadlines. Creates GitHub Issues for actionable items.

Reads data/feeds.json and persists known breaking changes in data/breaking-changes.json
to avoid duplicate alerts across runs.

Required environment variables:
  GITHUB_TOKEN          - GitHub PAT or Actions token (for issue creation)
  GITHUB_REPOSITORY     - owner/repo (set automatically in Actions)

Optional environment variables:
  FOUNDRY_PROJECT_ENDPOINT       - Foundry project endpoint (enables AI classification)
  FOUNDRY_MODEL_DEPLOYMENT_NAME  - Model deployment name (default: gpt-5.4)
  TRACKER_DAYS                   - Number of days to look back (default: 1)
  TRACKER_SKIP_ISSUES            - Set to "true" to skip GitHub Issue creation
"""

import asyncio
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

load_dotenv(override=False)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_PATH = os.path.join(ROOT_DIR, "data", "feeds.json")
KNOWN_PATH = os.path.join(ROOT_DIR, "data", "breaking-changes.json")

# Keywords that signal a breaking change or deprecation
PRIMARY_KEYWORDS = [
    r"deprecat",
    r"breaking\s+change",
    r"end\s+of\s+life",
    r"\bEOL\b",
    r"\bretir(e|ing|ed|ement)\b",
    r"\bsunset",
    r"\bremoved\b",
    r"no\s+longer\s+supported",
    r"migration\s+required",
    r"action\s+required",
]

SECONDARY_KEYWORDS = [
    r"will\s+be\s+removed",
    r"planned\s+removal",
    r"support\s+ends",
    r"upgrade\s+required",
    r"end\s+of\s+support",
    r"will\s+no\s+longer",
]

PRIMARY_PATTERNS = [re.compile(kw, re.IGNORECASE) for kw in PRIMARY_KEYWORDS]
SECONDARY_PATTERNS = [re.compile(kw, re.IGNORECASE) for kw in SECONDARY_KEYWORDS]


def parse_published_datetime(value):
    """Parse published date string into timezone-aware UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    return None


def load_articles(path=DATA_PATH):
    """Load articles from the feed JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("articles", [])


def load_known(path=KNOWN_PATH):
    """Load previously detected breaking changes."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {item["link"]: item for item in data.get("items", []) if item.get("link")}


def save_known(known_dict, path=KNOWN_PATH):
    """Persist known breaking changes."""
    data = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "items": list(known_dict.values()),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Known breaking changes saved ({len(known_dict)} items)")


def filter_recent(articles, days=1):
    """Return articles published within the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for a in articles:
        dt = parse_published_datetime(a.get("published"))
        if dt and dt >= cutoff:
            recent.append(a)
    return recent


def match_keywords(text):
    """Check if text matches breaking change keywords. Returns (is_match, confidence)."""
    if not text:
        return False, "none"
    for pattern in PRIMARY_PATTERNS:
        if pattern.search(text):
            return True, "high"
    for pattern in SECONDARY_PATTERNS:
        if pattern.search(text):
            return True, "medium"
    return False, "none"


def find_candidates(articles):
    """Identify articles that look like breaking changes based on keywords."""
    candidates = []
    for a in articles:
        title = a.get("title", "")
        summary = a.get("summary", "")
        combined = f"{title} {summary}"

        is_match, confidence = match_keywords(combined)
        if is_match:
            candidates.append({
                **a,
                "_keyword_confidence": confidence,
            })
    return candidates


async def classify_with_ai(candidates):
    """Use Foundry AI to classify severity and extract deadlines from candidates."""
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        print("No FOUNDRY_PROJECT_ENDPOINT set, skipping AI classification")
        return None

    from agent_framework.azure import AzureAIClient
    from azure.identity.aio import DefaultAzureCredential

    articles_text = ""
    for i, c in enumerate(candidates):
        articles_text += (
            f"\n[{i}] Title: {c.get('title', '')}\n"
            f"    Summary: {c.get('summary', '')}\n"
            f"    Source: {c.get('blog', '')}\n"
            f"    Published: {c.get('published', '')}\n"
        )

    prompt = f"""You are a technical analyst reviewing Azure and GitHub announcements for breaking changes, deprecations, and migration deadlines.

For each article below, provide a JSON array with one object per article containing:
- "index": the article number
- "severity": one of "critical", "warning", or "info"
  - "critical": imminent deadline (< 90 days from now) or already-breaking change
  - "warning": future deprecation announced with timeline > 90 days
  - "info": mentions deprecation but no clear action or timeline
- "deadline": the deprecation/retirement date if mentioned (ISO-8601 format), or null
- "impact": a concise one-line summary of what's changing and who's affected (max 100 chars)
- "action": what users should do (max 100 chars), or null if unclear

Today's date: {datetime.now(timezone.utc).date().isoformat()}

Articles:
{articles_text}

Respond with ONLY a JSON array, no markdown fences, no explanation."""

    deployment = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-5.4")

    try:
        async with DefaultAzureCredential() as credential:
            async with AzureAIClient(
                project_endpoint=endpoint,
                model_deployment_name=deployment,
                credential=credential,
            ).as_agent(
                name="BreakingChangeClassifier",
                instructions="You are a precise technical analyst. Respond only with valid JSON.",
            ) as agent:
                response = await agent.run(prompt)
                text = response.text.strip()
                # Strip markdown fences if present
                if text.startswith("```"):
                    text = re.sub(r"^```\w*\n?", "", text)
                    text = re.sub(r"\n?```$", "", text)
                classifications = json.loads(text)
                print(f"AI classified {len(classifications)} candidates")
                return classifications
    except Exception as e:
        print(f"AI classification failed: {e}")
        return None


def apply_classifications(candidates, classifications):
    """Merge AI classifications into candidate articles."""
    if not classifications:
        # Fallback: use keyword confidence as severity proxy
        for c in candidates:
            confidence = c.get("_keyword_confidence", "medium")
            c["severity"] = "warning" if confidence == "high" else "info"
            c["deadline"] = None
            c["impact"] = c.get("summary", "")[:100]
            c["action"] = None
        return candidates

    classified = []
    by_index = {item["index"]: item for item in classifications}
    for i, c in enumerate(candidates):
        ai = by_index.get(i, {})
        c["severity"] = ai.get("severity", "info")
        c["deadline"] = ai.get("deadline")
        c["impact"] = ai.get("impact", c.get("summary", "")[:100])
        c["action"] = ai.get("action")
        classified.append(c)
    return classified


def severity_emoji(severity):
    """Return emoji for severity level."""
    return {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")


def format_breaking_change_bullet(item):
    """Format a single breaking change as a markdown bullet."""
    emoji = severity_emoji(item.get("severity", "info"))
    title = item.get("title", "Untitled")
    link = item.get("link", "")
    impact = item.get("impact", "")
    deadline = item.get("deadline")

    parts = [emoji]
    if link:
        parts.append(f"[{title}]({link})")
    else:
        parts.append(title)

    if impact:
        parts.append(f"— {impact}")

    if deadline:
        parts.append(f"⏰ **Deadline: {deadline}**")

    return " ".join(parts)


def build_consolidated_issue(items, date_label):
    """Build markdown body for the consolidated daily issue."""
    lines = [
        f"# Breaking Changes Detected — {date_label}",
        "",
        f"Found **{len(items)}** articles with breaking changes or deprecations.",
        "",
    ]

    # Group by severity
    for severity in ["critical", "warning", "info"]:
        group = [i for i in items if i.get("severity") == severity]
        if not group:
            continue
        label = {"critical": "🔴 Critical", "warning": "🟡 Warning", "info": "🔵 Informational"}[severity]
        lines.append(f"## {label} ({len(group)})")
        lines.append("")
        for item in group:
            lines.append(f"- {format_breaking_change_bullet(item)}")
            action = item.get("action")
            if action:
                lines.append(f"  - **Action:** {action}")
        lines.append("")

    return "\n".join(lines)


def build_individual_issue(item):
    """Build title and body for an individual breaking change issue."""
    emoji = severity_emoji(item.get("severity", "warning"))
    title_text = item.get("title", "Untitled")
    title = f"{emoji} Breaking Change: {title_text}"

    lines = [
        f"## {title_text}",
        "",
    ]

    link = item.get("link", "")
    if link:
        lines.append(f"**Source:** [{item.get('blog', 'Unknown')}]({link})")
    lines.append(f"**Severity:** {item.get('severity', 'unknown').upper()}")
    lines.append(f"**Published:** {item.get('published', 'unknown')}")

    deadline = item.get("deadline")
    if deadline:
        lines.append(f"**Deadline:** ⏰ {deadline}")

    lines.append("")

    impact = item.get("impact", "")
    if impact:
        lines.append(f"### Impact")
        lines.append("")
        lines.append(impact)
        lines.append("")

    action = item.get("action")
    if action:
        lines.append(f"### Recommended Action")
        lines.append("")
        lines.append(action)
        lines.append("")

    summary = item.get("summary", "")
    if summary:
        lines.append("### Full Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")

    return title, "\n".join(lines)


def create_github_issue(title, body, labels):
    """Create a GitHub Issue. Returns the issue URL or None."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token or not repo:
        print(f"Skipping issue creation (token={'set' if token else 'missing'}, repo={repo or 'missing'})")
        return None

    url = f"https://api.github.com/repos/{repo}/issues"
    payload = json.dumps({
        "title": title[:256],
        "body": body[:65000],
        "labels": labels,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            issue_url = result.get("html_url", "")
            print(f"  Issue created: {issue_url}")
            return issue_url
    except Exception as e:
        print(f"  Failed to create issue: {e}")
        return None


def main():
    print("=" * 60)
    print("Breaking Changes Tracker")
    print("=" * 60)

    days = int(os.environ.get("TRACKER_DAYS", "1"))
    skip_issues = os.environ.get("TRACKER_SKIP_ISSUES", "").lower() == "true"

    articles = load_articles()
    print(f"Loaded {len(articles)} total articles")

    recent = filter_recent(articles, days=days)
    print(f"Found {len(recent)} articles from the last {days} day(s)")

    candidates = find_candidates(recent)
    print(f"Found {len(candidates)} breaking change candidates")

    if not candidates:
        print("No breaking changes detected. Done.")
        return

    # Load known to filter out already-alerted items
    known = load_known()
    new_candidates = [c for c in candidates if c.get("link") not in known]
    print(f"New candidates (not previously seen): {len(new_candidates)}")

    if not new_candidates:
        print("All candidates were previously detected. Done.")
        return

    # AI classification
    classifications = None
    try:
        classifications = asyncio.run(classify_with_ai(new_candidates))
    except Exception as e:
        print(f"AI classification failed, using keyword fallback: {e}")

    classified = apply_classifications(new_candidates, classifications)

    # Count by severity
    severity_counts = {}
    for item in classified:
        s = item.get("severity", "info")
        severity_counts[s] = severity_counts.get(s, 0) + 1
    print(f"Severity breakdown: {severity_counts}")

    # Persist to known
    for item in classified:
        link = item.get("link", "")
        if link:
            known[link] = {
                "link": link,
                "title": item.get("title", ""),
                "severity": item.get("severity", "info"),
                "deadline": item.get("deadline"),
                "impact": item.get("impact", ""),
                "action": item.get("action"),
                "detected": datetime.now(timezone.utc).isoformat(),
                "published": item.get("published", ""),
                "blog": item.get("blog", ""),
            }
    save_known(known)

    if skip_issues:
        print("Issue creation skipped (TRACKER_SKIP_ISSUES=true)")
    else:
        # Create individual issues for critical and warning
        high_severity = [i for i in classified if i.get("severity") in ("critical", "warning")]
        if high_severity:
            print(f"\nCreating {len(high_severity)} individual issue(s)...")
            for item in high_severity:
                title, body = build_individual_issue(item)
                labels = ["breaking-change", item.get("severity", "warning")]
                create_github_issue(title, body, labels)

        # Create consolidated daily issue
        date_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        consolidated_title = f"📋 Breaking Changes Report — {date_label}"
        consolidated_body = build_consolidated_issue(classified, date_label)
        create_github_issue(consolidated_title, consolidated_body, ["breaking-change", "daily-report"])

    print(f"\n{'=' * 60}")
    print(f"Done! Detected {len(classified)} breaking change(s)")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
