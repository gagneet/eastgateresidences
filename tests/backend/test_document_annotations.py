"""
Tests for document annotation CRUD endpoints.

Coverage:
  1. List annotations — happy path, is_test_data exclusion
  2. Create annotation — happy path, image size limit (413), BOLA
  3. Update annotation — owner can update, non-owner 403
  4. Delete annotation — owner can delete, manager can delete, other user 403
  5. Cross-tenant isolation — annotation from building A invisible to building B
  6. BOLA guard — document not in building returns 404 on all endpoints
  7. updated_at changes on update
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace
from server import app, get_current_user, get_current_building

client = TestClient(app)

# ── Fixtures ──────────────────────────────────────────────────────────────────

BUILDING_A = "building-aaa"
BUILDING_B = "building-bbb"
DOC_ID = "doc-001"
ANN_ID = "ann-001"
OWNER_ID = "user-owner"
OTHER_ID = "user-other"
MANAGER_ID = "user-mgr"


def _user(uid=OWNER_ID, role="owner"):
    return {"id": uid, "full_name": "Test User", "role": role, "is_approved": True}


def _manager():
    return {"id": MANAGER_ID, "full_name": "Manager", "role": "strata_manager", "is_approved": True}


def _doc(building_id=BUILDING_A):
    return {"id": DOC_ID, "building_id": building_id, "title": "Test Doc", "file_type": "application/pdf"}


def _annotation(building_id=BUILDING_A, created_by=OWNER_ID, is_test=False):
    ann = {
        "id": ANN_ID,
        "highlight_id": "h-001",
        "document_id": DOC_ID,
        "building_id": building_id,
        "position": {
            "boundingRect": {"x1": 0, "y1": 0, "x2": 100, "y2": 20, "width": 800, "height": 1000, "pageNumber": 1},
            "rects": [],
            "pageNumber": 1,
        },
        "content": {"text": "important clause", "image": None},
        "comment": {"text": "Review this", "emoji": "⚠️"},
        "created_by": created_by,
        "created_by_name": "Test User",
        "created_at": "2026-04-26T00:00:00Z",
        "updated_at": "2026-04-26T00:00:00Z",
    }
    if is_test:
        ann["is_test_data"] = True
    return ann


def _create_body():
    return {
        "highlight_id": "h-new",
        "position": {
            "boundingRect": {"x1": 0, "y1": 0, "x2": 50, "y2": 10, "width": 800, "height": 1000, "pageNumber": 2},
            "rects": [],
            "pageNumber": 2,
        },
        "content": {"text": "clause text", "image": None},
        "comment": {"text": "Note this", "emoji": "📌"},
    }


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}


def _make_db(docs_find_one=None, ann_find=None, ann_find_one=None,
             ann_insert=None, ann_update=None, ann_delete=None):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=ann_find if ann_find is not None else [])

    mock_db = SimpleNamespace(
        documents=SimpleNamespace(find_one=AsyncMock(return_value=docs_find_one)),
        document_annotations=SimpleNamespace(
            find=MagicMock(return_value=cursor),
            find_one=AsyncMock(return_value=ann_find_one),
            insert_one=AsyncMock(return_value=MagicMock(inserted_id="new-id")),
            update_one=AsyncMock(),
            delete_one=AsyncMock(),
        ),
    )
    return mock_db


# ─────────────────────────────────────────────────────────────────────────────
# 1. List annotations
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_annotations_happy_path(auth_header):
    ann = _annotation()
    mock_db = _make_db(docs_find_one=_doc(), ann_find=[ann])

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.get(f"/api/documents/{DOC_ID}/annotations", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == ANN_ID
        assert data[0]["comment"]["text"] == "Review this"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_list_annotations_document_not_found(auth_header):
    mock_db = _make_db(docs_find_one=None)

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.get(f"/api/documents/{DOC_ID}/annotations", headers=auth_header)
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Create annotation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_annotation_happy_path(auth_header):
    mock_db = _make_db(docs_find_one=_doc())

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.post(
                f"/api/documents/{DOC_ID}/annotations",
                json=_create_body(),
                headers=auth_header,
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["highlight_id"] == "h-new"
        assert data["document_id"] == DOC_ID
        assert data["building_id"] == BUILDING_A
        assert data["created_by"] == OWNER_ID
        # Verify building_id injected into insert
        call_args = mock_db.document_annotations.insert_one.call_args[0][0]
        assert call_args["building_id"] == BUILDING_A
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_create_annotation_image_too_large(auth_header):
    mock_db = _make_db(docs_find_one=_doc())
    body = _create_body()
    body["content"]["image"] = "A" * (512 * 1024 + 1)  # 512 KB + 1 byte

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.post(
                f"/api/documents/{DOC_ID}/annotations",
                json=body,
                headers=auth_header,
            )
        assert resp.status_code == 413
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_create_annotation_bola_wrong_building(auth_header):
    """Document not found in requester's building → 404, not data from building B."""
    mock_db = _make_db(docs_find_one=None)  # not found in BUILDING_A

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.post(
                f"/api/documents/{DOC_ID}/annotations",
                json=_create_body(),
                headers=auth_header,
            )
        assert resp.status_code == 404
        mock_db.document_annotations.insert_one.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Update annotation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_annotation_owner_succeeds(auth_header):
    ann = _annotation(created_by=OWNER_ID)
    mock_db = _make_db(docs_find_one=_doc(), ann_find_one=ann)

    app.dependency_overrides[get_current_user] = lambda: _user(uid=OWNER_ID)
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.put(
                f"/api/documents/{DOC_ID}/annotations/{ANN_ID}",
                json={"comment": {"text": "Updated note", "emoji": "✏️"}},
                headers=auth_header,
            )
        assert resp.status_code == 200
        assert resp.json()["comment"]["text"] == "Updated note"
        mock_db.document_annotations.update_one.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_update_annotation_non_owner_forbidden(auth_header):
    ann = _annotation(created_by=OWNER_ID)  # owned by OWNER_ID
    mock_db = _make_db(docs_find_one=_doc(), ann_find_one=ann)

    app.dependency_overrides[get_current_user] = lambda: _user(uid=OTHER_ID)  # different user
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.put(
                f"/api/documents/{DOC_ID}/annotations/{ANN_ID}",
                json={"comment": {"text": "Hacked", "emoji": ""}},
                headers=auth_header,
            )
        assert resp.status_code == 403
        mock_db.document_annotations.update_one.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_update_annotation_updated_at_changes(auth_header):
    ann = _annotation(created_by=OWNER_ID)
    original_updated_at = ann["updated_at"]
    mock_db = _make_db(docs_find_one=_doc(), ann_find_one=ann)

    app.dependency_overrides[get_current_user] = lambda: _user(uid=OWNER_ID)
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db), \
                patch("routers.document_annotations.get_current_utc_iso", return_value="2026-05-01T00:00:00Z"):
            resp = client.put(
                f"/api/documents/{DOC_ID}/annotations/{ANN_ID}",
                json={"comment": {"text": "New text", "emoji": ""}},
                headers=auth_header,
            )
        assert resp.status_code == 200
        assert resp.json()["updated_at"] == "2026-05-01T00:00:00Z"
        assert resp.json()["updated_at"] != original_updated_at
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Delete annotation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_annotation_owner_succeeds(auth_header):
    ann = _annotation(created_by=OWNER_ID)
    mock_db = _make_db(docs_find_one=_doc(), ann_find_one=ann)

    app.dependency_overrides[get_current_user] = lambda: _user(uid=OWNER_ID)
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.delete(
                f"/api/documents/{DOC_ID}/annotations/{ANN_ID}",
                headers=auth_header,
            )
        assert resp.status_code == 200
        mock_db.document_annotations.delete_one.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_delete_annotation_manager_can_delete_any(auth_header):
    """Managers can delete other users' annotations."""
    ann = _annotation(created_by=OWNER_ID)
    mock_db = _make_db(docs_find_one=_doc(), ann_find_one=ann)

    app.dependency_overrides[get_current_user] = lambda: _manager()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.delete(
                f"/api/documents/{DOC_ID}/annotations/{ANN_ID}",
                headers=auth_header,
            )
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_delete_annotation_other_user_forbidden(auth_header):
    ann = _annotation(created_by=OWNER_ID)
    mock_db = _make_db(docs_find_one=_doc(), ann_find_one=ann)

    app.dependency_overrides[get_current_user] = lambda: _user(uid=OTHER_ID, role="owner")
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.delete(
                f"/api/documents/{DOC_ID}/annotations/{ANN_ID}",
                headers=auth_header,
            )
        assert resp.status_code == 403
        mock_db.document_annotations.delete_one.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Cross-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_tenant_list_isolation(auth_header):
    """Annotations for building A must not be returned when requesting as building B."""
    # Building B user sees no document → 404 before any annotation is read
    mock_db = _make_db(docs_find_one=None)

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_B
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.get(
                f"/api/documents/{DOC_ID}/annotations",
                headers=auth_header,
            )
        assert resp.status_code == 404
        mock_db.document_annotations.find.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


@pytest.mark.asyncio
async def test_create_annotation_building_id_scoped(auth_header):
    """Created annotation must carry the building_id from the authenticated context."""
    mock_db = _make_db(docs_find_one=_doc(building_id=BUILDING_A))

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.post(
                f"/api/documents/{DOC_ID}/annotations",
                json=_create_body(),
                headers=auth_header,
            )
        assert resp.status_code == 201
        inserted = mock_db.document_annotations.insert_one.call_args[0][0]
        assert inserted["building_id"] == BUILDING_A
        assert "BUILDING_B" not in str(inserted)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


# ─────────────────────────────────────────────────────────────────────────────
# 6. BOLA — all endpoints guard with document existence check
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("GET", f"/api/documents/{DOC_ID}/annotations", None),
    ("POST", f"/api/documents/{DOC_ID}/annotations", _create_body()),
    ("PUT", f"/api/documents/{DOC_ID}/annotations/{ANN_ID}", {"comment": {"text": "x", "emoji": ""}}),
    ("DELETE", f"/api/documents/{DOC_ID}/annotations/{ANN_ID}", None),
])
@pytest.mark.asyncio
async def test_bola_document_not_in_building(method, path, body, auth_header):
    mock_db = _make_db(docs_find_one=None)  # doc not visible in this building

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            fn = getattr(client, method.lower())
            resp = fn(path, json=body, headers=auth_header) if body else fn(path, headers=auth_header)
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)


# ─────────────────────────────────────────────────────────────────────────────
# 7. is_test_data filter — test records must not appear in production listing
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_annotations_excludes_test_data(auth_header):
    """The find() query must include is_test_data: {$ne: True} so perf/test
    annotations are never returned to real users."""
    mock_db = _make_db(docs_find_one=_doc(), ann_find=[])

    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_current_building] = lambda: BUILDING_A
    try:
        with patch("routers.document_annotations.db", mock_db):
            resp = client.get(f"/api/documents/{DOC_ID}/annotations", headers=auth_header)
        assert resp.status_code == 200
        call_filter = mock_db.document_annotations.find.call_args[0][0]
        assert call_filter.get("is_test_data") == {"$ne": True}
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)
