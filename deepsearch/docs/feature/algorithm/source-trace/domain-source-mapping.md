# 域名来源映射

## 维护范围

本文档覆盖全局溯源中的域名来源映射能力，包括种子映射加载、动态映射初始化、域名查询和本地映射保存。

本文档不覆盖 citation 插入和来源匹配算法。

## 功能目的

域名来源映射用于把 URL 域名归一到更易展示的来源名称。它让 citation data 和参考文献展示可以使用稳定来源名，而不是只展示裸域名。

## 可见行为

- 系统启动或首次使用时加载 seed mappings。
- 查询域名时返回来源名和是否命中映射。
- 新映射可以保存到动态映射文件。
- 测试可以重置内存状态，避免跨用例污染。

## 关键代码路径

- 域名来源映射：`openjiuwen_deepsearch/algorithm/source_trace/domain_source_mapping.py`
- 种子数据：`openjiuwen_deepsearch/algorithm/source_trace/seed_mappings.json`

主要测试：

- `tests/source_tracer/test_domain_source_mapping.py`

## 核心流程

1. 初始化时读取 seed mapping。
2. 如存在动态映射文件，合并动态映射。
3. 查询 URL 域名对应来源名。
4. 未命中时返回 fallback 来源。
5. 新映射通过原子写保存到本地 JSON。

## 数据契约与依赖

输入：

- registered domain。
- seed mappings JSON。
- 动态映射 JSON。

输出：

- `(source, matched)`。
- 当前映射快照。

## 边界与错误处理

- 本地 JSON 写入需要使用原子替换，避免部分写损坏映射。
- 无效域名不应污染动态映射。
- 测试必须能重置全局缓存状态。

## 测试与验证

推荐命令：

```bash
uv run pytest tests/source_tracer/test_domain_source_mapping.py
```

## 相关文档

- [全局溯源总览](../source-trace.md)
- [Citation 校验](./citation-checking.md)
