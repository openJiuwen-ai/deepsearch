# -*- coding: UTF-8 -*-
"""
域名→来源映射模块单元测试

覆盖范围:
- lookup_source: hit / miss / empty / ip / subdomain
- save_mapping: 本地 dict + JSON 持久化
- corrupted_json / missing_json 错误恢复
- asyncio.Lock 并发安全
- 种子数据 seed_mappings.json 完整性
- 动态 JSON 保存不含种子冗余
"""

import asyncio
import json
from unittest.mock import patch

import pytest


MODULE_PATH = "openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_module_state():
    """每个测试前后重置模块全局状态。"""
    import openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping as mod
    mod.reset_for_testing()
    yield
    mod.reset_for_testing()


@pytest.fixture
def json_file(tmp_path):
    """将 _get_mapping_json_path 指向 tmp_path/domain_source_map.json。"""
    p = tmp_path / "domain_source_map.json"
    with patch(f"{MODULE_PATH}._get_mapping_json_path", return_value=p):
        yield p


# ──────────────────────────────────────────────────────────────────────────────
# Original Tests (非分布式)
# ──────────────────────────────────────────────────────────────────────────────


class TestLookupSource:
    """lookup_source 基础功能。"""

    @pytest.mark.asyncio
    async def test_lookup_source_hit(self, json_file):
        """已知种子域名命中 → (source_name, True)。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            lookup_source, init_domain_source_mapping,
        )
        await init_domain_source_mapping()
        source, cached = await lookup_source("zhihu.com")
        assert source == "知乎"
        assert cached is True

    @pytest.mark.asyncio
    async def test_lookup_source_miss(self, json_file):
        """未知域名 → (normalized_domain, False)。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            lookup_source, init_domain_source_mapping,
        )
        await init_domain_source_mapping()
        source, cached = await lookup_source("completely-unknown-xyz-99999.com")
        assert source == "completely-unknown-xyz-99999.com"
        assert cached is False

    @pytest.mark.asyncio
    async def test_lookup_source_empty(self):
        """空字符串 → ("", False)。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            lookup_source,
        )
        source, cached = await lookup_source("")
        assert source == ""
        assert cached is False

    @pytest.mark.asyncio
    async def test_lookup_source_ip_address(self):
        """IP 地址 → (ip_string, False)。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            lookup_source,
        )
        source, cached = await lookup_source("192.168.1.1")
        assert source == "192.168.1.1"
        assert cached is False

    @pytest.mark.asyncio
    async def test_lookup_source_subdomain_to_registered(self, json_file):
        """子域名 zhuanlan.zhihu.com → registered domain zhihu.com → '知乎'。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            lookup_source, init_domain_source_mapping,
        )
        await init_domain_source_mapping()
        source, cached = await lookup_source("zhuanlan.zhihu.com")
        assert source == "知乎"
        assert cached is True


class TestSaveAndLoad:
    """save_mapping + lookup_source 往返测试。"""

    @pytest.mark.asyncio
    async def test_save_and_load(self, json_file):
        """保存新域名后 lookup 能命中。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            save_mapping, lookup_source, init_domain_source_mapping,
        )
        await init_domain_source_mapping()
        await save_mapping("newsite-roundtrip.com", "新网站往返测试")
        source, cached = await lookup_source("newsite-roundtrip.com")
        assert source == "新网站往返测试"
        assert cached is True


class TestJsonPersistence:
    """JSON 文件异常处理。"""

    @pytest.mark.asyncio
    async def test_corrupted_json(self, json_file):
        """JSON 文件内容损坏 → init 不崩溃，种子数据正常加载。"""
        json_file.write_text("{invalid json content !!!", encoding="utf-8")
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            init_domain_source_mapping, get_current_map,
        )
        await init_domain_source_mapping()
        current = get_current_map()
        assert "zhihu.com" in current
        assert current["zhihu.com"] == "知乎"

    @pytest.mark.asyncio
    async def test_missing_json(self, json_file):
        """JSON 文件不存在 → init 正常工作。"""
        assert not json_file.exists()
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            init_domain_source_mapping, get_current_map,
        )
        await init_domain_source_mapping()
        current = get_current_map()
        assert "zhihu.com" in current


class TestConcurrency:
    """并发安全。"""

    @pytest.mark.asyncio
    async def test_concurrent_write(self, json_file):
        """20 个 save_mapping 并发调用 → 全部写入 dict 且一致。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            save_mapping, init_domain_source_mapping, get_current_map,
        )
        await init_domain_source_mapping()
        domains = [(f"concurrent{i}.com", f"并发站点{i}") for i in range(20)]
        await asyncio.gather(*[save_mapping(d, s) for d, s in domains])
        current = get_current_map()
        for domain, expected_source in domains:
            assert current.get(domain) == expected_source


class TestSeedData:
    """种子数据 seed_mappings.json 完整性。"""

    def test_seed_data_coverage(self):
        """seed_mappings.json 至少包含 50 条。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            get_seed_count,
        )
        assert get_seed_count() >= 50

    def test_seed_data_known_domains(self):
        """验证特定已知域名→来源映射正确。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            get_seed_data,
        )
        seed = get_seed_data()
        assert seed["zhihu.com"] == "知乎"
        assert seed["weibo.com"] == "微博"
        assert seed["reuters.com"] == "Reuters"
        assert seed["bilibili.com"] == "哔哩哔哩"
        assert seed["cnn.com"] == "CNN"
        # 从动态映射并入种子的条目
        assert seed["21jingji.com"] == "21财经"
        assert seed["bjnews.com.cn"] == "新京报"
        assert seed["sina.cn"] == "新浪财经"


class TestDynamicJsonExcludesSeed:
    """动态 JSON 保存不含种子冗余条目。"""

    @pytest.mark.asyncio
    async def test_save_excludes_seed_entries(self, json_file):
        """save_mapping 写入的动态 JSON 不包含种子条目。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            save_mapping, init_domain_source_mapping,
        )

        await init_domain_source_mapping()
        # 保存一个非种子域名
        await save_mapping("dynamic-only.com", "动态站点")

        # JSON 文件写入
        assert json_file.exists()
        json_data = json.loads(json_file.read_text(encoding="utf-8"))

        # 非种子域名应存在
        assert json_data["dynamic-only.com"] == "动态站点"
        # 种子域名不应在动态 JSON 中
        assert "zhihu.com" not in json_data
        assert "reuters.com" not in json_data

    @pytest.mark.asyncio
    async def test_seed_override_not_saved_to_dynamic_json(self, json_file):
        """种子域名的映射不会被写入动态 JSON（即使运行时重复保存种子域名）。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            save_mapping, init_domain_source_mapping,
        )

        await init_domain_source_mapping()
        # 尝试保存一个与种子同名的域名（会被幂等检查跳过，因为值相同）
        await save_mapping("zhihu.com", "知乎")

        # JSON 文件不应存在（幂等跳过，不写 JSON）
        assert not json_file.exists()

    @pytest.mark.asyncio
    async def test_dynamic_mappings_helper(self, json_file):
        """_get_dynamic_mappings() 仅返回非种子条目。"""
        from openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping import (
            init_domain_source_mapping, save_mapping,
        )
        import openjiuwen_deepsearch.algorithm.source_trace.domain_source_mapping as mod

        await init_domain_source_mapping()
        await save_mapping("runtime-entry.com", "运行时站点")

        dynamic = mod._get_dynamic_mappings()
        assert "runtime-entry.com" in dynamic
        assert "zhihu.com" not in dynamic
