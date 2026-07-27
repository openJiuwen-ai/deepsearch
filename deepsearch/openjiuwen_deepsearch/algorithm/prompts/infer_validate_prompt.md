---
CURRENT_TIME: {{ CURRENT_TIME }}
---
You will be given a conclusion string labeled "statement" and a list of reference materials labeled "references". Your task is to select, from the references, those that are relevant to the statement.

# Writing Guidelines

Example format of input references:
[
    {
        "id": 0,
        "content": "", // string-type reference material
    }
    {
        "id": 1,
        "content": "", 
    }
]

1. Compare each "content" in the references with the statement to determine relevance. Relevance is defined as: the content contains the same facts or data (data may be approximately matched) as those mentioned in the statement.
2. If a reference is deemed relevant, add its "id" to the output list.

Use the following output format strictly. Generate output in JSON format and do not include any non-existent IDs:
[0, 1]

# User Input

Below are the provided "statement" and "references":
<statement>
{{statement}}
</statement>

<references>
{{references}}
</references>

# Note
1. Adhere strictly to the specified output format. Do not engage in casual conversation or produce any output outside the given instructions.
2. Always use the language specified by the locale = **{{ language }}**.