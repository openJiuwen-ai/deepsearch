# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen_deepsearch.framework.openjiuwen.tools.local_search import create_local_search_tool, \
    update_local_search_mapping
from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import create_web_search_tool, \
    update_web_search_mapping

__all__ = [
    "update_web_search_mapping",
    "update_local_search_mapping",
    "create_web_search_tool",
    "create_local_search_tool"
]
