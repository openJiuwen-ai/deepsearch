# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import base64
import logging
import time
from abc import ABC, abstractmethod
from tempfile import TemporaryDirectory
from io import BytesIO
from pathlib import Path

import docx
import markdown2
from docx.document import Document

from server.deepsearch.core.manager.report_manager.docx_export import convert_md_to_docx
from server.deepsearch.core.manager.report_manager.html_export import convert_md_to_html
from server.deepsearch.core.manager.report_manager.conversion_utils import (
    postprocess_html,
    preprocess_markdown_text,
    protect_math_spans,
    restore_math_spans,
)
from server.deepsearch.core.manager.report_manager.report_bundle import build_report_bundle, pack_bundle_to_base64
from server.deepsearch.core.manager.report_manager.word_utils import set_global_styles, html_to_doc

logger = logging.getLogger(__name__)


class DefaultReportFormatProcessor(ABC):
    """Define the common interface for report export processors."""

    @staticmethod
    def _base64_to_raw(base64_content: str) -> str:
        """Decode a base64 UTF-8 string into raw text.

        Args:
            base64_content: base64 编码后的 UTF-8 文本。

        Returns:
            str: 解码后的原始文本内容。
        """
        return base64.b64decode(base64_content.encode("utf-8")).decode("utf-8")

    @staticmethod
    @abstractmethod
    def _raw_to_base64(raw_report) -> str:
        """Encode a raw export artifact into base64 content.

        Args:
            raw_report: 原始导出产物。

        Returns:
            str: base64 编码后的导出内容。
        """
        raise NotImplementedError

    @classmethod
    def base64_convert_from_markdown(cls, b64_md_report_content: str):
        """Convert base64 Markdown content into a base64 export artifact.

        Args:
            b64_md_report_content: base64 编码后的 Markdown 内容。

        Returns:
            str: base64 编码后的转换结果。
        """
        raw_md_report_content = cls._base64_to_raw(b64_md_report_content)
        raw_converted_report = cls.convert_from_markdown(raw_md_report_content)
        return cls._raw_to_base64(raw_converted_report)

    def convert_from_final_result_to_bundle_base64(self, final_result: dict) -> str:
        """Convert final_result content into a base64 ZIP bundle.

        Args:
            final_result: 工作流最终结果字典。

        Returns:
            str: base64 编码后的 ZIP 压缩包内容。

        Raises:
            NotImplementedError: 当前处理器尚未实现该能力。
        """
        start_time = time.perf_counter()
        logger.info("Starting report bundle export processor=%s", self.__class__.__name__)
        with TemporaryDirectory(prefix="report_convert_") as tmpdir:
            workspace = Path(tmpdir)
            self.convert_from_final_result(final_result, workspace)
            encoded = pack_bundle_to_base64(workspace / "report_bundle")
            logger.info(
                "Completed report bundle export processor=%s bundle_base64_length=%s duration_ms=%.2f",
                self.__class__.__name__,
                len(encoded),
                (time.perf_counter() - start_time) * 1000,
            )
            return encoded

    @classmethod
    @abstractmethod
    def convert_from_markdown(cls, md_report_content: str):
        """Convert raw Markdown text into a target artifact.

        Args:
            md_report_content: 原始 Markdown 文本。

        Returns:
            Any: 目标导出产物。
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def convert_from_final_result(cls, final_result: dict, workspace: Path):
        """Convert final_result into an on-disk export artifact.

        Args:
            final_result: 工作流最终结果字典。
            workspace: 当前导出任务的工作目录。

        Returns:
            Any: 转换后的主产物内容或路径。
        """
        raise NotImplementedError


class ReportHtml(DefaultReportFormatProcessor):
    """Provide HTML export support for report conversion."""

    @staticmethod
    def _raw_to_base64(raw_report: str) -> str:
        """Encode HTML text into base64.

        Args:
            raw_report: 原始 HTML 文本。

        Returns:
            str: base64 编码后的 HTML。
        """
        return base64.b64encode(raw_report.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _load_css():
        css_path = Path(__file__).resolve().parent / "css" / "style.css"
        with open(css_path, "r", encoding="utf-8") as f:
            return f"<style>{f.read()}</style>"

    @staticmethod
    def _enable_html_latex(html_body: str) -> str:
        mathjax_scripts = """
        <script>
        window.MathJax = {
            tex: {
                inlineMath: [['\\\\(', '\\\\)']],
                displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
                macros: {
                    bm: ['{\\\\boldsymbol{#1}}', 1]
                }
            }
        };
        </script>
        <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
        """
        if "</body>" in html_body:
            # 如果你的 HTML 有 </body>，插入在它前面
            html_body = html_body.replace("</body>", mathjax_scripts + "</body>")
        else:
            # 如果没有 body 标签，就直接追加
            html_body += mathjax_scripts

        return html_body

    @classmethod
    def convert_from_markdown(cls, md_report_content: str) -> str:
        md_report_content = preprocess_markdown_text(md_report_content)
        md_report_content, math_spans = protect_math_spans(md_report_content)
        html_body = markdown2.markdown(
            md_report_content,
            extras=["tables", "fenced-code-blocks", "code-friendly"]
        )
        html_body = restore_math_spans(html_body, math_spans)
        html_body = cls._enable_html_latex(html_body)
        html_body = postprocess_html(html_body)

        default_style_block_n = cls._load_css()
        # 包裹完整 HTML
        html_report_content = f"""
                    <html>
                        <head>
                            {default_style_block_n}
                        </head>
                        <body>
                          <div class="report-container">
                            {html_body}
                          </div>
                        </body>
                    </html>
                    """

        return html_report_content

    @classmethod
    def convert_from_final_result(cls, final_result: dict, workspace: Path) -> str:
        """Convert final_result into HTML text through the bundle workspace.

        Args:
            final_result: 工作流最终结果字典。
            workspace: 当前导出任务的工作目录。

        Returns:
            str: 最终导出的 HTML 文本。
        """
        start_time = time.perf_counter()
        logger.info("Converting final_result to HTML workspace=%s", workspace)
        bundle = build_report_bundle(final_result, workspace)
        output_html = bundle.root_dir / "report.html"
        convert_md_to_html(bundle.markdown_path, output_html)
        html_text = output_html.read_text(encoding="utf-8")
        logger.info(
            "Converted final_result to HTML output=%s html_length=%s duration_ms=%.2f",
            output_html,
            len(html_text),
            (time.perf_counter() - start_time) * 1000,
        )
        return html_text


class ReportWord(DefaultReportFormatProcessor):
    """Provide DOCX export support for report conversion."""

    @staticmethod
    def _raw_to_base64(raw_report: Document) -> str:
        """Encode a DOCX document into base64.

        Args:
            raw_report: python-docx Document 对象。

        Returns:
            str: base64 编码后的 DOCX 二进制。
        """
        buffer = BytesIO()
        raw_report.save(buffer)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    @staticmethod
    def _html_to_word(html_report_content: str) -> Document:
        doc = docx.Document()
        set_global_styles(doc)
        default_style_dict = {
            "heading1": "heading 1",
            "heading2": "heading 2",
            "heading3": "heading 3",
            "heading4": "heading 4",
            "heading5": "heading 5",
            "heading6": "heading 6",
            "heading7": "heading 7",
            "heading8": "heading 8",
            "heading9": "heading 9",
            "paragraph": "Normal",
            "table": "Table Grid",
            "default": "Normal"
        }
        html_to_doc(doc, html_report_content, default_style_dict)
        return doc

    @classmethod
    def convert_from_markdown(cls, md_report_content: str) -> Document:
        html_report_content = ReportHtml.convert_from_markdown(md_report_content)

        # convert to word
        doc = cls._html_to_word(html_report_content)
        return doc

    @classmethod
    def convert_from_final_result(cls, final_result: dict, workspace: Path) -> Path:
        """Convert final_result into a DOCX file through the pure-Python pipeline.

        Args:
            final_result: 工作流最终结果字典。
            workspace: 当前导出任务的工作目录。

        Returns:
            Path: 导出的 DOCX 文件路径。
        """
        start_time = time.perf_counter()
        logger.info("Converting final_result to DOCX workspace=%s", workspace)
        bundle = build_report_bundle(final_result, workspace)
        output_docx = bundle.root_dir / "report.docx"
        convert_md_to_docx(bundle.markdown_path, output_docx)
        logger.info(
            "Converted final_result to DOCX output=%s docx_bytes=%s duration_ms=%.2f",
            output_docx,
            output_docx.stat().st_size if output_docx.exists() else 0,
            (time.perf_counter() - start_time) * 1000,
        )
        return output_docx
