"""专业版与独立 Brief 工作流的 Prompt 隔离测试。"""

from pathlib import Path
import re


PROFESSIONAL_PROMPTS = [
    "outliner",
    "dep_driving_outliner",
    "outliner_interaction",
    "dep_driving_outliner_interaction",
    "planner",
    "dep_driving_planner",
    "collector_supervisor",
    "sub_section_outline",
    "sub_report_sidecar",
    "report_abstract_markdown",
    "report_conclusion_markdown",
]

# 允许携带 brief_outline 注入契约变量的 prompt（brief 大纲升级转换场景）。
# brief 结构约束仅作用于首版大纲生成，故只有 outliner.md 可携带；
# 两个交互 prompt 与普通专业版运行完全一致，不得再出现 brief_outline。
BRIEF_OUTLINE_EXEMPTED_PROMPTS = {
    "outliner",
}


def test_professional_prompts_have_no_brief_conditions():
    """专业版 Prompt 不得再携带 Brief Jinja 条件。

    例外：首版大纲 prompt（outliner.md）的 brief_outline 条件块属于 brief
    大纲升级注入契约（客户端回传 brief final_result.metadata 后的受控转换
    场景），允许保留；交互轮 prompt 不在例外之列。
    """
    directory = Path("openjiuwen_deepsearch/algorithm/prompts")
    for prompt_name in PROFESSIONAL_PROMPTS:
        for template_name in ("system.md", "user.md"):
            template_path = directory / prompt_name / template_name
            if not template_path.is_file():
                continue
            content = template_path.read_text(encoding="utf-8")
            identifier = f"{prompt_name}/{template_name}"
            assert "report_type" not in content, identifier
            if prompt_name == "outliner" and template_name == "user.md":
                assert not re.search(
                    r"\{%\s*(?:if|elif)[^%]*brief(?!_outline)", content, flags=re.IGNORECASE
                ), identifier
            else:
                assert not re.search(
                    r"\{%\s*(?:if|elif)[^%]*brief", content, flags=re.IGNORECASE
                ), identifier
