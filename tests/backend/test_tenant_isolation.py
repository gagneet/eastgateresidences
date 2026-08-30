import pytest

from database import TenantCollection
from request_context import set_ctx_building_id


class DummyCollection:
    def __init__(self, name: str):
        self.name = name
        self.last_pipeline = None

    def aggregate(self, pipeline, *args, **kwargs):
        self.last_pipeline = pipeline
        return pipeline


def test_inject_bid_requires_context_for_or():
    set_ctx_building_id(None)
    tc = TenantCollection(DummyCollection("announcements"))

    with pytest.raises(RuntimeError):
        tc._inject_bid({"$or": [{"status": "active"}]})


def test_inject_bid_allows_explicit_building_id_without_context():
    set_ctx_building_id(None)
    tc = TenantCollection(DummyCollection("announcements"))
    filt = {"$and": [{"status": "active"}, {"building_id": "b1"}]}
    assert tc._inject_bid(filt) == filt


def test_inject_bid_adds_building_id_with_context():
    set_ctx_building_id("b1")
    tc = TenantCollection(DummyCollection("announcements"))

    assert tc._inject_bid(None) == {"$or": [{"building_id": "b1"}, {"plan_id": "b1"}]}

    result = tc._inject_bid({"status": "active"})
    assert result == {"$and": [{"status": "active"}, {"$or": [{"building_id": "b1"}, {"plan_id": "b1"}]}]}


def test_aggregate_secures_lookup_and_union_with():
    set_ctx_building_id("b1")
    dummy_coll = DummyCollection("announcements")
    tc = TenantCollection(dummy_coll)

    pipeline = [
        {"$match": {"status": "active"}},
        {
            "$lookup": {
                "from": "documents",
                "pipeline": [{"$match": {"status": "active"}}],
                "as": "docs",
            }
        },
        {
            "$unionWith": {
                "coll": "documents",
                "pipeline": [{"$match": {"status": "active"}}],
            }
        },
    ]

    tc.aggregate(pipeline)
    # Inspect the secured pipeline that was passed to the underlying collection.
    # (Return value is an _AsyncAggregateCursorProxy, not a list — use last_pipeline.)
    secured = dummy_coll.last_pipeline

    assert secured[0] == {"$match": {"building_id": "b1"}}
    lookup_stage = secured[2]["$lookup"]
    assert lookup_stage["pipeline"][0] == {"$match": {"building_id": "b1"}}

    union_stage = secured[3]["$unionWith"]
    assert union_stage["pipeline"][0] == {"$match": {"building_id": "b1"}}
