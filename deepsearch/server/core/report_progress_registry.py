# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""在本地或 Redis 发布报告进度，不参与任务执行和 checkpoint 管理。"""

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from functools import wraps

from redis.asyncio import Redis, RedisCluster
from redis.exceptions import RedisError

from openjiuwen_deepsearch.framework.openjiuwen.agent.report_progress import ResearchProgress
from openjiuwen_deepsearch.utils.constants_utils.session_contextvars import report_progress_context
from server.core.config import settings
from server.schemas.deepsearch_run import ResearchProgressResponse

logger = logging.getLogger(__name__)

# 所有 key 使用同一报告 hash tag，可在 Redis Cluster 中原子执行。
_ALLOCATE = """
local existing = redis.call('GET', KEYS[2])
if existing then return existing end
local now = redis.call('TIME')
local nonce = redis.sha1hex(ARGV[1] .. ':' .. now[1] .. ':' .. now[2])
redis.call('HSETNX', KEYS[1], 'epoch', nonce)
local epoch = redis.call('HGET', KEYS[1], 'epoch')
local version = redis.call('HINCRBY', KEYS[1], 'version', 1)
local lease = epoch .. ':' .. tostring(version)
redis.call('SET', KEYS[2], lease, 'EX', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[2])
return lease
"""

# 有效凭据必须在实际执行前取得；过期或已关闭的旧轮次不能重新登记。
_VALID_LEASE = """
local lease = ARGV[2] .. ':' .. ARGV[3]
if redis.call('GET', KEYS[3]) ~= lease then return 0 end
if redis.call('HGET', KEYS[4], 'epoch') ~= ARGV[2] then return 0 end
"""
_EXTEND_LEASE = """
for _, key in ipairs(KEYS) do redis.call('EXPIRE', key, ARGV[4]) end
return 1
"""
_REGISTER = _VALID_LEASE + """
local current = redis.call('GET', KEYS[2])
if not current and redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
if current then
    local epoch, version = string.match(current, '^([^:]+):(%d+)$')
    if epoch == ARGV[2] and tonumber(version) > tonumber(ARGV[3]) then return 0 end
    if current == lease and redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then return 0 end
end
redis.call('HSET', KEYS[1], 'owner', ARGV[1], 'snapshot', ARGV[5])
redis.call('SET', KEYS[2], lease)
""" + _EXTEND_LEASE
_CURRENT_OWNER = _VALID_LEASE + """
if redis.call('GET', KEYS[2]) ~= lease then return 0 end
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then return 0 end
"""
_REFRESH = _CURRENT_OWNER + """
redis.call('HSET', KEYS[1], 'snapshot', ARGV[5])
""" + _EXTEND_LEASE
_RENEW = _CURRENT_OWNER + _EXTEND_LEASE
_REMOVE = """
redis.call('DEL', KEYS[3])
if redis.call('HGET', KEYS[1], 'owner') ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


class ProgressStorageUnavailable(RuntimeError):
    """共享进度不可读取，不能作为没有活跃报告处理。"""


@dataclass
class ProgressRegistration:
    """关联本轮进度与后台发布任务。

    Attributes:
        task_key: 原 HTTP 运行字典使用的空间、会话组合键。
        redis_key: Redis 进度记录键。
        token: 本轮唯一标识，防止进度互相覆盖，不是执行锁。
        progress: SDK 与 HTTP 共用的进度对象。
        task: 后台发布任务；本地部署为 None。
        closed: 是否已停止发布。
        lock: 串行化本轮关闭操作。
        ready: 共享轮次准备已完成，失败时同样唤醒业务执行。
        epoch: 同一报告轮次序列的有效期标识。
        version: 在实际执行前取得的共享递增轮次。
    """

    task_key: str
    redis_key: str
    token: str
    progress: ResearchProgress
    task: asyncio.Task | None = None
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    epoch: str | None = None
    version: str | None = None

    @property
    def redis_keys(self) -> tuple[str, str, str, str]:
        """返回同槽的快照、水位、轮次凭据和序列 key。

        Returns:
            当前报告的四个 Redis key，凭据仅属于本轮。
        """
        prefix = self.redis_key.removesuffix(":active")
        return self.redis_key, prefix + ":latest", prefix + ":round:" + self.token, prefix + ":sequence"


class ReportProgressRegistry:
    """管理进度投影，存储故障不阻止原报告执行。

    Attributes:
        ttl_seconds: 停止续期后的记录保留秒数。
        heartbeat_seconds: 后台发布间隔。
    """

    def __init__(self, *, ttl_seconds: int = 30, heartbeat_seconds: float = 1.0):
        """创建观察器，Redis 连接按需建立。

        Args:
            ttl_seconds: 共享记录有效期，需大于发布间隔。
            heartbeat_seconds: 发布间隔秒数。

        Raises:
            ValueError: 时间参数不符合正值与有效期约束。
        """
        if heartbeat_seconds <= 0 or ttl_seconds <= heartbeat_seconds:
            raise ValueError("progress TTL must exceed the positive heartbeat interval")
        self.ttl_seconds = ttl_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._client = None
        self._entries: dict[str, ProgressRegistration] = {}
        self._cleanup_tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        """返回当前部署是否启用 Redis 共享进度。"""
        return settings.checkpointer_type.strip().lower() == "redis"

    @staticmethod
    def _key(space_id: str, conversation_id: str) -> str:
        """生成与 checkpoint、取消总线互不混用的 Redis key。

        Args:
            space_id: 空间标识。
            conversation_id: 会话标识。

        Returns:
            带 Redis Cluster hash tag 的进度键。
        """
        identity = json.dumps([space_id, conversation_id], ensure_ascii=False).encode()
        digest = hashlib.sha256(identity).hexdigest()
        return f"deepsearch:progress:{{{digest}}}:active"

    def _redis(self):
        """获取异步 Redis 客户端，连接与命令最多等待 2 秒。

        Returns:
            Redis 或 RedisCluster 客户端。
        """
        if self._client is None:
            client_type = RedisCluster if settings.redis_cluster_mode else Redis
            self._client = client_type.from_url(
                settings.redis_url, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2,
            )
        return self._client

    def register(self, space_id: str, conversation_id: str) -> ProgressRegistration:
        """登记观察记录，不检查或改变任务能否执行。

        Args:
            space_id: 空间标识。
            conversation_id: 会话标识。

        Returns:
            可传入报告上下文的本轮记录。
        """
        task_key = f"{space_id}:{conversation_id}"
        previous = self._entries.get(task_key)
        registration = ProgressRegistration(
            task_key, self._key(space_id, conversation_id), uuid.uuid4().hex,
            ResearchProgress(conversation_id),
        )
        self._entries[task_key] = registration
        if previous is not None:
            self._schedule_close(previous)
        if self.enabled:
            registration.task = asyncio.create_task(
                self._heartbeat(registration), name="report_progress_heartbeat",
            )
        else:
            registration.ready.set()
        return registration

    async def prepare(self, registration: ProgressRegistration) -> None:
        """在实际执行前等待一次有界的共享轮次准备，存储故障不拒绝业务。

        Args:
            registration: 当前 HTTP 执行轮次的观察记录。
        """
        await registration.ready.wait()

    def _schedule_close(self, registration: ProgressRegistration) -> None:
        """异步清理观察任务，不取消报告执行。

        Args:
            registration: 要关闭的进度记录。
        """
        registration.progress.finish()
        task = asyncio.create_task(self.close(registration))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    def discard(self, task_key: str) -> None:
        """原取消或容量清理结束后移除进度。

        Args:
            task_key: 原 HTTP 字典使用的组合键。
        """
        registration = self._entries.get(task_key)
        if registration is not None:
            self._schedule_close(registration)

    def mark_cancelling(self, space_id: str, conversation_id: str) -> ProgressRegistration | None:
        """观察原取消请求并捕获本轮注册，不转发或执行取消。

        Args:
            space_id: 空间标识。
            conversation_id: 会话标识。

        Returns:
            取消发生时的注册对象；异步清理只关闭此对象，避免影响后续轮次。
        """
        registration = self._entries.get(f"{space_id}:{conversation_id}")
        if registration is not None:
            registration.progress.mark_cancelling()
        return registration

    async def _heartbeat(self, registration: ProgressRegistration) -> None:
        """每周期合并变化，未变化时只定期续期，不重传快照。

        Args:
            registration: 当前发布记录。
        """
        keys = registration.redis_keys
        try:
            # 分配只做一次且早于实际执行，不能在执行后的故障重试中领取更大的轮次。
            async with asyncio.timeout(2):
                lease = await self._redis().eval(
                    _ALLOCATE, 2, keys[3], keys[2], registration.token, self.ttl_seconds,
                )
            registration.epoch, registration.version = lease.split(":", 1)
            if not registration.epoch or not registration.version.isdecimal():
                raise ValueError("Invalid progress round")
        except (RedisError, OSError, ValueError, AttributeError):
            # 分配响应丢失时不能确认轮次，放弃本轮共享发布；迟到的分配只留下短期元数据。
            registration.closed = True
            logger.warning("Report progress round unavailable; execution continues without shared publishing")
            return
        finally:
            registration.ready.set()

        registered = False
        unavailable = False
        last_snapshot = None
        last_published = 0.0
        loop = asyncio.get_running_loop()
        renew_interval = min(5.0, self.ttl_seconds / 3)
        while not registration.closed:
            snapshot = registration.progress.snapshot()
            if snapshot is None:
                await self.close(registration)
                return
            try:
                changed = snapshot != last_snapshot
                if changed or not registered:
                    result = await self._redis().eval(
                        _REFRESH if registered else _REGISTER, 4, *keys,
                        registration.token, registration.epoch, registration.version, self.ttl_seconds,
                        json.dumps(asdict(snapshot), ensure_ascii=False),
                    )
                elif loop.time() - last_published >= renew_interval:
                    result = await self._redis().eval(
                        _RENEW, 4, *keys, registration.token,
                        registration.epoch, registration.version, self.ttl_seconds,
                    )
                else:
                    await asyncio.sleep(self.heartbeat_seconds)
                    continue
                if not result:
                    # 不重建被后续轮次覆盖或已过期的旧记录，不干预报告执行。
                    await self.close(registration, finish_progress=False)
                    return
                registered = True
                last_snapshot = snapshot
                last_published = loop.time()
                unavailable = False
            except (RedisError, OSError):
                if not unavailable:
                    logger.warning("Report progress publishing unavailable; execution continues")
                unavailable = True
            await asyncio.sleep(self.heartbeat_seconds)

    async def get_progress(self, space_id: str, conversation_id: str) -> dict | None:
        """读取进度，不续期、不创建 Agent、不读取 checkpoint。

        Args:
            space_id: 空间标识。
            conversation_id: 会话标识。

        Returns:
            当前进度与轻量汇总，无活跃记录时为 None。

        Raises:
            ProgressStorageUnavailable: Redis 不可用或共享记录损坏。
        """
        if not self.enabled:
            registration = self._entries.get(f"{space_id}:{conversation_id}")
            snapshot = registration.progress.snapshot() if registration is not None else None
            if snapshot is None:
                return None
            return ResearchProgressResponse.model_validate(asdict(snapshot)).model_dump()
        try:
            raw = await self._redis().hget(self._key(space_id, conversation_id), "snapshot")
            if raw is None:
                return None
            return ResearchProgressResponse.model_validate(json.loads(raw)).model_dump()
        except (RedisError, OSError, ValueError) as exc:
            raise ProgressStorageUnavailable("Report progress storage unavailable") from exc

    async def close(self, registration: ProgressRegistration | None, *, finish_progress: bool = True) -> None:
        """停止发布并只删除本轮记录，不触碰 checkpoint。

        Args:
            registration: 本轮记录；未启用观察时可为 None。
            finish_progress: 是否结束本地进度；仅共享记录失效时保留 SDK 观察。
        """
        if registration is None:
            return
        async with registration.lock:
            if finish_progress:
                registration.progress.finish()
                if self._entries.get(registration.task_key) is registration:
                    self._entries.pop(registration.task_key, None)
            if registration.closed:
                return
            registration.closed = True
            registration.ready.set()
            if registration.task is not None and registration.task is not asyncio.current_task():
                registration.task.cancel()
                await asyncio.gather(registration.task, return_exceptions=True)
            if registration.task is not None:
                try:
                    await self._redis().eval(_REMOVE, 4, *registration.redis_keys, registration.token)
                except (RedisError, OSError):
                    logger.warning("Report progress cleanup deferred to record expiry")

    async def shutdown(self) -> None:
        """关闭本进程观察任务与 Redis 连接。"""
        await asyncio.gather(*(self.close(item) for item in list(self._entries.values())))
        if self._cleanup_tasks:
            await asyncio.gather(*list(self._cleanup_tasks), return_exceptions=True)
        if self._client is not None:
            try:
                await self._client.aclose()
            except (RedisError, OSError):
                logger.warning("Could not close report progress connection")
            finally:
                self._client = None


def observe_http_progress(produce):
    """向 HTTP 输出任务提供进度上下文，不改变取消和交互逻辑。

    Args:
        produce: 原报告输出函数。

    Returns:
        原函数的异步包装方法。
    """
    @wraps(produce)
    async def observed(ctx):
        """执行原输出任务并处理本轮进度。

        Args:
            ctx: 包含可选进度记录的输出上下文。
        """
        registration = ctx.progress_registration
        if registration is None:
            return await produce(ctx)
        token = report_progress_context.set(registration.progress)
        try:
            return await produce(ctx)
        finally:
            try:
                snapshot = registration.progress.snapshot()
                keep_waiting = (
                    snapshot is not None and snapshot.status == "waiting_user_input"
                    and not ctx.cancel_event.is_set() and not ctx.resume_requested.is_set()
                )
                if not keep_waiting:
                    await report_progress_registry.close(registration)
            finally:
                report_progress_context.reset(token)

    return observed


report_progress_registry = ReportProgressRegistry()
