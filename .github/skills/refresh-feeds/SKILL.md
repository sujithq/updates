---
name: refresh-feeds
description: Fetches the latest articles from all configured RSS and TechCommunity feed sources and updates data/feeds.json. Use when asked to refresh feeds, update articles, fetch new posts, or sync the feed data.
---

# Refreshing Feed Data

When asked to refresh feeds or update the article data, follow this process:

## What This Skill Does

Runs `scripts/fetch_feeds.py` to pull fresh articles from all enabled feed sources defined in `scripts/feeds.json` and writes the results to `data/feeds.json` (the source of truth for all downstream agents and scripts).

## 1. Verify prerequisites

Ensure the Python environment is available and dependencies are installed:

```bash
pip install -r scripts/requirements.txt
```

Required environment variables (set in `.env` or CI secrets):
- `FOUNDRY_PROJECT_ENDPOINT` — (optional) enables AI-generated summaries per article
- `FOUNDRY_MODEL_DEPLOYMENT_NAME` — (optional, default: `gpt-5.4`)

If these are not set, the script still runs but skips AI summaries.

## 2. Run the feed fetcher

```bash
python scripts/fetch_feeds.py
```

The script will:
1. Read feed source definitions from `scripts/feeds.json`
2. Fetch RSS/Atom content from all enabled sources (TechCommunity boards + direct RSS URLs)
3. Deduplicate articles by link
4. Filter articles by age (default: last 7 days; controlled by `FEED_DAYS` env var)
5. Optionally generate AI summaries via Foundry
6. Write results to `data/feeds.json` and `data/feed.xml`

### Optional: control lookback window

```bash
FEED_DAYS=14 python scripts/fetch_feeds.py
```

## 3. Verify the output

After the run, check that `data/feeds.json` was updated:

```bash
python -c "import json; d=json.load(open('data/feeds.json')); print(f'Total articles: {len(d)}')"
```

You can also inspect the most recently published articles:

```bash
python -c "
import json
from datetime import datetime
d = json.load(open('data/feeds.json'))
recent = sorted(d, key=lambda x: x.get('published',''), reverse=True)[:5]
for a in recent:
    print(a['published'], a['title'])
"
```

## 4. Next steps

After refreshing feeds, you can:
- Run the **push-to-search** skill to index the new articles into Azure AI Search
- Run `python scripts/breaking_changes.py` to scan new articles for breaking changes
- Run `python scripts/technology_radar.py` to detect new feature maturity transitions
- Run `python scripts/weekly_digest.py` to generate a fresh digest

## Rules

- Always refresh feeds before running any downstream analysis if the data may be stale
- `data/feeds.json` is the **single source of truth** — do not manually edit it; always regenerate by running `python scripts/fetch_feeds.py`
- If the script fails with authentication errors, ensure `az login` has been run or the managed identity has the required Foundry role
