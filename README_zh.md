**阅读语言：** [English](./README.md) | 简体中文

<h1 align="center">openJiuwen Search</h1>

<p align="center">
  <strong>从深度研究到代码搜索的全方位智能搜索解决方案</strong>
</p>

<p align="center">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-Apache--2.0-green.svg" alt="License" />
  </a>
  <img src="https://img.shields.io/badge/python-≥3.11-blue.svg" alt="Python Version" />
  <img src="https://img.shields.io/badge/os-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg" alt="OS Support" />
</p>


# 🌟 简介

**openJiuwen Search** 是 openJiuwen 开源项目中搜索技术相关能力的集合，提供从深度研究到代码搜索的全方位智能搜索解决方案。本仓库整合了多个专注于不同场景的搜索智能体，旨在为企业和开发者提供企业级 Agentic AI 搜索能力。

---

## 📦 仓库包含的子项目

### 🔬 DeepSearch - 深度检索与研究引擎

**DeepSearch** 是一款知识增强高性能、高精准深度检索与研究引擎。它有效利用结构化知识及大模型，融合各种工具，提供企业级 Agentic AI 搜索及研究能力。

**核心能力：**
- **深度研究（Deep Research）**：多步骤、多源验证、逻辑严谨的结构化研究报告生成
- **深度搜索（Deep Search）**：基于状态空间搜索机制的智能问答，支持多步推理和工具调用
- **知识增强融合检索**：支持本地知识库（关键词、向量、图谱检索）与联网增强引擎的融合检索
- **图文并茂报告生成**：支持包含图文可视化的报告生成，内容可溯源
- **片段级结果溯源**：输出结果具有经过校验的引用信息，支持片段级信息溯源及可信度评估
- **协同可交互**：支持在规划阶段与用户进行自然语言式反馈交互

**应用场景：**
- 金融分析研报：对接本地投资与金融知识库，生成投资及金融研报
- 学术与政策研究：获取相关政策信息、实施细则，生成研究报告
- 复杂问题搜索：解决需要多步骤、多源验证的专业决策场景

**详细文档：** [DeepSearch 项目文档](./deepsearch/README_zh.md)

---

### 💻 CodeSearch - 代码搜索智能体

**CodeSearch** 是一款面向代码仓库的智能检索引擎。输入一段问题描述（如 GitHub Issue、缺陷报告、功能需求），即可输出"要解决它应当查看哪些文件的哪些行"，为缺陷定位、代码问答与自动修复流水线提供精准的代码上下文。

**核心能力：**
- **智能体式多轮检索**：检索智能体自主决策（查看仓库结构、多策略搜索、扩展上下文、筛选提交），双模型协同控制成本
- **面向代码的混合索引**：语法感知切块（当前支持 Python），双路稀疏 BM25，支持增量索引与可选稠密向量检索
- **双引擎等价实现**：工作流图引擎（默认）+ 纯代码循环引擎，测试锁定输出逐字节一致
- **面向服务的工程能力**：支持 SDK、CLI、HTTP 服务与容器镜像多种形态

**应用场景：**
- 缺陷定位：将 Issue 转化为需要修改的具体函数与代码行
- 代码问答上下文供给：为"这个功能在哪实现"等问题提供精确到行的代码依据
- 大型仓库导航：在陌生的大型代码库中以自然语言换取相关代码切片

**快速上手：** [CodeSearch 快速指引](./codesearch/docs/zh/2.安装指导/快速指引.md)

**详细文档：** [CodeSearch 项目文档](./codesearch/README_zh.md)

---

### 🧰 openjiuwen-search-base - 搜索公共基础库

**openjiuwen-search-base** 是 openJiuwen 系列检索产品的**公共基础能力层**，为 DeepSearch、CodeSearch 等产品提供可复用的 LLM 接入、向量检索、工作流等基础组件。本包不依赖任何产品包，核心仅依赖 pydantic，重量级依赖以可选分组提供并使用受保护导入，可按需启用。

**核心模块：**
- **llm**：LLM 客户端协议、消息与工具调用的规范化模型、openJiuwen 模型适配（含 SSL 证书处理）、`LLMConfig`
- **embedding**：OpenAI 兼容的 embedding 客户端，支持本地 SQLite 缓存、有限重试与指数退避、持久连接复用
- **milvus**：查询表达式安全构造（统一转义）、集合命名约定（产品前缀 + 模式版本）、通用存取客户端
- **workflow**：工作流节点模板（三段式）与分支路由构造
- **logging_utils**：日志管理与敏感信息脱敏
- **runtime**：运行注册表，以 run_id 在工作流中传递，活对象不进入可复制的工作流状态

**设计要点：**
- **依赖方向**：base 不依赖任何产品包，产品依赖 base
- **可选重依赖**：核心导入路径不触碰 openjiuwen / pymilvus / aiohttp，未安装对应分组时仍可导入并测试纯逻辑部分
- **命名空间隔离**：集合名生成 `{前缀}{名称}__{模式版本}` 形式，使多个产品可安全共用同一 Milvus 实例

**快速安装：**
```sh
pip install -e .                            # 核心
pip install -e '.[workflow,milvus,embed]'   # 按需启用
```

**详细文档：** [base 项目文档](./base/README.md)

---

## 🚀 快速开始

### DeepSearch 快速上手

#### SDK 版本安装

适用于二次开发、定制化部署或源码级调试：

- [DeepSearch SDK 安装指导](./deepsearch/docs/zh/2.安装指导/DeepSearch_SDK/README.md)

**推荐模型配置：**
- **已验证模型**：Qwen3.7-Max（推荐）、Qwen3.8-Flash、Qwen3.7-Plus、Qwen3-Max、GLM-5.3、GLM-5.3-Flash、GLM-5.2、GLM-5.1、GLM-5、DeepSeek V3.2、Kimi-K2.5
- **建议**：使用性能较强的模型生成报告，以兼顾生成质量与调用稳定性
- **注意**：使用思考类模型时，报告生成耗时会显著增加，如对生成速度有要求，建议优先使用非思考模型

### CodeSearch 快速上手

```
# 索引一个本地仓库
pip install -e ../base -e '.[dev,milvus,llm,server]'
codesearch index --repo /path/to/your/repo --collection my_repo

# 用自然语言检索（需在 .env 配置 CODESEARCH_LLM_API_KEY 等）
codesearch search --collection my_repo --query "TypeError when calling foo() with empty list"
```

👉 详细使用指南：[CodeSearch 快速上手](./codesearch/docs/zh/3.快速上手/3.快速上手.md)

---

## 📚 文档导航

### DeepSearch 文档

- [产品简介](./deepsearch/docs/zh/1.产品简介/1.产品简介.md) - 了解产品定位与核心特性
- [安装指导](./deepsearch/docs/zh/2.安装指导/快速指引.md) - 详细的安装配置说明
- [开发指南](./deepsearch/docs/zh/3.开发指南/README.md) - 开发者文档
- [FAQ](./deepsearch/docs/zh/4.FAQ/README.md) - 常见问题解答

### CodeSearch 文档

- [产品简介](./codesearch/docs/zh/1.产品简介/1.产品简介.md) - 了解产品定位与核心特性
- [安装指导](./codesearch/docs/zh/2.安装指导/README.md) - 详细的安装配置说明
- [快速上手](./codesearch/docs/zh/3.快速上手/3.快速上手.md) - 快速开始使用指南
- [开发指南](./codesearch/docs/zh/4.开发指南/README.md) - 开发者文档
- [FAQ](./codesearch/docs/zh/5.FAQ/README.md) - 常见问题解答

### base 文档

- [openjiuwen-search-base 说明](./base/README.md) - 公共基础能力库的模块与设计说明

---

## 🏗️ 系统架构

### DeepSearch 架构概览

DeepSearch 主要基于 openJiuwen agent-core 构建，可以对接不同大模型及工具能力。系统主要由以下部分组成：

![DeepSearch 架构](./deepsearch/docs/zh/images/architecture.png)

- **管理器**：提供基于 openJiuwen agent-core 框架进行 Agent 创建、编排流程管理、配置管理等能力
- **查询规划**：提供基于意图识别的查询路由、结构规划、任务分解、查询改写等查询理解功能
- **知识检索**：提供离线知识构建与在线检索两大功能，支持多种检索模式
- **理解分析**：提供对检索结果及其他上下文信息的理解能力，包含评估、精炼、扩展、融合等功能
- **结果生成**：提供答案、报告生成、交互式编辑及结果溯源等主要功能

### CodeSearch 架构概览

```
┌──────────────── 索引（离线） ────────────────┐
│  代码仓库 → 语法感知切块 → Milvus 双路稀疏索引  │
│  增量：文件哈希去重，多版本共享未变更文件        │
└────────────────────────────────────────────┘
┌──────────────── 检索（在线） ────────────────┐
│  问题描述 → 检索智能体（决策模型 · 筛选模型 ·   │
│            片段记忆 · 五类工具）→ 文件+行区间   │
└────────────────────────────────────────────┘
```

---

## 🤝 贡献指南

欢迎贡献代码、报告问题或提出新功能！

- 提交 Issue 和 Pull Request
- 参考[贡献指南](https://www.openjiuwen.com/contribute)
- 参与社区讨论

---

## ⚖️ 许可证

本项目采用 **Apache 2.0** 许可证。详见 [LICENSE](./LICENSE) 文件。

---

## 📞 联系我们

- **项目主页**：[https://gitcode.com/openJiuwen/deepsearch](https://gitcode.com/openJiuwen/deepsearch)
- **官方网站**：[https://www.openjiuwen.com](https://www.openjiuwen.com)
- **问题反馈**：[Issues](https://gitcode.com/openJiuwen/deepsearch/issues)

---

## 📝 声明

本产品仅作为流程编排工具，不包含 AI 模型能力；用户在连接 AI 模型用于特定业务场景时，需自行承担欧盟 AI 法案等相关合规义务。

---

**🌟 如果这个项目对你有帮助，请给我们一个 Star！您的支持是我们持续改进的动力！**