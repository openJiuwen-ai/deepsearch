import json
from unittest.mock import Mock, AsyncMock, patch

import pytest

from openjiuwen_deepsearch.algorithm.query_understanding.outliner import (
    Outliner,
    check_tool_call,
    create_outline_tool,
    normalize_sections,
)
from openjiuwen_deepsearch.algorithm.prompts.template import apply_system_prompt
from openjiuwen_deepsearch.common.exception import CustomValueException
from openjiuwen_deepsearch.framework.openjiuwen.agent.search_context import Outline, Section


async def _make_async_iter(chunks: list):
    """Helper function to create an async iterator from a list of chunks."""
    for chunk in chunks:
        yield chunk

# 定义测试数据
test_data = {
    'max_outline_retry_num': 2,
    'messages': [{'content': '中国汽车产业结构', 'name': '', 'role': 'user'}]
}

outline_response = Outline(
    language="zh-CN",
    title="中国汽车产业结构",
    thought="中国汽车产业结构分析",
    sections=[
        Section(
            id="1",
            title="1. 中国汽车产业概述",
            description="中国汽车产业概述",
            format_requirements=[],
            is_core_section=False,
            section_focus="market_size_and_growth",
            focus_dimensions=["industry_structure"],
        )
    ],
)

tool_name = create_outline_tool(1).card.name
tool_call_id = '123'
functioncall_response = {
    'content': '',
    'name': None,
    'raw_content': None,
    'reason_content': None,
    'role': 'assistant',
    'tool_calls': [
        {
            'args': {
                'language': 'zh-CN',
                'sections': [
                    {
                        'description': '中国汽车产业概述',
                        'title': '1. 中国汽车产业概述',
                        'format_requirements': [],
                        'is_core_section': False,
                        'section_focus': 'market_size_and_growth',
                        'focus_dimensions': ['industry_structure'],
                    },
                ],
                'thought': '中国汽车产业结构分析',
                'title': '中国汽车产业结构'
            },
            'id': tool_call_id,
            'name': tool_name,
            'type': 'tool_call'
        }
    ],
    'usage_metadata': None
}


# 测试用例
class TestOutliner:

    @pytest.fixture
    def mock_llm(self):
        return Mock()

    @pytest.fixture
    def setup_outliner(self, mock_llm):
        with patch('openjiuwen_deepsearch.algorithm.query_understanding.outliner.llm_context', return_value=mock_llm):
            outliner = Outliner("test", "outliner")
        return outliner

    @pytest.mark.asyncio
    async def test_generate_outline_success(self, setup_outliner, mock_llm):
        """测试成功生成大纲"""
        mock_llm_response = {
            'current_outline': outline_response,
            'success_flag': True,
            'error_msg': ''
        }

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.outliner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=functioncall_response
        ) as mock_ainvoke:
            result = await setup_outliner.generate_outline(test_data)

        assert result == mock_llm_response
        mock_ainvoke.assert_awaited_once()
        prompt = mock_ainvoke.await_args.args[1]
        rendered_prompt = "\n".join(message["content"] for message in prompt)
        assert "User-Specified Structure Preservation" in rendered_prompt
        assert "If an explicit structure exists, it is authoritative" in rendered_prompt
        assert "default planning aids" in rendered_prompt
        assert "do not create" in rendered_prompt
        assert "additional top-level sections" in rendered_prompt
        assert "do not add top-level sections to reach 4 dimensions" in rendered_prompt

    def test_normalize_sections_parses_json_string(self):
        args = {
            "language": "zh-CN",
            "title": "test",
            "thought": "thought",
            "sections": '[{"title": "A", "description": "desc"}]',
        }
        normalized = normalize_sections(args)
        assert isinstance(normalized["sections"], list)
        assert normalized["sections"][0]["title"] == "A"

    def test_normalize_sections_parses_markdown_wrapped_json(self):
        args = {
            "language": "zh-CN",
            "title": "test",
            "thought": "thought",
            "sections": '```json\n[{"title": "B", "description": "desc"}]\n```',
        }
        normalized = normalize_sections(args)
        assert normalized["sections"][0]["title"] == "B"

    def test_normalize_sections_parses_double_encoded_json(self):
        inner = json.dumps([{"title": "C", "description": "desc"}], ensure_ascii=False)
        args = {
            "language": "zh-CN",
            "title": "test",
            "thought": "thought",
            "sections": json.dumps(inner, ensure_ascii=False),
        }
        normalized = normalize_sections(args)
        assert normalized["sections"][0]["title"] == "C"

    def test_check_tool_call_sections_must_be_list(self):
        """check_tool_call 验证 sections """
        tool = create_outline_tool(1)
        tool_calls = [
            {
                'args': {
                    'language': 'zh-CN',
                    'sections': 'invalid-sections',
                    'thought': 'test thought',
                    'title': 'test title'
                },
                'name': tool.card.name,
            }
        ]

        with pytest.raises(CustomValueException, match='Sections is not a list'):
            check_tool_call(tool, tool_calls)

    @pytest.mark.parametrize(
        "missing_field",
        ["format_requirements", "section_focus", "focus_dimensions"],
    )
    def test_check_tool_call_requires_section_contract_fields(self, missing_field):
        tool = create_outline_tool(1)
        section = {
            "title": "test section",
            "description": "test description",
            "format_requirements": [],
            "section_focus": "section_specific_analysis",
            "focus_dimensions": ["overview"],
        }
        section.pop(missing_field)
        tool_calls = [
            {
                "args": {
                    "language": "zh-CN",
                    "sections": [section],
                    "thought": "test thought",
                    "title": "test title",
                },
                "name": tool.card.name,
            }
        ]

        with pytest.raises(CustomValueException, match=missing_field):
            check_tool_call(tool, tool_calls)

    def test_check_tool_call_allows_empty_format_requirements(self):
        tool = create_outline_tool(1)
        tool_calls = [
            {
                "args": {
                    "language": "zh-CN",
                    "sections": [
                        {
                            "title": "test section",
                            "description": "test description",
                            "format_requirements": [],
                            "section_focus": "section_specific_analysis",
                            "focus_dimensions": ["overview"],
                        }
                    ],
                    "thought": "test thought",
                    "title": "test title",
                },
                "name": tool.card.name,
            }
        ]

        check_tool_call(tool, tool_calls)

    def test_outline_tool_section_count_respects_explicit_user_structure(self):
        tool = create_outline_tool(5)
        sections_description = tool.card.input_params["properties"]["sections"]["description"]

        assert "Target count: 5" in sections_description
        assert "explicitly defines top-level parts" in sections_description
        assert "lettered parts" in sections_description
        assert "divided into N major parts" in sections_description
        assert "titled numbered tasks" in sections_description
        assert "one sibling object" in sections_description
        assert "sub-requirements inside the relevant description" in sections_description
        assert "source-use restrictions inside format_requirements" in sections_description
        assert "under a named/lettered major part are subordinate" in sections_description
        assert "Do not add introduction" in sections_description
        assert "Never serialize section objects" in sections_description
        assert "Generate exactly 5" not in sections_description
        assert "must be exactly 5" not in sections_description

        title_description = tool.card.input_params["properties"]["sections"]["items"]["properties"]["title"]["description"]
        assert "use only that title" in title_description
        assert "Relationship Analysis" in title_description
        assert "Do not append" in title_description

        description_description = tool.card.input_params["properties"]["sections"]["items"]["properties"]["description"]["description"]
        assert "Concise plain-prose research requirements" in description_description
        assert "format_requirements instead of copying them here" in description_description
        assert "Do not include serialized section objects" in description_description
        assert "'\"title\":'" in description_description

        format_requirements = tool.card.input_params["properties"]["sections"]["items"]["properties"]["format_requirements"]
        assert format_requirements["type"] == "array"
        assert "Output format requirements" in format_requirements["description"]

        required_items = tool.card.input_params["properties"]["sections"]["items"]["required"]
        assert "format_requirements" in required_items
        assert "section_focus" in required_items
        assert "focus_dimensions" in required_items

    def test_outliner_template_prompt_requires_section_contract_fields(self):
        prompts = apply_system_prompt(
            "outliner_template",
            {
                "entry_search_results": [],
                "report_template": "# Market Overview\n> Function: explain the market\n> is_core_section: false",
                "questions": "Analyze AI infrastructure vendors",
                "user_feedback": "",
                "language": "en-US",
            },
        )
        rendered_prompt = "\n".join(message["content"] for message in prompts)

        assert "Required Section Contract Fields" in rendered_prompt
        assert "`format_requirements`" in rendered_prompt
        assert "`section_focus`" in rendered_prompt
        assert "`focus_dimensions`" in rendered_prompt
        assert '"format_requirements": []' in rendered_prompt
        assert '"section_focus": "section_specific_analysis"' in rendered_prompt
        assert '"focus_dimensions":' in rendered_prompt

    @pytest.mark.asyncio
    async def test_generate_outline_failure(self, setup_outliner, mock_llm):
        """测试生成大纲失败"""
        mock_llm_response = {
            'current_outline': {},
            'success_flag': False,
            'error_msg': '[211800]Error when Outliner generate an outline: TestMessage'
        }

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.outliner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                side_effect=Exception("TestMessage")
        ):
            result = await setup_outliner.generate_outline(test_data)

        assert result == mock_llm_response

    @pytest.mark.asyncio
    async def test_generate_outline_with_runtime_api_tool(self, setup_outliner, mock_llm):
        """测试 outliner 场景会合并并执行运行时 API 工具"""
        custom_input = {
            **test_data,
            "api_tools_config": {
                "query_understanding_tools": [
                    {
                        "tool_id": "tool-1",
                        "name": "runtime_outline_tool",
                        "description": "Runtime outline tool",
                        "path": "https://example.com/outline",
                        "http_method": "post",
                        "request_params": [
                            {
                                "name": "title",
                                "description": "outline title",
                                "send_method": "body",
                                "required": True,
                            },
                            {
                                "name": "language",
                                "description": "language",
                                "send_method": "body",
                                "required": False,
                            }
                        ],
                    }
                ]
            }
        }
        custom_response = {
            **functioncall_response,
            'tool_calls': [
                {
                    'args': {
                        'language': 'zh-CN',
                        'title': '运行时大纲'
                    },
                    'id': tool_call_id,
                    'name': 'runtime_outline_tool',
                    'type': 'tool_call'
                }
            ],
        }
        mock_http_response = Mock()
        mock_http_response.headers = {}
        mock_http_response.encoding = "utf-8"
        mock_http_response.raise_for_status = Mock()
        json_data = json.dumps({
            "code": 0,
            "message": "ok",
            "data": {
                "language": "zh-CN",
                "title": "运行时大纲",
                "thought": "Generated by runtime api",
                "sections": [],
            }
        }).encode("utf-8")
        mock_http_response.aiter_bytes = Mock(return_value=_make_async_iter([json_data]))
        
        mock_stream_cm = Mock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_http_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)
        
        mock_client = Mock()
        mock_client.stream = Mock(return_value=mock_stream_cm)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
                'openjiuwen_deepsearch.algorithm.query_understanding.outliner.ainvoke_llm_with_stats',
                new_callable=AsyncMock,
                return_value=custom_response
        ) as mock_invoke, patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.validate_runtime_request_url',
                return_value=None
        ), patch(
                'openjiuwen_deepsearch.framework.openjiuwen.tools.runtime_api.runtime_api.httpx.AsyncClient',
                return_value=mock_client
        ):
            result = await setup_outliner.generate_outline(custom_input)

        tools = mock_invoke.await_args.kwargs["tools"]
        tool_names = [
            getattr(tool, "name", tool["name"] if isinstance(tool, dict) else None)
            for tool in tools
        ]
        assert result["success_flag"] is True
        assert result["current_outline"].title == "运行时大纲"
        assert tool_name in tool_names
        assert "runtime_outline_tool" in tool_names

