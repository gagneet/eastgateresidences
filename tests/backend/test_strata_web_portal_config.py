"""Tests for the per-building Strata Web portal connection config service.

Security-critical invariants:
  * the password is stored ENCRYPTED (never plaintext),
  * masked reads never expose the password (only `password_set`),
  * the decrypted-credential accessor is gated on `enabled` and building-scoped.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BACKEND = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


@pytest.fixture(autouse=True)
def _working_cipher(monkeypatch):
    """Give utils.encryption a real Fernet cipher regardless of the env's ENCRYPTION_KEY."""
    from cryptography.fernet import Fernet
    import utils.encryption as enc
    monkeypatch.setattr(enc, "_cipher", Fernet(Fernet.generate_key()))


def _fake_db():
    """A per-building in-memory stand-in for db._db[collection] (find_one/update_one)."""
    store: dict[str, dict] = {}

    async def find_one(query, projection=None):
        doc = store.get(query["building_id"])
        return dict(doc) if doc else None

    async def update_one(query, update, upsert=False):
        bid = query["building_id"]
        doc = store.setdefault(bid, {})
        doc.update(update["$set"])

    coll = MagicMock()
    coll.find_one = AsyncMock(side_effect=find_one)
    coll.update_one = AsyncMock(side_effect=update_one)
    fake = MagicMock()
    fake._db.__getitem__.return_value = coll
    return fake, store


@pytest.mark.asyncio
async def test_password_is_encrypted_and_never_leaked():
    from services import strata_web_portal_config_service as svc
    fake, store = _fake_db()
    with patch.object(svc, "db", fake):
        masked = await svc.upsert_portal_config(
            "13195",
            {"portal_url": "https://my.civium.com.au", "scheme_number": "13195",
             "username": "user@example.com", "password": "s3cr3t-portal-pw", "enabled": True},
            updated_by="admin-1",
        )
        # Masked response exposes password_set but NEVER the password or ciphertext field.
        assert masked["password_set"] is True
        assert "password" not in masked
        assert "portal_password_enc" not in masked
        assert masked["enabled"] is True
        assert masked["portal_url"] == "https://my.civium.com.au"

        # Stored value is Fernet ciphertext, not the plaintext.
        stored = store["13195"]
        assert stored["portal_password_enc"].startswith("enc:")
        assert "s3cr3t-portal-pw" not in stored["portal_password_enc"]
        assert "portal_password" not in stored or stored.get("portal_password") is None


@pytest.mark.asyncio
async def test_omitted_password_keeps_existing():
    from services import strata_web_portal_config_service as svc
    fake, store = _fake_db()
    with patch.object(svc, "db", fake):
        await svc.upsert_portal_config("13195", {"password": "first-pw", "enabled": True}, updated_by="a")
        first_ct = store["13195"]["portal_password_enc"]
        # Update other fields WITHOUT a password — ciphertext must be unchanged.
        await svc.upsert_portal_config("13195", {"username": "new@example.com"}, updated_by="a")
        assert store["13195"]["portal_password_enc"] == first_ct
        assert store["13195"]["username"] == "new@example.com"


@pytest.mark.asyncio
async def test_credentials_accessor_gated_on_enabled():
    from services import strata_web_portal_config_service as svc
    fake, store = _fake_db()
    with patch.object(svc, "db", fake):
        await svc.upsert_portal_config(
            "13195", {"portal_url": "https://x", "username": "u", "password": "pw", "enabled": True},
            updated_by="a",
        )
        creds = await svc.get_portal_credentials("13195")
        assert creds is not None
        assert creds["password"] == "pw"  # round-trips (decrypts) for the scraper boundary
        assert creds["username"] == "u"

        # Disable → the accessor returns None even though a password is stored.
        await svc.upsert_portal_config("13195", {"enabled": False}, updated_by="a")
        assert await svc.get_portal_credentials("13195") is None


@pytest.mark.asyncio
async def test_missing_building_is_empty_not_error():
    from services import strata_web_portal_config_service as svc
    fake, _ = _fake_db()
    with patch.object(svc, "db", fake):
        masked = await svc.get_portal_config_masked("99999")
        assert masked["building_id"] == "99999"
        assert masked["password_set"] is False
        assert masked["enabled"] is False
        assert await svc.get_portal_credentials("99999") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
