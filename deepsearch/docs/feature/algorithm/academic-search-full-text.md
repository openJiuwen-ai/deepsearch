# Academic search full-text enrichment

## Purpose

PubMed and arXiv search results preserve the abstract or bibliographic fallback in `content`. Each query returns at most one result by default, and the search wrappers attempt to retrieve its full text from official open-access sources.

## Behavior and data contract

- PubMed uses PMCID to retrieve PMC JATS XML.
- arXiv prefers official HTML and falls back to the official PDF.
- Full text is stored separately in `full_text`; `content` remains the abstract.
- When an available scholarly result enters Collector, `full_text` becomes the evidence `original_content`; the abstract remains the fallback when enrichment is unavailable or failed.
- `content_type`, `full_text_url`, `full_text_format`, `full_text_status`, and `full_text_truncated` describe availability and provenance.
- `academic_source`, `academic_source_id`, and `evidence_content_type` survive document selection so writing and final citations can be audited without logging article content.
- PubMed and arXiv `published` values participate in Collector `source_date` filtering. Exact dates compare directly; year-only and month-only values are treated as possible date ranges and are removed only when the entire range is outside the requested bounds.
- Missing, failed, or malformed full text never blocks the normal search result.
- The wrappers pass the model-generated query through unchanged. All PubMed E-utilities operations share one process-local request schedule across wrapper instances. arXiv Atom API calls share a request schedule, while official HTML/PDF downloads share a process-local concurrency limit of two; a 429 cooldown applies to both paths.
- HTTP 429, 500, 502, 503, 504, connection failures, and timeouts are attempted at most three times. `Retry-After` is honored up to 30 seconds; otherwise retries use one- and two-second exponential delays. Other 4xx responses and content parsing errors are not retried.
- arXiv follows redirects for HTML and PDF downloads. Legacy arXiv identifiers retain their archive category when a PDF URL is constructed.

## End-to-end audit

Each successful full-text document emits structured, content-free events to `common.log` at four stages:

- `returned`: an official full text was retrieved;
- `entered`: that full text became Collector evidence;
- `selected`: Reporter selected that full-text evidence for writing;
- `cited`: the final checked citations retained that document.

Use a dedicated log directory for one experiment, then summarize its `common.log`:

```powershell
& ".\.venv\Scripts\python.exe" -m openjiuwen_deepsearch.utils.academic_full_text_audit `
  "<experiment-log-dir>\common\common.log" `
  --conversation-id "<conversation-id>" `
  --output "<experiment-log-dir>\academic_full_text_summary.json"
```

Every formal audit event carries the `conversation_id` from the active workflow logging context, whose lifetime is bounded and reset by the workflow runner. Calls made without that active context skip formal audit emission without failing search, even if another session object remains in the task context. The CLI requires `--conversation-id` and reports only that conversation's raw full-text return events, unique returned papers, and unique papers reaching Collector, writing selection, and final citation for PubMed and arXiv separately. Legacy events without a conversation ID are intentionally excluded.

## Configuration

`web_search_engine_config.extension` accepts:

- `scholarly_fetch_full_text` (default `true`)
- `scholarly_max_full_text_results` (default `1`)
- `scholarly_full_text_timeout_seconds` (default `30`)
- `scholarly_max_full_text_length` (default: Collector document content limit)
- `pubmed_requests_per_second` (default `1/3`; applies to ESearch, PubMed EFetch, and PMC EFetch through a process-local shared schedule)
- `arxiv_requests_per_second` (default `1/3`; applies to Atom API calls through a process-local shared schedule)

The normal scholarly result limit is also `1` by default. A caller can still explicitly supply a different `max_web_search_results` when constructing a wrapper.

## Key paths and tests

- `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/pubmed.py`
- `openjiuwen_deepsearch/framework/openjiuwen/tools/search_api/scholarly_search/arxiv.py`
- `openjiuwen_deepsearch/algorithm/research_collector/collector_function.py`
- `openjiuwen_deepsearch/algorithm/research_collector/collector_evidence.py`
- `openjiuwen_deepsearch/algorithm/report/report.py`
- `openjiuwen_deepsearch/framework/openjiuwen/agent/main_graph_nodes.py`
- `openjiuwen_deepsearch/utils/academic_full_text_audit.py`
- `tests/tools/search_api/test_scholarly_search.py`
- `tests/info_collector/algorithm/test_collector_function.py`
- `tests/info_collector/algorithm/test_collector_evidence.py`
- `tests/utils/test_academic_full_text_audit.py`
