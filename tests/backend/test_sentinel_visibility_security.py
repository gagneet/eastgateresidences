import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from routers.community import get_blog_posts, get_blog_post
from routers.listings import get_listings, get_listing
from models.user import UserRole


# Mock database
@pytest.fixture
def mock_db():
    with patch("routers.community.db") as mock_comm_db, \
            patch("routers.listings.db") as mock_list_db:
        yield mock_comm_db, mock_list_db


@pytest.mark.asyncio
async def test_get_blog_posts_unapproved_visibility():
    """Verify that unapproved users only see public blog posts."""
    mock_user = {
        "id": "user-1",
        "role": UserRole.TENANT,
        "is_approved": False,
        "full_name": "Unapproved User"
    }

    mock_comm_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_comm_db.blog_posts.find.return_value = mock_cursor

    with patch("routers.community.db", mock_comm_db), \
            patch("routers.community.is_approved_user", return_value=False):
        await get_blog_posts(current_user=mock_user, building_id="13195")

        # Check the query
        args, _ = mock_comm_db.blog_posts.find.call_args
        query = args[0]

        # It should have is_public: True because user is not approved
        assert query["is_public"] is True


@pytest.mark.asyncio
async def test_get_blog_posts_defaults_missing_legacy_tags():
    """Legacy blog documents without tags should not 500 during response validation."""
    mock_user = {
        "id": "user-1",
        "role": UserRole.TENANT,
        "is_approved": False,
        "full_name": "Unapproved User"
    }
    legacy_post = {
        "id": "post-1",
        "building_id": "13195",
        "is_public": True,
        "is_published": True,
        "title": "Legacy Post",
        "content": "No tags field on this old document",
        "author_id": "author-1",
        "author_name": "Author",
        "views": 0,
        "created_at": "2026-06-29T00:00:00+00:00",
        "updated_at": "2026-06-29T00:00:00+00:00",
    }

    mock_comm_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[legacy_post])
    mock_comm_db.blog_posts.find.return_value = mock_cursor

    with patch("routers.community.db", mock_comm_db), \
            patch("routers.community.is_approved_user", return_value=False):
        result = await get_blog_posts(current_user=mock_user, building_id="13195")

    assert result[0].tags == []


def test_server_blog_response_defaults_missing_legacy_tags():
    """The inline /api/blog route in server.py uses its own response model."""
    from server import BlogPostResponse

    post = BlogPostResponse(
        id="post-1",
        title="Legacy Post",
        content="No tags field on this old document",
        is_published=True,
        author_id="author-1",
        author_name="Author",
        views=0,
        created_at="2026-06-29T00:00:00+00:00",
        updated_at="2026-06-29T00:00:00+00:00",
    )

    assert post.tags == []


@pytest.mark.asyncio
async def test_get_blog_post_unauthenticated_bola():
    """Verify that unauthenticated users cannot access private blog posts via BOLA."""
    post_id = "post-123"
    private_post = {
        "id": post_id,
        "building_id": "13195",
        "is_public": False,
        "title": "Private Post",
        "content": "Secret content",
        "views": 0
    }

    mock_comm_db = MagicMock()
    mock_comm_db.blog_posts.find_one = AsyncMock(return_value=private_post)

    with patch("routers.community.db", mock_comm_db), \
            patch("routers.community.is_approved_user", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await get_blog_post(post_id=post_id, current_user=None, building_id="13195")
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_listings_unapproved_visibility():
    """Verify that unapproved users only see public listings."""
    mock_user = {
        "id": "user-1",
        "role": UserRole.TENANT,
        "is_approved": False,
        "full_name": "Unapproved User"
    }

    mock_list_db = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_list_db.listings.find.return_value = mock_cursor

    with patch("routers.listings.db", mock_list_db), \
            patch("routers.listings.is_approved_user", return_value=False):
        await get_listings(current_user=mock_user, building_id="13195")

        # Check the query
        args, _ = mock_list_db.listings.find.call_args
        query = args[0]

        # It should have is_public: True because user is not approved
        assert query["is_public"] is True


@pytest.mark.asyncio
async def test_get_listing_unauthenticated_bola():
    """Verify that unauthenticated users cannot access private listings via BOLA."""
    listing_id = "list-123"
    private_listing = {
        "id": listing_id,
        "building_id": "13195",
        "is_public": False,
        "title": "Private Listing",
        "description": "Secret item"
    }

    mock_list_db = MagicMock()
    mock_list_db.listings.find_one = AsyncMock(return_value=private_listing)

    with patch("routers.listings.db", mock_list_db), \
            patch("routers.listings.is_approved_user", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await get_listing(listing_id=listing_id, current_user=None, building_id="13195")
        assert exc.value.status_code == 403
