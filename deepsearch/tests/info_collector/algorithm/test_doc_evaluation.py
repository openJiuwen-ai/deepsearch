import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
from openjiuwen_deepsearch.algorithm.research_collector.doc_evaluation import \
    run_doc_evaluation, parse_evaluator_output, process_scored_item, \
    extract_scores, ensure_document_index_field, validate_document_index, \
    log_content_and_scores, info_evaluator, invoke_llm_with_retry, build_evaluator_messages

MODULE_PATH = "openjiuwen_deepsearch.algorithm.research_collector.doc_evaluation"


def test_build_evaluator_messages_uses_compact_fields_only():
    documents = [
        {
            "source_id": "web_1",
            "title": "Alpha",
            "url": "https://example.com/a",
            "snippet": "不应进入评估 prompt 的 snippet",
            "summary": "不应进入评估 prompt 的 summary",
            "key_passages": ["关键片段"],
            "original_content": "不应进入评估 prompt 的长正文",
        }
    ]

    messages = build_evaluator_messages(documents)
    rendered = str(messages)

    assert "Alpha" in rendered
    assert "关键片段" in rendered
    assert "不应进入评估 prompt 的 snippet" not in rendered
    assert "不应进入评估 prompt 的 summary" not in rendered
    assert "不应进入评估 prompt 的长正文" not in rendered


@pytest.mark.asyncio
async def test_run_doc_evaluation_accepts_compact_documents(mocker):
    from openjiuwen_deepsearch.algorithm.research_collector import doc_evaluation

    mocker.patch.object(
        doc_evaluation,
        "info_evaluator",
        return_value=(
            '[{"document_index": 0, "doc_time": "2025 5月", '
            '"scores": {"relevance": 8}}]'
        ),
    )

    result = await doc_evaluation.run_doc_evaluation(
        query="Alpha",
        documents=[{"source_id": "web_1", "title": "Alpha", "key_passages": ["关键片段"]}],
        llm=object(),
    )

    assert result[0]["document_index"] == 0
    assert result[0]["doc_time"] == "2025 5月"


@pytest.mark.asyncio
async def test_run_doc_evaluation_rejects_legacy_contents_argument(mocker):
    from openjiuwen_deepsearch.algorithm.research_collector import doc_evaluation

    mocker.patch.object(doc_evaluation, "info_evaluator", return_value="[]")

    with pytest.raises(TypeError):
        await doc_evaluation.run_doc_evaluation(
            query="Alpha",
            contents=["旧全文"],
            llm=object(),
        )

    with pytest.raises(TypeError):
        await doc_evaluation.run_doc_evaluation("Alpha", ["旧全文"], object())


class TestRunDocEvaluation:
    """测试 run_doc_evaluation 函数"""
    def setup_method(self):
        self.sample_query = "test query"
        self.sample_documents = [
            {"source_id": "0", "title": "Doc 0", "url": "", "key_passages": ["content 1"]},
            {"source_id": "1", "title": "Doc 1", "url": "", "key_passages": ["content 2"]},
            {"source_id": "2", "title": "Doc 2", "url": "", "key_passages": ["content 3"]},
        ]
        self.sample_llm = None

    @pytest.mark.asyncio
    async def test_run_doc_evaluation_success(self):
        """测试成功的文档评估流程"""
        mock_scored_result_str = '[{"document_index": 0, "scores": {"relevance": 0.9}, "doc_time": "2023-01-01"}]'
        expected_output = [{"document_index": 0, "scores": {"relevance": 0.9}, "doc_time": "2023-01-01"}]

        # 直接mock函数，不通过模块路径
        with patch(f"{MODULE_PATH}.info_evaluator", new_callable=AsyncMock) as mock_evaluator, \
                patch(f"{MODULE_PATH}.parse_evaluator_output") as mock_parse, \
                patch(f"{MODULE_PATH}.process_scored_item") as mock_process, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_evaluator.return_value = mock_scored_result_str
            mock_parse.return_value = expected_output
            mock_process.return_value = expected_output[0]

            result = await run_doc_evaluation(self.sample_query, self.sample_documents, self.sample_llm)

            mock_evaluator.assert_called_once_with(self.sample_query, self.sample_documents, self.sample_llm)
            mock_parse.assert_called_once_with(mock_scored_result_str)
            mock_process.assert_called_once_with(expected_output[0], 0, self.sample_documents)
            assert result == expected_output
            mock_logger.info.assert_any_call("[POST PROCESSING] Start content evaluation.")
            mock_logger.info.assert_any_call("[POST PROCESSING] Process finish.")

    @pytest.mark.asyncio
    async def test_run_doc_evaluation_empty_documents(self):
        """测试空 compact documents 列表"""
        with patch(f"{MODULE_PATH}.info_evaluator", new_callable=AsyncMock) as mock_evaluator, \
                patch(f"{MODULE_PATH}.parse_evaluator_output") as mock_parse, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_evaluator.return_value = "[]"
            mock_parse.return_value = []

            result = await run_doc_evaluation(self.sample_query, [], llm=None)

            assert result == []
            mock_evaluator.assert_called_once_with(self.sample_query, [], None)
            mock_logger.info.assert_any_call("[POST PROCESSING] Start content evaluation.")
            mock_logger.info.assert_any_call("[POST PROCESSING] Process finish.")

    @pytest.mark.asyncio
    async def test_run_doc_evaluation_parse_returns_non_list(self):
        """测试解析结果不是列表的情况"""
        with patch(f"{MODULE_PATH}.info_evaluator", new_callable=AsyncMock) as mock_evaluator, \
                patch(f"{MODULE_PATH}.parse_evaluator_output") as mock_parse, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_evaluator.return_value = "invalid"
            mock_parse.return_value = "not a list"  # 返回非列表

            result = await run_doc_evaluation(self.sample_query, self.sample_documents, llm=None)

            assert result == []
            mock_evaluator.assert_called_once_with(self.sample_query, self.sample_documents, None)
            mock_logger.info.assert_any_call("[POST PROCESSING] Start content evaluation.")
            mock_logger.info.assert_any_call("[POST PROCESSING] Process finish.")

    @pytest.mark.asyncio
    async def test_run_doc_evaluation_process_scored_item_returns_none(self):
        """测试处理评分项返回None的情况"""
        scored_items = [{"document_index": 0, "scores": {}}, {"document_index": 1, "score": {}}]

        with patch(f"{MODULE_PATH}.info_evaluator", new_callable=AsyncMock) as mock_evaluator, \
                patch(f"{MODULE_PATH}.parse_evaluator_output") as mock_parse, \
                patch(f"{MODULE_PATH}.process_scored_item") as mock_process:
            mock_evaluator.return_value = "[]"
            mock_parse.return_value = scored_items
            # 第一个返回有效项，第二个返回None
            mock_process.side_effect = [scored_items[0], None]

            result = await run_doc_evaluation(self.sample_query, self.sample_documents, llm=None)

            assert result == [scored_items[0]]
            mock_evaluator.assert_called_once_with(self.sample_query, self.sample_documents, None)
            mock_process.assert_any_call(scored_items[0], 0, self.sample_documents)
            mock_process.assert_any_call(scored_items[1], 1, self.sample_documents)
            assert mock_process.call_count == 2


class TestParseEvaluatorOutput:
    def test_parse_evaluator_output_success(self):
        """测试成功的JSON解析"""
        valid_json = '[{"document_index": 0, "scores": {"relevance": 0.9}}]'

        # 如果 normalize_json_output 存在，mock它
        with patch(f"{MODULE_PATH}.normalize_json_output") as mock_normalize:
            mock_normalize.return_value = valid_json

            result = parse_evaluator_output(valid_json)

            mock_normalize.assert_called_once_with(valid_json)
            assert result == [{"document_index": 0, "scores": {"relevance": 0.9}}]

    def test_parse_evaluator_output_json_decode_error(self):
        """测试JSON解析错误"""
        invalid_json = "invalid json"

        with patch(f"{MODULE_PATH}.normalize_json_output") as mock_normalize, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_normalize.side_effect = json.JSONDecodeError("Expecting value", "doc", 0)
            mock_sensitive.return_value = False

            result = parse_evaluator_output(invalid_json)

            assert result == []
            mock_logger.error.assert_called_once()


class TestProcessScoredItem:
    def setup_method(self):
        self.contents = ["content 1", "content 2", "content 3"]

    def test_process_scored_item_valid_input(self):
        """测试有效的输入"""
        scored = {"document_index": 1, "scores": {"relevance": 0.8}, "doc_time": "2023-01-01"}

        result = process_scored_item(scored, 0, self.contents)

        assert result == scored

    def test_process_scored_item_rejects_legacy_content_index(self):
        """测试拒绝旧 content 索引字段。"""
        scored = {"content": 1, "scores": {"relevance": 0.8}, "doc_time": "2023-01-01"}

        result = process_scored_item(scored, 0, self.contents)

        assert result is None

    def test_process_scored_item_non_dict_input(self):
        """测试非字典输入"""
        with patch(f"{MODULE_PATH}.logger") as mock_logger:
            result = process_scored_item("not a dict", 1, self.contents)

            assert result is None
            mock_logger.error.assert_called_once()

class TestExtractScores:
    def setup_method(self):
        self.scored = {"score": {"relevance": 0.9, "accuracy": 0.8}}

    def test_extract_scores_with_score_field(self):
        """测试包含score字段的情况"""
        result = extract_scores(self.scored)
        assert result == {"relevance": 0.9, "accuracy": 0.8}

    def test_extract_scores_with_scores_field(self):
        """测试包含scores字段的情况"""
        result = extract_scores({"scores": {"relevance": 0.9, "accuracy": 0.8}})
        assert result == {"relevance": 0.9, "accuracy": 0.8}


class TestEnsureDocumentIndex:
    def test_ensure_document_index_field_complete(self):
        """测试完整的输入"""
        scored = {"document_index": 1, "scores": {"relevance": 0.9}, "doc_time": "2023-01-01"}
        result = ensure_document_index_field(scored, 0)
        assert result == scored

    def test_ensure_document_index_field_rejects_legacy_content(self):
        """测试旧 content 索引字段不会被转换为 document_index。"""
        scored = {"content": 1, "scores": {"relevance": 0.9}, "doc_time": "2023-01-01"}

        with pytest.raises(KeyError, match="deprecated content field"):
            ensure_document_index_field(scored, 0)

    def test_ensure_document_index_field_with_score_dict(self):
        """测试包含score字典的情况"""
        scored = {"document_index": 0, "score": {"relevance": 0.9, "accuracy": 0.8}}
        result = ensure_document_index_field(scored, 0)

        # 验证score被转换为scores
        assert "score" not in result
        assert result["scores"] == {"relevance": 0.9, "accuracy": 0.8}
        assert result["document_index"] == 0
        assert result["doc_time"] == "Unknown"

    def test_ensure_document_index_field_missing_index_raises(self):
        """测试缺少 document_index 字段"""
        scored = {"scores": {"relevance": 0.9}}

        with pytest.raises(KeyError, match="document_index"):
            ensure_document_index_field(scored, 5)


class TestValidateDocumentIndex:
    def test_validate_document_index_valid(self):
        """测试有效的索引"""
        scored = {"document_index": 1}
        contents = ["content 0", "content 1", "content 2"]

        # 不应该抛出异常
        validate_document_index(scored, contents)

    def test_validate_document_index_out_of_range_positive(self):
        """测试超出范围的索引"""
        scored = {"document_index": 5}
        contents = ["content 0", "content 1"]

        with pytest.raises(IndexError, match="document_index 5 is out of range"):
            validate_document_index(scored, contents)


class TestLogContentAndScores:
    def setup_method(self):
        self.scored = {"document_index": 1, "scores": {"relevance": 0.9, "accuracy": 0.8}}
        self.contents = ["short", "this is a very long content that should be truncated"]

    def test_log_content_and_scores_normal(self):
        """测试正常情况"""
        with patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_sensitive.return_value = False

            log_content_and_scores(self.scored, self.contents)

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            assert "this is a very long content that should be truncated" in call_args
            assert "evaluation score: " in call_args

class TestInfoEvaluator:
    def setup_method(self):
        self.query = "test query"
        self.documents = [
            {
                "source_id": "0",
                "title": "测试标题",
                "url": "https://example.com/a",
                "key_passages": ["关键片段"],
            }
        ]
        self.llm = None

    @pytest.mark.asyncio
    async def test_info_evaluator_success(self):
        """测试成功的LLM调用"""
        expected_response = {"content": '[{"document_index": 0, "scores": {}}]'}

        # 如果 apply_system_prompt 存在， mock
        with patch(f"{MODULE_PATH}.apply_system_prompt") as mock_apply_prompt, \
                patch(f"{MODULE_PATH}.invoke_llm_with_retry", new_callable=AsyncMock) as mock_invoke:
            mock_prompts = [{"role": "system", "content": "evaluate"}]
            mock_apply_prompt.return_value = mock_prompts
            mock_invoke.return_value = expected_response

            result = await info_evaluator(self.query, self.documents, self.llm)

            mock_apply_prompt.assert_called_once_with("info_evaluator_doc", {
                "query": self.query,
                "messages": build_evaluator_messages(self.documents)
            })
            mock_invoke.assert_called_once_with(mock_prompts, self.llm)
            assert result == expected_response["content"]

    @pytest.mark.asyncio
    async def test_info_evaluator_sensitive_mode_exception(self):
        """测试敏感模式下的LLM调用异常"""
        with patch(f"{MODULE_PATH}.apply_system_prompt") as mock_apply_prompt, \
                patch(f"{MODULE_PATH}.invoke_llm_with_retry", new_callable=AsyncMock) as mock_invoke, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_apply_prompt.return_value = []
            mock_invoke.side_effect = Exception("LLM invocation failed")
            mock_sensitive.return_value = True  # 敏感模式

            result = await info_evaluator(self.query, self.documents, self.llm)

            # 验证敏感模式下的错误日志
            mock_logger.error.assert_called_once_with("[POST PROCESSING] Failed to evaluate doc. ")
            # 验证没有调用带堆栈的exception日志
            mock_logger.exception.assert_not_called()
            assert result == "[]"

    @pytest.mark.asyncio
    async def test_info_evaluator_non_sensitive_mode_exception(self):
        """测试非敏感模式下的LLM调用异常"""
        with patch(f"{MODULE_PATH}.apply_system_prompt") as mock_apply_prompt, \
                patch(f"{MODULE_PATH}.invoke_llm_with_retry", new_callable=AsyncMock) as mock_invoke, \
                patch(f"{MODULE_PATH}.LogManager.is_sensitive") as mock_sensitive, \
                patch(f"{MODULE_PATH}.logger") as mock_logger:
            mock_apply_prompt.return_value = []
            mock_invoke.side_effect = Exception("LLM invocation failed with details")
            mock_sensitive.return_value = False  # 非敏感模式

            result = await info_evaluator(self.query, self.documents, self.llm)

            # 验证非敏感模式下的异常日志
            mock_logger.error.assert_called_once_with("[POST PROCESSING] Failed to evaluate doc. LLM invocation failed with details")
            assert result == "[]"

class TestInvokeLLMWithRetry:
    def setup_method(self):
        self.prompt = [{"role": "user", "content": "test"}]
        self.mock_llm_instance = Mock()
        self.llm = None

    @pytest.mark.asyncio
    async def test_invoke_llm_with_retry_success_first_try(self):
        """测试第一次调用成功"""
        mock_response= {"content": "response"}

        # 如果 llm_wapper 存在， mock它
        with patch(f"{MODULE_PATH}.ainvoke_llm_with_stats", new_callable=AsyncMock) as mock_llm_call:

            mock_llm_call.return_value = mock_response

            result = await invoke_llm_with_retry(self.prompt, self.llm)

            mock_llm_call.assert_called_once()
            assert result == mock_response
