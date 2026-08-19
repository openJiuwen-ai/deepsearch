# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum
import logging
import os

logger = logging.getLogger(__name__)

# 报告选材的时效加成权重(temporal_scope 为 None 时不生效)。
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
