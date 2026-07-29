# 4.开发指南

本章面向二次开发与维护人员，说明当前 DeepSearch 的目录结构、主要开发入口和核心 API 文档。

## 文档导航

- [目录结构](./directory_structure.md)
  - 说明 `openjiuwen_deepsearch/` 的分层设计、主流程与常见定位路径。
- [openJiuwen DeepSearch开发指南](./openJiuwen%20DeepSearch开发指南/README.md)
  - 提供配置初始化、Agent 创建、研究报告生成、模板处理、人机交互与报告后局部优化示例。
- [API 文档](./API文档/agent_factory.md)
  - 包含 `agent_factory`、`workflow`、`deepsearch_agent`、`main_nodes`、`base_node`、`search_context`、`config`、`report_convert` 等接口说明。

## 推荐阅读顺序

1. 先阅读 [目录结构](./directory_structure.md)，建立模块分层和主链路认识。
2. 再阅读 [openJiuwen DeepSearch开发指南](./openJiuwen%20DeepSearch开发指南/README.md)，理解常见接入方式与运行示例。
3. 最后按需查阅 [API 文档](./API文档/agent_factory.md)，定位具体类、节点、配置项与导出接口。
