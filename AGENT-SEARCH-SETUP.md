# Agent + Azure AI Search Integration Setup

Quick reference guide for integrating your Foundry agent with Azure AI Search knowledge base.

## Files Included

| File | Purpose | Location |
|------|---------|----------|
| `agent-search-instructions.md` | System prompt for agent behavior | Root / Agent config |
| `agent.yaml` | Tool definitions and model config | Root / Agent project |
| `search-index-schema.json` | Azure AI Search index schema | Root / Reference |

## Quick Setup (3 steps)

### 1. Configure Agent System Prompt
Copy the contents of **`agent-search-instructions.md`** into your Foundry agent's system prompt field or `.prompt.md` file.

```bash
# Option A: Set as system prompt in agent configuration
cp agent-search-instructions.md .agent-system-prompt.md

# Option B: Merge into existing agent.yaml
cat agent-search-instructions.md >> agent.yaml
```

### 2. Update agent.yaml
Merge the tool configuration from **`agent.yaml`** into your Foundry agent configuration.

**Key sections to merge:**
- `tools` section (search_azure_news_feed definition)
- `execution` section (trace_level: verbose for debugging)
- `response_validation` section (enforce citations and status formatting)

**Critical fields:**
```yaml
include_source_fields:
  - "title"
  - "link"      # ⭐ Direct source URLs (fix for search index links)
  - "source"
  - "status"
  - "date"
  - "category"
```

### 3. Verify Search Index Schema
Review **`search-index-schema.json`** and ensure your Azure AI Search index has:

```json
{
  "name": "link",
  "type": "Edm.String",
  "retrievable": true  // ⭐ CRITICAL for markdown link generation
}
```

**Deploy schema:**
```bash
# Via Azure CLI
az search index create \
  --resource-group rg-squintelier-5556 \
  --service-name squintelier-5556-search \
  --index-definition @search-index-schema.json

# Via Bicep (add to infra/main.bicep)
resource searchIndex 'Microsoft.Search/searchServices/indexes@2024-06-01-preview' = {
  name: '${searchService.name}/azure-news-feed'
  properties: loadJsonContent('../search-index-schema.json')
}
```

## Validation Checklist

- [ ] System prompt includes mandatory search invocation rule
- [ ] Response format sections (✅ GA / 🧪 Preview / 🔒 Internal) are enforced
- [ ] `link` field is included in `include_source_fields` in agent.yaml
- [ ] Markdown links use format: `[title](link)` not `[title](search_index_url)`
- [ ] Search index has `link` field and it's marked `retrievable: true`
- [ ] Foundry trace logging enabled (`trace_level: verbose`)
- [ ] Test query: "What's the latest GitHub Copilot news?"
  - [ ] Response includes inline links to actual source URLs
  - [ ] Search & Verification section shows retrieved documents
  - [ ] Validation checklist is present at bottom

## Test Query Examples

```
"What is the latest news on GitHub Copilot?"
"Show me recent Azure announcements"
"What's generally available vs in preview this week?"
"Tell me about Microsoft Foundry updates"
```

## Expected Response Structure

```
Executive Summary
[Overview of announcements]

✅ Generally Available
- [Feature Name](actual_url) — Description

🧪 Public Preview
- [Feature Name](actual_url) — Description

🔒 Private Preview / Internal-only
- [Feature Name] — Description

---

**[Search & Verification]**

Search Query Used: `user query here`

Documents Retrieved (with source links):
- [Title](url) — Status: GA | Source: GitHub Blog
- [Title](url) — Status: Preview | Source: Azure Updates

Validation Checklist:
✅ Tool is always called (Foundry trace confirms invocation)
✅ All links point to source documents (not search index)
✅ No hallucinated roadmap items (all sourced from index)
✅ Internal-only items appear only in internal mode
✅ GitHub + Microsoft products grouped together
✅ GA / Preview buckets remain consistent
```

## Environment Variables Required

```bash
AZURE_SEARCH_ENDPOINT=https://<service>.search.windows.net
AZURE_SEARCH_API_KEY=<admin-or-query-key>
FOUNDRY_PROJECT_ENDPOINT=https://<project>.api.azureml.ms
FOUNDRY_MODEL_DEPLOYMENT_NAME=gpt-5.4  # or similar
```

## Troubleshooting

### Links still point to search index
- **Cause**: `link` field not in `include_source_fields`
- **Fix**: Add `"link"` to the agent.yaml tool configuration

### Search tool not being called
- **Cause**: `always_invoke: true` not set
- **Check**: Foundry trace should show `search_azure_news_feed` call
- **Fix**: Enable verbose tracing with `trace_level: verbose`

### Response format not using ✅/🧪/🔒
- **Cause**: System prompt not properly set in agent config
- **Fix**: Copy exact text from `agent-search-instructions.md` "Status Categories" section

### Hallucinated announcements appearing
- **Cause**: Tool not being invoked or index not being checked
- **Fix**: Verify search tool invocation in Foundry trace; ensure `response_validation.check_hallucination: true`

## References

- [Azure AI Search Documentation](https://learn.microsoft.com/azure/search/)
- [Azure OpenAI Embeddings](https://learn.microsoft.com/azure/ai-services/openai/reference#embeddings)
- [Foundry Agent Framework](https://learn.microsoft.com/azure/ai-studio/concepts/agents)
