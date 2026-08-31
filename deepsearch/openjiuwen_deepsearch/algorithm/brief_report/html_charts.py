"""Brief HTML 报告的 ECharts 配置、修复与运行时注入。"""

import hashlib
import html
import json
import logging
import re
from pathlib import Path

from openjiuwen_deepsearch.algorithm.brief_report.html_safety import (
    _HtmlStructureScanner,
    _insert_before,
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
_TEMPLATE_BLOCK_RE = re.compile(
    r'''(?is)<template\b(?=[^>]*\bid\s*=\s*(?:"chart-configs"|'chart-configs'|chart-configs\b))[^>]*>(.*?)</template\s*>'''
)
_CHART_PLACEHOLDER_RE = re.compile(
    r'(?is)<div\b[^>]*class="[^"]*echarts-chart[^"]*"[^>]*>.*?</div>'
)


def validate_chart_option(option: object) -> str | None:
    """递归校验 ECharts option。"""
    if not isinstance(option, dict):
        return "chart option must be a JSON object"
    return _validate_option_node(option, "option")


def _validate_option_node(node: object, path: str) -> str | None:
    if node is None or isinstance(node, (bool, int, float)):
        return None
    if isinstance(node, str):
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
            if key == "formatter" and isinstance(value, str) and ("<" in value or ">" in value):
                return f"{path}.{key} formatter must not contain HTML tags"
            error = _validate_option_node(value, f"{path}.{key}")
            if error:
                return error
        return None
    return f"{path} has unsupported type {type(node).__name__}"


def _chart_axis_at(option: dict, axis_name: str, index: int = 0) -> dict | None:
    axes = option.get(axis_name)
    if isinstance(axes, dict):
        return axes if index == 0 else None
    if isinstance(axes, list) and 0 <= index < len(axes) and isinstance(axes[index], dict):
        return axes[index]
    return None


def _chart_category_values(option: dict) -> list[object] | None:
    axis = _chart_axis_at(option, "xAxis")
    if not isinstance(axis, dict) or not isinstance(axis.get("data"), list):
        return None
    return axis["data"]


def _chart_data_value(item: object) -> object:
    if item is None:
        return None
    if isinstance(item, dict):
        return item.get("value")
    if isinstance(item, list):
        return item[-1] if item else None
    return item


def _align_named_chart_data(data: list[object], categories: list[object]) -> list[object] | None:
    if not data or not all(
        isinstance(item, dict) and "name" in item and "value" in item for item in data
    ):
        return None
    category_keys = [str(category) for category in categories]
    data_by_name = {str(item["name"]): item for item in data}
    data_keys = list(data_by_name)
    if len(data_keys) != len(data) or any(key not in category_keys for key in data_keys):
        return None
    return [data_by_name.get(key) for key in category_keys]


def _chart_series_axis_name(option: dict, series: dict) -> str:
    axis_index = series.get("yAxisIndex", 0)
    if not isinstance(axis_index, int) or isinstance(axis_index, bool):
        axis_index = 0
    axis = _chart_axis_at(option, "yAxis", axis_index)
    axis_name = axis.get("name", "") if isinstance(axis, dict) else ""
    return f"{series.get('name', '')} {axis_name}".casefold()


def _is_ratio_chart_series(option: dict, series: dict) -> bool:
    text = _chart_series_axis_name(option, series)
    return any(keyword.casefold() in text for keyword in _RATIO_SERIES_KEYWORDS)


def _legend_item_name(item: object) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("name"), str):
        return item["name"]
    return None


def _prune_chart_axes_and_legend(option: dict, series: list[dict]) -> None:
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
    """按分类轴归一化图表数据，并降级不完整的比例类折线。"""
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


def _validate_chart_configs(scanner: _HtmlStructureScanner) -> tuple[list[str], list[str]]:
    """校验图表配置 JSON、id 格式与占位元素一一对应。"""
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
    if sorted(config_ids) != sorted(scanner.chart_ids):
        return [
            "chart_config: chart ids between placeholders and configs must match one-to-one "
            f"(placeholders={sorted(scanner.chart_ids)}, configs={sorted(config_ids)})"
        ], []
    return [], []


def _has_renderable_chart(html_text: str) -> bool:
    if _CHART_SCRIPT_MARKER in html_text:
        return True
    match = _TEMPLATE_BLOCK_RE.search(html_text)
    if match is None:
        return False
    try:
        configs = json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError):
        return False
    return isinstance(configs, list) and bool(configs)


def _load_echarts_source() -> str:
    if not _ECHARTS_ASSET_PATH.is_file():
        raise FileNotFoundError("echarts vendor asset is missing")
    data = _ECHARTS_ASSET_PATH.read_bytes()
    if hashlib.sha256(data).hexdigest() != ECHARTS_SHA256:
        raise ValueError("echarts vendor asset sha256 mismatch")
    return data.decode("utf-8")


def inject_chart_scripts(html_text: str) -> str:
    """把 chart-configs template 转为固定的 ECharts 初始化脚本。"""
    match = _TEMPLATE_BLOCK_RE.search(html_text)
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
        without_template = _TEMPLATE_BLOCK_RE.sub("", html_text)
        return _CHART_PLACEHOLDER_RE.sub("", without_template)
    for config in configs:
        option = config.get("option") or {}
        tooltip = option.get("tooltip")
        if isinstance(tooltip, dict):
            tooltip["renderMode"] = "richText"
        else:
            option["tooltip"] = {"renderMode": "richText"}
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
    without_template = _TEMPLATE_BLOCK_RE.sub("", html_text)
    return _insert_before(without_template, "</body>", f"{script}\n")


def inject_echarts_library(html_text: str) -> str:
    """按需把校验过的 echarts.min.js 以内联脚本注入 head。"""
    if not _has_renderable_chart(html_text):
        return html_text
    source = _load_echarts_source()
    block = f"{_ECHARTS_LIB_MARKER}<script>{source}</script>{_ECHARTS_LIB_END}"
    if "</head>" in html_text:
        return _insert_before(html_text, "</head>", f"{block}\n")
    return _insert_before(html_text, "</body>", f"{block}\n")


def _placeholder_re_for(chart_id: str) -> re.Pattern[str]:
    return re.compile(
        rf'(?is)<div\b[^>]*data-chart-id="{re.escape(chart_id)}"[^>]*>.*?</div>'
    )


def _extract_fragment_charts(fragment: str, section_id: str) -> tuple[str, list[dict]]:
    """提取章节图表配置、重命名成对 id，并移除未配对项。"""
    scanner = _HtmlStructureScanner()
    scanner.feed(fragment)
    scanner.close()
    configs_by_id: dict[str, dict] = {}
    if scanner.chart_configs_raw is not None:
        try:
            parsed = json.loads(scanner.chart_configs_raw)
        except ValueError as exc:
            raise ValueError(f"chart_config: invalid JSON ({exc})") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise ValueError("chart_config: configs must be a JSON array of objects")
        for config in parsed:
            chart_id = config.get("id")
            if not isinstance(chart_id, str) or not _CHART_ID_RE.match(chart_id):
                raise ValueError(f"chart_config: invalid chart id {chart_id!r}")
            error = validate_chart_option(config.get("option"))
            if error:
                raise ValueError(f"chart_config: {error} (chart {chart_id})")
            configs_by_id[chart_id] = config
    result = fragment
    kept_configs: list[dict] = []
    for index, old_id in enumerate(scanner.chart_ids, start=1):
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
    result = _TEMPLATE_BLOCK_RE.sub("", result)
    return result, kept_configs


def _normalize_chart_configs(
    fragments: list[str], configs: list[dict]
) -> tuple[list[str], list[dict]]:
    """应用图表语义修复并移除已无可渲染序列的占位。"""
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
