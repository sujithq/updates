#!/usr/bin/env python3
"""
Azure AI Search indexer for azure-news-feed articles.

Reads data/feeds.json and upserts all articles into an Azure AI Search index.
The index is created (or updated) automatically on first run.

Semantic search is configured so the index works as a RAG knowledge source
for a Copilot agent out of the box.

Required environment variables (set in .env or CI secrets):
  AZURE_SEARCH_ENDPOINT  - e.g. https://my-service.search.windows.net
    AZURE_SEARCH_KEY       - Optional admin API key fallback
  AZURE_SEARCH_INDEX     - Index name (default: azure-news-feed)
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv(override=False)

from azure.core.credentials import AzureKeyCredential
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    SemanticConfiguration,
    SemanticSearch,
    SemanticPrioritizedFields,
    SemanticField,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_PATH = os.path.join(ROOT_DIR, "data", "feeds.json")

BATCH_SIZE = 100


def get_env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        print(f"Error: environment variable '{name}' is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def get_search_credential():
    key = os.environ.get("AZURE_SEARCH_KEY", "").strip()
    if key:
        print("Using Azure AI Search admin key authentication")
        return AzureKeyCredential(key)

    print("Using Azure AI Search DefaultAzureCredential authentication")
    return DefaultAzureCredential()


def make_doc_id(link: str) -> str:
    """Stable URL-safe ID from article link (Azure Search key constraint)."""
    return hashlib.sha256(link.encode()).hexdigest()[:40]


def build_index(index_name: str) -> SearchIndex:
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SimpleField(name="link", type=SearchFieldDataType.String, filterable=True),
        # Canonical URL for tools/runtimes that auto-pick a URL field for citations.
        SimpleField(name="url", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="published", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SearchableField(name="summary", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchableField(name="blog", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="blogId", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="author", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="sourceType", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="feedUrl", type=SearchFieldDataType.String),
    ]

    semantic_config = SemanticConfiguration(
        name="default",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="title"),
            content_fields=[SemanticField(field_name="summary")],
            keywords_fields=[SemanticField(field_name="blog"), SemanticField(field_name="author")],
        ),
    )

    return SearchIndex(
        name=index_name,
        fields=fields,
        semantic_search=SemanticSearch(configurations=[semantic_config]),
    )


def ensure_index(index_client: SearchIndexClient, index_name: str) -> None:
    index = build_index(index_name)
    result = index_client.create_or_update_index(index)
    print(f"Index '{result.name}' ready ({len(result.fields)} fields, semantic: enabled)")


def normalize_date(value: str) -> str | None:
    """Return ISO-8601 with Z suffix accepted by Azure Search DateTimeOffset."""
    if not value:
        return None
    try:
        # already ISO
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def load_articles(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    articles = data.get("articles", [])
    print(f"Loaded {len(articles)} articles from {path}")
    return articles


def articles_to_docs(articles: list[dict]) -> list[dict]:
    docs = []
    for a in articles:
        link = a.get("link", "")
        if not link:
            continue
        docs.append({
            "id": make_doc_id(link),
            "title": a.get("title", ""),
            "link": link,
            "url": link,
            "published": normalize_date(a.get("published", "")),
            "summary": a.get("summary", ""),
            "blog": a.get("blog", ""),
            "blogId": a.get("blogId", ""),
            "author": a.get("author", ""),
            "sourceType": a.get("sourceType", ""),
            "feedUrl": a.get("feedUrl", ""),
        })
    return docs


def upload_batches(search_client: SearchClient, docs: list[dict]) -> None:
    total = len(docs)
    uploaded = 0
    failed = 0

    for i in range(0, total, BATCH_SIZE):
        batch = docs[i: i + BATCH_SIZE]
        results = search_client.upload_documents(documents=batch)
        batch_failed = sum(1 for r in results if not r.succeeded)
        batch_ok = len(batch) - batch_failed
        uploaded += batch_ok
        failed += batch_failed
        print(f"  Batch {i // BATCH_SIZE + 1}: {batch_ok} ok, {batch_failed} failed")

    print(f"\nUpload complete: {uploaded} succeeded, {failed} failed out of {total} documents")


def get_service_name(endpoint: str) -> str:
    host = urlparse(endpoint).netloc
    if host.endswith(".search.windows.net"):
        return host.replace(".search.windows.net", "")
    return host


def wait_for_document_count(search_client: SearchClient, expected_minimum: int, timeout_seconds: int = 30) -> int:
    """Poll until index reflects writes or timeout; indexing is near-real-time but not instant."""
    deadline = time.time() + timeout_seconds
    last_count = 0
    while time.time() < deadline:
        try:
            last_count = search_client.get_document_count()
            if last_count >= expected_minimum:
                return last_count
        except Exception:
            # Keep retrying transient read-after-write/data-plane delays.
            pass
        time.sleep(2)
    return last_count


def main():
    print("=" * 60)
    print("Azure News Feed - Push to Azure AI Search")
    print("=" * 60)

    endpoint = get_env("AZURE_SEARCH_ENDPOINT")
    index_name = os.environ.get("AZURE_SEARCH_INDEX", "azure-news-feed").strip() or "azure-news-feed"

    credential = get_search_credential()
    index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
    search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)

    ensure_index(index_client, index_name)

    articles = load_articles(DATA_PATH)
    docs = articles_to_docs(articles)
    print(f"Prepared {len(docs)} documents for indexing")

    upload_batches(search_client, docs)

    service_name = get_service_name(endpoint)
    visible_count = wait_for_document_count(search_client, len(docs))
    print(f"Search service: {service_name}.search.windows.net")
    print(f"Index '{index_name}' current document count: {visible_count}")

    print(f"\n{'=' * 60}")
    print(f"Done! Index '{index_name}' at {endpoint}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
