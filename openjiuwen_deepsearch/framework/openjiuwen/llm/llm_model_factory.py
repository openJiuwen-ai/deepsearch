# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from dataclasses import dataclass, field
from typing import Optional
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig


@dataclass
class LLMModelParams:
    model_provider: str
    api_key: str
    api_base: str
    timeout: Optional[int] = None
    hyper_parameters: Optional[dict] = field(default=None)
    extension: Optional[dict] = field(default=None)


class LLMModelFactory:

    @staticmethod
    def get_model(params: LLMModelParams):
        """Get model instance based on provider"""

        provider_map = {
            "openai": "OpenAI",
            "siliconflow": "SiliconFlow"
        }
        actual_provider = provider_map.get(params.model_provider.lower(), params.model_provider)

        request_config = ModelRequestConfig()

        if params.hyper_parameters:
            for key, value in params.hyper_parameters.items():
                if hasattr(request_config, key):
                    setattr(request_config, key, value)

        if params.extension:
            for key, value in params.extension.items():
                setattr(request_config, key, value)

        client_config = ModelClientConfig(
            api_key=params.api_key,
            api_base=params.api_base,
            timeout=params.timeout,
            client_provider=actual_provider,
            verify_ssl=os.getenv("LLM_SSL_VERIFY", "true").lower() == "true"
        )
        return Model(model_client_config=client_config, model_config=request_config)
