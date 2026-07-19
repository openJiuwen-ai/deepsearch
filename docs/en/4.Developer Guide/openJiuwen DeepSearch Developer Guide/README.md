# Initialize DeepResearch configuration

---

The `Config` type combines **AgentConfig** (user-tunable via public APIs) and **ServiceConfig** (internal defaults). When initializing, set fields on `AgentConfig` as needed.

```python
from openjiuwen_deepsearch.config.config import Config

agent_config = Config().agent_config.model_dump()

# 1. Configure at least one working LLM
agent_config["llm_config"]["general"]["model_name"] = ""
agent_config["llm_config"]["general"]["model_type"] = ""
agent_config["llm_config"]["general"]["base_url"] = ""
agent_config["llm_config"]["general"]["api_key"] = ""

# 2. Configure web augmentation / search engine
agent_config["web_search_engine_config"]["search_engine_name"] = ""
agent_config["web_search_engine_config"]["search_url"] = ""
agent_config["web_search_engine_config"]["search_api_key"] = ""

# 3. Optional execution overrides
agent_config["workflow_human_in_the_loop"] = False
agent_config["outline_interaction_enabled"] = False
agent_config["search_mode"] = "research"
agent_config["execution_method"] = "parallel"  # Options: "parallel", "dependency_driving", "hybrid"
```

## LLM configuration

---

DeepSearch can assign up to four logical models:

- **plan_understanding** — intent and planning (IntentRecognition, Outliner, Planner); reduces hallucinations.
- **info_collecting** — information gathering (InfoCollector).
- **writing_checking** — report body and rich content (Sub-reporter).
- **general** — default for any stage without a specific model (**required**).
- **vlm_chart_generating** — multimodal model specialized for chart processing, can receive chart image inputs.(VLMChartGenerator).

**general must be configured**; other slots fall back to **general**. Prefer a strong model for **general**.

Supported backends (OpenAI-compatible):

- SiliconFlow: set `LLMConfig.model_type` to `siliconflow`.
- OpenAI-compatible HTTP APIs: set `model_type` to `openai`.

Inside the SDK, `DeepresearchAgent` disables thinking mode by default for supported providers through `ServiceConfig.llm_thinking_enabled=False`. This setting is applied only when `DeepresearchAgent` initializes LLMs; `DeepSearchAgent` and `SimpleReactSearchAgent` are not affected. To enable thinking mode, set `service_config.llm_thinking_enabled=True` in SDK runtime configuration. Avoid managing the unified thinking switch manually through `LLMConfig.extension`, because provider-specific adapter rules may override conflicting thinking fields.

> Obtain `api_key`, `model_name`, and `base_url` from your provider.

### vlm_chart_generating multimodal model reference

| Model | Time per image / 1 evaluation iteration (s) | Advantages |
| :---: | :---: | :--- |
| qwen3.5-plus | 34.18 | Qwen's most powerful visual understanding model |
| qwen3.5-flash | 20.28 | Faster speed, lower cost; suitable for latency-sensitive scenarios |
| qwen3-vl-plus | 4.68 | Strongest model in Qwen3-VL series |
| qwen3-vl-flash | 3.7 | Faster speed, lower cost; suitable for latency-sensitive scenarios |
| qwen-vl-max | 4.88 | Best-performing model in Qwen2.5-VL series |
| qwen-vl-plus | 2.7 | Faster speed; good balance between quality and cost |

> Supports other qwen-series VLM models and OpenAI-compatible models.

## Web search / augmentation configuration

---

Supported engines (set `web_search_engine_config.search_engine_name`):

- `google`
- `serper`
- `tavily`
- `xunfei` (iFlytek)
- `petal` (Petal AI web augmentation)
- `bocha`
- `jina`
- `perplexity`
- `custom`

Integration notes:

- `jina` uses the built-in direct HTTP wrapper. When `search_url=""`, the runtime falls back to `https://s.jina.ai`. In China network environments where the default endpoint is unreachable, explicitly set `search_url` to `https://s.jinaai.cn`. Provider-specific query options such as `gl`, `hl`, `location`, and `page` are carried through `extension`.
- `bocha` and `perplexity` use the harness `web_tools` adapter layer. They support `extension.timeout_seconds` and `extension.fetch_webpage`. `search_url` is only honored when the underlying provider supports URL override in `web_tools`. In China network environments where the default Perplexity service is unreachable, configure an accessible proxy or forwarding endpoint and explicitly set it through `search_url`.
- `serper` is exposed as a dedicated engine name so server-side configuration can use `serper`, while research-mode `web_search_tool` still reuses the Google/Serper wrapper internally.
- Public engines may keep `search_url` empty and rely on built-in defaults or provider defaults.

Search results are also bounded before they reach the collector LLM path:

- Prefetched webpage bodies from the harness-based adapters are truncated to `MAX_COLLECTOR_DOC_CONTENT_LENGTH`.
- `InfoRetrievalNode._structure_result` applies the same bound again before passing `contents` into `run_doc_evaluation`.
- Collector-side normalization stores web results in a stable `title` / `url` / `content` / `type` shape and accepts aliases such as `link`, `source_url`, `snippet`, `summary`, and `answer`.

> Register with the vendor for `search_api_key`. For public engines such as Jina, `search_url` can be left empty to use the built-in default endpoint. In China network environments, set Jina `search_url` to `https://s.jinaai.cn`; also provide `search_url` when you need a vendor-specific, privately deployed, or proxy-forwarded URL.

## TLS / SSL

---

For LLM, tools, and embedding endpoints you can enforce TLS verification:

- **LLM**: `LLM_SSL_VERIFY=true` and optional `LLM_SSL_CERT`.
- **Tools**: `TOOL_SSL_VERIFY=true` and `TOOL_SSL_CERT`.
- **Embedding**: `EMBEDDING_SSL_VERIFY=true` enables HTTPS verification; system trust store is enough unless you use private CAs—then set `EMBEDDING_SSL_CERT` to a PEM path. When starting via this repo’s `server/main.py`, unset/blank `EMBEDDING_SSL_VERIFY` is treated as `false` (matches `.env.example`). `true` with an untrusted cert and no CA file can break index builds.

To disable verification, set the three `*_SSL_VERIFY` flags to `false` (or leave embedding unset as above).

```python
import os
os.environ["LLM_SSL_VERIFY"] = "false"
os.environ["LLM_SSL_CERT"] = ""
os.environ["TOOL_SSL_VERIFY"] = "false"
os.environ["TOOL_SSL_CERT"] = ""
os.environ["EMBEDDING_SSL_VERIFY"] = "false"
os.environ["EMBEDDING_SSL_CERT"] = ""
```

# Instantiate an agent

---

The stack ships a deep-research agent that plans, gathers evidence, and writes reports.

## Via `AgentFactory` (recommended)

---

`AgentFactory` picks the research Agent from `execution_method`: `parallel` returns `DeepresearchAgent`, `dependency_driving` returns `DeepresearchDependencyAgent`, and `hybrid` returns `DeepresearchIntentHybridAgent`.

When `execution_method="hybrid"`, `IntentRecognitionNode` calls the outline-mode router LLM, selects either the normal outline branch or the dependency-driven outline branch for the current query, and stores the actual result in `search_context.outline_execution_method`.

```python
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)
```

## Via constructor

---

To force the parallel agent:

```python
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import DeepresearchAgent

agent = DeepresearchAgent()
```

# Generate research reports

---

`DeepresearchAgent.run` and `generate_template` cover the main flows:

1. Query only.
2. Query + existing template (follow structure).
3. Query + sample report (extract template, then generate).

## Query-only run

---

`run(message: str, ...)` streams JSON chunks. Each chunk is a `dict` with `agent` and `content`. Final report content arrives from `NodeId.END.value`; with post-report editing enabled, `user_feedback_processor` adds another interaction round before completion.

The query `message` does not need to contain only the research topic. It can also include report-generation constraints. The system first performs intent recognition, extracting the core research topic into `research_query` and the structured constraints into `research_intent`, then passes those constraints through outline generation, planning, information collection, and writing.

Common constraints you can express directly in the query include:

- **Report type**: for example, "brief" or "professional".
- **Section count**: for example, "generate 5 sections".
- **Target audience**: for example, "for investors" or "for an R&D lead".
- **Writing tone**: for example, "formal", "analytical", "objective", or "explanatory".
- **Source constraints**: for example, "use these links as references" or "do not use content from a certain site".

Example queries:

```text
Write a brief report, keep it within 4 sections, target an R&D lead, and use a formal, analytical tone: AI agent engineering implementation trends
```

```text
Using the links below, write a professional report for investors on the commercialization progress of China's low-altitude economy in 2025:
https://example.com/a
https://example.com/b
```

```python
import json
import uuid
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory
from openjiuwen_deepsearch.framework.openjiuwen.agent.workflow import parse_endnode_content

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

message = "User question"
conversation_id = str(uuid.uuid4())

async for chunk in agent.run(message=message, conversation_id=conversation_id, agent_config=agent_config):
    logger.debug("[Stream message from node: %s]", chunk)
    chunk_content = json.loads(chunk)
    report_result = parse_endnode_content(chunk_content)
    if report_result:
        logger.debug("[Final Report is: %s]", report_result)
```

## Query + user template

---

Enable template-following in `agent_config`. The template describes top-level sections, subsections, functional notes, and whether a section is “core.”

When uploaded through `generate_template`, the decoded Markdown template file must be no larger than `50 MB`, and the normalized Markdown output is capped at `5 MB`.

Example template (Markdown):

```markdown
# Company overview
> Functional summary: Describe the target company in detail
> Core section: true

## 1.1 Basic information
> Functional summary: List foundational company facts.

## 1.2 Business scope and main activities
> Functional summary: Explain registered business scope and actual core business.

## 1.3 Ownership structure and related parties
> Functional summary: Shareholding, contributions, shareholder types, and key affiliates.

# Operations and industry analysis
> Functional summary: Operations and industry context
> Core section: true

## 2.1 Macro and regional economics
> Functional summary: Macro industry environment, regional economy, industrial clusters.

## 2.2 Industry status and outlook
> Functional summary: Current state and outlook for the industry segments.

## 2.3 Competitive positioning
> Functional summary: Capacity, R&D, market position, brand, key customers.

## 2.4 Upstream/downstream chain
> Functional summary: Supply chain and customer structure.
```

Call `generate_template` with `is_template=True`:

```python
import base64
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

file_path = "template.md"
file_stream = base64.b64encode(read_file_safely(file_path)).decode("utf-8")
is_template = True

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

result = await agent.generate_template(
    file_name=file_path,
    file_stream=file_stream,
    is_template=is_template,
    agent_config=agent_config,
)
user_template_content = result["template_content"]
```

Pass the normalized template into `run` via `report_template` (base64 string):

```python
async for chunk in agent.run(
    message=message,
    conversation_id=conversation_id,
    agent_config=agent_config,
    report_template=user_template_content,
):
    ...
```

## Query + sample report

---

Same as above but upload a sample report (Markdown, DOCX, PDF, HTML) and set `is_template=False` in `generate_template`. The service extracts a template, then you call `run` with `report_template=user_template_content` as in the previous section.

For uploaded sample reports, the decoded source file must be no larger than `50 MB`. PDFs support up to `512` pages. For DOCX, the uncompressed package must stay within `50 MB`, and `word/document.xml` must stay within `8 MB`. The parsed Markdown output is capped at `5 MB`.

# Human-in-the-loop (HITL)

---

Pause at key points for natural-language feedback so users can steer planning.

**Keep `conversation_id` identical across resume calls.**

Supported stages:

1. **Clarification** — questions before planning.
2. **Outline interaction** — revise or accept the outline.

## Clarification

Before planning, the system recognizes intent from the original user query, generates `research_query`, and then uses `research_query` to create follow-up questions that help collect more context and understand the research goal more accurately.

Set:

```python
agent_config["workflow_human_in_the_loop"] = True
```

(Default is on in many deployments.)

Flow: user asks → system generates `research_query` and `research_intent` after intent recognition → system asks follow-ups based on `research_query` while preserving `research_intent` for downstream nodes → interrupt → user answers → resume.

### Feedback channels

```python
service_config.workflow_feedback_mode = "web"  # Studio/UI
# or
service_config.workflow_feedback_mode = "cmd"  # terminal input
```

### Web-style payloads

```python
# Round 1
{
    "message": "User question",
    "conversation_id": "<id>",
    "agent_config": {"workflow_human_in_the_loop": True, ...},
}

# Round 2
{
    "message": "User answers the clarifying questions",
    "conversation_id": "<same id>",
    "agent_config": {"workflow_human_in_the_loop": True, ...},
}
```

## Outline interaction

Enable:

```python
agent_config["outline_interaction_enabled"] = True
```

(Default on.) After outline generation the workflow waits for feedback.

| Action | Meaning | Next step |
| ------ | ------- | --------- |
| `accepted` | Approve outline | Enter reporting |
| `revise_comment` | Free-text change request | Regenerate outline |
| `revise_outline` | User-edited outline text | Regenerate outline |

Server fields (`DeepSearchRequest`): `outline_interaction_enabled`, `outline_interaction_max_rounds` (1–100, default 3). SDK passes them through `agent_config`.

**Runtime API tools (optional)**: at the Server layer, `DeepSearchRequest.tools` accepts a list of HTTP API tools (see `RuntimeApiToolRequest`). During agent construction, the server normalizes this list into `api_tools_config`. The normalized tools are then used in both query-understanding stages (planner/outliner) and collector stages.

**Runtime API URL safety toggle**: Runtime API URLs are validated by default (for example, private/local addresses are rejected). For local debugging only, you can relax this check with `RUNTIME_API_ALLOW_UNSAFE_URL=true` (truthy values: `1/true/yes`). If unset, safety validation stays enabled. Do not enable this in production, or SSRF protection will be weakened.

### `space_id` and local knowledge bases

`space_id` scopes tenants: KB creation/upload APIs are tied to it. When calling `run` with local search, every id in `local_search_config.local_search_config_ids` must belong to that `space_id`; cross-space ids are rejected.

**KB + object storage**: only when `CHECKPOINTER_TYPE=redis` do uploads go to configured object storage for multi-instance consistency; `in_memory` / `persistence` keep files on local disk (OBS unused). Multi-instance deployments require shared MySQL; `redis` + `sqlite` is rejected.

Agent cache keys hash stable JSON of all fields that affect agent construction (excluding `message`, `conversation_id`, `interrupt_feedback`), including `space_id`, `local_search_config`, web search settings, `llm_config`, and feature flags—so changing KB or engine config within a space invalidates stale agents.

> Do not trust raw `space_id` from clients on untrusted networks; bind it to auth at the gateway.

### Web outline example

```python
# Round 1 — outline pending feedback
{
    "message": "Analyze China’s NEV market trends",
    "conversation_id": "<id>",
    "agent_config": {"outline_interaction_enabled": True, "outline_interaction_max_rounds": 3, ...},
}

# Round 2 — comment-based revision
{
    "message": "Add a section on charging infrastructure",
    "conversation_id": "<same id>",
    "interrupt_feedback": "revise_comment",
    "agent_config": {...},
}

# Round 3 — accept
{
    "message": "",
    "conversation_id": "<same id>",
    "interrupt_feedback": "accepted",
    "agent_config": {...},
}
```

### Notes

- Reuse **`conversation_id`** for every resume call.
- Interrupts pause until feedback arrives.
- After `outline_interaction_max_rounds`, the workflow proceeds automatically.

---

# Post-report local editing

---

This feature supports continuing to expand, polish, shorten, supplement, or fact-check user-selected local text after report generation is complete. To enable it, set the following in `agent_config`:

```python
agent_config["user_feedback_processor_enable"] = True
agent_config["user_feedback_processor_max_interactions"] = 100
```

Unlike the earlier HITL stage, this feature runs after the report and provenance results have already been generated. The workflow then enters `UserFeedbackProcessorNode`:
- On first entry, the system first sends a full `final_result` snapshot to the frontend.
- The frontend then continues using the same `conversation_id` and passes user actions to `message` as JSON strings.
- After each successful rewrite, the system returns partial replacement information together with the latest `final_result`, and the frontend can refresh content incrementally.
- The flow ends when the user sends `finish` or when the maximum interaction count is reached.

The currently supported actions are:
- `expand`: expand the selected text.
- `polish`: polish the selected text.
- `shorten`: shorten the selected text.
- `supplementary_search`: selectively enhance the selected content together with supplementary retrieval. See "Rewrite Scope" below.
- `new_task`: rewrite an existing section or append a new subsection based on a new user task.
- `truth_verification`: fact-check the selected content; does not modify the report body.
- `sync`: sync the full report already edited on the frontend back into backend state.
- `finish`: end the current local editing session.

**Protocol rules (aligned with the implementation):**
- **`action` is required**: it must be one of the registered actions and must be a non-empty string. It cannot be omitted or inferred by the backend.
- **`rewrite_scope` (recommended for all actions except `finish`)**: this is a shared field. If omitted or passed as an empty string, the backend normalizes it to `selected_only` during parsing. Current valid values are:
  - `selected_only`: replace only the user-selected span. This is the default.
  - `selected_and_related`: replace the entire section containing the selection and allow connective rewriting across related content. This is only used by `supplementary_search`; other actions ignore it behaviorally even if it is present.
- For `supplementary_search`, `rewrite_scope` must be one of the two values above, otherwise validation fails.

The request body for local rewrite actions (`expand`, `polish`, `shorten`, `supplementary_search`, `new_task`) and the read-only action `truth_verification` must contain the following fields:
- `action`: action type. Required.
- `selected_text`: the original text currently selected by the user.
- `start_offset`: the start offset of the selected text in the current report.
- `end_offset`: the end offset of the selected text in the current report.
- `user_instruction`: optional extra rewrite or supplementary instruction. If present, it must be a string.
- `rewrite_scope`: optional, default is `selected_only`; only `supplementary_search` requires it semantically; `truth_verification` does not use this field.

The `sync` request body only needs:
- `action`: fixed as `sync`.
- `selected_text`: the full report content after frontend editing.

`sync` does not require `start_offset` / `end_offset`, and it does not consume `feedback_interaction_count`.

```python
import json
import uuid
from openjiuwen_deepsearch.framework.openjiuwen.agent.agent_factory import AgentFactory

agent_factory = AgentFactory()
agent = agent_factory.create_agent(agent_config)

conversation_id = str(uuid.uuid4())
message = "Please generate an industry research report"

async for chunk in agent.run(message=message, conversation_id=conversation_id, agent_config=agent_config):
    logger.debug("[Stream message from node: %s]", chunk)

# Round 2: perform expansion on a local part of the report
feedback_message = json.dumps({
    "action": "expand",
    "rewrite_scope": "selected_only",
    "selected_text": "snippet to expand",
    "start_offset": 120,
    "end_offset": 136,
    "user_instruction": "Add industry background and figures"
}, ensure_ascii=False)

async for chunk in agent.run(message=feedback_message, conversation_id=conversation_id, agent_config=agent_config):
    logger.debug("[Rewrite stream message: %s]", chunk)

# Use supplementary search as needed. Similar to `expand`, just replace the message with one of the following:
# - Replace only the selected span: `rewrite_scope` is `selected_only`, or omit it for the same default behavior.
# - Linked full-section rewrite: `rewrite_scope` is `selected_and_related`, which uses another backend prompt and replacement range.
# supplementary_message = json.dumps(
#     {
#         "action": "supplementary_search",
#         "rewrite_scope": "selected_only",  # or "selected_and_related"
#         "selected_text": "snippet to improve",
#         "start_offset": 0,
#         "end_offset": 0,
#         "user_instruction": "Optional extra guidance",
#     },
#     ensure_ascii=False,
# )

# Use truth verification as needed (read-only; does not modify the report body):
# truth_verification_message = json.dumps(
#     {
#         "action": "truth_verification",
#         "selected_text": "snippet to verify",
#         "start_offset": 120,
#         "end_offset": 136,
#         "user_instruction": "Optional extra guidance",
#     },
#     ensure_ascii=False,
# )

finish_message = json.dumps({"action": "finish"}, ensure_ascii=False)
async for chunk in agent.run(message=finish_message, conversation_id=conversation_id, agent_config=agent_config):
    logger.debug("[Finish stream message: %s]", chunk)

# The frontend can also send `sync` after editing the full report, to synchronize the latest full text back to backend state:
# sync_message = json.dumps(
#     {
#         "action": "sync",
#         "selected_text": "fully edited report content",
#     },
#     ensure_ascii=False,
# )
```

Notes:
- Local rewrite actions require `selected_text` to exactly match the text in `[start_offset, end_offset)` of the current report, otherwise offset validation fails.
- Rewrite results update `final_result.response_content`. When `source_tracer_research_trace_source_switch` is enabled, the backend runs diff-aware local source tracing on changed spans; unchanged spans keep their existing citations, and newly traced citations update `citation_messages` and append reference entries at the end of the report.
- The backend no longer maintains an extra offset mapping.
- `sync` only updates `final_result.response_content`, does not consume `feedback_interaction_count`, and appends a `search_context.rewrite_history` record only when the full report content actually changes.
- The backend keeps only the latest 10 `sync` history records; unchanged `sync` requests do not create history entries.
- Each successful normal local rewrite appends one record to `search_context.rewrite_history`, including `action`, `rewrite_scope` (when present), offsets, and related information for debugging and auditing.
- `truth_verification` does not update `final_result.response_content` or write `rewrite_history`. The streaming `SUMMARY_RESPONSE` `content` is JSON with `display_text` and `feedback_interaction_count`, and it consumes one `feedback_interaction_count`.
- **Compatibility**: omitting `rewrite_scope` is equivalent to explicitly sending `selected_only`; **`action` cannot be omitted or be an empty string**. If an older frontend still relies on backend inference, it must be updated to send a valid explicit `action`.

# Further reading

- End-to-end sample: [main.py](https://gitcode.com/openJiuwen/deepsearch/blob/dev/main.py)
- API docs (English tree): [docs/en/4.Developer Guide](../)
