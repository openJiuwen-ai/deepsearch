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
- Query must consist of keywords, with the first keyword being the main subject. The total number of keywords should be less than 5.
- Query should ensure that the most current information is gathered. The current time is {{ CURRENT_TIME }}.
- Do not produce more than {{ number_queries }} queries.
- Write your response in {{ language }}.

## Output Format
- Return a JSON object with exactly these keys:
  - "missing_evidence": A list of verifiable evidence requirements for the current step.
  - "queries": A list of search queries, each query is less than 5 keywords.
- Do not output explanations, rationale, markdown fences, or any extra keys.

## Example
{
    "missing_evidence": ["specific verifiable evidence requirement for the current step"],
    "queries": ["Tesla battery lifespan official"]
}
