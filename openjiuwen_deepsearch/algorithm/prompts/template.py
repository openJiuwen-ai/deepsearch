# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import logging
import os
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode


logger = logging.getLogger(__name__)

jinja_env = Environment(
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=select_autoescape(),
    loader=FileSystemLoader(os.path.dirname(__file__))
)


def apply_system_prompt(prompt_template_file: str, context_vars: dict) -> list:
    """apply system prompt template"""

    context_vars["CURRENT_TIME"] = datetime.now(tz=timezone.utc).strftime("%a %b %d %H:%M:%S %Y %Z")
    try:
        prompt_file_path = f"{prompt_template_file}.md"
        os.path.realpath(prompt_file_path)
        prompt_template = jinja_env.get_template(prompt_file_path)
        system_prompt = prompt_template.render(**context_vars)
        if not context_vars.get("messages"):
            return [{"role": "system", "content": system_prompt}]
        return [{"role": "system", "content": system_prompt}, *context_vars["messages"]]
    except FileNotFoundError as e:
        raise CustomValueException(
            error_code=StatusCode.FILE_NOT_FOUND_ERROR_PROMPT.code,
            message=StatusCode.FILE_NOT_FOUND_ERROR_PROMPT.errmsg.format(name=prompt_template_file)
        ) from e
    except Exception as e:
        raise CustomValueException(
            error_code=StatusCode.APPLY_SYSTEM_PROMPT_FAILED.code,
            message=StatusCode.APPLY_SYSTEM_PROMPT_FAILED.errmsg.format(name=prompt_template_file)
        ) from e
