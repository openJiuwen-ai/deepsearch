# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Python 切块器：stdlib `ast` 实现。

与旧 tree-sitter 版本的行为对齐：按命名定义（函数/类）切块、嵌套定义各自成块
（允许区间重叠）、收集定义体内全部函数调用名、无定义时整文件一块。
kind 命名沿用 tree-sitter 节点类型（function_definition / class_definition），
保持索引 schema 兼容。选择 stdlib ast 的原因：零依赖、可测试；
tree-sitter 实现可作为 Chunker 协议的并行实现按需引入（多语言时）。
"""

import ast
import logging

from openjiuwen_codesearch.indexing.chunkers.base import Chunk

logger = logging.getLogger(__name__)

_KIND_MAP = {
    ast.FunctionDef: "function_definition",
    ast.AsyncFunctionDef: "function_definition",
    ast.ClassDef: "class_definition",
}


def _collect_calls(node: ast.AST) -> list[str]:
    calls = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            try:
                called = ast.unparse(sub.func).strip()
            except Exception:  # noqa: BLE001  个别奇异节点不阻塞切块
                continue
            if called:
                calls.append(called)
    return calls


class PythonAstChunker:
    def chunk_source(self, source: str) -> list[Chunk]:
        lines = source.splitlines()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # 语法坏文件退化为整文件一块（与旧实现的解析失败路径一致）
            return self._whole_file_chunk(source, lines)

        chunks: list[Chunk] = []
        for node in ast.walk(tree):
            kind = _KIND_MAP.get(type(node))
            if kind is None:
                continue
            name = getattr(node, "name", "")
            if not name:
                continue
            start_line = node.lineno
            end_line = node.end_lineno or node.lineno
            chunk_text = "\n".join(lines[start_line - 1 : end_line])
            if not chunk_text.strip():
                continue
            chunks.append(
                Chunk(
                    text=chunk_text,
                    start_line=start_line,
                    end_line=end_line,
                    kind=kind,
                    name=name,
                    calls=_collect_calls(node),
                )
            )

        if not chunks:
            return self._whole_file_chunk(source, lines)
        chunks.sort(key=lambda c: (c.start_line, c.end_line))
        return chunks

    def chunk_file(self, file_path: str) -> list[Chunk]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except OSError as e:
            logger.warning("Failed to read %s: %s", file_path, e)
            return []
        return self.chunk_source(source)

    @staticmethod
    def _whole_file_chunk(source: str, lines: list[str]) -> list[Chunk]:
        if not source.strip():
            return []
        return [Chunk(text=source, start_line=1, end_line=max(1, len(lines)))]
