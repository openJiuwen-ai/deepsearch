"""Normalize report formatting instructions."""


def normalize_report_format_requirements(value: object) -> list[str]:
    """Keep non-empty string instructions without interpreting their contents."""
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [item.strip() for item in items if isinstance(item, str) and item.strip()]
