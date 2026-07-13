Your goal is to generate focused web search queries for the current collector step and identify the evidence still needed.

## Current task context

Topic:
{{ plan_title }}

Research guidance:
{{ plan_thought }}

Task title:
{{ step_title }}

Task description:
{{ step_description }}

## Instructions
- First identify the current step's missing evidence as concrete, verifiable evidence requirements.
- Each missing evidence item should name the object, scope, acceptance standard, and intended report use when possible.
- Each missing evidence item must be a single plain string, not an object with subfields.
- Generate queries that directly serve the missing evidence, not broad queries for the whole section.
- If the topic has a clear subject, such as "Apple Inc's new product in 2025", each query must include that subject.
- Queries should be diverse. Each query should focus on one specific aspect of the missing evidence.
- Do not generate multiple similar queries.
- **Query Count**:
    - For steps that need retrieval, generate 1..{{ max_search_query_count }} queries.
    - Use fewer queries for simple or low-value gaps; use more only for independent critical evidence gaps.
    - Return `queries: []` only when the current step explicitly does not require external retrieval.
    - Do not generate queries just to fill the limit.
- **Query Coverage**:
    - Within the `{{ max_search_query_count }}` query limit, prioritize covering the most important `missing_evidence` items. When missing evidence exceeds the query limit, focus on the items most critical to the step's conclusion.
    - **Adaptive evidence types**: When the step's domain supports both factual/data evidence and analytical/interpretive evidence, generate at least one query targeting each type. For purely qualitative domains (humanities, law, philosophy) or purely practical tasks (design, generation), adapt the evidence-type requirement to what the domain naturally supports.
    - **Opposing/contrasting terms**: Include opposing or contrasting search terms ONLY when the step's topic involves genuine debate, competing approaches, or alternative viewpoints. Do NOT fabricate opposition for factual, technical, or methodological queries where there is no meaningful counter-position.
    - **Anti-repetition**: Each query should target a different information need. If two queries would likely return overlapping results, merge them and add a query for a different missing evidence item.
- Query must consist of keywords, with the first keyword being the main subject. The total number of keywords should be less than 5.
- Query should ensure that the most current information is gathered. The current time is {{ CURRENT_TIME }}.
- Do not produce more than {{ max_search_query_count }} queries.
- For retrieval-needed steps, the allowed query count range is 1..{{ max_search_query_count }}.
- Write your response in {{ language }}.
- For each query, also choose a secondary vertical search engine in `search_engine_name`.
  - Use `"pubmed"` for medicine, clinical evidence, biology, drugs, disease, epidemiology, genes, proteins, or patient-related evidence.
  - Use `"arxiv"` for AI, computer science, mathematics, statistics, physics, algorithms, machine learning, LLM, RAG, benchmarks, or preprint evidence.
  - Use `""` for general web evidence such as official websites, news, policy, standards, company information, market data, or when no vertical source is appropriate.
- `search_engine_name` is an additional vertical search engine. It does not replace the user's configured primary web search engine.

## Output Format
- Return a JSON object with exactly these keys:
  - "missing_evidence": A list of verifiable evidence requirements for the current step.
  - "queries": A list of query objects. Each object contains:
    - "query": A search query less than 5 keywords.
    - "search_engine_name": One of "pubmed", "arxiv", or "".
- Do not output explanations, rationale, markdown fences, or any extra keys.

## Example
{
    "missing_evidence": ["specific verifiable evidence requirement for the current step"],
    "queries": [
        {
            "query": "Tesla battery lifespan official",
            "search_engine_name": ""
        }
    ]
}
