# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""API 路由：健康检查、检索、索引（后台任务）。

设计取舍：
- **检索**同步返回（典型 3 分钟量级，服务端设超时上限，超时返回 504）；
- **索引**是分钟级长任务，交后台执行并返回 job_id，由 `/jobs/{id}` 轮询状态；
  作业状态保存在进程内存（单进程服务足够；多副本部署需换外部存储），
  并设条数上限，避免长驻进程无界增长。

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

from fastapi import APIRouter, HTTPException

from openjiuwen_codesearch import CodeSearchConfig, CodeSearchRetriever

from openjiuwen_codesearch.server.core.config import settings
from openjiuwen_codesearch.server.schemas import (
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

# 按 collection 复用检索器：每请求新建会重复 connect + load collection（秒级）
_retrievers: dict[str, CodeSearchRetriever] = {}
_retriever_lock = asyncio.Lock()


def remember_job(job: JobResponse) -> None:
    jobs[job.job_id] = job
    jobs.move_to_end(job.job_id)
    while len(jobs) > MAX_JOBS:
        oldest, _ = jobs.popitem(last=False)
        logger.debug("job table full; evicted %s", oldest)


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


async def _get_retriever(collection: str) -> CodeSearchRetriever:
    async with _retriever_lock:
        retriever = _retrievers.get(collection)
        if retriever is None:
            retriever = CodeSearchRetriever(CodeSearchConfig.from_env(), collection_name=collection)
            _retrievers[collection] = retriever
        return retriever


async def _drop_retriever(collection: str) -> None:
    """索引（尤其 reset=True）会重建 collection，缓存的句柄随即失效。"""
    async with _retriever_lock:
        retriever = _retrievers.pop(collection, None)
    if retriever is not None:
        await retriever.close()


async def shutdown_retrievers() -> None:
    """进程关停时释放所有 Milvus 连接别名。"""
    async with _retriever_lock:
        retrievers = list(_retrievers.values())
        _retrievers.clear()
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
    retriever = await _get_retriever(req.collection)
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


async def _run_index_job(job_id: str, req: IndexRequest, repo_path: str) -> None:
    # 先失效缓存：本次索引可能重建 collection，旧句柄不能继续服务检索
    await _drop_retriever(req.collection)
    config = CodeSearchConfig.from_env()
    retriever = CodeSearchRetriever(config, collection_name=req.collection)
    try:
        report = await retriever.index_repository(
            repo_path, revision=req.revision, reset=req.reset
        )
        remember_job(
            JobResponse(
                job_id=job_id,
                status="succeeded",
                detail=(
                    f"{report.files_total} files "
                    f"({report.files_new} new, {report.files_reused} reused), "
                    f"{report.chunks_inserted} chunks inserted"
                ),
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
