# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
class WebSearchEngineBasicException(Exception):
    CODE = "WEB_SEARCH_ENGINE_EX"

    def __init__(self, msg: str):
        super().__init__(f"[{self.CODE}] {msg}")


class WebSearchEngineExistsException(WebSearchEngineBasicException):
    """联网增强引擎已存在,异常"""
    pass


class ValidationError(Exception):
    """数据验证失败,异常"""
    pass


class WebSearchEngineNotFoundException(WebSearchEngineBasicException):
    """联网增强引擎不存在,异常"""
    pass


class WebSearchEngineApiKeyDecryptError(Exception):
    """联网增强引擎api key解密失败异常"""
    pass


class WebSearchEngineNotRegisterException(WebSearchEngineBasicException):
    """联网增强引擎未注册异常"""
    CODE = "WEB_SEARCH_ENGINE_NOT_REGISTER"


class WebSearchEngineExecutionException(WebSearchEngineBasicException):
    """联网增强引擎访问时出现错误异常"""
    CODE = "WEB_SEARCH_ENGINE_EXECUTION_ERROR"


class ReportTemplateBasicException(Exception):
    CODE = "REPORT_TEMPLATE_EX"

    def __init__(self, msg: str):
        super().__init__(f"[{self.CODE}] {msg}")


class TemplateNotFoundException(ReportTemplateBasicException):
    """模板不存在异常"""
    CODE = "TEMPLATE_NOT_FOUND"


class TemplateGenerationException(ReportTemplateBasicException):
    """AI 生成模板内容失败异常"""
    CODE = "TEMPLATE_GEN_FAILED"


class TemplateValidationError(ReportTemplateBasicException):
    """模板数据验证失败异常"""
    CODE = "TEMPLATE_VALIDATION_ERR"


class WebSearchEngineConfigGetException(Exception):
    """获取联网增强引擎配置信息错误异常"""
    CODE = "WEB_SEARCH_ENGINE_CONFIG_GET_ERR"


class LocalSearchEngineConfigGetException(Exception):
    """获取本地搜索引擎配置信息错误异常"""
    CODE = "LOCAL_SEARCH_ENGINE_CONFIG_GET_ERR"


class LLMConfigGetException(Exception):
    """获取LLM配置信息错误异常"""
    CODE = "LLM_CONFIG_GET_ERR"


class ReportTemplateNotFoundException(Exception):
    """报告模板不存在异常"""
    CODE = "REPORT_TEMPLATE_NOT_FOUND"


class SearchEngineConfigException(Exception):
    """引擎配置异常"""
    CODE = "SEARCH_ENGINE_CONFIG_ERR"
