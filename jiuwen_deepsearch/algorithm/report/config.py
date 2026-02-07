# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum
import logging

logger = logging.getLogger(__name__)


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
        return self.name.lower()


class ReportLang(enum.Enum):
    EN = "en-US"
    ZN = "zh-CN"