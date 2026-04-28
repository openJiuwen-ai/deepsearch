import json
import logging
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


def chunked_iterable(iterable, size: int):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            break
        yield chunk


def populate_datetime_str(date_str: Optional[str]) -> Optional[str]:
    """
    Extends a YYYY-MM-DD string to include verbal representations.
    Example: "2023-01-01" -> "2023 01 01 January January 2023"
    """
    if not date_str:
        return None

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.year} {dt.month:02d} {dt.day:02d} {dt.strftime('%B')} {dt.strftime('%B %Y')}"
    except ValueError:
        logger.warning("Could not parse date string: %s", date_str)
        return date_str  # Return original if parse fails


def update_dict_lists(key: str, value: str, input_dict: Dict[str, List[str]]) -> None:
    """
    Appends value to the list at input_dict[key], creating the list if needed.
    """
    input_dict.setdefault(key, []).append(value)


def update_dict_str(
    key: str, value: str, input_dict: Dict[str, str], check_conflict: bool = False
) -> None:
    """
    Sets input_dict[key] = value. Optionally warns if overwriting with a different value.
    """
    if check_conflict and key in input_dict:
        if input_dict[key] != value:
            if LogManager.is_sensitive():
                logger.warning("Conflict for key (value omitted).")
            else:
                logger.warning(
                    "Conflict for key '%s': existing '%s' vs new '%s'",
                    key,
                    input_dict[key],
                    value,
                )

    input_dict[key] = value


def read_jsonl(data_location: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Reads a JSONL file and returns a list of dictionaries.
    """
    data_instances = []
    path = Path(data_location)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {data_location}")

    with path.open("r", encoding="utf-8") as jsonl_f:
        for line_num, line in enumerate(jsonl_f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data_instances.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.error("Error parsing JSON on line %s.", line_num)

    return data_instances
