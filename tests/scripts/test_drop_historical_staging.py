from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "drop_historical_staging",
        ROOT / "scripts" / "data_cleanup" / "drop_historical_staging.py",
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
    def __init__(self, data: dict[str, list[dict]]):
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
        "historical_annual_levies": [{"_id": "a1"}, {"_id": "a2"}],
        "historical_levy_issuances": [{"_id": "i1"}],
        "historical_financial_snapshots": [{"_id": "s1"}, {"_id": "s2"}, {"_id": "s3"}],
    })


def test_dry_run_does_not_delete():
    db = _make_db()

    counts = mod.run_cleanup(
        db,
        read_from_postgres_enabled=True,
        divergence_dates=set(),
        cutover_recorded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        dry_run=True,
        confirm=False,
        as_of_date=date(2026, 5, 20),
    )

    assert counts == {
        "historical_annual_levies": 2,
        "historical_levy_issuances": 1,
        "historical_financial_snapshots": 3,
    }
    assert db.dropped == []


def test_refuses_when_toggle_disabled():
    db = _make_db()

    with pytest.raises(SystemExit, match="read_from_postgres is not enabled"):
        mod.run_cleanup(
            db,
            read_from_postgres_enabled=False,
            divergence_dates=set(),
            cutover_recorded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            dry_run=False,
            confirm=True,
            as_of_date=date(2026, 5, 20),
        )


def test_refuses_before_seven_day_cutover_window():
    db = _make_db()

    with pytest.raises(SystemExit, match="7-day cutover window has not elapsed"):
        mod.run_cleanup(
            db,
            read_from_postgres_enabled=True,
            divergence_dates=set(),
            cutover_recorded_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
            dry_run=False,
            confirm=True,
            as_of_date=date(2026, 5, 20),
        )


def test_refuses_when_parity_window_has_divergence():
    db = _make_db()

    with pytest.raises(SystemExit, match="last 7 days are not divergence-free"):
        mod.run_cleanup(
            db,
            read_from_postgres_enabled=True,
            divergence_dates={date(2026, 5, 18)},
            cutover_recorded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            dry_run=False,
            confirm=True,
            as_of_date=date(2026, 5, 20),
        )


def test_confirmation_mismatch_refuses():
    db = _make_db()
    answers = iter(["historical_annual_levies", "wrong-name"])

    with pytest.raises(SystemExit, match="Confirmation mismatch"):
        mod.run_cleanup(
            db,
            read_from_postgres_enabled=True,
            divergence_dates=set(),
            cutover_recorded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            dry_run=False,
            confirm=True,
            as_of_date=date(2026, 5, 20),
            input_func=lambda _prompt: next(answers),
        )

    assert db.dropped == []


def test_full_drop_after_gate_passes():
    db = _make_db()
    answers = iter(list(mod.HISTORICAL_COLLECTIONS))

    counts = mod.run_cleanup(
        db,
        read_from_postgres_enabled=True,
        divergence_dates=set(),
        cutover_recorded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        dry_run=False,
        confirm=True,
        as_of_date=date(2026, 5, 20),
        input_func=lambda _prompt: next(answers),
    )

    assert counts["historical_annual_levies"] == 2
    assert db.dropped == list(mod.HISTORICAL_COLLECTIONS)
