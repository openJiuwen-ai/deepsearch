# -*- coding: UTF-8 -*-
"""Build stable DeepResearch prompt messages without retaining conversation state."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, meta, select_autoescape

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode


PROMPT_ROOT = Path(__file__).resolve().parent
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_RESERVED_CONTEXT_KEYS = frozenset({"messages", "history", "prior_messages", "images"})


@dataclass(frozen=True)
class PromptBuildOptions:
    """单次提示构建的消息级输入。

    Attributes:
        prior_messages: 追加在 system 消息之后的历史消息序列。
        images: 附加到 user 消息的图片 base64 序列。
    """

    prior_messages: Sequence[Any] = ()
    images: Sequence[str] = ()


def build_prompt_messages(
    prompt_name: str,
    context: Mapping[str, Any] | None = None,
    *,
    options: PromptBuildOptions | None = None,
) -> list[Any]:
    """渲染一条稳定的 system 消息、传入的历史消息，以及可选的 user 消息。

    Args:
        prompt_name: 提示模板名，对应 prompts 目录下的子目录名。
        context: 渲染 user 模板所用的变量字典。
        options: 消息级输入，包含历史消息与图片。

    Returns:
        按 role 组织好的消息列表：system 消息、历史消息，以及渲染后的 user 消息。

    Raises:
        CustomValueException: 提示名非法、上下文占用预留键、模板缺失或渲染失败时抛出。
    """
    options = options or PromptBuildOptions()
    try:
        render_context = dict(context or {})
        _validate_name_and_context(prompt_name, render_context)
        environment = _create_environment()

        system_name = f"{prompt_name}/system.md"
        _assert_static_template_tree(environment, system_name)
        system_text = environment.get_template(system_name).render()
        messages: list[Any] = [
            {"role": "system", "content": system_text},
            *list(options.prior_messages),
        ]

        user_name = f"{prompt_name}/user.md"
        if not _template_exists(environment, user_name):
            if options.images:
                _raise_prompt_error(prompt_name, "images require user.md")
            return messages

        user_variables = _template_variables(environment, user_name, recursive=True)
        if "current_date" in user_variables and "current_date" not in render_context:
            render_context["current_date"] = datetime.now(timezone.utc).date().isoformat()
        user_text = environment.get_template(user_name).render(**render_context)
        content: Any = (
            _multimodal_content(user_text, options.images) if options.images else user_text
        )
        return [*messages, {"role": "user", "content": content}]
    except CustomValueException:
        raise
    except TemplateNotFound as error:
        return _raise_missing_prompt(prompt_name, error)
    except Exception as error:
        return _raise_prompt_error(prompt_name, str(error), error)


def _create_environment() -> Environment:
    """创建用于加载并渲染提示模板的 Jinja 环境。

    Returns:
        配置了 trim/lstrip、自动转义与文件系统加载器的 Environment。
    """
    return Environment(
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=select_autoescape(),
        loader=FileSystemLoader(str(PROMPT_ROOT)),
    )


def _validate_name_and_context(prompt_name: str, context: Mapping[str, Any]) -> None:
    """校验提示名与上下文键是否合法。

    Args:
        prompt_name: 待校验的提示名。
        context: 待校验的渲染上下文。

    Raises:
        CustomValueException: 提示名不合法或上下文包含预留键时抛出。
    """
    if not isinstance(prompt_name, str) or not _NAME_RE.fullmatch(prompt_name):
        _raise_prompt_error(str(prompt_name), "invalid prompt name")
    reserved_keys = _RESERVED_CONTEXT_KEYS.intersection(context)
    if reserved_keys:
        _raise_prompt_error(prompt_name, f"reserved context keys: {sorted(reserved_keys)}")


def _assert_static_template_tree(environment: Environment, template_name: str) -> None:
    """断言 system 模板及其静态 include 不包含运行时变量。

    Args:
        environment: 解析模板所用的 Jinja 环境。
        template_name: system 模板名。

    Raises:
        CustomValueException: 模板含运行时变量、动态 include 或缺失时抛出。
    """
    visited: set[str] = set()

    def visit(name: str) -> None:
        """递归检查单个模板（含其静态 include）。"""
        if name in visited:
            return
        visited.add(name)
        source, _, _ = environment.loader.get_source(environment, name)
        parsed = environment.parse(source)
        variables = meta.find_undeclared_variables(parsed)
        if variables:
            _raise_prompt_error(name, f"system template has runtime variables: {sorted(variables)}")
        for referenced_name in meta.find_referenced_templates(parsed):
            if referenced_name is None:
                _raise_prompt_error(name, "system template has a dynamic include")
            visit(referenced_name)

    try:
        visit(template_name)
    except TemplateNotFound as error:
        _raise_missing_prompt(template_name.removesuffix("/system.md"), error)


def _template_exists(environment: Environment, template_name: str) -> bool:
    """判断模板文件是否存在。

    Args:
        environment: 加载模板所用的 Jinja 环境。
        template_name: 模板名。

    Returns:
        模板存在时返回 True，否则返回 False。
    """
    try:
        environment.loader.get_source(environment, template_name)
    except TemplateNotFound:
        return False
    return True


def _template_variables(
    environment: Environment, template_name: str, *, recursive: bool = False
) -> set[str]:
    """返回模板的未声明变量，可选地包含静态 include 中的变量。

    Args:
        environment: 解析模板所用的 Jinja 环境。
        template_name: 模板名。
        recursive: 是否递归收集静态 include 模板的变量。

    Returns:
        模板中未声明的变量名集合。
    """
    variables: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        """递归收集单个模板（含其静态 include）的变量。"""
        if name in visited:
            return
        visited.add(name)
        source, _, _ = environment.loader.get_source(environment, name)
        parsed = environment.parse(source)
        variables.update(meta.find_undeclared_variables(parsed))
        if recursive:
            for referenced_name in meta.find_referenced_templates(parsed):
                if referenced_name is not None:
                    visit(referenced_name)

    visit(template_name)
    return variables


def _multimodal_content(text: str, images: Sequence[str]) -> list[dict[str, Any]]:
    """将文本与 base64 图片组装为多模态 user 内容。

    Args:
        text: 文本内容。
        images: 图片的 base64 编码序列。

    Returns:
        由文本项与 image_url 项组成的多模态内容列表。
    """
    return [
        {"type": "text", "text": text},
        *[
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            }
            for image_base64 in images
        ],
    ]


def _raise_missing_prompt(prompt_name: str, error: Exception) -> NoReturn:
    """提示模板缺失时抛出文件未找到错误。

    Args:
        prompt_name: 提示名。
        error: 原始异常。

    Raises:
        CustomValueException: 始终抛出提示文件未找到错误。
    """
    raise CustomValueException(
        error_code=StatusCode.FILE_NOT_FOUND_ERROR_PROMPT.code,
        message=StatusCode.FILE_NOT_FOUND_ERROR_PROMPT.errmsg.format(name=prompt_name),
    ) from error


def _raise_prompt_error(
    prompt_name: str, detail: str,
    error: Exception | None = None,
) -> NoReturn:
    """提示构建失败时抛出统一的应用提示失败错误。

    Args:
        prompt_name: 提示名。
        detail: 失败明细。
        error: 可选，作为异常链原因的原始异常。

    Raises:
        CustomValueException: 始终抛出应用系统提示失败错误。
    """
    cause = error or ValueError(detail)
    raise CustomValueException(
        error_code=StatusCode.APPLY_SYSTEM_PROMPT_FAILED.code,
        message=StatusCode.APPLY_SYSTEM_PROMPT_FAILED.errmsg.format(name=prompt_name),
    ) from cause
