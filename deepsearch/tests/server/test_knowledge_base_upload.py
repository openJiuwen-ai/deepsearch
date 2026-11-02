import pytest
from fastapi import status

from server.local_retrieval.core.manager import knowledge_base as kb_manager
from server.schemas.common import ResponseModel


def test_resolve_upload_file_path_keeps_valid_document_id_within_storage_root(tmp_path):
    storage_root = tmp_path / "knowledge_base" / "space-1" / "kb-1"
    storage_root.mkdir(parents=True)

    file_path = kb_manager._resolve_upload_file_path(
        storage_root, "document_123-abc", ".txt"
    )

    assert file_path == storage_root / "document_123-abc.txt"
    assert file_path.is_relative_to(storage_root)


@pytest.mark.parametrize(
    "document_id",
    [
        "../outside",
        "/tmp/outside",
        r"..\outside",
        r"C:\outside",
        r"\\server\share",
        ".",
        "..",
    ],
)
def test_resolve_upload_file_path_rejects_path_like_document_ids(tmp_path, document_id):
    storage_root = tmp_path / "knowledge_base" / "space-1" / "kb-1"
    storage_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="Invalid document identifier"):
        kb_manager._resolve_upload_file_path(storage_root, document_id, ".txt")


@pytest.mark.asyncio
async def test_document_upload_rejects_traversal_before_storage_or_document_sync(monkeypatch):
    monkeypatch.setattr(
        kb_manager.knowledge_base_repository,
        "knowledge_base_get",
        lambda _: ResponseModel(code=status.HTTP_200_OK, message="Success", data={"id": "kb-1"}),
    )
    monkeypatch.setattr(kb_manager, "kb_obs_misconfigured_message", lambda: None)

    def unexpected_side_effect(**_):
        raise AssertionError("unsafe document ID must be rejected before this operation")

    monkeypatch.setattr(
        kb_manager.knowledge_base_repository,
        "document_id_list",
        unexpected_side_effect,
    )
    monkeypatch.setattr(kb_manager, "_get_storage_path", unexpected_side_effect)

    response = await kb_manager.document_upload(
        space_id="space-1",
        kb_id="kb-1",
        files=[],
        metadata={"doc_list": ["../../../../outside"]},
    )

    assert response.code == status.HTTP_400_BAD_REQUEST
    assert response.message == "Invalid document identifier"
