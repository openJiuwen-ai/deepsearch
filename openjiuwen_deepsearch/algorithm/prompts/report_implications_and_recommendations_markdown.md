---
CURRENT_TIME: {{ CURRENT_TIME }}
---

# AI Research Report — The last chapter

## Step 1: Identify User Role (Internal Reasoning Only)
You must internally determine the user's real role (occupation/identity) using the two core inputs below.
- Input Information:
 - User's Original Query (user_query): {{user_query}}
 - All sub_reports

- Judgment Requirements:
  - Specific Role Positioning: For user_role, use precise occupations (e.g., "Commercial Bank Manager", "Economic Researcher", "Graduate Student in Environmental Science at a University", "Strategic Analyst in the Internet Industry", "R&D Manager at a Biopharmaceutical Enterprise") rather than generalized labels.
  - Core Judgment Dimensions for judge_basis:
    - Demand scenarios in the user_query (e.g., "decision-making support service for commercial banks' operations", "project feasibility analysis", "reference for policy application materials");
    - Professional terms/expression habits in the user_query (academic, commercial, policy-related, or teaching vocabulary);
    - Theme direction of the report_content (monetary policy, cutting-edge technology, industry market, academic theory, policy interpretation) and depth requirements (basic cognition, in-depth analysis, data support, conclusion demonstration).

- Important:
  - Do NOT output `user_role` or `judge_basis` in the final report.
  - They are only used internally for tone and for generating the last sentence in Step 2.

## Step 2: Generate Report Chapter
You are an {{user_role}}. Produce the final chapter for a research report. Use the inputs below to create a concise, decision‑oriented chapter suitable for senior managers, regulators, and specialist readers.

Inputs:
- Research subject: [{{report_task}}]
- - Key findings: [{{current_outline}}]
- - All sub_reports

Formatting and content rules
- Chapter structure: three main sections — "Conclusion", "Implications" and "Recommendations".
- Conclusion:
  - Summarize the most critical and representative conclusions of all sub_reports in highly condensed language
  - Briefly review the core trends or key data points identified in the report to strengthen the support for the conclusions
  - Summary should have depth, clear viewpoints, and avoid vague expressions
  - Extract consistent insights across all sub_reports by synthesizing their conclusions into a unified, high-level summary that emphasizes shared patterns and overarching implications
  - Key information must be highlighted in bold font (e.g., **18%**, **关键信息**).
  - Must end with "Here are the implications and recommendations for {{user_role}}." (translated into the corresponding language, e.g., "以下是针对金融分析师的启示和建议：" in Chinese), {{user_role}} must be inserted into this sentence and translated into the corresponding language, even though it is not shown elsewhere.
- Implications: 3–5 numbered items. Each item must have a short bold title followed by 3–5 sentences explaining the issue, its cause, and the study evidence linking to it, implications better inspired by Conclusion above
- Recommendations: 3–5 numbered items. Each item must have a short bold title followed by 3–5 sentences explaining the goal, key actions and expected impact, recommendations better correspond to the Implications above.
- Language: Formal, precise word; avoid slogans and vague platitudes. Use active verbs (e.g., "Strengthen", "Optimize", "Enhance").
- Tone: Actionable and evidence‑linked; prioritize feasibility and measurable outcomes.
- Do not include citations or footnotes. Do not invent quantitative figures not provided in inputs.
- Do not generate any first level title (e.g., #).
- Do not generate any third level or deeper level title (e.g., ###).
- The language of generated content is specified by language = **{{language}}**

Output format (exact)
- Conclusion content: Begin directly with the summary content with no "Conclusion" or "Conclusion, Implications and Recommendations" such Subheading
- Subheading: "Implications"(or translate into the corresponding language, e.g., "启示" in Chinese) then bulleted list as specified, use second level headings(e.g., ##)
- Subheading: "Recommendations"(or translate into the corresponding language, e.g., "建议" in Chinese) then bulleted list as specified, use second level headings(e.g., ##)

General Example
摩根大通作为全球系统重要性银行中唯一位于第四档的机构，凭借卓越的市值表现、资产回报率（ROA）和股权回报率（ROE）在全球大型银行中占据领先地位。本报告通过历史演进、经营现状、未来趋势、利益相关方治理、量化数据与对标分析等维度，全面剖析其核心竞争力，并为中国商业银行提供可借鉴的发展路径。\n\n**摩根大通的核心优势体现在以下几个方面：一是强大的综合化业务结构**，消费与社区银行、投资银行、资产管理等板块协同效应显著；**二是领先的资本效率与盈利能力**，ROE、ROA等指标持续优于同业；**三是强大的市场影响力**，影响力的根基在于综合竞争实力；**四是前瞻性的科技布局**，人工智能、区块链、数字货币等领域投入巨大且成果转化明显；**五是健全的利益相关方治理体系**，在ESG责任履行、监管合规、员工多样性等方面树立行业标杆。\n\n面对日益复杂的地缘政治环境和宏观经济波动，摩根大通通过多元化资产配置、压力测试机制和全球资源整合能力有效应对不确定性。同时，其绿色金融战略、科技创新驱动以及国际化网络布局，进一步巩固了其在全球金融体系中的领导地位。 \n\n总体来看，摩根大通的成功不仅源于其雄厚的资本实力和稳健的财务表现，更在于其长期坚持以客户为中心、以创新为动力、以风控为底线的战略定力。这些特质使其在全球银行业竞争中始终保持领先。**以下是针对商业银行的经济研究工作的启示和建议：**

## 启示

**第一，中资银行的国际影响力有待提升**： 近年来，中资银行在标准制定、绿色金融等领域发挥的作用日益凸显，但与其资本资产规模相比仍显不足。这一方面是因为国际化业务拓展不够，全球化经营的客户相对较少，在全球范围内谋划交易银行、投资银行、金融市场业务的能力有待提升；另一方面，缺乏打造国际影响力、发出中国声音、唱响中国经济光明论的平台建设，也成为重要的制约因素。
**第二，xxx**：xxx
**第三，xxx**：xxx

## 建议

**第一，拓展国际影响力**: 。围绕“一带一路”倡议地区、走出去的国际化企业，积极拓展国际业务布局，形成良好扎实的全球化客户基础。依托上海、香港两大国际中心建设契机，加强面向全球的金融市场服务能力建设，提升对各类市场的服务适应性。发挥绿色金融的先发优势，践行ESG发展理念，将发展ESG作为提升品牌形象、拓展国际影响力的重要抓手。通过研究发声、筹办会议等方式搭建发声平台，引导影响力提升，为唱响中国经济光明论贡献大行力量。
**第二，xxx**：xxx
**第三，xxx**：xxx