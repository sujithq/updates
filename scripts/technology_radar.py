#!/usr/bin/env python3
"""
Technology Radar

Tracks Azure and GitHub features moving between Private Preview, Public Preview,
and General Availability. Surfaces weekly status transitions so teams can see
what's newly available or approaching maturity.

Reads data/feeds.json, detects status signals via keyword heuristics, classifies
with Foundry AI, persists state in data/radar-state.json, writes a radar markdown
file, and creates a GitHub Issue.

Required environment variables:
  GITHUB_TOKEN          - GitHub PAT or Actions token (for issue creation)
  GITHUB_REPOSITORY     - owner/repo (set automatically in Actions)

Optional environment variables:
  FOUNDRY_PROJECT_ENDPOINT       - Foundry project endpoint (enables AI classification)
  FOUNDRY_MODEL_DEPLOYMENT_NAME  - Model deployment name (default: gpt-5.4)
  RADAR_DAYS                     - Number of days to look back (default: 7)
"""

import asyncio
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

load_dotenv(override=False)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_PATH = os.path.join(ROOT_DIR, "data", "feeds.json")
STATE_PATH = os.path.join(ROOT_DIR, "data", "radar-state.json")
DIGESTS_DIR = os.path.join(ROOT_DIR, "digests")

# Patterns to detect status signals in title + summary
GA_PATTERNS = [
    re.compile(r"generally\s+available", re.IGNORECASE),
    re.compile(r"\bnow\s+GA\b", re.IGNORECASE),
    re.compile(r"\bis\s+GA\b", re.IGNORECASE),
    re.compile(r"General\s+Availability", re.IGNORECASE),
    re.compile(r"\bGA\s+announcement\b", re.IGNORECASE),
    re.compile(r"moved?\s+to\s+GA\b", re.IGNORECASE),
]

PREVIEW_PATTERNS = [
    re.compile(r"public\s+preview", re.IGNORECASE),
    re.compile(r"now\s+in\s+preview", re.IGNORECASE),
    re.compile(r"available\s+in\s+preview", re.IGNORECASE),
    re.compile(r"enter(?:s|ing)\s+preview", re.IGNORECASE),
    re.compile(r"preview\s+announcement", re.IGNORECASE),
    re.compile(r"\bin\s+preview\b", re.IGNORECASE),
]

PRIVATE_PREVIEW_PATTERNS = [
    re.compile(r"private\s+preview", re.IGNORECASE),
    re.compile(r"limited\s+preview", re.IGNORECASE),
    re.compile(r"gated\s+preview", re.IGNORECASE),
    re.compile(r"invite[\s-]only\s+preview", re.IGNORECASE),
]


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


def load_state(path=STATE_PATH):
    """Load previously tracked radar state. Keyed by feature name."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("features", {})


def save_state(features, path=STATE_PATH):
    """Persist radar state."""
    data = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "features": features,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Radar state saved ({len(features)} tracked features)")


def filter_recent(articles, days=7):
    """Return articles published within the last N days, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for a in articles:
        dt = parse_published_datetime(a.get("published"))
        if dt and dt >= cutoff:
            recent.append(a)
    recent.sort(
        key=lambda x: parse_published_datetime(x.get("published")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return recent


def detect_status(text):
    """Detect the status signal from text. Returns status string or None."""
    # Check in order of specificity: private preview > GA > public preview
    for pattern in PRIVATE_PREVIEW_PATTERNS:
        if pattern.search(text):
            return "private-preview"
    for pattern in GA_PATTERNS:
        if pattern.search(text):
            return "ga"
    for pattern in PREVIEW_PATTERNS:
        if pattern.search(text):
            return "preview"
    return None


def find_status_articles(articles):
    """Identify articles that mention a status transition."""
    results = []
    for a in articles:
        title = a.get("title", "")
        summary = a.get("summary", "")
        combined = f"{title} {summary}"

        status = detect_status(combined)
        if status:
            results.append({
                **a,
                "_detected_status": status,
            })
    return results


async def classify_with_ai(items):
    """Use Foundry AI to extract feature names and classify status transitions."""
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        print("No FOUNDRY_PROJECT_ENDPOINT set, skipping AI classification")
        return None

    from agent_framework.azure import AzureAIClient
    from azure.identity.aio import DefaultAzureCredential

    articles_text = ""
    for i, item in enumerate(items):
        articles_text += (
            f"\n[{i}] Title: {item.get('title', '')}\n"
            f"    Summary: {item.get('summary', '')}\n"
            f"    Source: {item.get('blog', '')}\n"
            f"    Detected status: {item.get('_detected_status', 'unknown')}\n"
        )

    prompt = f"""You are a technical analyst tracking Azure and GitHub feature maturity status.

For each article below, extract the feature/product name and its status. Respond with a JSON array:
- "index": the article number
- "feature": the specific feature or product name (concise, e.g. "Azure Container Storage", not the full title)
- "status": one of "ga", "preview", "private-preview"
  - "ga": feature is now generally available
  - "preview": feature is now in public preview
  - "private-preview": feature is in private/limited preview
- "status_detail": a one-line description of what's changing (max 80 chars)
- "service": the parent Azure/GitHub service (e.g. "Azure Kubernetes Service", "GitHub Actions", "Azure AI Search")

If an article mentions multiple features with different statuses, create one entry per feature.
If the article does not actually announce a status change (false positive), set status to null.

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
                name="TechnologyRadarClassifier",
                instructions="You are a precise technical analyst. Respond only with valid JSON.",
            ) as agent:
                response = await agent.run(prompt)
                text = response.text.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```\w*\n?", "", text)
                    text = re.sub(r"\n?```$", "", text)
                classifications = json.loads(text)
                print(f"AI classified {len(classifications)} items")
                return classifications
    except Exception as e:
        print(f"AI classification failed: {e}")
        return None


def apply_classifications(items, classifications):
    """Merge AI classifications into detected items."""
    classified = []

    if not classifications:
        # Fallback: use keyword detection as-is, derive feature name from title
        for item in items:
            item["feature"] = item.get("title", "Unknown")[:80]
            item["status"] = item.get("_detected_status", "preview")
            item["status_detail"] = item.get("summary", "")[:80]
            item["service"] = item.get("blog", "Unknown")
            classified.append(item)
        return classified

    by_index = defaultdict(list)
    for c in classifications:
        by_index[c.get("index")].append(c)

    for i, item in enumerate(items):
        ai_entries = by_index.get(i, [])
        if not ai_entries:
            # No AI classification, use fallback
            item["feature"] = item.get("title", "Unknown")[:80]
            item["status"] = item.get("_detected_status", "preview")
            item["status_detail"] = item.get("summary", "")[:80]
            item["service"] = item.get("blog", "Unknown")
            classified.append(item)
        else:
            for ai in ai_entries:
                if ai.get("status") is None:
                    continue  # AI flagged as false positive
                entry = {**item}
                entry["feature"] = ai.get("feature", item.get("title", "")[:80])
                entry["status"] = ai.get("status", item.get("_detected_status", "preview"))
                entry["status_detail"] = ai.get("status_detail", "")
                entry["service"] = ai.get("service", item.get("blog", ""))
                classified.append(entry)

    return classified


def detect_movements(classified, previous_state):
    """Compare current classifications against previous state to find movements."""
    movements = []
    for item in classified:
        feature = item.get("feature", "")
        current_status = item.get("status", "")
        previous = previous_state.get(feature)

        if previous and previous.get("status") != current_status:
            item["_previous_status"] = previous.get("status")
            item["_movement"] = f"{previous.get('status')} → {current_status}"
            movements.append(item)

    return movements


STATUS_EMOJI = {
    "ga": "🟢",
    "preview": "🔵",
    "private-preview": "🟣",
}

STATUS_LABELS = {
    "ga": "Generally Available",
    "preview": "Public Preview",
    "private-preview": "Private Preview",
}

# Display order
STATUS_ORDER = ["ga", "preview", "private-preview"]


def format_radar_bullet(item):
    """Format a radar item as a markdown bullet."""
    title = item.get("title", "Untitled")
    link = item.get("link", "")
    feature = item.get("feature", title)
    detail = item.get("status_detail", "")
    service = item.get("service", "")

    parts = []
    if link:
        parts.append(f"**{feature}** — [{title}]({link})")
    else:
        parts.append(f"**{feature}** — {title}")

    if detail:
        parts[0] += f" — {detail}"
    if service:
        parts[0] += f" *({service})*"

    movement = item.get("_movement")
    if movement:
        parts[0] += f" ⬆️ `{movement}`"

    return "- " + parts[0]


def build_radar_markdown(classified, movements, week_label, date_range):
    """Build the radar markdown content."""
    lines = [
        f"# Technology Radar — {week_label}",
        "",
        f"*{date_range}*",
        "",
    ]

    total = len(classified)
    lines.append(f"Tracking **{total}** feature status announcements this week.")
    lines.append("")

    # Status movements section
    if movements:
        lines.append("## ⬆️ Status Changes")
        lines.append("")
        lines.append("Features that moved between maturity tiers this week:")
        lines.append("")
        for item in movements:
            lines.append(format_radar_bullet(item))
        lines.append("")

    # Group by status
    for status in STATUS_ORDER:
        emoji = STATUS_EMOJI.get(status, "⚪")
        label = STATUS_LABELS.get(status, status)
        group = [i for i in classified if i.get("status") == status and i not in movements]

        if not group:
            continue

        lines.append(f"## {emoji} {label} ({len(group)})")
        lines.append("")
        for item in group:
            lines.append(format_radar_bullet(item))
        lines.append("")

    if not classified:
        lines.append("No feature status announcements detected this week.")
        lines.append("")

    return "\n".join(lines)


def write_radar_file(content, week_label):
    """Write the radar markdown to digests/radar-YYYY-WXX.md."""
    os.makedirs(DIGESTS_DIR, exist_ok=True)
    filename = f"radar-{week_label}.md"
    filepath = os.path.join(DIGESTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Radar written to {filepath}")
    return filepath


def update_state(classified, previous_state):
    """Update the radar state with current classifications."""
    for item in classified:
        feature = item.get("feature", "")
        if not feature:
            continue
        previous_state[feature] = {
            "status": item.get("status"),
            "link": item.get("link", ""),
            "title": item.get("title", ""),
            "service": item.get("service", ""),
            "lastSeen": datetime.now(timezone.utc).isoformat(),
            "published": item.get("published", ""),
        }
    return previous_state


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
            print(f"Issue created: {issue_url}")
            return issue_url
    except Exception as e:
        print(f"Failed to create issue: {e}")
        return None


def main():
    print("=" * 60)
    print("Technology Radar")
    print("=" * 60)

    days = int(os.environ.get("RADAR_DAYS", "7"))

    articles = load_articles()
    print(f"Loaded {len(articles)} total articles")

    recent = filter_recent(articles, days=days)
    print(f"Found {len(recent)} articles from the last {days} days")

    status_articles = find_status_articles(recent)
    print(f"Found {len(status_articles)} articles with status signals")

    if not status_articles:
        print("No status transitions detected. Done.")
        return

    # Count by detected status
    status_counts = defaultdict(int)
    for a in status_articles:
        status_counts[a.get("_detected_status", "unknown")] += 1
    print(f"Status breakdown: {dict(status_counts)}")

    # AI classification
    classifications = None
    try:
        classifications = asyncio.run(classify_with_ai(status_articles))
    except Exception as e:
        print(f"AI classification failed, using keyword fallback: {e}")

    classified = apply_classifications(status_articles, classifications)
    print(f"Classified {len(classified)} radar items")

    # Load previous state and detect movements
    previous_state = load_state()
    movements = detect_movements(classified, previous_state)
    if movements:
        print(f"Detected {len(movements)} status movement(s)")

    # Compute week label and date range
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=days)
    iso_year, iso_week, _ = now.isocalendar()
    week_label = f"{iso_year}-W{iso_week:02d}"
    date_range = f"{week_start.strftime('%B %d')} – {now.strftime('%B %d, %Y')}"

    # Build and write markdown
    radar_md = build_radar_markdown(classified, movements, week_label, date_range)
    filepath = write_radar_file(radar_md, week_label)

    # Update and save state
    updated_state = update_state(classified, previous_state)
    save_state(updated_state)

    # Create GitHub Issue
    ga_count = sum(1 for i in classified if i.get("status") == "ga")
    preview_count = sum(1 for i in classified if i.get("status") == "preview")
    issue_title = f"🔭 Technology Radar — {week_label} ({ga_count} GA, {preview_count} Preview)"
    issue_url = create_github_issue(issue_title, radar_md, ["technology-radar"])

    print(f"\n{'=' * 60}")
    print(f"Done! Radar: {filepath}")
    if issue_url:
        print(f"Issue: {issue_url}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
