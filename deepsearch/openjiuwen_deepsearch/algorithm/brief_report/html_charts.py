"""Brief HTML 报告的 ECharts 配置、修复与运行时注入。"""

import hashlib
import html
import json
import logging
import re
from pathlib import Path

from openjiuwen_deepsearch.algorithm.brief_report.html_safety import (
    HtmlStructureScanner,
    insert_before,
)


logger = logging.getLogger(__name__)

_CHART_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
_FORBIDDEN_URL_PAYLOADS = ("http://", "https://", "image://", "data:", "javascript:")
_GAP_SENSITIVE_SERIES_TYPES = frozenset({"line", "area"})
_RATIO_SERIES_KEYWORDS = (
    "占比", "占gdp", "比例", "份额", "share", "ratio", "rate",
    "percent", "percentage", "%",
)
_ECHARTS_ASSET_PATH = Path(__file__).parent / "assets" / "echarts.min.js"
ECHARTS_SHA256 = "bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1"
_ECHARTS_LIB_MARKER = "<!--openjiuwen:echarts-lib-->"
_ECHARTS_LIB_END = "<!--/openjiuwen:echarts-lib-->"
_CHART_SCRIPT_MARKER = "<!--openjiuwen:chart-init-->"
_CHART_SCRIPT_END = "<!--/openjiuwen:chart-init-->"
TEMPLATE_BLOCK_RE = re.compile(
    r'''(?is)<template\b(?=[^>]*\bid\s*=\s*'''
    r'''(?:"chart-configs"|'chart-configs'|chart-configs\b))[^>]*>'''
    r'''(.*?)</template\s*>'''
)
_CHART_PLACEHOLDER_RE = re.compile(
    r'(?is)<div\b[^>]*class="[^"]*echarts-chart[^"]*"[^>]*>.*?</div>'
)


def validate_chart_option(option: object) -> str | None:
    """递归校验 ECharts option。

    Args:
        option: 待校验的 ECharts 配置对象。

    Returns:
        首个发现的校验错误；配置合法时返回 ``None``。
    """
    if not isinstance(option, dict):
        return "chart option must be a JSON object"
    return _validate_option_node(option, "option")


def _validate_option_node(node: object, path: str) -> str | None:
    """递归检查 option 节点中的类型、URL 载荷和 formatter。

    Args:
        node: 当前待检查的 JSON 节点。
        path: 当前节点在 option 中的路径，用于生成可定位的错误信息。

    Returns:
        首个发现的校验错误；当前节点及其子节点合法时返回 ``None``。
    """
    if node is None or isinstance(node, (bool, int, float)):
        return None
    if isinstance(node, str):
        # 图表 option 最终会进入页面脚本，字符串中的 URL 载荷不能绕过离线安全边界。
        lowered = node.lower()
        for payload in _FORBIDDEN_URL_PAYLOADS:
            if payload in lowered:
                return f"{path} contains forbidden URL payload: {payload}"
        return None
    if isinstance(node, list):
        for index, item in enumerate(node):
            error = _validate_option_node(item, f"{path}[{index}]")
            if error:
                return error
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "formatter" and isinstance(value, str):
                if "<" in value or ">" in value:
                    return f"{path}.{key} formatter must not contain HTML tags"
            error = _validate_option_node(value, f"{path}.{key}")
            if error:
                return error
        return None
    return f"{path} has unsupported type {type(node).__name__}"


def _chart_axis_at(option: dict, axis_name: str, index: int = 0) -> dict | None:
    """读取指定索引的坐标轴配置，兼容对象和数组两种 ECharts 形式。

    Args:
        option: ECharts option 配置。
        axis_name: 坐标轴字段名，例如 ``xAxis`` 或 ``yAxis``。
        index: 坐标轴在数组中的索引。

    Returns:
        对应的坐标轴字典；字段不存在或类型不符时返回 ``None``。
    """
    axes = option.get(axis_name)
    if isinstance(axes, dict):
        return axes if index == 0 else None
    if isinstance(axes, list) and 0 <= index < len(axes) and isinstance(axes[index], dict):
        return axes[index]
    return None


def _chart_category_values(option: dict) -> list[object] | None:
    """提取分类 x 轴的数据列表。

    Args:
        option: ECharts option 配置。

    Returns:
        x 轴分类值列表；不是分类轴或缺少数据时返回 ``None``。
    """
    axis = _chart_axis_at(option, "xAxis")
    if not isinstance(axis, dict) or not isinstance(axis.get("data"), list):
        return None
    return axis["data"]


def _chart_data_value(item: object) -> object:
    """从 ECharts 数据项中提取用于缺失值判断的实际值。

    Args:
        item: ECharts 支持的原始值、数组、对象或 ``None``。

    Returns:
        数据项的数值部分；空数组和空值返回 ``None``。
    """
    if item is None:
        return None
    if isinstance(item, dict):
        return item.get("value")
    if isinstance(item, list):
        return item[-1] if item else None
    return item


def _align_named_chart_data(data: list[object], categories: list[object]) -> list[object] | None:
    """按分类轴顺序重排带名称的数据项。

    Args:
        data: 由 ``name`` 和 ``value`` 组成的 ECharts 数据项列表。
        categories: x 轴分类值列表。

    Returns:
        与分类轴顺序一致的数据列表；数据无法一一对应时返回 ``None``。
    """
    if not data or not all(
        isinstance(item, dict) and "name" in item and "value" in item for item in data
    ):
        return None
    category_keys = [str(category) for category in categories]
    data_by_name = {str(item["name"]): item for item in data}
    data_keys = list(data_by_name)
    if len(data_keys) != len(data) or any(key not in category_keys for key in data_keys):
        return None
    # ECharts 的 named data 可以乱序，按分类轴重排后才能保证数据与标签对齐。
    return [data_by_name.get(key) for key in category_keys]


def _chart_series_axis_name(option: dict, series: dict) -> str:
    """拼接系列名与所属 y 轴名称，供语义识别使用。

    Args:
        option: ECharts option 配置。
        series: 当前系列配置。

    Returns:
        经过大小写折叠的系列名和坐标轴名称。
    """
    axis_index = series.get("yAxisIndex", 0)
    if not isinstance(axis_index, int) or isinstance(axis_index, bool):
        axis_index = 0
    axis = _chart_axis_at(option, "yAxis", axis_index)
    axis_name = axis.get("name", "") if isinstance(axis, dict) else ""
    return f"{series.get('name', '')} {axis_name}".casefold()


def _is_ratio_chart_series(option: dict, series: dict) -> bool:
    """判断系列是否表达比例、占比或百分比语义。

    Args:
        option: ECharts option 配置。
        series: 当前系列配置。

    Returns:
        系列名或 y 轴名称包含比例关键词时返回 ``True``。
    """
    text = _chart_series_axis_name(option, series)
    return any(keyword.casefold() in text for keyword in _RATIO_SERIES_KEYWORDS)


def _legend_item_name(item: object) -> str | None:
    """提取 legend 项名称。

    Args:
        item: legend 支持的字符串或对象形式的数据项。

    Returns:
        legend 项名称；无法识别时返回 ``None``。
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("name"), str):
        return item["name"]
    return None


def _prune_chart_axes_and_legend(option: dict, series: list[dict]) -> None:
    """删除未使用的坐标轴和 legend 项，并重映射轴索引。

    Args:
        option: 需要原地修复的 ECharts option 配置。
        series: 修复后保留的系列列表。
    """
    y_axes = option.get("yAxis")
    if isinstance(y_axes, list):
        if not series:
            option["yAxis"] = []
        else:
            used_indexes: set[int] = set()
            for item in series:
                axis_index = item.get("yAxisIndex", 0)
                if not isinstance(axis_index, int) or isinstance(axis_index, bool):
                    axis_index = 0
                if 0 <= axis_index < len(y_axes):
                    used_indexes.add(axis_index)
            if used_indexes:
                # 删除坐标轴后，系列中的 yAxisIndex 也必须同步压缩到新数组索引。
                kept_indexes = sorted(used_indexes)
                index_map = {old: new for new, old in enumerate(kept_indexes)}
                option["yAxis"] = [y_axes[index] for index in kept_indexes]
                for item in series:
                    axis_index = item.get("yAxisIndex", 0)
                    if not isinstance(axis_index, int) or isinstance(axis_index, bool):
                        axis_index = 0
                    if "yAxisIndex" in item:
                        item["yAxisIndex"] = index_map.get(axis_index, 0)

    legend = option.get("legend")
    if isinstance(legend, dict) and isinstance(legend.get("data"), list):
        series_names = {
            item.get("name") for item in series if isinstance(item.get("name"), str)
        }
        if series_names:
            legend["data"] = [
                item for item in legend["data"] if _legend_item_name(item) in series_names
            ]


def normalize_chart_option(option: dict) -> tuple[dict, list[str]]:
    """按分类轴归一化图表数据，并降级不完整的比例类折线。

    Args:
        option: 需要原地归一化的 ECharts option 配置。

    Returns:
        二元组，分别为归一化后的 option 和语义修复警告列表。
    """
    categories = _chart_category_values(option)
    raw_series = option.get("series")
    if isinstance(raw_series, dict):
        series = [raw_series]
        series_is_object = True
    elif isinstance(raw_series, list):
        series = [item for item in raw_series if isinstance(item, dict)]
        series_is_object = False
    else:
        return option, []

    warnings: list[str] = []
    kept_series: list[dict] = []
    category_count = len(categories) if categories is not None else None
    for item in series:
        series_name = str(item.get("name") or "<unnamed>")
        series_type = str(item.get("type") or "").casefold()
        data = item.get("data")
        if not isinstance(data, list):
            kept_series.append(item)
            continue

        if categories is not None:
            has_named_data = bool(data) and all(
                isinstance(data_item, dict)
                and "name" in data_item
                and "value" in data_item
                for data_item in data
            )
            aligned = _align_named_chart_data(data, categories)
            if aligned is not None:
                data = aligned
                item["data"] = data
            elif has_named_data:
                # 无法和分类轴一一对应时，整条系列比错误展示错位数据更危险。
                warnings.append(f"chart_series_category_mismatch: {series_name}")
                continue
            elif len(data) != category_count:
                warnings.append(
                    f"chart_series_length_mismatch: {series_name} "
                    f"(expected {category_count}, got {len(data)})"
                )
                continue

        missing_indexes = [
            index for index, data_item in enumerate(data)
            if _chart_data_value(data_item) is None
        ]
        if series_type in _GAP_SENSITIVE_SERIES_TYPES:
            item["connectNulls"] = False
        if missing_indexes and _is_ratio_chart_series(option, item):
            # 比例序列的缺失值容易造成误导性连线，直接丢弃该系列并保留其余数据。
            missing_labels = (
                [str(categories[index]) for index in missing_indexes if categories is not None]
                or [str(index) for index in missing_indexes]
            )
            warnings.append(
                f"chart_series_dropped_incomplete: {series_name} "
                f"(missing categories: {', '.join(missing_labels)})"
            )
            continue
        kept_series.append(item)

    if series_is_object:
        option["series"] = kept_series[0] if len(kept_series) == 1 else kept_series
    else:
        option["series"] = kept_series
    _prune_chart_axes_and_legend(option, kept_series)
    return option, warnings


def validate_chart_configs(scanner: HtmlStructureScanner) -> tuple[list[str], list[str]]:
    """校验图表配置 JSON、id 格式与占位元素一一对应。

    Args:
        scanner: 已扫描待校验 HTML 的结构扫描器。

    Returns:
        二元组，分别为阻断错误列表和可继续生成的警告列表。
    """
    if scanner.chart_configs_raw is None and not scanner.chart_ids:
        return [], []
    if scanner.chart_configs_raw is None:
        return [], [
            "chart_config: placeholders present but template#chart-configs missing "
            f"(placeholders={sorted(scanner.chart_ids)}); they will be stripped at injection"
        ]
    try:
        configs = json.loads(scanner.chart_configs_raw)
    except ValueError as exc:
        return [f"chart_config: invalid JSON ({exc})"], []
    if not isinstance(configs, list) or not all(isinstance(item, dict) for item in configs):
        return ["chart_config: configs must be a JSON array of objects"], []
    config_ids: list[str] = []
    for index, config in enumerate(configs):
        config_id = config.get("id")
        if not isinstance(config_id, str) or not _CHART_ID_RE.match(config_id):
            return [f"chart_config: invalid chart id at index {index}"], []
        config_ids.append(config_id)
        error = validate_chart_option(config.get("option"))
        if error:
            return [f"chart_config: {error} (chart {config_id})"], []
    for chart_id in scanner.chart_ids:
        if not isinstance(chart_id, str) or not _CHART_ID_RE.match(chart_id):
            return [f"chart_config: invalid data-chart-id {chart_id!r}"], []
    # 占位符的文档顺序和 template 内配置顺序可能不同，因此按 id 集合比较。
    if sorted(config_ids) != sorted(scanner.chart_ids):
        return [
            "chart_config: chart ids between placeholders and configs must match one-to-one "
            f"(placeholders={sorted(scanner.chart_ids)}, configs={sorted(config_ids)})"
        ], []
    return [], []


def _has_renderable_chart(html_text: str) -> bool:
    """判断 HTML 中是否存在可渲染的图表配置或初始化脚本。

    Args:
        html_text: 待检查的 HTML 文本。

    Returns:
        存在初始化脚本或非空合法配置列表时返回 ``True``。
    """
    if _CHART_SCRIPT_MARKER in html_text:
        return True
    match = TEMPLATE_BLOCK_RE.search(html_text)
    if match is None:
        return False
    try:
        configs = json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError):
        return False
    return isinstance(configs, list) and bool(configs)


def _load_echarts_source() -> str:
    """读取并校验随包分发的 ECharts JavaScript 资源。

    Returns:
        ECharts 压缩 JavaScript 源码。

    Raises:
        FileNotFoundError: 随包资源文件不存在。
        ValueError: 资源内容的 SHA-256 与固定值不一致，或无法按 UTF-8 解码。
    """
    if not _ECHARTS_ASSET_PATH.is_file():
        raise FileNotFoundError("echarts vendor asset is missing")
    data = _ECHARTS_ASSET_PATH.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(data).hexdigest() != ECHARTS_SHA256:
        raise ValueError("echarts vendor asset sha256 mismatch")
    return data.decode("utf-8")


def inject_chart_scripts(html_text: str) -> str:
    """把 chart-configs template 转为固定的 ECharts 初始化脚本。

    Args:
        html_text: 已通过结构校验、包含可选 chart-configs template 的 HTML。

    Returns:
        移除配置 template、并在需要时插入初始化脚本后的 HTML。

    Raises:
        json.JSONDecodeError: template 中的配置不是合法 JSON。
    """
    match = TEMPLATE_BLOCK_RE.search(html_text)
    if match is None:
        stripped = _CHART_PLACEHOLDER_RE.sub("", html_text)
        if stripped != html_text:
            logger.warning(
                "[BriefHtmlReporter] Stripped %d chart placeholder(s) without template#chart-configs.",
                len(_CHART_PLACEHOLDER_RE.findall(html_text)),
            )
        return stripped
    configs = json.loads(html.unescape(match.group(1)))
    if not configs:
        without_template = TEMPLATE_BLOCK_RE.sub("", html_text)
        return _CHART_PLACEHOLDER_RE.sub("", without_template)
    for config in configs:
        option = config.get("option") or {}
        tooltip = option.get("tooltip")
        if isinstance(tooltip, dict):
            tooltip["renderMode"] = "richText"
        else:
            option["tooltip"] = {"renderMode": "richText"}
    # 转义尖括号，避免配置中的文本被浏览器解析成脚本上下文中的 HTML。
    payload = json.dumps(configs, ensure_ascii=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
    script = (
        f"{_CHART_SCRIPT_MARKER}<script>\n"
        "(function () {\n"
        '  "use strict";\n'
        f"  var configs = {payload};\n"
        "  var render = function () {\n"
        "    for (var i = 0; i < configs.length; i++) {\n"
        "      var el = document.querySelector('[data-chart-id=\"' + configs[i].id + '\"]');\n"
        "      if (!el) { continue; }\n"
        "      window.echarts.init(el).setOption(configs[i].option);\n"
        "    }\n"
        "  };\n"
        '  if (document.readyState === "loading") {\n'
        '    document.addEventListener("DOMContentLoaded", render);\n'
        "  } else { render(); }\n"
        "})();\n"
        f"</script>{_CHART_SCRIPT_END}"
    )
    without_template = TEMPLATE_BLOCK_RE.sub("", html_text)
    return insert_before(without_template, "</body>", f"{script}\n")


def inject_echarts_library(html_text: str) -> str:
    """按需把校验过的 echarts.min.js 以内联脚本注入 head。

    Args:
        html_text: 已完成图表初始化脚本注入的 HTML。

    Returns:
        存在可渲染图表时带有内联 ECharts 库的 HTML，否则原样返回。

    Raises:
        FileNotFoundError: 随包 ECharts 资源不存在。
        ValueError: ECharts 资源校验失败或无法解码。
    """
    if not _has_renderable_chart(html_text):
        return html_text
    source = _load_echarts_source()
    block = f"{_ECHARTS_LIB_MARKER}<script>{source}</script>{_ECHARTS_LIB_END}"
    if "</head>" in html_text:
        return insert_before(html_text, "</head>", f"{block}\n")
    return insert_before(html_text, "</body>", f"{block}\n")


def _placeholder_re_for(chart_id: str) -> re.Pattern[str]:
    """构造只匹配指定图表 id 占位元素的正则。

    Args:
        chart_id: 图表占位元素的原始 id。

    Returns:
        匹配该图表占位 ``div`` 的编译正则。
    """
    return re.compile(
        rf'(?is)<div\b[^>]*data-chart-id="{re.escape(chart_id)}"[^>]*>.*?</div>'
    )


def _drop_fragment_charts(fragment: str, section_id: str, reason: str) -> tuple[str, list[dict]]:
    """删除章节内无法安全解析的全部 ECharts 内容。

    Args:
        fragment: 包含章节 HTML 和可选图表内容的片段。
        section_id: 当前章节唯一标识。
        reason: 删除图表的原因，用于日志记录。

    Returns:
        移除图表内容后的片段，以及空的配置列表。
    """
    result = TEMPLATE_BLOCK_RE.sub("", fragment)
    result = _CHART_PLACEHOLDER_RE.sub("", result)
    logger.warning(
        "[BriefHtmlReporter] Dropped invalid section charts; section=%s reason=%s.",
        section_id,
        reason,
    )
    return result, []


def extract_fragment_charts(fragment: str, section_id: str) -> tuple[str, list[dict]]:
    """提取章节图表配置，删除无法安全渲染的图表并重命名成对 id。

    Args:
        fragment: 已清理的章节 HTML 片段。
        section_id: 当前章节唯一标识。

    Returns:
        移除 chart-configs template、完成 id 归一化后的片段，以及可用配置列表。
    """
    scanner = HtmlStructureScanner()
    scanner.feed(fragment)
    scanner.close()
    configs_by_id: dict[str, dict] = {}
    if scanner.chart_configs_raw is not None:
        try:
            parsed = json.loads(scanner.chart_configs_raw)
        except ValueError as exc:
            return _drop_fragment_charts(fragment, section_id, f"invalid JSON ({exc})")
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            return _drop_fragment_charts(
                fragment, section_id, "configs must be a JSON array of objects"
            )
        for config in parsed:
            chart_id = config.get("id")
            if not isinstance(chart_id, str) or not _CHART_ID_RE.match(chart_id):
                logger.warning(
                    "[BriefHtmlReporter] Dropped invalid section chart config; "
                    "section=%s chart_id=%r.",
                    section_id,
                    chart_id,
                )
                continue
            error = validate_chart_option(config.get("option"))
            if error:
                logger.warning(
                    "[BriefHtmlReporter] Dropped invalid section chart config; "
                    "section=%s chart_id=%s reason=%s.",
                    section_id,
                    chart_id,
                    error,
                )
                continue
            configs_by_id[chart_id] = config
    result = fragment
    kept_configs: list[dict] = []
    for index, old_id in enumerate(scanner.chart_ids, start=1):
        # 每次只处理一个占位符，避免重复 id 让一次配置被错误复制到多个节点。
        config = configs_by_id.pop(old_id, None)
        if config is None:
            result = _placeholder_re_for(old_id).sub("", result, count=1)
            logger.warning(
                "[BriefHtmlReporter] Section chart placeholder without config stripped; "
                "section=%s chart_id=%s.", section_id, old_id,
            )
            continue
        new_id = f"s{section_id}-c{index}"
        result = _placeholder_re_for(old_id).sub(
            lambda placeholder: placeholder.group(0).replace(
                f'data-chart-id="{old_id}"', f'data-chart-id="{new_id}"'
            ),
            result,
            count=1,
        )
        config["id"] = new_id
        kept_configs.append(config)
    result = TEMPLATE_BLOCK_RE.sub("", result)
    return result, kept_configs


def normalize_chart_configs(
    fragments: list[str], configs: list[dict]
) -> tuple[list[str], list[dict]]:
    """应用图表语义修复并移除已无可渲染序列的占位。

    Args:
        fragments: 按章节顺序排列的 HTML 片段。
        configs: 已提取的 ECharts 配置列表。

    Returns:
        二元组，分别为同步移除空图表占位后的片段列表和配置列表。
    """
    normalized_configs: list[dict] = []
    for config in configs:
        option = config.get("option")
        if isinstance(option, dict):
            _, warnings = normalize_chart_option(option)
            for warning in warnings:
                logger.warning(
                    "[BriefHtmlReporter] Chart semantic fallback: chart=%s %s.",
                    config.get("id"), warning,
                )
            if isinstance(option.get("series"), list) and not option["series"]:
                chart_id = config.get("id")
                if isinstance(chart_id, str):
                    pattern = _placeholder_re_for(chart_id)
                    fragments = [pattern.sub("", fragment, count=1) for fragment in fragments]
                logger.warning(
                    "[BriefHtmlReporter] Chart semantic fallback: chart=%s has no renderable series; "
                    "chart placeholder removed.", chart_id,
                )
                continue
        normalized_configs.append(config)
    return fragments, normalized_configs
