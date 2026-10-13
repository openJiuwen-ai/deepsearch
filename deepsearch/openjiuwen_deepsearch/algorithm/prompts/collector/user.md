Current date: {{ current_date }}

{% if query %}
## Task

Gather detailed, accurate information for this query:

{{ query }}
{% else %}
Continue using the preceding tool result. Select the next action only when it can add useful information to the task.
{% endif %}

You have {{ remaining_steps }} tool-call step(s) remaining. Choose the next tool wisely.

All outputs must be in the specified language: **{{ language }}**.
