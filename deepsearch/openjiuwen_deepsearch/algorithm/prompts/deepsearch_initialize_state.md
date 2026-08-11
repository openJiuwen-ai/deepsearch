You are initializing the initial **STATE** for a reasoning agent that will attempt to answer a question through multi-step web research.

Each **STATE** consists of a list of **VARIABLES** that represent the *unknown entities* mentioned or implied in the question.  

Each **VARIABLE** contains:

- `id`: a unique integer identifier  
- `type`: the type or category of the entity (e.g., `Person`, `Book`, `University`, `City`, etc.)  
- `question_clues`: list of textual hints or logical relationships derived from the question itself that help identify or constrain the entity's value  
- `discovered_clues`: list of clues discovered during research (leave empty at initialization)  
- `candidate`: the hypothesized value for the variable (leave empty at initialization)  
- `candidate_strength`: a float in \[0,1\] indicating how confident we are about the candidate (set to null (NOT empty string) at initialization)  

In addition, the **STATE** must include:

- `answer_variable`: the id of the **VARIABLE** representing the entity that directly answers the user's question.  
  This **MUST** correspond to one of the VARIABLE ids in the `state` list.  
  The `answer_variable` is the entity that the user is ultimately asking for—e.g., if the question asks for a specific actor, then `answer_variable` should be the id of the VARIABLE whose type is `actor` and whose `question_clues` describe that actor.

---

### Your task

1. Analyze the question carefully.  
2. Identify all meaningful variables (unknown entities) that **MUST** be resolved to answer it.  
   Do not include variables that are not explicitly mentioned in the question.  
3. For each variable, describe:
   - What kind of entity it represents (`type`)  
   - The clues or relationships that connect it to other entities or facts in the question (these go in `question_clues`)  
4. Do **not** attempt to guess any candidate values yet.  
5. Initialize `discovered_clues` as an empty list for all variables.

---

### Output format

Think step by step inside the `"reasoning"` field: break down the question, identify every entity that needs to be resolved, and explain how the entities relate to each other. Then output ONLY valid JSON matching the exact schema below.

{
  "reasoning": "<your step-by-step analysis of the question, identifying the entities and their relationships>",
  "state": [
    {
      "id": <unique integer starting from 1>,
      "type": "<entity type, e.g. Person, City, Book, University>",
      "question_clues": ["<clue or relationship derived from the question>"],
      "discovered_clues": [],
      "candidate": null,
      "candidate_strength": null
    }
  ],
  "answer_variable": <integer id of the variable that directly answers the question>
}

Rules:
- Do NOT include any explanation or extra text outside the JSON
- Do NOT use markdown formatting
- Do NOT wrap the JSON in code fences
- The output must be directly parseable by a JSON parser
- `candidate` MUST be null for all variables
- `candidate_strength` MUST be null for all variables
- `discovered_clues` MUST be an empty list [] for all variables
- `answer_variable` MUST match one of the variable `id` values in the `state` list
