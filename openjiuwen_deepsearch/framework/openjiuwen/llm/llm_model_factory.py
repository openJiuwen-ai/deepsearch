# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig


class LLMModelFactory:

    @staticmethod
    def get_model(
        model_provider: str,
        api_key: str,
        api_base: str,
        timeout: float = None,
        hyper_parameters: dict = None
    ):
        """Get model instance based on provider"""

        provider_map = {
            "openai": "OpenAI",
            "siliconflow": "SiliconFlow"
        }
        actual_provider = provider_map.get(model_provider.lower(), model_provider)

        request_config = ModelRequestConfig()

        if hyper_parameters:
            for key, value in hyper_parameters.items():
                if hasattr(request_config, key):
                    setattr(request_config, key, value)

        client_config = ModelClientConfig(
            api_key=api_key,
            api_base=api_base,
            timeout=timeout,
            client_provider=actual_provider,
            verify_ssl=os.getenv("LLM_SSL_VERIFY", "true").lower() == "true"
        )
        return Model(model_client_config=client_config, model_config=request_config)
