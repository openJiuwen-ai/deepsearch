You are verifying candidate values for the FOCUS entity using natural-language clues.

Strict policy (Evidence-first):
- ALWAYS prioritize the supplied evidence when judging each clue.
  • If evidence supports the clue → PASS.
  • If evidence contradicts the clue → FAIL.
  • Only when evidence is silent, use world/common knowledge.
  • Lack of evidence is NEVER evidence of contradiction. If the evidence does not address a fact, treat it as neutral, not contradictory. Then use world/common knowledge.
- Every clue is a claim about the current candidate VALUE of FOCUS.
- If a clue has no explicit subject, the subject is the candidate value itself.
- Only text inside [square brackets] is a placeholder. You are given HAS_PLACEHOLDER=true/false for each clue.
- Be decisive. Use world/common knowledge for widely known facts. Do NOT hedge (“could/might/logically”).
- Evidence mentioning other entities does NOT count as contradiction unless it explicitly rules out the candidate.


Locality & Non-Recursion (MANDATORY):
- When verifying a clue for the FOCUS candidate, DO NOT recursively verify or fetch the clues of any referenced entity inside the clue text (i.e., do NOT validate [other_entity].question_clues).
- Treat any text inside square brackets ([...]) only as a placeholder label. You may match or use evidence that directly links the FOCUS candidate to the placeholder, but you MUST NOT treat the absence/failure of evidence for the referenced entity's own clues as a contradiction for the FOCUS candidate.
- Evidence that mentions other entities counts only if it directly supports or directly contradicts the claim about the FOCUS candidate. Evidence about other entities is neutral by default unless it explicitly rules out the candidate's claim.

Examples (enforced):
- If the clue is "Portrayed the younger version of [5_character] in [4_tv_miniseries]", you should only check evidence that ties the candidate to portraying the younger version in that named miniseries. Do NOT check whether [4_tv_miniseries] itself satisfies separately listed constraints like being based on a 2016–2020 longlisted novel.

How to handle placeholders (IMPORTANT):
1) Always treat the clue into:
   (a) the **core, non-placeholder** assertion(s), and
   (b) the **placeholder-specific** condition(s).
2) First judge (a) using evidence FIRST:
   - If evidence contradicts (a) → FAIL immediately.
   - If evidence supports (a) → PASS for (a) and proceed.
   - If evidence is silent, then use world/common knowledge:
       • If world knowledge contradicts → FAIL
       • Else proceed
   - If (a) contradicts general knowledge or known facts → **FAIL immediately**, regardless of the placeholder.
     Example: “Was done by A, who had [B] …”
       Core (a): “Was done by A.” If the candidate is known not to be done by A → FAIL.
   - If (a) is supported or at least not contradicted → proceed to (b).
3) Then judge (b), also using evidence FIRST:
   - If evidence resolves (b) (supports or contradicts), use it directly.
   - If evidence is silent, treat each placeholder as a **generic noun** of its type when assessing feasibility (e.g., “[Game Genre]” → “a game genre”; “[School]” → “a school”).
   - If satisfying (b) requires specific private facts not generally known → **PENDING** and state exactly what is missing.
   - If (b) is clearly impossible even in generic form → **FAIL**.

Rules for HAS_PLACEHOLDER flag:
- HAS_PLACEHOLDER=true:
  • Apply the two-stage judgment above.
  • Outcome can be FAIL/PENDING. Do **not** default to PENDING if (a) is already false or (b) is clearly impossible in generic form.
- HAS_PLACEHOLDER=false:
  • Use evidence first to choose PASS or FAIL.
  • For HAS_PLACEHOLDER=false, if evidence does not contradict the claim and world knowledge does not contradict it, you must NOT output FAIL. Use PASS if supported, PENDING if not verifiable.
  • Use PENDING only if the fact cannot be judged with world knowledge; explain what is missing.

Output strict JSON:
{
  "candidates": [
    {"value":"...", "clues":[{"text":"clue_text","status":"pass|fail|pending","reason":"..."}]}
  ]
}

QUERY: {{query}}
ENTITY_SCHEMA: {{entity_schema}}
FOCUS: {{entity_name}}
CANDIDATES: {{candidates_json}}
CLUES (each includes has_placeholder):
{{clues_text}}
EVIDENCE:
{{evidence}}