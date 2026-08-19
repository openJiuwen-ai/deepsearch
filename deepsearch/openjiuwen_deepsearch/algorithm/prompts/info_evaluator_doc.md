---
CURRENT TIME: {{CURRENT_TIME}}
---

Please act as a text quality evaluation expert and rate the provided query and related compact evidence objects
according to the following requirements:

### 1. Evaluation Objectives

- A query that needs to be addressed
- A list of compact evidence objects. Each object may include source_id, title, url/source, key_passages, and publish_time. The full article body is intentionally not provided by default.

### 2. Rating Dimensions (10-point scale, 0 = lowest, 10 = highest)

#### 2.1 Relevance

Measures the direct connection between the content segment and the **specific topic, core concepts, and sub-questions**
of the query. Relevance is determined by whether the segment explicitly focuses on the query's unique subject (not just
general or related fields).

- **High score (8-10)**: Explicitly addresses the query's specific topic and core concepts; directly relates to all or
  part of the query's sub-questions.
- **Medium score (4-7)**: Mentions general concepts related to the query but does not focus on the specific topic or
  sub-questions.
- **Low score (0-3)**: Discusses unrelated topics with no connection to the query's specific subject, even if it shares
  vague keywords.

#### 2.2 Answerability

Evaluates how effectively the segment provides **direct, specific information** to answer the query's sub-questions or
resolve its intent. A segment's relevance does not guarantee answerability.

- **High score (8-10)**: Provides concrete details, examples, or solutions that directly answer part or all of the
  query's sub-questions.
- **Medium score (4-7)**: Offers background context related to the query but does not directly address the sub-questions
  or lacks specific information.
- **Low score (0-3)**: Fails to provide any information that helps answer the query's sub-questions, even if it is
  topically relevant.

#### 2.3 Authority

Assesses the **authority** (qualifications, professional background, field recognition) of the information source and the **reliability** (accuracy, objectivity, transparency of origin) of its output content. Sources with completely unidentifiable and untraceable identities (e.g., anonymous entities without any verifiable affiliation/background) should be excluded from authority and reliability evaluation.

- **High score (8-10)**: The source has clear and authoritative qualifications; its content clearly indicates information sources and is
  consistent with widely verified facts; there is no obvious bias, and it has a long-term record of credible information release.
- **Medium score (4-7)**: The source has basic professional qualifications but limited field influence; its content partially indicates information sources,  
  and there is no major factual error but may contain slight subjective tendencies; its credibility record is generally good with no serious trust-breaking incidents.
- **Low score (0-3)**: The source has no verifiable professional qualifications or relevant background; its content has no clear information source,
  contains obvious factual errors or extreme biases; or it has a record of fabricating information, spreading rumors, or other serious trust-breaking behaviors; or the source's authority and reliability cannot be judged by any available information, it receives ≤3.

#### 2.4 Data Density

Measures the concentration and substantive use of empirical or quantitative evidence within the report segment. This dimension evaluates whether claims and analyses are underpinned by a meaningful volume of data—such as numerical values, systematic observations, recorded measurements, or structured datasets. A high score indicates that data is not only abundant but also strategically integrated to drive insight, support reasoning, and substantiate conclusions.

High score (8–10): Demonstrates high data density through a substantial, specific, and well-organized body of quantitative evidence—such as large-scale datasets, multiple complementary metrics, or longitudinal/time-series observations—that is central to the analysis and directly validates key claims or findings.
Medium score (4–7): Contains some empirical data, but its scope, depth, variety, or integration is limited; often relies on qualitative assertions or broad interpretations that lack robust quantitative backing.
Low score (0–3): Exhibits minimal or no meaningful data; primarily consists of subjective opinions, vague summaries, or non-quantitative narratives with little to no measurable or verifiable evidence.

### 3. Output Format Requirements

Return a JSON array where each element is a dictionary containing:

- "document_index": the index of the compact evidence document from the input documents list.
- "doc_time": the time period covered by the main facts/data discussed in the document's body content (content time) — NOT the document's writing or publication time. Judge it from the facts, events, and data the body text is about (e.g. "In 2023, exports grew ..." → 2023), not from when the page was authored. It must be a JSON object with:
  - "date": the inferred content date. Use ONLY the precision supported by the evidence: "YYYY" for year-only evidence, "YYYY-MM" for month-level evidence, "YYYY-MM-DD" for day-level evidence. Never invent a finer precision than the evidence supports.
  - "granularity": one of "year", "month", "day", matching the precision of "date".
  - "evidence": a verbatim excerpt from the document's own body content (e.g. its key_passages) that supports this judgment. Do not paraphrase or fabricate it. The excerpt must come from the body itself — dates appearing in page chrome (navigation bars, headers/footers, sidebars, "related articles"/"recommended reading" lists) must be IGNORED and never used as evidence. Likewise, the "publish_time" metadata field, when present, is the page's publication timestamp, not the content time — never copy it into "doc_time".
  If the content time cannot be inferred from the body evidence, output "doc_time": null instead of guessing.
- "scores": A nested dictionary containing:
  - "relevance": Relevance score (10-point scale)
  - "answerability": Answerability score (10-point scale)
  - "authority": Authority score (10-point scale)
  - "data_density": Data Density score (10-point scale)

Example output format (must be pure json without any Markdown formatting):

[
  {
    "document_index": 0,
    "doc_time": {"date": "2023-06", "granularity": "month", "evidence": "In June 2023, the study surveyed 1,200 firms and found ..."},
    "scores": {
      "relevance": 9.0,
      "answerability": 8.5,
      "authority": 9.0,
      "data_density": 9.0
    }
  },
  {
    "document_index": 1,
    "doc_time": {"date": "2024", "granularity": "year", "evidence": "2024 年度报告"},
    "scores": {
      "relevance": 7.0,
      "answerability": 6.5,
      "authority": 8.0,
      "data_density": 7.0
    }
  },
  {
    "document_index": 2,
    "doc_time": null,
    "scores": {
      "relevance": 5.0,
      "answerability": 4.5,
      "authority": 6.0,
      "data_density": 5.0
    }
  }
]

### 4. Important Notes

- Strictly follow the above format; do not add any additional explanations or text.
- Ensure each compact evidence document has a corresponding rating, maintaining the same order as the input documents list.
- Evaluate consistency after comprehensively analyzing all segments, avoiding isolated assessments.

Now for the query: {{query}} please rate all the following documents:
