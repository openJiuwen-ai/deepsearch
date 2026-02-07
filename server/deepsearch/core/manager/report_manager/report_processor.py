# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import base64
import re
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path

import docx
import markdown2
from docx.document import Document

from server.deepsearch.core.manager.report_manager.word_utils import set_global_styles, html_to_doc


class DefaultReportFormatProcessor(ABC):
    @staticmethod
    def _base64_to_raw(base64_content: str) -> str:
        return base64.b64decode(base64_content.encode("utf-8")).decode("utf-8")

    @staticmethod
    @abstractmethod
    def _raw_to_base64(raw_report) -> str:
        pass

    @classmethod
    def base64_convert_from_markdown(cls, b64_md_report_content: str):
        raw_md_report_content = cls._base64_to_raw(b64_md_report_content)
        raw_converted_report = cls.convert_from_markdown(raw_md_report_content)
        return cls._raw_to_base64(raw_converted_report)

    @classmethod
    @abstractmethod
    def convert_from_markdown(cls, md_report_content: str):
        pass


class ReportHtml(DefaultReportFormatProcessor):
    @staticmethod
    def _raw_to_base64(raw_report: str) -> str:
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
                inlineMath: [['$', '$'], ['\\\\(', '\\\\)']]
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

    @staticmethod
    def _fix_markdown_latex(md_content):
        # 匹配行内公式 $...$ 和块级公式 $$...$$
        pattern = re.compile(r'(\${1,2})(.+?)\1', re.DOTALL)

        def fix_formula(match):
            delimiter = match.group(1)
            content = match.group(2)

            # 修复 ^x → ^{x}，但跳过 ^{x}
            content = re.sub(r'\^([A-Za-z0-9*])', r'^{\1}', content)

            # 转义*，但跳过已经转义的 \*
            content = re.sub(r'(?<!\\)\*', r'\\*', content)
            return f"{delimiter}{content}{delimiter}"

        return pattern.sub(fix_formula, md_content)

    @classmethod
    def convert_from_markdown(cls, md_report_content: str) -> str:
        md_report_content = cls._fix_markdown_latex(md_report_content)
        html_body = markdown2.markdown(
            md_report_content,
            extras=["tables", "fenced-code-blocks", "code-friendly"]
        )
        html_body = cls._enable_html_latex(html_body)

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


class ReportWord(DefaultReportFormatProcessor):
    @staticmethod
    def _raw_to_base64(raw_report: Document) -> str:
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
