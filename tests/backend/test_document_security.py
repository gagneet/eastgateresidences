from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi import HTTPException

from routers.documents import get_document


@pytest.mark.asyncio
async def test_get_document_unauthorized_bypass_attempt():
    # Mock document that is NOT public and only allowed for 'super_admin'
    mock_doc = {
        "id": "doc123",
        "title": "Private Doc",
        "is_public": False,
        "allowed_roles": ["super_admin"],
        "uploaded_by": "admin_id",
        "category": "legal",
        "file_name": "test.pdf",
        "file_type": "application/pdf",
        "file_size": 1024,
        "uploaded_by_name": "Admin",
        "created_at": "2024-01-01",
        "updated_at": "2024-01-01"
    }

    # Mock user who has 'can_view_documents' permission (e.g., an 'owner')
    # but is NOT the uploader and NOT an admin
    mock_user = {
        "id": "owner_id",
        "role": "owner",
        "full_name": "John Owner"
    }

    # Mock get_user_permissions: True for can_view_documents, False for can_manage_users
    mock_permissions = MagicMock()
    mock_permissions.can_view_documents = True
    mock_permissions.can_manage_users = False

    with patch("routers.documents.db") as mock_db, \
            patch("routers.documents.get_user_permissions", return_value=mock_permissions):
        mock_db.documents.find_one = AsyncMock(return_value=mock_doc)

        # This should now RAISE an HTTPException(403) because the user is not an admin,
        # not the uploader, and their role 'owner' is not in ['super_admin']
        with pytest.raises(HTTPException) as excinfo:
            await get_document("doc123", current_user=mock_user)

        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == "Not authorized"


@pytest.mark.asyncio
async def test_get_document_authorized_admin():
    mock_doc = {
        "id": "doc123",
        "title": "Private Doc",
        "is_public": False,
        "allowed_roles": ["super_admin"],
        "uploaded_by": "admin_id",
        "category": "legal",
        "file_name": "test.pdf",
        "file_type": "application/pdf",
        "file_size": 1024,
        "uploaded_by_name": "Admin",
        "created_at": "2024-01-01",
        "updated_at": "2024-01-01"
    }

    mock_user = {
        "id": "another_admin_id",
        "role": "super_admin",
        "full_name": "Admin User"
    }

    # Mock get_user_permissions: can_manage_users=True means they are an admin
    mock_permissions = MagicMock()
    mock_permissions.can_view_documents = True
    mock_permissions.can_manage_users = True

    with patch("routers.documents.db") as mock_db, \
            patch("routers.documents.get_user_permissions", return_value=mock_permissions):
        mock_db.documents.find_one = AsyncMock(return_value=mock_doc)

        response = await get_document("doc123", current_user=mock_user)
        assert response.id == "doc123"


@pytest.mark.asyncio
async def test_get_document_authorized_uploader():
    mock_doc = {
        "id": "doc123",
        "title": "Private Doc",
        "is_public": False,
        "allowed_roles": [],
        "uploaded_by": "owner_id",
        "category": "legal",
        "file_name": "test.pdf",
        "file_type": "application/pdf",
        "file_size": 1024,
        "uploaded_by_name": "Owner",
        "created_at": "2024-01-01",
        "updated_at": "2024-01-01"
    }

    mock_user = {
        "id": "owner_id",
        "role": "owner",
        "full_name": "John Owner"
    }

    mock_permissions = MagicMock()
    mock_permissions.can_view_documents = True
    mock_permissions.can_manage_users = False

    with patch("routers.documents.db") as mock_db, \
            patch("routers.documents.get_user_permissions", return_value=mock_permissions):
        mock_db.documents.find_one = AsyncMock(return_value=mock_doc)

        response = await get_document("doc123", current_user=mock_user)
        assert response.id == "doc123"
