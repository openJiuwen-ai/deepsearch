# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


import base64
import logging
import re
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import pypdfium2 as pdfium
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdfium2 import PdfDocument

from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.9  # 相似度阈值
LENGTH_RATIO_THRESHOLD = 0.9  # 长度比例阈值


def extract_bookmarks(doc: PdfDocument):
    """提取PDF书签(目录项)"""
    bookmarks = doc.get_toc()

    page_bookmarks = {}
    if not bookmarks:
        return page_bookmarks

    for bm in bookmarks:
        title = bm.title.strip()
        level = bm.level + 1  # 书签级别从0开始，转换为1开始
        page_index = bm.page_index
        if page_index in page_bookmarks:
            page_bookmarks[page_index].append((level, title))
        else:
            page_bookmarks[page_index] = [(level, title)]
    return page_bookmarks


def extract_line_with_size(pdf: PdfDocument):
    """提取每行文本及其最大字体大小"""
    results = {
        "pages": [],
        "min_head_size": 0,
        "top_5_font": []
    }

    for page in pdf:
        textpage = page.get_textpage()
        page_text = textpage.get_text_range()
        page_results = []
        for line in page_text.splitlines():
            page_results.append({
                'line_text': line.strip(),
                'font_size': 0  # 占位符，后续可扩展为实际字体大小
            })
        results["pages"].append(page_results)
        textpage.close()
        page.close()
    return results


def preprocess_pdf(base64_string: str) -> Tuple[dict, dict]:
    """预处理PDF，提取书签和行文本及字体大小"""
    pdf = None
    try:
        pdf_bytes = base64.b64decode(base64_string)
        pdf = pdfium.PdfDocument(pdf_bytes)
        bookmarks = extract_bookmarks(pdf)
        line_size_info = extract_line_with_size(pdf)
        return bookmarks, line_size_info
    except Exception as e:
        if LogManager.is_sensitive():
            logger.error("Error in preprocess_pdf: An error occurred while processing the PDF.")
        else:
            logger.error(f"Error in preprocess_pdf: {e}")
        return {}, {}
    finally:
        if pdf is not None:
            pdf.close()


def calculate_heading_level(line: str) -> int:
    """计算Markdown标题的级别"""
    stripped = line.strip()
    if not stripped.startswith('#'):
        return 0
    return len(stripped) - len(stripped.lstrip('#'))


def calculate_similarity(a, b):
    """计算两个字符串的相似度 (0-1)"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def is_similar(title, raw_line_text) -> bool:
    """判断两个字符串是否相似"""
    if not title or not raw_line_text:
        return False
    return (len(raw_line_text) > 0 and len(title) > 0 and
            min(len(raw_line_text), len(title)) / max(len(raw_line_text), len(title)) > LENGTH_RATIO_THRESHOLD)


def is_part_title(current_heading_raw, title, raw_line_text) -> bool:
    """判断当前拼接的标题是否为目标标题的前缀"""
    return current_heading_raw == "" and title.lower().startswith(raw_line_text.lower()) and len(raw_line_text) > 8


def deal_with_remaining_titles(current_bookmarks, current_index, markdown_lines):
    """处理页面结束时残留的标题拼接"""
    level, title = current_bookmarks[current_index - 1]
    heading = "#" * min(level, 6) + " " + title
    markdown_lines.append(heading)


def process_with_bookmarks(lines_with_size, page_bookmarks) -> list[str]:
    """处理有书签的PDF文档"""
    markdown_lines = []
    for page_num in range(len(lines_with_size["pages"])):
        current_state = {
            "current_index": 0,
            "heading_raw": "",
            "handled": False,
            "bookmarks": page_bookmarks.get(page_num, []),
        }

        for line_with_size in lines_with_size["pages"][page_num]:
            line_text = line_with_size['line_text']
            current_state["handled"] = False

            if current_state["current_index"] < len(current_state["bookmarks"]):
                level, title = current_state["bookmarks"][current_state["current_index"]]
                # 高度匹配
                if is_similar(title, line_text):
                    similarity = calculate_similarity(line_text, title)
                    if similarity >= SIMILARITY_THRESHOLD:
                        heading = "#" * min(level, 6) + " " + title
                        markdown_lines.append(heading)
                        current_state["current_index"] += 1
                        current_state["handled"] = True
                        continue

                # 情况2：标题部分匹配
                if is_part_title(current_state["heading_raw"], title, line_text):
                    current_state["heading_raw"] = line_text
                    current_state["handled"] = True
                    continue

                # 情况3：正在拼接标题
                if current_state.get("heading_raw") != "":
                    new_heading = current_state["heading_raw"] + line_text
                    if new_heading.lower() == title.lower():
                        heading = "#" * min(level, 6) + " " + title
                        markdown_lines.append(heading)
                        current_state["current_index"] += 1
                        current_state["heading_raw"] = ""
                        current_state["handled"] = True
                        continue
                    if new_heading.lower().startswith(title.lower()):
                        current_state["heading_raw"] = new_heading
                        current_state["handled"] = True
                        continue

                # 情况4：放弃当前标题，尝试下一个标题
                if current_state["heading_raw"] != "":
                    current_state["heading_raw"] = ""
                    current_state["current_index"] += 1  # 放弃当前标题，尝试下一个标题

            # 普通文本 (未被标题处理逻辑)
            if not current_state["handled"]:
                markdown_lines.append(line_text)

        # 处理页面结束时残留的标题拼接
        if current_state["heading_raw"]:
            deal_with_remaining_titles(current_state["bookmarks"], current_state["current_index"], markdown_lines)

        if markdown_lines and markdown_lines[-1].strip() != "":
            markdown_lines.append("")  # 页面间添加空行

    return markdown_lines


class TemplateUtils:
    _ALLOWED_TEMPLATE_SUFFIX = [".md"]
    _ALLOWED_SOURCE_SUFFIX = [".md", ".html", ".pdf", ".docx"]
    _MAX_REPORT_SIZE = 50 * 1024 * 1024
    _MAX_TEMPLATE_COUNT = 100
    _NAME_PATTERN = re.compile(r'^[\u4e00-\u9fa5a-zA-Z0-9_\-\.]+$')

    @classmethod
    def check_template_name(cls, name: str) -> None:
        """校验模板名称"""
        if not name or not cls._NAME_PATTERN.match(name):
            raise CustomValueException(
                error_code=StatusCode.TEMPLATE_NAME_INVALID.code,
                message=StatusCode.TEMPLATE_NAME_INVALID.errmsg.format(name=name))

    @classmethod
    def valid_report_suffix(cls, report_name: str) -> None:
        """
        校验来源报告的文件名后缀是否受支持。
        说明：
        - 新接口以流形式上传内容(report_stream)，不再校验磁盘存在性；
        - 仅根据 report_name 的后缀判定类型是否合法。
        """
        if not report_name:
            raise CustomValueException(error_code=StatusCode.PARAM_CHECK_ERROR_REPORT_NAME_REQUIRED.code,
                                       message=StatusCode.PARAM_CHECK_ERROR_REPORT_NAME_REQUIRED.errmsg)
        suffix = Path(report_name).suffix.lower()
        if suffix not in cls._ALLOWED_SOURCE_SUFFIX:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_SUFFIX_INVALID.code,
                message=StatusCode.PARAM_CHECK_ERROR_SUFFIX_INVALID.errmsg.format(
                    file_type="report", suffix=suffix, allowed_suffixes=cls._ALLOWED_SOURCE_SUFFIX))
        return suffix

    @classmethod
    def valid_template_suffix(cls, file_name: str) -> None:
        """
        校验源模板文件名的后缀是否受支持。
        - 仅根据 file_name 的后缀判定类型是否合法。
        """
        if not file_name:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_TEMPLATE_NAME_REQUIRED.code,
                message=StatusCode.PARAM_CHECK_ERROR_TEMPLATE_NAME_REQUIRED.errmsg)
        suffix = Path(file_name).suffix.lower()
        if suffix not in cls._ALLOWED_TEMPLATE_SUFFIX:
            raise CustomValueException(
                error_code=StatusCode.PARAM_CHECK_ERROR_SUFFIX_INVALID.code,
                message=StatusCode.PARAM_CHECK_ERROR_SUFFIX_INVALID.errmsg.format(
                    file_type="template", suffix=suffix, allowed_suffixes=cls._ALLOWED_TEMPLATE_SUFFIX))

        return suffix

    @classmethod
    def count_templates(cls, template_dir: Path) -> int:
        """统计有效模板文件数量"""
        return sum(
            1 for f in template_dir.iterdir()
            if f.is_file() and f.suffix in cls._ALLOWED_TEMPLATE_SUFFIX
        )

    @classmethod
    def fmt_bytes(cls, size: int) -> str:
        """格式化字节大小为可读字符串"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f}{unit}"
            size /= 1024
        return f"{size:.2f}TB"

    @classmethod
    def postprocess_structure(cls, headings_output: str) -> str:
        lines = headings_output.strip().splitlines()
        h1_count = sum(1 for line in lines if line.startswith("# "))

        new_lines = []
        if h1_count in (0, 1):
            for line in lines:
                if line.startswith("# "):
                    continue
                if line.startswith("## "):
                    new_lines.append("#" + line[2:])
                elif line.startswith("### "):
                    new_lines.append("##" + line[3:])
        else:
            for line in lines:
                if not line.startswith("#"):
                    continue
                if line.startswith("###"):
                    continue
                new_lines.append(line)

        return "\n".join(new_lines)

    @classmethod
    def get_h1_count_skip(cls, lines) -> tuple[int, bool]:
        """Get h1 level headers count and skip boolean."""
        h1_count = sum(1 for line in lines if line.strip().startswith("# "))
        skip = True if h1_count == 1 else False
        return h1_count, skip

    @classmethod
    def deal_with_level_2_3(cls, level: int, stripped: str, line: str, new_lines: list[str]) -> None:
        """Deal with level 2 and 3 headers."""

        if level == 2:
            new_lines.append("#" + stripped[2:])
        elif level == 3:
            new_lines.append("##" + stripped[3:])
        else:
            new_lines.append(line)

    @classmethod
    def postprocess_structure_keep_content(cls, headings_output: str) -> str:
        lines = headings_output.strip().splitlines()
        h1_count, skip = TemplateUtils.get_h1_count_skip(lines)

        new_lines = []
        skip_section = False
        skip_level = 0

        for line in lines:
            stripped = line.strip()
            level = calculate_heading_level(stripped)

            if level:
                if h1_count == 1:
                    # 跳过直到第一个 H2
                    if skip:
                        if level == 2:
                            skip = False
                            new_lines.append("#" + stripped[2:])
                        continue

                    if level >= 4:
                        skip_section = True
                        skip_level = level
                        continue
                    if skip_section and level <= skip_level:
                        skip_section = False

                    if skip_section:
                        continue
                    TemplateUtils.deal_with_level_2_3(level, stripped, line, new_lines)
                else:
                    if level >= 3:
                        skip_section = True
                        skip_level = level
                        continue
                    if skip_section and level <= skip_level:
                        skip_section = False
                    if skip_section:
                        continue
                    new_lines.append(line)
            else:
                if skip or skip_section:
                    continue
                new_lines.append(line)

        return "\n".join(new_lines)

    @classmethod
    def pdf_base64_to_markdown(cls, pdf_base64_string: str) -> str:
        """
        Converts a PDF document from a base64 string to Markdown.

        Args:
            base64_string (str): The base64-encoded string of the PDF document.
        """

        page_bookmarks, lines_with_size = preprocess_pdf(pdf_base64_string)

        if len(page_bookmarks) > 0:
            markdown_lines = process_with_bookmarks(lines_with_size, page_bookmarks)
            md_content = "\n".join(markdown_lines).strip()
            return md_content
        logger.error("Failed to convert pdf file to markdown, no page_bookmarks")
        raise CustomValueException(error_code=StatusCode.CONVERT_PDF_FILE_TO_MARKDOWN_FAILED.code,
                                   message=StatusCode.CONVERT_PDF_FILE_TO_MARKDOWN_FAILED.errmsg)

    @classmethod
    def word_base64_to_markdown(cls, base64_string: str) -> str:
        """
        Converts a Word document (DOCX) from a base64 string to Markdown.

        Args:
            base64_string (str): The base64-encoded string of the Word document.
            output_file_path (str, optional): Path to save the Markdown file. If not provided, 
                                            the Markdown content is only returned as a string.

        Returns:
            str: The converted Markdown content.
        """
        try:
            # Decode the base64 string to bytes
            docx_bytes = base64.b64decode(base64_string)

            docx_file = BytesIO(docx_bytes)

            doc = Document(docx_file)

            markdown_lines = []

            for element in doc.element.body:
                if element.tag.endswith('p'):
                    paragraph = TemplateUtils._get_paragraph_from_element(element, doc)
                    if paragraph and paragraph.text.strip():
                        markdown_line = TemplateUtils._process_paragraph(paragraph)
                        if markdown_line:
                            markdown_lines.append(markdown_line)  # 段落间添加空行

                elif element.tag.endswith('tbl'):
                    table = TemplateUtils._get_table_from_element(element, doc)
                    if table:
                        markdown_table = TemplateUtils._process_table(table)
                        if markdown_table:
                            markdown_lines.append(markdown_table)  # 表格间添加空行

            md_content = "\n".join(markdown_lines)
            return md_content

        except Exception as e:
            if LogManager.is_sensitive():
                logger.error("Error in word_base64_to_markdown: An error occurred while processing the Word document.")
                raise CustomValueException(
                    error_code=StatusCode.CONVERT_DOCX_FILE_FAILED.code,
                    message="Failed to convert word to markdown."
                ) from e

            logger.error(f"Error in word_base64_to_markdown: {str(e)}")
            raise CustomValueException(
                error_code=StatusCode.CONVERT_DOCX_FILE_FAILED.code,
                message=f"Failed to convert word to markdown : {str(e)}"
            ) from e

    @classmethod
    def _get_paragraph_from_element(cls, element, doc) -> Optional[Paragraph]:
        return Paragraph(element, doc)

    @classmethod
    def _get_table_from_element(cls, element, doc):
        return Table(element, doc)

    @classmethod
    def _process_table(cls, table: Table) -> str:
        """Convert a docx Table to Markdown format."""
        markdown_lines = []

        # Process header row
        header_cells = table.rows[0].cells
        header_line = "| " + " | ".join(cell.text.strip() for cell in header_cells) + " |"
        separator_line = "| " + " | ".join("---" for _ in header_cells) + " |"
        markdown_lines.append(header_line)
        markdown_lines.append(separator_line)

        # Process data rows
        for row in table.rows[1:]:
            row_cells = row.cells
            row_line = "| " + " | ".join(cell.text.strip() for cell in row_cells) + " |"
            markdown_lines.append(row_line)

        return "\n".join(markdown_lines)

    @classmethod
    def _process_paragraph(cls, paragraph: Paragraph) -> str:
        """Convert a docx Paragraph to Markdown format."""
        text = paragraph.text.strip()
        if not text:
            return ""

        # Determine heading level
        style_name = paragraph.style.name.lower()
        if style_name.startswith("heading"):
            try:
                level = int(style_name.split()[-1])
                return f"{'#' * level} {text}"
            except(ValueError, IndexError):
                return text  # Fallback to normal text if parsing fails
        elif paragraph.style.name.lower().startswith("hh"):
            try:
                level = int(style_name[2:])
                return f"{'#' * level} {text}"
            except(ValueError, IndexError):
                return text  # Fallback to normal text if parsing fails
        elif paragraph.style.name.lower().startswith("list"):
            return f"- {text}"
        else:
            return text
