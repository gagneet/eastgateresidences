# @featuretrace:security-ip-logging — Extra request signals captured on each login attempt.
# Layer: service
# Data flow: Request headers -> collect_login_signals() -> login_audit_logs.signals
#            -> /security/login-activity -> Security & IP Logs page (global).
# Related: backend/utils/client_ip.py
#          backend/utils/geo.py
#          backend/routers/auth.py
#          backend/routers/security.py
# Tests: tests/backend/test_login_signals.py

"""Capture what the browser already tells us, beyond IP and User-Agent.

## Why bother

The login audit row records IP, UA, geo and a risk score. That is enough to say
*where* a login came from and almost nothing about *how plausible* it is. The
headers below arrive on every request at no cost and materially improve the
answer, and each one is chosen because it changes a decision an operator would
make — not because it is available.

| Signal | What it is worth |
|---|---|
| Client Hints (``Sec-CH-UA*``) | The browser stating its own identity, rather than us sniffing a UA string that browsers deliberately freeze and lie in. When present it is strictly better evidence than the UA. |
| ``Accept-Language`` | A stable per-user trait. A sudden change on an otherwise-normal account is a classic session-hijack tell. |
| ``Origin`` / ``Referer`` | Where the login form was served from. A successful login whose Origin is not our own domain is the signature of a phishing proxy replaying credentials. |
| ``X-Forwarded-Proto`` | Whether the request reached the edge over TLS. A plaintext login is a finding in itself. |
| ASN / hosting flag | A residential ISP versus a datacentre ASN separates "the owner logged in from home" from "something logged in from a VPS". The single highest-value anomaly signal here. |
| ``DNT`` / ``Sec-GPC`` | Privacy preferences worth honouring, and stable enough to corroborate a device. |

## What is deliberately NOT captured

- **No client-side fingerprinting.** Canvas, WebGL, font enumeration and the
  like are invasive, are what privacy regulators single out, and would make this
  system a tracking tool. Everything here is a header the browser sends anyway.
- **No full cookie or token contents**, obviously.
- **Nothing that requires JavaScript cooperation**, because a login page under
  attack is exactly where injected JavaScript cannot be trusted.

Header values are attacker-controlled, so every field is length-capped and
stored as data only. Nothing here is ever used to make an authorisation
decision — its job is to inform a human reviewing the log.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Hard cap on any single captured header value. These are attacker-controlled
#: and land in an append-only security log; an unbounded Referer would let a
#: caller write arbitrary bulk into it.
MAX_VALUE_LENGTH = 256

#: Datacentre/hosting ASN keywords. A login from one of these is not
#: automatically bad — VPNs are legitimate — but it is never the same thing as a
#: login from a residential ISP, and the log should not present them alike.
_HOSTING_ASN_HINTS = (
    "amazon", "aws", "google", "microsoft", "azure", "digitalocean", "linode",
    "ovh", "hetzner", "vultr", "cloudflare", "oracle", "alibaba", "tencent",
    "leaseweb", "choopa", "contabo", "scaleway", "m247", "datacamp",
    "hosting", "datacenter", "data center", "colo", "vpn", "proxy",
)


def _clip(value: Any) -> str | None:
    """Trim and cap a header value; empty becomes None so absence stays visible."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:MAX_VALUE_LENGTH]


def _first_language(accept_language: str | None) -> str | None:
    """Return the highest-priority language tag from an Accept-Language header.

    ``en-AU,en;q=0.9,fr;q=0.8`` -> ``en-AU``. The full header is kept separately;
    this is the part worth comparing between logins.
    """
    if not accept_language:
        return None
    first = accept_language.split(",")[0].strip()
    return first.split(";")[0].strip() or None


def is_hosting_provider(isp: str | None) -> bool:
    """True when the ISP name looks like a datacentre rather than a home ISP.

    Deliberately a keyword match rather than a bought dataset: it is transparent,
    has no licence cost, and errs toward flagging. A false positive costs a
    second glance at a log row; a false negative hides a VPS login among
    residential ones.
    """
    if not isp:
        return False
    lowered = isp.lower()
    return any(hint in lowered for hint in _HOSTING_ASN_HINTS)


def collect_login_signals(request: Any, *, expected_origins: tuple[str, ...] = ()) -> dict:
    """Gather the extra request signals for one login attempt.

    Never raises — a signal-collection failure must not block a login, and an
    empty dict degrades the log rather than the auth flow.

    ``expected_origins`` lets the caller pass the app's own front-end origins so
    ``origin_matches_site`` can be computed. When none are supplied the field is
    ``None`` (unknown), never ``False``, because "we did not check" and "it did
    not match" are different findings.
    """
    try:
        headers = request.headers
        accept_language = _clip(headers.get("Accept-Language"))
        origin = _clip(headers.get("Origin"))
        referer = _clip(headers.get("Referer"))

        origin_matches: bool | None = None
        if expected_origins:
            probe = origin or referer
            origin_matches = bool(probe) and any(
                probe.lower().startswith(expected.lower()) for expected in expected_origins
            )

        return {
            # Client Hints — the browser's own statement of platform and form
            # factor. Present on Chromium; absent on Safari/Firefox, where None
            # correctly means "not offered" rather than "not a browser".
            "ch_ua": _clip(headers.get("Sec-CH-UA")),
            "ch_platform": _clip(headers.get("Sec-CH-UA-Platform")),
            "ch_platform_version": _clip(headers.get("Sec-CH-UA-Platform-Version")),
            "ch_mobile": _clip(headers.get("Sec-CH-UA-Mobile")),
            "ch_model": _clip(headers.get("Sec-CH-UA-Model")),

            "accept_language": accept_language,
            "primary_language": _first_language(accept_language),

            # Phishing tell: a successful login whose Origin is not our own site
            # is the signature of a credential-replaying proxy.
            "origin": origin,
            "referer": referer,
            "origin_matches_site": origin_matches,

            # A plaintext login is a finding regardless of anything else.
            "forwarded_proto": _clip(headers.get("X-Forwarded-Proto")),

            # Privacy preferences: stable per-device, and worth honouring.
            "dnt": _clip(headers.get("DNT")),
            "sec_gpc": _clip(headers.get("Sec-GPC")),

            # Fetch metadata — a login POST should be same-origin from a document.
            # Anything else suggests it was driven from somewhere unexpected.
            "sec_fetch_site": _clip(headers.get("Sec-Fetch-Site")),
            "sec_fetch_mode": _clip(headers.get("Sec-Fetch-Mode")),
        }
    except Exception:  # noqa: BLE001 — signals are a nice-to-have, auth is not
        logger.warning("login signal collection failed; storing none", exc_info=True)
        return {}
