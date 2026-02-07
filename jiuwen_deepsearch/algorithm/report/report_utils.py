# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Tuple

from jiuwen_deepsearch.common.common_constants import CHINESE, ENGLISH


def _has_mixed_unit_separators(unit: str) -> bool:
    unit_lower = unit.lower()
    return any(sep in unit for sep in ("或", "/", "|", ",", ";")) or " and " in unit_lower


def validate_visualization_extraction_schema(payload: dict) -> bool:
    """
    Validate stage-1 visualization extraction output schema:
    {
      "image_title": str,
      "image_type": "pie|line|timeline|bar",
      "records": [[x: str, value_string: str, unit_string: str], ...]
    }
    """
    if not isinstance(payload, dict):
        return False

    image_title = payload.get("image_title", "")
    image_type = payload.get("image_type", "")
    records = payload.get("records", [])
    if not (
        isinstance(image_title, str)
        and image_type in ("bar", "line", "pie", "timeline")
        and isinstance(records, list)
    ):
        return False

    for row in records:
        if not isinstance(row, list) or len(row) != 3:
            return False
        x, value_str = row[0], row[1]
        unit_str = row[2]
        if not (
            isinstance(x, str) and isinstance(value_str, str) and isinstance(unit_str, str)
        ):
            return False
        if not x.strip() or not value_str.strip():
            return False
        if image_type != "timeline" and not unit_str:
            return False
        # Prevent mixed/ambiguous unit strings from reaching normalization/mermaid steps.
        if image_type != "timeline" and _has_mixed_unit_separators(unit_str):
            return False

    return True


def validate_visualization_normalization_schema(
    normalized_payload: dict,
    image_type: str,
) -> bool:
    """
    Validate stage-2 normalization output schema:
    {
      "unit": str,
      "records": [[x: str, value: number], ...]
    }
    """
    if not isinstance(normalized_payload, dict) or image_type not in ("bar", "line", "pie"):
        return False

    unit = normalized_payload.get("unit", "")
    records = normalized_payload.get("records", [])
    if not isinstance(unit, str) or not unit or _has_mixed_unit_separators(unit):
        return False
    if not isinstance(records, list):
        return False

    for row in records:
        if not isinstance(row, list) or len(row) != 2:
            return False
        x, value = row[0], row[1]
        if not (
            isinstance(x, str)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
        ):
            return False
        if image_type == "pie" and float(value) < 0:
            return False

    return True


class ArticlePart:
    parts = ["abstract", "conclusion", "reference"]
    patterns = {
        "abstract": {
            CHINESE: r"摘要",
            ENGLISH: r"Abstract",
        },
        "conclusion": {
            CHINESE: r"结论",
            ENGLISH: r"Conclusion",
        },
        "reference": {
            CHINESE: r"参考文章",
            ENGLISH: r"References",
        },
    }
    not_found_prompts = {
        "abstract": {
            CHINESE: "# 摘要\n\n[未能从生成内容中提取到摘要]",
            ENGLISH: "# Abstract\n\n[No abstract could be extracted from the generated content]",
        },
        "conclusion": {
            CHINESE: "# 结论\n\n[未能从生成内容中提取到结论]",
            ENGLISH: "# Conclusion\n\n[No conclusion could be extracted from the generated content]",
        },
        "reference": {
            CHINESE: "# 参考文章\n\n[未能从生成内容中提取到参考文章]",
            ENGLISH: "# Reference Articles\n\n[No reference could be extracted from the generated content]",
        },
    }
    titles = {
        "abstract": {CHINESE: "# 摘要\n\n", ENGLISH: "# Abstract\n\n"},
        "conclusion": {CHINESE: "# 结论\n\n", ENGLISH: "# Conclusion\n\n"},
        "reference": {CHINESE: "# 参考文章\n\n", ENGLISH: "# Reference Articles\n\n"},
    }

    @classmethod
    def get_not_found_prompt(cls, part, lang):
        """Get not found language prompt by language"""
        return cls.not_found_prompts.get(part, {}).get(lang, "")

    @classmethod
    def get_title(cls, part, lang):
        """Get title by language"""
        return cls.titles.get(part, {}).get(lang, "")


class MarkdownOutlineRenumber:
    def __init__(self):
        self.counters = {}  # Store counters for each level
        self.prev_level = 0  # Previous header level
        self.history = []
        self.in_code_block = False  # Whether inside a code block (``` ... ```)
        self.in_math_block = False  # Whether inside a math block ($$ ... $$)

    @staticmethod
    def _parse_header(match) -> Tuple[int, str, str]:
        """parse markdown header into parts and calculate level"""
        # Extract full match and groups
        full_match = match.group(0)  # Entire regex match
        outline_part = match.group(1)  # Continuous '#' part (e.g. #, ##, ###)

        # Compute header level
        level = outline_part.count("#")
        return level, outline_part, full_match

    def renumber_headers(self, content: str) -> str:
        """renumber subsection header number in general report"""
        pattern = r"^ *(#{1,3}(?!\#)) +([0-9.]*) *"
        lines = content.split("\n")
        output_lines = []

        for line in lines:
            stripped = line.strip()

            # --- Detect code fence (``` ... ```) start/end ---
            if re.match(r"^ *```.*$", line):
                self.in_code_block = not self.in_code_block
                output_lines.append(line)
                continue

            # --- Detect math block ($$ ... $$) start/end ---
            if re.match(r"^ *\$\$ *$", line):
                self.in_math_block = not self.in_math_block
                output_lines.append(line)
                continue

            # --- Inside code/math blocks: skip header handling ---
            if self.in_code_block or self.in_math_block:
                output_lines.append(line)
                continue

            # --- Indented code blocks (>= 4 spaces or tab) ---
            if line.startswith("    ") or line.startswith("\t"):
                output_lines.append(line)
                continue

            # --- Blockquotes (lines starting with '>') ---
            if stripped.startswith(">"):
                output_lines.append(line)
                continue

            # --- Other cases: normal header processing ---
            new_line = re.sub(pattern, self._replace_header, line)
            output_lines.append(new_line)

        return "\n".join(output_lines)

    def _update_counters(self, level: int):
        # If level decreases, reset higher-level counters
        if level < self.prev_level:
            for i in range(level + 1, max(self.counters.keys(), default=0) + 1):
                if i in self.counters:
                    self.counters[i] = 0

        # Initialize counter for current level (if missing)
        if level not in self.counters:
            self.counters[level] = 0

        # Update counter for current level
        self.counters[level] += 1

        # Update previous header level
        self.prev_level = level

    def _generate_new_number(self, level: int) -> str:
        # Build new numbering
        new_number_parts = []
        for i in range(1, level + 1):
            if i in self.counters:
                new_number_parts.append(str(self.counters[i]))
            else:
                # If a level has no counter, default to 1
                new_number_parts.append("1")
                self.counters[i] = 1
        return ".".join(new_number_parts)

    def _replace_header(self, match) -> str:
        level, outline_part, full_match = MarkdownOutlineRenumber._parse_header(match)

        # Update counters
        self._update_counters(level)

        # Generate new number
        new_number = self._generate_new_number(level)

        # Return the new header line. Level-1 headers end with a dot.
        level_1_dot = "." if level == 1 else ""
        after_replace = f"{outline_part} {new_number}{level_1_dot} "

        if full_match != after_replace:
            self.history.append(f"from[{full_match}] -> to[{after_replace}]")

        return after_replace


class XYChartMermaidGenerator:
    """
    Unified generator for Mermaid `xychart-beta` charts.
    It supports both "bar" and "line" and chooses the final Mermaid statement based on `image_type`.
    The axis/scaling/formatting behavior follows the previous bar chart implementation.
    """

    LABEL_MAX_LEN = 15  # x-axis label max length
    WIDTH_MIN = 360  # minimum chart width (avoid cramped labels)
    WIDTH_MAX = 960  # maximum chart width (allow more categories without crowding)
    WIDTH_BASE = 220  # base width before per-category expansion (keep small charts compact)
    WIDTH_PER_CATEGORY = 70  # width added per category (balanced density for 3–12 points)
    HEIGHT = 360  # chart height (more vertical room for data labels)
    TARGET_TOP_RATIO = 1.0  # baseline y_max target ratio
    NEG_TOP_RATIO = 0.95  # all-negative: keep headroom toward 0
    Y_MIN_EXAGGERATION_MAX = 6.0  # limit vertical exaggeration for all-positive ranges
    PAD_RATIO_TIGHT = 0.08  # padding ratio for concentrated ranges
    PAD_RATIO_LOOSE = 0.12  # padding ratio for wide ranges
    PAD_UP_FRACTION = 0.5  # upper padding fraction of lower padding
    BAR_ZERO_GAP_RATIO = 2.5  # include zero if gap to zero is not too large
    # Horizontal bar decision parameters (avoid label overlap)
    HORIZONTAL_TOTAL_LABEL_LIMIT = 80.0
    LABEL_WEIGHT_CJK = 2.0
    LABEL_WEIGHT_ASCII = 1.0
    NICE_MULTIPLIERS = (
        1, 1.1, 1.2, 1.25, 1.5, 1.6, 1.8, 
        2, 2.2, 2.5, 3, 3.5,
        4, 4.5, 5, 6, 7, 8, 9, 10, 10.5, 11, 12, 15,
    )

    @classmethod
    def _sanitize_label(cls, label: str | None) -> str:
        raw = (str(label) if label is not None else "").strip().replace('"', "'")
        if not raw:
            return "Item"
        return raw

    @classmethod
    def _label_weight_length(cls, label: str) -> float:
        total = 0.0
        for ch in label:
            if "\u4e00" <= ch <= "\u9fff":
                total += cls.LABEL_WEIGHT_CJK
            else:
                total += cls.LABEL_WEIGHT_ASCII
        return total

    @classmethod
    def _should_use_horizontal(cls, labels: list[str], count: int) -> bool:
        if count <= 0:
            return False
        weights = [cls._label_weight_length(label) for label in labels]
        total_len = sum(weights)
        max_len = max(weights, default=0.0)
        per_label_limit = cls.HORIZONTAL_TOTAL_LABEL_LIMIT / max(count, 1)
        return not (
            total_len <= cls.HORIZONTAL_TOTAL_LABEL_LIMIT
            and max_len <= per_label_limit
        )

    @staticmethod
    def _format_num(value: float) -> str:
        """Format numbers for Mermaid (avoid scientific notation)."""
        abs_val = abs(value)
        if abs_val == 0:
            return "0"
        if abs(value - round(value)) < 1e-6 and abs_val >= 1:
            return str(int(round(value)))
        if abs_val >= 1:
            return f"{value:.2f}".rstrip("0").rstrip(".")
        if abs_val >= 0.01:
            return f"{value:.3f}".rstrip("0").rstrip(".")
        decimals = max(6, int(-math.floor(math.log10(abs_val))) + 2)
        return f"{value:.{decimals}f}".rstrip("0").rstrip(".")

    @classmethod
    def _nice_ceil(cls, value: float) -> float:
        """Ceil to a 'nice' number for y-axis max."""
        if value <= 0:
            return 1.0
        exp = 10 ** math.floor(math.log10(value))
        for m in cls.NICE_MULTIPLIERS:
            candidate = m * exp
            if candidate >= value:
                return candidate
        return 10 * exp

    @classmethod
    def _nice_step(cls, target_step: float) -> float:
        """Pick a 'nice' step size close to target_step."""
        if target_step <= 0:
            return 1.0
        exp = 10 ** math.floor(math.log10(target_step))
        for m in cls.NICE_MULTIPLIERS:
            candidate = m * exp
            if candidate >= target_step:
                return candidate
        return 10 * exp

    @classmethod
    def generate_from_json(cls, json_string: str) -> str:
        if not json_string:
            raise ValueError("empty input")
        data = json.loads(json_string)
        if not data or data.get("image_type") not in ("bar", "line"):
            raise ValueError("input must be a bar/line chart visualization JSON")

        chart_type = data.get("image_type")  # "bar" or "line"
        raw_unit = (data.get("unit") or "").strip()
        if cls._detect_mixed_unit(raw_unit):
            raise ValueError("mixed units are not allowed for a single chart")

        records = data.get("records", [])
        if not records or len(records) < 2:
            raise ValueError("records are required")

        x_values: list[str] = []
        raw_values: list[float] = []
        for row in records:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("each record must be a 2-element array")
            label, value = row[0], row[1]
            if not isinstance(label, str):
                raise ValueError("record[0] must be a string")
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError("record[1] must be a finite number")
            x_values.append(cls._sanitize_label(label))
            raw_values.append(float(value))

        # Unit conversion is handled by LLM; keep original values and unit.
        display_values = raw_values
        y_min, y_max = cls._compute_y_range(display_values, chart_type)

        unit_title = raw_unit or "Value"
        x_axis = ", ".join(f'"{c}"' for c in x_values)
        series_values = ", ".join(cls._format_num(v) for v in display_values)
        y_min_s = cls._format_num(y_min)
        y_max_s = cls._format_num(y_max)

        count = len(x_values)
        width = max(
            cls.WIDTH_MIN,
            min(cls.WIDTH_MAX, cls.WIDTH_BASE + count * cls.WIDTH_PER_CATEGORY),
        )

        use_horizontal = (
            chart_type == "bar" and cls._should_use_horizontal(x_values, count)
        )
        chart_orientation = (
            "xychart-beta horizontal" if use_horizontal else "xychart-beta"
        )

        lines = [
            "---",
            "config:",
            f"    horizontal: {'true' if use_horizontal else 'false'}",
            f"    width: {width}",
            f"    height: {cls.HEIGHT}",
            "    showDataLabel: true",
            "    themeVariables:",
            "        xyChart:",
            "            plotColorPalette: '#7c3aed'",
            "---",
            chart_orientation,
            f"    x-axis [{x_axis}]",
            f'    y-axis "{unit_title}" {y_min_s} --> {y_max_s}',
            f"    {chart_type} [{series_values}]",
        ]
        return "\n".join(lines)

    @classmethod
    def _nice_floor(cls, value: float) -> float:
        if value >= 0:
            return 0.0
        abs_val = abs(value)
        exp = 10 ** math.floor(math.log10(abs_val))
        for m in cls.NICE_MULTIPLIERS:
            candidate = -m * exp
            if candidate <= value:
                return candidate
        return -10 * exp

    @classmethod
    def _nice_neg_ceil(cls, value: float) -> float:
        """
        "Nice" ceiling for negative numbers (move toward 0).
        Returns a negative number >= value.
        """
        if value >= 0:
            return 0.0
        abs_val = abs(value)
        exp = 10 ** math.floor(math.log10(abs_val))
        # choose the largest multiplier (<=10) that is still <= abs_val/exp
        for m in reversed(cls.NICE_MULTIPLIERS):
            if m > 10:
                continue
            if m * exp <= abs_val + 1e-12:
                return -m * exp
        return -exp

    @staticmethod
    def _detect_mixed_unit(unit: str | None) -> bool:
        if not unit:
            return False
        unit_lower = unit.lower()
        return (
            "或" in unit
            or "/" in unit
            or "|" in unit
            or "," in unit
            or ";" in unit
            or " and " in unit_lower
        )

    @classmethod
    def _compute_y_range(
        cls, values: list[float], chart_type: str
    ) -> tuple[float, float]:
        if not values:
            return 0.0, 1.0
        vmin = min(values)
        vmax = max(values)
        if vmax == 0 and vmin == 0:
            return 0.0, 1.0

        if vmin < 0:
            # Mixed-sign: include both sides with nice bounds.
            if vmax > 0:
                return cls._nice_floor(vmin), cls._nice_ceil(vmax)
            # All-negative: keep y_max near the max value (toward 0) to reduce top blank space.
            y_min = cls._nice_floor(vmin)
            y_max = cls._nice_neg_ceil(vmax * cls.NEG_TOP_RATIO)
            if y_max < vmax:
                y_max = vmax
            if y_max <= y_min:
                y_max = vmax
            return y_min, y_max

        vrange = vmax - vmin

        def _padded_range(
            min_val: float, max_val: float, force_zero: str | None
        ) -> tuple[float, float]:
            span = max_val - min_val
            if span <= 0:
                span = max(abs(max_val), abs(min_val), 1.0) * 0.1
            denom = max(abs(max_val), abs(min_val), 1e-9)
            range_ratio = span / denom
            pad_down = span * (
                cls.PAD_RATIO_TIGHT if range_ratio < 0.25 else cls.PAD_RATIO_LOOSE
            )
            pad_up = pad_down * cls.PAD_UP_FRACTION
            min_candidate = min_val - pad_down
            max_candidate = max_val + pad_up
            if force_zero == "min":
                min_candidate = 0.0
            elif force_zero == "max":
                max_candidate = 0.0

            span = max_candidate - min_candidate
            if span <= 0:
                return min_val, max_val
            step = cls._nice_step(span / 6.0)
            if step <= 0:
                return min_val, max_val
            y_min = math.floor(min_candidate / step) * step
            y_max = math.ceil(max_candidate / step) * step
            if force_zero == "min" and y_min < 0:
                y_min = 0.0
            if force_zero == "max" and y_max > 0:
                y_max = 0.0
            if y_min > min_val:
                y_min = min_val
            if y_max < max_val:
                y_max = max_val
            if max_val >= 0 and y_min < 0:
                y_min = 0.0
            if min_val <= 0 and y_max > 0 and force_zero == "max":
                y_max = 0.0
            return y_min, y_max

        def _should_include_zero(min_val: float, max_val: float) -> bool:
            if chart_type != "bar":
                return False
            gap_to_zero = min_val if min_val > 0 else -max_val
            span = max_val - min_val
            if span <= 0:
                span = max(abs(max_val), abs(min_val), 1.0) * 0.1
            return gap_to_zero <= span * cls.BAR_ZERO_GAP_RATIO

        if vmin >= 0:
            if _should_include_zero(vmin, vmax):
                return _padded_range(vmin, vmax, "min")
            return _padded_range(vmin, vmax, None)
        if vmax <= 0:
            if _should_include_zero(vmin, vmax):
                return _padded_range(vmin, vmax, "max")
            return _padded_range(vmin, vmax, None)

        y_min = cls._nice_floor(vmin)
        y_max = cls._nice_ceil(vmax)
        return y_min, y_max


class PieChartMermaidGenerator:
    OTHER_LABEL = "other"
    EPSILON = 1e-6

    @classmethod
    def _sanitize_label(cls, label: str) -> str:
        # Keep original characters; only normalize whitespace and protect quotes.
        label = str(label).strip()
        if not label:
            return "label"
        label = label.replace('"', "'")
        return re.sub(r"\s+", " ", label).strip()

    @staticmethod
    def _format_num(value: float) -> str:
        # Preserve original magnitude without rounding; avoid scientific notation.
        try:
            dec_value = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return str(value)
        text = format(dec_value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in ("-0", "-0.0") else text

    @staticmethod
    def _format_other_value(value: float) -> str:
        # "other" is computed: round to 3 decimals and trim trailing zeros.
        try:
            dec_value = Decimal(str(value)).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, ValueError):
            return str(value)
        text = format(dec_value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in ("-0", "-0.0") else text

    @classmethod
    def generate_from_json(cls, json_string: str) -> str:
        if not json_string:
            raise ValueError("empty input")
        data = json.loads(json_string)
        if not data or data.get("image_type") != "pie":
            raise ValueError("input must be a pie chart visualization JSON")

        unit = (data.get("unit") or "").strip()
        percent_mode = bool(unit and ("%" in unit or "百分比" in unit))
        records = data.get("records", [])
        if not isinstance(records, list) or len(records) < 2:
            raise ValueError("records are required")
        labels: list[str] = []
        values: list[float] = []
        raw_values: list[float] = []
        other_flags: list[bool] = []

        for row in records:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("each record must be a 2-element array")
            label, value = row[0], row[1]
            if not isinstance(label, str) or not label.strip():
                raise ValueError("record[0] must be a non-empty string")
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError("record[1] must be a finite number")
            value_f = float(value)
            if value_f < 0:
                raise ValueError("value must be non-negative")
            labels.append(cls._sanitize_label(label))
            values.append(value_f)
            raw_values.append(value_f)
            other_flags.append(False)

        total = sum(values)
        if percent_mode:
            if total > 100.0 + cls.EPSILON:
                raise ValueError("percent values sum exceeds 100")
            if total < 100.0 - cls.EPSILON:
                labels.append(cls.OTHER_LABEL)
                other_value = 100.0 - total
                values.append(other_value)
                raw_values.append(other_value)
                other_flags.append(True)

        items = list(zip(labels, values, raw_values, other_flags))
        items.sort(key=lambda item: (item[3], -item[1]))

        lines = ["pie"]
        for label, value, raw_value, is_other in items:
            unit_suffix = unit if unit else ""
            num_text = (
                cls._format_other_value(raw_value)
                if is_other
                else cls._format_num(raw_value)
            )
            display_label = f"{label} ({num_text}{unit_suffix})"
            value_text = (
                cls._format_other_value(value) if is_other else cls._format_num(value)
            )
            lines.append(f'    "{display_label}" : {value_text}')
        return "\n".join(lines)


class TimelineChartMermaidGenerator:
    """
    Output Mermaid schema:
    timeline
        title <title>
            <time> : <event><br>...
    """

    @staticmethod
    def _format_event_text(text: str) -> str:
        # Allow line breaks via <br>
        return (
            str(text)
            .strip()
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "<br>")
        )

    @classmethod
    def generate_from_json(cls, json_string: str) -> str:
        if not json_string:
            raise ValueError("empty input")
        data = json.loads(json_string)
        if not data or data.get("image_type") != "timeline":
            raise ValueError("input must be a timeline visualization JSON")

        records = data.get("records", [])
        if not isinstance(records, list) or len(records) < 1:
            raise ValueError("records are required")

        lines = ["timeline"]

        # Render each record: 8-space indent to match the requested schema.
        for row in records:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("each record must be a 2-element array")
            t, event = row[0], row[1]
            if not isinstance(t, str) or not t.strip():
                raise ValueError("record[0] must be a non-empty string time")
            if not isinstance(event, str) or not event.strip():
                raise ValueError("record[1] must be a non-empty string event")
            lines.append(f"        {t.strip()} : {cls._format_event_text(event)}")

        return "\n".join(lines)
