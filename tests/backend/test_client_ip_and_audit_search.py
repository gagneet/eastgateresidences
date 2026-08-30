"""Public/local IP resolution, client detection, and the audit search grammar.

Three defects reported on 2026-08-24, all on the security log:

* the dashboard showed an **internal** address with no way to tell whether the
  proxy sent no header, sent one that was distrusted, or the caller really was
  local;
* the Device column read **"Unknown"** for every row, because every recent login
  is a ``python-requests`` probe and a non-browser UA fell through the browser
  sniffing;
* the search box matched a substring of **email only**, so "everything except
  the monitoring probe" — the first thing anyone asks of a security log — was
  unaskable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from utils.audit_search import SEARCH_HELP, parse_audit_query  # noqa: E402
from utils.client_ip import ClientIPs, ip_fields, resolve_client_ips  # noqa: E402
from utils.geo import parse_user_agent  # noqa: E402
from utils.login_signals import collect_login_signals, is_hosting_provider  # noqa: E402


class _Request:
    """Minimal stand-in with the two attributes the resolvers read."""

    def __init__(self, peer: str | None, headers: dict | None = None):
        self.client = type("C", (), {"host": peer})() if peer else None
        self.headers = headers or {}


# ── Public vs local IP ───────────────────────────────────────────────────────

def test_behind_a_trusted_proxy_both_addresses_are_kept():
    """The reported bug: only the proxy's address was stored."""
    resolved = resolve_client_ips(_Request("127.0.0.1", {"X-Forwarded-For": "118.210.60.180"}))

    assert resolved.public_ip == "118.210.60.180"
    assert resolved.local_ip == "127.0.0.1"
    assert resolved.display == "118.210.60.180 (127.0.0.1)"


def test_no_forwarded_header_records_no_public_ip():
    """The diagnosis that was previously invisible.

    public_ip is None — NOT backfilled with the proxy address — because "no
    public address was established" is exactly the finding an operator needs.
    """
    resolved = resolve_client_ips(_Request("127.0.0.1", {}))

    assert resolved.public_ip is None
    assert resolved.local_ip == "127.0.0.1"
    assert resolved.display == "127.0.0.1"


def test_an_untrusted_peer_cannot_forge_a_public_ip():
    """The trust rule is inherited from geo.get_real_ip and must not relax.

    A peer that is not a known proxy setting X-Forwarded-For is claiming to be
    someone else; it is recorded as itself.
    """
    resolved = resolve_client_ips(_Request("198.51.100.7", {"X-Forwarded-For": "1.2.3.4"}))

    assert resolved.public_ip != "1.2.3.4"


def test_cloudflare_header_wins_over_x_real_ip():
    """With Cloudflare in front of nginx, X-Real-IP is the Cloudflare edge."""
    resolved = resolve_client_ips(_Request("127.0.0.1", {
        "CF-Connecting-IP": "118.210.60.180",
        "X-Real-IP": "172.68.1.1",
    }))

    assert resolved.public_ip == "118.210.60.180"


def test_a_proxy_chain_skips_private_hops():
    """X-Forwarded-For is a chain; a corporate proxy may prepend its own address.

    Taking only the leftmost entry would record 192.168.1.50 as the public IP.
    """
    resolved = resolve_client_ips(
        _Request("127.0.0.1", {"X-Forwarded-For": "192.168.1.50, 118.210.60.180"})
    )

    assert resolved.public_ip == "118.210.60.180"
    assert resolved.local_ip == "192.168.1.50"


@pytest.mark.parametrize("peer", ["::1", "::ffff:127.0.0.1", "172.18.0.1", "10.0.0.7"])
def test_ipv6_loopback_and_container_addresses_are_local_not_public(peer):
    """``::1`` is IPv6 loopback — the request never left the host.

    Reported directly: "What can we do about IP Address like ::1?" It is
    classified as local, which is what it is, and the row then shows whether a
    public address was established alongside it.
    """
    resolved = resolve_client_ips(_Request(peer, {}))

    assert resolved.public_ip is None
    assert resolved.local_ip is not None


def test_a_direct_public_client_is_not_duplicated():
    """No proxy means no separate local address worth printing twice."""
    resolved = resolve_client_ips(_Request("118.210.60.180", {}))

    assert resolved.display == "118.210.60.180"


@pytest.mark.parametrize("junk", ["not-an-ip", "", "unknown", "999.999.999.999"])
def test_garbage_forwarded_values_are_ignored(junk):
    """Header values are attacker-controlled; an unparseable one is not an IP."""
    resolved = resolve_client_ips(_Request("127.0.0.1", {"X-Forwarded-For": junk}))

    assert resolved.public_ip is None


def test_a_port_suffix_is_stripped():
    """Some proxies append ``:port`` to the forwarded address."""
    resolved = resolve_client_ips(_Request("127.0.0.1", {"X-Forwarded-For": "118.210.60.180:51515"}))

    assert resolved.public_ip == "118.210.60.180"


def test_resolution_never_raises():
    """IP resolution must never be able to break a login."""
    class Broken:
        @property
        def client(self):
            raise RuntimeError("boom")
        headers = {}

    assert resolve_client_ips(Broken()) == ClientIPs(None, None)


def test_ip_fields_keeps_the_legacy_column_meaning():
    """``ip_address`` must still hold what every existing reader expects."""
    fields = ip_fields(_Request("127.0.0.1", {"X-Forwarded-For": "118.210.60.180"}))

    assert fields["ip_address"] == "118.210.60.180"
    assert fields["public_ip"] == "118.210.60.180"
    assert fields["local_ip"] == "127.0.0.1"
    assert fields["ip_display"] == "118.210.60.180 (127.0.0.1)"


def test_ip_address_falls_back_to_local_never_empty():
    """"unknown" is a statement; "" reads as a field nobody filled in."""
    assert ip_fields(_Request(None, {}))["ip_address"] == "unknown"


# ── Device detection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ua,expected_label", [
    ("python-requests/2.34.2", "Python requests"),
    ("curl/8.4.0", "curl"),
    ("PostmanRuntime/7.36.0", "Postman"),
    ("Go-http-client/2.0", "Go HTTP client"),
])
def test_non_browser_clients_are_named_not_unknown(ua, expected_label):
    """Every recent row in login_audit_logs is python-requests.

    Reporting those as an unknown desktop browser buried the single most useful
    fact — that they are script logins — and sent the operator hunting a parser
    bug that does not exist.
    """
    parsed = parse_user_agent(ua)

    assert parsed["device_type"] == "api"
    assert parsed["browser"] == expected_label


def test_a_missing_user_agent_is_unknown_not_desktop():
    """Claiming a device we never observed is a fabrication.

    Browsers always send a UA, so its absence is itself a signal.
    """
    assert parse_user_agent("")["device_type"] == "unknown"


@pytest.mark.parametrize("ua,device", [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "desktop"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1", "mobile"),
    ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1", "tablet"),
])
def test_real_browsers_still_classify_correctly(ua, device):
    """The API-client branch must not swallow genuine browsers."""
    assert parse_user_agent(ua)["device_type"] == device


# ── Extra login signals ──────────────────────────────────────────────────────

def test_signals_capture_client_hints_and_language():
    """Generated function header.

    Function: test_signals_capture_client_hints_and_language
    Path: tests/backend/test_client_ip_and_audit_search.py
    """
    signals = collect_login_signals(_Request("127.0.0.1", {
        "Sec-CH-UA-Platform": '"macOS"',
        "Sec-CH-UA-Mobile": "?0",
        "Accept-Language": "en-AU,en;q=0.9,fr;q=0.8",
    }))

    assert signals["ch_platform"] == '"macOS"'
    assert signals["primary_language"] == "en-AU"


def test_origin_match_is_none_when_not_checked():
    """"we did not check" and "it did not match" are different findings."""
    assert collect_login_signals(_Request("127.0.0.1", {"Origin": "https://evil.test"}))[
        "origin_matches_site"
    ] is None


def test_origin_mismatch_is_detected_when_expected_origins_are_supplied():
    """A successful login from a foreign Origin is the phishing-proxy signature."""
    signals = collect_login_signals(
        _Request("127.0.0.1", {"Origin": "https://evil.test"}),
        expected_origins=("https://www.eastgateresidences.com.au",),
    )
    assert signals["origin_matches_site"] is False


def test_header_values_are_length_capped():
    """These are attacker-controlled and land in an append-only log."""
    signals = collect_login_signals(_Request("127.0.0.1", {"Referer": "https://x/" + "a" * 5000}))
    assert len(signals["referer"]) <= 256


@pytest.mark.parametrize("isp,expected", [
    ("Amazon Technologies Inc.", True),
    ("DigitalOcean, LLC", True),
    ("Telstra Internet", False),
    ("TPG Telecom", False),
    (None, False),
])
def test_hosting_provider_detection(isp, expected):
    """A VPS login and a home login are not the same event."""
    assert is_hosting_provider(isp) is expected


# ── Search grammar ───────────────────────────────────────────────────────────

def test_exclusion_is_the_whole_point():
    """The reported request: "if I do not want the IP 192.0.2.1"."""
    for query in ("ip!=192.0.2.1", "-ip:192.0.2.1"):
        parsed, unknown = parse_audit_query(query)
        assert unknown == []
        assert "$nor" in str(parsed)
        assert "192" in str(parsed)


def test_equality_is_anchored():
    """Unanchored, ``ip:10.0.0.1`` would also match 10.0.0.10."""
    parsed, _ = parse_audit_query("ip:10.0.0.1")
    assert parsed["ip_address"]["$regex"].startswith("^")
    assert parsed["ip_address"]["$regex"].endswith("$")


def test_contains_operator_is_available_when_wanted():
    """Generated function header.

    Function: test_contains_operator_is_available_when_wanted
    Path: tests/backend/test_client_ip_and_audit_search.py
    """
    parsed, _ = parse_audit_query("email~=eastgate")
    assert not parsed["email"]["$regex"].startswith("^")


def test_terms_combine_with_and():
    """Generated function header.

    Function: test_terms_combine_with_and
    Path: tests/backend/test_client_ip_and_audit_search.py
    """
    parsed, _ = parse_audit_query("country:AU -city:Canberra")
    assert "$and" in parsed
    assert len(parsed["$and"]) == 2


def test_a_bare_word_searches_identity_fields():
    """Generated function header.

    Function: test_a_bare_word_searches_identity_fields
    Path: tests/backend/test_client_ip_and_audit_search.py
    """
    parsed, _ = parse_audit_query("anthony")
    fields = {list(clause)[0] for clause in parsed["$or"]}
    assert "email" in fields and "ip_address" in fields


def test_regex_metacharacters_in_a_search_term_are_escaped():
    """Search text is user input reaching a database query.

    Unescaped, ``.*`` would match everything and read as "the filter is broken".
    """
    parsed, _ = parse_audit_query("ip:.*")
    assert parsed["ip_address"]["$regex"] == r"^\.\*$"


def test_an_unknown_field_is_reported_not_ignored():
    """A typo silently matching everything is the worst failure for a security tool."""
    parsed, unknown = parse_audit_query("adress:x")

    assert unknown == ["adress"]
    assert parsed == {}


def test_numeric_comparison_on_risk():
    """Generated function header.

    Function: test_numeric_comparison_on_risk
    Path: tests/backend/test_client_ip_and_audit_search.py
    """
    parsed, _ = parse_audit_query("risk:>=50")
    assert parsed == {"risk_score": {"$gte": 50}}


def test_quoted_values_survive_spaces():
    """Generated function header.

    Function: test_quoted_values_survive_spaces
    Path: tests/backend/test_client_ip_and_audit_search.py
    """
    parsed, _ = parse_audit_query('city:"New South Wales"')
    assert "New" in parsed["geo.city"]["$regex"]


def test_empty_search_filters_nothing():
    """Generated function header.

    Function: test_empty_search_filters_nothing
    Path: tests/backend/test_client_ip_and_audit_search.py
    """
    for value in (None, "", "   "):
        assert parse_audit_query(value) == ({}, [])


def test_the_help_payload_documents_every_supported_field():
    """The UI renders this, so it must not drift from the parser."""
    from utils.audit_search import FIELD_MAP

    assert set(SEARCH_HELP["fields"]) == set(FIELD_MAP)
    assert SEARCH_HELP["examples"], "the help panel needs worked examples"
    for example in SEARCH_HELP["examples"]:
        _parsed, unknown = parse_audit_query(example["query"])
        assert unknown == [], f"documented example {example['query']!r} uses an unknown field"
