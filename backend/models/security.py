"""
Security audit log models for IP logging and login activity tracking.
"""

from pydantic import BaseModel
from typing import List, Optional


class LoginAuditCreate(BaseModel):
    user_id: Optional[str] = None  # None for unknown-user attempts
    email: str
    ip_address: str
    status: str  # "success" | "failed" | "deactivated"
    failure_reason: Optional[str] = None  # "invalid_password" | "user_not_found" | "account_deactivated"
    user_agent: str
    device_fingerprint: str
    device_info: dict  # {browser, browser_version, os, os_version, device_type}
    geo: dict  # {country_code, country_name, city, lat, lon, timezone, isp}
    risk_score: int = 0  # 0–100
    risk_flags: List[str] = []  # ["new_country", "new_device", "new_ip", "odd_time"]
    cf_ray: Optional[str] = None
    attempted_at: str  # ISO 8601


class LoginAuditResponse(BaseModel):
    id: str
    user_id: Optional[str] = None
    email: str
    ip_address: str
    status: str
    failure_reason: Optional[str] = None
    user_agent: str
    device_fingerprint: str
    device_info: dict
    geo: dict
    risk_score: int
    risk_flags: List[str]
    cf_ray: Optional[str] = None
    attempted_at: str
    user_full_name: Optional[str] = None


class SecurityStatsResponse(BaseModel):
    total_logins_30d: int
    failed_attempts_30d: int
    unique_ips_30d: int
    unique_countries_30d: int
    suspicious_events_30d: int
    daily_activity: List[dict]  # [{date, success, failed}]
    country_distribution: List[dict]  # [{country_code, country_name, count}]
    device_distribution: List[dict]  # [{device_type, count}]
    top_failed_ips: List[dict]  # [{ip, count, last_seen, country}]


class UserSecuritySummary(BaseModel):
    last_login_at: Optional[str] = None
    last_login_ip: Optional[str] = None
    last_login_country: Optional[str] = None
    last_login_city: Optional[str] = None
    last_login_device: Optional[str] = None
    failed_attempts_30d: int = 0
    suspicious_events_30d: int = 0
    unique_ips_30d: int = 0
    recent_logins: List[dict] = []  # last 5–20 login entries


class FlagIPRequest(BaseModel):
    ip_address: str
    reason: str
    action: str = "suspicious"  # "suspicious" | "blocked"
