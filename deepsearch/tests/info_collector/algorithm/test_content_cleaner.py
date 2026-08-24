# -*- coding: UTF-8 -*-
"""搜索内容噪声清洗（content_cleaner）单元测试。

覆盖设计文档第十一节：L0 残留规范化、L1 行级样板删除（R1/R2/R3 正反向）、
L2 护栏回退（G1/G2/G3 含 fallback_reason）、门控、中英文样本、确定性。
样本均为合成文本（设计文档 3.2 节实测样本未入库，不做 fixture 固化）。
"""
from dataclasses import replace

import pytest

from openjiuwen_deepsearch.algorithm.research_collector.collector_function import (
    process_tavily_search_result,
    _normalize_web_search_item,
)
from openjiuwen_deepsearch.algorithm.research_collector.content_cleaner import (
    CleaningStats,
    ContentCleaningConfig,
    clean_web_content,
    coerce_content_cleaning_config,
    default_content_cleaning_config,
)


def _cfg_no_guard(**overrides):
    """构造关闭护栏与门控的测试配置（直测 L0/L1 规则本身）。"""
    base = replace(
        default_content_cleaning_config(),
        min_chars=0,
        min_keep_chars=0,
        min_keep_ratio=0.0,
        max_remove_ratio=1.0,
        anchor_keep_ratio=0.0,
    )
    return replace(base, **overrides) if overrides else base


def _cfg(**overrides):
    """在默认配置基础上覆盖指定字段的测试配置。"""
    return replace(default_content_cleaning_config(), **overrides)


# 合成正文段落（长行，无链接，不含特征词）
CN_BODY = (
    "人工智能是研究、开发用于模拟、延伸和扩展人的智能的理论、方法、技术及应用系统的一门新的技术科学，"
    "该领域的研究包括机器人、语言识别、图像识别、自然语言处理和专家系统等方向。"
)
EN_BODY = (
    "Machine learning is a field of artificial intelligence that focuses on building systems "
    "that learn from data and improve their performance over time without being explicitly programmed."
)


def _long_cn_body(times=8):
    return CN_BODY * times


def _long_en_body(times=6):
    return EN_BODY + " " + (EN_BODY + " ") * (times - 1) if times > 1 else EN_BODY


class TestL0Normalize:
    """L0 残留规范化（机械替换）"""

    def test_html_entity_unescape(self):
        content = "研究发现 A&amp;B 的占比达 &#52;&#48;%，详见说明&nbsp;文档。"
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "A&B" in cleaned
        assert "40%" in cleaned
        assert "&amp;" not in cleaned and "&#52;" not in cleaned
        assert "L0" in stats.applied_rules

    def test_image_syntax_preserved(self):
        """图片语法 ![alt](url) 原样保留，不删不归档（用户决策）。"""
        content = _long_en_body() + "\n\n![架构示意图](https://example.com/a.png)\n\n" + _long_en_body()
        cleaned, _ = clean_web_content(content, _cfg_no_guard())
        assert "![架构示意图](https://example.com/a.png)" in cleaned

    def test_markdown_link_restored_to_text(self):
        content = "详情请见[完整报告](https://example.com/report)与[数据附录](https://example.com/appendix)章节。"
        cleaned, _ = clean_web_content(content, _cfg_no_guard())
        assert "[完整报告](https://example.com/report)" not in cleaned
        assert "完整报告" in cleaned and "数据附录" in cleaned
        assert "https://example.com/report" not in cleaned

    def test_bare_url_line_preserved(self):
        """裸 URL 行不动，防误伤数据集链接表。"""
        content = ("数据集下载地址：\nhttps://example.com/dataset.zip\n"
                   "镜像地址：https://mirror.example.com/dataset.zip")
        cleaned, _ = clean_web_content(content, _cfg_no_guard())
        assert "https://example.com/dataset.zip" in cleaned
        assert "https://mirror.example.com/dataset.zip" in cleaned

    def test_orphan_html_tags_removed(self):
        content = "<div>\n第一段正文内容保持完整不变。\n</div>\n\n<br/>\n\n第二段正文内容同样保持完整。"
        cleaned, _ = clean_web_content(content, _cfg_no_guard())
        assert "<div>" not in cleaned and "</div>" not in cleaned and "<br/>" not in cleaned
        assert "第一段正文内容保持完整不变。" in cleaned

    def test_inline_angle_bracket_text_preserved(self):
        """非孤立（紧贴文字的）尖括号写法不删，防误伤比较式/泛型。"""
        content = "泛型写法 List<T> 与比较式 a<b>c 在正文中应原样保留，不被当作标签残留删除。"
        cleaned, _ = clean_web_content(content, _cfg_no_guard())
        assert cleaned == content

    def test_blank_lines_compressed(self):
        content = "第一段。\n\n\n\n\n第二段。"
        cleaned, _ = clean_web_content(content, _cfg_no_guard())
        assert "第一段。\n\n第二段。" == cleaned

    def test_crlf_blank_lines_compressed(self):
        """CRLF 文档连续 ≥3 空行同样压缩为单个空行。"""
        content = "第一段。\r\n\r\n\r\n\r\n第二段。"
        cleaned, _ = clean_web_content(content, _cfg_no_guard())
        assert "第一段。\n\n第二段。" == cleaned

    def test_escaped_html_tag_preserved_as_text(self):
        """转义标签 &lt;div&gt; 是正文内容（HTML 教程常见），反转义为可见文本保留，
        不再被孤立标签规则删除（孤立标签删除先于实体反转义执行）。"""
        content = ("在页面布局开发中可以使用容器标签组织内容，这是常见做法，需要开发者熟练掌握布局语义。\n\n"
                   "&lt;div&gt;\n\n"
                   "上文提到的标签属于块级元素，使用时需要注意文档流与盒模型的具体行为差异。") * 20
        cleaned, stats = clean_web_content(content, _cfg(min_chars=20))
        assert stats.fallback_reason is None
        assert "<div>" in cleaned
        assert "&lt;" not in cleaned


class TestL1R1LinkListBlock:
    """R1 链接列表块：≥3 行 且 链接行占比 ≥60% 且 平均行长 ≤40"""

    def test_nav_link_block_removed(self):
        nav = ("[首页](/home) [资讯](/news) [专栏](/col)\n"
               "[报告](/r1) [研究](/r2) [数据](/r3)\n"
               "[关于](/about) [联系](/contact) [帮助](/help)")
        content = _long_cn_body() + "\n\n" + nav + "\n\n" + _long_cn_body()
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "首页" not in cleaned and "报告](/r1)" not in cleaned
        assert "R1" in stats.applied_rules
        assert CN_BODY[:20] in cleaned

    def test_long_line_toc_block_kept(self):
        """同样行数但长行（正文目录型段落，行长超阈值）不删。"""
        toc = ("[2025年中国人工智能产业全景深度研究报告完整版](https://example.com/report/2025)\n"
               "[2025年全球机器人行业市场分析与投资前景预测](https://example.com/report/2026)\n"
               "[2025年自然语言处理技术演进路线与落地案例集](https://example.com/report/2027)")
        content = _long_cn_body() + "\n\n" + toc + "\n\n" + _long_cn_body()
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "R1" not in stats.applied_rules
        assert "2025年中国人工智能产业全景深度研究报告完整版" in cleaned

    def test_few_lines_link_block_kept(self):
        """不足 3 行的链接块不删。"""
        nav = "[首页](/home) [资讯](/news)\n[关于](/about) [联系](/contact)"
        content = _long_cn_body() + "\n\n" + nav + "\n\n" + _long_cn_body()
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "R1" not in stats.applied_rules
        assert "首页" in cleaned


class TestL1R2ChromeBlock:
    """R2 chrome 块：命中特征词 且（链接行占比 ≥40% 或 平均行长 ≤25），双条件防误伤"""

    def test_long_sentence_with_feature_word_kept(self):
        """含"版权所有"的长句正文不删（行长超阈值、无链接）。"""
        body = ("本报告版权所有归某某研究机构所有，任何单位和个人未经授权不得转载、摘编或利用其它方式使用本报告内容，"
                "违者将依法追究相应法律责任。")
        content = _long_cn_body() + "\n\n" + body + "\n\n" + _long_cn_body()
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "R2" not in stats.applied_rules
        assert "本报告版权所有归某某研究机构所有" in cleaned

    def test_short_line_chrome_block_removed(self):
        """含特征词且短行的块删除。"""
        chrome = "免责声明\n关注我们\n上一篇\n下一篇"
        content = _long_cn_body() + "\n\n" + chrome + "\n\n" + _long_cn_body()
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "R2" in stats.applied_rules
        assert "免责声明" not in cleaned

    def test_link_dense_chrome_block_removed(self):
        """含特征词且高链接密度的块删除。"""
        chrome = ("[Subscribe](/subscribe) [Sign Up](/signup) [Follow us](/follow)\n"
                  "[Home](/) [Topics](/topics) [Products](/products)\n"
                  "[About](/about) [Contact](/contact) [Help](/help)")
        content = _long_en_body() + "\n\n" + chrome + "\n\n" + _long_en_body()
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "Subscribe" not in cleaned
        assert ("R1" in stats.applied_rules) or ("R2" in stats.applied_rules)


class TestL1R3TailBlock:
    """R3 尾部样板块：文档末尾 20% 区域 且 命中尾部特征词 ≥2 个"""

    def test_tail_boilerplate_removed(self):
        tail = ("本网站所有内容版权所有，未经书面授权不得转载、摘编或利用其它方式使用本站内容资源。\n"
                "隐私政策声明：我们高度重视用户个人信息与隐私保护，详情请参阅本站隐私政策说明页面。")
        body = _long_cn_body(10)
        content = body + "\n\n" + body + "\n\n" + tail
        # 确认尾块处于文档末尾 20% 区域内
        assert content.index(tail) / len(content) >= 0.8
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "R3" in stats.applied_rules
        assert "版权所有" not in cleaned

    def test_same_block_in_middle_kept(self):
        """同型块位于文档中部时不删（位置条件不满足）。"""
        middle = ("本网站所有内容版权所有，未经书面授权不得转载、摘编或利用其它方式使用本站内容资源。\n"
                  "隐私政策声明：我们高度重视用户个人信息与隐私保护，详情请参阅本站隐私政策说明页面。")
        body = _long_cn_body(10)
        content = middle + "\n\n" + body + "\n\n" + body
        assert content.index(middle) / len(content) < 0.8
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert "R3" not in stats.applied_rules
        assert "版权所有" in cleaned

    def test_oversized_block_not_evaluated(self):
        """超过 5000 字符的块（多为正文容器）不参与 L1 评估。"""
        long_block = (CN_BODY + "版权所有。") * 60  # 单块 >5000 字符且含特征词
        content = long_block + "\n\n" + _long_cn_body()
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert not stats.applied_rules
        assert cleaned == content  # 含特征词的正文容器块未被删


class TestGuardFallback:
    """L2 护栏：任一触发整体回退原文，fallback_reason 正确"""

    def test_g1_min_keep_chars_fallback(self):
        """清洗后短于 min_keep_chars → 回退，fallback_reason=min_keep。"""
        nav = ("[首页](/home) [资讯](/news) [专栏](/col)\n"
               "[报告](/r1) [研究](/r2) [数据](/r3)\n"
               "[关于](/about) [联系](/contact) [帮助](/help)")
        content = "正文只有很短的一段。" + "\n\n" + nav
        cfg = _cfg(min_chars=20, min_keep_chars=500, min_keep_ratio=0.0)
        cleaned, stats = clean_web_content(content, cfg)
        assert cleaned == content
        assert stats.fallback_reason == "min_keep"
        assert stats.cleaned_chars == stats.raw_chars
        assert stats.removed_ratio == 0.0

    def test_g2_max_remove_ratio_fallback(self):
        """删除占比超过 max_remove_ratio → 回退，fallback_reason=max_remove_ratio。"""
        nav = ("[首页](/home) [资讯](/news) [专栏](/col)\n"
               "[报告](/r1) [研究](/r2) [数据](/r3)\n"
               "[关于](/about) [联系](/contact) [帮助](/help)")
        body = "这一段正文长度有限，只占总内容的小部分，清洗后保留占比低于四成门限。" + CN_BODY
        content = body + "\n\n" + nav + "\n\n" + nav
        removed = len(content) - len(body)
        assert removed / len(content) > 0.6
        # 压低 G1 门限，确保先越过 G1 再触发 G2
        cfg = _cfg(min_chars=20, min_keep_chars=0, min_keep_ratio=0.0)
        cleaned, stats = clean_web_content(content, cfg)
        assert cleaned == content
        assert stats.fallback_reason == "max_remove_ratio"

    def test_g3_anchor_keep_fallback(self):
        """数字锚点保留率不足 → 回退，fallback_reason=anchor_keep（备案号为长数字锚点）。"""
        tail = "ICP备12345678号-1\n公网安备 11010502099999号\n版权所有\n关注我们"
        body = _long_cn_body(10)
        content = body + "\n\n" + body + "\n\n" + tail
        cfg = _cfg(min_chars=20)
        cleaned, stats = clean_web_content(content, cfg)
        assert cleaned == content
        assert stats.fallback_reason == "anchor_keep"
        # 回退时保留规则命中记录用于排障
        assert stats.applied_rules

    def test_no_change_no_fallback(self):
        """未删除任何内容时不触发护栏。"""
        content = _long_cn_body(10)
        cleaned, stats = clean_web_content(content, _cfg(min_chars=20))
        assert cleaned == content
        assert stats.fallback_reason is None
        assert stats.applied_rules == []


class TestG3AnchorMasking:
    """G3 URL 掩蔽口径：URL 路径内数字不是事实锚点；两侧同一掩蔽+提取函数做集合比较"""

    def test_r1_numeric_short_url_nav_removed_without_fallback(self):
        """R1 删除带数字短链（/a/12345）的导航块后不回退——URL 内数字不计入锚点。"""
        body = "新能源汽车产业链上下游协同发展，电池技术迭代加快，充电基础设施持续完善。" * 50
        nav = ("[报告一](/a/12345) [报告二](/a/23456)\n"
               "[报告三](/a/34567) [报告四](/a/45678)\n"
               "[报告五](/a/56789) [报告六](/a/67890)")
        content = body + "\n\n" + nav
        cleaned, stats = clean_web_content(content, _cfg(min_chars=20))
        assert stats.fallback_reason is None
        assert "R1" in stats.applied_rules
        assert stats.removed_ratio > 0
        assert "报告一" not in cleaned

    def test_long_numeric_url_link_restore_without_fallback(self):
        """幸存内容的长数字 URL 经 L0 链接还原剥掉后不触发 anchor_keep 回退。"""
        body = "人工智能产业规模持续扩大，技术演进路线清晰，应用场景不断拓展深化。" * 60
        related = "\n".join(
            f"[深度报告标题第{i}期](/article/2025081{i:04d}/detail.shtml)" for i in range(12)
        )
        content = body + "\n\n" + related
        cleaned, stats = clean_web_content(content, _cfg(min_chars=20))
        assert stats.fallback_reason is None
        # URL 已被链接还原剥掉，锚文本保留
        assert "2025081" not in cleaned
        assert "深度报告标题第0期" in cleaned

    def test_thousand_separator_anchors_kept_no_fallback(self):
        """千分位数字（1234,567 / 1,234.5%）正文原样保留时不误回退（归一化两侧对称）。"""
        body = ("该项工程总投资1234,567万元，建设周期跨越多个年度，"
                "满意度达1,234.5%的受访者认为政策效果显著。") * 40
        nav = ("[首页](/home) [资讯](/news) [专栏](/col)\n"
               "[报告](/r1) [研究](/r2) [数据](/r3)\n"
               "[关于](/about) [联系](/contact) [帮助](/help)")
        content = body + "\n\n" + nav
        cleaned, stats = clean_web_content(content, _cfg(min_chars=20))
        assert stats.fallback_reason is None
        assert "R1" in stats.applied_rules
        assert "1234,567" in cleaned
        assert "1,234.5%" in cleaned


class TestGating:
    """门控：开关关闭 / 原文短于 min_chars 时跳过清洗"""

    def test_disabled_returns_original(self):
        nav = ("[首页](/home) [资讯](/news) [专栏](/col)\n"
               "[报告](/r1) [研究](/r2) [数据](/r3)\n"
               "[关于](/about) [联系](/contact) [帮助](/help)")
        content = _long_cn_body() + "\n\n" + nav
        cleaned, stats = clean_web_content(content, _cfg(enabled=False, min_chars=0))
        assert cleaned == content
        assert stats.applied_rules == []
        assert stats.fallback_reason is None

    def test_short_content_skipped(self):
        content = "短摘要[链接](https://example.com/a)&amp;实体。"
        cleaned, stats = clean_web_content(content, _cfg(min_chars=1500))
        assert cleaned == content
        assert stats.raw_chars == stats.cleaned_chars

    def test_full_text_field_not_cleaned(self):
        """学术 full_text 字段不清洗；content 清洗不影响 full_text。"""
        nav = ("[Home](/) [Topics](/t) [Products](/p)\n"
               "[Login](/login) [Sign Up](/su) [Subscribe](/sub)\n"
               "[About](/about) [Contact](/contact) [Help](/help)")
        noisy = _long_en_body(8) + "\n\n" + nav
        item = {
            "title": "Paper",
            "url": "https://example.com/paper",
            "content": noisy,
            "full_text_status": "available",
            "full_text": noisy,
        }
        normalized = _normalize_web_search_item(item, cleaning_config=_cfg())
        assert normalized["content"] != noisy
        assert "Home" not in normalized["content"]
        # full_text 原样保留（仅受既有 10000 字符截断约束）
        assert normalized["full_text"] == noisy[:10000]
        assert "Home" in normalized["full_text"]


class TestNormalizeIntegration:
    """_normalize_web_search_item 挂点集成"""

    def test_cleaning_applied_at_normalize(self):
        nav = ("[首页](/home) [资讯](/news) [专栏](/col)\n"
               "[报告](/r1) [研究](/r2) [数据](/r3)\n"
               "[关于](/about) [联系](/contact) [帮助](/help)")
        content = _long_cn_body(20) + "\n\n" + nav
        assert len(content) >= _cfg().min_chars
        item = {"title": "T", "url": "https://example.com/a", "content": content}
        normalized = _normalize_web_search_item(item, cleaning_config=_cfg())
        assert len(normalized["content"]) < len(content)
        assert "帮助" not in normalized["content"]
        assert "content_cleaning" not in normalized

    def test_short_content_passthrough(self):
        """短于门控的文档内容原样透传。"""
        item = {"title": "T", "url": "https://example.com/a", "content": "短内容"}
        normalized = _normalize_web_search_item(item, cleaning_config=_cfg())
        assert normalized["content"] == "短内容"
        assert "content_cleaning" not in normalized

    def test_default_config_from_service_config(self):
        """不传 cleaning_config 时按默认值构造（默认开启）。"""
        cfg = default_content_cleaning_config()
        assert cfg.enabled is True
        assert cfg.min_chars > 0

    def test_agent_input_dict_config_override(self):
        """agent_input 注入 dict 配置可覆盖默认值（如关闭开关）。"""
        nav = ("[首页](/home) [资讯](/news) [专栏](/col)\n"
               "[报告](/r1) [研究](/r2) [数据](/r3)\n"
               "[关于](/about) [联系](/contact) [帮助](/help)")
        content = _long_cn_body(20) + "\n\n" + nav
        agent_input = {
            "messages": [],
            "web_page_search_record": [],
            "local_text_search_record": [],
            "other_tool_record": [],
            "content_cleaning_config": {"enabled": False},
        }
        result, _ = process_tavily_search_result(
            agent_input, [{"title": "T", "url": "https://example.com/a", "content": content}]
        )
        assert result[0]["content"] == content
        assert "content_cleaning" not in result[0]


class TestMultilingualSamples:
    """中英文样本各 ≥3 个：导航型/页脚型/推荐列表型"""

    @pytest.mark.parametrize("noise", [
        # 导航型
        "[首页](/home) [资讯](/news) [专栏](/col)\n"
        "[报告](/r1) [研究](/r2) [数据](/r3)\n"
        "[关于](/about) [联系](/contact) [帮助](/help)",
        # 推荐列表型（含特征词的相关阅读，短行）
        "相关阅读\n[今日要闻](/n1) [热点追踪](/n2)\n[专题报道](/n3) [深度分析](/n4)\n分享到\n关注我们",
        # 页脚型（短行多特征词）
        "免责声明\n违法和不良信息举报\n上一篇\n下一篇",
    ])
    def test_chinese_noise_removed(self, noise):
        content = _long_cn_body(10) + "\n\n" + noise + "\n\n" + _long_cn_body(10)
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert stats.applied_rules
        assert stats.removed_ratio > 0
        assert CN_BODY[:20] in cleaned

    @pytest.mark.parametrize("noise", [
        # 导航型
        "[Home](/) [News](/n) [Docs](/d)\n"
        "[Blog](/b) [Price](/p) [API](/api)\n"
        "[About](/a) [Contact](/c) [Help](/h)",
        # 订阅型（特征词 + 链接块）
        "[Subscribe](/subscribe) [Sign Up](/signup)\n[Follow us](/follow) [Login](/login)\n[Home](/) [Topics](/t)",
        # 页脚型
        "All rights reserved.\nPrivacy Policy\nCookie Policy\nFollow us",
    ])
    def test_english_noise_removed(self, noise):
        content = _long_en_body(8) + "\n\n" + noise + "\n\n" + _long_en_body(8)
        cleaned, stats = clean_web_content(content, _cfg_no_guard())
        assert stats.applied_rules
        assert stats.removed_ratio > 0
        assert EN_BODY[:20] in cleaned

    @pytest.mark.parametrize("body", [
        _long_cn_body(10),
        _long_en_body(8),
        "发展和改革委员会近日发布通知，部署相关工作安排。" * 12,
        "The committee published its annual report on industrial policy and regional development. " * 10,
    ])
    def test_clean_body_untouched(self, body):
        """干净正文（无噪声）不删任何内容且不触发回退（低门控确保规则真正跑过）。"""
        cleaned, stats = clean_web_content(body, _cfg(min_chars=20))
        assert cleaned == body
        assert stats.applied_rules == []
        assert stats.fallback_reason is None
        assert stats.removed_ratio == 0.0


class TestDeterminism:
    """确定性：同输入两次清洗输出一致（去重键稳定的前提）"""

    def test_same_input_same_output(self):
        tail = "免责声明\n关注我们\n上一篇\n下一篇"
        content = _long_cn_body(10) + "\n\n" + tail + "\n\n" + _long_cn_body(10)
        first_text, first_stats = clean_web_content(content, _cfg())
        second_text, second_stats = clean_web_content(content, _cfg())
        assert first_text == second_text
        assert first_stats.to_dict() == second_stats.to_dict()

    def test_coerce_config_stable(self):
        """配置归一化：None/对象/dict 三种输入行为确定。"""
        default_cfg = coerce_content_cleaning_config(None)
        assert default_cfg == default_content_cleaning_config()
        assert coerce_content_cleaning_config(default_cfg) is default_cfg
        overridden = coerce_content_cleaning_config({"enabled": False, "unknown_key": 1})
        assert overridden.enabled is False
        assert overridden.min_chars == default_cfg.min_chars

    def test_coerce_config_ignores_wrong_typed_values(self):
        """dict 注入类型错的值视同未知键忽略、回退默认值；归一化链路不抛异常。"""
        default_cfg = default_content_cleaning_config()
        # 字符串 / None 注入被忽略
        coerced = coerce_content_cleaning_config({"min_chars": "abc", "enabled": "yes"})
        assert coerced.min_chars == default_cfg.min_chars
        assert coerced.enabled == default_cfg.enabled
        assert coerce_content_cleaning_config({"min_chars": None}).min_chars == default_cfg.min_chars
        # bool 是 int 子类：int 字段拒收 bool；bool 字段拒收 int
        assert coerce_content_cleaning_config({"min_chars": True}).min_chars == default_cfg.min_chars
        assert coerce_content_cleaning_config({"enabled": 1}).enabled == default_cfg.enabled
        # float 字段收 int（合法数值）
        assert coerce_content_cleaning_config({"max_remove_ratio": 1}).max_remove_ratio == 1
        # 归一化链路不炸：类型错的注入回退默认值后正常清洗/透传
        item = {"title": "T", "url": "https://example.com/a", "content": "x" * 2000}
        normalized = _normalize_web_search_item(
            item, cleaning_config=coerce_content_cleaning_config({"min_chars": "abc"}))
        assert normalized["content"] == "x" * 2000
