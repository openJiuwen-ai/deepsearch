# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, Field, field_validator

from openjiuwen_deepsearch.algorithm.search_nodes.verify_utils import (
    compute_overall,
    find_relative_info,
    has_number_like,
    has_placeholder,
    norm_val,
    render_clues_placeholder,
    to_entity_map,
    update_entities,
    verify_coarse,
    verify_coarse_set,
)
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.common.status_code import StatusCode
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import (
    CandidateVerifiedClues,
    State,
    VerifiedVariableResult,
)
from openjiuwen_deepsearch.utils.log_utils.log_manager import LogManager

logger = logging.getLogger(__name__)


class EntityCandidate(BaseModel):
    variable_id: str
    value: str


class Entity(BaseModel):
    variable_id: str
    type: str
    clues: List[str]
    value: Optional[str] = None


class VerifyOptions(BaseModel):
    mode: str = Field(default="isolated", description="isolated | linked | set")
    defer_numeric: bool = Field(default=False, description="whether to defer numeric clues")
    max_links: Optional[int] = None
    use_scholar: Optional[bool] = None
    llm_ctx_max: Optional[int] = None
    llm_model_name: Optional[str] = None
    judge_model: Optional[str] = None
    numeric_model: Optional[str] = None
    judge_max_tokens: Optional[int] = None
    numeric_max_tokens: Optional[int] = None

    @field_validator("mode")
    @classmethod
    def normalize_mode(cls, v: str) -> str:
        v = v.lower()
        return v if v in ("isolated", "linked", "set") else "isolated"


class Candidate(BaseModel):
    variable_id: str
    type: str
    value: str
    evidence: Optional[str] = ""


class VerifyInput(BaseModel):
    entities: List[Entity]
    candidates: List[Candidate]
    problem_text: str
    options: VerifyOptions = Field(default_factory=VerifyOptions)


def _build_clue_items(clues: List[str], entity_map: Dict[str, Any], mode: str) -> List[Dict[str, Any]]:
    if mode == "linked":
        return render_clues_placeholder(clues, entity_map)
    return [{"text": c, "has_placeholder": has_placeholder(c)} for c in clues]


def _split_numeric_clues(clue_items: List[Dict[str, Any]], defer_numeric: bool):
    if not defer_numeric:
        return clue_items, [], []

    numeric, filtered = [], []
    for item in clue_items:
        if has_number_like(item.get("text", "")):
            numeric.append(item)
        else:
            filtered.append(item)
    return filtered, numeric, filtered


def process_verify_report(
    cand_reports: Dict[str, Any],
    clue_items: List[Dict[str, Any]],
    defer_numeric: bool,
    numeric_clues: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    reports = cand_reports.get("candidates", [])
    results: Dict[str, Any] = {}

    for cand in reports:
        fixed_statuses = []
        promote_flag = False

        for idx, clue_judge in enumerate(cand.get("clues", [])):
            status = (clue_judge.get("status") or "").strip().lower()
            text = (clue_judge.get("text") or "").strip()

            has_ph = clue_items[idx]["has_placeholder"] if idx < len(clue_items) else has_placeholder(text)

            if has_ph and status == "pass":
                promote_flag = True
                status = "pending"

            clue_judge["status"] = status
            fixed_statuses.append(status)

        if defer_numeric:
            for item in numeric_clues:
                cand["clues"].append(
                    {
                        "text": item.get("text", ""),
                        "status": "pending",
                        "reason": "contains digit, requiring careful analysis",
                    }
                )

        cand["overall"] = compute_overall(fixed_statuses, promote_flag)

        if len(candidates) == 1:
            candidate = candidates[0]
        else:
            candidate = next(
                (c for c in candidates if norm_val(c.get("variable_id")) == norm_val(cand.get("id"))),
                {},
            )

        if candidate.get("evidence"):
            cand["evidence"] = candidate["evidence"]

        cand.pop("id", None)
        results[candidate.get("variable_id")] = cand

    return results


@dataclass
class VerifyEntityConfig:
    entities: Any
    candidate: Any
    query: str
    model_name: str
    mode: str = "linked"
    defer_numeric: bool = False


async def verify_entity(verify_config: VerifyEntityConfig):
    entity_map = to_entity_map(verify_config.entities, key_name="variable_id")
    focus = norm_val(verify_config.candidate.get("variable_id"))
    entity_names = [norm_val(e.get("variable_id")) for e in verify_config.entities if e.get("variable_id")]

    if not focus or focus not in entity_names:
        logger.error("[verify_entity] invalid candidate variable_id")
        return {}, 0, 0

    focus_entity = entity_map[focus]
    clue_items = _build_clue_items(focus_entity.get("clues", []), entity_map, verify_config.mode)
    used_clues, numeric_clues, _ = _split_numeric_clues(clue_items, verify_config.defer_numeric)

    candidate_value = verify_config.candidate.get("value")
    evidence = verify_config.candidate.get("evidence")

    if not candidate_value:
        return {}, 0, 0

    from openjiuwen_deepsearch.algorithm.search_nodes.verify_utils import VerifyCoarseConfig

    cand_reports, in_tok, out_tok = await verify_coarse(
        VerifyCoarseConfig(
            query=verify_config.query,
            entities=verify_config.entities,
            focus=focus,
            candidate=candidate_value,
            clue_items=used_clues,
            evidence=evidence,
            model_name=verify_config.model_name,
        )
    )

    results = process_verify_report(
        cand_reports,
        used_clues,
        verify_config.defer_numeric,
        numeric_clues,
        [verify_config.candidate],
    )
    return results, in_tok, out_tok


@dataclass
class VerifyEntitySetConfig:
    entities: Any
    candidates: Any
    query: str
    model_name: str
    mode: str = "linked"
    defer_numeric: bool = False


async def verify_entity_set(verify_config: VerifyEntitySetConfig):
    entity_map = to_entity_map(verify_config.entities, key_name="variable_id")
    entity_names = [norm_val(e.get("variable_id")) for e in verify_config.entities if e.get("variable_id")]

    prepared_candidates = []
    numeric_clues, clue_items = [], []

    for candidate in verify_config.candidates:
        focus = norm_val(candidate.get("variable_id"))
        if not focus or focus not in entity_names:
            logger.error("[verify_entity_set] invalid candidate variable_id")
            continue

        if not candidate.get("value"):
            continue

        focus_entity = entity_map[focus]
        clue_items = _build_clue_items(focus_entity.get("clues", []), entity_map, verify_config.mode)
        used_clues, numeric_clues, _ = _split_numeric_clues(clue_items, verify_config.defer_numeric)

        candidate["clue_items"] = used_clues
        prepared_candidates.append(candidate)

    if not prepared_candidates:
        return {}, 0, 0

    cand_reports, in_tok, out_tok = await verify_coarse_set(
        query=verify_config.query,
        entities=verify_config.entities,
        candidates=prepared_candidates,
        model_name=verify_config.model_name,
    )

    results = process_verify_report(
        cand_reports,
        clue_items,
        verify_config.defer_numeric,
        numeric_clues,
        prepared_candidates,
    )
    return results, in_tok, out_tok


class SimpleTool:
    name: str
    description: str
    args_schema: Type[BaseModel]

    async def run(self, raw_args: Dict[str, Any]):
        args = self.args_schema.model_validate(raw_args)
        return await self._run(**args.model_dump())

    async def _run(self, **kwargs):
        raise NotImplementedError


class Verify(SimpleTool):
    name: str = "verify"
    description: str = "Clue verification via retrieval and LLM reasoning."
    args_schema: ClassVar[Type[BaseModel]] = VerifyInput

    async def _run(
        self,
        entities: List[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        problem_text: str,
        options: Dict[str, Any],
        **kwargs,
    ):
        for e in entities:
            e["variable_id"] = norm_val(e["variable_id"])
            if e.get("value"):
                e["value"] = norm_val(e["value"])

        for c in candidates:
            c["variable_id"] = norm_val(c["variable_id"])
            c["value"] = norm_val(c["value"])

        mode = (options.get("mode") or "isolated").lower()
        model_name = options.get("judge_model") or options.get("llm_model_name")
        defer_numeric = options.get("defer_numeric", False)

        entities = update_entities(entities, candidates)

        total_in, total_out = 0, 0
        verify_results: Dict[str, Any] = {}

        if mode == "set":
            verify_results, total_in, total_out = await verify_entity_set(
                VerifyEntitySetConfig(
                    entities=entities,
                    candidates=candidates,
                    query=problem_text,
                    mode="isolated",
                    defer_numeric=defer_numeric,
                    model_name=model_name,
                )
            )
        else:
            for candidate in candidates:
                res, in_tok, out_tok = await verify_entity(
                    VerifyEntityConfig(
                        entities=entities,
                        candidate=candidate,
                        query=problem_text,
                        mode="linked",
                        defer_numeric=defer_numeric,
                        model_name=model_name,
                    )
                )
                total_in += in_tok
                total_out += out_tok
                verify_results.update(res)

        return verify_results, total_in, total_out

    async def _arun(self, *args, **kwargs):
        raise NotImplementedError


async def validate_new_state(new_state: State, validator_model_name: str, query: str) -> List[VerifiedVariableResult]:
    total_in, total_out = 0, 0
    verify_results: List[VerifiedVariableResult] = []

    entity_mapping: Dict[str, Any] = {}
    entities: List[dict] = []
    focus_candidates: List[dict] = []

    if isinstance(new_state, dict):
        new_state = State(**new_state)

    for s in new_state.state:
        if s.candidate and norm_val(s.candidate) != "none":
            entity_mapping[norm_val(s.id)] = s
            entities.append({"variable_id": str(s.id), "type": s.type, "clues": s.question_clues})

            discovered, in_tok, out_tok = await find_relative_info(
                candidate=s.candidate,
                clue_items=s.discovered_clues,
                model_name=validator_model_name,
            )
            total_in += in_tok or 0
            total_out += out_tok or 0

            focus_candidates.append(
                {
                    "variable_id": str(s.id),
                    "type": s.type,
                    "value": s.candidate,
                    "evidence": " | ".join(discovered),
                }
            )

    verify_result = {}
    if focus_candidates:
        verify_tool = Verify()
        verify_result, in_tok, out_tok = await verify_tool.run(
            {
                "entities": entities,
                "candidates": focus_candidates,
                "problem_text": query,
                "options": {
                    "mode": "linked",
                    "defer_numeric": False,
                    "llm_model_name": validator_model_name,
                },
            }
        )
        total_in += in_tok or 0
        total_out += out_tok or 0

    for key, result in verify_result.items():
        variable = entity_mapping.get(norm_val(key))
        if not variable:
            continue
        verify_results.append(
            VerifiedVariableResult(
                id=variable.id,
                type=variable.type,
                candidate_verified_clues=CandidateVerifiedClues.model_validate(result),
            )
        )

    logger.info(
        "[validate_new_state] verify_results: %s",
        "***" if LogManager.is_sensitive() else verify_results,
    )
    return verify_results, total_in, total_out


async def run_validations(
    new_states: List[Any],
    llm_model_name: str,
    query: str,
) -> List[Tuple[Any, Any, int, int]]:
    result: List[Tuple[Any, Any, int, int]] = []
    for i, new_state in enumerate(new_states):
        try:
            verify_results, input_tokens, output_tokens = await validate_new_state(new_state, llm_model_name, query)
            result.append((new_state, verify_results, input_tokens, output_tokens))
        except Exception as e:
            raise CustomValueException(
                StatusCode.VALIDATE_NEW_STATE_ERROR.code,
                StatusCode.VALIDATE_NEW_STATE_ERROR.errmsg.format(e=f"state[{i}]: {e}"),
            ) from e
    return result
