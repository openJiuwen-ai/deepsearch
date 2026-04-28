import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openjiuwen_deepsearch.algorithm.search_nodes.llm_utils import RunLLMConfig, run_llm
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.utils.constants_utils.node_constants import AgentLlmName

logger = logging.getLogger(__name__)

HAS_PLACEHOLDER_RX = re.compile(r"\[[^\]]+\]")
ENTITY_RX = re.compile(r"\[([^\]]+)\]")
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


NUMBER_WORDS = {
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
    "hundred",
    "thousand",
    "million",
    "billion",
}

QUANTITY_PHRASES = [
    r"no\s+more\s+than",
    r"less\s+than",
    r"more\s+than",
    r"at\s+least",
    r"at\s+most",
    r"up\s+to",
    r"about\s+\d+",
    r"between\s+\d+\s+and\s+\d+",
]


def norm_val(s: Any, blank: bool = True) -> Any:
    if isinstance(s, str):
        if not blank:
            while " " in s:
                s = s.replace(" ", "")
        return re.sub(r"\s+", " ", (s or "").strip().lower())
    if isinstance(s, int):
        return str(s)
    return s


def to_entity_map(entities: List[dict], key_name: str) -> Dict[str, dict]:
    return {norm_val(e.get(key_name)): e for e in entities if e.get(key_name)}


def has_placeholder(clue: str) -> bool:
    return bool(HAS_PLACEHOLDER_RX.search(clue))


def extract_json_substr(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError(f"LLM content is not str: {type(text)}")
    m = JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start: end + 1].strip()
        return candidate

    raise CustomValueException(
        StatusCode.RUN_ACTION_PARSE_ERROR.code,
        StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(e="No JSON-looking substring found"),
    )


def parse_llm_result(llm_result) -> dict:
    if isinstance(llm_result, dict):
        content = llm_result.get("content")
        return json.loads(content)
    s = extract_json_substr(llm_result) if "```json" in llm_result else llm_result
    try:
        obj = json.loads(s)
    except Exception as e:
        raise CustomValueException(
            StatusCode.RUN_ACTION_PARSE_ERROR.code,
            StatusCode.RUN_ACTION_PARSE_ERROR.errmsg.format(e=f"JSON parse failed: {e}\n---raw---\n{s}\n-----------"),
        ) from e
    if not isinstance(obj, dict):
        raise TypeError(f"Expected JSON object, got {type(obj).__name__}")
    return obj


def render_clues_placeholder(clues: List[str], emap: Dict[str, dict]) -> List[Dict[str, Any]]:
    rendered: List[Dict[str, Any]] = []

    def repl(match: re.Match) -> str:
        key = norm_val(match.group(1).strip())
        value = emap.get(key, {}).get("value")
        return match.group(0) if not value else value

    for clue in clues:
        replaced = ENTITY_RX.sub(repl, clue)
        rendered.append({"text": replaced, "has_placeholder": has_placeholder(replaced)})
    return rendered


def render_clues(entities: List[Dict[str, Any]], candidates: Optional[List[dict]]) -> List[Dict[str, Any]]:
    entity_map = {norm_val(e["variable_id"]): norm_val(e.get("value")) for e in entities if "variable_id" in e}

    if candidates:
        entity_map.update(
            {norm_val(c["variable_id"]): norm_val(c.get("value")) for c in candidates if "variable_id" in c}
        )

    def repl(match: re.Match) -> str:
        key = norm_val(match.group(1).strip())
        return str(entity_map.get(key)) if entity_map.get(key) else match.group(0)

    for entity in entities:
        vid = norm_val(entity["variable_id"])
        if entity_map.get(vid):
            entity["value"] = entity_map[vid]

        rendered = [ENTITY_RX.sub(repl, clue) for clue in entity.get("clues", [])]
        entity["clues"] = rendered

    return entities


def update_entities(entities: List[Dict[str, Any]], candidates: List[dict]) -> List[Dict[str, Any]]:
    entity_map = {norm_val(e["variable_id"]): norm_val(e.get("value")) for e in entities if "variable_id" in e}
    entity_map.update({norm_val(c["variable_id"]): norm_val(c.get("value")) for c in candidates if "variable_id" in c})

    for entity in entities:
        vid = norm_val(entity["variable_id"])
        if entity_map.get(vid):
            entity["value"] = entity_map[vid]

    return entities


def merge_candidates(pools: List[dict]) -> dict:
    merged: Dict[str, Dict[str, dict]] = {}
    for pool in pools:
        for ent in pool.get("candidates", []):
            name = norm_val(ent["variable_id"])
            merged.setdefault(name, {})

            for c in ent.get("candidates", []):
                val_norm = norm_val(c["value"])
                slot = merged[name].setdefault(
                    val_norm,
                    {"value": c["value"], "evidence": [], "rationale": []},
                )
                if c.get("evidence"):
                    slot["evidence"].append(c["evidence"])
                if c.get("rationale"):
                    slot["rationale"].append(c["rationale"])

    final = {"candidates": []}
    for name, vals in merged.items():
        final["candidates"].append(
            {
                "variable_id": name,
                "candidates": [
                    {
                        "value": v["value"],
                        "evidence": " | ".join(v["evidence"]),
                        "rationale": " | ".join(v["rationale"]),
                    }
                    for v in vals.values()
                ],
            }
        )

    return final


def compute_overall(clue_statuses: List[str], flag: bool) -> str:
    if any(s == "fail" for s in clue_statuses):
        return "fail"
    if clue_statuses and all(s == "pass" for s in clue_statuses):
        return "pass"
    if flag:
        logger.info("ERROR! need re-verification")
    return "pending"


def has_number_like(text: str) -> bool:
    cleaned = re.sub(r"\[[^\]]*\]", "", text).lower()
    if re.search(r"\d", cleaned):
        return True
    if any(re.search(p, cleaned) for p in QUANTITY_PHRASES):
        return True
    tokens = re.findall(r"[a-zA-Z]+", cleaned)
    return any(tok in NUMBER_WORDS for tok in tokens)


def has_number_in_clues(clues: List[str]) -> List[int]:
    indices: List[int] = []
    for idx, clue in enumerate(clues):
        cleaned = re.sub(r"\[.*?\]", "", clue).lower()
        if re.search(r"\d", cleaned):
            indices.append(idx)
            continue
        if any(re.search(p, cleaned) for p in QUANTITY_PHRASES):
            indices.append(idx)
            continue
        tokens = re.findall(r"[a-zA-Z]+", cleaned)
        if any(tok in NUMBER_WORDS for tok in tokens):
            indices.append(idx)
    return indices


async def find_relative_info(
    candidate: Any,
    clue_items: Any,
    model_name: str,
):
    content, _, in_tok, out_tok = await run_llm(
        RunLLMConfig(
            config={
                "model_name": model_name,
                "max_tries": 4,
            },
            prompt_template_file="deepsearch_filter_relative_info",
            context_vars={
                "candidate": candidate,
                "search_results": clue_items,
            },
            need_stream_out=False,
            agent_name=AgentLlmName.VALIDATE_NEW_STATE.value,
        )
    )

    results = parse_llm_result(content).get("results", [])
    if not results:
        return [], in_tok, out_tok

    filtered = [clue_items[d["index"] - 1] for d in results if d.get("relevant") and d.get("index") is not None]

    return filtered, in_tok, out_tok


@dataclass
class VerifyCoarseConfig:
    query: str
    entities: Any
    focus: str
    candidate: Any
    clue_items: Any
    evidence: Any
    model_name: str


async def verify_coarse(verify_config: VerifyCoarseConfig):
    clues_text = "\n".join(
        f"- {it['text']} (HAS_PLACEHOLDER={str(it['has_placeholder']).lower()})" for it in verify_config.clue_items
    )
    content, _, in_tok, out_tok = await run_llm(
        RunLLMConfig(
            config={
                "model_name": verify_config.model_name,
                "max_tries": 4,
            },
            prompt_template_file="deepsearch_verify",
            context_vars={
                "query": verify_config.query,
                "entity_schema": verify_config.entities,
                "entity_name": verify_config.focus,
                "candidates_json": verify_config.candidate,
                "clues_text": clues_text,
                "evidence": verify_config.evidence,
            },
            need_stream_out=False,
            agent_name=AgentLlmName.VALIDATE_NEW_STATE.value,
        )
    )

    return parse_llm_result(content), in_tok, out_tok


async def verify_coarse_set(
    query: str,
    entities: Any,
    candidates: Any,
    model_name: str,
):
    content, _, in_tok, out_tok = await run_llm(
        RunLLMConfig(
            config={
                "model_name": model_name,
                "max_tries": 4,
            },
            prompt_template_file="deepsearch_verify_set",
            context_vars={
                "query": query,
                "entity_schema": entities,
                "candidates_json": candidates,
            },
            need_stream_out=False,
            agent_name=AgentLlmName.VALIDATE_NEW_STATE.value,
        )
    )

    return parse_llm_result(content), in_tok, out_tok
