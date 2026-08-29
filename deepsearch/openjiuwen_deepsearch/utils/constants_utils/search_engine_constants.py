# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum


class SearchEngine(enum.Enum):
    TAVILY = "tavily"
    PUBMED = "pubmed"
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    GOOGLE = "google"
    XUNFEI = "xunfei"
    PETAL = "petal"
    BOCHA = "bocha"
    JINA = "jina"
    PERPLEXITY = "perplexity"
    SERPER = "serper"


class LocalSearch(enum.Enum):
    OPENAPI = "openapi"
    NATIVE = "native"


#: 原生支持按发表日期过滤的搜索引擎（source_date 约束可由引擎原生过滤）。
TEMPORAL_SCOPE_SEARCH_ENGINES = {
    SearchEngine.TAVILY.value,
}
