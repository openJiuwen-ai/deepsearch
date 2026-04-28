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


def get_prompt_section(prompt_template_file: str, context_vars: dict | None = None) -> str:
    context_vars = context_vars or {}
    try:
        prompt_file_path = f"{prompt_template_file}.md"
        prompt_template = jinja_env.get_template(prompt_file_path)
        return prompt_template.render(**context_vars)
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


def apply_vlm_prompt(prompt_template_file: str, context_vars: dict, image_base64_list: list[str]) -> list:
    """
    apply vlm prompt (png image base64)
    """
    context_vars["CURRENT_TIME"] = datetime.now(tz=timezone.utc).strftime("%a %b %d %H:%M:%S %Y %Z")
    try:
        prompt_file_path = f"{prompt_template_file}.md"
        os.path.realpath(prompt_file_path)
        prompt_template = jinja_env.get_template(prompt_file_path)
        # text
        system_prompt = prompt_template.render(**context_vars)
        text_prompt = {
            "type": "text",
            "text": system_prompt
        }
        # image
        image_prompt = [{
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{image_base64}"
                }
            } for image_base64 in image_base64_list]
        vlm_prompt = [text_prompt]
        vlm_prompt.extend(image_prompt)
        if not context_vars.get("messages"):
            return [{"role": "user", "content": vlm_prompt}]
        return [{"role": "user", "content": vlm_prompt}, *context_vars["messages"]]
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
