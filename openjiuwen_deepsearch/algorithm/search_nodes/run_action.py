# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List

import json_repair

from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt, get_prompt_section
from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import RunLLMConfig, _is_context_limit_error, run_llm
from openjiuwen_deepsearch.algorithm.search_nodes.utils import (
    _save_result,
    format_action_for_log,
    to_dict_safe,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    Result,
    State,
    Variable,
)
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName, NodeId
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)

_PARSE_ERROR_CACHE: Dict[str, str] = {}

_SEARCH_FETCH_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Run a search engine on the web. You pass an array of search queries; "
                "the tool returns consolidated results (snippets and links) for all "
                "queries in one call. Use this when you need to discover pages or "
                "information on the internet. Pass multiple queries in one call to "
                "cover different phrasings or aspects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": (
                            "Search query strings. One or more queries; each is run "
                            "against the search engine and results are returned together."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch the full content of one or more webpages and get a summary "
                "aligned to a goal. You provide URLs (e.g. from web_search) and a goal; "
                "the tool fetches the pages and returns extracted evidence and a summary "
                "relevant to that goal. Use this when you have specific URLs and need "
                "their content summarized or filtered by an objective."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": (
                            "The URL(s) to fetch. A single URL or a list of URLs; each "
                            "page is fetched and summarized according to the goal."
                        ),
                    },
                    "goal": {
                        "type": "string",
                        "description": (
                            "What you want from the page(s). The tool extracts evidence "
                            "and summarizes content relevant to this objective."
                        ),
                    },
                },
                "required": ["url", "goal"],
            },
        },
    },
]

_RETRIEVE_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "retrieve",
            "description": (
                "Search a fixed corpus of pre-indexed web content. You "
                "pass natural-language queries; the tool returns the most relevant "
                "text passages from the corpus via similarity search. Use this when "
                "you need evidence or answers from that corpus. Supports 1–3 queries "
                "per call; use multiple queries to target different aspects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 3,
                        "description": (
                            "One or more natural-language queries. Each query is run "
                            "against the corpus; matching passages are returned for "
                            "all queries in one response."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def get_tool_definitions(retrieval_tool_only: bool = False) -> List[Dict[str, Any]]:
    """Return native OpenAI-format tool definitions for the run_action agent."""
    if retrieval_tool_only:
        return list(_RETRIEVE_TOOLS)
    return list(_SEARCH_FETCH_TOOLS)


@dataclass
class ParseAndApplyLLMResultConfig:
    config: Dict[str, Any]
    action: Dict[str, Any]
    llm_result: Any
    base_state: Any
    messages: List[Any]
    new_found_evidence_ids: List[Any]


@dataclass
class RunActionConfig:
    llm_config: dict
    config: dict
    action: dict
    state: Any
    query: str
    messages: List[Any]
    new_found_evidence_ids: List[Any]
    validate_new_states: Any
    validate_answer: Any
    action_start_time: float
    retrieval_tool_only: bool = False
    retrieval_settings: dict | None = None
    context_limit_reached_strategy: str = "fail"
    total_input_tokens: int = 0
    total_output_tokens: int = 0


def _get_parse_error(key: str) -> str:
    if key not in _PARSE_ERROR_CACHE:
        _PARSE_ERROR_CACHE[key] = get_prompt_section(f"deepsearch_run_action_error_{key}").strip()
    return _PARSE_ERROR_CACHE[key]


# Can sometimes lead to errors when the llm expects a tool response id for each tool call input.
def _strip_tool_responses(messages: List[Any]) -> List[Any]:
    """Remove all tool-response messages (role=tool)."""
    return [m for m in messages if not (isinstance(m, dict) and m.get("role") == "tool")]


def _strip_tool_inputs_and_responses(messages: List[Any]) -> List[Any]:
    """Remove tool-response messages and redact tool_calls in assistant messages.

    - role=tool messages are dropped entirely.
    - role=assistant messages with tool_calls have the tool_calls removed
      so the turn structure is preserved.
    """
    result = []
    for m in messages:
        if not isinstance(m, dict):
            result.append(m)
            continue
        role = m.get("role", "")
        if role == "tool":
            continue
        if role == "assistant" and m.get("tool_calls"):
            redacted = {k: v for k, v in m.items() if k != "tool_calls"}
            result.append(redacted)
            continue
        result.append(m)
    return result


def _estimate_message_tokens(messages: List[Any]) -> int:
    """Rough token count for logs only: ~4 characters per token."""
    try:
        serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        serialized = str(messages)
    return max(1, (len(serialized) + 3) // 4)


def normalize_str_or_list(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
            return value.strip()
        except Exception:
            return value.strip()
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return None


def extract_tag(text: str, tag: str) -> str | None:
    start_tag, end_tag = f"<{tag}>", f"</{tag}>"
    s, e = text.rfind(start_tag), text.rfind(end_tag)
    if s != -1 and e != -1 and e > s:
        return text[s + len(start_tag): e].strip()
    return None


def sanitize_search_query(q: str) -> str:
    if not isinstance(q, str):
        return q

    q = q.replace('\\"', '"')

    if q.startswith('"') and q.endswith('"'):
        q = q[1:-1]

    q = q.replace('"', "")
    q = " ".join(q.split())

    return q.strip()


def apply_patch(
    config: dict,
    base_state: State | dict,
    patch: Dict,
    new_found_evidence_ids: List[str] | None = None,
) -> State:
    new_found_evidence_ids = new_found_evidence_ids or []
    discovered_clues_mode = config.get("discovered_clues_mode", "report")
    base_state = to_dict_safe(base_state)
    if not isinstance(base_state, dict):
        raise CustomValueException(
            StatusCode.RUN_ACTION_PARSE_ERROR.code,
            StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(e="base_state must be a State model or dict"),
        )

    new_patch = []
    if "state" in patch and isinstance(patch["state"], list):
        new_patch = patch["state"]
    elif "new_state" in patch and isinstance(patch["new_state"], list):
        new_patch = patch["new_state"]
    else:
        raise CustomValueException(
            StatusCode.RUN_ACTION_PARSE_ERROR.code,
            StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(
                e="Patch must contain 'state': [ ... ]/ 'new_state': [ ... ]"
            ),
        )
    by_id = {v.get("id"): v for v in base_state.get("state", [])}
    new_vars: List[Variable] = []
    updated_ids = set()
    for v in base_state.get("state", []):
        new_vars.append(
            Variable(
                id=v.get("id"),
                type=v.get("type"),
                question_clues=(list(v.get("question_clues")) if v.get("question_clues") else []),
                discovered_clues=(list(v.get("discovered_clues")) if v.get("discovered_clues") else []),
                candidate=v.get("candidate"),
                candidate_strength=v.get("candidate_strength"),
            )
        )

    # Apply updates
    for pv in new_patch:
        if "id" not in pv:
            raise CustomValueException(
                StatusCode.RUN_ACTION_PARSE_ERROR.code,
                StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(
                    e="Every patched variable must include immutable 'id'."
                ),
            )
        vid = pv["id"]
        if vid not in by_id:
            raise CustomValueException(
                StatusCode.RUN_ACTION_PARSE_ERROR.code,
                StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(e=f"Patched id {vid} not found in base state."),
            )
        idx = next(i for i, vv in enumerate(new_vars) if vv.id == vid)
        nv = new_vars[idx]
        if "candidate" in pv:
            if pv["candidate"]:
                nv.candidate = str(pv["candidate"])
            else:
                nv.candidate = None
        if "candidate_strength" in pv:
            nv.candidate_strength = pv["candidate_strength"]
        if "discovered_clues" in pv and pv["discovered_clues"] is not None:
            if discovered_clues_mode == "none":
                nv.discovered_clues = []
            else:
                new_clues = pv["discovered_clues"]
                if isinstance(new_clues, list):
                    nv.discovered_clues = new_clues
                elif isinstance(new_clues, str):
                    nv.discovered_clues = [new_clues]
                else:
                    raise CustomValueException(
                        StatusCode.RUN_ACTION_PARSE_ERROR.code,
                        StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(e="discovered_clues must be a list"),
                    )

        updated_ids.add(vid)

    depth = (base_state.get("depth") or 0) + 1
    return State(
        state=new_vars,
        depth=depth,
        id=f"{depth:03x}_{int(time.time()*100):08x}{secrets.token_hex(1)}",
        answer_variable=base_state.get("answer_variable"),
        retrieved_evidence_ids=new_found_evidence_ids + base_state.get("retrieved_evidence_ids", []),
    )


def _parse_one_native_tool_call(tc: dict) -> tuple[dict | None, str | None]:
    """Normalize one provider tool call to ``{name, arguments, tool_call_id}``."""
    if not isinstance(tc, dict):
        return None, "tool call is not a dict"
    fn = tc.get("function") or {}
    tool_name = (tc.get("name") or fn.get("name") or "").strip()
    tool_call_id = tc.get("id") or ""
    raw_args = tc.get("args")
    if raw_args is None:
        raw_args = tc.get("arguments")
    if raw_args is None:
        raw_args = fn.get("arguments")
    if raw_args is None:
        raw_args = {}
    if isinstance(raw_args, str):
        try:
            arguments = json.loads(raw_args) if raw_args.strip() else {}
        except Exception as e:
            logger.warning(
                "[run_action] tool call arguments are not valid JSON (id=%s): %s",
                tool_call_id or "?",
                "*" if LogManager.is_sensitive() else e,
            )
            arguments = {}
    else:
        if raw_args is not None and not isinstance(raw_args, dict):
            logger.warning(
                "[run_action] tool call arguments are not a dict (id=%s); using empty dict",
                tool_call_id or "?",
            )
        arguments = raw_args if isinstance(raw_args, dict) else {}

    if not tool_name:
        return None, "tool call is missing a tool name"

    if "query" in arguments:
        arguments["query"] = normalize_str_or_list(arguments["query"])
        if arguments["query"] is None:
            return None, (
                "Error: Tool call has an invalid 'query' field. "
                "'query' must be a non-empty string or list of strings."
            )
        if isinstance(arguments["query"], list):
            arguments["query"] = [sanitize_search_query(q) for q in arguments["query"]]
        elif isinstance(arguments["query"], str):
            arguments["query"] = sanitize_search_query(arguments["query"])

    if "url" in arguments:
        arguments["url"] = normalize_str_or_list(arguments["url"])
        if arguments["url"] is None:
            return None, (
                "Error: Tool call has an invalid 'url' field. " "'url' must be a non-empty string or list of strings."
            )

    return (
        {"name": tool_name, "arguments": arguments, "tool_call_id": tool_call_id},
        None,
    )


def parse_run_action_result(llm_result):
    if isinstance(llm_result, dict):
        raw_tcs = llm_result.get("tool_calls") or []
        if raw_tcs:
            parsed: List[dict] = []
            for i, tc in enumerate(raw_tcs):
                one, err = _parse_one_native_tool_call(tc)
                if err:
                    return None, None, f"Tool call [{i}]: {err}"
                parsed.append(one)
            return "tool_calls", parsed, None
        content = llm_result.get("content", "")
    elif isinstance(llm_result, str):
        content = llm_result
    else:
        return None, None, "Warning: LLM output is neither dict nor str."

    if "<state>" in content:
        mode = "state"
        if "</state>" not in content:
            logger.warning("[run_action] <state> tag not closed — LLM output was likely truncated; attempting recovery")
            content = content + "</state>"
        json_text = extract_tag(content, "state")
    elif "<answer>" in content:
        mode = "answer"
        if "</answer>" not in content:
            logger.warning(
                "[run_action] <answer> tag not closed — LLM output was likely truncated; attempting recovery"
            )
            content = content + "</answer>"
        json_text = extract_tag(content, "answer")
    else:
        return None, None, _get_parse_error("no_output")

    try:
        data = json_repair.loads(json_text)
        return mode, data, None
    except Exception as e:
        if mode == "state":
            error_message = f"Warning: State patch in between the <state> tags is not a valid json. Error: {e}"
        elif mode == "answer":
            error_message = f"Error: Answer patch in between the <answer> tags is not a valid json. Error: {e}"

        return None, None, error_message


def _normalize_new_states(raw_new_states: list) -> list:
    """Handle common LLM mistakes in new_states format.

    Expected:  [{"state": [{"id": 1, ...}]}, {"state": [{"id": 1, ...}]}]
      - each item is a branch (state wrapper) containing a list of variable patches

    Mistake 1 – flat variable dicts (no "state" wrapper):
      [{"id": 1, ...}, {"id": 1, ...}]
      → each dict becomes its own branch: [{"state": [item]} for item in ...]

    Mistake 2 – mixed: some items are branch wrappers, others are flat variable dicts.
      No normalisation attempted; left for apply_patch to surface the error.
    """
    if not raw_new_states or not isinstance(raw_new_states, list):
        return raw_new_states
    if all(isinstance(item, dict) and "id" in item and "state" not in item for item in raw_new_states):
        logger.info(
            "[run_action] auto-wrapping %d flat variable patches, each into its own branch",
            len(raw_new_states),
        )
        return [{"state": [item]} for item in raw_new_states]
    return raw_new_states


def parse_and_apply_llm_result(parse_config: ParseAndApplyLLMResultConfig):
    new_found_evidence_ids = parse_config.new_found_evidence_ids or []
    if isinstance(parse_config.llm_result, dict):
        content = parse_config.llm_result.get("content", "")
        raw_tool_calls = parse_config.llm_result.get("tool_calls") or []
    else:
        content = parse_config.llm_result or ""
        raw_tool_calls = []

    content = (content or "").strip()
    if raw_tool_calls:
        assistant_msg = {"role": "assistant", "content": content, "tool_calls": raw_tool_calls}
    else:
        assistant_msg = {"role": "assistant", "content": content}
    messages = parse_config.messages + [assistant_msg]

    mode, patch_obj, error_message = parse_run_action_result(parse_config.llm_result)

    if error_message:
        if raw_tool_calls:
            logger.warning(
                "[run_action] LLM returned tool_calls but parse/validation failed; "
                "error is appended as a user message for the next turn. %s",
                "*" if LogManager.is_sensitive() else error_message,
            )
            # No tool rows were executed; assistant must not keep tool_calls or the API rejects the next request.
            # messages[-1] = {"role": "assistant", "content": content}
        messages.append({"role": "user", "content": error_message})
    if error_message or mode is None or mode not in ("tool_calls", "state", "answer"):
        return None, {"messages": messages}, parse_config.config
    error_message = (
        "This is a placeholder error message. It should be replaced with the actual "
        "error message. If not there is a BUG in the agent. If you are an agent, "
        "please try to fix the bug and continue the action, else termination the action."
    )
    if mode == "tool_calls":
        return mode, {"tool_calls": patch_obj, "messages": messages}, parse_config.config
    elif mode == "state":
        failed_patches: List[dict] = []
        all_merged_states: List[State] = []
        if patch_obj and not isinstance(patch_obj, dict):
            error_message = (
                f"Error: The JSON inside <state> must be a single JSON object (dict), "
                f"not a {type(patch_obj).__name__}. This usually happens when variable patches "
                "are placed outside the 'new_states' array, creating multiple top-level JSON objects. "
                "Ensure all variable patches are inside 'new_states' and the entire output is one JSON object."
            )
        elif patch_obj and "new_states" in patch_obj:
            raw_new_states = patch_obj["new_states"]
            raw_new_states = _normalize_new_states(raw_new_states)
            for patch in raw_new_states:
                try:
                    patch_state = patch.get("state", [])
                    if not patch_state or len(patch_state) == 0:
                        raise CustomValueException(
                            StatusCode.RUN_ACTION_PARSE_ERROR.code,
                            StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(e="Patch state is empty or missing"),
                        )
                    new_state = apply_patch(
                        parse_config.config,
                        parse_config.base_state,
                        patch,
                        new_found_evidence_ids=new_found_evidence_ids,
                    )
                    all_merged_states.append(new_state)
                except Exception as e:
                    failed_patches.append(
                        {
                            "error": "patch_apply_failed",
                            "exception": str(e),
                            "patch": patch,
                        }
                    )

            if len(failed_patches) == 0:
                result_to_save = Result(
                    messages=messages,
                    new_states=all_merged_states,
                    found_answer=None,
                    retrieved_evidence_ids=new_found_evidence_ids
                    + parse_config.base_state.get("retrieved_evidence_ids", []),
                    previous_action_id=parse_config.action.get("id", ""),
                )
                time_taken = time.time() - parse_config.config["action_start_time"]
                validate_new_states = parse_config.config.get("validator_agent", {}).get("validate_new_states", True)
                if not validate_new_states:
                    updated_config = _save_result(
                        parse_config.config,
                        parse_config.action,
                        result_to_save,
                        time_taken,
                    )
                    return mode, result_to_save, updated_config
                return mode, result_to_save, parse_config.config
            else:
                error_message = (
                    f"Error: Parsing the json inbetween the <state> has led to the "
                    f"following errors {failed_patches}. Please try again ensuring to "
                    "fix the failed state_patches and repeat the correctly outputed ones (if any)"
                )
        elif patch_obj and "new_states" not in patch_obj:
            error_message = (
                "Error: The JSON inside <state> must have a top-level 'new_states' key "
                f"containing a list of state branches. Got top-level keys: {list(patch_obj.keys())}. "
                "Do NOT place variable patches or other fields (e.g. 'type', 'question_clues', "
                "'discovered_clues') at the top level — they belong inside each branch's 'state' list."
            )
        logger.warning(
            "New state patch error: %s",
            "*" if LogManager.is_sensitive() else error_message,
        )
        messages.append({"role": "user", "content": error_message})

        return (
            mode,
            {"new_states": all_merged_states, "answer": "", "messages": messages},
            parse_config.config,
        )

    elif mode == "answer":
        failed_patches: List[dict] = []
        if patch_obj and patch_obj.get("answer", "") and "new_state" in patch_obj:
            answer = patch_obj.get("answer")
            try:
                new_state = apply_patch(
                    parse_config.config,
                    parse_config.action["state"],
                    patch_obj,
                    new_found_evidence_ids=new_found_evidence_ids,
                )
            except Exception as e:
                failed_patches.append(
                    {
                        "error": "patch_apply_failed",
                        "exception": str(e),
                        "patch": patch_obj,
                    }
                )

            if len(failed_patches) == 0:
                if answer:
                    result_to_save = Result(
                        messages=messages,
                        new_states=[new_state],
                        found_answer=answer,
                        retrieved_evidence_ids=new_found_evidence_ids
                        + parse_config.base_state.get("retrieved_evidence_ids", []),
                        previous_action_id=parse_config.action.get("id", ""),
                    )
                    time_taken = time.time() - parse_config.config["action_start_time"]
                    validate_answer = parse_config.config.get("validator_agent", {}).get("validate_answer", True)
                    if not validate_answer:
                        updated_config = _save_result(
                            parse_config.config,
                            parse_config.action,
                            result_to_save,
                            time_taken,
                        )
                        return mode, result_to_save, updated_config
                    return mode, result_to_save, parse_config.config
                else:
                    error_message = new_state.str_to_verify_result()
            else:
                error_message = (
                    f"Error: Parsing the json inbetween the <state> has led to the "
                    f"following errors {failed_patches}. Please try again ensuring to "
                    "fix the failed state_patches and repeat the correctly outputed ones (if any)"
                )
        else:
            error_message = (
                "Error: Answer patch is a valid json but it does not contain a " "'answer' and 'new_state' field."
            )
        logger.warning(
            "Answer patch error: %s",
            "*" if LogManager.is_sensitive() else error_message,
        )
        messages.append({"role": "user", "content": error_message})

        return (
            mode,
            {"new_states": patch_obj, "answer": answer, "messages": messages},
            parse_config.config,
        )
    messages.append({"role": "user", "content": _get_parse_error("no_output")})
    return None, {"messages": messages}, parse_config.config


async def run_action(params: RunActionConfig) -> dict:
    """Execute the run-action LLM call and return a result dict.

    Returns one of three types of dict depending on outcome:

    success=True  {"success": True, "mode": str, "data": Any, "config": dict,
                   "total_input_tokens": int, "total_output_tokens": int,
                   "validate_new_states": Any, "validate_answer": Any}
        mode is one of:
          "tool_calls" – LLM issued tool call(s); data contains {"tool_calls", "messages"}
          "state"     – LLM proposed new states; data contains {"new_states", "messages", ...}
          "answer"    – LLM produced a final answer; data contains {"answer", "new_state", "messages"}
          None        – LLM output could not be parsed; data contains {"messages"}

    success=False, try_again=True  (context-limit hit, strategy allows retry)
        strategy "reduced_retrieval_request": {"success": False, "try_again": True,
                                               "config": dict, "retrieval_settings": dict}
        strategy "delete_tool_responses" or "delete_tool_input_and_responses":
                                              {"success": False, "try_again": True, "messages": list}

    success=False  (non-retryable failure – LLM error or parse error)
        {"success": False, "next_node": str, "error": str, "messages": list}
    """
    messages = params.messages
    if not messages:
        prompt_messages = [
            {
                "role": "user",
                "content": (
                    "Please apply the above instructions to the following query, state and action proposal:\n"
                    f"Query: {params.query}\n"
                    f"State: {params.state}\n"
                    f"Action Proposal: {params.action['proposal']['direction']}"
                ),
            }
        ]
    else:
        prompt_messages = messages[1:]
    context_vars = build_react_prompt_context(
        discovered_clues_mode=params.config.get("discovered_clues_mode", "report"),
        extra_vars={"messages": prompt_messages},
    )
    template = (
        "deepsearch_run_action" if params.config.get("use_candidate_strength", True) else "deepsearch_run_action_no_cs"
    )
    tools = get_tool_definitions(retrieval_tool_only=params.retrieval_tool_only)
    messages_for_parse = apply_system_prompt(
        prompt_template_file=template,
        context_vars=context_vars or {},
    )
    try:
        llm_result, _, input_tokens, output_tokens = await run_llm(
            RunLLMConfig(
                config=params.llm_config,
                prompt_template_file=template,
                context_vars=context_vars,
                need_stream_out=False,
                agent_name=AgentLlmName.RUN_ACTION.value,
                tools=tools,
            )
        )
    except CustomValueException as e:
        if e.error_code not in (
            StatusCode.LLM_CALL_FAILED.code,
            StatusCode.AGENT_RETRY_FAILED_ALL_ATTEMPTS.code,
        ):
            raise
        err_msg = e.message or str(e)
        strategy = params.context_limit_reached_strategy
        is_ctx_limit = e.error_code == StatusCode.LLM_CALL_FAILED.code and _is_context_limit_error(
            err_msg, getattr(e, "__cause__", None)
        )
        if is_ctx_limit:
            msg_turns = len(messages_for_parse)
            approx_tokens = _estimate_message_tokens(messages_for_parse)
            if strategy == "reduced_retrieval_request" and params.retrieval_tool_only:
                reduced = dict(params.retrieval_settings or {})
                reduced["top_k"] = max(1, (params.retrieval_settings or {}).get("top_k", 1) // 2)
                reduced["top_k_multiply_factor"] = max(
                    1, (params.retrieval_settings or {}).get("top_k_multiply_factor", 1) // 2
                )
                logger.warning(
                    "[run_action] context limit hit – reducing retrieval settings: "
                    "top_k=%d top_k_multiply_factor=%d | approx_tokens≈%d message_turns=%d | %s",
                    reduced["top_k"],
                    reduced["top_k_multiply_factor"],
                    approx_tokens,
                    msg_turns,
                    format_action_for_log(params.action),
                )
                return dict(
                    success=False,
                    try_again=True,
                    config={**params.config, "retrieval_settings": reduced, "context_limit_reached_strategy": "fail"},
                    retrieval_settings=reduced,
                )
            elif strategy == "delete_tool_responses":
                cleaned = _strip_tool_responses(messages_for_parse)
                original_tokens = _estimate_message_tokens(messages_for_parse)
                cleaned_tokens = _estimate_message_tokens(cleaned)
                logger.warning(
                    "[run_action] context limit hit – deleted tool responses | "
                    "message_turns %d→%d | approx_tokens ~%d→~%d | %s",
                    len(messages_for_parse),
                    len(cleaned),
                    original_tokens,
                    cleaned_tokens,
                    format_action_for_log(params.action),
                )
                return dict(
                    success=False,
                    try_again=True,
                    messages=cleaned,
                )
            elif strategy == "delete_tool_input_and_responses":
                cleaned = _strip_tool_inputs_and_responses(messages_for_parse)
                original_tokens = _estimate_message_tokens(messages_for_parse)
                cleaned_tokens = _estimate_message_tokens(cleaned)
                logger.warning(
                    "[run_action] context limit hit – deleted tool inputs+responses | "
                    "message_turns %d→%d | approx_tokens ~%d→~%d | %s",
                    len(messages_for_parse),
                    len(cleaned),
                    original_tokens,
                    cleaned_tokens,
                    format_action_for_log(params.action),
                )
                return dict(
                    success=False,
                    try_again=True,
                    messages=cleaned,
                )
            logger.warning(
                "[run_action] context limit hit — strategy=%r retrieval_tool_only=%s; "
                "no automated trim/retry applies; failing | approx_tokens≈%d message_turns=%d | %s. "
                "If the run ends here, inspect the error result JSON under log_dir/Result/ "
                "(see following [_save_result] line with same action_id).",
                strategy,
                params.retrieval_tool_only,
                approx_tokens,
                msg_turns,
                format_action_for_log(params.action),
            )
            return dict(
                success=False,
                next_node=NodeId.END_NODE.value,
                error=err_msg,
                messages=messages_for_parse,
            )
        return dict(
            success=False,
            next_node=NodeId.END_NODE.value,
            error=err_msg,
            messages=messages_for_parse,
        )
    total_input_tokens = params.total_input_tokens + input_tokens
    total_output_tokens = params.total_output_tokens + output_tokens
    mode, data, updated_config = parse_and_apply_llm_result_safe(
        ParseAndApplyLLMResultConfig(
            config={**params.config, "action_start_time": params.action_start_time},
            action=params.action,
            llm_result=llm_result,
            base_state=params.state,
            messages=messages_for_parse,
            new_found_evidence_ids=params.new_found_evidence_ids,
        )
    )
    return dict(
        mode=mode,
        data=data,
        config=updated_config,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        validate_new_states=params.validate_new_states,
        validate_answer=params.validate_answer,
        success=True,
    )


def build_react_prompt_context(
    *,
    discovered_clues_mode: str = "blacklist",
    extra_vars: dict | None = None,
) -> dict:
    """Build context for run_action system prompt. Sections are loaded from prompts folder."""
    clues_key = discovered_clues_mode if discovered_clues_mode in ("blacklist", "report", "none") else "blacklist"
    context = {
        "CLUES_SECTION": get_prompt_section(f"deepsearch_run_action_clues_{clues_key}"),
    }
    if extra_vars:
        context.update(extra_vars)
    return context


def parse_and_apply_llm_result_safe(
    parse_config: ParseAndApplyLLMResultConfig,
) -> tuple:
    try:
        mode, data, updated_config = parse_and_apply_llm_result(
            ParseAndApplyLLMResultConfig(
                config=parse_config.config,
                action=parse_config.action,
                llm_result=parse_config.llm_result,
                base_state=parse_config.base_state,
                messages=parse_config.messages,
                new_found_evidence_ids=parse_config.new_found_evidence_ids,
            )
        )
        return mode, data, updated_config
    except Exception as e:
        raise CustomValueException(
            StatusCode.RUN_ACTION_PARSE_ERROR.code,
            StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(e=e),
        ) from e
