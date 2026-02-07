# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import enum


class SearchEngine(enum.Enum):
    TAVILY = "tavily"
    GOOGLE = "google"
    XUNFEI = "xunfei"
    PETAL = "petal"


class LocalSearch(enum.Enum):
    OPENAPI = "openapi"
    NATIVE = "native"
