# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from openjiuwen_deepsearch.config.config import Config
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline
from openjiuwen_deepsearch.utils.common_utils.security_utils import ensure_safe_directory
from openjiuwen_deepsearch.utils.debug_utils.outline_visualization import OutlineToExcelExporter

logger = logging.getLogger(__name__)


class ResultExporter:
    """
    安全结果导出管理器
    """
    _initialized: bool = False
    _export_enabled: bool = False
    _results_dir: Optional[str] = None
    _validated_dir: Optional[str] = None
    _SAFE_BASE: str = os.path.realpath("./output/results")

    @classmethod
    def _sanitize_filename_component(cls, value, fallback: str) -> str:
        """Convert untrusted text into a single safe filename component."""
        text = str(value or "").strip()
        chars = []
        for char in text:
            if char.isalnum() or char in {"-", "_", "."}:
                chars.append(char)
            else:
                chars.append("_")

        component = "".join(chars).strip("._")
        return (component or fallback)[:100]

    @classmethod
    def _safe_output_path(cls, output_dir: str, filename: str) -> str:
        output_root = os.path.realpath(output_dir)
        candidate = os.path.realpath(os.path.join(output_root, filename))
        if os.path.commonpath([output_root, candidate]) != output_root:
            raise ValueError(
                f"Unsafe export path outside outline directory: {candidate}"
            )
        return candidate

    @classmethod
    def init(cls, results_dir: Optional[str] = None) -> None:
        """初始化导出器运行目录和基础状态。"""

        if cls._initialized:
            return

        config = Config()
        cls._export_enabled = config.service_config.model_dump().get(
            "export_intermediate_results", False
        )
        cls._results_dir = results_dir

        if cls._export_enabled:
            try:
                cls._validated_dir = ensure_safe_directory(
                    cls._results_dir or cls._SAFE_BASE, cls._SAFE_BASE
                )
            except Exception as e:
                cls._validated_dir = None
                logger.warning(
                    "[ResultExporter] ensure_safe_directory failed while export_intermediate_results "
                    "is enabled; intermediate export is disabled. requested_dir=%s safe_base=%s error=%s",
                    cls._results_dir or cls._SAFE_BASE,
                    cls._SAFE_BASE,
                    e,
                    exc_info=True,
                )

        cls._initialized = True

    @classmethod
    def export_outline(cls, outline, session_id) -> None:
        """导出大纲（自动初始化）"""
        if not cls._initialized:
            cls.init()

        log_prefix = f"[{cls.__class__.__name__}]"
        if not cls._export_enabled:
            logger.warning(f"{log_prefix} Export is disabled")
            return

        if not cls._validated_dir:
            logger.warning(f"{log_prefix} Result dir is invalid, "
                           f"result_dir: {cls._results_dir}, safe_base_dir: {cls._SAFE_BASE}")
            return

        try:
            # Outline 类型校验
            if isinstance(outline, Outline):
                data = outline.model_dump()
                outline_title = outline.title
            elif isinstance(outline, dict):
                data = outline
                outline_title = outline.get("title")
            else:
                return

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            safe_title = cls._sanitize_filename_component(outline_title, "outline")
            safe_session_id = cls._sanitize_filename_component(session_id, "session")
            base_name = f"{safe_title}_{safe_session_id}_{timestamp}"
            output_dir = os.path.join(cls._validated_dir, "outline")
            os.makedirs(output_dir, exist_ok=True)

            # JSON
            json_path = cls._safe_output_path(output_dir, f"{base_name}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"{log_prefix} Exported Outline JSON: {json_path}")

            # Excel
            excel_path = cls._safe_output_path(output_dir, f"{base_name}.xlsx")
            exporter = OutlineToExcelExporter(data)
            exporter.export_to_excel(excel_path)
            logger.info(f"{log_prefix} Exported Outline Excel: {excel_path}") 

        except Exception as e:
            logger.error(f"{log_prefix} Failed to export outline: {e}", exc_info=True)
