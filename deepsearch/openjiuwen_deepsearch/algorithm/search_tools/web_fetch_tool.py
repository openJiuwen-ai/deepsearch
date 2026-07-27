import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple, Union

from openjiuwen_deepsearch.algorithm.prompts.template import get_prompt_section
from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import (
    RunLLMConfig,
    _is_context_limit_error,
    run_llm,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.framework.openjiuwen.tools.fetch_api import (
    resolve_web_fetch_provider,
    supported_fetch_providers,
)
from openjiuwen_deepsearch.utils.common_utils.llm_utils import format_llm_log_correlation_suffix
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import msvcrt

    def _lock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class WebFetch:
    name = "web_fetch"
    description = "Retrieve webpage content and summarize information relevant to a given objective."
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": ["string", "array"],
                "items": {"type": "string"},
                "minItems": 1,
            },
            "goal": {"type": "string"},
            "log_fetch": {"type": "bool"},
            "fetch_tool_model": {"type": "string"},
        },
        "required": ["url", "goal"],
    }

    _log_lock = asyncio.Lock()

    def __init__(self, config: Optional[dict]) -> None:
        config = config or {}
        provider_config = config.get("web_fetch_provider_config") or {}
        self.provider_name, self.provider = resolve_web_fetch_provider(provider_config)
        self.web_fetch_log_file = config.get("web_fetch_log_file", "gnosis/tool_log/web_fetch_log.jsonl")
        log_dir = os.path.dirname(self.web_fetch_log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    async def acall(self, params: Union[str, dict]) -> str:
        return await self._acall_impl(params)

    def call(self, params: Union[str, dict]) -> str:
        if not isinstance(params, dict):
            raise ValueError(f"[WebFetch] Invalid request format: expected dict, got {type(params).__name__}")
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
        if not isinstance(params, dict):
            raise ValueError(f"[WebFetch] Invalid request format: expected dict, got {type(params).__name__}")
        missing = [f for f in ("url", "goal") if f not in params]
        if missing:
            raise ValueError(
                f"[WebFetch] Invalid request format: missing required field(s): {', '.join(missing)}. "
                f"Provided keys: {list(params.keys())}"
            )
        urls = params["url"]
        goal = params["goal"]
        log_fetch = params.get("log_fetch", False)
        model_name = params.get("fetch_tool_model")
        if isinstance(urls, str):
            return await self._handle_single(url=urls, goal=goal, log_fetch=log_fetch, model_name=model_name)
        if isinstance(urls, list):
            return await self._handle_batch(urls=urls, goal=goal, log_fetch=log_fetch, model_name=model_name)
        return "[WebFetch] Invalid 'url' type"

    async def _handle_batch(
        self,
        urls: List[str],
        goal: str,
        log_fetch: bool,
        model_name: Optional[str],
    ) -> str:
        tasks = [
            self._handle_single(
                url=u,
                goal=goal,
                log_fetch=log_fetch,
                model_name=model_name,
            )
            for u in urls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        outputs = []
        for u, r in zip(urls, results):
            if isinstance(r, Exception):
                outputs.append(f"Error fetching {u}: {r}")
            else:
                outputs.append(r)

        return "\n=======\n".join(outputs)

    async def _handle_single(
        self,
        url: str,
        goal: str,
        log_fetch: bool,
        model_name: Optional[str],
    ) -> str:
        return await self._execute_fetch(
            url=url,
            goal=goal,
            log_fetch=log_fetch,
            model_name=model_name,
        )

    def _provider_config_error(self) -> str:
        providers = ", ".join(supported_fetch_providers())
        if not self.provider_name:
            return (
                "[web_fetch] No fetch provider configured. "
                "Set agent_config.web_fetch_provider_config.provider_name explicitly."
            )
        return (
            f"[web_fetch] Unsupported fetch provider '{self.provider_name}'. "
            f"Supported providers: {providers}."
        )

    async def _execute_fetch(
        self,
        url: str,
        goal: str,
        log_fetch: bool,
        model_name: Optional[str],
    ) -> str:
        if self.provider is None:
            return self._provider_config_error()

        page_text = await asyncio.to_thread(self.provider.fetch_page, url)
        raw_page = page_text

        if page_text and not page_text.startswith("[web_fetch] Failed"):
            extracted = await WebFetch._analyze_content(
                page_text,
                goal,
                model_name,
            )

            if extracted is None:
                logger.error(
                    "[WebFetch] extractor returned no structured result; falling back | provider=%s url=%s goal=%s%s",
                    self.provider_name,
                    url,
                    "*" if LogManager.is_sensitive() else goal,
                    format_llm_log_correlation_suffix(),
                )
                return await self._fallback(
                    url,
                    goal,
                    raw_page,
                    log_fetch,
                )

            result = (
                f"The useful information in {url} for user goal {goal} as follows:\n\n"
                f"Evidence in page:\n{extracted['evidence']}\n\n"
                f"Summary:\n{extracted['summary']}\n\n"
            )

            if log_fetch:
                await self._write_log(url, goal, raw_page, result)

            return result

        return await self._fallback(url, goal, raw_page, log_fetch)

    @staticmethod
    def _extractor_raw_preview(raw: str, limit: int = 200) -> str:
        t = str(raw).replace("\n", " ").replace("\r", " ").strip()
        if len(t) > limit:
            return t[: limit - 3] + "..."
        return t

    @staticmethod
    def _parse_extractor_json(raw: str, attempt_no: int) -> Optional[dict]:
        if not raw or not str(raw).strip():
            return None
        try:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(cleaned)
            return {
                "evidence": parsed["evidence"],
                "summary": parsed["summary"],
            }
        except Exception as e:
            logger.warning(
                "[WebFetch] extractor did not return valid JSON (attempt=%s, direct parse): %s | raw_preview=%s",
                attempt_no,
                e,
                WebFetch._extractor_raw_preview(raw),
                exc_info=True,
            )
        try:
            left, right = raw.find("{"), raw.rfind("}")
            if left != -1 and right != -1 and left <= right:
                parsed = json.loads(raw[left: right + 1])
                return {
                    "evidence": parsed["evidence"],
                    "summary": parsed["summary"],
                }
        except Exception as e:
            logger.warning(
                "[WebFetch] extractor did not return valid JSON (attempt=%s, brace slice): %s | raw_preview=%s",
                attempt_no,
                e,
                WebFetch._extractor_raw_preview(raw),
                exc_info=True,
            )
        return None

    @staticmethod
    def _llm_error_message(exc: BaseException) -> str:
        if isinstance(exc, CustomValueException) and exc.message:
            return str(exc.message)
        return str(exc)

    @staticmethod
    def _is_llm_context_limit(exc: Optional[BaseException]) -> bool:
        if exc is None:
            return False
        chain = getattr(exc, "__cause__", None) or exc
        return _is_context_limit_error(
            WebFetch._llm_error_message(exc),
            chain,
        )

    @staticmethod
    async def _analyze_content(
        content: str,
        goal: str,
        model_name: Optional[str],
    ) -> Optional[dict]:
        """Extract evidence/summary via LLM; retry on failure, truncate body only when error is context limit."""
        max_attempts = 4
        working = content

        for attempt in range(max_attempts):
            prompt_content = get_prompt_section(
                "web_fetch_extractor",
                {"goal": goal, "webpage_content": working},
            )
            messages = [{"role": "user", "content": prompt_content}]
            raw, err = await WebFetch._invoke_llm(messages, model_name)

            parsed = WebFetch._parse_extractor_json(raw, attempt_no=attempt + 1)
            if parsed is not None:
                return parsed

            if attempt >= max_attempts - 1:
                break

            if WebFetch._is_llm_context_limit(err):
                new_len = (
                    min(400_000, int(0.8 * len(working))) if attempt >= max_attempts - 2 else int(0.8 * len(working))
                )
                working = working[:new_len]
                logger.warning(
                    "[WebFetch._analyze_content] context limit; truncated body to len=%s (attempt %s/%s)%s",
                    len(working),
                    attempt + 1,
                    max_attempts,
                    format_llm_log_correlation_suffix(),
                )

        return None

    @staticmethod
    async def _invoke_llm(
        messages: list,
        model_name: str,
        max_retries: int = 2,
    ) -> Tuple[str, Optional[BaseException]]:
        llm_config = {
            "model_name": model_name,
            "max_tries": max_retries + 1,
        }

        try:
            content, _, _, _ = await run_llm(
                RunLLMConfig(
                    config=llm_config,
                    system_prompt="You are a helpful assistant that analyzes webpage content.",
                    context_vars={"messages": messages},
                    need_stream_out=False,
                    agent_name=AgentLlmName.TOOL.value,
                )
            )

            if content:
                try:
                    json.loads(content)
                except Exception:
                    left, right = content.find("{"), content.rfind("}")
                    if left != -1 and right != -1 and left <= right:
                        content = content[left: right + 1]
                return content, None
            logger.warning(
                "[WebFetch._invoke_llm] run_llm returned empty content for model %s",
                "*" if LogManager.is_sensitive() else model_name,
            )
        except Exception as e:
            return "", e

        return "", None

    async def _write_log(self, url: str, goal: str, raw: str, summary: str) -> None:
        record = {
            "timestamp": time.time(),
            "url": url,
            "goal": goal,
            "provider_name": self.provider_name,
            "provider_output": raw,
            "summary": summary,
        }
        if self.provider_name == "jina":
            record["jina_output"] = raw

        def _do_write() -> None:
            try:
                with open(self.web_fetch_log_file, "ab") as f:
                    locked = False
                    try:
                        _lock_file(f)
                        locked = True
                        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
                        f.write(line)
                        f.flush()
                    finally:
                        if locked:
                            try:
                                _unlock_file(f)
                            except Exception as e:
                                logger.warning("Failed to release log file lock: %s", e)
            except Exception as e:
                logger.warning("Failed to write web_fetch log: %s", e)

        async with self._log_lock:
            await asyncio.to_thread(_do_write)

    async def _fallback(
        self,
        url: str,
        goal: str,
        raw: str,
        log_fetch: bool,
    ) -> str:
        raw_section = ""
        if not LogManager.is_sensitive():
            raw_text = "" if raw is None else str(raw)
            raw_text = raw_text.strip()
            if raw_text:
                raw_section = f"Raw page content [:2000 chars]:\n{raw_text[:2000]}\n\n"

        result = (
            f"The useful information in {url} for user goal {goal} as follows:\n\n"
            "Evidence in page:\n"
            "The provided webpage content could only be accessed as raw text (no structured extraction available).\n\n"
            f"{raw_section}"
            "Summary:\n"
            "A structured summary could not be produced from the page.\n\n"
        )
        if log_fetch:
            await self._write_log(url, goal, raw, result)
        return result
