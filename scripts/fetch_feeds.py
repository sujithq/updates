#!/usr/bin/env python3
"""
Azure News Feed - Configurable RSS Feed Fetcher

This version moves feed definitions to feeds.json and uses one generic fetch flow
for all source types.

Supported source types:
- techcommunity: requires "board_id"
- rss: requires "feed_url"
"""

import feedparser
import json
import os
import re
import time
from collections import Counter
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from html import unescape


TECHCOMMUNITY_RSS_TEMPLATE = (
    "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id={board_id}"
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "feeds.json")
DATA_DIR = os.path.join(ROOT_DIR, "data")
JSON_OUTPUT_PATH = os.path.join(DATA_DIR, "feeds.json")
RSS_OUTPUT_PATH = os.path.join(DATA_DIR, "feed.xml")


def load_config(path=CONFIG_PATH):
    """Load configuration from feeds.json."""
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "sources" not in config or not isinstance(config["sources"], list):
        raise ValueError("Invalid config: 'sources' must be a list")

    return config


def get_defaults(config):
    """Return defaults from config with sensible fallbacks."""
    defaults = config.get("defaults", {})
    return {
        "sleep_seconds": defaults.get("sleep_seconds", 0.5),
        "max_age_days": defaults.get("max_age_days", 30),
        "summary_max_length": defaults.get("summary_max_length", 300),
        "author": defaults.get("author", "Microsoft"),
    }


def clean_html(text):
    """Remove HTML tags and normalize whitespace."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = unescape(clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def truncate(text, max_length=300):
    """Truncate text to max_length, ending at a word boundary."""
    if len(text) <= max_length:
        return text
    truncated = text[:max_length].rsplit(" ", 1)[0]
    return truncated + "..."


def parse_date(entry):
    """Parse date from feed entry and return ISO-8601 string."""
    for field in ["published_parsed", "updated_parsed"]:
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (ValueError, TypeError):
                continue

    for field in ["published", "updated"]:
        date_str = entry.get(field, "")
        if date_str:
            # Keep original if feedparser did not give structured date
            return date_str

    return datetime.now(timezone.utc).isoformat()


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

        # Handle trailing Z in ISO-8601 strings.
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


def resolve_feed_url(source):
    """Resolve the actual feed URL for a source."""
    source_type = source.get("type")

    if source_type == "techcommunity":
        board_id = source.get("board_id")
        if not board_id:
            raise ValueError(f"Source '{source.get('id', 'unknown')}' missing board_id")
        return TECHCOMMUNITY_RSS_TEMPLATE.format(board_id=board_id)

    if source_type == "rss":
        feed_url = source.get("feed_url")
        if not feed_url:
            raise ValueError(f"Source '{source.get('id', 'unknown')}' missing feed_url")
        return feed_url

    raise ValueError(
        f"Unsupported source type '{source_type}' for source '{source.get('id', 'unknown')}'"
    )


def extract_summary(entry):
    """Get the best available summary/content from a feed entry."""
    summary = entry.get("summary", "")
    if summary:
        return summary

    description = entry.get("description", "")
    if description:
        return description

    content = entry.get("content", [])
    if content and isinstance(content, list):
        first = content[0]
        if isinstance(first, dict):
            return first.get("value", "")

    return ""


def fetch_source(source, default_author="Microsoft", summary_max_length=300):
    """Fetch and normalize articles from one configured source."""
    source_id = source.get("id", "unknown")
    source_name = source.get("name", source_id)
    url = resolve_feed_url(source)

    print(f"Fetching: {source_name} ({source_id})...")
    articles = []

    try:
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            print(f"  Warning: Could not parse feed for {source_name}")
            return articles

        count = 0
        for entry in feed.entries:
            summary = clean_html(extract_summary(entry))

            articles.append(
                {
                    "title": clean_html(entry.get("title", "Untitled")),
                    "link": entry.get("link", ""),
                    "published": parse_date(entry),
                    "summary": truncate(summary, summary_max_length),
                    "blog": source_name,
                    "blogId": source_id,
                    "author": entry.get("author", default_author),
                    "feedUrl": url,
                    "sourceType": source.get("type", ""),
                }
            )
            count += 1

        print(f"  Found {count} articles")

    except Exception as e:
        print(f"  Error fetching {source_name}: {e}")

    return articles


def sort_articles(articles):
    """Sort articles newest first using parsed publication datetime."""
    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        articles,
        key=lambda x: parse_published_datetime(x.get("published")) or min_dt,
        reverse=True,
    )


def dedupe_and_filter_articles(articles, max_age_days=30):
    """
    Remove duplicates by link and discard articles older than max_age_days.

    Articles with unparseable dates are kept (to avoid accidentally dropping valid posts).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    seen_links = set()
    unique_articles = []

    for article in sort_articles(articles):
        link = article.get("link", "")
        published = article.get("published", "")
        published_dt = parse_published_datetime(published)

        if not link or link in seen_links:
            continue

        if published_dt is not None and published_dt < cutoff:
            continue

        seen_links.add(link)
        unique_articles.append(article)

    return unique_articles


def generate_rss_feed(articles):
    """Generate an RSS feed XML file from aggregated articles."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    rss = Element("rss", version="2.0")
    rss.set("xmlns:dc", "http://purl.org/dc/elements/1.1/")

    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "Azure News Feed"
    SubElement(channel, "link").text = "https://azurefeed.news"
    SubElement(channel, "description").text = "Aggregated daily news from Azure blogs"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    SubElement(channel, "generator").text = "Azure News Feed"
    SubElement(channel, "language").text = "en"

    for article in articles[:50]:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = article["title"]
        SubElement(item, "link").text = article["link"]
        SubElement(item, "guid").text = article["link"]
        SubElement(item, "description").text = article["summary"]
        SubElement(item, "dc:creator").text = article["author"]

        try:
            dt = datetime.fromisoformat(article["published"])
            SubElement(item, "pubDate").text = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        except (ValueError, TypeError):
            pass

        SubElement(item, "category").text = article["blog"]

    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(
        rss, encoding="unicode"
    )

    with open(RSS_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"RSS feed saved to {RSS_OUTPUT_PATH}")


def generate_ai_summary(articles):
    """Generate an AI summary of today's articles using OpenAI (optional)."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("No OPENAI_API_KEY set, skipping AI summary")
        return None

    try:
        import openai

        today = datetime.now(timezone.utc).date().isoformat()
        today_articles = [
            a for a in articles if a.get("published", "").startswith(today)
        ]

        if not today_articles:
            print("No articles published today, skipping AI summary")
            return None

        titles = "\n".join(
            [
                f"- {a['title']} ({a['blog']})"
                for a in today_articles[:20]
            ]
        )

        prompt = (
            "You are a concise tech news editor. Summarize today's Azure blog posts "
            "in 2-3 sentences highlighting the most important themes and announcements. "
            "Be specific about technologies mentioned. Here are the articles:\n\n"
            + titles
        )

        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )

        summary = response.choices[0].message.content.strip()
        print(f"AI summary generated: {summary[:100]}...")
        return summary

    except Exception as e:
        print(f"AI summary failed: {e}")
        return None


def validate_sources(sources):
    """Basic validation for configured sources."""
    seen_ids = set()

    for source in sources:
        source_id = source.get("id")
        source_name = source.get("name")
        source_type = source.get("type")

        if not source_id:
            raise ValueError("Every source must have an 'id'")
        if source_id in seen_ids:
            raise ValueError(f"Duplicate source id found: {source_id}")
        seen_ids.add(source_id)

        if not source_name:
            raise ValueError(f"Source '{source_id}' missing 'name'")
        if not source_type:
            raise ValueError(f"Source '{source_id}' missing 'type'")

        if source_type == "techcommunity" and not source.get("board_id"):
            raise ValueError(f"TechCommunity source '{source_id}' missing 'board_id'")

        if source_type == "rss" and not source.get("feed_url"):
            raise ValueError(f"RSS source '{source_id}' missing 'feed_url'")


def main():
    print("=" * 60)
    print("Azure News Feed - Fetching RSS Feeds")
    print("=" * 60)

    config = load_config(CONFIG_PATH)
    defaults = get_defaults(config)

    sources = [
        source for source in config["sources"] if source.get("enabled", True)
    ]

    enabled_ids = [source.get("id", "unknown") for source in sources]
    print(f"Enabled sources ({len(enabled_ids)}): {', '.join(enabled_ids) if enabled_ids else 'none'}")

    if not sources:
        print("Warning: No feeds are enabled. Set 'enabled': true for at least one source in feeds.json")
        return

    validate_sources(sources)

    all_articles = []
    source_names_by_id = {source.get("id", "unknown"): source.get("name", "unknown") for source in sources}

    for source in sources:
        all_articles.extend(
            fetch_source(
                source,
                default_author=defaults["author"],
                summary_max_length=defaults["summary_max_length"],
            )
        )
        time.sleep(defaults["sleep_seconds"])

    unique_articles = dedupe_and_filter_articles(
        all_articles,
        max_age_days=defaults["max_age_days"],
    )

    discarded = len(all_articles) - len(unique_articles)
    if discarded:
        print(f"Filtered out {discarded} duplicate/older-than-{defaults['max_age_days']}-days articles")

    fetched_counts = Counter(article.get("blogId", "unknown") for article in all_articles)
    kept_counts = Counter(article.get("blogId", "unknown") for article in unique_articles)

    print("Source summary (fetched -> kept):")
    for source in sources:
        source_id = source.get("id", "unknown")
        source_name = source_names_by_id.get(source_id, source_id)
        print(f"  - {source_name} ({source_id}): {fetched_counts[source_id]} -> {kept_counts[source_id]}")

    summary = generate_ai_summary(unique_articles)

    data = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "totalArticles": len(unique_articles),
        "articles": unique_articles,
    }

    if summary:
        data["summary"] = summary

    os.makedirs(DATA_DIR, exist_ok=True)

    with open(JSON_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    generate_rss_feed(unique_articles)

    print(f"\n{'=' * 60}")
    print(f"Done! {len(unique_articles)} unique articles saved to {JSON_OUTPUT_PATH}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()