from types import SimpleNamespace

from openjiuwen_deepsearch.framework.openjiuwen.tools.search_api.native_local_search_api.api_wrapper import (
    NativeLocalSearchAPIWrapper,
)


def _build_wrapper() -> NativeLocalSearchAPIWrapper:
    return NativeLocalSearchAPIWrapper(knowledge_base_configs=[])


def test_process_and_format_prefers_metadata_document_name():
    wrapper = _build_wrapper()
    retrieval_result = SimpleNamespace(
        doc_id="doc-1",
        chunk_id="chunk-9",
        text="content body",
        score=0.98,
        metadata={"document_name": "Annual Report 2025.pdf"},
    )

    result = wrapper._process_and_format([{"raw": retrieval_result, "kb_id": "kb-a"}], num=1)

    assert len(result) == 1
    assert result[0]["title"] == "Annual Report 2025.pdf"
    assert result[0]["document_name"] == "Annual Report 2025.pdf"
    assert result[0]["file_id"] == "doc-1"


def test_process_and_format_prefers_direct_field_title():
    wrapper = _build_wrapper()
    retrieval_result = SimpleNamespace(
        doc_id="doc-2",
        chunk_id="chunk-1",
        text="another body",
        score=0.9,
        metadata={"document_name": "Ignored Metadata Name", "title": "Project Plan"},
    )

    result = wrapper._process_and_format([{"raw": retrieval_result, "kb_id": "kb-b"}], num=1)

    assert len(result) == 1
    assert result[0]["title"] == "Project Plan"
    assert result[0]["document_name"] == "Ignored Metadata Name"


def test_process_and_format_falls_back_to_unique_id():
    wrapper = _build_wrapper()
    retrieval_result = SimpleNamespace(
        doc_id="",
        chunk_id="chunk-2",
        text="fallback body",
        score=0.8,
        metadata={},
    )

    result = wrapper._process_and_format([{"raw": retrieval_result, "kb_id": "kb-c"}], num=1)

    assert len(result) == 1
    assert result[0]["title"] == "missing_id_chunk-2"
    assert result[0]["document_name"] == "missing_id_chunk-2"
    assert result[0]["file_id"] == "missing_id_chunk-2"
