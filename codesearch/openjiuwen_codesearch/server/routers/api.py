# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""API 路由：健康检查、检索、索引（后台任务）。

设计取舍：
- **检索**同步返回（典型 3 分钟量级，服务端设超时上限，超时返回 504）；
- **索引**是分钟级长任务，交后台执行并返回 job_id，由 `/jobs/{id}` 轮询状态；
  作业状态保存在进程内存（单进程服务足够；多副本部署需换外部存储），
  并设条数上限，避免长驻进程无界增长。
- **引擎**：请求体可选 `engine`（默认 `auto`）；`retropus` 须显式指定。
  缓存键为 `(collection, engine)`。Retropus 索引驻留进程内，索引成功后
  不得 `close()`；Milvus 族仍可索引后释放句柄。同 collection 若已用另一
  后端（retropus ↔ milvus）索引过，search 返回 409。

安全边界：本服务**不含鉴权**，且 `/v1/index` 读取的是服务端本地目录。
因此索引根目录必须经 `CODESEARCH_INDEX_ROOTS` 显式配置白名单，
未配置时该接口一律拒绝——否则可访问本服务者即可让服务端索引宿主机任意
目录下的 .py 文件，再通过 `/v1/search` 读回其内容。
"""

import asyncio
import logging
import uuid
from collections import OrderedDict
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException

from openjiuwen_codesearch import CodeSearchConfig, CodeSearchRetriever

from openjiuwen_codesearch.server.core.config import settings
from openjiuwen_codesearch.server.schemas import (
    EngineName,
    HealthResponse,
    HitModel,
    IndexRequest,
    JobResponse,
    SearchRequest,
    SearchResponse,
)

logger = logging.getLogger(__name__)

api_router = APIRouter()

# 进程内作业表，有界：超出后淘汰最早写入的记录
MAX_JOBS = 512
jobs: "OrderedDict[str, JobResponse]" = OrderedDict()

# 后台任务的强引用：事件循环只持弱引用，不留强引用则任务可能被 GC
# 回收而中途消失，作业状态将永远停在 running
_background_tasks: set[asyncio.Task] = set()

# 按 (collection, engine) 复用检索器：每请求新建会重复 connect + load（秒级）；
# Retropus 还必须复用同一实例上的进程内 KG/BM25
CacheKey = tuple[str, EngineName]
_retrievers: dict[CacheKey, CodeSearchRetriever] = {}
_retriever_lock = asyncio.Lock()

# 本进程内各 collection 最近一次成功索引所用的 engine（用于跨后端 409）
_indexed_engines: dict[str, EngineName] = {}

IndexBackend = Literal["retropus", "milvus"]


def remember_job(job: JobResponse) -> None:
    jobs[job.job_id] = job
    jobs.move_to_end(job.job_id)
    while len(jobs) > MAX_JOBS:
        oldest, _ = jobs.popitem(last=False)
        logger.debug("job table full; evicted %s", oldest)


def _index_backend(engine: EngineName) -> IndexBackend:
    return "retropus" if engine == "retropus" else "milvus"


def _config_for_engine(engine: EngineName) -> CodeSearchConfig:
    config = CodeSearchConfig.from_env()
    return config.model_copy(
        update={"agent": config.agent.model_copy(update={"engine": engine})}
    )


def _resolve_repo_path(raw: str) -> str:
    """校验索引路径落在白名单根目录内，返回规范化后的绝对路径。

    先 resolve 再比较：符号链接与 `..` 在此一并展开，无法借它们绕出白名单。
    """
    roots = settings.allowed_index_roots()
    if not roots:
        raise HTTPException(
            status_code=403,
            detail=(
                "indexing is disabled: set CODESEARCH_INDEX_ROOTS to the "
                "directories this service is allowed to index"
            ),
        )
    try:
        target = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail="repo_path does not exist") from e
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="repo_path is not a directory")
    if not any(target == root or root in target.parents for root in roots):
        raise HTTPException(status_code=403, detail="repo_path is outside CODESEARCH_INDEX_ROOTS")
    return str(target)


def _ensure_engine_compatible(collection: str, engine: EngineName) -> None:
    previous = _indexed_engines.get(collection)
    if previous is None:
        return
    if _index_backend(previous) != _index_backend(engine):
        raise HTTPException(
            status_code=409,
            detail=(
                f"collection {collection!r} was indexed with engine={previous!r}; "
                f"request used engine={engine!r}. Re-index with the desired engine "
                f"or pass a matching engine on search."
            ),
        )


async def _drop_keys(keys: list[CacheKey]) -> None:
    dropped: list[CodeSearchRetriever] = []
    async with _retriever_lock:
        for key in keys:
            retriever = _retrievers.pop(key, None)
            if retriever is not None:
                dropped.append(retriever)
    for retriever in dropped:
        await retriever.close()


async def _drop_incompatible_backends(collection: str, engine: EngineName) -> None:
    """Drop cached retrievers for the other index backend on this collection."""
    want = _index_backend(engine)
    async with _retriever_lock:
        keys = [
            key
            for key in _retrievers
            if key[0] == collection and _index_backend(key[1]) != want
        ]
    if keys:
        await _drop_keys(keys)


async def _get_retriever(collection: str, engine: EngineName) -> CodeSearchRetriever:
    key = (collection, engine)
    async with _retriever_lock:
        retriever = _retrievers.get(key)
        if retriever is None:
            retriever = CodeSearchRetriever(
                _config_for_engine(engine), collection_name=collection
            )
            _retrievers[key] = retriever
        return retriever


async def _drop_retriever(collection: str, engine: EngineName) -> None:
    """索引（尤其 reset=True）会重建 collection，缓存的句柄随即失效。"""
    await _drop_keys([(collection, engine)])


async def shutdown_retrievers() -> None:
    """进程关停时释放所有检索器资源（Milvus 连接 / Retropus 进程内索引）。"""
    async with _retriever_lock:
        retrievers = list(_retrievers.values())
        _retrievers.clear()
        _indexed_engines.clear()
    for retriever in retrievers:
        try:
            await retriever.close()
        except Exception:  # 关停阶段尽力而为，单个失败不影响其余
            logger.exception("failed to close retriever during shutdown")


@api_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=version("openjiuwen-codesearch"))


@api_router.post("/v1/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    _ensure_engine_compatible(req.collection, req.engine)
    retriever = await _get_retriever(req.collection, req.engine)
    try:
        result = await asyncio.wait_for(
            retriever.search(req.query, revision=req.revision, top_k=req.top_k),
            timeout=settings.search_timeout_seconds,
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail="search timed out") from e

    return SearchResponse(
        termination=result.termination.value,
        turns=result.turns,
        total_input_tokens=result.total_input_tokens,
        total_output_tokens=result.total_output_tokens,
        hits=[
            HitModel(
                file_path=h.file_path,
                start_line=h.start_line,
                end_line=h.end_line,
                text=h.text,
            )
            for h in result.hits
        ],
    )


def _job_success_detail(report) -> str:
    return (
        f"{report.files_total} files "
        f"({report.files_new} new, {report.files_reused} reused), "
        f"{report.chunks_inserted} chunks inserted"
    )


async def _run_index_job(job_id: str, req: IndexRequest, repo_path: str) -> None:
    await _drop_incompatible_backends(req.collection, req.engine)
    config = _config_for_engine(req.engine)
    key: CacheKey = (req.collection, req.engine)

    if CodeSearchRetriever.engine_keeps_index_in_process(req.engine):
        # 进程内索引：必须复用缓存实例，索引成功后不得 close
        async with _retriever_lock:
            retriever = _retrievers.get(key)
            if retriever is None:
                retriever = CodeSearchRetriever(config, collection_name=req.collection)
                _retrievers[key] = retriever
        try:
            report = await retriever.index_repository(
                repo_path, revision=req.revision, reset=req.reset
            )
            _indexed_engines[req.collection] = req.engine
            remember_job(
                JobResponse(
                    job_id=job_id, status="succeeded", detail=_job_success_detail(report)
                )
            )
        except Exception as e:  # 作业失败需记录状态而非拖垮服务
            logger.exception("index job %s failed", job_id)
            remember_job(JobResponse(job_id=job_id, status="failed", detail=str(e)))
        return

    # Milvus 族：先失效缓存（重建 collection 后旧句柄不可用），索引后可 close
    await _drop_retriever(req.collection, req.engine)
    retriever = CodeSearchRetriever(config, collection_name=req.collection)
    try:
        report = await retriever.index_repository(
            repo_path, revision=req.revision, reset=req.reset
        )
        _indexed_engines[req.collection] = req.engine
        remember_job(
            JobResponse(
                job_id=job_id, status="succeeded", detail=_job_success_detail(report)
            )
        )
    except Exception as e:  # 作业失败需记录状态而非拖垮服务
        logger.exception("index job %s failed", job_id)
        remember_job(JobResponse(job_id=job_id, status="failed", detail=str(e)))
    finally:
        await retriever.close()


@api_router.post("/v1/index", response_model=JobResponse, status_code=202)
async def index(req: IndexRequest) -> JobResponse:
    repo_path = _resolve_repo_path(req.repo_path)
    job_id = uuid.uuid4().hex
    remember_job(JobResponse(job_id=job_id, status="running"))
    task = asyncio.create_task(_run_index_job(job_id, req, repo_path))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return jobs[job_id]


@api_router.get("/v1/jobs/{job_id}", response_model=JobResponse)
async def job_status(job_id: str) -> JobResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
