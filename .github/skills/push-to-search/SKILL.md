---
name: push-to-search
description: Indexes articles from data/feeds.json into Azure AI Search. Use when asked to index articles, update the search index, push to search, or sync the Azure AI Search index.
---

# Pushing Articles to Azure AI Search

When asked to index articles or update the Azure AI Search index, follow this process:

## What This Skill Does

Runs `scripts/push_to_search.py` to read articles from `data/feeds.json` (the source of truth) and upsert them into the Azure AI Search index (`azure-news-feed` by default). The index is created or updated automatically on each run.

## 1. Verify prerequisites

Ensure the Python environment is available and dependencies are installed:

```bash
pip install -r scripts/requirements.txt
```

Required environment variables (set in `.env` or CI secrets):
- `AZURE_SEARCH_ENDPOINT` — e.g. `https://my-service.search.windows.net`
- `AZURE_SEARCH_INDEX` — Index name (default: `azure-news-feed`)
- `AZURE_SEARCH_KEY` — (optional) Admin API key; falls back to `DefaultAzureCredential`

## 2. Ensure the feed data is current

Before indexing, confirm `data/feeds.json` is up to date. If you are unsure, run the **refresh-feeds** skill first:

```bash
python scripts/fetch_feeds.py
```

## 3. Run the indexer

```bash
python scripts/push_to_search.py
```

The script will:
1. Read all articles from `data/feeds.json`
2. Create (or update) the Azure AI Search index schema with semantic search enabled
3. Upsert articles in batches of 100
4. Print a summary of documents indexed

## 4. Verify the index

After the run, you can test the index with a quick query:

```bash
python -c "
import os
from azure.search.documents import SearchClient
from azure.identity import DefaultAzureCredential
endpoint = os.environ['AZURE_SEARCH_ENDPOINT']
index = os.environ.get('AZURE_SEARCH_INDEX', 'azure-news-feed')
client = SearchClient(endpoint, index, DefaultAzureCredential())
results = list(client.search('*', top=3))
print(f'Index contains at least {len(results)} documents')
for r in results:
    print(' -', r.get('title','(no title)'))
"
```

## 5. Index schema

The index is configured with semantic search and the following key fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | String (key) | SHA-256 hash of the article link (first 40 chars) |
| `title` | Searchable string | Article title |
| `summary` | Searchable string | Article content/summary |
| `blog` | Filterable string | Source blog name |
| `published` | Filterable datetime | Publication date |
| `link` | String | Source article URL |

## Rules

- Always run `push_to_search.py` after `fetch_feeds.py` to keep the index in sync with `data/feeds.json`
- Do not manually edit the Azure AI Search index — always regenerate from `data/feeds.json` via this skill
- If authentication fails, ensure `az login` has been run locally or the managed identity has the `Search Index Data Contributor` role
