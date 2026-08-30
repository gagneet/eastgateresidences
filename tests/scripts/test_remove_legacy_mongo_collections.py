"""Tests for scripts/data_cleanup/remove_legacy_mongo_collections.py."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "remove_legacy_mongo_collections",
        ROOT / "scripts" / "data_cleanup" / "remove_legacy_mongo_collections.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


class FakeCollection:
    def __init__(self, docs: list[dict]):
        self.docs = [dict(doc) for doc in docs]

    def find(self, *_args, **_kwargs):
        return [dict(doc) for doc in self.docs]


class FakeDatabase:
    def __init__(self, data: dict[str, list[dict]], name: str = "strata_test"):
        self.name = name
        self._collections = {
            collection_name: FakeCollection(docs)
            for collection_name, docs in data.items()
        }
        self.dropped: list[str] = []

    def __getitem__(self, collection_name: str) -> FakeCollection:
        return self._collections.setdefault(collection_name, FakeCollection([]))

    def drop_collection(self, collection_name: str) -> None:
        self.dropped.append(collection_name)
        self._collections[collection_name] = FakeCollection([])


def _make_db() -> FakeDatabase:
    return FakeDatabase({
        "organisations": [
            {"_id": "org-1", "name": "Legacy Org"},
            {"_id": "org-2", "name": "Legacy Org 2"},
        ],
        "trial_requests": [
            {"_id": "trial-1", "contact_email": "trial@example.com"},
        ],
    })


def test_dry_run_does_not_delete(caplog):
    db = _make_db()

    with caplog.at_level("INFO"):
        counts = mod.run_cleanup(db, dry_run=True, confirm=False)

    assert counts == {"organisations": 2, "trial_requests": 1}
    assert db.dropped == []
    assert len(db["organisations"].docs) == 2
    assert len(db["trial_requests"].docs) == 1


def test_missing_mongo_env_vars_refuse(monkeypatch):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)

    with pytest.raises(SystemExit, match="MONGO_URL and DB_NAME environment variables must be set"):
        mod._get_database()


def test_confirm_without_collection_name_refuses():
    db = _make_db()
    answers = iter(["organisations", "wrong-name"])

    with pytest.raises(SystemExit, match="Confirmation mismatch"):
        mod.run_cleanup(
            db,
            dry_run=False,
            confirm=True,
            today=date(2026, 6, 5),
            input_func=lambda _prompt: next(answers),
        )

    assert db.dropped == []


def test_stability_gate_blocks_before_target_date():
    db = _make_db()

    with pytest.raises(SystemExit, match="Refusing destructive cleanup before 2026-06-04"):
        mod.run_cleanup(
            db,
            dry_run=False,
            confirm=True,
            today=date(2026, 6, 3),
            input_func=lambda _prompt: "ignored",
        )

    assert db.dropped == []


def test_production_env_blocks_any_run():
    db = _make_db()

    with pytest.raises(SystemExit, match="Refusing to run legacy Mongo cleanup against production"):
        mod.run_cleanup(
            db,
            dry_run=True,
            confirm=False,
            app_env="production",
            db_name="strata_production",
        )

    assert db.dropped == []


def test_override_flag_bypasses_gate():
    db = _make_db()
    answers = iter(["organisations", "trial_requests"])

    counts = mod.run_cleanup(
        db,
        dry_run=False,
        confirm=True,
        override_stability_gate=True,
        today=date(2026, 6, 3),
        input_func=lambda _prompt: next(answers),
    )

    assert counts == {"organisations": 2, "trial_requests": 1}
    assert db.dropped == ["organisations", "trial_requests"]


def test_full_deletion_against_test_database(caplog):
    db = _make_db()
    answers = iter(["organisations", "trial_requests"])

    with caplog.at_level("INFO"):
        counts = mod.run_cleanup(
            db,
            dry_run=False,
            confirm=True,
            today=date(2026, 6, 5),
            input_func=lambda _prompt: next(answers),
        )

    assert counts == {"organisations": 2, "trial_requests": 1}
    assert db.dropped == ["organisations", "trial_requests"]
    assert db["organisations"].docs == []
    assert db["trial_requests"].docs == []
    assert "collection=organisations _id=org-1" in caplog.text
    assert "collection=organisations _id=org-2" in caplog.text
    assert "collection=trial_requests _id=trial-1" in caplog.text
