#!/usr/bin/env python3
"""
Weekly Digest Agent

Generates a curated weekly summary of Azure/GitHub/DevTools announcements from
the feed data, writes a markdown digest file, and optionally creates a GitHub Issue.

Reads data/feeds.json (produced by fetch_feeds.py) and filters to the last 7 days.
Uses Microsoft Foundry for AI summarization when available; falls back to a
structured listing otherwise.

Required environment variables:
  GITHUB_TOKEN          - GitHub PAT or Actions token (for issue creation)
  GITHUB_REPOSITORY     - owner/repo (set automatically in Actions)

Optional environment variables:
  FOUNDRY_PROJECT_ENDPOINT       - Foundry project endpoint (enables AI summary)
  FOUNDRY_MODEL_DEPLOYMENT_NAME  - Model deployment name (default: gpt-5.4)
  DIGEST_DAYS                    - Number of days to look back (default: 7)
"""

import asyncio
import json
import os
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
DIGESTS_DIR = os.path.join(ROOT_DIR, "digests")

# High-level categories mapped from blogId values
CATEGORY_MAP = {
    # Azure infrastructure & compute
    "azurecompute": "Azure Infrastructure",
    "azureinfrastructureblog": "Azure Infrastructure",
    "azurenetworkingblog": "Azure Infrastructure",
    "azurestorageblog": "Azure Infrastructure",
    "azurestackblog": "Azure Infrastructure",
    "azurehighperformancecomputingblog": "Azure Infrastructure",
    "azureconfidentialcomputingblog": "Azure Infrastructure",
    "azurevirtualdesktopblog": "Azure Infrastructure",
    # Azure platform & services
    "appsonazureblog": "Azure Platform",
    "azurepaasblog": "Azure Platform",
    "integrationsonazureblog": "Azure Platform",
    "messagingonazureblog": "Azure Platform",
    "azurecommunicationservicesblog": "Azure Platform",
    "azuremapsblog": "Azure Platform",
    "azurearcblog": "Azure Platform",
    # Data & AI
    "analyticsonazure": "Data & AI",
    "azure-databricks": "Data & AI",
    "cosmosdbblog": "Data & AI",
    "azuresqlblog": "Data & AI",
    "azure-ai-foundry-blog": "Data & AI",
    "foundryblog": "Data & AI",
    # DevTools
    "visualstudio": "Developer Tools",
    "vscodeblog": "Developer Tools",
    "vscode": "Developer Tools",
    "azuredevops": "Developer Tools",
    "azuresdkblog": "Developer Tools",
    "azuretoolsblog": "Developer Tools",
    "commandline": "Developer Tools",
    "aspireblog": "Developer Tools",
    "dotnet": "Developer Tools",
    "developfromthecloud": "Developer Tools",
    "iseblog": "Developer Tools",
    "msdevblog": "Developer Tools",
    "azuredevcommunityblog": "Developer Tools",
    # GitHub
    "githubblog": "GitHub",
    "githubchangelog": "GitHub",
    # Copilot & Agents
    "microsoft365copilotblog": "Copilot & Agents",
    "securitycopilotblog": "Copilot & Agents",
    "agent-365-blog": "Copilot & Agents",
    # Security
    "microsoftsentinelblog": "Security",
    "microsoftdefendercloudblog": "Security",
    "azurenetworksecurityblog": "Security",
    "azureadvancedthreatprotection": "Security",
    "microsoftsecurity": "Security",
    # Governance & operations
    "azuregovernanceandmanagementblog": "Governance & Operations",
    "azureobservabilityblog": "Governance & Operations",
    "azuremigrationblog": "Governance & Operations",
    "finopsblog": "Governance & Operations",
    "modernizationbestpracticesblog": "Governance & Operations",
    # Containers & Kubernetes
    "aksblog": "Containers & Kubernetes",
    "aksreleases": "Containers & Kubernetes",
    "kubernetes": "Containers & Kubernetes",
    # External & ecosystem
    "terraform": "Ecosystem",
    "oracleonazureblog": "Ecosystem",
    "letsdodevops": "Ecosystem",
    "linuxandopensourceblog": "Ecosystem",
    "microsoft-planetary-computer-blog": "Ecosystem",
    "telecommunications-industry-blog": "Ecosystem",
    "azure-customer-innovation-blog": "Ecosystem",
    "azure-events": "Ecosystem",
    "azurearchitectureblog": "Ecosystem",
    "azureupdates": "Azure Updates",
    "allthingsazure": "Azure Updates",
}

# Display order for categories
CATEGORY_ORDER = [
    "Copilot & Agents",
    "GitHub",
    "Data & AI",
    "Developer Tools",
    "Azure Platform",
    "Azure Infrastructure",
    "Containers & Kubernetes",
    "Security",
    "Governance & Operations",
    "Azure Updates",
    "Ecosystem",
    "Other",
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


def categorize(articles):
    """Group articles by high-level category. Returns dict[category, list[article]]."""
    groups = defaultdict(list)
    for a in articles:
        blog_id = a.get("blogId", "")
        category = CATEGORY_MAP.get(blog_id, "Other")
        groups[category].append(a)
    return groups


def format_article_bullet(a):
    """Format a single article as a markdown bullet."""
    title = a.get("title", "Untitled")
    link = a.get("link", "")
    summary = a.get("summary", "")
    blog = a.get("blog", "")

    if summary and len(summary) > 120:
        summary = summary[:120].rsplit(" ", 1)[0] + "..."

    parts = []
    if link:
        parts.append(f"- [{title}]({link})")
    else:
        parts.append(f"- {title}")
    if summary:
        parts[0] += f" — {summary}"
    if blog:
        parts[0] += f" *({blog})*"
    return parts[0]


def build_plain_digest(articles_by_category, week_label, date_range):
    """Build a structured markdown digest without AI summarization."""
    lines = [
        f"# Weekly Digest — {week_label}",
        "",
        f"*{date_range}*",
        "",
        f"**{sum(len(v) for v in articles_by_category.values())} announcements** across "
        f"{len(articles_by_category)} categories.",
        "",
    ]

    for category in CATEGORY_ORDER:
        items = articles_by_category.get(category)
        if not items:
            continue
        lines.append(f"## {category}")
        lines.append("")
        for a in items[:15]:
            lines.append(format_article_bullet(a))
        if len(items) > 15:
            lines.append(f"- *...and {len(items) - 15} more*")
        lines.append("")

    return "\n".join(lines)


async def generate_ai_digest(articles_by_category, week_label, date_range):
    """Use Foundry to generate an AI-curated digest. Returns markdown string or None."""
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
    if not endpoint:
        print("No FOUNDRY_PROJECT_ENDPOINT set, skipping AI digest generation")
        return None

    from agent_framework.azure import AzureAIClient
    from azure.identity.aio import DefaultAzureCredential

    # Build a structured input for the model
    category_summaries = []
    for category in CATEGORY_ORDER:
        items = articles_by_category.get(category)
        if not items:
            continue
        titles = "\n".join(
            f"  - {a.get('title', 'Untitled')} ({a.get('blog', '')})" for a in items[:20]
        )
        category_summaries.append(f"{category} ({len(items)} articles):\n{titles}")

    all_categories_text = "\n\n".join(category_summaries)
    total = sum(len(v) for v in articles_by_category.values())

    prompt = f"""You are a technical editor creating a weekly digest of Microsoft Azure, GitHub, and developer ecosystem announcements.

Period: {date_range}
Total articles: {total}

Articles by category:

{all_categories_text}

Write a weekly digest in markdown with:
1. An executive summary (3-5 sentences) highlighting the week's most important themes
2. For each category that has articles, write a "## Category Name" section with:
   - A 1-2 sentence category overview
   - Bullet points for the most notable announcements (max 10 per category)
   - Each bullet should be concise (one line)

Do NOT include links (they will be added separately).
Do NOT invent announcements — only reference titles from the list above.
Focus on what matters most to developers and architects."""

    deployment = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-5.4")

    try:
        async with DefaultAzureCredential() as credential:
            async with AzureAIClient(
                project_endpoint=endpoint,
                model_deployment_name=deployment,
                credential=credential,
            ).as_agent(
                name="WeeklyDigestEditor",
                instructions="You are a concise, expert technical editor for Azure and GitHub news.",
            ) as agent:
                response = await agent.run(prompt)
                ai_content = response.text.strip()
                print(f"AI digest generated ({len(ai_content)} chars)")
                return ai_content
    except Exception as e:
        print(f"AI digest generation failed: {e}")
        return None


def build_digest(articles_by_category, week_label, date_range, ai_content=None):
    """Build the final digest markdown, with or without AI content."""
    lines = [
        f"# Weekly Digest — {week_label}",
        "",
        f"*{date_range}*",
        "",
    ]

    total = sum(len(v) for v in articles_by_category.values())

    if ai_content:
        # Insert AI summary, then append the full article listing
        lines.append(ai_content)
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"## All Articles ({total})")
        lines.append("")
        for category in CATEGORY_ORDER:
            items = articles_by_category.get(category)
            if not items:
                continue
            lines.append(f"### {category}")
            lines.append("")
            for a in items:
                lines.append(format_article_bullet(a))
            lines.append("")
    else:
        lines.append(
            f"**{total} announcements** across {len(articles_by_category)} categories."
        )
        lines.append("")
        for category in CATEGORY_ORDER:
            items = articles_by_category.get(category)
            if not items:
                continue
            lines.append(f"## {category}")
            lines.append("")
            for a in items[:15]:
                lines.append(format_article_bullet(a))
            if len(items) > 15:
                lines.append(f"- *...and {len(items) - 15} more*")
            lines.append("")

    return "\n".join(lines)


def write_digest_file(content, week_label):
    """Write the digest markdown to digests/YYYY-WXX.md. Returns the file path."""
    os.makedirs(DIGESTS_DIR, exist_ok=True)
    filename = f"{week_label}.md"
    filepath = os.path.join(DIGESTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Digest written to {filepath}")
    return filepath


def create_github_issue(title, body):
    """Create a GitHub Issue via the REST API. Requires GITHUB_TOKEN and GITHUB_REPOSITORY."""
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token:
        print("No GITHUB_TOKEN set, skipping issue creation")
        return None
    if not repo:
        print("No GITHUB_REPOSITORY set, skipping issue creation")
        return None

    url = f"https://api.github.com/repos/{repo}/issues"
    payload = json.dumps({
        "title": title,
        "body": body,
        "labels": ["weekly-digest"],
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
            print(f"GitHub Issue created: {issue_url}")
            return issue_url
    except Exception as e:
        print(f"Failed to create GitHub Issue: {e}")
        return None


def main():
    print("=" * 60)
    print("Weekly Digest Agent")
    print("=" * 60)

    days = int(os.environ.get("DIGEST_DAYS", "7"))

    articles = load_articles()
    print(f"Loaded {len(articles)} total articles")

    recent = filter_recent(articles, days=days)
    print(f"Found {len(recent)} articles from the last {days} days")

    if not recent:
        print("No recent articles found. Nothing to digest.")
        return

    articles_by_category = categorize(recent)
    category_counts = {k: len(v) for k, v in articles_by_category.items() if v}
    print(f"Categories: {category_counts}")

    # Compute week label and date range
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=days)
    iso_year, iso_week, _ = now.isocalendar()
    week_label = f"{iso_year}-W{iso_week:02d}"
    date_range = f"{week_start.strftime('%B %d')} – {now.strftime('%B %d, %Y')}"

    # Generate AI summary if Foundry is available
    ai_content = None
    try:
        ai_content = asyncio.run(
            generate_ai_digest(articles_by_category, week_label, date_range)
        )
    except Exception as e:
        print(f"AI digest failed, falling back to plain listing: {e}")

    digest = build_digest(articles_by_category, week_label, date_range, ai_content)

    # Write markdown file
    filepath = write_digest_file(digest, week_label)

    # Create GitHub Issue
    issue_title = f"📰 Weekly Digest — {week_label} ({date_range})"

    # Truncate body for issue if needed (GitHub limit is 65536 chars)
    issue_body = digest
    if len(issue_body) > 60000:
        issue_body = issue_body[:60000] + "\n\n*...truncated. See full digest in the repository.*"

    issue_url = create_github_issue(issue_title, issue_body)

    print(f"\n{'=' * 60}")
    print(f"Done! Digest: {filepath}")
    if issue_url:
        print(f"Issue: {issue_url}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
