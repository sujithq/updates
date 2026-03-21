---
name: add-feed-source
description: Guides through adding a new RSS or TechCommunity feed source to scripts/feeds.json. Use when asked to add a feed, add a source, track a new blog, or monitor a new RSS feed.
---

# Adding a Feed Source

When asked to add a new feed source, follow this process:

## 1. Determine the source type

Ask the user for the feed URL or TechCommunity board name. Determine the type:

- **TechCommunity blog**: URL contains `techcommunity.microsoft.com`. Extract the `board_id` from the URL path (the segment after `/t5/` and before `/`). The type is `"techcommunity"`.
- **RSS feed**: Any other URL with an RSS/Atom feed. The type is `"rss"`.

If the user provides a blog page URL (not a feed URL), help them find the RSS feed URL:
- For `devblogs.microsoft.com/<blog>/` → append `feed/`
- For `github.blog/` → use `https://github.blog/feed/`
- For GitHub releases → use `https://github.com/<owner>/<repo>/releases.atom`
- For TechCommunity → construct: `https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=<board_id>`

## 2. Generate the JSON entry

Read `scripts/feeds.json` to check for duplicate IDs and understand the existing format. Then generate a new entry:

For **TechCommunity** sources:
```json
{
  "id": "<board_id>",
  "name": "<Human-readable name>",
  "type": "techcommunity",
  "board_id": "<board_id>",
  "enabled": true
}
```

For **RSS** sources:
```json
{
  "id": "<short-kebab-case-id>",
  "name": "<Human-readable name>",
  "type": "rss",
  "feed_url": "<full RSS feed URL>",
  "enabled": true
}
```

## 3. Validate before adding

Before inserting the entry into `scripts/feeds.json`:

1. **Check for duplicate IDs**: Search the existing `sources` array. The `id` must be unique.
2. **Check for duplicate URLs**: For RSS sources, verify the `feed_url` isn't already present. For TechCommunity, verify the `board_id` isn't already present.
3. **Verify the feed is reachable**: If terminal tools are available, test with:
   ```bash
   curl -sI "<feed_url>" | head -5
   ```
4. **Verify it returns valid RSS/Atom**: Optionally fetch the first few lines to confirm XML content.

## 4. Add the entry

Insert the new entry at the end of the `sources` array in `scripts/feeds.json`, before the closing `]`.

## 5. Refresh feeds and verify

After adding the entry, use the **refresh-feeds** skill (or run the fetcher directly) to verify the new source is picked up:

```bash
python scripts/fetch_feeds.py
```

The script prints how many articles were found per source. Confirm the new source appears in the output and that `data/feeds.json` was updated.

## 6. Sync the search index

Once feeds are refreshed and `data/feeds.json` is updated, use the **push-to-search** skill (or run the indexer directly) to make the new articles available in Azure AI Search:

```bash
python scripts/push_to_search.py
```

## 7. Category mapping (optional)

If the user wants the new source to appear in weekly digest categories, suggest adding a mapping in `scripts/weekly_digest.py` in the `CATEGORY_MAP` dictionary. Show the existing categories and ask which one fits, or suggest creating a new one.

## Rules

- Never add a source without confirming the `id` is unique
- Always set `"enabled": true` for new sources unless the user says otherwise
- Use lowercase kebab-case for IDs (e.g., `azure-functions-blog`, not `AzureFunctionsBlog`)
- The `name` field should be human-readable (e.g., "Azure Functions Blog")
- For TechCommunity sources, the `id` and `board_id` should match
