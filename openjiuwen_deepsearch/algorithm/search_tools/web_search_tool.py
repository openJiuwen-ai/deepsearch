import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Union

import requests

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
        if isinstance(config.get("serper_api_key", None), (bytes, bytearray)):
            try:
                self.serper_api_key = config.get("serper_api_key", None).decode("utf-8")
            except Exception:
                self.serper_api_key = str(config.get("serper_api_key", None))
        else:
            self.serper_api_key = config.get("serper_api_key", None)
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
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self._acall_impl(params)).result()

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

        result = await asyncio.to_thread(self._execute_query, query)

        if log_enabled:
            await self._write_log(query, result)

        return result

    def _execute_query(self, query: str) -> str:
        endpoint = "https://google.serper.dev/search"
        headers = {
            "X-API-KEY": self.serper_api_key,
            "Content-Type": "application/json",
        }

        payload = self._build_payload(query)
        last_error: Optional[str] = None

        for attempt in range(5):
            try:
                resp = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=20,
                )
                if resp.ok:
                    return WebSearch._format_output(query, resp.json())
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as exc:
                last_error = str(exc)

            time.sleep(0.6 * (attempt + 1))

        return f'No usable results for query "{query}". Error: {last_error}'

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    def _build_payload(self, query: str) -> dict:
        if self._contains_cjk(query):
            return {
                "q": query,
                "location": "China",
                "gl": "cn",
                "hl": "zh-cn",
            }
        return {
            "q": query,
            "location": "United States",
            "gl": "us",
            "hl": "en",
        }

    @staticmethod
    def _format_output(query: str, data: dict) -> str:
        organic = data.get("organic")
        if not organic:
            return f'No usable results for query "{query}".'

        blocks = []
        for idx, item in enumerate(organic, start=1):
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            source = item.get("source", "")
            date = item.get("date", "")

            entry = f"{idx}. [{title}]({link})"
            if date:
                entry += f"\nPublished: {date}"
            if source:
                entry += f"\nOrigin: {source}"
            if snippet:
                entry += f"\n{snippet}"

            blocks.append(entry)

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
