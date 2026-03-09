Your goal is to generate sophisticated and diverse web search queries. These queries are intended for an advanced automated web research tool capable of analyzing complex results, following links, and synthesizing information.

## Instructions:
- If the topic has a clear subject, such as "Apple Inc's new product in 2025" where "Apple Inc" is the subject, your query keywords must include this subject.
- Queries should be diverse, if the topic is broad, generate more than 1 query.
- Each query should focus on one specific aspect of the original question.
- Don't generate multiple similar queries, each query must be unique.
- Query must consist of keywords, with the first keyword being the main subject. The total number of keywords should less than 5. 
- Query should ensure that the most current information is gathered. The current time is {{ CURRENT_TIME }}.
- Don't produce more than {{ number_queries }} queries.
- Write your response in {{ language }}.

# Current Search Topic:

{{ research_record }}

## Format: 
- Format your response as a JSON object with ALL two of these exact keys:
   - "description": Brief explanation of why these queries are relevant
   - "query": A list of search queries, each query is less than 5 keywords, connect keywords with spaces e.g. "Tesla battery lifespan offical statement"

## Example: (Directly provide a structured response with out ```json tags)
```json
{
    "description": "",  // string
    "queries": [""],  // list of string, e.g. ["Tesla battery lifespan offical statement"]
}
```
