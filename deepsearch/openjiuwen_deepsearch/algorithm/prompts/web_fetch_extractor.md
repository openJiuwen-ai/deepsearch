You are given a task to analyze webpage content in relation to a specific objective.

### Objective
{{ goal }}

### Source Content
The text below is raw material retrieved from the webpage:
{{ webpage_content }}

### Instructions
Using only the source content above, identify information that directly contributes to the objective.

Respond strictly in JSON format with the following fields:
- "evidence": excerpts or passages from the content that support the objective.
- "summary": a concise synthesis explaining how the evidence addresses the objective.
