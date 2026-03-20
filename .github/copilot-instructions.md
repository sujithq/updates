# Copilot Instructions

## Project Overview

This is an Azure news feed aggregator that collects announcements from 60+ Microsoft/Azure/GitHub RSS sources, indexes them into Azure AI Search, and exposes them via a Microsoft Foundry agent. The agent answers questions about Azure, GitHub, Copilot, and developer ecosystem announcements grounded in the search index.

## Architecture

```
scripts/feeds.json (source config)
        │
        ▼
scripts/fetch_feeds.py ──► data/feeds.json + data/feed.xml
        │
        ├──► scripts/push_to_search.py ──► Azure AI Search ("azure-news-feed" index)
        │                                        │
        │                                        ▼
        │                              Foundry Agent (agent.yaml)
        │
        └──► scripts/weekly_digest.py ──► digests/YYYY-WXX.md + GitHub Issue
```

- **Feed ingestion**: `fetch_feeds.py` reads source definitions from `scripts/feeds.json`, fetches RSS feeds (TechCommunity boards and direct RSS URLs), deduplicates by link, filters by age, optionally generates an AI summary via Foundry, and writes to `data/`.
- **Search indexing**: `push_to_search.py` reads `data/feeds.json` and upserts documents into Azure AI Search with semantic search configured. Index schema is created/updated automatically on each run.
- **Foundry agent**: Defined in `agent.yaml` with system prompt in `agent-search-instructions.md`. The agent always invokes `search_azure_news_feed` before answering and formats responses with ✅ GA / 🧪 Preview / 🔒 Internal status buckets.
- **Weekly digest**: `weekly_digest.py` filters the last 7 days of articles from `data/feeds.json`, groups by category, generates an AI-curated summary via Foundry, writes to `digests/`, and creates a GitHub Issue. Falls back to a plain structured listing if Foundry is unavailable.
- **Infrastructure**: Bicep templates in `infra/` provision Azure AI Search and RBAC assignments (via `azd provision`). The Foundry project itself is pre-existing and referenced by resource ID.

## Build & Run Commands

### Local setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r scripts/requirements.txt
```

### Run feed fetcher

```powershell
python scripts/fetch_feeds.py
```

### Push to Azure AI Search

```powershell
python scripts/push_to_search.py
```

### Discover new TechCommunity boards not yet tracked

```powershell
python scripts/discover_techcommunity.py
```

### Generate weekly digest

```powershell
python scripts/weekly_digest.py
```

Set `DIGEST_DAYS` to override the default 7-day lookback window.

### Provision infrastructure

```powershell
azd provision --no-prompt
```

### Bootstrap OIDC identity (one-time)

```powershell
.\scripts\bootstrap.ps1 -SubscriptionId <sub> -TenantId <tenant> -Repository owner/repo
```

## Environment Variables

Required in `.env` for local dev (loaded via `python-dotenv`):

- `AZURE_SEARCH_ENDPOINT` — Azure AI Search endpoint
- `AZURE_SEARCH_KEY` — Admin API key (optional; falls back to `DefaultAzureCredential`)
- `AZURE_SEARCH_INDEX` — Index name (default: `azure-news-feed`)
- `FOUNDRY_PROJECT_ENDPOINT` — Foundry project endpoint (optional; enables AI summary)
- `FOUNDRY_MODEL_DEPLOYMENT_NAME` — Model deployment name (default: `gpt-5.4`)

## Key Conventions

### Feed source configuration

All feed sources are defined in `scripts/feeds.json`, not in code. Each source has a `type` field (`"techcommunity"` or `"rss"`) and can be toggled with `"enabled": true/false`. TechCommunity sources use a `board_id`; RSS sources use a `feed_url`.

### Authentication

The project uses **keyless auth (OIDC)** everywhere in CI. GitHub Actions authenticates via a user-assigned managed identity with federated credentials. Locally, `DefaultAzureCredential` is used (requires `az login`). The `.env` file supports an optional `AZURE_SEARCH_KEY` fallback for local dev.

### Two separate identities in CI

- `AZURE_PROVISION_CLIENT_ID` — Bootstrap identity for `azd provision` (stable, manually created via `bootstrap.ps1`)
- `AZURE_CLIENT_ID` — Runtime identity for feed fetching (created by `azd provision`, output as `AZURE_CLIENT_ID`)

These must be different; the `fetch-feeds` workflow validates this.

### Article document schema

Articles flow through the system with these fields: `title`, `link`, `url`, `published`, `summary`, `blog`, `blogId`, `author`, `sourceType`, `feedUrl`. The `link` field is the direct source URL and is critical for citation generation. Document IDs are SHA-256 hashes of the link (first 40 chars).

### Infrastructure as Code

Bicep templates use `azd` parameter substitution via `main.parameters.json` with `${ENV_VAR}` syntax. The Foundry project is referenced as an existing resource (not created by this repo). Cross-resource-group RBAC for the Foundry project is handled by the `foundry-project-rbac` module.

### GitHub Actions

- `fetch-feeds.yml` — Runs every 3 hours on schedule. Fetches feeds, pushes to search, commits updated `data/` back to the repo.
- `weekly-digest.yml` — Runs every Monday at 07:00 UTC. Generates an AI-curated weekly digest, commits to `digests/`, and creates a GitHub Issue. Also supports manual trigger.
- `azd-provision.yml` — Manual trigger only. Provisions Azure AI Search + RBAC via `azd provision`.
