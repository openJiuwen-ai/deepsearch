Current date: {{ current_date }}
Output language: {{ language }}
Maximum search query count: {{ max_search_query_count }}

## Current task context

Topic:
{{ plan_title }}

Research guidance:
{{ plan_thought }}

Task title:
{{ step_title }}

Task description:
{{ step_description }}

{% if has_target_papers %}
## Target papers
{{ target_papers_text }}

- Treat these values as user constraints, not as already verified evidence.
- Generate a locator query only when the paper, its dataset, methods, or findings directly support this isolated collector step. Unrelated steps must not search for it.
- For each relevant paper, use this locator priority exactly: direct academic URL > PMID > DOI > arXiv ID > full title > implicit fingerprint.
- For a supplied URL, emit that exact URL as the one locator query so the existing URL search/fetch chain can retrieve it. Do not replace it with an unrelated topical query.
- Generate at most one locator query for a relevant paper in the initial round, within the existing `{{ max_search_query_count }}` limit. Do not emit separate PMID, DOI, and title queries for the same paper.
- Set `search_engine_names` to `["pubmed"]` for a PMID or biomedical target and set `search_engine_names` to `["arxiv"]` for an arXiv ID or a clear AI/CS/mathematics/statistics/physics target. Use `[]` when DOI or title domain cannot be determined safely.
- Write vertical locator queries in English academic terminology. Preserve exact identifiers and full titles.
- Keep the locator query separate from ordinary thematic evidence queries. Do not mix dataset/year clues with report instructions or generic terms.
- A dataset observation year is not a publication-date boundary and must not be treated as `temporal_scope`.
- For an unresolved implicit fingerprint, relax only in this order: dataset + data year + topic; dataset + topic; canonical dataset name or abbreviation + topic.
- Collector sessions are isolated. Reduce duplication through relevance gating; do not assume or promise a report-level exactly-once lookup.
{% endif %}

{% if has_temporal_scope %}
## Research Time Boundary
{{ temporal_scope_instruction }}
- Interpret "latest" as the latest information available within this boundary.
- {{ temporal_query_instruction }}
- Do not use provider-specific filter syntax (e.g. engine date parameters); only natural-language time phrases are allowed.
- A query may contain at most five topical keywords; the time phrase does not count toward the five topical keywords.
{% endif %}
{% if has_temporal_scope == false %}
Query should ensure that the most current information available as of {{ current_date }} is gathered.
{% endif %}
