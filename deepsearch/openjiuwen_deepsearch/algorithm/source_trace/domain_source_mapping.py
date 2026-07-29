# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
域名→来源映射模块

职责：
- 维护域名（registered_domain）到来源名称的映射关系
- 模块级 dict 作为运行时缓存 + JSON 文件持久化
- 纯内存 + 种子映射 + JSON 持久化实现
- 分层数据源：种子 JSON（最高优先级） > 动态 JSON
- 种子数据维护在包内 seed_mappings.json（只读），纳入版本管理
- 动态映射仅存储在平台可写数据目录，首次部署时为空，运行时积累

数据持久化位置：
- 种子：包内 seed_mappings.json（只读，随版本发布）
- 动态：可写数据目录下的 domain_source_map.json
  - Windows: %LOCALAPPDATA%/deepsearch/domain_source_map.json
  - macOS/Linux: ~/.local/share/deepsearch/domain_source_map.json
  - 绝对路径配置时直接使用配置值

使用方式：
1. lookup_source(domain) -> (source_name, was_cached)
2. save_mapping(registered_domain, source)
"""

import os
import platform
import asyncio
import ipaddress
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

import tldextract

from openjiuwen_deepsearch.utils.common_utils.url_utils import (
    extract_domain_from_url,
    normalize_domain,
)

logger = logging.getLogger(__name__)


# ─── 运行时状态 ───

# 映射缓存
_domain_source_map: dict[str, str] = {}
# 负缓存：已知 miss 的域名，避免重复查询
_miss_cache: set[str] = set()
# 初始化锁（仅用于初始化和写操作，读操作不受此锁阻塞）
_init_lock = asyncio.Lock()
_initialized = False


# ─── 种子数据追踪 ───

# 种子数据已从 Python 硬编码剥离至包内 seed_mappings.json，
# 通过 _load_seed_json() 加载并赋予最高优先级。
# _seed_keys 用于追踪哪些 key 来自种子文件，保存动态 JSON 时排除种子条目。
_seed_keys: set[str] = set()


# ─── 公共 API ───


def _get_seed_json_path() -> Path:
    """获取包内种子 JSON 文件路径（使用包资源 API，不依赖工作目录）。

    种子数据存储在包内 seed_mappings.json（只读），与动态映射文件分离。
    动态映射写入使用 _get_mapping_json_path() 返回的可写路径。
    """
    try:
        import importlib.resources as pkg_resources
        # Python 3.9+：使用 files() API 获取包内资源路径
        ref = pkg_resources.files("openjiuwen_deepsearch.algorithm.source_trace")
        seed_path = ref.joinpath("seed_mappings.json")
        # 取真实路径（支持从 .whl 安装后读取）
        return Path(str(seed_path))
    except (ImportError, TypeError, FileNotFoundError):
        # 降级：直接基于包路径查找（仅适用于源码安装模式）
        package_dir = Path(__file__).resolve().parent
        seed_json = package_dir / "seed_mappings.json"
        return seed_json


def _get_mapping_json_path() -> Path:
    """获取动态映射 JSON 文件路径（用于读写）。

    优先级：
    1. 如果配置了绝对路径，直接使用（适用于生产部署）
    2. 否则使用用户可写数据目录下的映射文件
       - Windows: %APPDATA%/deepsearch/
       - POSIX: $XDG_DATA_HOME/deepsearch/ 或 ~/.local/share/deepsearch/
    3. 降级：使用包目录（仅适用于源码开发模式）
    """
    from openjiuwen_deepsearch.config.config import Config
    config = Config()
    map_path = config.service_config.source_tracer_domain_source_map_path

    # 绝对路径：直接使用
    resolved = Path(map_path)
    if resolved.is_absolute():
        return resolved

    # 相对路径：解析到可写数据目录，避免写入安装目录（可能只读）
    data_dir = _get_writable_data_dir()
    return data_dir / map_path


def _get_writable_data_dir() -> Path:
    """获取平台相关的可写数据目录。

    - Windows: %LOCALAPPDATA%/deepsearch
    - macOS/Linux: ~/.local/share/deepsearch 或 $XDG_DATA_HOME/deepsearch
    - 降级：当前工作目录下的 .deepsearch_data/
    """

    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        data_dir = Path(base) / "deepsearch"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "")
        if xdg:
            data_dir = Path(xdg) / "deepsearch"
        else:
            data_dir = Path(os.path.expanduser("~")) / ".local" / "share" / "deepsearch"

    # 确保 data_dir 是可写的，否则降级到工作目录
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".write_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return data_dir
    except OSError:
        # 降级到工作目录下的子目录
        fallback = Path.cwd() / ".deepsearch_data"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "Data directory %s is not writable, falling back to %s",
            data_dir, fallback,
        )
        return fallback


def _extract_registered_domain(domain: str) -> str:
    """
    使用 tldextract 从域名中提取 registered domain。

    例如: zhuanlan.zhihu.com → zhihu.com
          news.example.co.uk → example.co.uk
    """
    if not domain:
        return ""
    extracted = tldextract.extract(domain)
    if extracted.domain and extracted.suffix:
        return f"{extracted.domain}.{extracted.suffix}"
    return domain


def _is_ip_address(domain: str) -> bool:
    """检查是否为 IP 地址。"""
    if not domain:
        return False
    try:
        ipaddress.ip_address(domain)
        return True
    except ValueError:
        return False


def _load_seed_json() -> dict[str, str]:
    """从包内种子 JSON 文件加载映射表（使用包资源API，不依赖工作目录）。

    种子文件是只读的权威数据源，享有最高合并优先级（不可被动态 JSON 覆盖）。
    即使从 wheel 安装也能正确读取。损坏或不存在返回空 dict。
    """
    seed_path = _get_seed_json_path()
    try:
        if not seed_path.exists():
            logger.warning("Seed JSON file not found at %s. Seed mappings unavailable.", seed_path)
            return {}
        with open(seed_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if k and v}
        logger.warning("Seed JSON file is not a dict: %s", seed_path)
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load seed JSON file %s: %s.", seed_path, e)
        return {}


def _load_local_json() -> dict[str, str]:
    """从动态映射 JSON 文件加载映射表。损坏或不存在返回空 dict。"""
    json_path = _get_mapping_json_path()
    if not json_path.exists():
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if k and v}
        logger.warning("Mapping JSON file is not a dict: %s", json_path)
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load mapping JSON file %s: %s. Starting with empty dict.", json_path, e)
        return {}


def _save_local_json_atomic(data: dict[str, str]) -> None:
    """
    将映射表原子写入本地 JSON 文件。

    使用 tempfile + os.replace 模式，避免写入中途进程退出导致截断/非法 JSON。

    注意：此方法仅保证单次文件替换的原子性，不协调多进程的读-修改-写操作。
    """
    json_path = _get_mapping_json_path()
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        # 写入临时文件
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            prefix="domain_source_map_",
            dir=str(json_path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 原子替换目标文件
            os.replace(tmp_path, str(json_path))
        except BaseException:
            # 清理临时文件（替换失败时）
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning("Failed to save mapping JSON file %s: %s", json_path, e)


async def init_domain_source_mapping() -> None:
    """
    初始化映射表。启动加载流程：
    1. 动态 JSON（中优先级）
    2. 种子 JSON（最高优先级，覆盖动态 JSON）

    最终合并到 _domain_source_map。
    """
    global _domain_source_map, _initialized, _seed_keys

    async with _init_lock:
        if _initialized:
            return

        # 1. 中优先级：动态映射 JSON（可写数据目录）
        dynamic_json_data = _load_local_json()

        # 2. 最高优先级：种子 JSON（包内只读，不可被覆盖）
        seed_json_data = _load_seed_json()

        # 合并：动态 JSON → 种子 JSON（种子最高优先级）
        merged: dict[str, str] = {}
        merged.update(dynamic_json_data)
        merged.update(seed_json_data)        # 最高优先级，种子不可覆盖

        _domain_source_map = merged
        _seed_keys = set(seed_json_data.keys())
        _initialized = True

        logger.info(
            "Domain source mapping initialized: seed=%d, dynamic=%d, total=%d",
            len(seed_json_data),
            len(dynamic_json_data),
            len(_domain_source_map),
        )


async def lookup_source(domain: str) -> tuple[str, bool]:
    """
    查询域名对应的来源名称。

    Args:
        domain: 域名字符串，可以是完整 netloc（如 zhuanlan.zhihu.com）
                或已经是 registered domain（如 zhihu.com）

    Returns:
        (source_name, was_cached):
        - 命中本地缓存：(source_name, True)
        - 全部 miss：(normalized_domain, False)，同时记录负缓存避免重复查询
        - 空域名：("", False)
        - IP 地址：(ip_string, False)
    """
    if not domain:
        return "", False

    domain = domain.strip().lower()
    if not domain:
        return "", False

    # 预处理：如果传入的是 URL 而非域名，先提取域名
    domain = extract_domain_from_url(domain) or domain

    # IP 地址不进入映射表
    if _is_ip_address(domain):
        return domain, False

    registered_domain = _extract_registered_domain(domain)
    if not registered_domain:
        return normalize_domain(domain), False

    # 提取后的 registered domain 也可能是 IP
    if _is_ip_address(registered_domain):
        return registered_domain, False

    # 首次确保已初始化
    if not _initialized:
        async with _init_lock:
            if not _initialized:
                await _init_inner()

    # 本地 dict 查询（无锁，读操作不阻塞其他读）
    if registered_domain in _domain_source_map:
        logger.debug(
            "[DOMAIN MAPPING]: lookup_source hit local - domain=%s, source=%s",
            registered_domain, _domain_source_map[registered_domain],
        )
        return _domain_source_map[registered_domain], True

    # 负缓存检查：已知 miss 的域名不再查询
    if registered_domain in _miss_cache:
        normalized = normalize_domain(registered_domain)
        logger.debug(
            "[DOMAIN MAPPING]: lookup_source negative cache - domain=%s", registered_domain,
        )
        return normalized, False

    # miss → 记录负缓存，返回规范化后的域名
    _miss_cache.add(registered_domain)
    normalized = normalize_domain(registered_domain)
    logger.debug(
        "[DOMAIN MAPPING]: lookup_source miss - domain=%s, fallback=%s",
        registered_domain, normalized,
    )
    return normalized, False


async def _init_inner() -> None:
    """在 _init_lock 内部执行初始化，避免重复初始化。"""
    global _domain_source_map, _initialized, _seed_keys
    if _initialized:
        return

    dynamic_json_data = _load_local_json()
    seed_json_data = _load_seed_json()

    merged: dict[str, str] = {}
    merged.update(dynamic_json_data)
    merged.update(seed_json_data)

    _domain_source_map = merged
    _seed_keys = set(seed_json_data.keys())
    _initialized = True


async def save_mapping(registered_domain: str, source: str) -> None:
    """
    保存域名→来源映射。

    内部会统一调用 _extract_registered_domain 转换 key，确保与 lookup_source 的查询 key 一致。
    例如：传入 news.example.co.uk 会被转为 example.co.uk 再保存。

    Args:
        registered_domain: 可以是完整域名（如 news.example.co.uk）或已提取的 registered domain（如 zhihu.com）
        source: 来源名称
    """
    if not registered_domain or not source:
        return

    registered_domain = registered_domain.strip().lower()
    source = source.strip()
    if not registered_domain or not source:
        return

    # IP 地址不保存
    if _is_ip_address(registered_domain):
        return

    # 统一转换为 registered domain，确保保存 key 与 lookup_source 的查询 key 一致
    registered_domain = _extract_registered_domain(registered_domain)
    if not registered_domain:
        return

    # 转换后也可能是 IP
    if _is_ip_address(registered_domain):
        return

    # 确保已初始化
    if not _initialized:
        async with _init_lock:
            if not _initialized:
                await _init_inner()

    # 幂等检查：本地已有且相同，跳过
    existing = _domain_source_map.get(registered_domain)
    if existing == source:
        return

    # 清除负缓存（该域名现在有映射了）
    _miss_cache.discard(registered_domain)

    # 1. 写入本地 dict
    _domain_source_map[registered_domain] = source

    # 2. 本地 JSON 持久化：仅保存动态映射（排除种子条目）
    _save_local_json_atomic(_get_dynamic_mappings())


def reset_for_testing() -> None:
    """重置模块状态，仅用于测试。"""
    global _domain_source_map, _initialized, _miss_cache, _seed_keys
    _domain_source_map = {}
    _initialized = False
    _miss_cache = set()
    _seed_keys = set()


def _get_dynamic_mappings() -> dict[str, str]:
    """返回运行时动态映射（排除种子数据），用于 JSON 持久化。

    种子数据来自包内 seed_mappings.json，无需写入动态映射文件，
    避免每次保存都冗余写入不变的种子条目。
    """
    return {k: v for k, v in _domain_source_map.items() if k not in _seed_keys}


def get_seed_data() -> dict[str, str]:
    """返回种子数据的只读副本（从包内 seed_mappings.json 加载）。"""
    return _load_seed_json()


def get_current_map() -> dict[str, str]:
    """返回当前映射表的只读副本。"""
    return dict(_domain_source_map)


def get_seed_count() -> int:
    """返回种子数据条目数（从包内 seed_mappings.json 加载）。"""
    return len(_load_seed_json())
