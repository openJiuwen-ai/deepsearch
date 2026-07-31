# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""泛型运行注册表：per-run 活对象注入。

纪律：workflow session state 只携带可序列化的 run_id 字符串；含锁/连接的
活对象（客户端、记忆等）经本注册表在节点内取回，运行结束 finally 注销。
避免大负载与不可复制对象进入会被框架复制的 workflow state。
"""

import threading
import uuid
from contextlib import contextmanager
from typing import Generic, Iterator, TypeVar

T = TypeVar("T")


class RunRegistry(Generic[T]):
    """线程安全（base 为通用包，不假设消费方只在单事件循环内使用）。"""

    def __init__(self) -> None:
        self._runs: dict[str, T] = {}
        self._lock = threading.Lock()

    def register(self, ctx: T) -> str:
        run_id = uuid.uuid4().hex
        with self._lock:
            self._runs[run_id] = ctx
        return run_id

    def get(self, run_id: str) -> T:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(f"Unknown or expired run_id: {run_id}")
            return self._runs[run_id]

    def unregister(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    @contextmanager
    def session(self, ctx: T) -> Iterator[str]:
        """结构化注册：`with registry.session(ctx) as run_id:`——
        把"必须 finally 注销"的调用方纪律变成结构，长驻服务防泄漏。"""
        run_id = self.register(ctx)
        try:
            yield run_id
        finally:
            self.unregister(run_id)
