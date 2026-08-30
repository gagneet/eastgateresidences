# @featuretrace:progressive-navigation — Canonical building scope and permission hydration coverage.
# Data flow: FastAPI dependency metadata → navigation endpoints → get_current_building.
# Related: backend/routers/navigation.py

from __future__ import annotations

import inspect

from fastapi.params import Depends

from routers import navigation
from utils.auth import get_current_building


def _dependency_for(endpoint, parameter_name: str):
    parameter = inspect.signature(endpoint).parameters[parameter_name]
    assert isinstance(parameter.default, Depends)
    return parameter.default.dependency


def test_all_navigation_endpoints_use_canonical_building_dependency():
    endpoints = (
        navigation.get_nav_config,
        navigation.upsert_preferences,
        navigation.track_navigation,
        navigation.get_nav_badges,
    )

    for endpoint in endpoints:
        assert _dependency_for(endpoint, "building_id") is get_current_building


def test_navigation_router_has_no_permissive_header_resolver():
    assert not hasattr(navigation, "_resolve_building_id")


def test_navigation_permission_flags_are_resolved_from_permission_model():
    source = inspect.getsource(navigation.get_nav_config)

    assert "get_user_permissions(current_user).model_dump()" in source
    assert "current_user.items()" not in source
