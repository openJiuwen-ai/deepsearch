# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from jiuwen_deepsearch.config.config import Config
from jiuwen_deepsearch.framework.jiuwen.agent.search_context import Outline
from jiuwen_deepsearch.utils.common_utils.security_utils import ensure_safe_directory
from jiuwen_deepsearch.utils.debug_utils.outline_visualization import OutlineToExcelExporter

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
    def init(cls, results_dir: Optional[str] = None) -> None:

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
            except Exception:
                cls._validated_dir = None

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
            elif isinstance(outline, dict):
                data = outline
            else:
                return

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            base_name = f"{outline.title}_{session_id}_{timestamp}"
            output_dir = os.path.join(cls._validated_dir, "outline")
            os.makedirs(output_dir, exist_ok=True)

            # JSON
            json_path = os.path.join(output_dir, f"{base_name}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"{log_prefix} Exported Outline JSON: {json_path}")

            # Excel
            excel_path = os.path.join(output_dir, f"{base_name}.xlsx")
            exporter = OutlineToExcelExporter(data)
            exporter.export_to_excel(excel_path)
            logger.info(f"{log_prefix} Exported Outline Excel: {excel_path}")

        except Exception as e:
            logger.error(f"{log_prefix} Failed to export outline: {e}", exc_info=True)
