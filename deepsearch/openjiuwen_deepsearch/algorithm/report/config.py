# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum
import logging
import os

logger = logging.getLogger(__name__)

# 时效软着陆排序权重(时间约束强化 v2,设计文档:
# docs/superpowers/specs/2026-08-17-temporal-constraint-v2-design.md ③)。
# 贪心选材边际价值加 w_t * timeliness_score(timeliness_score ∈ [-1.0, 1.0])。
# 取值远小于单条 rationale 的 coverage_gain(有效覆盖阈值 0.3),
# 只做同分决胜,不能盖过真实覆盖差异;temporal_scope 为 None 时完全不生效。
# 环境变量 TEMPORAL_TIMELINESS_WEIGHT 可覆盖(A/B 实验用),默认 0.15。
TEMPORAL_TIMELINESS_WEIGHT = float(os.environ.get("TEMPORAL_TIMELINESS_WEIGHT", "0.15"))


class ReportStyle(enum.Enum):
    SCHOLARLY = "scholarly"
    SCIENCE_COMMUNICATION = "science_communication"
    NEWS_REPORT = "news_report"
    SELF_MEDIA = "self_media"


class ReportFormat(enum.Enum):
    MARKDOWN = "markdown"
    WORD = "word"
    PPT = "ppt"
    EXCEL = "excel"
    HTML = "html"
    PDF = "pdf"

    def get_name(self):
        """返回当前报告配置的名称标识。"""
        return self.name.lower()


class ReportLang(enum.Enum):
    EN = "en-US"
    ZN = "zh-CN"
