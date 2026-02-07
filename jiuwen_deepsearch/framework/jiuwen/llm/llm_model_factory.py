# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Type

from openjiuwen.core.utils.llm.base import BaseModelClient
from openjiuwen.core.utils.llm.model_library.openai import OpenAILLM
from openjiuwen.core.utils.llm.model_library.siliconflow import Siliconflow
from openjiuwen.core.utils.llm.model_utils.model_factory import ModelFactory


class LLMModelFactory(ModelFactory):

    def _initialize_models(self):
        """Register all valid subclasses of BaseChatModel"""
        self.model_map: Dict[str, Type[BaseModelClient]] = {
            "openai": OpenAILLM,
            "siliconflow": Siliconflow,
        }
