import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
import html
from types import SimpleNamespace
import server

from server import app, get_current_user, get_current_building

client = TestClient(app)


def _ec_user(uid="user-1"):
    """Return an approved ec_member user — has can_upload_documents=True."""
    return {"id": uid, "full_name": "Test EC Member", "role": "ec_member", "is_approved": True}


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_move_document_bola_scoping(auth_header):
    """Verify move_document uses building_id in all DB calls."""
    doc_id = "test-doc-123"
    folder_id = "test-folder-456"
    building_id = "bld-789"
    # ec_member is approved by default and has can_upload_documents=True
    mock_user = {
        "id": "user-1",
        "full_name": "Test User",
        "role": "ec_member",
        "is_approved": True,
    }

    mock_documents = SimpleNamespace(
        find_one=AsyncMock(),
        update_one=AsyncMock(),
    )
    mock_document_folders = SimpleNamespace(
        find_one=AsyncMock(),
    )
    mock_db = SimpleNamespace(
        documents=mock_documents,
        document_folders=mock_document_folders,
    )

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_building] = lambda: building_id

    try:
        with patch.object(server, "db", mock_db), patch(
                "utils.auth.decode_token",
                return_value={"user_id": "user-1", "building_id": building_id},
        ):
            mock_documents.find_one.return_value = {
                "id": doc_id,
                "building_id": building_id,
                "uploaded_by": "user-1",
            }
            mock_document_folders.find_one.return_value = {
                "id": folder_id,
                "building_id": building_id,
            }
            mock_documents.update_one.return_value.matched_count = 1

            response = client.put(
                f"/api/documents/{doc_id}/move",
                json={"folder_id": folder_id},
                headers=auth_header,
            )
            assert response.status_code == 200

            args_doc, _ = mock_documents.find_one.call_args
            assert args_doc[0]["building_id"] == building_id

            args_folder, _ = mock_document_folders.find_one.call_args
            assert args_folder[0]["building_id"] == building_id

            args_upd, _ = mock_documents.update_one.call_args
            assert args_upd[0]["building_id"] == building_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_rename_document_xss_and_bola(auth_header):
    """Verify rename_document fixes XSS and uses building_id."""
    doc_id = "test-doc-123"
    building_id = "bld-789"
    unsafe_title = "<script>alert('xss')</script>"

    # ec_member is approved by default and has can_upload_documents=True
    mock_user = {
        "id": "user-1",
        "full_name": "Test User",
        "role": "ec_member",
        "is_approved": True,
    }

    mock_documents = SimpleNamespace(
        find_one=AsyncMock(),
        update_one=AsyncMock(),
    )
    mock_db = SimpleNamespace(
        documents=mock_documents,
    )

    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_current_building] = lambda: building_id

    try:
        with patch.object(server, "db", mock_db), patch(
                "utils.auth.decode_token",
                return_value={"user_id": "user-1", "building_id": building_id},
        ):
            mock_documents.find_one.return_value = {
                "id": doc_id,
                "building_id": building_id,
                "uploaded_by": "user-1",
            }
            mock_documents.update_one.return_value.matched_count = 1

            response = client.put(
                f"/api/documents/{doc_id}/rename",
                json={"new_title": unsafe_title},
                headers=auth_header,
            )
            assert response.status_code == 200

            args_upd, _ = mock_documents.update_one.call_args
            assert args_upd[0]["building_id"] == building_id
            assert args_upd[1]["$set"]["title"] == html.escape(unsafe_title)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_bulk_move_documents_bola_scoping(auth_header):
    """Verify bulk_move_documents scopes both folder lookup and document update to building_id."""
    building_id = "bld-789"
    doc_ids = ["doc-1", "doc-2"]
    folder_id = "folder-99"

    mock_document_folders = SimpleNamespace(find_one=AsyncMock())
    mock_documents = SimpleNamespace(update_many=AsyncMock())
    mock_db = SimpleNamespace(
        document_folders=mock_document_folders,
        documents=mock_documents,
    )

    app.dependency_overrides[get_current_user] = lambda: _ec_user()
    app.dependency_overrides[get_current_building] = lambda: building_id

    try:
        with patch.object(server, "db", mock_db):
            mock_document_folders.find_one.return_value = {
                "id": folder_id,
                "building_id": building_id,
            }
            mock_documents.update_many.return_value = MagicMock(modified_count=2)

            response = client.post(
                "/api/documents/bulk-move",
                json={"document_ids": doc_ids, "target_folder_id": folder_id},
                headers=auth_header,
            )
            assert response.status_code == 200
            assert response.json()["count"] == 2

            folder_args, _ = mock_document_folders.find_one.call_args
            assert folder_args[0]["building_id"] == building_id

            upd_args, _ = mock_documents.update_many.call_args
            assert upd_args[0]["building_id"] == building_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_move_document_cross_tenant_rejected(auth_header):
    """A document belonging to a different building must not be moveable (BOLA)."""
    doc_id = "doc-other-tenant"
    building_id = "bld-789"

    mock_documents = SimpleNamespace(find_one=AsyncMock(return_value=None))
    mock_db = SimpleNamespace(
        documents=mock_documents,
        document_folders=SimpleNamespace(find_one=AsyncMock()),
    )

    app.dependency_overrides[get_current_user] = lambda: _ec_user()
    app.dependency_overrides[get_current_building] = lambda: building_id

    try:
        with patch.object(server, "db", mock_db):
            response = client.put(
                f"/api/documents/{doc_id}/move",
                json={"folder_id": "any-folder"},
                headers=auth_header,
            )
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_rename_document_cross_tenant_rejected(auth_header):
    """A document belonging to a different building must not be renameable (BOLA)."""
    doc_id = "doc-other-tenant"
    building_id = "bld-789"

    mock_documents = SimpleNamespace(find_one=AsyncMock(return_value=None))
    mock_db = SimpleNamespace(documents=mock_documents)

    app.dependency_overrides[get_current_user] = lambda: _ec_user()
    app.dependency_overrides[get_current_building] = lambda: building_id

    try:
        with patch.object(server, "db", mock_db):
            response = client.put(
                f"/api/documents/{doc_id}/rename",
                json={"new_title": "Hacked Title"},
                headers=auth_header,
            )
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)
