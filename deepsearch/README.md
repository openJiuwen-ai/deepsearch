**Read this in:** English | [简体中文](./README_zh.md)

# 🔬 What is openJiuwen-DeepSearch?

**openJiuwen-DeepSearch** is a knowledge-augmented, high-performance, high-precision deep retrieval and research engine. It combines structured knowledge and large language models with tools to deliver enterprise-grade Agentic AI search and research. Built on **openJiuwen agent-core**, it coordinates multiple agents for query planning, information gathering, understanding and reflection, and report generation—tackling complex reasoning and research workloads.

## Use cases

openJiuwen-DeepSearch provides deep search and deep research for enterprises and end users. This release focuses on **deep research**: multi-step workflows, multi-source validation, rigorous reasoning, and structured output for professional or high-stakes decisions.

- **Financial analysis reports**: Connect local investment and finance knowledge bases and web-augmented search to plan tasks, gather and analyze information (e.g. *“Impact of Fed rate cuts in 2025 on A-share tech stocks”*), and produce investment and finance reports.
- **Academic and policy research**: Use local or web-augmented sources for policies and implementation details, then plan, collect, analyze, and generate reports (e.g. *“Impact of China’s ‘new quality productive forces’ policy on manufacturing SMEs”*).

## Core capabilities

- **Example-driven report generation**
  - Start from a report template or extract structure from a sample report, then generate similar reports.
  - Samples can be Markdown, HTML, Word, PDF, etc.; templates can be exported.

- **Knowledge-augmented hybrid retrieval**
  - Local knowledge bases with keyword, vector, graph, and hybrid retrieval.
  - Hybrid retrieval across local corpora and the open web.
  - Online knowledge construction, evaluation, and refinement to improve fused search quality and reduce context cost.

- **Collaborative and interactive**
  - Natural-language feedback during planning.
  - Collaborative revision based on user feedback.

- **Segment-level provenance**
  - Validated citations in outputs and reports; preview and open sources.
  - Segment-level traceability and confidence.
  - Provenance reasoning and visualization for key claims.

- **Rich reports with visuals**
  - Reports with figures and charts; content remains traceable.
  - Markdown output and export to Word, HTML, and other formats.

## System architecture

The diagram below outlines the architecture. openJiuwen-DeepSearch is built mainly on **openJiuwen agent-core** and can connect to different LLMs and tools.

DeepSearch includes a manager, query planning, knowledge retrieval, understanding and analysis, and result generation:

![Architecture](./docs/zh/images/architecture.png)

- **Manager**: Agent creation, workflow orchestration, and configuration on the agent-core framework so agents coordinate efficiently.
- **Query planning**: Intent-based routing, structural planning, task decomposition, query rewriting, and related understanding to capture user intent and schedule work.
- **Knowledge retrieval**: Offline knowledge construction (parsing, chunking, index building) and online retrieval (keyword inverted index, vector search, knowledge-graph search, hybrid modes), plus pluggable web search.
- **Understanding and analysis**: Evaluate, refine, expand, and fuse retrieval results and other context.
- **Result generation**: Answers, report generation, interactive editing, and provenance.

**Abbreviations**

- **agent-core**: openJiuwen agent-core  
- **DeepSearch**: openJiuwen-DeepSearch  

# 📦 Installation

This section points to the installation guide so you can deploy on common platforms.

## Installation guides

DeepSearch is installed via the SDK, covering quick deployment, custom builds, integration, and source-level debugging:

- [DeepSearch SDK installation](./docs/en/2.Installation%20Guide/DeepSearch_SDK/README.md)

### Docker Compose one-click deployment

From the `deepsearch/docker/` directory, use Docker Compose to bring up the multi-service stack:

**Important**: Configuration file location
```
📁 deepsearch/
  ├── 📁 docker/
  │   ├── .env            ← Must be here (copy from ../.env.example)
  │   ├── docker-compose.yml
  │   └── docker-compose.full.yml
  └── .env.example        ← Template file
```

```bash
cd deepsearch/docker
cp ../.env.example .env                # create deepsearch/docker/.env; edit to fill in LLM / search credentials

# Choose one of three modes:
docker compose up -d                                      # minimal: quick eval (sqlite + in_memory)
docker compose -f docker-compose.full.yml up -d           # full: single-node prod (MySQL + Milvus + persistence)
docker compose -f docker-compose.distributed.yml up -d --scale deepsearch=3  # distributed: multi-instance (Redis + OBS)
```

**Note**: Docker Compose sets `BACKEND_PORT=8000` (fixed, container-side), different from `.env.example`'s `BACKEND_PORT=6000` (local source run). The `.env` file must live in `deepsearch/docker/` (same directory as the compose files). See [Docker Installation](./docs/en/2.Installation%20Guide/DeepSearch_SDK/Docker%20Installation/README.md).

More navigation: [Documentation hub](./docs/README.md).

# 🚀 Quick start

👉 For a full demo video, download the [complete video](https://openjiuwen-ci.obs.cn-north-4.myhuaweicloud.com/deepsearch/readme/9e6e857a424167500d4b4277485ea9b1_raw.mp4).

**_Note:_**

* **_Verified models: Qwen3.7-Max (recommended), Qwen3.8-Flash, Qwen3.7-Plus, Qwen3-Max, GLM-5.3, GLM-5.3-Flash, GLM-5.2, GLM-5.1, GLM-5, DeepSeek V3.2, Kimi-K2.5._**
* **_It is recommended to use a more powerful model to generate the report, so as to balance output quality and generation stability. If the model’s capability or concurrency handling is insufficient, it may affect the quality or completeness of the report._**
* **_Thinking models involve more complex reasoning and analysis, so report generation takes significantly longer. If generation speed matters, prefer non-thinking models._**

# 💻 Developer guide

To work from source or extend DeepSearch, see the [Developer Guide](./docs/en/3.Developer%20Guide/README.md). Contributions are welcome.

**Note:** Except when resuming the **same** task (e.g. HITL clarification, outline interaction, cancellation), each call to the DeepSearch SDK **`run`** API should use a **new** `conversation_id`. Do not reuse a `conversation_id` across unrelated runs.

# ❓ FAQ

[FAQ](./docs/en/4.FAQ/README.md).

# ⚖️ License

This project is licensed under **Apache 2.0**. See the [LICENSE](LICENSE) file.

# 🤝 Contributing

Issues and pull requests are welcome. See the [contribution guide](https://www.openjiuwen.com/contribute).

This product serves solely as a workflow orchestration tool and does not embed any AI model capabilities. When users integrate AI models for specific business scenarios, they shall bear full responsibility for compliance obligations under the EU AI Act and other relevant regulatory frameworks.
