# @featuretrace:security-ip-logging — Geo lookup, device/UA parsing and device fingerprinting.
# Layer: service
# Data flow: Request UA + IP -> parse_user_agent()/lookup_geo() -> login_audit_logs.device_info/geo (global).
# Related: backend/utils/client_ip.py
#          backend/routers/auth.py
# Tests: tests/backend/test_client_ip_and_audit_search.py
"""
Geo utility for IP resolution, user-agent parsing, device fingerprinting,
and MaxMind GeoLite2 geolocation lookups.

Client IP selection is delegated to utils.client_ip so audit, rate-limit, and
request-metadata consumers share the same trusted-proxy and parsing policy.
"""

import hashlib
import os
import re

import logging
from fastapi import Request
from typing import Optional

from utils.client_ip import resolve_client_ips

logger = logging.getLogger(__name__)


# MaxMind DB paths — check env var, then user home, then system path
def _find_db(env_var: str, filename: str) -> str:
    """Generated function header.

    Function: _find_db
    Path: backend/utils/geo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if os.getenv(env_var):
        return os.getenv(env_var)
    candidates = [
        f"/home/gagneet/GeoIP/{filename}",
        f"/usr/share/GeoIP/{filename}",
        f"/var/lib/GeoIP/{filename}",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]  # will fail gracefully when opened


_CITY_DB_PATH = _find_db("GEOIP_CITY_DB", "GeoLite2-City.mmdb")
_ASN_DB_PATH = _find_db("GEOIP_ASN_DB", "GeoLite2-ASN.mmdb")

# Lazy-loaded readers
_city_reader = None
_asn_reader = None


def _get_city_reader():
    """Generated function header.

    Function: _get_city_reader
    Path: backend/utils/geo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    global _city_reader
    if _city_reader is None:
        try:
            import geoip2.database
            _city_reader = geoip2.database.Reader(_CITY_DB_PATH)
        except Exception as e:
            logger.warning(f"GeoLite2-City DB not available: {e}")
    return _city_reader


def _get_asn_reader():
    """Generated function header.

    Function: _get_asn_reader
    Path: backend/utils/geo.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    global _asn_reader
    if _asn_reader is None:
        try:
            import geoip2.database
            _asn_reader = geoip2.database.Reader(_ASN_DB_PATH)
        except Exception as e:
            logger.warning(f"GeoLite2-ASN DB not available: {e}")
    return _asn_reader


def get_real_ip(request: Request) -> str:
    """Compatibility wrapper around the canonical client-IP resolver."""
    return resolve_client_ips(request).best


#: Non-browser clients, matched before browser sniffing. A script, uptime probe
#: or API consumer is not an "Unknown desktop browser" — calling it that hides
#: the most useful fact about the row. Ordered most specific first.
_API_CLIENT_SIGNATURES = (
    ("python-requests", "Python requests"),
    ("python-httpx", "Python httpx"),
    ("aiohttp", "Python aiohttp"),
    ("postmanruntime", "Postman"),
    ("insomnia", "Insomnia"),
    ("curl/", "curl"),
    ("wget/", "Wget"),
    ("okhttp", "OkHttp"),
    ("axios/", "Axios"),
    ("node-fetch", "node-fetch"),
    ("go-http-client", "Go HTTP client"),
    ("java/", "Java client"),
    ("powershell", "PowerShell"),
    ("uptime", "Uptime monitor"),
    ("pingdom", "Pingdom"),
    ("bot", "Bot/crawler"),
    ("spider", "Bot/crawler"),
    ("headlesschrome", "Headless Chrome"),
    ("playwright", "Playwright"),
    ("puppeteer", "Puppeteer"),
)


def parse_user_agent(ua_string: str) -> dict:
    """
    Parse User-Agent string into device/browser/OS info.
    Returns dict with: browser, browser_version, os, os_version, device_type

    ``device_type`` is one of ``desktop``, ``mobile``, ``tablet``, ``api`` or
    ``unknown``.

    Two corrections over the original, both driven by real rows in
    ``login_audit_logs`` that read "Unknown / desktop":

    * A missing User-Agent now yields ``device_type="unknown"``, not
      ``"desktop"``. Claiming a device we never observed is a fabrication, and
      an absent UA is itself a signal — browsers always send one.
    * Non-browser clients are identified by name. Every recent login row in this
      database carries ``python-requests/2.34.2``; reporting that as an unknown
      desktop browser buried the fact that these are script logins, and sent the
      operator looking for a parser bug that does not exist.
    """
    if not ua_string:
        return {"browser": "Unknown", "browser_version": "", "os": "Unknown", "os_version": "",
                "device_type": "unknown"}

    ua = ua_string.lower()

    # Non-browser clients first — a curl or python-requests UA contains none of
    # the browser tokens below, so without this it falls through to "Unknown".
    for needle, label in _API_CLIENT_SIGNATURES:
        if needle in ua:
            version = ""
            m = re.search(re.escape(needle.rstrip("/")) + r"[/ ]([\d.]+)", ua)
            if m:
                version = m.group(1)
            return {
                "browser": label,
                "browser_version": version,
                "os": "Unknown",
                "os_version": "",
                "device_type": "api",
            }

    # Device type detection
    if any(k in ua for k in ["mobile", "android", "iphone", "ipod"]):
        device_type = "mobile"
    elif any(k in ua for k in ["tablet", "ipad"]):
        device_type = "tablet"
    else:
        device_type = "desktop"

    # Browser detection (order matters — more specific first)
    browser = "Unknown"
    browser_version = ""
    if "edg/" in ua or "edge/" in ua:
        browser = "Edge"
        m = re.search(r"edg[e]?/([\d.]+)", ua)
        if m:
            browser_version = m.group(1)
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
        m = re.search(r"opr/([\d.]+)", ua)
        if m:
            browser_version = m.group(1)
    elif "chrome/" in ua and "chromium" not in ua:
        browser = "Chrome"
        m = re.search(r"chrome/([\d.]+)", ua)
        if m:
            browser_version = m.group(1)
    elif "firefox/" in ua:
        browser = "Firefox"
        m = re.search(r"firefox/([\d.]+)", ua)
        if m:
            browser_version = m.group(1)
    elif "safari/" in ua:
        browser = "Safari"
        m = re.search(r"version/([\d.]+)", ua)
        if m:
            browser_version = m.group(1)
    elif "msie " in ua or "trident/" in ua:
        browser = "Internet Explorer"

    # OS detection — iPhone/iPad must be checked BEFORE mac os x (iPhone UAs contain both)
    os_name = "Unknown"
    os_version = ""
    if "android" in ua:
        os_name = "Android"
        m = re.search(r"android ([\d.]+)", ua)
        if m:
            os_version = m.group(1)
    elif "iphone os" in ua or "ipad; cpu os" in ua or "cpu iphone os" in ua:
        os_name = "iOS"
        m = re.search(r"(?:iphone|ipad|cpu iphone)[^;]*os ([\d_]+)", ua)
        if m:
            os_version = m.group(1).replace("_", ".")
    elif "windows nt" in ua:
        os_name = "Windows"
        m = re.search(r"windows nt ([\d.]+)", ua)
        if m:
            nt_map = {"10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7", "6.0": "Vista", "5.1": "XP"}
            os_version = nt_map.get(m.group(1), m.group(1))
    elif "mac os x" in ua:
        os_name = "macOS"
        m = re.search(r"mac os x ([\d_]+)", ua)
        if m:
            os_version = m.group(1).replace("_", ".")
    elif "linux" in ua:
        os_name = "Linux"

    return {
        "browser": browser,
        "browser_version": browser_version,
        "os": os_name,
        "os_version": os_version,
        "device_type": device_type,
    }


def generate_device_fingerprint(ip: str, ua: str) -> str:
    """
    Generate a sha256 device fingerprint from IP + User-Agent.
    Used to detect new devices across logins.
    """
    raw = f"{ip}|{ua}"
    return hashlib.sha256(raw.encode()).hexdigest()


def lookup_geo(ip: str, cf_country: Optional[str] = None) -> dict:
    """
    Look up geolocation for an IP using MaxMind GeoLite2-City.
    Falls back to CF-IPCountry header for country if DB is unavailable.

    Returns dict with: country_code, country_name, city, latitude, longitude, timezone, isp
    """
    result = {
        "country_code": "AU",
        "country_name": "Australia",
        "city": "Unknown",
        "latitude": None,
        "longitude": None,
        "timezone": "Australia/Sydney",
        "isp": "Unknown",
    }

    # Skip private/loopback IPs
    if not ip or ip in ("unknown", "127.0.0.1", "::1") or ip.startswith("192.168.") or ip.startswith(
            "10.") or ip.startswith("172."):
        return result

    # Try MaxMind City DB
    reader = _get_city_reader()
    if reader:
        try:
            response = reader.city(ip)
            result["country_code"] = response.country.iso_code or "AU"
            result["country_name"] = response.country.name or "Unknown"
            result["city"] = response.city.name or "Unknown"
            result["latitude"] = response.location.latitude
            result["longitude"] = response.location.longitude
            result["timezone"] = response.location.time_zone or "UTC"
        except Exception as e:
            logger.debug(f"GeoIP city lookup failed for {ip}: {e}")
            # Fall back to CF header
            if cf_country:
                result["country_code"] = cf_country
    else:
        # No City DB — use CF header if available
        if cf_country:
            result["country_code"] = cf_country

    # Try MaxMind ASN DB for ISP
    asn_reader = _get_asn_reader()
    if asn_reader:
        try:
            asn_response = asn_reader.asn(ip)
            result["isp"] = asn_response.autonomous_system_organization or "Unknown"
        except Exception as e:
            logger.debug(f"GeoIP ASN lookup failed for {ip}: {e}")

    return result
