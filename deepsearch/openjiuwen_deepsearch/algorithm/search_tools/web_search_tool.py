import asyncio
import contextvars
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional, Union

from openjiuwen_deepsearch.framework.openjiuwen.tools.web_search import (
    get_web_search_api_wrapper,
    run_web_search,
)

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt

    def _lock_file_exclusive(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

    def _lock_file_shared(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

else:
    import fcntl

    def _lock_file_exclusive(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _lock_file_shared(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)


class WebSearch:
    name = "web_search"
    description = (
        "Execute web queries through a search engine and return structured results."
    )

    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": ["string", "array"],
                "items": {"type": "string"},
                "description": "Search keyword(s).",
            },
            "log_search": {
                "type": "boolean",
                "default": True,
            },
        },
        "required": ["query"],
    }

    _file_lock = asyncio.Lock()

    def __init__(self, config: Optional[dict]) -> None:
        config = config or {}
        self.web_search_log_file = config.get(
            "web_search_log_file", "gnosis/tool_log/web_search_log.jsonl"
        )
        log_dir = os.path.dirname(self.web_search_log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    async def acall(self, params: Union[str, dict]) -> str:
        return await self._acall_impl(params)

    def call(self, params: Union[str, dict]) -> str:
        if not isinstance(params, dict) or "query" not in params:
            return "[WebSearch] Invalid request format"
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False
        if not in_loop:
            return asyncio.run(self._acall_impl(params))
        context = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(context.run, asyncio.run, self._acall_impl(params)).result()

    async def _acall_impl(self, params: Union[str, dict]) -> str:
        if not isinstance(params, dict) or "query" not in params:
            return "[WebSearch] Invalid request format"
        queries = params["query"]
        log_enabled = params.get("log_search", True)
        if isinstance(queries, str):
            return await self._handle_single(queries, log_enabled)
        if isinstance(queries, list):
            return await self._handle_batch(queries, log_enabled)
        return "[WebSearch] Invalid 'query' type"

    async def _handle_batch(
        self,
        queries: List[str],
        log_enabled: bool,
    ) -> str:
        tasks = [self._handle_single(q, log_enabled) for q in queries]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        outputs = []
        for q, r in zip(queries, results):
            if isinstance(r, Exception):
                outputs.append(f"[WebSearch error] {q}: {r}")
            else:
                outputs.append(r)

        return "\n=======\n".join(outputs)

    async def _handle_single(
        self,
        query: str,
        log_enabled: bool,
    ) -> str:
        cached = await self._load_from_cache(query)
        if cached is not None:
            return cached

        result = await self._execute_query(query)

        if log_enabled:
            await self._write_log(query, result)

        return result

    async def _execute_query(self, query: str) -> str:
        search_engine_name, api_wrapper = get_web_search_api_wrapper()
        if not search_engine_name or api_wrapper is None:
            return (
                f'No usable results for query "{query}". Error: '
                "Active web search engine is not initialized."
            )

        result = await run_web_search(query, search_engine_name)
        if not isinstance(result, dict):
            return f'No usable results for query "{query}".'

        error = str(result.get("error") or "").strip()
        if error:
            return f'No usable results for query "{query}". Error: {error}'

        return WebSearch._format_output(query, result.get("search_results"))

    @staticmethod
    def _first_non_empty(item: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @classmethod
    def _normalize_result_row(cls, item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None

        title = cls._first_non_empty(item, ("title", "name"))
        link = cls._first_non_empty(item, ("url", "link", "source_url"))
        snippet = cls._first_non_empty(
            item,
            ("content", "snippet", "summary", "answer", "description", "raw_content"),
        )
        source = cls._first_non_empty(
            item,
            ("source", "site_name", "displayLink", "origin"),
        )
        date = cls._first_non_empty(
            item,
            ("date", "published", "published_at", "published_date"),
        )

        if not link:
            return None

        return {
            "title": title or link,
            "link": link,
            "snippet": snippet,
            "source": source,
            "date": date,
        }

    @classmethod
    def _format_output(cls, query: str, rows: Any) -> str:
        if not isinstance(rows, list):
            return f'No usable results for query "{query}".'

        blocks = []
        for item in rows:
            normalized = cls._normalize_result_row(item)
            if normalized is None:
                continue

            title = normalized["title"]
            link = normalized["link"]
            snippet = normalized["snippet"]
            source = normalized["source"]
            date = normalized["date"]

            idx = len(blocks) + 1
            entry = f"{idx}. [{title}]({link})"
            if date:
                entry += f"\nPublished: {date}"
            if source:
                entry += f"\nOrigin: {source}"
            if snippet:
                entry += f"\n{snippet}"

            blocks.append(entry)

        if not blocks:
            return f'No usable results for query "{query}".'

        header = f'Results for query "{query}" ({len(blocks)} entries):\n\n'
        return header + "\n\n".join(blocks)

    def _do_load_from_cache_sync(self, query: str) -> Optional[str]:
        if not os.path.exists(self.web_search_log_file):
            return None
        try:
            with open(self.web_search_log_file, "rb") as f:
                locked = False
                try:
                    _lock_file_shared(f)
                    locked = True
                    for line in f:
                        try:
                            record = json.loads(line.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError) as e:
                            logger.debug(
                                "Failed to parse JSON line in cache: %s", e
                            )
                            continue
                        if record.get("query") == query:
                            return record.get("result")
                finally:
                    if locked:
                        try:
                            _unlock_file(f)
                        except Exception as e:
                            logger.warning(
                                "Failed to release file lock: %s", e
                            )
        except Exception as e:
            logger.debug("Failed to load from cache: %s", e)
        return None

    async def _load_from_cache(self, query: str) -> Optional[str]:
        async with self._file_lock:
            return await asyncio.to_thread(
                self._do_load_from_cache_sync, query
            )

    def _do_write_log_sync(self, query: str, result: str) -> None:
        record = {
            "timestamp": time.time(),
            "query": query,
            "result": result,
        }

        try:
            with open(self.web_search_log_file, "ab") as f:
                locked = False
                try:
                    _lock_file_exclusive(f)
                    locked = True
                    line = (
                        json.dumps(record, ensure_ascii=False) + "\n"
                    ).encode("utf-8")
                    f.write(line)
                    f.flush()
                finally:
                    if locked:
                        try:
                            _unlock_file(f)
                        except Exception as e:
                            logger.warning(
                                "Failed to release log file lock: %s", e
                            )
        except Exception as e:
            logger.warning("Failed to write web_search log: %s", e)

    async def _write_log(self, query: str, result: str) -> None:
        async with self._file_lock:
            await asyncio.to_thread(self._do_write_log_sync, query, result)
