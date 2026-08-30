"""Concurrent-account-use detection on /security/my-activity.

The feature answers "is my account signed in somewhere else?" from login_audit_logs.
Auth is a stateless JWT with no session store, so the honest claim is bounded: a
sign-in inside the token lifetime could still hold a valid session. These tests pin
the boundaries of that claim and the false-positive guards.
"""
from datetime import datetime, timedelta, timezone

import pytest

from routers.security import (
    _build_concurrent_access,
    _describe_device,
    _detect_impossible_travel,
    _haversine_km,
)

SYD = (-33.8688, 151.2093)
LON = (51.5074, -0.1278)
MEL = (-37.8136, 144.9631)


def _login(*, at, ip, city="Sydney", country="Australia", cc="AU",
           browser="Chrome", os_name="Windows", device_type="desktop", lat=None, lon=None):
    coords = {"latitude": lat, "longitude": lon} if lat is not None else {}
    return {
        "attempted_at": at,
        "ip_address": ip,
        "status": "success",
        "geo": {"city": city, "country_name": country, "country_code": cc, **coords},
        "device_info": {"browser": browser, "os": os_name, "device_type": device_type},
    }


class TestNoHistoryIsNotAnAllClear:
    def test_empty_history_is_reported_as_unavailable_not_as_zero(self):
        """The failure this guards is a reassuring lie.

        With no login rows the naive result is "0 other sources" — which renders as
        "your account is only in use here" when it actually means nothing was ever
        recorded. East Gate's login_audit_logs is empty right now, so this is the live
        state, not a hypothetical.
        """
        result = _build_concurrent_access([], current_ip="203.0.113.1", window_hours=24)
        assert result["history_available"] is False
        assert result["source_count"] == 0
        assert result["other_source_count"] == 0

    def test_history_present_sets_the_flag(self):
        logins = [_login(at="2026-08-28T10:00:00+00:00", ip="203.0.113.1")]
        result = _build_concurrent_access(logins, current_ip="203.0.113.1", window_hours=24)
        assert result["history_available"] is True


class TestSourceGrouping:
    def test_same_device_and_city_on_a_changed_ip_is_one_source(self):
        """A phone that changes IP must not read as a second location.

        device_fingerprint is sha256(ip|user_agent), so it changes on every IP change.
        Grouping on it would turn one commuting user into a fleet of unknown devices —
        which is why grouping is by device shape and place instead.
        """
        logins = [
            _login(at="2026-08-28T10:00:00+00:00", ip="203.0.113.1"),
            _login(at="2026-08-28T11:00:00+00:00", ip="203.0.113.9"),
            _login(at="2026-08-28T12:00:00+00:00", ip="198.51.100.7"),
        ]
        result = _build_concurrent_access(logins, current_ip="203.0.113.1", window_hours=24)
        assert result["source_count"] == 1
        assert result["other_source_count"] == 0
        assert sorted(result["sources"][0]["ip_addresses"]) == ["198.51.100.7", "203.0.113.1", "203.0.113.9"]
        assert result["sources"][0]["login_count"] == 3

    def test_a_different_device_in_a_different_city_is_a_second_source(self):
        logins = [
            _login(at="2026-08-28T10:00:00+00:00", ip="203.0.113.1"),
            _login(at="2026-08-28T11:00:00+00:00", ip="198.51.100.4",
                   city="Perth", browser="Safari", os_name="iOS", device_type="mobile"),
        ]
        result = _build_concurrent_access(logins, current_ip="203.0.113.1", window_hours=24)
        assert result["source_count"] == 2
        assert result["other_source_count"] == 1
        other = [s for s in result["sources"] if not s["is_current_ip"]][0]
        assert other["city"] == "Perth"
        assert other["device"] == "Safari on iOS"

    def test_unknown_ua_fields_do_not_fragment_one_source(self):
        """parse_user_agent returns the STRING "Unknown", not None.

        Left raw, real rows render as "Unknown on Unknown" and — worse — a source whose
        city is "Unknown" splits from one whose city is missing, inflating the count of
        places the account appears to be in use.
        """
        logins = [
            _login(at="2026-08-28T10:00:00+00:00", ip="203.0.113.1",
                   browser="Unknown", os_name="Unknown", city="Unknown"),
            _login(at="2026-08-28T11:00:00+00:00", ip="203.0.113.2",
                   browser=None, os_name="", city=None),
        ]
        result = _build_concurrent_access(logins, current_ip="203.0.113.1", window_hours=24)
        assert result["source_count"] == 1
        assert result["sources"][0]["device"] == "Unrecognised client"
        assert result["sources"][0]["city"] is None

    def test_current_ip_marks_exactly_the_matching_source(self):
        logins = [
            _login(at="2026-08-28T10:00:00+00:00", ip="203.0.113.1"),
            _login(at="2026-08-28T11:00:00+00:00", ip="198.51.100.4", city="Perth", browser="Safari"),
        ]
        result = _build_concurrent_access(logins, current_ip="198.51.100.4", window_hours=24)
        assert [s["is_current_ip"] for s in result["sources"]].count(True) == 1
        assert next(s for s in result["sources"] if s["is_current_ip"])["city"] == "Perth"

    def test_sources_are_ordered_most_recent_first(self):
        logins = [
            _login(at="2026-08-28T09:00:00+00:00", ip="203.0.113.1"),
            _login(at="2026-08-28T18:00:00+00:00", ip="198.51.100.4", city="Perth", browser="Safari"),
        ]
        result = _build_concurrent_access(logins, current_ip="203.0.113.1", window_hours=24)
        assert result["sources"][0]["city"] == "Perth"


class TestImpossibleTravel:
    def test_sydney_to_london_in_two_hours_is_flagged(self):
        logins = [
            _login(at="2026-08-28T00:00:00+00:00", ip="1.1.1.1", lat=SYD[0], lon=SYD[1]),
            _login(at="2026-08-28T02:00:00+00:00", ip="2.2.2.2", city="London",
                   country="United Kingdom", cc="GB", lat=LON[0], lon=LON[1]),
        ]
        travel = _detect_impossible_travel(logins)
        assert travel is not None
        assert travel["implied_speed_kmh"] > 900
        assert travel["from"]["city"] == "Sydney"
        assert travel["to"]["city"] == "London"

    def test_sydney_to_london_over_a_day_is_ordinary_travel(self):
        """A real flight must not be reported as a compromise."""
        logins = [
            _login(at="2026-08-28T00:00:00+00:00", ip="1.1.1.1", lat=SYD[0], lon=SYD[1]),
            _login(at="2026-08-29T00:00:00+00:00", ip="2.2.2.2", city="London",
                   country="United Kingdom", cc="GB", lat=LON[0], lon=LON[1]),
        ]
        assert _detect_impossible_travel(logins) is None

    def test_geoip_jitter_within_a_city_is_not_travel(self):
        """City-level GeoIP disagrees by tens of km on the same connection."""
        logins = [
            _login(at="2026-08-28T00:00:00+00:00", ip="1.1.1.1", lat=-33.87, lon=151.21),
            _login(at="2026-08-28T00:05:00+00:00", ip="1.1.1.2", lat=-33.90, lon=151.18),
        ]
        assert _detect_impossible_travel(logins) is None

    def test_missing_coordinates_yield_no_claim(self):
        """Absent geo must produce silence, never a default of 'fine'."""
        logins = [
            _login(at="2026-08-28T00:00:00+00:00", ip="1.1.1.1"),
            _login(at="2026-08-28T00:05:00+00:00", ip="2.2.2.2", city="London"),
        ]
        assert _detect_impossible_travel(logins) is None

    def test_identical_timestamps_do_not_divide_by_zero(self):
        logins = [
            _login(at="2026-08-28T00:00:00+00:00", ip="1.1.1.1", lat=SYD[0], lon=SYD[1]),
            _login(at="2026-08-28T00:00:00+00:00", ip="2.2.2.2", city="London",
                   country="United Kingdom", cc="GB", lat=LON[0], lon=LON[1]),
        ]
        assert _detect_impossible_travel(logins) is None

    def test_worst_pair_is_reported_when_several_qualify(self):
        logins = [
            _login(at="2026-08-28T00:00:00+00:00", ip="1.1.1.1", lat=SYD[0], lon=SYD[1]),
            _login(at="2026-08-28T00:30:00+00:00", ip="2.2.2.2", city="Melbourne",
                   lat=MEL[0], lon=MEL[1]),
            _login(at="2026-08-28T00:45:00+00:00", ip="3.3.3.3", city="London",
                   country="United Kingdom", cc="GB", lat=LON[0], lon=LON[1]),
        ]
        travel = _detect_impossible_travel(logins)
        assert travel["to"]["city"] == "London"


class TestHaversine:
    def test_known_distance_sydney_to_london(self):
        km = _haversine_km(*SYD, *LON)
        assert 16_900 < km < 17_050, f"expected ~16,990 km, got {km:.0f}"

    def test_zero_distance(self):
        assert _haversine_km(*SYD, *SYD) == pytest.approx(0.0, abs=1e-6)


class TestDescribeDevice:
    @pytest.mark.parametrize("info,expected", [
        ({"browser": "Chrome", "os": "Windows"}, "Chrome on Windows"),
        ({"browser": "Chrome", "os": None}, "Chrome"),
        ({"browser": "Unknown", "os": "Unknown"}, "Unrecognised client"),
        ({}, "Unrecognised client"),
    ])
    def test_labels(self, info, expected):
        assert _describe_device({"device_info": info}) == expected

    def test_missing_device_info_key_entirely(self):
        assert _describe_device({}) == "Unrecognised client"
