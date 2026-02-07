# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
import base64
import io
import re

from bs4 import BeautifulSoup, NavigableString
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.styles.style import ParagraphStyle
from docx.text.paragraph import Paragraph
from latex2mathml.converter import convert as latex2mathml_convert
from mathml2omml import convert

HYPERLINK_URI = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"  # URI for word hyperlink
OMML_URI = "http://schemas.openxmlformats.org/officeDocument/2006/math"  # URI for word omml


def _get_style_def_by_tag(tag: str) -> str:
    """
    将 HTML 标签映射为语义名称。

    参数:
        tag (str): HTML 标签名称，例如 'h1', 'p', 'table', 'div' 等。

    返回:
        str: 对应的语义名称，例如 'heading1', 'paragraph', 'table', 或 'default'。
    """
    tag = tag.lower().strip("<>/")  # 清理标签格式

    # 处理 heading 标签
    if tag.startswith("h") and tag[1:].isdigit():
        level = int(tag[1:])
        if 1 <= level <= 9:
            return f"heading{level}"

    # 特定标签映射
    tag_map = {
        "title": "title",
        "p": "paragraph",
        "table": "table"
    }

    return tag_map.get(tag, "default")


def _get_style_by_tag(tag_name, style_dict, doc, default="Normal") -> ParagraphStyle:
    """
    从指定文件中读取样式配置，并返回指定键的样式名称。

    参数：
    - tag_name: html中的tag名
    - style_dict: 样式dict
    - doc: 加载的带样式的Document
    - default: 如果找不到键时返回的默认值

    返回：
    - 样式名称字符串
    """
    style_def = _get_style_def_by_tag(tag_name)  # style_def是我们自己定义的名字，这里根据html的tag名拿到style_def
    style_name = style_dict.get(style_def, default)  # 再根据style_def从模板json中读取到对应的docx style_name
    return doc.styles[style_name]


def _apply_style_font_on_para_run(p: Paragraph, style_rFonts) -> None:
    if style_rFonts is None:
        return

    # make sure there is rFonts
    for run in p.runs:
        e = run._element
        if e.rPr is None:
            e.insert(0, OxmlElement('w:rPr'))
        if e.rPr.rFonts is None:
            rFonts = OxmlElement('w:rFonts')
            e.rPr.append(rFonts)
        else:
            rFonts = e.rPr.rFonts

        # set run font
        for attr in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'):
            val = style_rFonts.get(qn(attr))
            if val:
                rFonts.set(qn(attr), val)


def _apply_inline_style(run, tag_name):
    rPr = run._r.get_or_add_rPr()

    if tag_name in ("strong", "b"):
        b = OxmlElement("w:b")
        rPr.append(b)

    if tag_name in ("em", "i"):
        i = OxmlElement("w:i")
        rPr.append(i)

    if tag_name == "u":
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rPr.append(u)


def _apply_style_font_on_run(run, style_rFonts) -> None:
    if style_rFonts is None:
        return

    e = run._element
    if e.rPr is None:
        e.insert(0, OxmlElement('w:rPr'))
    if e.rPr.rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        e.rPr.append(rFonts)
    else:
        rFonts = e.rPr.rFonts

    for attr in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'):
        val = style_rFonts.get(qn(attr))
        if val:
            rFonts.set(qn(attr), val)


def _add_hyperlink(paragraph, url, text):
    # 创建关系 id
    part = paragraph.part
    r_id = part.relate_to(
        url,
        HYPERLINK_URI,
        is_external=True
    )

    # 创建 <w:hyperlink>
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    # 创建 <w:r>
    r = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")

    # 超链接样式（蓝色 + 下划线）
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)

    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0000FF")
    rPr.append(color)

    r.append(rPr)

    # 文本节点
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)

    hyperlink.append(r)
    paragraph._p.append(hyperlink)


def _process_inline(p, node, style_rFonts, current_run=None):
    """递归处理段落内的所有 inline 节点"""

    # 纯文本
    if isinstance(node, NavigableString):
        text = str(node)
        if not text:
            return

        if current_run is None:
            run = p.add_run(text)
        else:
            run = current_run
            run.add_text(text)

        _apply_style_font_on_run(run, style_rFonts)
        return

    # 图片（通常自己一个 run，和 current_run 无强关联）
    if node.name == "img":
        src = node.get("src")
        if src and src.startswith("data:image"):
            header, b64data = src.split(",", 1)
            img_bytes = base64.b64decode(b64data)
            run = p.add_run()
            run.add_picture(io.BytesIO(img_bytes))
        return

    # 超链接：让 add_hyperlink 自己处理 run/样式
    if node.name == "a":
        href = node.get("href")
        text = node.get_text(strip=True)
        if href and text:
            _add_hyperlink(p, href, text)
        return

    # inline 标签（strong, b, em, i, u, etc.）
    if node.name in ("strong", "b", "em", "i", "u"):
        # 如果已有 run，就在这个 run 上叠加样式；否则新建一个 run
        run = current_run or p.add_run()
        _apply_style_font_on_run(run, style_rFonts)
        _apply_inline_style(run, node.name)

        for child in node.contents:
            _process_inline(p, child, style_rFonts, current_run=run)
        return

    # 其他标签 → 递归处理，保持 current_run 传递
    for child in node.contents:
        _process_inline(p, child, style_rFonts, current_run=current_run)


def _add_para_and_apply_style(doc, element, style_dict):
    style = _get_style_by_tag(element.name, style_dict, doc)
    p = doc.add_paragraph(style=style)

    style_rPr = style.element.get_or_add_rPr()
    style_rFonts = style_rPr.find(qn('w:rFonts'))

    for child in element.contents:
        _process_inline(p, child, style_rFonts)


def _insert_omml(paragraph, omml_xml: str):
    """向段落中插入 OMML 公式"""
    wrapped_xml = f''' <root xmlns:m="{OMML_URI}"> {omml_xml} </root> '''
    try:
        # 1. 解析 OMML 字符串为 python-docx 可识别的 XML
        root = parse_xml(wrapped_xml)
        omath = root[0]  # 取出真正的 <m:oMath> 节点

        # 2. 插入到 run 中
        run = paragraph.add_run()
        run._r.append(omath)

    except Exception as e:
        raise ValueError("insert omml to doc failed") from e


def _latex_to_omml(latex: str) -> str:
    """
    将 LaTeX 数学公式转换为 Word 可识别的 OMML XML 字符串。
    依赖：
        pip install latex2mathml
        pip install lxml
    参数：
        latex: 纯 LaTeX 数学表达式（不含 $）
    返回：
        OMML XML 字符串，可直接插入 python-docx
    """

    try:
        # 1. LaTeX → MathML
        mathml = latex2mathml_convert(latex)

        # 2. MathML → OMML（使用 mathml2omml-as）
        omml = convert(mathml)

        return omml
    except Exception as e:
        raise ValueError("transfer latex to omml failed") from e


def _add_latex_paragraph(doc, text, style=None):
    """
    将含有 $...$ / $$...$$ 的文本插入 Word，
    普通文本 → run
    公式 → OMML
    """
    inline_math = re.compile(r'\$(.+?)\$')
    block_math = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)

    # 1. 先处理块级公式 $$...$$
    pos = 0
    for m in block_math.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            p = doc.add_paragraph(before, style=style)

        latex = m.group(1).strip()
        omml = _latex_to_omml(latex)

        p = doc.add_paragraph(style=style)
        _insert_omml(p, omml)

        pos = m.end()

    # 剩余部分继续处理行内公式
    text = text[pos:]

    # 2. 行内公式处理 $...$
    p = doc.add_paragraph(style=style)
    pos = 0
    for m in inline_math.finditer(text):
        before = text[pos:m.start()]
        if before:
            p.add_run(before)

        latex = m.group(1).strip()
        omml = _latex_to_omml(latex)
        _insert_omml(p, omml)

        pos = m.end()

    # 3. 剩余普通文本
    if pos < len(text):
        p.add_run(text[pos:])


def _set_default_table_border(table):
    # 表格居中对齐
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 设置边框（模拟 Table Grid）
    tbl = table._element
    tblPr = tbl.xpath('./w:tblPr')[0]

    tblBorders = OxmlElement('w:tblBorders')

    for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        border = OxmlElement(f'w:{border_name}')
        border.set(qn('w:val'), 'single')  # 实线
        border.set(qn('w:sz'), '4')  # 线宽（1/8 pt）
        border.set(qn('w:space'), '0')  # 间距
        border.set(qn('w:color'), 'auto')  # 自动颜色
        tblBorders.append(border)

    tblPr.append(tblBorders)


def html_to_doc(doc, html, style_dict):
    soup = BeautifulSoup(html, 'html.parser')
    container = soup.find("div", class_="report-container")

    for element in container.children:
        if element.name is None:
            continue

        if element.name in [f"h{i}" for i in range(1, 9)]:
            _add_para_and_apply_style(doc, element, style_dict)

        elif element.name == 'p':
            text = element.get_text(strip=True)

            # 判断是否包含公式
            if '$' in text:
                paragraph_style = _get_style_by_tag("p", style_dict, doc)
                _add_latex_paragraph(
                    doc=doc,
                    text=text,
                    style=paragraph_style
                )
            else:
                _add_para_and_apply_style(doc, element, style_dict)

        elif element.name == 'blockquote':
            para = doc.add_paragraph(element.get_text(strip=True))
            para.paragraph_format.left_indent = Pt(18)
            para.paragraph_format.space_before = Pt(6)
            para.paragraph_format.space_after = Pt(6)

        elif element.name == 'ul' or element.name == 'ol':
            paragraph_style = _get_style_by_tag("p", style_dict, doc)
            for li in element.find_all('li'):
                p = doc.add_paragraph(li.get_text(strip=True), style=paragraph_style)
                style_rPr = paragraph_style.element.get_or_add_rPr()
                style_rFonts = style_rPr.find(qn('w:rFonts'))

                _apply_style_font_on_para_run(p, style_rFonts)

        elif element.name == 'table':
            table_style = _get_style_by_tag(element.name, style_dict, doc)
            rows = element.find_all('tr')
            if rows:
                cols_count = len(rows[0].find_all(['td', 'th']))
                table = doc.add_table(rows=len(rows), cols=cols_count)
                if table_style.type == WD_STYLE_TYPE.TABLE:
                    table.style = table_style
                else:
                    _set_default_table_border(table)
                for r_idx, row in enumerate(rows):
                    cells = row.find_all(['td', 'th'])
                    for c_idx, cell in enumerate(cells):
                        table.cell(r_idx, c_idx).text = cell.get_text(strip=True)
                        p = table.cell(r_idx, c_idx).paragraphs[0]
                        # set table fonts to paragraph default
                        paragraph_style = _get_style_by_tag("p", style_dict, doc)
                        # get rFonts in style
                        style_rPr = paragraph_style.element.get_or_add_rPr()
                        style_rFonts = style_rPr.find(qn('w:rFonts'))

                        _apply_style_font_on_para_run(p, style_rFonts)


def set_global_styles(doc, font_name="微软雅黑", font_size=11):
    normal_style = doc.styles['Normal']
    normal_font = normal_style.font
    normal_font.name = font_name
    normal_font.size = Pt(font_size)
    normal_style._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)

    heading_sizes = [24, 18, 16, 14, 12, 11]
    for i in range(1, 7):
        heading_style = doc.styles[f'Heading {i}']
        heading_font = heading_style.font
        heading_font.name = font_name
        heading_font.size = Pt(heading_sizes[i - 1])
        heading_font.italic = False
        heading_style._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)

    for style in doc.styles:
        if style.type == 1:  # Paragraph style
            pf = style.paragraph_format
            pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
            pf.line_spacing = 1.5
