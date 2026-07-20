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
    def _katex_resources() -> tuple[str, str]:
        """Return (css_link_tag, js_script_tag) for KaTeX rendering.

        KaTeX 是一种快速、轻量的 LaTeX 公式渲染库，相比 MathJax 渲染速度更快、
        体积更小。使用 auto-render 扩展自动扫描页面中的 ``$...$`` 和 ``$$...$$``
        并渲染。

        在调用 ``renderMathInElement`` 之前，先执行 ``escapeCurrencyDollars``
        对正文文本节点中的货币美元（如 ``$4``、``$5``、``$1,200.50``）做占位
        保护，避免被 KaTeX auto-render 误配对为公式定界符；渲染完成后再还原
        占位符。该逻辑与 ``html_export.py`` 文件导出路径保持一致。

        Returns:
            tuple[str, str]: (CSS 链接标签, JS 脚本标签)。
        """
        katex_version = "0.16.11"
        css_link = (
            f'<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@{katex_version}/dist/katex.min.css" />'
        )
        js_script = (
            f'<script src="https://cdn.jsdelivr.net/npm/katex@{katex_version}/dist/katex.min.js">'
            f'</script>\n'
            f'<script src="https://cdn.jsdelivr.net/npm/katex@{katex_version}/dist/contrib/auto-render.min.js">'
            f'</script>\n'
            f"<script>\n"
            f"document.addEventListener('DOMContentLoaded', function() {{\n"
            f"    var PH = '\\uFF04';\n"
            f"    var MATH_OPS = '\\\\+-*/!%^_{{}}=<>×÷';\n"
            f"    function hasMathOp(s) {{\n"
            f"        for (var j = 0; j < s.length; j++) {{\n"
            f"            if (MATH_OPS.indexOf(s[j]) !== -1) return true;\n"
            f"        }}\n"
            f"        return false;\n"
            f"    }}\n"
            f"    function escapeCurrencyDollars() {{\n"
            f"        var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);\n"
            f"        var nodes = [];\n"
            f"        while (walker.nextNode()) nodes.push(walker.currentNode);\n"
            f"        nodes.forEach(function(node) {{\n"
            f"            var text = node.nodeValue;\n"
            f"            if (text.indexOf('$') === -1) return;\n"
            f"            var out = '';\n"
            f"            var modified = false;\n"
            f"            var i = 0;\n"
            f"            while (i < text.length) {{\n"
            f"                if (text[i] === '$' && i + 1 < text.length &&\n"
            f"                        text[i + 1] >= '0' && text[i + 1] <= '9') {{\n"
            f"                    var next$ = text.indexOf('$', i + 1);\n"
            f"                    if (next$ === -1 || next$ - i > 50 ||\n"
            f"                            !hasMathOp(text.substring(i + 1, next$))) {{\n"
            f"                        out += PH;\n"
            f"                        modified = true;\n"
            f"                        i++;\n"
            f"                        continue;\n"
            f"                    }}\n"
            f"                }}\n"
            f"                out += text[i];\n"
            f"                i++;\n"
            f"            }}\n"
            f"            if (modified) node.nodeValue = out;\n"
            f"        }});\n"
            f"    }}\n"
            f"    escapeCurrencyDollars();\n"
            f"    renderMathInElement(document.body, {{\n"
            f"        delimiters: [\n"
            f"            {{left: '$$', right: '$$', display: true}},\n"
            f"            {{left: '$', right: '$', display: false}},\n"
            f"        ],\n"
            f"        macros: {{\n"
            f"            '\\\\bm': '\\\\boldsymbol{{#1}}'\n"
            f"        }},\n"
            f"        throwOnError: false\n"
            f"    }});\n"
            f"    var restoreWalker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);\n"
            f"    while (restoreWalker.nextNode()) {{\n"
            f"        var n = restoreWalker.currentNode;\n"
            f"        if (n.nodeValue.indexOf(PH) !== -1) {{\n"
            f"            n.nodeValue = n.nodeValue.split(PH).join('$');\n"
            f"        }}\n"
            f"    }}\n"
            f"}});\n"
            f"</script>"
        )
        return css_link, js_script

    @classmethod
    def convert_from_markdown(cls, md_report_content: str) -> str:
        md_report_content = preprocess_markdown_text(md_report_content)
        md_report_content, math_spans = protect_math_spans(md_report_content)
        html_body = markdown2.markdown(
            md_report_content,
            extras=["tables", "fenced-code-blocks", "code-friendly"]
        )
        html_body = restore_math_spans(html_body, math_spans)
        html_body = postprocess_html(html_body)

        default_style_block_n = cls._load_css()
        katex_css, katex_js = cls._katex_resources()
        # 包裹完整 HTML
        html_report_content = f"""<!DOCTYPE html>
                    <html lang="zh-CN">
                        <head>
                            <meta charset="UTF-8">
                            {default_style_block_n}
                            {katex_css}
                        </head>
                        <body>
                          <div class="report-container">
                            {html_body}
                          </div>
                          {katex_js}
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
