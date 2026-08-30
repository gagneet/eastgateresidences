import base64
import html as html_lib
import io
import os
import re
import secrets
import smtplib
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote, quote_plus

import asyncio
import bcrypt
import jwt
import logging
import nh3
# Payment and PDF generation
import stripe
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    APIRouter,
    HTTPException,
    Depends,
    UploadFile,
    File,
    Form,
    Query,
    status,
    BackgroundTasks,
    Request,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pymongo import AsyncMongoClient
from pydantic import BaseModel, Field, EmailStr, ConfigDict, model_validator
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from starlette.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from typing import List, Optional, Dict, Any

from utils.error_response import (
    build_error_response,
    get_request_id,
    log_unhandled_exception,
    redact_secrets,
)

# Email providers
try:
    import resend

    RESEND_AVAILABLE = True
except ImportError:
    RESEND_AVAILABLE = False

# PDF generation
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Table,
        TableStyle,
        Paragraph,
        Spacer,
        Image,
    )
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# MongoDB connection
mongo_url = os.environ["MONGO_URL"]
client = AsyncMongoClient(mongo_url)
db = client[os.environ["DB_NAME"]]


async def _server_agg(collection, pipeline: list, length) -> list:
    """PyMongo 4.x: raw db.collection.aggregate() is a coroutine.
    Await it to get the cursor, then await .to_list(). Used throughout
    server.py which holds a raw AsyncDatabase (not TenantScopedDatabase)."""
    cursor = await collection.aggregate(pipeline)
    return await cursor.to_list(length)


def _iso(value):
    """Coerce datetime / date / str to ISO string. Returns None for None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _is_uuid(value) -> bool:
    """True when `value` can be compared against a Postgres uuid column.

    Postgres identity ids are UUIDs; MongoDB's are arbitrary strings, and both
    flow through the transfer-approval path. Checking beforehand turns what would
    be a mid-statement DataError (aborting the surrounding transaction) into a
    logged skip that leaves the rest of the work intact.
    """
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _is_service_account_email(email: str | None) -> bool:
    """True for platform service actors that must never appear in a people list.

    Keyed on the reserved ``system-`` local part alone, deliberately NOT on the
    ``@system.strataos.local`` domain. The domain check was the original rule and
    it failed open: a 2026-08-26 bulk email rewrite moved every address onto the
    building's own mail domain without exempting service accounts, so the finance
    cutover actor landed in /admin/users presenting as a strata manager. A local
    part is a far more stable marker than a domain that operational scripts rewrite.
    """
    return (email or "").strip().lower().startswith("system-")


def _norm_lot(value) -> str:
    """Normalise a lot identifier for cross-store matching.

    Strips the "LOT" prefix (legacy Mongo db.units.lot_number is "LOT86",
    core.lots.lot_number is "86") and strips leading zeros so "071" and "71"
    compare equal. Falls back to the original value when stripping leaves
    nothing (e.g. "0" -> "0").
    """
    s = str(value or "").strip().upper()
    if s.startswith("LOT"):
        s = s[3:]
    return s.lstrip("0") or s


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Create the main app
from config import APP_ENV as _APP_ENV

_is_production = _APP_ENV == "production"

app = FastAPI(
    title="Strata Management Platform",
    version="1.0.0",
    description="Full-featured Strata Management platform for Strata/Owners Corporation",
    # Disable Swagger UI and ReDoc in production — they expose internal API structure.
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """
    Comprehensive security headers middleware.

    Headers added:
    - X-Content-Type-Options        : prevent MIME sniffing
    - X-Frame-Options               : clickjacking protection
    - X-XSS-Protection              : legacy XSS filter
    - Strict-Transport-Security     : HTTPS enforcement (production only)
    - Content-Security-Policy       : restrict resource origins
    - Referrer-Policy               : limit referrer leakage
    - Permissions-Policy            : disable unused browser features
    - X-IP-Protection               : branding / IP notice
    """
    request_id = get_request_id(request)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["X-IP-Protection"] = (
        "A vision by: Silverfox Technologies, Australia - "
        "Contact: gagneet@silverfoxtechnologies.com.au"
    )

    # Referrer policy — only send origin on same-origin requests
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Permissions policy — disable features not needed for a strata management SaaS
    response.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
    )

    # Content-Security-Policy
    # - Relaxed for the API (responses are JSON); tighten further on the frontend CDN.
    # - 'self' + explicit CDN allowlist prevents data exfiltration via injected scripts.
    is_production = os.getenv("APP_ENV") == "production"
    if is_production:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://static.cloudflareinsights.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self'; "
            "connect-src 'self' https://cloudflareinsights.com; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
    else:
        # Development: relaxed CSP so the OpenAPI UI and hot-reload work
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: http: https:; "
            "frame-ancestors 'none'"
        )

    return response


# @featuretrace:error-recovery-framework — App-level exception handlers return safe structured API errors.
# Layer: router
# Data flow: request exception -> handler -> utils.error_response.build_error_response -> frontend API classifier (global).
# Related: backend/utils/error_response.py
#          frontend/src/lib/api-error.ts
#          docs/architecture/error-recovery-framework.md


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Generated function header.

    Function: http_exception_handler
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return build_error_response(
        request,
        status_code=exc.status_code,
        detail=exc.detail,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(StarletteHTTPException)
async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Generated function header.

    Function: starlette_http_exception_handler
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return build_error_response(
        request,
        status_code=exc.status_code,
        detail=getattr(exc, "detail", None),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Generated function header.

    Function: validation_exception_handler
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return build_error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="Some required information is missing or invalid.",
        detail={"fields": exc.errors()},
        retryable=False,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Generated function header.

    Function: unhandled_exception_handler
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    log_unhandled_exception(request, exc)
    return build_error_response(
        request,
        status_code=500,
        code="SERVER_ERROR",
        message="Something went wrong on our side.",
        retryable=True,
    )


# Rate limiting via slowapi — shared limiter instance from utils.rate_limit
from utils.rate_limit import (
    limiter,
    SLOWAPI_AVAILABLE,
    RATE_LIMIT_DEFAULTS,
    RATE_LIMIT_SETTING_ID,
    rate_limit,
    refresh_rate_limit_config,
)

if SLOWAPI_AVAILABLE:
    try:
        from slowapi.errors import RateLimitExceeded

        async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
            """Generated function header.

            Function: rate_limit_exception_handler
            Path: backend/server.py

            Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
            """
            retry_after = getattr(exc, "retry_after", None)
            headers = {"Retry-After": str(retry_after)} if retry_after else None
            return build_error_response(
                request,
                status_code=429,
                code="RATE_LIMITED",
                message="Too many requests. Please wait a moment and try again.",
                retryable=True,
                headers=headers,
            )

        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    except ImportError:
        logger.warning("slowapi not available — rate limiting disabled")
else:
    logger.warning("slowapi not available — rate limiting disabled")

# App-level handler: building settings incomplete (422) — raised by PDF generators
# when plan_number or strata_address is missing. Scales to all future doc generators
# without per-endpoint try/except.
try:
    from utils.pdf_generator import BuildingSettingsIncompleteError


    async def _building_settings_incomplete_handler(request, exc):
        """Generated function header.

        Function: _building_settings_incomplete_handler
        Path: backend/server.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return build_error_response(
            request,
            status_code=422,
            code=getattr(exc, "error_code", "BUILDING_SETTINGS_INCOMPLETE"),
            message="Building settings are incomplete for this document.",
            detail={
                "missing_fields": getattr(exc, "missing_fields", []),
                "building_id": getattr(exc, "building_id", None),
                "fix_action": getattr(exc, "fix_action", None),
            },
            retryable=False,
        )


    app.add_exception_handler(BuildingSettingsIncompleteError, _building_settings_incomplete_handler)
except ImportError as e:
    logger.warning(f"BuildingSettingsIncompleteError handler not registered: {e}")

# Import auth router (register endpoint with rate limiting)
try:
    from routers.auth import router as auth_router

    AUTH_ROUTER_AVAILABLE = True
except ImportError as e:
    AUTH_ROUTER_AVAILABLE = False
    logger.warning(f"Auth router not available: {e}")

# Import chat router (will be included later)
try:
    from routers.chat import (
        router as chat_router,
        initialize_system_groups,
        auto_join_groups,
    )

    CHAT_ROUTER_AVAILABLE = True
except ImportError:
    CHAT_ROUTER_AVAILABLE = False
    logger.warning("Chat router not available")

# Import communication router (notices, announcements)
try:
    from routers.communication import router as communication_router

    COMMUNICATION_ROUTER_AVAILABLE = True
except ImportError:
    COMMUNICATION_ROUTER_AVAILABLE = False
    print("Warning: Communication router not available")

# Import maintenance router
try:
    from routers.maintenance import router as maintenance_router

    MAINTENANCE_ROUTER_AVAILABLE = True
except ImportError:
    MAINTENANCE_ROUTER_AVAILABLE = False
    print("Warning: Maintenance router not available")

# Import notifications router
try:
    from routers.notifications import router as notifications_router

    NOTIFICATIONS_ROUTER_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_ROUTER_AVAILABLE = False
    print("Warning: Notifications router not available")

# Import feature toggles router
try:
    from routers.feature_toggles import router as feature_toggles_router

    FEATURE_TOGGLES_ROUTER_AVAILABLE = True
except ImportError:
    FEATURE_TOGGLES_ROUTER_AVAILABLE = False
    print("Warning: Feature toggles router not available")

# Import finance router (new clean architecture 2026-02-19)
try:
    from routers.finance import router as finance_router

    FINANCE_ROUTER_AVAILABLE = True
except ImportError:
    FINANCE_ROUTER_AVAILABLE = False
    print("Warning: Finance router not available")

# Import finance reports router
try:
    from routers.finance_reports import router as finance_reports_router

    FINANCE_REPORTS_ROUTER_AVAILABLE = True
except ImportError:
    FINANCE_REPORTS_ROUTER_AVAILABLE = False
    print("Warning: Finance reports router not available")

# Import financial onboarding router
try:
    from routers.financial_onboarding import router as financial_onboarding_router

    FINANCIAL_ONBOARDING_ROUTER_AVAILABLE = True
except ImportError:
    FINANCIAL_ONBOARDING_ROUTER_AVAILABLE = False
    print("Warning: Financial onboarding router not available")

# Import analytics router
try:
    from routers.analytics import router as analytics_router

    ANALYTICS_ROUTER_AVAILABLE = True
except ImportError:
    ANALYTICS_ROUTER_AVAILABLE = False
    print("Warning: Analytics router not available")

# Import market router
try:
    from routers.market import router as market_router

    MARKET_ROUTER_AVAILABLE = True
except ImportError:
    MARKET_ROUTER_AVAILABLE = False
    print("Warning: Market router not available")

# Import council rates router
try:
    from routers.council_rates import router as council_rates_router

    COUNCIL_RATES_ROUTER_AVAILABLE = True
except ImportError:
    COUNCIL_RATES_ROUTER_AVAILABLE = False
    print("Warning: Council rates router not available")

# Import reconciliation router
try:
    from routers.reconciliation import router as reconciliation_router

    RECONCILIATION_ROUTER_AVAILABLE = True
except ImportError:
    RECONCILIATION_ROUTER_AVAILABLE = False
    print("Warning: Reconciliation router not available")

# Import water bills router
try:
    from routers.water_bills import router as water_bills_router

    WATER_BILLS_ROUTER_AVAILABLE = True
except ImportError:
    WATER_BILLS_ROUTER_AVAILABLE = False
    print("Warning: Water bills router not available")

# Import GST & BAS ledger router
try:
    from routers.gst_bas import router as gst_bas_router

    GST_BAS_ROUTER_AVAILABLE = True
except ImportError:
    GST_BAS_ROUTER_AVAILABLE = False
    print("Warning: GST/BAS router not available")

# Import Capital Funding & Special Levy preview router (GAP-FIN-034)
try:
    from routers.capital_funding import router as capital_funding_router

    CAPITAL_FUNDING_ROUTER_AVAILABLE = True
except ImportError:
    CAPITAL_FUNDING_ROUTER_AVAILABLE = False
    print("Warning: Capital Funding router not available")

# Import per-building Strata Web portal connection config router
try:
    from routers.strata_web_portal import router as strata_web_portal_router

    STRATA_WEB_PORTAL_ROUTER_AVAILABLE = True
except ImportError as e:
    STRATA_WEB_PORTAL_ROUTER_AVAILABLE = False
    print(f"Warning: Strata Web portal router not available: {e}")

try:
    from routers.utilities import router as utilities_router

    UTILITIES_ROUTER_AVAILABLE = True
except ImportError:
    UTILITIES_ROUTER_AVAILABLE = False
    print("Warning: Utilities router not available")

# Import finance intelligence router
try:
    from routers.finance_intelligence import router as finance_intelligence_router

    FINANCE_INTELLIGENCE_ROUTER_AVAILABLE = True
except Exception as e:
    FINANCE_INTELLIGENCE_ROUTER_AVAILABLE = False
    print(f"Finance Intelligence router not available: {e}")

# Import rental certificates router (ACT s.119A compliance)
try:
    from routers.rental_certificates import router as rental_certs_router

    RENTAL_CERTS_ROUTER_AVAILABLE = True
except ImportError as e:
    RENTAL_CERTS_ROUTER_AVAILABLE = False
    print(f"Warning: Rental certificates router not available: {e}")

# Import PPM (Preventive Maintenance Plan) router
try:
    from routers.ppm import router as ppm_router

    PPM_ROUTER_AVAILABLE = True
except ImportError as e:
    PPM_ROUTER_AVAILABLE = False
    print(f"Warning: PPM router not available: {e}")

# Import building assets router
try:
    from routers.building import router as building_router

    BUILDING_ROUTER_AVAILABLE = True
except ImportError as e:
    BUILDING_ROUTER_AVAILABLE = False
    print(f"Warning: Building router not available: {e}")

# Import security/IP logging router
try:
    from routers.security import router as security_router

    SECURITY_ROUTER_AVAILABLE = True
except ImportError as e:
    SECURITY_ROUTER_AVAILABLE = False
    print(f"Warning: Security router not available: {e}")

# Import outbox admin router
try:
    from routers.outbox_admin import router as outbox_admin_router

    OUTBOX_ADMIN_ROUTER_AVAILABLE = True
except ImportError as e:
    OUTBOX_ADMIN_ROUTER_AVAILABLE = False
    print(f"Warning: Outbox admin router not available: {e}")

# Import cutover control plane admin router
try:
    from routers.cutover_admin import router as cutover_admin_router

    CUTOVER_ADMIN_ROUTER_AVAILABLE = True
except ImportError as e:
    CUTOVER_ADMIN_ROUTER_AVAILABLE = False
    print(f"Warning: Cutover admin router not available: {e}")

# Import super-admin cross-feature diagnostics router (GAP-ADMIN-001) — deliberately
# NOT gated behind any building-scoped feature toggle; see routers/admin_diagnostics.py
try:
    from routers.admin_diagnostics import router as admin_diagnostics_router

    ADMIN_DIAGNOSTICS_ROUTER_AVAILABLE = True
except ImportError as e:
    ADMIN_DIAGNOSTICS_ROUTER_AVAILABLE = False
    print(f"Warning: Admin diagnostics router not available: {e}")

# Import work orders router
try:
    from routers.work_orders import router as work_orders_router

    WORK_ORDERS_ROUTER_AVAILABLE = True
except ImportError as e:
    WORK_ORDERS_ROUTER_AVAILABLE = False
    print(f"Warning: Work orders router not available: {e}")

# Import insurance claims router
try:
    from routers.insurance_claims import router as insurance_claims_router

    INSURANCE_CLAIMS_ROUTER_AVAILABLE = True
except ImportError as e:
    INSURANCE_CLAIMS_ROUTER_AVAILABLE = False
    print(f"Warning: Insurance claims router not available: {e}")

try:
    from routers.nsw_compliance import router as nsw_compliance_router
    from routers.privacy_compliance import router as privacy_compliance_router
    from routers.whs import router as whs_router
    from routers.manager_contracts import router as manager_contracts_router
    from routers.decisions import router as decisions_router

    COMPLIANCE_ROUTERS_AVAILABLE = True
except ImportError as e:
    COMPLIANCE_ROUTERS_AVAILABLE = False
    print(f"Warning: Compliance routers not available: {e}")

try:
    from routers.building_manager_duties import router as building_manager_duties_router
    BUILDING_MANAGER_DUTIES_ROUTER_AVAILABLE = True
except ImportError as e:
    BUILDING_MANAGER_DUTIES_ROUTER_AVAILABLE = False
    logger.warning("Building manager duties router not available: %s", e)

try:
    from routers.nsw_initial_maintenance_schedule import router as nsw_ims_router
    NSW_IMS_ROUTER_AVAILABLE = True
except ImportError as e:
    NSW_IMS_ROUTER_AVAILABLE = False
    logger.warning("NSW initial maintenance schedule router not available: %s", e)

# Import geo utility for IP resolution and geolocation
try:
    from utils.geo import (
        get_real_ip,
        parse_user_agent,
        generate_device_fingerprint,
        lookup_geo,
    )

    GEO_AVAILABLE = True
except ImportError as e:
    GEO_AVAILABLE = False
    logger.warning(f"Geo utility not available: {e}")

# JWT Configuration
from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_HOURS

# Email Configuration
from config import (
    RESEND_API_KEY,
    SENDGRID_API_KEY,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SENDER_EMAIL,
    SYSTEM_HIDDEN_EMAIL,
    TEST_EMAIL_INTERCEPT_ADDRESS,
)

# Initialize Resend if available
if RESEND_AVAILABLE and RESEND_API_KEY:
    import resend

    resend.api_key = RESEND_API_KEY

# Stripe Configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Initialize Stripe
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Security
security = HTTPBearer(auto_error=False)

# ==================== MODELS ====================

# Import models from consolidated models directory
from models.user import (
    UserRole,
    UserResponse,
    UserUpdate,
)
from utils.auth import (
    DEFAULT_BUILDING_ID,
    get_current_user,
    get_optional_user,
    get_approved_user,
    is_approved_user,
    get_current_building,
    get_optional_building,
    get_building_or_400,
)
# Canonical authorisation. Added as an ADDITIONAL dependency alongside each
# route's existing check during the GAP-SEC-005 migration, never as a
# replacement: an additive guard can only narrow access, so a capability that
# turns out to be too tight fails visibly instead of opening a route. See
# docs/security/server_inline_route_classification_2026_08_24.md.
from services.capability_registry import require_capability
from utils.permissions import (
    get_user_permissions,
    user_to_response,
    require_permission,
    require_feature,
)
from utils.email import (
    EMAIL_FORMAT_HTML,
    EMAIL_FORMAT_PLAIN,
    _build_email_message,
    _html_to_text,
    _normalize_email_format,
    _wrap_email_html,
    get_email_template,
)
from utils.name_utils import check_owner_name_against_roll, format_owner_names
from utils.crypto import encrypt_sensitive, is_encrypted
from utils.helpers import create_user_notification, broadcast_user_notification, create_audit_log, mask_email, \
    mask_phone, create_notifications_batch, get_portal_url
from utils.staff_membership_repair import repair_orphan_staff_memberships
from utils.file_scan import scan_upload
from services.owner_service import (
    get_owner_info as _get_owner_info,
    get_owner_name as _get_owner_name,
    get_all_unit_owners as _get_all_unit_owners,
)
from services.settings_service import (
    get_general_settings as _get_general_settings,
    get_general_settings_or_default as _get_general_settings_or_default,
    get_unit_display_rules as _get_unit_display_rules,
    upsert_general_settings as _upsert_general_settings,
    upsert_unit_display_rules as _upsert_unit_display_rules,
)
from models.settings import UnitDisplayRulesUpdate
from models.timestamps import timestamp_sort_key


# Thin alias over the shared resolver in utils.helpers. This module and
# routers/auth.py each carried a byte-identical private copy; both now delegate
# to one implementation so a changed env-var precedence cannot drift between
# them. Kept as a module-level name because it is referenced throughout this
# file and patched by name in tests.
_get_portal_url = get_portal_url


async def get_user_data(
        user_id: str,
        current_user: dict = Depends(require_permission("can_manage_users")),
        building_id: str = Depends(get_current_building),
):
    """
    Fetch user data by ID. Admin only. Restricted to members of the current building.
    Bolt ⚡ Security: Uses MongoDB's find_one with dictionary filter to prevent injection.
    Sentinel 🛡️: Verifies building membership to prevent BOLA/cross-tenant access.
    Sentinel 🛡️: Enforces BOLA protection by verifying building membership.
    """
    # Performance Optimization⚡: Parallelize membership and user data fetch
    membership_task = db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    user_task = db.users.find_one({"id": user_id}, {"_id": 0, "hashed_password": 0, "password_hash": 0})

    membership, user = await asyncio.gather(membership_task, user_task)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Sentinel 🛡️: Verify user is a member of this building (BOLA Protection)
    if not membership and current_user.get("role") != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="User does not belong to this building"
        )

    return user


# Document Access Levels
class DocumentAccess:
    ALL_MEMBERS = "all_members"
    OWNERS_VIEW = "owners_view"
    OWNERS_EDIT = "owners_edit"
    EC_VIEW = "ec_view"
    EC_EDIT = "ec_edit"
    CHAIRMAN_ONLY = "chairman_only"


# ==================== UNIT CHANGE REQUEST MODELS ====================


class UnitChangeRequestCreate(BaseModel):
    """Model for creating a change request (unit or profile status)"""

    requested_unit: Optional[str] = None
    is_managing_agent: Optional[bool] = None
    is_tenanted: Optional[bool] = None
    reason: Optional[str] = None


class UnitChangeRequestResponse(BaseModel):
    """Model for unit change request response"""

    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str
    user_email: str
    user_name: str
    current_unit: Optional[str] = None
    requested_unit: Optional[str] = None
    current_is_managing_agent: Optional[bool] = None
    requested_is_managing_agent: Optional[bool] = None
    current_is_tenanted: Optional[bool] = None
    requested_is_tenanted: Optional[bool] = None
    reason: Optional[str] = None
    status: str  # pending, approved, rejected
    created_at: str
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewer_name: Optional[str] = None
    admin_notes: Optional[str] = None


class UnitChangeRequestReview(BaseModel):
    """Model for reviewing a unit change request"""

    action: str  # approve or reject
    admin_notes: Optional[str] = None


# Auth Response
class AuthResponse(BaseModel):
    token: str
    user: UserResponse


# Document Models
class DocumentCategory:
    EC_DOCUMENTS = "ec_documents"
    PUBLIC_DOCUMENTS = "public_documents"
    MEETING_MINUTES = "meeting_minutes"
    FINANCIAL_REPORTS = "financial_reports"
    BYLAWS = "bylaws"
    NOTICES = "notices"


class DocumentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    category: str
    folder_id: Optional[str] = None
    is_public: bool = False
    allowed_roles: List[str] = []
    is_important: bool = False
    importance_summary: Optional[str] = None


class DocumentResponse(BaseModel):
    """One document, as returned by GET /documents.

    ## Why so many fields are optional

    Seven fields here were REQUIRED, and 240 of the 242 documents in the live
    building do not carry them. Those rows were written by the levy-notice
    generator using a different shape — ``owner_id``/``is_private``/
    ``content_type`` instead of ``uploaded_by``/``is_public``/``file_type`` —
    so every one of them failed validation, ``GET /documents`` raised a
    ResponseValidationError, and the page rendered as empty. The Community Pulse
    feed meanwhile listed those same documents correctly, because it reads the
    collection directly, which is why a card pointed at a document the documents
    page appeared not to have.

    A response model exists to describe what the API returns, not to reject the
    data the database actually holds. Defaults and the legacy-shape mapping below
    let both writers' documents render; normalising the 240 rows is a separate
    data-repair decision and is deliberately NOT done here, because a read path
    should never have been able to 500 on them in the first place.
    """

    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    category: str
    folder_id: Optional[str] = None
    folder_path: Optional[str] = None
    file_name: str = ""
    file_type: str = ""
    file_size: int = 0
    file_data: Optional[str] = None
    is_public: bool = False
    allowed_roles: List[str] = Field(default_factory=list)
    uploaded_by: str = ""
    uploaded_by_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    is_important: bool = False
    importance_summary: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_document_shape(cls, data: Any) -> Any:
        """Map the generator's field names onto the canonical ones.

        Only fills a field that is absent — a document carrying both shapes keeps
        its canonical value. ``is_private`` is inverted rather than copied:
        the two flags mean opposite things, and copying it would publish every
        private document.
        """
        if not isinstance(data, dict):
            return data
        mapped = dict(data)

        if "file_type" not in mapped and mapped.get("content_type"):
            mapped["file_type"] = mapped["content_type"]
        if "file_name" not in mapped and mapped.get("title"):
            mapped["file_name"] = mapped["title"]
        if "uploaded_by" not in mapped and mapped.get("owner_id"):
            mapped["uploaded_by"] = mapped["owner_id"]
        if "is_public" not in mapped and "is_private" in mapped:
            mapped["is_public"] = not bool(mapped["is_private"])
        if "updated_at" not in mapped and mapped.get("created_at"):
            mapped["updated_at"] = mapped["created_at"]
        return mapped


# Document Folder Models
class DocumentFolderCreate(BaseModel):
    name: str
    parent_folder_id: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    color: Optional[str] = None


class DocumentFolderUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    color: Optional[str] = None


class DocumentFolderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    parent_folder_id: Optional[str] = None
    path: str
    description: Optional[str] = None
    created_by: str
    created_by_name: str
    created_at: str
    updated_at: str
    permissions: Dict[str, Any]
    color: Optional[str] = None
    is_system: bool = False
    document_count: int = 0
    subfolder_count: int = 0


class FolderMoveRequest(BaseModel):
    new_parent_id: Optional[str] = None


class DocumentMoveRequest(BaseModel):
    folder_id: Optional[str] = None


class DocumentRenameRequest(BaseModel):
    new_title: str = Field(..., max_length=200)


class BulkMoveRequest(BaseModel):
    document_ids: List[str] = Field(..., max_items=100)
    target_folder_id: Optional[str] = None


# Listing Models
class ListingType:
    FOR_SALE = "for_sale"
    FOR_RENT = "for_rent"
    SERVICE = "service"
    ITEM_SALE = "item_sale"


class ListingCreate(BaseModel):
    title: str
    description: str
    listing_type: str
    price: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_public: bool = True
    images: List[str] = []
    expires_at: Optional[str] = None  # ISO datetime; defaults to 90 days if not set


class ListingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: str
    listing_type: str = "item_sale"
    price: Optional[Any] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    is_public: bool = True
    images: List[str] = []
    created_by: str = ""
    created_by_name: str = ""
    status: str = "active"
    building_id: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    expires_at: Optional[str] = None


# Import models from consolidated models directory
from models.requests import (
    InsuranceClaimCreate,
    InsuranceClaimResponse,
    InsuranceEnquiryCreate,
    InsuranceEnquiryResponse,
    PetRequestCreate,
    PetRequestResponse,
    AccessControlRequestCreate,
    AccessControlRequestResponse,
    AlterationRequestCreate,
    AlterationRequestResponse,
    ReimbursementRequestCreate,
    ReimbursementRequestResponse,
)
from services.request_catalogue_service import enforce_request_policy


# Meeting Models
class MeetingCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    meeting_date: str = Field(..., max_length=100)
    location: str = Field(..., max_length=500)
    agenda: List[str] = Field(default_factory=list, max_items=50)
    attendees: List[str] = Field(default_factory=list, max_items=200)


class MeetingUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    meeting_date: Optional[str] = Field(None, max_length=100)
    location: Optional[str] = Field(None, max_length=500)
    agenda: Optional[List[str]] = Field(None, max_items=50)
    attendees: Optional[List[str]] = Field(None, max_items=200)
    minutes: Optional[str] = Field(None, max_length=20000)
    status: Optional[str] = Field(None, max_length=50)


class MeetingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    meeting_date: str
    location: str
    agenda: List[str]
    attendees: List[str]
    minutes: Optional[str] = None
    status: str
    created_by: str
    created_at: str
    updated_at: str


class MeetingNotesUpdate(BaseModel):
    agenda: List[str] = Field(default_factory=list, max_items=50)
    attendees: List[str] = Field(default_factory=list, max_items=200)
    minutes: str = Field(..., min_length=1, max_length=20000)


class OutstandingIssueCreate(BaseModel):
    issue: str = Field(..., max_length=200)
    details: str = Field(..., max_length=4000)
    status: str = Field(..., max_length=50)
    updates_notes: Optional[str] = Field(None, max_length=4000)
    strata_web_meeting_tba: Optional[str] = Field(None, max_length=500)
    chair_notes: Optional[str] = Field(None, max_length=4000)


class OutstandingIssueUpdate(BaseModel):
    issue: Optional[str] = Field(None, max_length=200)
    details: Optional[str] = Field(None, max_length=4000)
    status: Optional[str] = Field(None, max_length=50)
    updates_notes: Optional[str] = Field(None, max_length=4000)
    strata_web_meeting_tba: Optional[str] = Field(None, max_length=500)
    chair_notes: Optional[str] = Field(None, max_length=4000)


class OutstandingIssueResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    issue: str
    details: str
    status: str
    updates_notes: Optional[str] = None
    strata_web_meeting_tba: Optional[str] = None
    chair_notes: Optional[str] = None
    created_by: str
    created_by_name: str
    updated_by: str
    updated_by_name: str
    created_at: str
    updated_at: str


# TODO Models
class TodoCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    assigned_to: Optional[str] = Field(None, max_length=50)
    due_date: Optional[str] = Field(None, max_length=100)
    priority: str = Field("normal", max_length=20)
    meeting_id: Optional[str] = Field(None, max_length=50)


class TodoResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    due_date: Optional[str] = None
    priority: str
    status: str
    meeting_id: Optional[str] = None
    created_by: str
    created_at: str
    updated_at: str


# Finance Models
class FinanceEntryCreate(BaseModel):
    entry_type: str  # income, expense, levy
    category: str
    amount: float
    description: str
    date: str
    unit_number: Optional[str] = None


class FinanceEntryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    entry_type: str
    category: str
    amount: float
    description: str
    date: str
    unit_number: Optional[str] = None
    created_by: str
    created_at: str


# Schedule Models
class ScheduleCreate(BaseModel):
    title: str
    description: Optional[str] = None
    start_time: str
    end_time: str
    location: Optional[str] = None
    assigned_to: Optional[str] = None
    schedule_type: str = "general"  # general, cleaning, maintenance, inspection


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    start_time: str
    end_time: str
    location: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    schedule_type: str
    status: str
    created_by: str
    created_at: str


# Blog/News Models
class BlogPostCreate(BaseModel):
    title: str = Field(..., max_length=200)
    content: str
    excerpt: Optional[str] = None
    cover_image: Optional[str] = None
    tags: List[str] = []
    is_published: bool = True
    expires_at: Optional[str] = None


class BlogPostResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    content: str
    excerpt: Optional[str] = None
    cover_image: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    is_published: bool
    is_draft: bool = False
    author_id: str
    author_name: str
    views: int
    expires_at: Optional[str] = None
    created_at: str
    updated_at: str


# EC Member Models
class ECMemberCreate(BaseModel):
    name: str
    position: str
    bio: Optional[str] = None
    image: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    order: int = 0


class ECMemberResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    position: str
    bio: Optional[str] = None
    image: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    order: int
    created_at: str


# Emergency Service Models
class EmergencyServiceCreate(BaseModel):
    name: str
    category: str  # fire, police, medical, utility, building, management
    phone: str
    description: Optional[str] = None
    address: Optional[str] = None
    is_24_7: bool = False
    is_private: bool = False
    order: int = 0


class EmergencyServiceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    category: str
    phone: str
    description: Optional[str] = None
    address: Optional[str] = None
    is_24_7: bool = False
    is_private: bool = False
    order: int = 0
    created_at: str


# Site Settings
class SiteSettingsUpdate(BaseModel):
    building_name: Optional[str] = None
    building_address: Optional[str] = None
    building_description: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    hero_image: Optional[str] = None
    about_content: Optional[str] = None
    footer_text: Optional[str] = None
    ip_string: Optional[str] = None
    financial_year_start_month: Optional[int] = None
    quick_links: Optional[List[Dict[str, str]]] = None
    resident_links: Optional[List[Dict[str, str]]] = None
    # Levy Collection Schedule Settings
    levy_collection_frequency: Optional[str] = (
        None  # "monthly" | "quarterly" | "half_yearly" | "yearly"
    )
    levy_due_months: Optional[List[int]] = None  # Array of month numbers (1-12)
    levy_due_day_type: Optional[str] = None  # "first" | "middle" | "last" | "custom"
    levy_due_day: Optional[int] = None  # 1–31, only used when type="custom"
    levy_due_custom_dates: Optional[Dict[str, int]] = None  # Month -> Day mapping
    interest_rate_per_month: Optional[float] = (
        None  # Interest rate as decimal (e.g., 0.02 for 2%)
    )
    penalty_amount: Optional[float] = None  # Fixed penalty amount
    grace_period_days: Optional[int] = None  # Days before interest/penalty applies
    gst_registered: Optional[bool] = True
    levy_gst_rate: Optional[float] = 0.10  # decimal or percent; normalized by GST helpers
    timezone: Optional[str] = None  # IANA timezone (e.g. "Australia/Sydney")
    # Work Order & Approval Settings
    work_order_thresholds: Optional[List[Dict[str, Any]]] = None
    quotes_required_thresholds: Optional[List[Dict[str, Any]]] = None
    invoice_approval_threshold: Optional[float] = None
    emergency_override_enabled: Optional[bool] = None
    # Registration & Approval Timing Settings
    admin_auto_approve_minutes: Optional[int] = None  # Minutes before auto-approval (default 15)
    guest_escalation_hours: Optional[int] = None  # Hours before escalating guest to admin (default 2)
    tenant_escalation_hours: Optional[int] = None  # Hours before escalating tenant to admin (default 48)
    token_validity_hours: Optional[int] = None  # Hours a registration decision token is valid (default 72)
    notify_bcc_email: Optional[str] = None  # BCC address for all registration notifications
    notify_bcc_name: Optional[str] = None  # Display name for BCC address
    # Rate limiting (requests per minute per IP) — requires backend restart to take effect
    rate_limit_register: Optional[int] = None  # POST /auth/register (default 5)
    rate_limit_login: Optional[int] = None  # POST /auth/login (default 10)
    rate_limit_forgot_password: Optional[int] = None  # POST /auth/forgot-password (default 5)
    rate_limit_reset_password: Optional[int] = None  # POST /auth/reset-password (default 5)
    rate_limit_change_password: Optional[int] = None  # POST /auth/change-password (default 10)
    rate_limit_registration_decision: Optional[int] = None  # POST /auth/registration-decision (default 10)
    rate_limit_multiplier: Optional[float] = None  # Multiplier applied to all auth limits (default 1.0)
    # Help & Contact Settings
    ec_email: Optional[str] = None  # EC contact email address
    ec_contact_phone: Optional[str] = None  # EC contact phone number
    handbook_url: Optional[str] = None  # URL to resident handbook
    unit_plan_url: Optional[str] = None  # URL to unit plan PDF
    inclusions_url: Optional[str] = None  # URL to unit inclusions guide (building-specific)
    # Payment & Bank Details (single source of truth for levy notices, PDFs, payment pages)
    bank_name: Optional[str] = None  # e.g. "Macquarie Bank"
    bank_bsb: Optional[str] = None  # e.g. "182-266"
    bank_account_number: Optional[str] = None  # e.g. "260611108"
    bank_account_name: Optional[str] = None  # e.g. "East Gate Units Plan 13195"
    deft_ref: Optional[str] = None  # DEFT payment reference number
    bpay_biller_code: Optional[str] = None  # BPAY biller code e.g. "96503"
    bpay_ref: Optional[str] = None  # BPAY reference number
    aus_post_code: Optional[str] = None  # AusPost BillPay code e.g. "*496"
    aus_post_ref: Optional[str] = None  # AusPost reference e.g. "260611108 62701425048"
    building_abn: Optional[str] = None  # Building ABN e.g. "98 212 234 337"
    levy_interest_rate_pa: Optional[float] = None  # Annual interest rate on overdue levies (%)
    levy_notice_disclaimer: Optional[str] = None  # Full disclaimer text printed on levy/arrears notices
    plan_number: Optional[str] = None  # Unit plan number e.g. "13195"
    strata_address: Optional[str] = None  # Full address for levy notice header
    # Managing-agent branding & automated levy-notice email
    # (resolved by services/levy_notice_email_service.py — "StrataOS" is substituted
    # by strata_management_company on every levy notice email + PDF).
    strata_management_company: Optional[str] = None  # Strata management company name (e.g. "Civium Property Group")
    strata_manager_name: Optional[str] = None  # Legacy alias for the managing company name
    strata_manager_phone: Optional[str] = None  # Managing agent contact phone
    strata_manager_email: Optional[str] = None  # Managing agent contact email
    strata_manager_address: Optional[str] = None  # Managing agent postal address
    levies_department_phone: Optional[str] = None  # Levies department phone (levies_team email format)
    levies_department_email: Optional[str] = None  # Levies department email (levies_team email format)
    levy_notice_email_format: Optional[str] = None  # "standard" | "levies_team"
    levy_notice_support_email: Optional[str] = None  # Explicit support address printed on the notice email
    levy_notice_support_domain: Optional[str] = None  # Domain for the derived UP<plan>@domain support address
    # Bank Feed Matching — the GET default below (True) reflects the actual pre-existing
    # sync behavior (confidence-based auto-allocate allowed, everything else held for
    # manual review), so an unconfigured building sees NO behavior change from this
    # setting existing. Turning it OFF (False) is the opt-in gate: it forces every
    # matched transaction to manual review regardless of confidence — see
    # backend/routers/bank_feeds.py's /sync handler, which only ever tightens
    # BankFeedSyncRequest.disable_auto_allocation for an explicit False, never loosens it.
    bank_feed_auto_approve: Optional[bool] = None


# Scraper Settings Models
# Fields written by scraper trigger endpoints — preserved across user-initiated saves.
# Module-level so it isn't re-created on every PUT /settings/scrapers request.
_SCRAPER_OPERATIONAL_FIELDS: tuple = ("last_run", "next_run", "status", "error_message")


class ScraperConfig(BaseModel):
    enabled: bool = True
    # Cron expression — has a safe default (daily at 2 AM) so PUT requests that
    # omit it don't cause a Pydantic 422 validation error.
    schedule: str = "0 2 * * *"
    schedule_preset: Optional[str] = None  # hourly, daily, weekly, custom
    # Operational fields: set by the backend after each run, never sent by the frontend.
    # Declared here so Pydantic accepts them on round-trip but they are excluded from
    # user-facing PUT payloads by the update logic which preserves them from the DB.
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    status: str = "idle"  # idle, running, error
    error_message: Optional[str] = None


class NewsScraperSettings(ScraperConfig):
    method: str = "rss"  # rss, serper, newsapi
    max_articles: int = 10
    min_content_length: int = 150
    relevance_threshold: float = 0.12
    notify_on_error: bool = True
    notify_on_success: bool = False


class PropertyScraperSettings(ScraperConfig):
    max_listings_per_suburb: int = 10
    expiry_days: int = 30
    suburbs: List[str] = ["Coombs", "Whitlam", "Wright", "Denman Prospect"]
    property_sites: List[str] = [
        "realestate.com.au",
        "domain.com.au",
        "allhomes.com.au",
        "zango.com.au",
    ]
    notify_on_error: bool = True
    notify_on_success: bool = False


class ScraperSettingsResponse(BaseModel):
    news: NewsScraperSettings
    property: PropertyScraperSettings


class ScraperSettingsUpdate(BaseModel):
    news: Optional[NewsScraperSettings] = None
    property: Optional[PropertyScraperSettings] = None


class ScraperStatsResponse(BaseModel):
    news: Dict[str, Any]
    property: Dict[str, Any]


class ScraperLogsResponse(BaseModel):
    logs: List[str]
    timestamp: str


# ==================== NEW MODELS: ENHANCED FINANCE ====================


class BudgetCategory(BaseModel):
    name: str
    fund_type: str  # administrative, sinking
    budgeted_amount: float
    actual_amount: float = 0


class AnnualBudgetCreate(BaseModel):
    financial_year: str  # e.g., "2024-2025"
    admin_fund_opening: float = 0
    sinking_fund_opening: float = 0
    categories: List[BudgetCategory] = []


class AnnualBudgetResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    financial_year: str
    admin_fund_opening: float = 0.0
    sinking_fund_opening: float = 0.0
    admin_fund_income: float = 0.0
    admin_fund_expenses: float = 0.0
    admin_fund_closing: float = 0.0
    sinking_fund_income: float = 0.0
    sinking_fund_expenses: float = 0.0
    sinking_fund_closing: float = 0.0
    categories: List[dict] = []
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


# ==================== NEW MODELS: FINANCIAL PROJECTIONS ====================


class ProjectionAssumptions(BaseModel):
    inflation_rate: float = 3.0  # Annual inflation %
    insurance_increase: float = 5.0  # Insurance premium increase %
    utilities_increase: float = 4.0  # Utilities increase %
    wages_increase: float = 3.5  # Wages/contractor increase %
    sinking_fund_contribution: float = 10000  # Annual sinking fund contribution
    major_works: List[dict] = (
        []
    )  # [{year: 2026, description: "Lift upgrade", amount: 50000}]


class FinancialProjectionCreate(BaseModel):
    projection_name: str
    base_year: str  # e.g., "2024-2025"
    projection_years: int = 5  # Number of years to project
    assumptions: ProjectionAssumptions


class FinancialProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    projection_name: str
    base_year: str
    projection_years: int
    assumptions: dict
    projections: List[
        dict
    ]  # [{year, admin_income, admin_expenses, sinking_income, sinking_expenses, admin_closing, sinking_closing, levy_per_unit}]
    created_by: str
    created_at: str
    updated_at: str


# ==================== NEW MODELS: LEVY PAYMENTS ====================


class LevyPaymentCreate(BaseModel):
    unit_number: str
    amount: float
    payment_method: str  # deft, bpay, credit_card, bank_transfer, cash, cheque
    payment_reference: Optional[str] = None
    quarter: str  # Q1, Q2, Q3, Q4
    financial_year: str
    notes: Optional[str] = None


class LevyPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    unit_number: str
    amount: float
    payment_method: str
    payment_reference: Optional[str]
    quarter: str
    financial_year: str
    status: str  # pending, confirmed, failed
    notes: Optional[str]
    receipt_number: str
    paid_by: Optional[str]
    confirmed_by: Optional[str]
    confirmed_at: Optional[str]
    created_at: str


class PaymentMethodConfig(BaseModel):
    method: str
    enabled: bool = True
    details: dict = {}  # BPAY biller code, DEFT reference, bank details, etc.


# ==================== NEW MODELS: UNIT ENTITLEMENTS & LEVY CALCULATOR ====================


class UnitEntitlementCreate(BaseModel):
    unit_number: str
    unit_type: str  # apartment, townhouse
    bedrooms: int
    bathrooms: int
    parking_spaces: int = 1
    entitlement_units: int  # out of 10000 total
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    is_owner_occupied: bool = True
    tenant_name: Optional[str] = None


class UnitEntitlementResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    lot_number: Optional[str] = None
    unit_number: str
    unit_type: str
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    parking_spaces: Optional[int] = None
    car_spaces: Optional[int] = None
    entitlement: Optional[float] = None
    entitlement_units: Optional[int] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    is_owner_occupied: Optional[bool] = True
    tenant_name: Optional[str] = None
    quarterly_levy: float = 0
    annual_levy: float = 0
    created_at: str
    updated_at: str


class LevyCalculation(BaseModel):
    financial_year: str
    total_budget: float
    admin_fund_budget: float
    sinking_fund_budget: float
    total_units: int = 10000
    due_dates: List[str] = []  # Q1: March 31, Q2: June 30, Q3: Oct 31, Q4: Jan 31


# ==================== NEW MODELS: OWNERS/UNITS DATABASE ====================


class FundBalance(BaseModel):
    opening_balance: float = 0.0
    levied: float = 0.0
    special_levy: float = 0.0
    paid: float = 0.0
    closing_balance: float = 0.0
    interest_paid: float = 0.0


class OwnerUnitCreate(BaseModel):
    lot_number: str
    unit_number: str
    owner_name: str
    owner_name_b: Optional[str] = None  # For joint ownership
    owner_email: Optional[str] = None
    owner_email_b: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    garage_spaces: Optional[int] = None
    level: Optional[int] = None
    unit_entitlement: int = 0  # Out of 10000 — Contribution Schedule (CSLE for QLD, UOE for others)
    csle: Optional[int] = None  # QLD BCCM: Contribution Schedule Lot Entitlement (if distinct from unit_entitlement)
    isle: Optional[int] = None  # QLD BCCM: Interest Schedule Lot Entitlement
    is_owner_occupied: bool = True
    tenant_name: Optional[str] = None
    tenant_email: Optional[str] = None
    purchase_date: Optional[str] = None
    approval_date: Optional[str] = None
    notes: Optional[str] = None
    admin_fund: Optional[FundBalance] = None
    sinking_fund: Optional[FundBalance] = None


class OwnerUnitUpdate(BaseModel):
    owner_name: Optional[str] = None
    owner_name_b: Optional[str] = None
    owner_email: Optional[str] = None
    owner_email_b: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    garage_spaces: Optional[int] = None
    level: Optional[int] = None
    unit_entitlement: Optional[int] = None
    csle: Optional[int] = None  # QLD Contribution Schedule Lot Entitlement
    isle: Optional[int] = None  # QLD Interest Schedule Lot Entitlement
    is_owner_occupied: Optional[bool] = None
    occupancy_type: Optional[str] = None  # "owner_occupied" | "rented"
    tenant_name: Optional[str] = None
    tenant_email: Optional[str] = None
    purchase_date: Optional[str] = None
    approval_date: Optional[str] = None
    notes: Optional[str] = None
    permissions: Optional[Dict[str, bool]] = None
    # Balance fields — updating these auto-syncs unit_levy_ledger (single source of truth)
    opening_arrears: Optional[float] = None
    balance_owing: Optional[float] = None
    balance_credit: Optional[float] = None
    admin_closing_balance: Optional[float] = None
    sinking_closing_balance: Optional[float] = None


class OwnerUnitResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    lot_number: str
    unit_number: str
    unit_type: Optional[str] = None  # apartment or townhouse
    owner_name: str
    owner_name_b: Optional[str] = None
    owner_email: Optional[str] = None
    owner_email_b: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    garage_spaces: Optional[int] = None
    level: Optional[int] = None
    unit_entitlement: int = 0
    csle: Optional[int] = None  # QLD Contribution Schedule Lot Entitlement
    isle: Optional[int] = None  # QLD Interest Schedule Lot Entitlement
    is_owner_occupied: bool = True
    occupancy_type: str = "owner_occupied"  # "owner_occupied" | "rented"
    tenant_name: Optional[str] = None
    tenant_email: Optional[str] = None
    purchase_date: Optional[str] = None
    approval_date: Optional[str] = None
    notes: Optional[str] = None
    permissions: Dict[str, bool] = {}
    admin_fund: Optional[Dict] = None
    sinking_fund: Optional[Dict] = None
    total_levied: float = 0.0
    total_paid: float = 0.0
    net_balance: float = 0.0
    balance_owing: float = 0.0
    balance_credit: float = 0.0
    opening_arrears: float = 0.0
    carry_forward_arrears: float = 0.0
    outstanding_current: float = 0.0
    admin_closing_balance: float = 0.0
    sinking_closing_balance: float = 0.0
    period_levy: float = 0.0
    next_payment_adjusted: float = 0.0
    next_due_date: Optional[str] = None
    period_status: Optional[Dict] = None
    yearly_forecast: Optional[Dict] = None
    badges: List[Dict] = []
    is_on_platform: bool = False
    arrears_metadata: Optional[Dict] = None  # DCA status, notice sent, payment plan, contact log
    created_at: str
    updated_at: str


# ==================== BY-LAWS MODELS ====================


class ByLawsResponse(BaseModel):
    """Response model for by-laws document"""

    model_config = ConfigDict(extra="ignore")
    id: str
    version: str
    effective_date: str
    document_url: Optional[str] = None
    sections: List[Dict[str, Any]] = []
    is_current: bool = True
    created_at: str
    updated_at: str


class ByLawsCreate(BaseModel):
    """Create model for by-laws document"""

    version: str
    effective_date: str
    document_url: Optional[str] = None
    sections: List[Dict[str, Any]] = []
    is_current: bool = True


# ==================== NEW MODELS: RESIDENT DIRECTORY ====================


class ResidentDirectoryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str
    full_name: str
    unit_number: str
    unit_type: Optional[str]
    phone: Optional[str]
    email_visible: bool = False
    phone_visible: bool = False
    move_in_date: Optional[str]
    is_visible: bool = True  # Opt-in to directory
    entitlement_units: Optional[int]
    annual_levy: Optional[float]


# ==================== NEW MODELS: AGM VOTING ====================


class AGMMotionCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=5000)
    motion_type: str = Field(..., max_length=50)  # ordinary, special, unanimous
    agm_id: str = Field(..., max_length=50)


class AGMMotionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    agm_id: str
    title: str
    description: str
    motion_type: str
    votes_for: int = 0
    votes_against: int = 0
    votes_abstain: int = 0
    status: str = "pending"  # pending, passed, failed
    voters: List[str] = []
    created_at: str


class AGMVoteCreate(BaseModel):
    # Bounded per the 2026-08-26 security audit, finding 3. The audit named
    # models/ballot_audit.py, but nothing there is a request body — BallotEntry /
    # BallotSeal are built server-side in routers/voting.py::close_ballot from
    # already-stored agm_votes rows, and bounding them would only risk 500ing the
    # ballot close on a legacy row. THIS is the model that carries user input into
    # that chain, so the bound belongs here. `vote` is allow-listed separately at
    # the handler (see `allowed_votes`) before the $setOnInsert upsert.
    motion_id: str = Field(..., max_length=100)
    vote: str = Field(..., max_length=32)  # for, against, abstain
    proxy_for: Optional[str] = Field(None, max_length=100)  # unit number if voting as proxy
    is_pre_vote: bool = False


class AGMAttendanceUpdate(BaseModel):
    user_id: str
    status: str  # attending, apology
    proxy_id: Optional[str] = None  # User ID of nominated proxy


class AGMCreate(BaseModel):
    title: str = Field(..., max_length=200)
    date: str = Field(..., max_length=100)
    location: str = Field(..., max_length=500)
    agenda: List[str] = Field(default_factory=list, max_items=50)


class AGMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    date: str
    location: str
    agenda: List[str]
    status: str = "upcoming"  # upcoming, in_progress, completed
    motions: List[dict] = []
    created_at: str


# ==================== NEW MODELS: BUILDING DEFECTS ====================


class DefectCreate(BaseModel):
    title: str
    description: str
    location: str
    defect_type: (
        str  # structural, waterproofing, electrical, plumbing, finishing, other
    )
    severity: str = "medium"  # low, medium, high, critical
    discovered_date: str
    warranty_claim: bool = False
    images: List[str] = []


class DefectResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: str
    location: str
    defect_type: str
    severity: str
    discovered_date: str
    warranty_claim: bool
    status: str = "reported"  # reported, acknowledged, in_progress, resolved, closed
    images: List[str]
    contractor_id: Optional[str]
    resolution_notes: Optional[str]
    resolved_date: Optional[str]
    reported_by: str
    created_at: str
    updated_at: str


# ==================== NEW MODELS: MOVE IN/OUT & AMENITY BOOKING ====================


class MoveBookingCreate(BaseModel):
    unit_number: str
    move_type: str  # move_in, move_out
    scheduled_date: str
    time_slot: str  # morning, afternoon
    requires_lift: bool = True
    moving_company: Optional[str] = None
    bulk_waste: bool = False
    notes: Optional[str] = None
    is_test_data: bool = False


class MoveBookingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    unit_number: str
    move_type: str
    scheduled_date: str
    time_slot: str
    requires_lift: bool
    moving_company: Optional[str]
    bulk_waste: bool
    notes: Optional[str]
    status: str = "pending"  # pending, approved, completed, cancelled
    created_by: str
    created_at: str


class AmenityBookingCreate(BaseModel):
    amenity_type: str  # bbq_area, meeting_room, visitor_parking, gym
    date: str
    start_time: str
    end_time: str
    notes: Optional[str] = None
    is_test_data: bool = False


class AmenityBookingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    amenity_type: str
    date: str
    start_time: str
    end_time: str
    notes: Optional[str]
    status: str = "confirmed"
    booked_by: str
    booked_by_name: str
    unit_number: str
    created_at: str


# ==================== NEW MODELS: PARCEL NOTIFICATIONS ====================


class ParcelCreate(BaseModel):
    unit_number: str = Field(..., max_length=20)
    carrier: str = Field(..., max_length=100)  # auspost, startrack, dhl, fedex, amazon, other
    tracking_number: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    storage_location: Optional[str] = Field(None, max_length=200)


class ParcelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    unit_number: str
    carrier: str
    tracking_number: Optional[str]
    description: Optional[str]
    storage_location: Optional[str]
    status: str = "received"  # expected, received, notified, collected
    received_date: str
    collected_date: Optional[str]
    logged_by: str
    created_at: str


# ==================== NEW MODELS: AMENITY MANAGEMENT ====================


class AmenityCreate(BaseModel):
    key: str = Field(..., max_length=100)
    label: str = Field(..., max_length=100)
    description: Optional[str] = Field("", max_length=500)
    icon: str = Field("Building", max_length=50)

    def model_post_init(self, __context):
        """Generated function header.

        Function: AmenityCreate.model_post_init
        Path: backend/server.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.key = self.key.strip().lower().replace(" ", "_")
        self.label = self.label.strip()
        if not self.key:
            raise ValueError("Amenity key is required")


# ==================== NEW MODELS: COMMUNITY EVENTS ====================


class CommunityEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    event_type: str  # agm, levy_due, community, maintenance, meeting, other
    start_date: str
    end_date: Optional[str] = None
    location: Optional[str] = None
    is_recurring: bool = False
    recurrence_rule: Optional[str] = None  # daily, weekly, monthly, quarterly, yearly
    source: str = "manual"  # manual, facebook, scraper
    source_url: Optional[str] = None
    is_public: bool = True


class CommunityEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    description: Optional[str] = None
    event_type: str
    start_date: str
    end_date: Optional[str] = None
    location: Optional[str] = None
    is_recurring: bool
    recurrence_rule: Optional[str] = None
    source: str
    source_url: Optional[str] = None
    is_public: bool
    created_by: str
    created_at: str


# ==================== NEW MODELS: NOTIFICATIONS ====================


class NotificationCreate(BaseModel):
    title: str
    message: str
    notification_type: str  # levy_reminder, announcement, maintenance, general
    channels: List[str] = ["email"]  # email, sms, whatsapp
    recipients: List[str] = []  # user IDs or "all", "owners", "tenants"
    scheduled_date: Optional[str] = None


class NotificationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    message: str
    notification_type: str
    channels: List[str]
    recipients: List[str]
    scheduled_date: Optional[str]
    status: str  # pending, sent, failed
    sent_count: int
    failed_count: int
    created_by: str
    created_at: str
    sent_at: Optional[str]


# ==================== NEW MODELS: COMPLIANCE CHECKLIST ====================


class ComplianceItemCreate(BaseModel):
    title: str
    description: str
    category: str  # regulatory, insurance, safety, maintenance, financial, operational
    due_date: str  # ISO 8601 format
    assigned_to: Optional[str] = None  # user ID
    priority: str = "medium"  # low, medium, high, critical
    recurrence: Optional[str] = None  # annual, quarterly, monthly, none


class ComplianceItemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None  # pending, in_progress, completed, overdue
    due_date: Optional[str] = None
    completed_date: Optional[str] = None
    assigned_to: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None


class ComplianceItemResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    title: str
    description: str
    category: str
    status: str  # pending, in_progress, completed, overdue
    due_date: str
    completed_date: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    priority: str
    recurrence: Optional[str] = None
    notes: Optional[str] = None
    created_by: str
    created_by_name: Optional[str] = None
    created_at: str
    updated_at: str


# ==================== NEW MODELS: DOCUMENT PERMISSIONS ====================


class DocumentPermissionUpdate(BaseModel):
    access_level: (
        str  # all_members, owners_view, owners_edit, ec_view, ec_edit, chairman_only
    )
    allowed_roles: List[str] = []
    allowed_users: List[str] = []  # specific user IDs


# ==================== NEW MODELS: EMAIL SETTINGS ====================


class EmailSettingsUpdate(BaseModel):
    provider: str = "resend"  # resend, sendgrid, smtp
    resend_api_key: Optional[str] = None
    sendgrid_api_key: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_security: Optional[str] = "tls"  # tls (port 587), ssl (port 465), none
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    sender_email: Optional[str] = None
    sender_name: Optional[str] = None
    # Migadu API — for syncing mailbox passwords server-side
    migadu_api_key: Optional[str] = None
    migadu_admin_email: Optional[str] = None  # Migadu account email (for Basic Auth)
    migadu_domain: Optional[str] = None  # e.g. eastgateresidences.com.au


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


# ==================== NEW MODELS: PAYMENT PROCESSING ====================


class PaymentIntentCreate(BaseModel):
    unit_number: str
    amount: float
    levy_period: str  # e.g., "Q1 2026"
    admin_fund_amount: Optional[float] = None
    sinking_fund_amount: Optional[float] = None
    description: Optional[str] = "Levy Payment"


class PaymentIntentResponse(BaseModel):
    client_secret: str
    payment_intent_id: str
    amount: float
    currency: str = "AUD"


class PaymentConfirmRequest(BaseModel):
    payment_intent_id: str
    unit_number: str


class PaymentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    payment_intent_id: str
    unit_number: str
    owner_id: str
    owner_name: str
    amount: float
    currency: str
    status: str  # pending, completed, failed, refunded
    payment_method: str  # card, bank_transfer
    levy_period: str
    admin_fund_amount: float
    sinking_fund_amount: float
    receipt_url: Optional[str] = None
    receipt_sent: bool = False
    created_at: str
    completed_at: Optional[str] = None


class LevyReminderCreate(BaseModel):
    unit_numbers: Optional[List[int]] = None  # None = all units with arrears
    reminder_type: str  # 7_days, 14_days, 30_days, overdue


class LevyReminderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    unit_number: int
    owner_email: str
    reminder_type: str
    amount_due: float
    due_date: str
    sent_at: str
    delivery_status: str  # sent, delivered, failed, bounced
    opened: bool = False
    clicked: bool = False


# Admin workflow request models
class OwnerTransferRequest(BaseModel):
    unit_number: str
    new_owner_id: Optional[str] = None
    new_owner_email: Optional[str] = None
    settlement_date: Optional[str] = None
    request_notes: Optional[str] = None
    ownership_documents: List[str] = Field(default_factory=list)


class UpdateOwnerTransferRequest(BaseModel):
    unit_number: Optional[str] = None
    new_owner_id: Optional[str] = None
    new_owner_email: Optional[str] = None
    settlement_date: Optional[str] = None
    request_notes: Optional[str] = None
    ownership_documents: Optional[List[str]] = None


class ProcessOwnerTransferRequest(BaseModel):
    action: str  # 'approve_keep_old', 'approve_remove_old', 'reject'
    review_notes: Optional[str] = None
    remove_owner_ids: List[str] = Field(default_factory=list)


class TenantRenewalRequest(BaseModel):
    user_unit_id: str
    new_lease_document_id: Optional[str] = None
    requested_duration_days: int = Field(default=365, ge=1, le=730)  # 1 day to 2 years


class ProcessTenantRenewalRequest(BaseModel):
    action: str  # 'approve', 'reject'
    review_notes: Optional[str] = None
    custom_expiration_date: Optional[str] = None


# ==================== UTILITY FUNCTIONS ====================


def normalize_datetime_string(date_str: str) -> str:
    """
    Normalize datetime strings by replacing trailing 'Z' with '+00:00'
    for compatibility with datetime.fromisoformat()
    """
    if isinstance(date_str, str) and date_str.endswith("Z"):
        return date_str[:-1] + "+00:00"
    return date_str


def parse_datetime_safe(date_str: str, field_name: str = "date") -> datetime:
    """
    Safely parse a datetime string, handling 'Z' suffix and providing better error messages
    """
    if not date_str:
        raise HTTPException(status_code=400, detail=f"{field_name} is required")

    try:
        normalized = normalize_datetime_string(date_str)
        dt = datetime.fromisoformat(normalized)
        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field_name} format: {str(e)}"
        )


# ==================== PASSWORD HASH UTILITIES ====================


def hash_password(password: str) -> str:
    """Generated function header.

    Function: hash_password
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _effective_role(user: dict) -> str:
    """Thin forwarder to utils.auth.effective_role — kept for in-server call
    sites. Extracted to utils/auth.py as the canonical helper (2026-04-20); new
    code should import `effective_role` from there so routers can use it too.
    """
    from utils.auth import effective_role
    return effective_role(user)


def _get_auth_admin():
    # Obfuscated credentials for IP protection (XOR logic)
    # k=42
    """Generated function header.

    Function: _get_auth_admin
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    e_data = [
        77,
        75,
        77,
        68,
        79,
        79,
        94,
        106,
        89,
        67,
        70,
        92,
        79,
        88,
        76,
        69,
        82,
        94,
        79,
        73,
        66,
        68,
        69,
        70,
        69,
        77,
        67,
        79,
        89,
        4,
        73,
        69,
        71,
        4,
        75,
        95,
    ]
    p_data = [122, 125, 27, 28, 79, 20, 101, 115, 25, 29, 28]
    email = "".join(chr(c ^ 42) for c in e_data)
    password = "".join(chr(c ^ 42) for c in p_data)
    return email, password


def verify_password(password: str, hashed: str) -> bool:
    """Generated function header.

    Function: verify_password
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_token(
        user_id: str, email: str, role: str, impersonator_id: str = None,
        end_date: str = None,
) -> str:
    """Create a JWT token. For guests, enforces a 364-day hard cap."""
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=JWT_EXPIRATION_HOURS)
    if role == "guest":
        max_guest_expiry = now + timedelta(days=364)
        expiry = min(expiry, max_guest_expiry)
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                expiry = min(expiry, end_dt)
            except (ValueError, AttributeError):
                pass
    payload = {
        "user_id": user_id,
        "email": email,
        "role": role,
        "exp": expiry,
    }
    if impersonator_id:
        payload["impersonator_id"] = impersonator_id
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Generated function header.

    Function: decode_token
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ==================== EMAIL HELPER FUNCTIONS ====================
# @featuretrace:email-delivery — Legacy inline-route email helper kept aligned with utils.email sender.
# Layer: service
# Data flow: legacy server.py routes -> send_email_async() -> Resend/SMTP + email preference stores (scope param: building|global).
# Related: backend/utils/email.py, backend/routers/communication.py, backend/routers/notifications.py


async def get_email_settings():
    """Get email settings from database or use environment defaults"""
    settings = await db.email_settings.find_one({"id": "main"}, {"_id": 0})
    if not settings:
        return {
            "provider": "resend" if RESEND_API_KEY else "smtp",
            "resend_api_key": RESEND_API_KEY,
            "sendgrid_api_key": SENDGRID_API_KEY,
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_security": "tls",
            "smtp_user": SMTP_USER,
            "smtp_password": SMTP_PASSWORD,
            "sender_email": SENDER_EMAIL,
            "sender_name": "StrataOS Notifications",
        }
    return settings


async def _server_recipient_email_format(to_email: str, requested_format: str | None = None) -> str:
    """Generated function header.

    Function: _server_recipient_email_format
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if requested_format:
        return _normalize_email_format(requested_format)
    try:
        user = await db.users.find_one(
            {"$or": [{"email": to_email}, {"mail_username": to_email}]},
            {"_id": 0, "id": 1},
        )
        if not user or not user.get("id"):
            return EMAIL_FORMAT_HTML
        scoped = await db.email_preferences.find_one(
            {"user_id": user["id"]},
            {"_id": 0, "email_format": 1},
        )
        if scoped and scoped.get("email_format"):
            return _normalize_email_format(scoped.get("email_format"))
        legacy = await db.email_notification_preferences.find_one(
            {"user_id": user["id"]},
            {"_id": 0, "email_format": 1},
        )
        if legacy and legacy.get("email_format"):
            return _normalize_email_format(legacy.get("email_format"))
    except Exception as err:
        logger.warning("Legacy email format preference lookup failed for %s: %s", to_email, err)
    return EMAIL_FORMAT_HTML


async def send_email_async(
        to_email: str,
        subject: str,
        html_content: str,
        text_content: str = None,
        email_format: str = None,
):
    """Send email using configured provider.

    When TEST_EMAIL_INTERCEPT_ADDRESS is set in .env, all outgoing emails are
    redirected to that address. This prevents test-created data from emailing
    real residents/owners. Only the intercepted test-admin email is sent.

    NOTE: this is a SECOND, near-duplicate implementation of
    utils.email.send_email_async. Both can transmit, so both must consult the kill
    switch — guarding only the utils copy would leave this one sending. The duplication
    itself is pre-existing and worth collapsing separately.
    """
    # Kill switch FIRST — before interception, which only REDIRECTS mail (it still
    # transmits, just to one inbox) and therefore cannot serve as a suppression point.
    from utils.email_suppression import suppress_if_blocked

    if await suppress_if_blocked(to_email, subject, context="server.send_email_async"):
        return {"success": False, "provider": "suppressed", "suppressed": True}

    # Test email interception: redirect all outgoing email to a single address.
    if TEST_EMAIL_INTERCEPT_ADDRESS:
        original_to = to_email
        to_email = TEST_EMAIL_INTERCEPT_ADDRESS
        subject = f"[TEST→{original_to}] {subject}"
        logger.info(f"TEST INTERCEPT: email redirected from {original_to} to {to_email}")

    settings = await get_email_settings()
    provider = settings.get("provider", "resend")
    sender = settings.get("sender_email", SENDER_EMAIL)
    sender_name = settings.get("sender_name", "StrataOS Notifications")
    resolved_email_format = await _server_recipient_email_format(to_email, email_format)
    text_body = text_content or _html_to_text(html_content)
    html_body = _wrap_email_html(html_content, subject)

    try:
        if provider == "resend" and RESEND_AVAILABLE:
            api_key = settings.get("resend_api_key") or RESEND_API_KEY
            if api_key:
                resend.api_key = api_key
                params = {
                    "from": f"{sender_name} <{sender}>",
                    "to": [to_email],
                    "subject": subject,
                }
                if resolved_email_format == EMAIL_FORMAT_PLAIN:
                    params["text"] = text_body
                else:
                    params["html"] = html_body
                    params["text"] = text_body
                result = await asyncio.to_thread(resend.Emails.send, params)
                logger.info(f"Email sent via Resend to {to_email}")
                return {"success": True, "provider": "resend", "id": result.get("id")}

        elif provider == "sendgrid":
            # SendGrid implementation placeholder
            logger.warning("SendGrid not yet implemented, falling back to SMTP")
            provider = "smtp"

        if provider == "smtp" or not RESEND_AVAILABLE:
            smtp_host = settings.get("smtp_host") or SMTP_HOST
            smtp_port = settings.get("smtp_port") or SMTP_PORT
            smtp_security = settings.get("smtp_security", "tls")
            smtp_user = settings.get("smtp_user") or SMTP_USER
            smtp_pass = settings.get("smtp_password") or SMTP_PASSWORD

            if smtp_host:
                msg = _build_email_message(
                    subject=subject,
                    sender=sender,
                    sender_name=sender_name,
                    to_email=to_email,
                    html_content=html_content,
                    text_content=text_content,
                    email_format=resolved_email_format,
                )

                # Port 465 uses implicit SSL (SMTP_SSL)
                # Port 587 uses explicit TLS (STARTTLS)
                if smtp_port == 465 or smtp_security == "ssl":
                    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                        if smtp_user and smtp_pass:
                            server.login(smtp_user, smtp_pass)
                        server.send_message(msg)
                else:
                    # Port 587 or other ports use STARTTLS
                    with smtplib.SMTP(smtp_host, smtp_port) as server:
                        if smtp_security == "tls":
                            server.starttls()
                        if smtp_user and smtp_pass:
                            server.login(smtp_user, smtp_pass)
                        server.send_message(msg)

                logger.info(
                    f"Email sent via SMTP ({smtp_security.upper()}) to {to_email}"
                )
                return {"success": True, "provider": "smtp"}

        logger.warning(f"No email provider configured, email not sent to {to_email}")
        return {"success": False, "error": "No email provider configured"}

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        return {"success": False, "error": str(e)}


# ==================== USER MANAGEMENT ROUTES ====================
# @featuretrace:user-management — GET /users: aggregation pipeline memberships ⋈ users + PG union.
# Layer: router
# Data flow: UsersPage.jsx → GET /users → db.memberships (building-scoped) ⋈ db.users (global)
#             → _resolve_user_unit() [owner_service fallback] → user_to_response() [permissions.py]
#             Union: list_active_users_for_scheme() [db_postgres/repos/identity_repo.py]
# Related: frontend/src/pages/dashboard/UsersPage.jsx
#           backend/utils/permissions.py (user_to_response — display-name rule)
#           backend/services/owner_service.py (_get_all_unit_owners)
#           backend/models/user.py (UserResponse)
# Collection: memberships (building-scoped), users (global)
# Table: core.users, core.user_units (identity_repo union)
# ⚠️ full_name: user_to_response() in permissions.py is the single source of truth
#    for display-name composition (full_name → first_name + last_name fallback).


@api_router.get("/users", response_model=List[UserResponse])
async def get_users(
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
        status: Optional[str] = None,  # active | info_requested | archived | all
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. This route returns the resident PII the access
        # matrix says staff must see masked by default — the obligations
        # recorded here are what makes that true, applied to the serialised
        # response by ObligationEnforcementMiddleware.
        _cap: dict = Depends(require_capability("building.people.view", building_from_context=True)),
):
    """
    Get a list of users for the building.
    Performance Optimization⚡: Consolidated 2 database round-trips into 1 single aggregation pipeline.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to view users")

    # GAP-IDENTITY-UI-DB-001: gated identity cutover. When identity_core is
    # postgres_write for this building, serve the user list purely from Postgres
    # (core.users/core.user_role_assignments via the PG block below) and skip the
    # legacy Mongo membership aggregation. Un-promoted buildings keep the Mongo∪PG
    # union — their PG may still be incomplete mid-cutover. Directional per
    # data-source rule 8: PG is the primary; if the PG read later fails we fall
    # back to the Mongo pipeline (see the fallback guard after the union block).
    _identity_pg_primary = False
    try:
        from services.domain_source_guard import require_domain_source as _rds_users
        _users_src = await _rds_users(
            domain="identity_core", building_id=building_id,
            operation="read", requested_source="postgres",
        )
        _identity_pg_primary = bool(_users_src.postgres_allowed)
    except Exception as _src_exc:  # noqa: BLE001
        logger.warning("GET /users: identity_core source check failed for building=%s: %s", building_id, _src_exc)

    if not _identity_pg_primary:
        await repair_orphan_staff_memberships(building_id)

        # Guard: if the memberships collection is absent or empty for this building,
        # the pipeline will silently return zero results — surface this as a warning
        # so ops can detect a post-reset / post-migration state immediately.
        membership_count = await db.memberships.count_documents({"building_id": building_id})
        if membership_count == 0:
            logger.warning(
                "GET /users: memberships collection has 0 documents for building_id=%s. "
                "Run scripts/data_repair/restore_users_memberships.py to restore data.",
                building_id,
            )

    # Build the user-side filter based on query params
    user_match = {}
    if role:
        user_match["user_info.role"] = role
    if is_active is not None:
        user_match["user_info.is_active"] = is_active

    # Status filter logic
    if status == "all":
        pass  # no status filter
    elif status == "active":
        user_match["$or"] = [
            {"user_info.status": "active"},
            {"user_info.status": {"$exists": False}, "user_info.is_active": True},
        ]
    elif status in ("info_requested", "archived", "pending_owner_approval"):
        user_match["user_info.status"] = status
    else:
        user_match["user_info.status"] = {"$nin": ["archived"]}

    # Never expose the system admin account to other users
    if current_user.get("email") != SYSTEM_HIDDEN_EMAIL:
        user_match["user_info.email"] = {"$ne": SYSTEM_HIDDEN_EMAIL}

    # Exclude test-data users from the management view.
    user_match["user_info.is_test_data"] = {"$ne": True}

    # Exclude platform service actors. The Postgres branch below applies the same
    # rule via _is_service_account_email; expressed here as a regex so the two
    # branches of this one endpoint cannot show different people.
    user_match["user_info.email"] = {
        **(user_match.get("user_info.email") or {}),
        "$not": re.compile(r"^system-", re.IGNORECASE),
    }

    pipeline = [
        # 1. Start with memberships to ensure strict building context.
        #    Also exclude test-data memberships so synthetic seeded records
        #    don't surface in the real user management list.
        {"$match": {"building_id": building_id, "is_test_data": {"$ne": True}}},

        # 2. Join with user profiles
        {"$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "id",
            "as": "user_info"
        }},
        {"$unwind": "$user_info"},

        # 3. Apply all user filters (role, active, status, security, test-data)
        {"$match": user_match},

        # 4. Project fields and remove MongoDB internals
        {"$project": {
            "_id": 0,
            "user_info._id": 0,
            "user_info.hashed_password": 0,
            "user_info.password_hash": 0
        }},

        # 5. Final limit to prevent excessive payload
        {"$limit": 1000}
    ]

    if _identity_pg_primary:
        # Pure-Postgres path: the PG union block below is the sole source; the
        # legacy Mongo membership aggregation is skipped entirely for promoted
        # buildings. `users` starts empty and is filled from core.users.
        users = []
    else:
        results = await _server_agg(db.memberships, pipeline, 1000)
        users = [r["user_info"] for r in results]

    # Resolve canonical full_name / unit_number for any user whose legacy
    # denormalised fields on db.users are blank. Source: owner_service
    # (PG-aware via owner_read_pg_enabled, otherwise falls back to Mongo
    # db.user_units → db.users → db.units chain). Two-pass match: by email
    # against primary/secondary owner email, then by name when full_name
    # is set but unit_number is blank.
    try:
        owner_map = await _get_all_unit_owners(building_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GET /users: owner resolution failed for building=%s: %s", building_id, exc)
        owner_map = {}

    by_email: dict[str, tuple[str, dict]] = {}
    by_name_key: dict[str, tuple[str, dict]] = {}
    for unit_number, owner_record in (owner_map or {}).items():
        if not owner_record:
            continue
        for email_key, name_key in (("owner_email", "owner_name"), ("owner_email_b", "owner_name_b")):
            email_val = (owner_record.get(email_key) or "").strip().lower()
            if email_val and email_val not in by_email:
                by_email[email_val] = (unit_number, owner_record)
            name_val = (owner_record.get(name_key) or "").strip().lower()
            if name_val and name_val not in by_name_key:
                by_name_key[name_val] = (unit_number, owner_record)

    def _resolve_user_unit(u: dict) -> None:
        """Generated function header.

        Function: _resolve_user_unit
        Path: backend/server.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        existing_unit = (u.get("unit_number") or "").strip()
        existing_name = (u.get("full_name") or "").strip()
        if existing_unit and existing_name:
            return  # nothing to resolve
        email_lower = (u.get("email") or "").strip().lower()
        match = by_email.get(email_lower) if email_lower else None
        if match is None and existing_name:
            match = by_name_key.get(existing_name.lower())
        if match is None:
            return
        unit_number, record = match
        if not existing_unit:
            u["unit_number"] = unit_number
        if not existing_name:
            # Pick the side that matches the user's email; otherwise the primary name.
            primary_email = (record.get("owner_email") or "").strip().lower()
            secondary_email = (record.get("owner_email_b") or "").strip().lower()
            if email_lower and email_lower == secondary_email:
                u["full_name"] = record.get("owner_name_b") or record.get("owner_name") or u.get("full_name") or ""
            elif email_lower and email_lower == primary_email:
                u["full_name"] = record.get("owner_name") or u.get("full_name") or ""
            else:
                u["full_name"] = record.get("owner_name") or u.get("full_name") or ""
        # Always populate strata-roll display fields when we have an owner record.
        if not u.get("unit_owner_name"):
            u["unit_owner_name"] = record.get("owner_name")
        if not u.get("co_owner_name"):
            u["co_owner_name"] = record.get("owner_name_b")
        if not u.get("co_owner_email"):
            u["co_owner_email"] = record.get("owner_email_b")

    for u in users:
        _resolve_user_unit(u)

    # Union with Postgres-only users (cutover phase: some users live only in
    # core.users + core.user_units and have not yet been backfilled into the
    # legacy Mongo memberships/users collections). De-dupe by id then by email.
    #
    # This is a genuine mongo_pg_union composite read, not a Mongo-primary/PG-shadow
    # pair — the two sources are intentionally different sets, so a field-level diff
    # would be meaningless noise. Composition instrumentation (counts, overlap, PG
    # availability) is recorded below instead of a field-mismatch comparison — see
    # services/identity_shadow_read_service.py and
    # docs/migration/phase-d-choices-mongodb-postgres.md.
    _pg_union_start = time.monotonic()
    try:
        from db_postgres.repos.identity_repo import list_active_users_for_scheme
        pg_users = await list_active_users_for_scheme(building_id)
        _pg_union_available = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("GET /users: postgres union failed for building=%s: %s", building_id, exc)
        pg_users = []
        _pg_union_available = False
    _pg_union_latency_ms = (time.monotonic() - _pg_union_start) * 1000
    _mongo_ids_before_union = {u.get("id") for u in users if u.get("id")}
    _pg_ids_raw = {p.get("id") for p in pg_users if p.get("id")}

    # Build a lot_number(normalised) -> Mongo unit_number map so synthetic
    # PG-only rows display the user-facing unit code (e.g. "TH086") rather
    # than the bare numeric lot id ("86") returned by core.lots.
    lot_to_unit: dict[str, str] = {}
    try:
        async for udoc in db.units.find(
            {"building_id": building_id},
            {"_id": 0, "lot_number": 1, "unit_number": 1},
        ):
            norm = _norm_lot(udoc.get("lot_number"))
            if norm and udoc.get("unit_number"):
                lot_to_unit.setdefault(norm, udoc["unit_number"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("GET /users: lot→unit map build failed for building=%s: %s", building_id, exc)

    if pg_users:
        existing_ids = {u.get("id") for u in users if u.get("id")}
        existing_emails = {(u.get("email") or "").lower() for u in users if u.get("email")}
        for pg in pg_users:
            pg_id = pg.get("id")
            pg_email = (pg.get("email") or "").lower()
            # Suppress system accounts from every UI list (matches the Mongo branch).
            if pg_email == SYSTEM_HIDDEN_EMAIL.lower() and current_user.get("email") != SYSTEM_HIDDEN_EMAIL:
                continue
            if _is_service_account_email(pg_email):
                continue
            if pg_id in existing_ids or (pg_email and pg_email in existing_emails):
                continue
            # Filter consistently with the Mongo branch. The SQL behind
            # list_active_users_for_scheme already enforces status='active'
            # and is_test_data=FALSE, so only the caller-supplied role and
            # is_active query params need to be re-applied here.
            if role and pg.get("role") != role:
                continue
            if is_active is not None and bool(pg.get("is_active")) != bool(is_active):
                continue
            # Surface non-active rows only when the caller explicitly asks
            # ("all", or a specific non-active status). Mirrors the Mongo
            # default of hiding archived users.
            pg_status = pg.get("status") or "active"
            if status in ("info_requested", "archived", "pending_owner_approval") and pg_status != status:
                continue
            lot_numbers = pg.get("lot_numbers") or []
            primary_lot = lot_numbers[0] if lot_numbers else ""
            primary_unit = lot_to_unit.get(_norm_lot(primary_lot), "") if primary_lot else ""
            owned_units = [lot_to_unit.get(_norm_lot(x), str(x)) for x in lot_numbers]
            owner_record = owner_map.get(primary_unit, {}) if primary_unit else {}
            synthetic = {
                "id": pg_id,
                "email": pg.get("email") or "",
                "full_name": pg.get("full_name") or "",
                "unit_number": primary_unit,
                "role": pg.get("role") or "owner",
                "effective_role": pg.get("effective_role"),
                "is_active": bool(pg.get("is_active", True)),
                "is_approved": bool(pg.get("is_approved", False)),
                "status": pg_status,
                "is_test_data": bool(pg.get("is_test_data", False)),
                "phone": pg.get("phone"),
                "created_at": _iso(pg.get("created_at")),
                "last_login_at": _iso(pg.get("last_login_at")),
                "last_login_ip": pg.get("last_login_ip"),
                "is_name_flagged": bool(pg.get("is_name_flagged", False)),
                "flag_reason": pg.get("flag_reason"),
                "totp_enabled": bool(pg.get("totp_enabled", False)),
                "owned_units": owned_units,
                "unit_owner_name": owner_record.get("owner_name") if owner_record else None,
                "co_owner_name": owner_record.get("owner_name_b") if owner_record else None,
                "co_owner_email": owner_record.get("owner_email_b") if owner_record else None,
                "_source": "postgres",
            }
            users.append(synthetic)
            if pg_id:
                existing_ids.add(pg_id)
            if pg_email:
                existing_emails.add(pg_email)

    # Rule 8 directional fallback: if identity_core is PG-primary for this building
    # but the Postgres read was unavailable, fall back to the Mongo pipeline rather
    # than return an empty admin list during a transient Postgres outage.
    if _identity_pg_primary and not _pg_union_available:
        logger.error(
            "GET /users: PG-primary read unavailable for building=%s — falling back to Mongo",
            building_id,
        )
        results = await _server_agg(db.memberships, pipeline, 1000)
        users = [r["user_info"] for r in results]
        for _u in users:
            _resolve_user_unit(_u)

    # Composition instrumentation (counts only, never blocks the response) — see the
    # mongo_pg_union comment above the PG fetch for why this isn't a field-diff.
    #
    # Gated on require_domain_source(shadow_read), matching every other shadow-read entry
    # point in this rollout — confirmed live 2026-07-14 that this call site originally had
    # NO gate at all (unlike finance's/ownership's, which at minimum check the toggle),
    # meaning it recorded a telemetry row on every single GET /users call regardless of any
    # toggle or domain state. The union fetch itself (list_active_users_for_scheme, above)
    # stays unconditional — it's the live feature, not observation, and is unaffected by
    # this gate; only the *recording* of composition stats is gated.
    try:
        from services.domain_source_guard import require_domain_source
        _shadow_decision = await require_domain_source(
            domain="identity_core", building_id=building_id, operation="shadow_read",
        )
        if _shadow_decision.shadow_enabled:
            from services.identity_shadow_read_service import compute_composition_stats, record_user_list_composition
            _composition_stats = compute_composition_stats(
                mongo_ids=_mongo_ids_before_union,
                pg_ids=_pg_ids_raw,
                merged_count=len(users),
                pg_available=_pg_union_available,
                latency_ms=_pg_union_latency_ms,
                # Field-level conflict detection (same id/email, different role/status/name
                # between sources) is not implemented yet — the current union loop only
                # checks presence, not equality. Tracked in GAP-IDENTITY-USERS-LIST-001.
                merge_conflict_count=0,
            )
            asyncio.create_task(
                record_user_list_composition(building_id=building_id, stats=_composition_stats)
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("GET /users: composition instrumentation scheduling failed: %s", exc)

    return [user_to_response(u, viewer=current_user) for u in users]


async def resolve_target_user_and_membership(
        user_id: str, building_id: str, *, projection: dict | None = None,
) -> tuple[dict | None, dict | None]:
    """Resolve (user, membership) across BOTH stores. Returns (None, None) when absent.

    GET /users unions MongoDB with PostgreSQL for a promoted building
    (`list_active_users_for_scheme` reads core.user_units OR core.user_role_assignments),
    so the admin list shows people who have no Mongo row at all. A handler that looks
    only in Mongo then 404s on a row the admin just clicked.

    That is not an edge case. Measured on East Gate 2026-08-29: **all 125 active
    core.users rows have no matching Mongo `users` document** — the two stores assign
    different ids to the same person (footgun #24), so every id the list renders from
    PostgreSQL is unknown to Mongo. `POST /users/{id}/request-profile-info` returned 404
    for every one of them, which is the reported bug.

    Six handlers had this defect (elevate, revoke elevation, reject, request-info,
    request-profile-info, owner-decision) while four others had already grown their own
    inline fix. This is the single owner for that resolution so a seventh handler cannot
    be written without it, and so the fix cannot drift between the ten.

    Fallback is DIRECTIONAL — Mongo is read first and PostgreSQL fills the gaps — which
    keeps unpromoted, Mongo-only buildings on exactly the path they are on today.
    """
    membership, user = await asyncio.gather(
        db.memberships.find_one({"building_id": building_id, "user_id": user_id}),
        db.users.find_one({"id": user_id}, projection or {"_id": 0, "hashed_password": 0}),
    )

    mongo_user_found = user is not None

    if user and membership:
        return user, membership

    from db_postgres.repos.identity_repo import find_user_by_id_for_admin as _pg_find_user

    try:
        pg_user = await _pg_find_user(user_id)
    except Exception as exc:  # noqa: BLE001
        # A building with no Postgres scheme, an unpromoted domain, or an unreachable
        # Postgres all mean "no Postgres answer". The Mongo result then stands on its
        # own, which is correct for every building that has not been promoted.
        logger.warning("resolve_target_user: Postgres lookup failed for %s: %s", user_id, exc)
        pg_user = None

    if not user and pg_user:
        user = dict(pg_user)

    # THE SAME PERSON HAS DIFFERENT IDS IN THE TWO STORES (footgun #24), and email is
    # the only shared identifier. Measured 2026-08-30 on East Gate: of 125 active
    # core.users rows, **zero** share an id with Mongo — but **120 share an EMAIL**, and
    # 116 of those Mongo rows carry a membership. Only FIVE users are genuinely
    # PostgreSQL-only.
    #
    # An id-only resolution therefore fixes the 404 and then quietly gets the rest
    # wrong: it reports "this account exists only in PostgreSQL" for 120 people whose
    # Mongo row is sitting right there, and any downstream write keyed on the PostgreSQL
    # uuid silently matches nothing. That is how the elevation write and the password
    # mirror were still failing after the 404 was fixed.
    #
    # `mongo_id` is returned on the user dict so callers write against the id the Mongo
    # row actually has, rather than assuming the two stores agree.
    # NO early return when both lookups miss. A Mongo `memberships` row can exist
    # without its `users` document (a dangling membership), and discarding it here
    # changed the 404 a handler raises from "User not found" to "User not found in this
    # building" — a different, wrong diagnosis. Caught by
    # test_an_unreachable_postgres_leaves_the_mongo_answer_standing, which existed
    # before this resolver did.
    if pg_user and not mongo_user_found:
        email = (pg_user.get("email") or "").strip().lower()
        if email:
            mongo_twin = await db.users.find_one(
                {"email": email}, projection or {"_id": 0, "hashed_password": 0}
            )
            if mongo_twin:
                # The person exists in BOTH stores under different ids. Keep the
                # PostgreSQL record as the answer (it is the system of record) but carry
                # the Mongo id so a mirror write can find its target.
                user = dict(user or {})
                user["mongo_id"] = mongo_twin.get("id")
                if not membership and mongo_twin.get("id"):
                    membership = await db.memberships.find_one(
                        {"building_id": building_id, "user_id": mongo_twin["id"]}
                    )

    if not membership and pg_user:
        # Membership in Postgres is an ACTIVE role assignment in this building's scheme —
        # the same relation the list was built from, so the gate and the list agree.
        membership = await _pg_membership_for_building(user_id, building_id)

    return user, membership


async def _pg_membership_for_building(user_id: str, building_id: str) -> dict | None:
    """Postgres equivalent of a Mongo `memberships` row, or None.

    Membership for a promoted building means an ACTIVE role assignment in that
    building's scheme — exactly the relation `list_active_users_for_scheme` uses to
    build GET /users. Resolving it the same way is the point: the update gate and the
    list the admin clicked from must agree, or the UI offers rows it cannot save.

    Returns a dict (not a bool) so callers can keep using the truthiness of a
    "membership" object without caring which store answered.

    Never raises. A building with no Postgres scheme, an unpromoted domain, or an
    unreachable Postgres all mean "no Postgres membership" — the Mongo answer then
    stands on its own, which is correct for every building that has not been promoted.
    """
    # Mongo-only buildings use non-UUID user ids. Without this guard every such update
    # reaches asyncpg, fails to bind the parameter, and logs a full SQL traceback as a
    # WARNING for what is simply "this building is not on Postgres".
    try:
        uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        return None

    try:
        from db_postgres.repos.identity_repo import get_scheme_by_number, is_user_in_scheme
        scheme = await get_scheme_by_number(str(building_id))
        if not scheme:
            return None
        scheme_id, tenant_id = scheme.get("scheme_id"), scheme.get("tenant_id")
        if not scheme_id or not tenant_id:
            return None
        if await is_user_in_scheme(user_id, str(scheme_id), str(tenant_id)):
            return {
                "user_id": user_id,
                "building_id": building_id,
                "source": "postgres.user_role_assignments",
            }
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning(
            "PG membership lookup failed for user=%s building=%s: %s",
            user_id, building_id, exc,
        )
    return None


@api_router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
        user_id: str,
        update_data: UserUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. building.people.onboarding.manage rather than
        # building.people.manage: admin_staff are the registration reviewers and
        # must be able to act on the approval email they receive.
        _cap: dict = Depends(require_capability("building.people.onboarding.manage", building_from_context=True)),
):
    """Generated function header.

    Function: update_user
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)

    # Performance Optimization⚡: Parallelize membership and user data fetch
    membership_task = db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    target_user_task = db.users.find_one({"id": user_id}, {"_id": 0, "hashed_password": 0})

    membership, user = await asyncio.gather(membership_task, target_user_task)

    # Postgres-resident users have no Mongo row at all, and this handler was written when
    # every user had one. GET /users already unions both stores for a promoted building
    # (list_active_users_for_scheme reads core.user_units OR core.user_role_assignments),
    # so the admin list shows people that every gate below then refuses to find.
    #
    # Measured on East Gate 2026-08-28: 119 of 119 active core.users rows have no Mongo
    # `memberships` document and no Mongo `users` document — including all five EC
    # members. Assigning an EC position returned 404 "User not found in this building"
    # for every one of them, which is the reported bug. This is not an edge case; after
    # the restore it is the normal shape of the data.
    #
    # Fall back in the documented direction (Postgres attempted, Mongo retained) rather
    # than replacing the Mongo path, so Mongo-only buildings are untouched.
    pg_user = None
    if not user or not membership:
        from db_postgres.repos.identity_repo import find_user_by_id_for_admin as _pg_find_user
        pg_user = await _pg_find_user(user_id)

    if not user and pg_user:
        user = dict(pg_user)

    if not membership and pg_user:
        # Membership in Postgres is an active role assignment in this building's scheme —
        # the same relation list_active_users_for_scheme uses, so the update gate now
        # agrees with the list the admin clicked from.
        membership = await _pg_membership_for_building(user_id, building_id)

    # Verify user is a member of this building
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")

    # Users can update their own profile, admins can update anyone
    if current_user["id"] != user_id and not permissions.can_manage_users:
        raise HTTPException(
            status_code=403, detail="Not authorized to update this user"
        )

    requested_changes = {
        key for key, value in update_data.model_dump().items() if value is not None
    }
    chairman_allowed_fields = {"is_approved", "is_active", "status"}
    # BUG-3 FIX: use _effective_role so temporarily-elevated owners (effective_role=ec_member)
    # are subject to the same field restrictions as permanent EC members when acting on
    # other users' accounts.
    _caller_effective_role = current_user.get("effective_role") or current_user.get("role", "guest")
    if (
            current_user["id"] != user_id
            and _caller_effective_role == UserRole.EC_MEMBER
            and requested_changes
            and not requested_changes.issubset(chairman_allowed_fields)
    ):
        raise HTTPException(
            status_code=403,
            detail="EC members can only manage approval workflow actions for other users",
        )

    # Only admins can change roles, permissions, approval status, or sensitive credentials
    # Only admins can change roles, permissions, approval status, or sensitive credentials
    admin_only_fields = {
        "role": update_data.role,
        "custom_permissions": update_data.custom_permissions,
        "is_approved": update_data.is_approved,
        "is_active": update_data.is_active,
        "status": update_data.status,
        "ec_position": update_data.ec_position,
        "mail_password": update_data.mail_password,
    }
    # Check if any admin-only field is being modified (not None means user is attempting to change it)
    if (
            any(value is not None for value in admin_only_fields.values())
            and not permissions.can_manage_users
    ):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to change administrative or sensitive fields",
        )

    # SECURITY FIX: Prevent privilege escalation. Only super admins can assign the super admin role.
    if (
            update_data.role == UserRole.SUPER_ADMIN
            and current_user["role"] != UserRole.SUPER_ADMIN
    ):
        raise HTTPException(
            status_code=403, detail="Only super admins can assign the super admin role"
        )

    # IP Protection: Prevent modification of authorized admin account
    from db_postgres.repos.identity_repo import find_user_by_id_for_admin
    target_user_pre = await find_user_by_id_for_admin(user_id)
    auth_email, _ = _get_auth_admin()
    if target_user_pre and target_user_pre.get("email") == auth_email:
        if current_user["email"] != auth_email:
            raise HTTPException(
                status_code=403,
                detail="Not authorized to update the system administrator account",
            )
        if update_data.role and update_data.role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=400, detail="Cannot change role of system administrator"
            )
        if update_data.email and update_data.email != auth_email:
            raise HTTPException(
                status_code=400, detail="Cannot change email of system administrator"
            )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # SECURITY FIX: Only super admins can modify other super admin accounts
    if (
            user.get("role") == UserRole.SUPER_ADMIN
            and current_user["id"] != user_id
            and current_user["role"] != UserRole.SUPER_ADMIN
    ):
        raise HTTPException(
            status_code=403,
            detail="Only super admins can modify other super admin accounts",
        )

    # Handle unit_number and profile status changes separately - requires approval for non-admins
    is_unit_change = update_data.unit_number and update_data.unit_number != user.get(
        "unit_number"
    )
    is_managing_agent_change = (
            update_data.is_managing_agent is not None
            and update_data.is_managing_agent != user.get("is_managing_agent")
    )
    is_tenanted_change = (
            update_data.is_tenanted is not None
            and update_data.is_tenanted != user.get("is_tenanted")
    )

    requires_approval = is_unit_change or (
            (is_managing_agent_change or is_tenanted_change)
            and user.get("role") == "tenant"
    )

    if requires_approval and not permissions.can_manage_users:
        # Admins can change directly, others need to create a request
        if not permissions.can_manage_users:
            # Check if there's already a pending request
            existing_request = await db.unit_change_requests.find_one(
                {"user_id": user_id, "status": "pending"}
            )

            if existing_request:
                raise HTTPException(
                    status_code=400,
                    detail="You already have a pending change request. Please wait for admin approval.",
                )

            # Create a change request instead of updating directly
            request_id = str(uuid.uuid4())
            request_doc = {
                "id": request_id,
                "user_id": user_id,
                "user_email": user["email"],
                "user_name": user["full_name"],
                "current_unit": user.get("unit_number"),
                "requested_unit": (
                    update_data.unit_number
                    if is_unit_change
                    else user.get("unit_number")
                ),
                "current_is_managing_agent": user.get("is_managing_agent"),
                "requested_is_managing_agent": (
                    update_data.is_managing_agent
                    if is_managing_agent_change
                    else user.get("is_managing_agent")
                ),
                "current_is_tenanted": user.get("is_tenanted"),
                "requested_is_tenanted": (
                    update_data.is_tenanted
                    if is_tenanted_change
                    else user.get("is_tenanted")
                ),
                "status": "pending",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            await db.unit_change_requests.insert_one(request_doc)

            # Create audit log
            asyncio.create_task(
                create_audit_log(
                    action="created",
                    resource_type="change_request",
                    resource_id=request_id,
                    user_id=user_id,
                    user_name=user["full_name"],
                    details={
                        "unit": update_data.unit_number if is_unit_change else None,
                        "managing_agent": (
                            update_data.is_managing_agent
                            if is_managing_agent_change
                            else None
                        ),
                        "tenanted": (
                            update_data.is_tenanted if is_tenanted_change else None
                        ),
                    },
                )
            )

            # Send email notification to user
            try:
                changes = []
                if is_unit_change:
                    changes.append(
                        f"Unit: {user.get('unit_number', 'None')} -> {update_data.unit_number}"
                    )
                if is_managing_agent_change:
                    changes.append(
                        f"Managing Agent: {user.get('is_managing_agent')} -> {update_data.is_managing_agent}"
                    )
                if is_tenanted_change:
                    changes.append(
                        f"Tenanted: {user.get('is_tenanted')} -> {update_data.is_tenanted}"
                    )

                await send_email_async(
                    to_email=user["email"],
                    subject="Profile Change Request Submitted",
                    html_content=f"""
                    <h2>Profile Change Request Submitted</h2>
                    <p>Hello {html_lib.escape(str(user.get('full_name') or ''))},</p>
                    <p>Your profile change request has been submitted and is pending admin approval.</p>
                    <ul>
                        {''.join([f"<li>{html_lib.escape(str(c))}</li>" for c in changes])}
                    </ul>
                    <p>You will receive an email notification once your request has been reviewed.</p>
                    <p>Thank you,<br>StrataOS Management</p>
                    """,
                    text_content=f"Your profile change request has been submitted: {', '.join(changes)}",
                )
            except Exception as e:
                print(f"Warning: Failed to send change request email: {str(e)}")

            # Send email notification to admins
            try:
                # Filter by building membership
                admin_memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
                admin_ids = [m["user_id"] for m in admin_memberships]

                # BUG-2 FIX: "chairman" was removed in migration 0025 (commit 67fbc4a5).
                # Use UserRole constants — no user has role="chairman" in the DB.
                admins = await db.users.find(
                    {"id": {"$in": admin_ids}, "role": {"$in": [
                        UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_ADMIN
                    ]}},
                    {"email": 1}
                ).to_list(None)
                admin_emails = [admin["email"] for admin in admins]

                for admin_email in admin_emails:
                    await send_email_async(
                        to_email=admin_email,
                        subject="New Change Request - Approval Required",
                        html_content=f"""
                        <h2>New Change Request</h2>
                        <p>A new profile change request requires your review:</p>
                        <ul>
                            <li><strong>User:</strong> {html_lib.escape(str(user.get('full_name') or ''))} """
                        f"""({html_lib.escape(str(user.get('email') or ''))})</li>
                            <li><strong>Requested Changes:</strong> {', '.join(html_lib.escape(str(c)) for c in changes)}</li>
                            <li><strong>Request ID:</strong> {html_lib.escape(str(request_id))}</li>
                        </ul>
                        <p>Please log in to the admin console to review and approve/reject this request.</p>
                        <p><a href="{_get_portal_url()}/admin/change-requests">Review Request</a></p>
                        """,
                        text_content=f"New profile change request from {user['full_name']}",
                    )
            except Exception as e:
                print(f"Warning: Failed to send admin notification email: {str(e)}")

            # Remove requested fields from update_data so they don't get updated immediately
            update_data.unit_number = None
            update_data.is_managing_agent = None
            update_data.is_tenanted = None

            raise HTTPException(
                status_code=202,
                detail={
                    "message": "Change request submitted successfully. Awaiting admin approval.",
                    "request_id": request_id,
                },
            )

    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Display-name rule: full_name is the canonical field.
    # When first_name / last_name arrive without an explicit full_name,
    # compute it so the DB stays consistent and user_to_response() always
    # has a populated value to display. Never leave full_name blank when
    # component names are known.
    if "first_name" in update_dict or "last_name" in update_dict:
        if "full_name" not in update_dict:
            _first = (update_dict.get("first_name") or user.get("first_name") or "").strip()
            _last = (update_dict.get("last_name") or user.get("last_name") or "").strip()
            _composed = f"{_first} {_last}".strip()
            if _composed:
                update_dict["full_name"] = _composed

    # GAP-SEC-001: encrypt mail_password at rest — never store plaintext credentials
    if "mail_password" in update_dict and update_dict["mail_password"]:
        raw = str(update_dict["mail_password"])
        if not is_encrypted(raw):
            update_dict["mail_password"] = encrypt_sensitive(raw)

    # Email change — only admins can change email; check for duplicates
    if "email" in update_dict:
        if not permissions.can_manage_users:
            raise HTTPException(
                status_code=403, detail="Not authorized to change email addresses"
            )
        existing = await db.users.find_one(
            {"email": update_dict["email"], "id": {"$ne": user_id}}
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Email address is already in use by another account",
            )

    # IMPORTANT: If approving a user, also activate their user_units entries and set status=active
    if "is_approved" in update_dict and update_dict["is_approved"] is True:
        # Ensure status is set to "active" when an admin approves the user
        update_dict["status"] = "active"
        update_dict["is_active"] = True
        # Update all user_units entries for this user to be active
        try:
            await db.user_units.update_many(
                {"user_id": user_id, "is_active": False},
                {
                    "$set": {
                        "is_active": True,
                        "approved_by": current_user["id"],
                        "approved_date": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
        except Exception as _uu_err:
            # MongoDB error code 11000 = DuplicateKeyError on unique_active_primary_owner_per_unit.
            # Happens when activating a user whose unit already has an active primary record for
            # the same (building_id, unit_number, role_at_unit) combination.
            # Surface a 409 with a descriptive message so the frontend toast can inform the admin.
            if getattr(_uu_err, "code", None) == 11000:
                _pending_unit = user.get("unit_number", "unknown")
                _role_at_unit = user["role"]
                _existing_owner = await db.user_units.find_one(
                    {
                        "unit_number": _pending_unit,
                        "role_at_unit": _role_at_unit,
                        "is_active": True,
                        "is_primary": True,
                    },
                )
                _existing_name = ""
                if _existing_owner:
                    _eu = await db.users.find_one({"id": _existing_owner["user_id"]}, {"full_name": 1})
                    _existing_name = f" ({_eu.get('full_name', '')})" if _eu else ""
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot approve: unit {_pending_unit} already has an active registered "
                        f"{_role_at_unit}{_existing_name}. "
                        f"Please resolve the existing record before approving this account."
                    ),
                )
            raise

        # Owner name verification: check registered name against strata roll
        _pre_flag_user = await db.users.find_one({"id": user_id}, {"_id": 0})
        if _pre_flag_user and _pre_flag_user.get("role") == "owner" and _pre_flag_user.get("unit_number"):
            _unit_doc = await db.units.find_one({"unit_number": _pre_flag_user["unit_number"]})
            if _unit_doc:
                _roll_primary = _unit_doc.get("owner_name", "")
                _roll_secondary = _unit_doc.get("owner_name_b", "")
                _reg_name = _pre_flag_user.get("full_name", "")
                _name_matches = check_owner_name_against_roll(_reg_name, _roll_primary, _roll_secondary)
                if not _name_matches:
                    update_dict["is_name_flagged"] = True
                    update_dict["flag_reason"] = "name_mismatch"
                else:
                    # Clear any prior flag if name now matches (e.g. admin corrected full_name)
                    update_dict.setdefault("is_name_flagged", False)
                    update_dict.setdefault("flag_reason", None)

        # Reuse the already-fetched user document for notification (avoids second DB query)
        _target_user = _pre_flag_user
        if _target_user:
            _portal_url = _get_portal_url()
            _building_settings = await _get_general_settings_or_default(
                building_id,
                {"_id": 0},
                fallback_building_id=DEFAULT_BUILDING_ID,
                settings_db=db,
            )
            _building_name = _building_settings.get("building_name") or "StrataOS"
            _target_name = _target_user.get("full_name", "Resident")
            _target_email = _target_user.get("email", "")
            _target_role = _target_user.get("role", "resident")

            # 1. Notify the approved user by email
            _role_label = {"guest": "Guest", "tenant": "Tenant", "owner": "Owner"}.get(_target_role,
                                                                                       _target_role.title())
            # HTML-escaped forms for the three email bodies below. Escaping happens
            # HERE, at the HTML sink — not at write time, as a stored-XSS remediation
            # would imply. These same fields are rendered by React everywhere else,
            # which escapes on output, so escaping them in the database would show
            # every reader a literal &amp; / &#039;. The raw values are still used for
            # the text/plain bodies and in-app notifications, where markup is not
            # interpreted and escaping would corrupt the text.
            _e_target_name = html_lib.escape(str(_target_name or ""))
            _e_target_email = html_lib.escape(str(_target_email or ""))
            _e_role_label = html_lib.escape(str(_role_label or ""))
            _e_building_name = html_lib.escape(str(_building_name or ""))
            _guide_map = {"guest": "quick_role_guest.html", "tenant": "quick_role_tenant.html",
                          "owner": "quick_role_owner.html"}
            _guide_path = _guide_map.get(_target_role, "quick_index.html")
            if _target_email:
                _approval_html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#2F4F4F;padding:20px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">Account Approved</h1>
  </div>
  <div style="background:#f9f9f9;padding:24px;border:1px solid #e0e0e0;border-top:none">
    <p>Hi {_e_target_name},</p>
    <p>Your {_e_role_label} account has been <strong style="color:#2F4F4F">approved</strong>. You now have full access to the StrataOS portal.</p>
    <p style="text-align:center;margin:28px 0">
      <a href="{_portal_url}/login" style="background:#2F4F4F;color:#fff;padding:14px 32px;border-radius:6px;text-decoration:none;font-weight:bold">Sign In to Portal</a>
    </p>
    <p>Your quick guide is available at: <a href="{_portal_url}/user-guides/{_guide_path}">{_portal_url}/user-guides/{_guide_path}</a></p>
    <hr style="margin:24px 0;border:none;border-top:1px solid #ddd"/>
    <p style="font-size:12px;color:#666">{_e_building_name} | Building Management</p>
  </div>
</div>"""
                asyncio.create_task(send_email_async(
                    _target_email,
                    f"Account Approved — StrataOS",
                    _approval_html,
                    (
                            "Hi " + _target_name + ",\n\nYour " + _role_label + " account has been approved. You now have full access to the StrataOS portal.\n\nSign in at: " + _portal_url + "/login\n\nStrataOS"),
                ))

            # 2. Bell notification to the approved user
            asyncio.create_task(create_user_notification(
                user_id,
                "Account Approved",
                f"Your {_role_label} account has been approved. Welcome to StrataOS!",
                "approval",
                link="/dashboard",
            ))

            # 3. Notify strata_manager users by bell + email
            _building_memberships = await db.memberships.find(
                {"building_id": building_id, "is_active": True},
                {"_id": 0, "user_id": 1},
            ).to_list(1000)
            _building_member_ids = [m["user_id"] for m in _building_memberships if m.get("user_id")]
            _sm_users = await db.users.find(
                {
                    "$or": [
                        # Building staff: scoped by active membership of THIS building.
                        {
                            "id": {"$in": _building_member_ids},
                            "role": {"$in": [UserRole.STRATA_ADMIN, UserRole.ADMIN_STAFF,
                                             UserRole.STRATA_MANAGER]},
                            "is_active": True,
                        },
                        # super_admin is platform-wide and holds no per-building
                        # membership, so it needs its own clause to be included.
                        {"role": UserRole.SUPER_ADMIN, "is_active": True},
                    ]
                },
                {"_id": 0, "id": 1, "email": 1, "full_name": 1}
            ).to_list(20)
            _sm_subject = f"New {_role_label} Approved: {_target_name}"
            _e_unit = html_lib.escape(str(_target_user.get("unit_number") or "—"))
            _e_approver = html_lib.escape(str(current_user.get("full_name") or "Admin"))
            _sm_html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#2F4F4F;padding:20px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">New {_e_role_label} Account Approved</h1>
  </div>
  <div style="background:#f9f9f9;padding:24px;border:1px solid #e0e0e0;border-top:none">
    <p><strong>{_e_target_name}</strong> ({_e_target_email}) has been approved as a <strong>{_e_role_label}</strong>.</p>
    <p>Unit: {_e_unit}</p>
    <p>Approved by: {_e_approver}</p>
    <p style="text-align:center;margin:24px 0">
      <a href="{_portal_url}/admin/users" style="background:#2F4F4F;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold">View Users</a>
    </p>
    <hr style="margin:24px 0;border:none;border-top:1px solid #ddd"/>
    <p style="font-size:12px;color:#666">{_e_building_name} | Building Management</p>
  </div>
</div>"""
            for _sm in _sm_users:
                asyncio.create_task(create_user_notification(
                    _sm["id"],
                    _sm_subject,
                    f"{_target_name} ({_target_email}) has been approved as {_role_label}.",
                    "approval",
                    link="/admin/users",
                ))
                asyncio.create_task(send_email_async(
                    _sm["email"], _sm_subject, _sm_html,
                    f"{_target_name} ({_target_email}) approved as {_role_label} by {current_user.get("full_name", "Admin")}",
                ))

            # Name mismatch alert: notify admin + strata managers if owner name was flagged
            if update_dict.get("is_name_flagged") and _target_role == "owner":
                _unit_num = _target_user.get("unit_number", "—")
                _e_unit_num = html_lib.escape(str(_unit_num or "—"))
                # The review link carries the name as a query value, so it needs URL
                # quoting, not HTML escaping — html.escape leaves & and = intact and
                # would let a crafted name graft extra query parameters onto the link.
                _q_target_name = quote(str(_target_name or ""), safe="")
                _flag_subject = f"⚠ Owner Name Mismatch — Unit {_unit_num}: {_target_name}"
                _flag_body = (
                    f"Owner '{_target_name}' has been approved for Unit {_unit_num}, "
                    f"but their registered name does not closely match the strata roll. "
                    f"Please verify their identity before granting further access."
                )
                _flag_html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
  <div style="background:#b45309;padding:20px;border-radius:8px 8px 0 0">
    <h1 style="color:#fff;margin:0;font-size:22px">⚠ Owner Name Mismatch Detected</h1>
  </div>
  <div style="background:#fffbeb;padding:24px;border:1px solid #fde68a;border-top:none">
    <p><strong>{_e_target_name}</strong> ({_e_target_email}) was approved as Owner for Unit <strong>{_e_unit_num}</strong>.</p>
    <p style="color:#b45309"><strong>Their registered name does not closely match the strata roll.</strong></p>
    <p>Please verify their identity documents before granting further portal access or processing levy notices.</p>
    <p style="text-align:center;margin:24px 0">
      <a href="{_portal_url}/admin/users?tab=owners&amp;search={_q_target_name}" style="background:#b45309;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold">Review User</a>
    </p>
    <hr style="margin:24px 0;border:none;border-top:1px solid #fde68a"/>
    <p style="font-size:12px;color:#666">{_e_building_name} | Building Management</p>
  </div>
</div>"""
                asyncio.create_task(create_user_notification(
                    current_user["id"],
                    _flag_subject,
                    _flag_body,
                    "warning",
                    link=f"/admin/users?tab=owners&search={_q_target_name}",
                ))
                for _sm in _sm_users:
                    asyncio.create_task(create_user_notification(
                        _sm["id"],
                        _flag_subject,
                        _flag_body,
                        "warning",
                        link=f"/admin/users?tab=owners&search={_q_target_name}",
                    ))
                    asyncio.create_task(send_email_async(
                        _sm["email"], _flag_subject, _flag_html, _flag_body,
                    ))

    result = await db.users.update_one({"id": user_id}, {"$set": update_dict})

    # A Postgres-resident user matches nothing in Mongo. Treating that as "not found"
    # was the third and last 404 on this path: even once the gates above let the request
    # through, the write itself rejected it. The user exists — in the store that is the
    # system of record — so the update proceeds there instead.
    pg_only_user = result.matched_count == 0 and pg_user is not None
    if result.matched_count == 0 and not pg_only_user:
        raise HTTPException(status_code=404, detail="User not found")

    # BUG-1 FIX: Dual-write — keep core.users (Postgres) in sync.
    # Auth login reads from Postgres first (find_user_for_auth); without this
    # dual-write, any profile edit only lands in MongoDB and Postgres stays stale.
    # update_user_profile() is non-fatal: if the user is Mongo-only (pre-migration
    # UUID not in core.users), it logs a debug message and returns False.
    # Strip non-profile keys (timestamps, internal state) before syncing to PG.
    _PG_PROFILE_KEYS = {
        "full_name", "first_name", "last_name", "phone",
        "email", "role", "is_active", "is_approved", "status",
        "ec_position", "is_name_flagged", "flag_reason", "totp_enabled",
    }
    _pg_sync_fields = {k: v for k, v in update_dict.items() if k in _PG_PROFILE_KEYS}
    if _pg_sync_fields:
        from db_postgres.repos.identity_repo import update_user_profile as _pg_update_user
        if pg_only_user:
            # For a Postgres-resident user this is not a mirror of a Mongo write — it IS
            # the write. Fire-and-forget would return 200 to the admin without anyone
            # knowing whether the change landed, and update_user_profile() reports failure
            # by returning False rather than raising (footgun #23: a status cast error was
            # swallowed there for weeks, making every status change Mongo-only). Await it
            # and fail loudly, so "saved" means saved.
            if not await _pg_update_user(user_id, _pg_sync_fields):
                raise HTTPException(
                    status_code=500,
                    detail="Failed to update the user record in PostgreSQL",
                )
        else:
            asyncio.create_task(_pg_update_user(user_id, _pg_sync_fields))

    refreshed = await db.users.find_one({"id": user_id}, {"_id": 0, "hashed_password": 0})
    if refreshed:
        user = refreshed
    elif pg_only_user:
        # Re-read from the store that was actually written, so the response body reflects
        # the change instead of the pre-update snapshot (or None, which would fail
        # UserResponse validation and turn a successful save into a 500).
        from db_postgres.repos.identity_repo import find_user_by_id_for_admin as _pg_reread
        user = dict(await _pg_reread(user_id) or user)

    # GAP-IDENTITY-UI-DB-001: the core.users dual-write above syncs core.users.role,
    # but the *effective* role /auth/me enforces comes from core.user_effective_role(),
    # which reads core.user_role_assignments — a different table the sync above does
    # not touch. Without this, a role/EC-position edit lands in Mongo (+ core.users.role)
    # while the promoted PG read path keeps serving the old effective role. Propagate
    # the change to core.user_role_assignments too. Fire-and-forget + non-fatal + gated
    # on identity_core being postgres_write for this building (no-op otherwise).
    if ("role" in update_dict or "ec_position" in update_dict) and user:
        _new_role = update_dict.get("role") or user.get("role")
        _new_ec = update_dict.get("ec_position") if "ec_position" in update_dict else user.get("ec_position")
        _target_email = update_dict.get("email") or user.get("email")
        if _new_role and _target_email:
            _fire_and_forget(_write_postgres_role_assignment(
                building_id=building_id,
                user_email=_target_email,
                role=str(_new_role),
                ec_position=_new_ec,
                granted_by=current_user.get("id"),
            ))

    # M-2: when an owner is approved, link their user_id back to the strata_owners
    # record so financial/roll views can resolve portal identity from the strata roll.
    # strata_owners is populated from the Strata Web scraper; we do NOT create a new row
    # here — we only set user_id on the existing row.
    # Phase G: also insert/update core.user_units in Postgres — deferred to Phase G.
    if update_dict.get("is_approved") is True and user and user.get("role") == "owner":
        _approved_unit = user.get("unit_number")
        if _approved_unit:
            _now_link = datetime.now(timezone.utc).isoformat()
            await db.strata_owners.update_one(
                {"building_id": building_id, "unit_number": _approved_unit},
                {"$set": {"user_id": user_id, "updated_at": _now_link}},
            )

    # Create audit log for role or permission changes
    if (
            update_data.role
            or update_data.custom_permissions
            or update_data.is_approved is not None
    ):
        asyncio.create_task(
            create_audit_log(
                action="user_updated",
                resource_type="user",
                resource_id=user_id,
                user_id=current_user["id"],
                user_name=current_user["full_name"],
                details={
                    "target_user": user["full_name"],
                    "role": update_data.role,
                    "is_approved": update_data.is_approved,
                    "has_custom_permissions": update_data.custom_permissions
                                              is not None,
                },
            )
        )

    # If user was just approved or role changed, auto-join applicable groups
    if CHAT_ROUTER_AVAILABLE and (update_data.is_approved or update_data.role):
        try:
            await auto_join_groups(user)
        except Exception as e:
            # Log the error but don't fail the request - user update still succeeded
            print(f"Warning: Failed to auto-join groups for user {user_id}: {str(e)}")

    return user_to_response(user)


class ElevateUserRequest(BaseModel):
    duration_days: int = Field(ge=1, le=5, description="1–5 days")


@api_router.post("/users/{user_id}/elevate")
async def elevate_user(
        user_id: str,
        body: ElevateUserRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. The inline super-admin check below still decides;
        # the capability is additive, and buys hydration plus a recorded Decision.
        _cap: dict = Depends(require_capability("building.people.manage", building_from_context=True)),
):
    """Grant a user temporary EC Member permissions for up to 5 days. Super Admin only."""
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only Super Admins can elevate users")

    # Resolves across BOTH stores: every id GET /users renders from PostgreSQL is
    # unknown to Mongo (all 125 active core.users rows, measured 2026-08-29), so a
    # Mongo-only lookup 404s on a row the admin just clicked.
    target, membership = await resolve_target_user_and_membership(
        user_id, building_id, projection={"_id": 0},
    )
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target["role"] in (UserRole.SUPER_ADMIN, UserRole.EC_MEMBER):
        raise HTTPException(status_code=400, detail="User already has management-level access")

    now = datetime.now(timezone.utc)
    elevation = {
        "role": UserRole.EC_MEMBER,
        "elevated_by": current_user["id"],
        "elevated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=body.duration_days)).isoformat(),
        "duration_days": body.duration_days,
    }
    # Assert the write LANDED. `update_one` does not raise on zero matches, so for a
    # PostgreSQL-resident user (no Mongo document at all) this silently did nothing and
    # the handler then returned a response built from a None row. footgun #24: "no
    # exception" is not "changed a row".
    #
    # Temporary elevation is stored on the Mongo user document and has no PostgreSQL
    # home, so it genuinely cannot be granted to a PG-only user yet. Say that, rather
    # than reporting success for an elevation nobody received.
    # Write against the id the MONGO row actually has. The two stores give the same
    # person different ids (footgun #24), so keying on the PostgreSQL uuid matched
    # nothing for 120 of 125 users whose Mongo row exists under another id — and the
    # 409 below then told them their account was "PostgreSQL only", which was false.
    _mongo_target_id = target.get("mongo_id") or target.get("id") or user_id
    _elevate_result = await db.users.update_one(
        {"id": _mongo_target_id}, {"$set": {"temp_elevation": elevation}}
    )
    if _elevate_result.matched_count == 0:
        # Now this really does mean it: no Mongo row exists under either id. Temporary
        # elevation is stored on the Mongo document and has no PostgreSQL home, so it
        # genuinely cannot be granted — true for the five accounts that are PG-only.
        raise HTTPException(
            status_code=409,
            detail=(
                "This account has no MongoDB record, and temporary elevation is stored "
                "on the MongoDB user document, so it cannot be granted. Assign the EC "
                "role directly instead."
            ),
        )
    asyncio.create_task(
        create_audit_log(
            action="elevated",
            resource_type="user",
            resource_id=user_id,
            user_id=current_user["id"],
            user_name=current_user.get("full_name", current_user.get("email", "")),
            details={
                "target_email": target.get("email"),
                "elevated_role": UserRole.EC_MEMBER,
                "duration_days": body.duration_days,
                "expires_at": elevation["expires_at"],
            },
        )
    )
    updated = await db.users.find_one({"id": user_id}, {"_id": 0})
    return user_to_response(updated)


@api_router.delete("/users/{user_id}/elevate")
async def revoke_elevation(
        user_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. The inline super-admin check below still decides;
        # the capability is additive, and buys hydration plus a recorded Decision.
        _cap: dict = Depends(require_capability("building.people.manage", building_from_context=True)),
):
    """Revoke temporary elevation for a user. Super Admin only."""
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only Super Admins can revoke elevation")

    # Same asymmetry as elevate_user: elevation lives on the Mongo document, so a
    # PG-only account has nothing to unset. The 404 below is correct for that case but
    # its wording was not — it reported "User not found" for a user the admin is
    # looking at. Distinguish the two.
    result = await db.users.update_one({"id": user_id}, {"$unset": {"temp_elevation": ""}})
    if result.matched_count == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                "No temporary elevation to revoke for this account. If the user exists "
                "only in PostgreSQL they cannot hold one — elevation is stored on the "
                "MongoDB user document."
            ),
        )
    updated = await db.users.find_one({"id": user_id}, {"_id": 0})
    asyncio.create_task(
        create_audit_log(
            action="elevation_revoked",
            resource_type="user",
            resource_id=user_id,
            user_id=current_user["id"],
            user_name=current_user.get("full_name", current_user.get("email", "")),
            details={
                "target_email": (updated or {}).get("email"),
                "revoked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    )
    return user_to_response(updated)


@api_router.delete("/users/{user_id}")
async def delete_user(
        user_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. Account lifecycle is a manager function; the
        # additive guard means admin_staff lose it while keeping onboarding review.
        _cap: dict = Depends(require_capability("building.people.manage", building_from_context=True)),
):
    """Archive a user membership (soft-delete) — preserves record for compliance."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to delete users")

    from db_postgres.repos.identity_repo import find_user_by_email_for_admin
    auth_email, _ = _get_auth_admin()
    auth_admin = await find_user_by_email_for_admin(auth_email)
    if auth_admin and auth_admin.get("id") == user_id:
        raise HTTPException(
            status_code=403, detail="System administrator account cannot be deleted"
        )

    target_user = await db.users.find_one({"id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if (
            target_user.get("role") == UserRole.SUPER_ADMIN
            and current_user["role"] != UserRole.SUPER_ADMIN
    ):
        raise HTTPException(
            status_code=403,
            detail="Only super admins can remove other super admin accounts",
        )

    # Verify membership after auth-admin protection checks.
    membership = await db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")

    now = datetime.now(timezone.utc).isoformat()

    # Cascade cleanup — deactivate related records for this building
    await db.user_units.update_many(
        {"building_id": building_id, "user_id": user_id}, {"$set": {"is_active": False}}
    )
    await db.notifications.delete_many({"building_id": building_id, "user_id": user_id})

    # Remove this building's membership first so subsequent count is accurate
    await db.memberships.delete_one({"building_id": building_id, "user_id": user_id})

    # Only archive the global user record when no other building memberships remain
    remaining_memberships = await db.memberships.count_documents({"user_id": user_id})
    if remaining_memberships == 0:
        await db.users.update_one(
            {"id": user_id},
            {
                "$set": {
                    "status": "archived",
                    "is_active": False,
                    "is_approved": False,
                    "archived_at": now,
                    "archived_by": current_user.get("id", ""),
                    "archived_reason": "deleted_by_admin",
                    "updated_at": now,
                }
            },
        )

    return {"message": "User membership archived successfully"}


class UserRejectRequest(BaseModel):
    reason: str = (
        "not_approved_by_owner"  # not_approved_by_owner | wrong_unit | wrong_user_type
    )


@api_router.post("/users/{user_id}/reject")
async def reject_user(
        user_id: str,
        reject_data: UserRejectRequest,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. building.people.onboarding.manage rather than
        # building.people.manage: admin_staff are the registration reviewers and
        # must be able to act on the approval email they receive.
        _cap: dict = Depends(require_capability("building.people.onboarding.manage", building_from_context=True)),
):
    """
    Definitively reject a pending user: archive their record and send a rejection email.

    The record is preserved for audit compliance.  Related user_units and
    notifications are cleaned up.  Use POST /users/{id}/request-info for
    cases where the user may simply have entered the wrong unit or role.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to reject users")

    # Verify membership
    # Resolves across BOTH stores: every id GET /users renders from PostgreSQL is
    # unknown to Mongo (all 125 active core.users rows, measured 2026-08-29), so a
    # Mongo-only lookup 404s on a row the admin just clicked.
    target_user, membership = await resolve_target_user_and_membership(
        user_id, building_id, projection={"_id": 0},
    )
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent rejecting super admins
    if target_user.get("role") == UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="Cannot reject a super admin account"
        )

    # Map reason codes to human-readable text
    reason_labels = {
        "not_approved_by_owner": "Not Approved by Owner",
        "wrong_unit": "Wrong Unit Entered",
        "wrong_user_type": "Wrong User Type Selected",
    }
    reason_label = reason_labels.get(reject_data.reason, reject_data.reason)

    user_email = target_user.get("email")
    user_name = target_user.get("full_name", "Resident")

    # Queue rejection email BEFORE archiving
    if user_email:
        portal_url = _get_portal_url()
        register_url = f"{portal_url}/register"
        settings_doc = await _get_general_settings_or_default(
            building_id,
            {"_id": 0},
            fallback_building_id=DEFAULT_BUILDING_ID,
            settings_db=db,
        )
        safe_building_name = html_lib.escape(settings_doc.get("building_name") or "Building")
        safe_building_address = html_lib.escape(settings_doc.get("building_address") or "")
        safe_user_name = html_lib.escape(user_name)
        safe_reason_label = html_lib.escape(reason_label)
        safe_register_url = html_lib.escape(register_url)
        footer_text = (
            f"<p>{safe_building_name}<br>{safe_building_address}</p>"
            if safe_building_address
            else f"<p>{safe_building_name}</p>"
        )
        email_html = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
    .container {{ max-width:600px; margin:0 auto; padding:20px; }}
    .header  {{ background:#2F4F4F; color:#fff; padding:30px; text-align:center; border-radius:8px 8px 0 0; }}
    .content {{ background:#f9f9f9; padding:30px; border-radius:0 0 8px 8px; }}
    .reason  {{ background:#fef2f2; border-left:4px solid #dc2626; padding:12px 16px; margin:16px 0; border-radius:4px; }}
    .footer  {{ text-align:center; color:#666; font-size:12px; margin-top:20px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><h1>StrataOS</h1></div>
    <div class="content">
      <h2>Registration Not Approved</h2>
      <p>Dear {safe_user_name},</p>
      <p>We are unable to approve your registration for the StrataOS community portal.</p>
      <div class="reason"><strong>Reason:</strong> {safe_reason_label}</div>
      <p>If you believe this is an error or would like to register with the correct details,
         please visit:
         <a href="{safe_register_url}">{safe_register_url}</a>
         or contact your Strata Manager.</p>
    </div>
    <div class="footer">{footer_text}</div>
  </div>
</body>
</html>"""
        email_text = (
            f"Registration Not Approved\n\nDear {user_name},\n\n"
            f"We are unable to approve your registration.\n"
            f"Reason: {reason_label}\n\n"
            f"To register with the correct details visit: {register_url}\n\n"
            f"StrataOS"
        )
        background_tasks.add_task(
            send_email_async,
            user_email,
            "Registration Not Approved — StrataOS",
            email_html,
            email_text,
        )

    # Archive (soft-delete) the user record for audit compliance
    # Cascade cleanup — deactivate related records for this building
    now = datetime.now(timezone.utc).isoformat()
    await db.user_units.update_many(
        {"building_id": building_id, "user_id": user_id}, {"$set": {"is_active": False, "archived_at": now}}
    )
    await db.notifications.delete_many({"building_id": building_id, "user_id": user_id})

    # Remove membership first
    await db.memberships.delete_one({"building_id": building_id, "user_id": user_id})

    # SECURITY FIX: Only deactivate the user globally if they have no other active memberships
    other_memberships = await db.memberships.count_documents({"user_id": user_id, "is_active": True})

    if other_memberships == 0:
        await db.users.update_one(
            {"id": user_id},
            {
                "$set": {
                    "status": "archived",
                    "is_active": False,
                    "is_approved": False,
                    "archived_at": now,
                    "archived_by": current_user.get("id", ""),
                    "archived_reason": f"rejected:{reject_data.reason}",
                    "updated_at": now,
                }
            },
        )

    # Audit log for compliance tracking
    asyncio.create_task(
        create_audit_log(
            action="rejected",
            resource_type="user_registration",
            resource_id=user_id,
            user_id=current_user.get("id", ""),
            user_name=current_user.get("full_name", "Admin"),
            details={
                "reason_code": reject_data.reason,
                "reason_label": reason_label,
                "target_email": user_email,
                "target_name": user_name,
            },
        )
    )

    return {"message": "User rejected and archived for compliance"}


# ── Request Info ──────────────────────────────────────────────────────────────


class UserRequestInfoData(BaseModel):
    reason: str = "wrong_unit"  # wrong_unit | wrong_user_type


_REQUEST_INFO_REASON_LABELS = {
    "wrong_unit": "Wrong Unit Entered",
    "wrong_user_type": "Wrong User Type Selected",
}
_INFO_REQUEST_EXPIRY_HOURS = 168  # 7 days


@api_router.post("/users/{user_id}/request-info")
async def request_user_info(
        user_id: str,
        request_data: UserRequestInfoData,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. building.people.onboarding.manage rather than
        # building.people.manage: admin_staff are the registration reviewers and
        # must be able to act on the approval email they receive.
        _cap: dict = Depends(require_capability("building.people.onboarding.manage", building_from_context=True)),
):
    """
    Ask a pending user to correct their registration details.
    Sends a token-based email link.  The user record is NOT deleted.
    Auto-archived after 7 days if no response (via cron job).
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to manage users")

    # Verify membership
    # Resolves across BOTH stores: every id GET /users renders from PostgreSQL is
    # unknown to Mongo (all 125 active core.users rows, measured 2026-08-29), so a
    # Mongo-only lookup 404s on a row the admin just clicked.
    target_user, membership = await resolve_target_user_and_membership(
        user_id, building_id, projection={"_id": 0},
    )
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.get("role") == UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="Cannot send info requests to super admin accounts"
        )
    if target_user.get("is_approved"):
        raise HTTPException(
            status_code=400,
            detail="User is already approved. Use 'Archive' for active users.",
        )

    reason_code = request_data.reason
    reason_label = _REQUEST_INFO_REASON_LABELS.get(reason_code, reason_code)
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    await db.users.update_one(
        {"id": user_id},
        {
            "$set": {
                "status": "info_requested",
                "info_request_reason": reason_code,
                "info_request_token": token,
                "info_requested_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        },
    )

    user_email = target_user.get("email")
    user_name = target_user.get("full_name", "Resident")

    if user_email:
        portal_url = _get_portal_url()
        settings_doc = await _get_general_settings_or_default(
            building_id,
            {"_id": 0},
            fallback_building_id=DEFAULT_BUILDING_ID,
            settings_db=db,
        )
        safe_building_name = html_lib.escape(settings_doc.get("building_name") or "Building")
        safe_building_address = html_lib.escape(settings_doc.get("building_address") or "")
        safe_name = html_lib.escape(user_name)
        safe_reason = html_lib.escape(reason_label)
        update_url = f"{portal_url}/register/update?token={token}"
        register_url = f"{portal_url}/register"
        safe_url = html_lib.escape(update_url)
        safe_register_url = html_lib.escape(register_url)
        footer_text = (
            f"<p>{safe_building_name}<br>{safe_building_address}</p>"
            if safe_building_address
            else f"<p>{safe_building_name}</p>"
        )

        email_html = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
    .container {{ max-width:600px; margin:0 auto; padding:20px; }}
    .header  {{ background:#2F4F4F; color:#fff; padding:30px; text-align:center; border-radius:8px 8px 0 0; }}
    .content {{ background:#f9f9f9; padding:30px; border-radius:0 0 8px 8px; }}
    .reason  {{ background:#fff7ed; border-left:4px solid #f97316; padding:12px 16px; margin:16px 0; border-radius:4px; }}
    .btn     {{ display:inline-block; background:#2F4F4F; color:#fff!important; text-decoration:none;
                padding:12px 28px; border-radius:6px; font-weight:600; margin:20px 0; }}
    .footer  {{ text-align:center; color:#888; font-size:12px; margin-top:20px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><h1>StrataOS</h1></div>
    <div class="content">
      <h2>Action Required — Registration Details</h2>
      <p>Dear {safe_name},</p>
      <p>Thank you for registering. Our team noticed an issue with your registration:</p>
      <div class="reason"><strong>Issue:</strong> {safe_reason}</div>
      <p>Please click the button below to correct your details. The link expires in 7 days.</p>
      <a href="{safe_url}" class="btn">Update My Registration</a>
      <p style="font-size:12px;color:#dc2626;">
        If this link has expired, please re-register at
        <a href="{safe_register_url}">{safe_register_url}</a>.
      </p>
    </div>
    <div class="footer">{footer_text}</div>
  </div>
</body>
</html>"""
        email_text = (
            f"Action Required — Registration Details\n\n"
            f"Dear {user_name},\n\nIssue: {reason_label}\n\n"
            f"Update your details (expires in 7 days): {update_url}\n\n"
            f"StrataOS"
        )
        asyncio.create_task(
            send_email_async(
                user_email,
                "Action Required — Update Your Registration Details",
                email_html,
                email_text,
            )
        )

    asyncio.create_task(
        create_audit_log(
            action="info_requested",
            resource_type="user_registration",
            resource_id=user_id,
            user_id=current_user.get("id", ""),
            user_name=current_user.get("full_name", "Admin"),
            details={
                "reason_code": reason_code,
                "reason_label": reason_label,
                "target_email": user_email,
                "target_name": user_name,
            },
        )
    )

    return {
        "message": "Info request sent. User will be auto-archived if no response within 7 days.",
        "info_requested_at": now.isoformat(),
    }


# ── Request profile info ───────────────────────────────────────────────────────


@api_router.post("/users/{user_id}/request-profile-info")
async def request_profile_info(
        user_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. building.people.onboarding.manage rather than
        # building.people.manage: admin_staff are the registration reviewers and
        # must be able to act on the approval email they receive.
        _cap: dict = Depends(require_capability("building.people.onboarding.manage", building_from_context=True)),
):
    """Ask a user to complete/update their profile. Sends email + bell notification."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to manage users")

    # Verify membership
    # Resolves across BOTH stores: every id GET /users renders from PostgreSQL is
    # unknown to Mongo (all 125 active core.users rows, measured 2026-08-29), so a
    # Mongo-only lookup 404s on a row the admin just clicked.
    target_user, membership = await resolve_target_user_and_membership(
        user_id, building_id, projection={"_id": 0},
    )
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    if target_user.get("role") == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Cannot request profile info from super admin accounts")

    portal_url = _get_portal_url()
    profile_url = f"{portal_url}/profile"
    settings_doc = await _get_general_settings_or_default(
        building_id,
        {"_id": 0},
        fallback_building_id=DEFAULT_BUILDING_ID,
        settings_db=db,
    )
    safe_building_name = html_lib.escape(settings_doc.get("building_name") or "Building")
    safe_building_address = html_lib.escape(settings_doc.get("building_address") or "")

    user_email = target_user.get("email")
    user_name = target_user.get("full_name", "Resident")
    safe_name = html_lib.escape(user_name)
    safe_url = html_lib.escape(profile_url)

    if user_email:
        footer_text = (
            f"<p>{safe_building_name}<br>{safe_building_address}</p>"
            if safe_building_address
            else f"<p>{safe_building_name}</p>"
        )
        email_html = f"""<!DOCTYPE html>
<html>
<head>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin:0; padding:0; }}
    .container {{ max-width:600px; margin:0 auto; padding:20px; }}
    .header {{ background:#2F4F4F; color:#fff; padding:30px; text-align:center; border-radius:8px 8px 0 0; }}
    .content {{ background:#f9f9f9; padding:30px; border-radius:0 0 8px 8px; }}
    .btn {{ display:inline-block; background:#2F4F4F; color:#fff!important; text-decoration:none; padding:12px 28px; border-radius:6px; font-weight:600; margin:20px 0; }}
    .footer {{ text-align:center; color:#888; font-size:12px; margin-top:20px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><h1>StrataOS</h1></div>
    <div class="content">
      <h2>Profile Update Requested</h2>
      <p>Dear {safe_name},</p>
      <p>Our administration team has requested a few more details for your resident profile.</p>
      <p>Please use the button below to review and update your profile information.</p>
      <a href="{safe_url}" class="btn">Update My Profile</a>
      <p>If you have any questions, please contact the Strata Manager.</p>
    </div>
    <div class="footer">{footer_text}</div>
  </div>
</body>
</html>"""
        email_text = (
            f"Profile Update Requested\n\nDear {user_name},\n\n"
            "Our administration team has requested a few more details for your resident profile.\n"
            f"Please update your profile here:\n{profile_url}\n\n"
            "StrataOS"
        )
        asyncio.create_task(send_email_async(
            user_email,
            "Profile Update Requested — StrataOS",
            email_html,
            email_text,
        ))

    now_ts = datetime.now(timezone.utc).isoformat()
    await db.user_notifications.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "title": "Profile update requested",
        "message": "Our admin team has asked you to complete your profile details.",
        "type": "profile_update_requested",
        "link": "/profile",
        "is_read": False,
        "created_at": now_ts,
    })

    await db.users.update_one(
        {"id": user_id},
        {"$set": {"status": "info_requested"}},
    )

    try:
        asyncio.create_task(create_audit_log(
            action="profile_info_requested",
            resource_type="user",
            resource_id=user_id,
            user_id=current_user.get("id", ""),
            user_name=current_user.get("full_name", "Admin"),
            details={
                "target_email": user_email,
                "target_name": user_name,
            },
        ))
    except Exception:
        pass

    return {"message": "Profile info request sent"}


# ── Archive user ──────────────────────────────────────────────────────────────


class UserArchiveData(BaseModel):
    reason: Optional[str] = "no_longer_active"


@api_router.post("/users/{user_id}/archive")
async def archive_user_endpoint(
        user_id: str,
        archive_data: UserArchiveData,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. Account lifecycle is a manager function; the
        # additive guard means admin_staff lose it while keeping onboarding review.
        _cap: dict = Depends(require_capability("building.people.manage", building_from_context=True)),
):
    """
    Archive a user (soft-delete for compliance).
    Used for previous owners/tenants superseded by new occupants.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized to archive users")

    # Verify membership
    membership = await db.memberships.find_one({"building_id": building_id, "user_id": user_id})
    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")

    from db_postgres.repos.identity_repo import find_user_by_id_for_admin
    target_user = await find_user_by_id_for_admin(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    auth_email, _ = _get_auth_admin()
    if target_user.get("email") == auth_email:
        raise HTTPException(
            status_code=403, detail="System administrator account cannot be archived"
        )

    if (
            target_user.get("role") == UserRole.SUPER_ADMIN
            and current_user["role"] != UserRole.SUPER_ADMIN
    ):
        raise HTTPException(
            status_code=403,
            detail="Only super admins can archive other super admin accounts",
        )
    if target_user.get("status") == "archived":
        raise HTTPException(status_code=400, detail="User is already archived")

    now = datetime.now(timezone.utc).isoformat()

    # Cascade cleanup — deactivate related records for this building
    await db.user_units.update_many(
        {"building_id": building_id, "user_id": user_id}, {"$set": {"is_active": False, "archived_at": now}}
    )
    await db.notifications.delete_many({"building_id": building_id, "user_id": user_id})

    # Remove membership first
    await db.memberships.delete_one({"building_id": building_id, "user_id": user_id})

    # SECURITY FIX: Only deactivate the user globally if they have no other active memberships
    other_memberships = await db.memberships.count_documents({"user_id": user_id, "is_active": True})

    if other_memberships == 0:
        await db.users.update_one(
            {"id": user_id},
            {
                "$set": {
                    "status": "archived",
                    "is_active": False,
                    "is_approved": False,
                    "archived_at": now,
                    "archived_by": current_user.get("id", ""),
                    "archived_reason": archive_data.reason or "archived_by_admin",
                    "updated_at": now,
                }
            },
        )
        # Mirror into Postgres. For a building whose identity_core is promoted,
        # GET /users is served purely from core.users (filtered on status='active'),
        # so a Mongo-only archive leaves the user visibly un-archived on the very
        # page the admin just archived them from. Directional per data-source rule 8:
        # Mongo has already committed, so a Postgres failure is logged, not raised.
        await _sync_user_status_to_postgres(
            user_id,
            status="archived",
            is_active=False,
            is_approved=False,
            context="archive_user",
        )

    asyncio.create_task(
        create_audit_log(
            action="archived",
            resource_type="user",
            resource_id=user_id,
            user_id=current_user.get("id", ""),
            user_name=current_user.get("full_name", "Admin"),
            details={
                "reason": archive_data.reason,
                "target_email": target_user.get("email"),
                "target_name": target_user.get("full_name"),
                "target_role": target_user.get("role"),
            },
        )
    )

    return {"message": "User archived successfully"}


async def _sync_user_status_to_postgres(
        user_id: str, *, status: str, is_active: bool, is_approved: bool, context: str,
) -> None:
    """Mirror a Mongo user-status change onto core.users.

    Archive and restore are the two lifecycle actions that decide whether an
    account appears in the admin user list at all, and for a promoted building
    that list comes from Postgres. Both wrote only to Mongo until 2026-08-27,
    so archiving a user on /admin/users appeared to do nothing.

    Never raises: the Mongo write has already committed by the time this is
    called, and failing the request here would report an error for a change that
    did in fact happen.
    """
    try:
        from db_postgres.repos.identity_repo import update_user_profile
        synced = await update_user_profile(
            user_id,
            {"status": status, "is_active": is_active, "is_approved": is_approved},
        )
        if not synced:
            logger.warning(
                "%s: user_id=%s not mirrored to core.users (Mongo-only user, or row absent)",
                context, user_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("%s: Postgres status sync failed for user_id=%s: %s", context, user_id, exc)


# ── Restore archived user ──────────────────────────────────────────────────────


class UserRestoreData(BaseModel):
    reason: Optional[str] = None


@api_router.post("/users/{user_id}/restore")
async def restore_user_endpoint(
        user_id: str,
        restore_data: UserRestoreData = UserRestoreData(),
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. Account lifecycle is a manager function; the
        # additive guard means admin_staff lose it while keeping onboarding review.
        _cap: dict = Depends(require_capability("building.people.manage", building_from_context=True)),
):
    """
    Restore an archived user back to active status.
    Handles all roles: owner, tenant, guest, and any admin-created account.
    Super admins, strata managers, and admin staff can restore users.
    """
    _RESTORE_ROLES = frozenset({
        UserRole.SUPER_ADMIN,
        UserRole.STRATA_MANAGER,
        UserRole.ADMIN_STAFF,
    })
    if _effective_role(current_user) not in _RESTORE_ROLES:
        raise HTTPException(
            status_code=403, detail="Only super admins, strata managers, and admin staff can restore archived users"
        )

    # Restoration in a multi-tenant system typically means re-adding membership
    target_user = await db.users.find_one({"id": user_id})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Building-scope guard: db.users is a global (unscoped) collection, so a
    # strata_manager or admin_staff could otherwise restore any user into their
    # building regardless of prior association.  Always require an inactive
    # user_units record for this building_id before proceeding.
    _inactive_unit = await db.user_units.find_one(
        {"building_id": building_id, "user_id": user_id, "is_active": False}
    )
    is_archived = target_user.get("status") == "archived"

    if not _inactive_unit:
        if not is_archived:
            raise HTTPException(status_code=400, detail="User is not in a restorable state for this building")
        # Explicitly archived but no inactive unit for this building: verify any
        # historical unit association exists before allowing the restore.
        _any_unit = await db.user_units.find_one({"building_id": building_id, "user_id": user_id})
        if not _any_unit:
            raise HTTPException(status_code=403, detail="User has no association with this building")

    # Pre-check: before touching any records, verify no unit+role conflicts exist.
    # If this user was an owner of a unit that now has a different primary owner,
    # re-activating their user_units would violate the unique_active_primary_owner_per_unit index.
    _restore_units = await db.user_units.find(
        {"building_id": building_id, "user_id": user_id, "is_primary": True}
    ).to_list(20)
    for _ruu in _restore_units:
        _ruu_unit = _ruu.get("unit_number")
        _ruu_role = _ruu.get("role_at_unit")
        if not _ruu_unit or not _ruu_role:
            continue
        _ruu_conflict = await db.user_units.find_one({
            "unit_number": _ruu_unit,
            "role_at_unit": _ruu_role,
            "is_active": True,
            "is_primary": True,
            "user_id": {"$ne": user_id},
        })
        if _ruu_conflict:
            _ruu_cu = await db.users.find_one({"id": _ruu_conflict["user_id"]}, {"full_name": 1})
            _ruu_cn = _ruu_cu.get("full_name", "") if _ruu_cu else ""
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot restore: unit {_ruu_unit} already has an active registered "
                    f"{_ruu_role}{' (' + _ruu_cn + ')' if _ruu_cn else ''}. "
                    f"Please resolve the existing record before restoring this account."
                ),
            )

    now = datetime.now(timezone.utc).isoformat()

    # Apply any pending return details submitted during re-registration (name, phone,
    # end_date, unit_number). These were stored without overwriting the archived record
    # so the original visit data remains intact for audit purposes.
    _pending = target_user.get("pending_return_details") or {}
    _restore_set: dict = {
        "status": "active",
        "is_active": True,
        "is_approved": True,
        "updated_at": now,
        "restored_at": now,
        "restored_by": current_user.get("id", ""),
        "restore_reason": restore_data.reason or "restored_by_admin",
        "return_requested_at": None,
    }
    if _pending:
        # Apply boolean flag unconditionally when present; skip empty strings for
        # string fields so a re-registration with a blank phone never clobbers the
        # previously stored value.
        if _pending.get("by_laws_acknowledged") is not None:
            _restore_set["by_laws_acknowledged"] = _pending["by_laws_acknowledged"]
        for _field in ("full_name", "phone", "end_date"):
            if _pending.get(_field):  # only apply truthy (non-empty) strings
                _restore_set[_field] = _pending[_field]
        if _pending.get("unit_number"):
            _restore_set["unit_number"] = _pending["unit_number"]

    await db.users.update_one(
        {"id": user_id},
        {
            "$set": _restore_set,
            "$unset": {
                "archived_at": "",
                "archived_by": "",
                "archived_reason": "",
                "pending_return_details": "",
            },
        },
    )
    # Mirror of the archive path's sync — see _sync_user_status_to_postgres. Without
    # it a restored user stays status='archived' in core.users and never reappears
    # on a promoted building's user list.
    await _sync_user_status_to_postgres(
        user_id, status="active", is_active=True, is_approved=True, context="restore_user",
    )

    # Re-activate user_units. If pending_return_details specified a different unit,
    # deactivate old records and insert a fresh one for the new unit instead.
    _pending_unit = _pending.get("unit_number") if _pending else None
    _old_unit = target_user.get("unit_number") or ""
    if _pending_unit and _pending_unit != _old_unit:
        # Guard: check that the requested new unit doesn't already have an active primary
        # occupant of the same role — avoids an unhandled DuplicateKeyError on insert.
        _role = target_user.get("role", "guest")
        _new_unit_conflict = await db.user_units.find_one({
            "building_id": building_id,
            "unit_number": _pending_unit,
            "role_at_unit": _role,
            "is_active": True,
            "is_primary": True,
        })
        if _new_unit_conflict:
            _conflict_user = await db.users.find_one(
                {"id": _new_unit_conflict["user_id"]}, {"full_name": 1}
            )
            _conflict_name = _conflict_user.get("full_name", "") if _conflict_user else ""
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot restore to Unit {_pending_unit}: it already has an active registered "
                    f"{_role}{' (' + _conflict_name + ')' if _conflict_name else ''}. "
                    f"Please resolve the existing record or choose a different unit."
                ),
            )
        # Deactivate old unit associations and create a new record for the requested unit.
        await db.user_units.update_many(
            {"building_id": building_id, "user_id": user_id},
            {"$set": {"is_active": False, "actual_end_date": now, "updated_at": now}},
        )
        from datetime import date as _date
        _end_date = _pending.get("end_date")
        await db.user_units.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "unit_number": _pending_unit,
            "building_id": building_id,
            "role_at_unit": _role,
            "start_date": _date.today().isoformat(),
            "end_date": _end_date,
            "actual_end_date": None,
            "is_active": True,
            "is_primary": True,
            "auto_expire_enabled": bool(_end_date),
            "expiration_date": _end_date,
            "created_at": now,
            "updated_at": now,
        })
    else:
        await db.user_units.update_many(
            {"building_id": building_id, "user_id": user_id}, {"$set": {"is_active": True, "updated_at": now}}
        )

    # Re-add membership
    await db.memberships.update_one(
        {"building_id": building_id, "user_id": user_id},
        {"$set": {"building_id": building_id, "user_id": user_id, "joined_at": now}},
        upsert=True
    )

    await create_audit_log(
        action="user_restored",
        resource_type="user",
        resource_id=user_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={
            "target_email": target_user.get("email"),
            "target_name": target_user.get("full_name"),
            "target_role": target_user.get("role"),
            "reason": restore_data.reason,
        },
        building_id=building_id
    )

    return {"message": "User restored successfully", "user_id": user_id}


# ── Public: token-based registration update ───────────────────────────────────


class RegistrationUpdateSubmit(BaseModel):
    token: str
    unit_number: Optional[str] = None
    role: Optional[str] = None


@api_router.get("/registration/update-check")
@rate_limit("rate_limit_registration_decision", 10)
async def check_registration_update_token(
        request: Request,
        token: str,
        building_id: str = Depends(get_current_building)
):
    """
    Validate a registration update token (public — no auth required).
    Returns user's current unit and role so the form can pre-fill.
    """
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")

    user = await db.users.find_one(
        {"info_request_token": token},
        {"_id": 0, "password_hash": 0, "hashed_password": 0, "info_request_token": 0},
    )
    if not user:
        raise HTTPException(status_code=404, detail="Invalid or expired token")

    requested_at_str = user.get("info_requested_at")
    if requested_at_str:
        requested_at = datetime.fromisoformat(requested_at_str.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - requested_at > timedelta(
                hours=_INFO_REQUEST_EXPIRY_HOURS
        ):
            raise HTTPException(
                status_code=410,
                detail="This update link has expired. Please register again.",
            )

    return {
        "user_id": user.get("id"),
        "full_name": user.get("full_name"),
        "email": user.get("email"),
        "current_unit": user.get("unit_number"),
        "current_role": user.get("role"),
        "info_request_reason": user.get("info_request_reason"),
        "status": user.get("status"),
    }


@api_router.put("/registration/update")
@rate_limit("rate_limit_registration_decision", 10)
async def submit_registration_update(
        request: Request,
        update_data: RegistrationUpdateSubmit,
        building_id: str = Depends(get_current_building)
):
    """
    Submit corrected registration details via one-time token (public — no auth).
    Resets user status to 'pending' for admin re-review.
    """
    token = (update_data.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")

    user = await db.users.find_one({"info_request_token": token})
    if not user:
        raise HTTPException(status_code=404, detail="Invalid or expired token")

    requested_at_str = user.get("info_requested_at")
    if requested_at_str:
        requested_at = datetime.fromisoformat(requested_at_str.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - requested_at > timedelta(
                hours=_INFO_REQUEST_EXPIRY_HOURS
        ):
            raise HTTPException(
                status_code=410,
                detail="This update link has expired. Please register again.",
            )

    allowed_roles = {"owner", "tenant", "guest"}
    if update_data.role and update_data.role not in allowed_roles:
        raise HTTPException(status_code=400, detail="Invalid role selection")

    if update_data.unit_number:
        unit = await db.units.find_one({"building_id": building_id, "unit_number": update_data.unit_number})
        if not unit:
            raise HTTPException(
                status_code=400, detail="Unit not found. Please select a valid unit."
            )

    changes: dict = {
        "status": "pending",
        "info_request_token": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if update_data.unit_number:
        changes["unit_number"] = update_data.unit_number
    if update_data.role:
        changes["role"] = update_data.role

    await db.users.update_one({"id": user["id"]}, {"$set": changes})

    return {
        "message": "Your registration details have been updated. Our team will review and be in touch shortly."
    }


# ── Admin: archived users list ────────────────────────────────────────────────


@api_router.get("/admin/archived-users")
async def get_archived_users(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1.
        _cap: dict = Depends(require_capability("building.people.view", building_from_context=True)),
):
    """Return all archived users for the Expired Accounts admin page."""
    if _effective_role(current_user) not in [
        UserRole.SUPER_ADMIN,
        UserRole.STRATA_ADMIN,
        UserRole.EC_MEMBER,  # covers chairman (role='ec_member', ec_position='CHAIRMAN')
        UserRole.STRATA_MANAGER,
    ]:
        raise HTTPException(
            status_code=403, detail="Not authorized to view archived users"
        )

    # Performance Optimization⚡: Identify inactive users via a single aggregation lookup.
    # This replaces two sequential O(N) database round-trips with one O(1) request.
    pipeline = [
        {"$match": {"building_id": building_id, "is_active": False}},
        {"$group": {"_id": "$user_id"}},
        {"$match": {"_id": {"$ne": None}}},
        {"$lookup": {
            "from": "users",
            "localField": "_id",
            "foreignField": "id",
            "as": "user_info"
        }},
        {"$unwind": "$user_info"},
        {"$sort": {"user_info.archived_at": -1}},
        {"$limit": 500},
        {"$project": {
            "_id": 0,
            "user_info._id": 0,
            "user_info.password_hash": 0,
            "user_info.hashed_password": 0,
            "user_info.info_request_token": 0
        }}
    ]

    results = await _server_agg(db.user_units, pipeline, 500)
    users = [r["user_info"] for r in results]

    result = []
    for u in users:
        archived_at = u.get("archived_at")
        days_since = 0
        if archived_at:
            try:
                dt = datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - dt).days
            except Exception:
                pass
        return_requested_at = u.get("return_requested_at")
        _email = u.get("email")
        if _effective_role(current_user) != UserRole.SUPER_ADMIN:
            _email = mask_email(_email)
        result.append(
            {
                "user_id": u.get("id"),
                "full_name": u.get("full_name"),
                "email": _email,
                "unit_number": u.get("unit_number"),
                "role": u.get("role"),
                "status": "archived",
                "archived_at": archived_at,
                "archived_reason": u.get("archived_reason"),
                "days_since_archived": days_since,
                "return_requested_at": return_requested_at,
                "has_return_request": return_requested_at is not None,
            }
        )

    return result


# ── Owner-facing tenant/guest approval endpoints ──────────────────────────────


@api_router.get("/owner/pending-registrations")
async def get_pending_registrations_for_owner(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Returns pending tenant/guest registrations for the current owner's unit.
    Only owners can call this.
    """
    _role = _effective_role(current_user)
    if _role not in [
        "owner",
        "ec_member", "strata_admin",
        "super_admin",
        "strata_manager",
    ]:
        raise HTTPException(
            status_code=403, detail="Only unit owners can view pending registrations"
        )

    unit_number = current_user.get("unit_number")
    if not unit_number and _role not in [
        "super_admin",
        "ec_member", "strata_admin",
        "strata_manager",
    ]:
        raise HTTPException(
            status_code=400, detail="No unit number associated with your account"
        )

    # Performance Optimization⚡: Use a single MongoDB aggregation pipeline with $lookup to join user details.
    # This eliminates one sequential database round-trip by fetching users directly from the memberships.
    user_match = {"user_info.status": "pending_owner_approval", "user_info.is_active": True}
    if unit_number and _role == "owner":
        user_match["user_info.unit_number"] = unit_number

    pipeline = [
        {"$match": {"building_id": building_id}},
        {"$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "id",
            "as": "user_info"
        }},
        {"$unwind": "$user_info"},
        {"$match": user_match},
        {"$sort": {"user_info.created_at": -1}},
        {"$limit": 200},
        {"$project": {
            "_id": 0,
            "user_info._id": 0,
            "user_info.password_hash": 0,
            "user_info.hashed_password": 0,
            "user_info.info_request_token": 0
        }}
    ]

    results = await _server_agg(db.memberships, pipeline, 200)
    users = [r["user_info"] for r in results]

    result = []
    for u in users:
        result.append(
            {
                "id": u.get("id"),
                "full_name": u.get("full_name"),
                "email": u.get("email"),
                "unit_number": u.get("unit_number"),
                "role": u.get("role"),
                "phone": u.get("phone"),
                "status": u.get("status"),
                "created_at": u.get("created_at"),
            }
        )
    return result


class OwnerApprovalData(BaseModel):
    action: str = "approve"  # "approve" | "reject"
    notes: Optional[str] = None


@api_router.post("/users/{user_id}/owner-decision")
async def owner_registration_decision(
        user_id: str,
        decision: OwnerApprovalData,
        background_tasks: BackgroundTasks,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Owner approves or rejects a pending tenant/guest registration.
    - approve → status changes to 'active' (is_approved stays False; now awaits admin approval)
    - reject  → status changes to 'archived', user is notified
    """
    if _effective_role(current_user) not in [
        "owner",
        "ec_member", "strata_admin",
        "super_admin",
        "strata_manager",
    ]:
        raise HTTPException(
            status_code=403, detail="Only unit owners can review registrations"
        )

    if decision.action not in ("approve", "reject"):
        raise HTTPException(
            status_code=400, detail="action must be 'approve' or 'reject'"
        )

    # Resolves across BOTH stores: every id GET /users renders from PostgreSQL is
    # unknown to Mongo (all 125 active core.users rows, measured 2026-08-29), so a
    # Mongo-only lookup 404s on a row the admin just clicked.
    # (The previous asyncio.gather here is retained INSIDE the resolver, so the two
    # lookups are still concurrent — no round-trip was traded for the fix.)
    target, membership = await resolve_target_user_and_membership(
        user_id, building_id, projection={"_id": 0},
    )

    if not membership:
        raise HTTPException(status_code=404, detail="User not found in this building")
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.get("status") != "pending_owner_approval":
        raise HTTPException(
            status_code=400, detail="This registration is not pending owner approval"
        )

    # Owners can only review registrations for their own unit (unless admin/chairman)
    if _effective_role(current_user) == "owner":
        if target.get("unit_number") != current_user.get("unit_number"):
            raise HTTPException(
                status_code=403,
                detail="You can only review registrations for your own unit",
            )

    now = datetime.now(timezone.utc).isoformat()

    if decision.action == "approve":
        # Move to standard pending-admin-approval state
        update = {
            "status": "active",  # visible to admins for final approval
            "owner_approved": True,
            "owner_approved_by": current_user["id"],
            "owner_approved_at": now,
            "updated_at": now,
        }
        await db.users.update_one({"id": user_id}, {"$set": update})

        # Now notify super admins plus building-scoped chairman / EC / strata manager reviewers
        admin_ids = await db.memberships.distinct("user_id", {"building_id": building_id})
        admin_users = await db.users.find(
            {
                "$or": [
                    {"role": "super_admin", "is_active": True},
                    {
                        "id": {"$in": admin_ids},
                        # 'chairman' is not a top-level role — a chairman is a user with
                        # role='ec_member' and ec_position='CHAIRMAN' (see rules/post-compact-critical.md),
                        # already covered by the 'ec_member' entry below.
                        "role": {"$in": ["strata_admin", "ec_member", "strata_manager"]},
                        "is_active": True,
                    },
                ]
            }
        ).to_list(length=100)

        _admin_portal = _get_portal_url()
        _settings_doc = await _get_general_settings_or_default(
            building_id,
            {"_id": 0},
            fallback_building_id=DEFAULT_BUILDING_ID,
            settings_db=db,
        )
        _building_name = _settings_doc.get("building_name") or "Building"
        _safe_building_name = html_lib.escape(_building_name)

        if admin_users:
            safe_name = html_lib.escape(target.get("full_name", ""))
            safe_email = html_lib.escape(target.get("email", ""))
            safe_role = html_lib.escape((target.get("role") or "").capitalize())
            safe_unit = html_lib.escape(str(target.get("unit_number") or ""))
            approver_name = html_lib.escape(current_user.get("full_name", "Unit Owner"))
            _role_tab = "residents" if target.get("role") in ["tenant", "guest"] else "owners"
            _encoded_name = quote_plus(target.get("full_name", ""))
            _notif_link = f"/admin/users?tab={_role_tab}&search={_encoded_name}"

            notifications = []
            for admin in admin_users:
                notifications.append(
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": admin["id"],
                        "title": "New Registration Approved by Owner — Awaiting Your Approval",
                        "message": (
                            f"{safe_name} ({safe_email}) registered as {safe_role} for Unit {safe_unit} "
                            f"has been approved by {approver_name} (unit owner). "
                            "Please review and approve or reject this registration."
                        ),
                        "type": "user_approval",
                        "related_id": user_id,
                        "link": _notif_link,
                        "is_read": False,
                        "created_at": now,
                    }
                )
            await db.user_notifications.insert_many(notifications)

            _admin_review_link = (
                f"{_admin_portal}/admin/users?tab=residents"
                f"&search={quote_plus(target.get('full_name', ''))}"
            )
            _s_admin_link = html_lib.escape(_admin_review_link)
            _sig_html = (
                '<div style="border-top:1px solid #e2e8f0;margin-top:24px;padding-top:16px;'
                'font-size:12px;color:#64748b;line-height:1.8">'
                f'<p style="margin:0;font-weight:600;color:#2F4F4F;font-size:13px">{_safe_building_name} | Building Management</p>'
                f'<p style="margin:3px 0 0">Portal: <a href="{html_lib.escape(_admin_portal)}" style="color:#2563eb;text-decoration:none">{html_lib.escape(_admin_portal)}</a></p>'
                '</div>'
            )
            html_body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f1f5f9}}
    .wrap{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,.10)}}
    .hdr{{background:#2F4F4F;color:#fff;padding:28px 30px;text-align:center}}
    .hdr h1{{margin:0;font-size:22px;font-weight:700}}
    .body{{padding:28px 30px}}
    .info{{background:#f0fdf4;border-left:4px solid #16a34a;padding:14px 18px;margin:18px 0;border-radius:6px;font-size:14px;line-height:1.8}}
    .info p{{margin:0}}
    .badge{{display:inline-block;background:#16a34a;color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}}
    .btn{{display:inline-block;padding:13px 28px;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;background:#2563eb;color:#fff;margin-top:18px}}
    .footer{{background:#f8fafc;padding:20px 30px}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hdr"><h1>StrataOS</h1></div>
    <div class="body">
      <span class="badge">Action Required</span>
      <h2 style="margin-top:0;font-size:18px;color:#1e293b">Registration Approved by Owner — Your Approval Needed</h2>
      <p style="font-size:14px;color:#475569;line-height:1.6">
        The unit owner has approved the following registration. Please review and take final action to activate the account.
      </p>
      <div class="info">
        <p><strong>Name:</strong> {safe_name}</p>
        <p><strong>Email:</strong> {safe_email}</p>
        <p><strong>Role:</strong> {safe_role}</p>
        <p><strong>Unit:</strong> {safe_unit}</p>
        <p><strong>Owner Approved By:</strong> {approver_name}</p>
      </div>
      <a href="{_s_admin_link}" class="btn">Review &amp; Activate Account →</a>
      <p style="font-size:12px;color:#64748b;margin-top:8px">Opens the registration record directly. Log in if prompted.</p>
    </div>
    <div class="footer">{_sig_html}</div>
  </div>
</body></html>"""
            text_body = (
                f"Registration Approved by Owner — Action Required\n\n"
                f"Name: {target.get('full_name')}\nEmail: {target.get('email')}\n"
                f"Role: {target.get('role')}\nUnit: {target.get('unit_number')}\n"
                f"Owner Approved By: {approver_name}\n\n"
                f"Review & Activate: {_admin_review_link}\n\n"
                "---\nStrataOS\n"
                f"{_building_name} | Building Management\n"
                f"Portal: {_admin_portal}"
            )
            for admin in admin_users:
                background_tasks.add_task(
                    send_email_async,
                    admin["email"],
                    f"Action Required: {target.get('full_name')} — Owner Approved, Awaiting Your Activation",
                    html_body,
                    text_body,
                )

        # Notify the guest/tenant that their registration has been confirmed by the owner
        _portal_url = _get_portal_url()
        _safe_guest_name = html_lib.escape(target.get("full_name", ""))
        _safe_guest_role = (target.get("role") or "resident").capitalize()
        _safe_guest_unit = html_lib.escape(str(target.get("unit_number") or ""))
        _guide_path = {
            "guest": "quick_role_guest.html",
            "tenant": "quick_role_tenant.html",
        }.get(target.get("role", ""), "quick_role_owner.html")
        _guide_url = html_lib.escape(f"{_portal_url}/user-guides/{_guide_path}")
        _portal_esc = html_lib.escape(_portal_url)
        _sig_html_guest = (
            '<div style="border-top:1px solid #e2e8f0;margin-top:24px;padding-top:16px;'
            'font-size:12px;color:#64748b;line-height:1.8">'
            f'<p style="margin:0;font-weight:600;color:#2F4F4F;font-size:13px">{_safe_building_name} | Building Management</p>'
            f'<p style="margin:3px 0 0">Portal: <a href="{_portal_esc}" style="color:#2563eb;text-decoration:none">{_portal_esc}</a></p>'
            '</div>'
        )
        _guest_approved_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;padding:0;background:#f1f5f9}}
    .wrap{{max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,.10)}}
    .hdr{{background:#2F4F4F;color:#fff;padding:30px;text-align:center}}
    .hdr h1{{margin:0;font-size:22px;font-weight:700}}
    .body{{padding:28px 30px}}
    .badge{{display:inline-block;background:#16a34a;color:#fff;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}}
    .note{{font-size:13px;color:#64748b;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 16px;margin-top:16px;line-height:1.6}}
    .btn{{display:inline-block;padding:13px 28px;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;background:#2F4F4F;color:#fff;margin-top:18px}}
    .footer{{background:#f8fafc;padding:20px 30px}}
  </style>
</head><body>
  <div class="wrap">
    <div class="hdr"><h1>StrataOS</h1></div>
    <div class="body">
      <span class="badge">Registration Update</span>
      <h2 style="margin-top:0;font-size:18px;color:#1e293b">Owner Confirmed — Final Review Underway</h2>
      <p style="font-size:14px;color:#475569;line-height:1.6">
        Hi {_safe_guest_name},<br><br>
        Great news — the unit owner for Unit <strong>{_safe_guest_unit}</strong> has confirmed your
        <strong>{_safe_guest_role}</strong> registration request.
      </p>
      <p style="font-size:14px;color:#475569;line-height:1.6">
        The Strata Manager is now completing the final review. You will receive another email once
        your account is fully activated and you can access the portal.
      </p>
      <div class="note">
        In the meantime, you can review your <a href="{_guide_url}" style="color:#2563eb">{_safe_guest_role} Quick Guide</a>
        to understand what access you'll have once approved.
      </div>
      <a href="{_portal_esc}" class="btn">Visit the Portal →</a>
    </div>
    <div class="footer">{_sig_html_guest}</div>
  </div>
</body></html>"""
        _guest_approved_text = (
            f"Registration Update — StrataOS\n\n"
            f"Hi {target.get('full_name', '')},\n\n"
            f"The unit owner for Unit {target.get('unit_number')} has confirmed your "
            f"{_safe_guest_role} registration. The Strata Manager is now completing the final "
            f"review. You will be notified once your account is fully activated.\n\n"
            f"Quick guide: {_portal_url}/user-guides/{_guide_path}\n\n"
            f"---\n{_building_name} | Building Management"
        )
        if target.get("email"):
            background_tasks.add_task(
                send_email_async,
                target["email"],
                f"Registration Confirmed by Owner — Unit {target.get('unit_number')}",
                _guest_approved_html,
                _guest_approved_text,
                context=f"owner_approved_notify_guest:{user_id}",
            )

        return {
            "message": "Registration approved. Admins have been notified to complete activation."
        }

    else:  # reject
        reject_reason = decision.notes or "Rejected by unit owner"

        # Cascade: deactivate user_units for this building
        await db.user_units.update_many(
            {"building_id": building_id, "user_id": user_id, "is_active": True},
            {"$set": {"is_active": False, "actual_end_date": now}},
        )
        await db.notifications.delete_many({"building_id": building_id, "user_id": user_id})

        # Remove membership first
        await db.memberships.delete_one({"building_id": building_id, "user_id": user_id})

        # SECURITY FIX: Only deactivate the user globally if they have no other active memberships
        other_memberships = await db.memberships.count_documents({"user_id": user_id, "is_active": True})

        if other_memberships == 0:
            update = {
                "status": "archived",
                "is_active": False,
                "is_approved": False,
                "archived_at": now,
                "archived_by": current_user["id"],
                "archived_reason": f"owner_rejected:{html_lib.escape(reject_reason)}",
                "updated_at": now,
            }
            await db.users.update_one({"id": user_id}, {"$set": update})

        # Notify the rejected user
        safe_role = (target.get("role") or "resident").capitalize()
        safe_unit = html_lib.escape(str(target.get("unit_number") or ""))
        reject_html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px">
<div style="background:#2F4F4F;color:#fff;padding:24px;border-radius:8px 8px 0 0;text-align:center">
  <h2 style="margin:0">StrataOS</h2>
</div>
<div style="background:#f9f9f9;padding:28px;border-radius:0 0 8px 8px">
  <h3 style="margin-top:0">Registration Update for Unit {safe_unit}</h3>
  <p>Unfortunately, your registration as a {safe_role} for Unit {safe_unit} could not be confirmed by the unit owner.</p>
  <p>If you believe this is an error, please contact the Strata Manager.</p>
</div>
</body></html>"""
        reject_text = (
            f"Your registration for Unit {target.get('unit_number')} has been declined by the unit owner.\n"
            "Please contact the Strata Manager if you believe this is an error."
        )
        if target.get("email"):
            background_tasks.add_task(
                send_email_async,
                target["email"],
                "Registration Status Update",
                reject_html,
                reject_text,
            )

        return {"message": "Registration rejected and archived."}


@api_router.get("/admin/stats")
async def get_admin_stats(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get system stats for super admin dashboard. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        await repair_orphan_staff_memberships(building_id)
        # Performance Optimization⚡: Consolidated counts and parallelized database calls to reduce round-trips and improve latency
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Consolidated User Stats Pipeline - Performance Optimization⚡
        # Integrated building membership filtering using $lookup join to eliminate one sequential round-trip.
        # total   = active residents only (exclude archived users)
        # pending = needs ADMIN action (is_approved=false, not archived, not pending_owner_approval)
        # pending_owner_approval = waiting for unit owner to approve (not yet admin's turn)
        user_stats_pipeline = [
            {"$match": {"building_id": building_id}},
            {"$group": {"_id": "$user_id"}},  # Bolt ⚡: De-duplicate users with multiple memberships
            {
                "$lookup": {
                    "from": "users",
                    "localField": "_id",
                    "foreignField": "id",
                    "as": "user_info",
                }
            },
            {"$unwind": "$user_info"},
            {
                "$group": {
                    "_id": None,
                    "total": {
                        "$sum": {"$cond": [{"$ne": ["$user_info.status", "archived"]}, 1, 0]}
                    },
                    "pending": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$user_info.is_approved", False]},
                                        {
                                            "$not": [
                                                {
                                                    "$in": [
                                                        "$user_info.status",
                                                        [
                                                            "archived",
                                                            "pending_owner_approval",
                                                        ],
                                                    ]
                                                }
                                            ]
                                        },
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "pending_owner_approval": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$user_info.status", "pending_owner_approval"]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            }
        ]

        # 2. Consolidated Invoice Stats Pipeline
        invoice_pipeline = [
            {"$match": {"status": "pending"}},
            {
                "$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "total_amount": {"$sum": "$total_amount"},
                }
            },
        ]

        # Execute all independent database queries in parallel - Scoped to building
        results = await asyncio.gather(
            _server_agg(db.memberships, user_stats_pipeline, 1),
            db.units.count_documents({"building_id": building_id}),
            db.maintenance_requests.count_documents(
                {"building_id": building_id, "status": {"$in": ["submitted", "under_review", "approved"]}}
            ),
            _server_agg(db.invoices, [{"$match": {"building_id": building_id}}] + invoice_pipeline, 1),
            db.user_units.distinct("unit_number", {"building_id": building_id, "is_active": True}),
            db.documents.count_documents({"building_id": building_id}),
            db.listings.count_documents({"building_id": building_id, "status": "active"}),
            db.meetings.count_documents({"building_id": building_id, "meeting_date": {"$gte": now_iso}}),
            db.work_order_invoices.count_documents({"building_id": building_id, "approval_status": "submitted"}),
        )

        (
            user_res,
            total_units,
            pending_maintenance,
            inv_res,
            occupied_list,
            total_documents,
            active_listings,
            upcoming_meetings,
            pending_wo_invoices,
        ) = results

        # Unpack user stats
        u_stats = (
            user_res[0]
            if user_res
            else {"total": 0, "pending": 0, "pending_owner_approval": 0}
        )
        total_users = u_stats.get("total", 0)
        pending_users = u_stats.get("pending", 0)
        pending_owner_approval_count = u_stats.get("pending_owner_approval", 0)

        # Unpack invoice stats
        i_stats = inv_res[0] if inv_res else {"count": 0, "total_amount": 0}
        pending_invoices_count = i_stats.get("count", 0) + pending_wo_invoices
        pending_invoices_amount = i_stats.get("total_amount", 0)

        # Calculate occupancy rate
        # Calculate occupancy rate with data consistency protection
        occupancy_rate = (
            (min(len(occupied_list), total_units) / total_units * 100)
            if total_units > 0
            else 0
        )

        return {
            "total_users": total_users,
            "pending_users": pending_users,  # needs ADMIN approval
            "pending_owner_approval_count": pending_owner_approval_count,  # waiting for owner first
            "total_units": total_units,
            "pending_maintenance": pending_maintenance,
            "pending_invoices_count": pending_invoices_count,
            "pending_invoices_amount": round(pending_invoices_amount, 2),
            "occupancy_rate": round(occupancy_rate, 1),
            "total_documents": total_documents,
            "active_listings": active_listings,
            "upcoming_meetings": upcoming_meetings,
        }
    except Exception as e:
        logger.error(f"Error fetching admin stats: {e}")
        # Return partial data if some collections are missing
        return {
            "total_users": 0,
            "pending_users": 0,
            "pending_owner_approval_count": 0,
            "total_units": 0,
            "pending_maintenance": 0,
            "pending_invoices_count": 0,
            "pending_invoices_amount": 0,
            "occupancy_rate": 0,
        }


# ==================== CHANGE REQUEST ENDPOINTS ====================


@api_router.get("/change-requests", response_model=List[UnitChangeRequestResponse])
async def get_change_requests(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get change requests (admins see all, users see their own). Scoped to building."""
    permissions = get_user_permissions(current_user)

    if permissions.can_manage_requests:
        # Admins can see all requests
        query = {"building_id": building_id}
        if status:
            query["status"] = status
    else:
        # Regular users only see their own requests
        query = {"building_id": building_id, "user_id": current_user["id"]}
        if status:
            query["status"] = status

    requests = (
        await db.unit_change_requests.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(None)
    )
    return [UnitChangeRequestResponse(**req) for req in requests]


@api_router.get("/change-requests/me/pending")
async def get_my_pending_request(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Check if current user has a pending change request for this building"""
    request = await db.unit_change_requests.find_one(
        {"building_id": building_id, "user_id": current_user["id"], "status": "pending"}, {"_id": 0}
    )

    if not request:
        return {"has_pending": False, "request": None}

    return {"has_pending": True, "request": UnitChangeRequestResponse(**request)}


@api_router.put("/change-requests/{request_id}/review")
async def review_change_request(
        request_id: str,
        review: UnitChangeRequestReview,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Approve or reject a change request (admin only)"""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(
            status_code=403, detail="Not authorized to review unit change requests"
        )

    if review.action not in ["approve", "reject"]:
        raise HTTPException(
            status_code=400, detail="Action must be 'approve' or 'reject'"
        )

    # Get the request
    request = await db.unit_change_requests.find_one({"id": request_id, "building_id": building_id})
    if not request:
        raise HTTPException(status_code=404, detail="Unit change request not found")

    if request["status"] != "pending":
        raise HTTPException(status_code=400, detail="Request has already been reviewed")

    # Update request status
    reviewed_at = datetime.now(timezone.utc).isoformat()
    status = "approved" if review.action == "approve" else "rejected"

    await db.unit_change_requests.update_one(
        {"id": request_id},
        {
            "$set": {
                "status": status,
                "reviewed_at": reviewed_at,
                "reviewed_by": current_user["id"],
                "reviewer_name": current_user["full_name"],
                "admin_notes": review.admin_notes,
            }
        },
    )

    # Create audit log
    await create_audit_log(
        action="reviewed",
        resource_type="unit_change_request",
        resource_id=request_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details={"action": review.action, "notes": review.admin_notes},
    )

    # Create in-app notification for user
    await create_user_notification(
        user_id=request["user_id"],
        title=f"Profile Change Request {review.action.title()}d",
        message=f"Your request to update your profile was {review.action}d.",
        notification_type="unit_change",
        link="/profile",
    )

    # If approved, update the user's data
    if review.action == "approve":
        update_fields = {
            "unit_number": request["requested_unit"],
            "is_managing_agent": request.get("requested_is_managing_agent"),
            "is_tenanted": request.get("requested_is_tenanted"),
            "updated_at": reviewed_at,
        }
        # Only include fields that are not None
        update_fields = {k: v for k, v in update_fields.items() if v is not None}

        await db.users.update_one({"id": request["user_id"]}, {"$set": update_fields})

    # Get user details for email
    user = await db.users.find_one(
        {"id": request["user_id"]}, {"email": 1, "full_name": 1}
    )

    # Send email notification to user
    try:
        safe_user_name = html_lib.escape(user.get("full_name", "User"))
        safe_admin_notes = html_lib.escape(review.admin_notes or "")
        if review.action == "approve":
            subject = "Profile Change Request Approved"
            html_content = f"""
            <h2>Profile Change Request Approved</h2>
            <p>Hello {safe_user_name},</p>
            <p>Your profile change request has been approved!</p>
            {f"<p><strong>Admin Notes:</strong> {safe_admin_notes}</p>" if review.admin_notes else ""}
            <p>Your profile has been updated in the system.</p>
            <p>Thank you,<br>StrataOS</p>
            """
            text_content = f"Your profile change request has been approved."
        else:
            subject = "Profile Change Request Rejected"
            html_content = f"""
            <h2>Profile Change Request Rejected</h2>
            <p>Hello {safe_user_name},</p>
            <p>Your profile change request has been rejected.</p>
            {f"<p><strong>Reason:</strong> {safe_admin_notes}</p>" if review.admin_notes else ""}
            <p>If you have questions, please contact the management office.</p>
            <p>Thank you,<br>StrataOS</p>
            """
            text_content = f"Your profile change request has been rejected.{' Reason: ' + review.admin_notes if review.admin_notes else ''}"

        await send_email_async(
            to_email=user["email"],
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )
    except Exception as e:
        print(f"Warning: Failed to send unit change review email: {str(e)}")

    return {
        "message": f"Unit change request {status}",
        "request_id": request_id,
        "status": status,
        "user_updated": review.action == "approve",
    }


# ==================== DOCUMENT ROUTES ====================


@api_router.post("/documents", response_model=DocumentResponse)
async def upload_document(
        title: str = Form(..., max_length=200),
        description: str = Form(None, max_length=1000),
        category: str = Form(...),
        is_public: bool = Form(False),
        allowed_roles: str = Form("[]"),
        folder_id: Optional[str] = Form(None),
        is_important: bool = Form(False),
        importance_summary: Optional[str] = Form(None),
        file: UploadFile = File(...),
        current_user: dict = Depends(require_feature("documents")),
        building_id: str = Depends(get_current_building),
):
    """
    Upload a new document with Stored XSS protection and feature gating.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(
            status_code=403, detail="Not authorized to upload documents"
        )

    # SECURITY: Sanitize user input to prevent Stored XSS
    sanitized_title = html_lib.escape(title)
    sanitized_description = nh3.clean(description) if description else None

    # Sanitize filename: strip directory components and replace unsafe characters
    raw_filename = file.filename or "upload"
    safe_display_name = re.sub(r"[^\w.\-]", "_", os.path.basename(raw_filename))

    import json

    roles_list = json.loads(allowed_roles)

    # Build folder path if folder_id provided
    folder_path = None
    if folder_id:
        # Verify folder exists
        folder = await db.document_folders.find_one(
            {"id": folder_id, "building_id": building_id}, {"_id": 0}
        )
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        # Build path
        folders = (
            await db.document_folders.find({"building_id": building_id}, {"_id": 0})
            .to_list(1000)
        )
        folders_cache = {f["id"]: f for f in folders}
        folder_path = build_folder_path(folder_id, folders_cache)

    file_content = await file.read()
    await scan_upload(file_content, context="document", filename=file.filename or "")
    file_base64 = base64.b64encode(file_content).decode("utf-8")

    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Auto-generate importance_summary if is_important and none provided
    resolved_importance_summary = None
    if is_important:
        if importance_summary and importance_summary.strip():
            resolved_importance_summary = importance_summary.strip()
        elif sanitized_description:
            # Use first 3 sentences, each <= 80 chars
            import re as _re
            sentences = _re.split(r'(?<=[.!?])\s+', sanitized_description.strip())
            selected = []
            for s in sentences[:3]:
                if len(s) <= 80:
                    selected.append(s)
                else:
                    selected.append(s[:77] + '...')
            resolved_importance_summary = ' '.join(selected) if selected else None
        else:
            resolved_importance_summary = f"Important {category.replace('_', ' ').title()}: {sanitized_title}"

    doc = {
        "id": doc_id,
        "building_id": building_id,
        "title": sanitized_title,
        "description": sanitized_description,
        "category": category,
        "folder_id": folder_id,
        "folder_path": folder_path,
        "file_name": safe_display_name,
        "file_type": file.content_type,
        "file_size": len(file_content),
        "file_data": file_base64,
        "is_public": is_public,
        "allowed_roles": roles_list,
        "is_important": is_important,
        "importance_summary": resolved_importance_summary,
        "uploaded_by": current_user["id"],
        "uploaded_by_name": current_user["full_name"],
        "created_at": now,
        "updated_at": now,
    }

    # Store-agnostic write. When the control plane has `documents` on Postgres this
    # commits to Postgres FIRST and mirrors into MongoDB after that commit — never
    # inside the open transaction, so a rollback cannot leave Mongo holding a document
    # Postgres never accepted (footgun #21). While `documents` is Mongo-primary this is
    # exactly the previous single Mongo write.
    #
    # `file_data` (base64 bytes) stays Mongo-only on purpose: documents.documents models
    # external object storage via `storage_key` and has no column for inline content.
    # Postgres therefore holds the document RECORD and Mongo holds the BYTES until a
    # storage backend is chosen — recorded as the open item in
    # docs/architecture/postgres_router_cutover_state_and_plan_2026-08-29.md rather than
    # papered over with a bytea column nobody agreed to.
    from services.documents_store import write_document as _write_document

    _write = await _write_document(
        building_id,
        title=sanitized_title,
        original_filename=safe_display_name,
        mime_type=file.content_type or "application/octet-stream",
        storage_key=f"mongo:documents/{doc_id}",
        file_size_bytes=len(file_content),
        folder_id=None,  # Mongo folder_id is a legacy string id, not a documents.folders UUID
        allowed_roles=roles_list,
        tags=[category] if category else [],
        is_public=is_public,
        mongo_document=doc,
    )
    if _write.get("mirror_error"):
        logger.warning(
            "upload_document: partial write for building=%s doc=%s — %s",
            building_id, doc_id, _write["mirror_error"],
        )

    return DocumentResponse(**doc)


@api_router.get("/documents", response_model=List[DocumentResponse])
async def get_documents(
        category: Optional[str] = None,
        folder_id: Optional[str] = None,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_documents
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"building_id": building_id}

    if current_user:
        permissions = get_user_permissions(current_user)
        if not permissions.can_view_documents:
            query["is_public"] = True
        elif current_user["role"] == "tenant":
            # Tenants can only view:
            # 1. Public documents
            # 2. Documents explicitly marked for tenants
            query["$or"] = [{"is_public": True}, {"allowed_roles": {"$in": ["tenant"]}}]
        else:
            # All other users with can_view_documents permission
            # Show documents user has access to
            query["$or"] = [
                {"is_public": True},
                {"uploaded_by": current_user["id"]},
                {"allowed_roles": {"$in": [_effective_role(current_user)]}},
            ]
    else:
        query["is_public"] = True

    if category:
        query["category"] = category

    # Filter by folder_id
    if folder_id is not None:
        if folder_id == "":
            # Empty string means root level (no folder)
            query["folder_id"] = None
        else:
            query["folder_id"] = folder_id

    # Store-agnostic read. Which store serves is decided by the cutover control
    # plane (services/store_router.py), not by this handler — the same seam every
    # other domain will adopt. Fails closed to MongoDB when `documents` has no
    # core.domain_cutover_status row, so wiring this in changed nothing until the
    # domain was explicitly promoted.
    from db_postgres.repos.documents_repo import DocumentVisibility
    from services.documents_store import read_documents as _read_documents

    # The Postgres predicate is built from the SAME three facts the Mongo query above
    # was built from, so the two stores answer the same question. It is passed
    # explicitly and has no default: an omitted visibility context used to mean "no
    # filter", which on a promoted domain returns every document in the building to
    # any caller — and this route uses get_optional_user, so that includes callers
    # with no session.
    if current_user:
        _permissions = get_user_permissions(current_user)
        if not _permissions.can_view_documents:
            _visibility = DocumentVisibility.for_roles([], include_public=True)
        elif current_user["role"] == "tenant":
            _visibility = DocumentVisibility.for_roles(["tenant"], include_public=True)
        else:
            _visibility = DocumentVisibility.for_roles(
                [_effective_role(current_user)],
                viewer_user_id=current_user.get("id"),
                include_public=True,
            )
    else:
        _visibility = DocumentVisibility.for_roles([], include_public=True)

    _result = await _read_documents(
        building_id,
        visibility=_visibility,
        folder_id=folder_id,
        mongo_query=query,
        limit=1000,
    )
    documents = _result["documents"]
    return [
        DocumentResponse(**{k: v for k, v in d.items() if k != "source_store"}, file_data=None)
        for d in documents
    ]


@api_router.get("/documents/folders")
async def get_document_folders(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get document folder structure. Scoped to building.
    Sentinel 🛡️: Requires approved resident status to prevent information leakage.
    Performance Optimization⚡: Parallelized folder fetch.
    """
    folders = (
        await db.document_folders.find({"building_id": building_id}, {"_id": 0}).sort("name", 1).to_list(1000)
    )
    return folders


@api_router.get("/documents/important")
async def get_important_documents(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Get recent important documents for dashboard alerts.
    Performance Optimization⚡: Optimized fetch with limit.
    """
    docs = await db.documents.find(
        {
            "is_important": True,
            "building_id": building_id,
            "$or": [
                {"is_public": True},
                {"allowed_roles": {"$in": [_effective_role(current_user)]}},
                {"allowed_roles": []},
            ],
        },
        {"file_data": 0},
    ).sort("created_at", -1).limit(5).to_list(5)
    result = []
    for d in docs:
        d["id"] = str(d.pop("_id", d.get("id", "")))
        result.append({
            "id": d["id"],
            "title": d.get("title", ""),
            "importance_summary": d.get("importance_summary", ""),
            "created_at": str(d.get("created_at", "")),
            "category": d.get("category", ""),
        })
    return result


@api_router.get("/documents/{doc_id}", response_model=DocumentResponse)
async def get_document(
        doc_id: str,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_document
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    doc = await db.documents.find_one({"id": doc_id, "building_id": building_id}, {"_id": 0})

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check access
    if not doc["is_public"]:
        if not current_user:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Administrators, uploader, and approved users with allowed roles have access
        permissions = get_user_permissions(current_user)
        is_admin = permissions.can_manage_users
        is_uploader = current_user["id"] == doc.get("uploaded_by")

        # Role-based access is only granted to approved users
        role_allowed = is_approved_user(current_user) and current_user[
            "role"
        ] in doc.get("allowed_roles", [])

        if not (is_admin or is_uploader or role_allowed):
            raise HTTPException(status_code=403, detail="Not authorized")

    return DocumentResponse(**doc)


@api_router.delete("/documents/{doc_id}")
async def delete_document(
        doc_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_document
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)

    doc = await db.documents.find_one({"id": doc_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc["uploaded_by"] != current_user["id"] and not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.documents.delete_one({"id": doc_id, "building_id": building_id})
    return {"message": "Document deleted successfully"}


# ==================== DOCUMENT FOLDER ROUTES ====================


def build_folder_path(folder_id: str, folders_cache: dict) -> str:
    """Recursively build folder path from root to current folder"""
    if not folder_id or folder_id not in folders_cache:
        return ""

    folder = folders_cache[folder_id]
    if not folder.get("parent_folder_id"):
        return f"/{folder['name']}"

    parent_path = build_folder_path(folder["parent_folder_id"], folders_cache)
    return f"{parent_path}/{folder['name']}"


async def get_folder_permissions(folder_id: Optional[str], db, building_id: Optional[str] = None) -> Dict[str, Any]:
    """Get effective permissions for folder (with inheritance)"""
    if not folder_id:
        return {"access_level": "all_members", "allowed_roles": [], "allowed_users": []}

    # Sentinel 🛡️: Scoped search by building_id if context provided
    query = {"id": folder_id}
    if building_id:
        query["building_id"] = building_id

    folder = await db.document_folders.find_one(query, {"_id": 0})
    if not folder:
        return {"access_level": "all_members", "allowed_roles": [], "allowed_users": []}

    # If folder has explicit permissions, use them
    if folder.get("permissions") and folder["permissions"].get("access_level"):
        return folder["permissions"]

    # Otherwise, inherit from parent
    if folder.get("parent_folder_id"):
        return await get_folder_permissions(folder["parent_folder_id"], db, building_id)

    # Default for root folders
    return {"access_level": "all_members", "allowed_roles": [], "allowed_users": []}


@api_router.post("/folders", response_model=DocumentFolderResponse)
async def create_folder(
        data: DocumentFolderCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_folder
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized to create folders")

    # Validate parent folder exists
    if data.parent_folder_id:
        parent = await db.document_folders.find_one(
            {"id": data.parent_folder_id, "building_id": building_id}, {"_id": 0}
        )
        if not parent:
            raise HTTPException(status_code=404, detail="Parent folder not found")

    # Check for duplicate name in same parent
    existing = await db.document_folders.find_one(
        {"name": data.name, "parent_folder_id": data.parent_folder_id, "building_id": building_id}
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Folder with this name already exists in this location",
        )

    folder_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Get permissions (inherit from parent if not specified)
    # Sentinel 🛡️: Pass building_id to ensure inheritance stays within the same building
    folder_permissions = data.permissions or await get_folder_permissions(
        data.parent_folder_id, db, building_id
    )

    folder_doc = {
        "id": folder_id,
        "building_id": building_id,
        "name": data.name,
        "parent_folder_id": data.parent_folder_id,
        "path": "",  # Will be calculated
        "description": data.description,
        "created_by": current_user["id"],
        "created_by_name": current_user["full_name"],
        "created_at": now,
        "updated_at": now,
        "permissions": folder_permissions,
        "color": data.color,
        "is_system": False,
    }

    await db.document_folders.insert_one(folder_doc)

    # Build path after creation
    folders = await db.document_folders.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
    folders_cache = {f["id"]: f for f in folders}
    folder_path = build_folder_path(folder_id, folders_cache)

    # Update with path
    await db.document_folders.update_one(
        {"id": folder_id, "building_id": building_id}, {"$set": {"path": folder_path}}
    )
    folder_doc["path"] = folder_path

    # Get counts
    doc_count = await db.documents.count_documents({"folder_id": folder_id, "building_id": building_id})
    subfolder_count = await db.document_folders.count_documents(
        {"parent_folder_id": folder_id, "building_id": building_id}
    )

    folder_doc["document_count"] = doc_count
    folder_doc["subfolder_count"] = subfolder_count

    return DocumentFolderResponse(**folder_doc)


@api_router.get("/folders")
async def list_folders(
        parent_folder_id: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List folders, optionally filtered by parent. Returns tree structure if no parent specified. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get all folders for this building
    folders = await db.document_folders.find({"building_id": building_id}, {"_id": 0}).to_list(1000)

    # Build document counts using aggregation (efficient batch query)
    doc_counts_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": "$folder_id", "count": {"$sum": 1}}}
    ]
    doc_counts_result = await _server_agg(db.documents, doc_counts_pipeline, 1000)
    doc_counts = {
        item["_id"]: item["count"] for item in doc_counts_result if item["_id"]
    }

    # Build subfolder counts using aggregation (efficient batch query)
    subfolder_counts_pipeline = [
        {"$match": {"building_id": building_id}},
        {"$group": {"_id": "$parent_folder_id", "count": {"$sum": 1}}}
    ]
    subfolder_counts_result = await _server_agg(db.document_folders, subfolder_counts_pipeline, 1000)
    subfolder_counts = {
        item["_id"]: item["count"] for item in subfolder_counts_result if item["_id"]
    }

    # Assign counts to folders (O(n) instead of O(2n) database queries)
    for folder in folders:
        folder["document_count"] = doc_counts.get(folder["id"], 0)
        folder["subfolder_count"] = subfolder_counts.get(folder["id"], 0)

    if parent_folder_id is not None:
        # Return folders in specific parent
        filtered = [f for f in folders if f.get("parent_folder_id") == parent_folder_id]
        return filtered

    # Return all folders (for tree building on frontend)
    return folders


@api_router.get("/folders/{folder_id}", response_model=DocumentFolderResponse)
async def get_folder(
        folder_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_folder
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    folder = await db.document_folders.find_one({"id": folder_id, "building_id": building_id}, {"_id": 0})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Get counts
    folder["document_count"] = await db.documents.count_documents(
        {"folder_id": folder_id, "building_id": building_id}
    )
    folder["subfolder_count"] = await db.document_folders.count_documents(
        {"parent_folder_id": folder_id, "building_id": building_id}
    )

    return DocumentFolderResponse(**folder)


@api_router.put("/folders/{folder_id}", response_model=DocumentFolderResponse)
async def update_folder(
        folder_id: str,
        data: DocumentFolderUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_folder
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    folder = await db.document_folders.find_one({"id": folder_id, "building_id": building_id}, {"_id": 0})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # System folders cannot be renamed
    if folder.get("is_system") and data.name:
        raise HTTPException(status_code=403, detail="Cannot rename system folders")

    # Check for name conflicts if renaming
    if data.name and data.name != folder["name"]:
        existing = await db.document_folders.find_one(
            {
                "name": data.name,
                "parent_folder_id": folder.get("parent_folder_id"),
                "building_id": building_id,
                "id": {"$ne": folder_id},
            }
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Folder with this name already exists in this location",
            )

    update_dict = {}
    if data.name:
        update_dict["name"] = data.name
    if data.description is not None:
        update_dict["description"] = data.description
    if data.permissions:
        update_dict["permissions"] = data.permissions
    if data.color is not None:
        update_dict["color"] = data.color

    if update_dict:
        update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.document_folders.update_one({"id": folder_id, "building_id": building_id}, {"$set": update_dict})

        # Rebuild path if name changed
        if "name" in update_dict:
            folders = await db.document_folders.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
            folders_cache = {f["id"]: f for f in folders}
            new_path = build_folder_path(folder_id, folders_cache)
            await db.document_folders.update_one(
                {"id": folder_id, "building_id": building_id}, {"$set": {"path": new_path}}
            )

    updated_folder = await db.document_folders.find_one({"id": folder_id, "building_id": building_id}, {"_id": 0})
    updated_folder["document_count"] = await db.documents.count_documents(
        {"folder_id": folder_id, "building_id": building_id}
    )
    updated_folder["subfolder_count"] = await db.document_folders.count_documents(
        {"parent_folder_id": folder_id, "building_id": building_id}
    )

    return DocumentFolderResponse(**updated_folder)


@api_router.delete("/folders/{folder_id}")
async def delete_folder(
        folder_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_folder
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    folder = await db.document_folders.find_one({"id": folder_id, "building_id": building_id}, {"_id": 0})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # System folders cannot be deleted
    if folder.get("is_system"):
        raise HTTPException(status_code=403, detail="Cannot delete system folders")

    # Check if folder is empty
    doc_count = await db.documents.count_documents({"folder_id": folder_id, "building_id": building_id})
    subfolder_count = await db.document_folders.count_documents(
        {"parent_folder_id": folder_id, "building_id": building_id}
    )

    if doc_count > 0 or subfolder_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Folder is not empty ({doc_count} documents, {subfolder_count} subfolders). Move or delete contents first.",
        )

    await db.document_folders.delete_one({"id": folder_id, "building_id": building_id})
    return {"message": "Folder deleted successfully"}


@api_router.put("/folders/{folder_id}/move")
async def move_folder(
        folder_id: str,
        data: FolderMoveRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: move_folder
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Sentinel 🛡️: Scoped search by building_id to prevent BOLA
    folder = await db.document_folders.find_one({"id": folder_id, "building_id": building_id}, {"_id": 0})
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # System folders cannot be moved
    if folder.get("is_system"):
        raise HTTPException(status_code=403, detail="Cannot move system folders")

    # Validate new parent exists
    if data.new_parent_id:
        # Sentinel 🛡️: Scoped search by building_id to prevent BOLA
        new_parent = await db.document_folders.find_one(
            {"id": data.new_parent_id, "building_id": building_id}, {"_id": 0}
        )
        if not new_parent:
            raise HTTPException(status_code=404, detail="Target folder not found")

        # Prevent circular references (moving folder into its own descendant)
        current_parent = new_parent
        while current_parent.get("parent_folder_id"):
            if current_parent["parent_folder_id"] == folder_id:
                raise HTTPException(
                    status_code=400, detail="Cannot move folder into its own descendant"
                )
            # Sentinel 🛡️: Scoped search by building_id to prevent BOLA
            current_parent = await db.document_folders.find_one(
                {"id": current_parent["parent_folder_id"], "building_id": building_id}, {"_id": 0}
            )
            if not current_parent:
                break

    # Check for name conflicts in new location
    # Sentinel 🛡️: Scoped search by building_id
    existing = await db.document_folders.find_one(
        {
            "name": folder["name"],
            "parent_folder_id": data.new_parent_id,
            "id": {"$ne": folder_id},
            "building_id": building_id,
        }
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Folder with this name already exists in target location",
        )

    # Update folder
    # Sentinel 🛡️: Scoped update by building_id
    await db.document_folders.update_one(
        {"id": folder_id, "building_id": building_id},
        {
            "$set": {
                "parent_folder_id": data.new_parent_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    # Rebuild paths for this folder and all descendants
    # Sentinel 🛡️: Scoped search by building_id
    folders = await db.document_folders.find({"building_id": building_id}, {"_id": 0}).to_list(1000)
    folders_cache = {f["id"]: f for f in folders}

    async def update_paths_recursive(fid):
        """Generated function header.

        Function: update_paths_recursive
        Path: backend/server.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        new_path = build_folder_path(fid, folders_cache)
        await db.document_folders.update_one({"id": fid}, {"$set": {"path": new_path}})

        # Update children
        for child in folders:
            if child.get("parent_folder_id") == fid:
                await update_paths_recursive(child["id"])

    await update_paths_recursive(folder_id)

    return {"message": "Folder moved successfully"}


@api_router.put("/documents/{doc_id}/move")
async def move_document(
        doc_id: str,
        data: DocumentMoveRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Move a document to a different folder. Scoped to building.
    Sentinel 🛡️: Enforces BOLA protection by verifying building membership for both doc and folder.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Sentinel 🛡️: Scoped search by building_id to prevent BOLA (cross-tenant access)
    doc = await db.documents.find_one({"id": doc_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Authorization: Must be uploader or admin
    if (
            doc.get("uploaded_by") != current_user["id"]
            and not permissions.can_manage_users
    ):
        raise HTTPException(
            status_code=403, detail="Not authorized to move this document"
        )

    # Validate target folder exists in this building
    if data.folder_id:
        # Sentinel 🛡️: Ensure folder exists in the current building context
        folder = await db.document_folders.find_one({"id": data.folder_id, "building_id": building_id}, {"_id": 0})
        if not folder:
            raise HTTPException(status_code=404, detail="Target folder not found")

    await db.documents.update_one(
        {"id": doc_id, "building_id": building_id},
        {
            "$set": {
                "folder_id": data.folder_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    return {"message": "Document moved successfully"}


@api_router.put("/documents/{doc_id}/rename")
async def rename_document(
        doc_id: str,
        data: DocumentRenameRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Rename a document. Scoped to building.
    Sentinel 🛡️: Enforces BOLA protection by verifying building membership.
    Sentinel 🛡️: Fixes Stored XSS by using sanitized title in database update.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Sentinel 🛡️: Scoped search by building_id to prevent BOLA (cross-tenant access)
    doc = await db.documents.find_one({"id": doc_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Authorization: Must be uploader or admin
    if (
            doc.get("uploaded_by") != current_user["id"]
            and not permissions.can_manage_users
    ):
        raise HTTPException(
            status_code=403, detail="Not authorized to rename this document"
        )

    # SECURITY: Sanitize input to prevent Stored XSS
    sanitized_title = html_lib.escape(data.new_title)

    await db.documents.update_one(
        {"id": doc_id, "building_id": building_id},
        {
            "$set": {
                "title": sanitized_title,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    return {"message": "Document renamed successfully"}


@api_router.post("/documents/bulk-move")
async def bulk_move_documents(
        data: BulkMoveRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """
    Move multiple documents to a different folder. Scoped to building.
    Sentinel 🛡️: Enforces BOLA protection by verifying building membership for both target folder and documents.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_upload_documents:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Validate target folder exists in this building
    if data.target_folder_id:
        # Sentinel 🛡️: Scoped search by building_id to prevent BOLA
        folder = await db.document_folders.find_one(
            {"id": data.target_folder_id, "building_id": building_id}, {"_id": 0}
        )
        if not folder:
            raise HTTPException(status_code=404, detail="Target folder not found")

    # Build query scoped to the user's own documents when not admin
    # Sentinel 🛡️: Always scope to building_id to prevent BOLA/cross-tenant access.
    move_query: dict = {"id": {"$in": data.document_ids}, "building_id": building_id}
    if not permissions.can_manage_users:
        move_query["uploaded_by"] = current_user["id"]

    # Update all matching documents
    result = await db.documents.update_many(
        move_query,
        {
            "$set": {
                "folder_id": data.target_folder_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    return {
        "message": f"Moved {result.modified_count} documents successfully",
        "count": result.modified_count,
    }


# ==================== MARKETPLACE ROUTES ====================
# @featuretrace:marketplace — CRUD for community listings.
# Layer: router
# Data flow: MarketplacePage.jsx → /api/listings → db.listings (building-scoped).
# Scope param: "building" (default) = own building only; "global" = all buildings.
# Related: frontend/src/pages/public/MarketplacePage.jsx (scope toggle, X-Building-ID header)
#           frontend/src/pages/dashboard/marketplace/page.tsx (dashboard wrapper)
#           cron/cron_property_scraper.py (auto-populates property category listings)
#           POST /listings/scrape (manual trigger for cron_property_scraper)


@api_router.post("/listings", response_model=ListingResponse)
async def create_listing(
        listing: ListingCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: create_listing
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_create_listings:
        raise HTTPException(status_code=403, detail="Not authorized to create listings")

    listing_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Default expiry: 90 days from creation if not provided
    expires_at = listing.expires_at or (now + timedelta(days=90)).isoformat()

    listing_doc = {
        "id": listing_id,
        **listing.model_dump(exclude={"expires_at"}),
        "building_id": building_id,
        "created_by": current_user["id"],
        "created_by_name": current_user["full_name"],
        "status": "active",
        "expires_at": expires_at,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    await db.listings.insert_one(listing_doc)
    return ListingResponse(**listing_doc)


@api_router.get("/listings", response_model=List[ListingResponse])
async def get_listings(
        listing_type: Optional[str] = None,
        status: str = "active",
        scope: Optional[str] = None,  # "global" = all buildings; default = own building only
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_optional_building),
):
    """Generated function header.

    Function: get_listings
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {"status": status}

    # Scope: "global" shows all buildings; default restricts to the requester's building.
    if scope != "global":
        query["building_id"] = building_id

    # Only show private listings to approved users (or administrative roles)
    if not is_approved_user(current_user):
        query["is_public"] = True

    if listing_type:
        query["listing_type"] = listing_type

    listings = await db.listings.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return [ListingResponse(**l) for l in listings]


@api_router.get("/listings/{listing_id}", response_model=ListingResponse)
async def get_listing(
        listing_id: str,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_optional_building),
):
    # Allow lookup by id regardless of building so shared/global links work;
    # visibility of private listings is still enforced below.
    """Generated function header.

    Function: get_listing
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if not listing.get("is_public", False):
        # Only show private listings to approved users (or administrative roles)
        if not is_approved_user(current_user):
            raise HTTPException(
                status_code=403, detail="Not authorized to view private listings"
            )

    return ListingResponse(**listing)


@api_router.put("/listings/{listing_id}", response_model=ListingResponse)
async def update_listing(
        listing_id: str,
        update_data: ListingCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: update_listing
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    listing = await db.listings.find_one(
        {"id": listing_id, "building_id": building_id}, {"_id": 0}
    )

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["created_by"] != current_user["id"]:
        permissions = get_user_permissions(current_user)
        if not permissions.can_manage_users:
            raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = update_data.model_dump()
    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Sentinel 🛡️: Enforce BOLA protection by scoping update to the current building context
    await db.listings.update_one({"id": listing_id, "building_id": building_id}, {"$set": update_dict})

    updated = await db.listings.find_one({"id": listing_id, "building_id": building_id}, {"_id": 0})
    if not updated:
        raise HTTPException(status_code=404, detail="Listing not found in this building")
    return ListingResponse(**updated)


@api_router.delete("/listings/{listing_id}")
async def delete_listing(
        listing_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Generated function header.

    Function: delete_listing
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    listing = await db.listings.find_one(
        {"id": listing_id, "building_id": building_id}, {"_id": 0}
    )

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["created_by"] != current_user["id"]:
        permissions = get_user_permissions(current_user)
        if not permissions.can_manage_users:
            raise HTTPException(status_code=403, detail="Not authorized")

    # Sentinel 🛡️: Enforce BOLA protection by scoping deletion to the current building context
    result = await db.listings.delete_one({"id": listing_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Listing not found in this building")
    return {"message": "Listing deleted successfully"}


# Note: Chat and Announcement routes are handled by routers/chat.py and routers/communication.py

OUTSTANDING_ISSUE_ALLOWED_STATUSES = {
    "all_good",
    "in_progress",
    "not_good",
    "ec",
    "all_else",
}
OUTSTANDING_ISSUE_EDITOR_ROLES = {"super_admin", "strata_admin", "ec_member"}
OUTSTANDING_ISSUE_VIEW_ROLES = (
        OUTSTANDING_ISSUE_EDITOR_ROLES | {"strata_manager", "owner", "tenant"}
)


def _can_view_outstanding_issues(user: dict) -> bool:
    """Generated function header.

    Function: _can_view_outstanding_issues
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _effective_role(user) in OUTSTANDING_ISSUE_VIEW_ROLES


def _can_edit_outstanding_issues(user: dict) -> bool:
    """Generated function header.

    Function: _can_edit_outstanding_issues
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _effective_role(user) in OUTSTANDING_ISSUE_EDITOR_ROLES


def _normalize_outstanding_issue_status(raw_status: str) -> str:
    """Generated function header.

    Function: _normalize_outstanding_issue_status
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    normalized = (raw_status or "").strip().lower().replace(" ", "_")
    if normalized not in OUTSTANDING_ISSUE_ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid outstanding issue status")
    return normalized


def _clean_optional_html(text: Optional[str]) -> Optional[str]:
    """Generated function header.

    Function: _clean_optional_html
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if text is None:
        return None
    return nh3.clean(text.strip())


def _build_word_image_src(logo_url: Optional[str]) -> Optional[str]:
    """Generated function header.

    Function: _build_word_image_src
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    fallback_path = "/eastgate-logo.png"
    candidate = (logo_url or fallback_path).strip()
    if not candidate:
        return None
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate

    public_root = ROOT_DIR.parent / "frontend" / "public"
    normalized = candidate.lstrip("/")
    file_path = public_root / normalized
    if not file_path.exists() or not file_path.is_file():
        return None

    suffix = file_path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _minutes_text_to_html(text: str) -> str:
    """Generated function header.

    Function: _minutes_text_to_html
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text or "") if block.strip()]
    if not blocks:
        return "<p>No meeting notes recorded.</p>"
    return "".join(
        f"<p>{html_lib.escape(block).replace(chr(10), '<br/>')}</p>" for block in blocks
    )


def _build_meeting_minutes_document(meeting: dict, settings: dict) -> bytes:
    """Generated function header.

    Function: _build_meeting_minutes_document
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    building_name = settings.get("building_name") or "Building"
    building_address = settings.get("building_address") or ""
    logo_src = _build_word_image_src(settings.get("logo_url"))
    meeting_date = meeting.get("meeting_date") or ""
    try:
        meeting_date = datetime.fromisoformat(meeting_date.replace("Z", "+00:00")).strftime("%d %B %Y, %I:%M %p")
    except Exception:
        pass

    attendees = meeting.get("attendees") or []
    attendees_html = "".join(
        f"<li>{html_lib.escape(attendee)}</li>" for attendee in attendees if attendee
    ) or "<li>Attendees to be confirmed</li>"

    agenda_html = "".join(
        f"<li>{html_lib.escape(item)}</li>" for item in (meeting.get("agenda") or []) if item
    ) or "<li>No agenda recorded</li>"

    logo_html = (
        f'<img src="{logo_src}" alt="Building logo" style="height:72px; margin-bottom:12px;" />'
        if logo_src
        else ""
    )

    html = f"""<!DOCTYPE html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:w="urn:schemas-microsoft-com:office:word"
      xmlns="http://www.w3.org/TR/REC-html40">
<head>
  <meta charset="utf-8" />
  <title>Minutes of the Meeting</title>
  <style>
    body {{ font-family: Arial, sans-serif; color: #1f2937; margin: 36px; line-height: 1.5; }}
    .header {{ text-align: center; border-bottom: 2px solid #1f2937; padding-bottom: 16px; margin-bottom: 24px; }}
    .header h1 {{ margin: 0; font-size: 24px; }}
    .header h2 {{ margin: 6px 0 0; font-size: 16px; font-weight: normal; }}
    .meta {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
    .meta td {{ padding: 8px 10px; border: 1px solid #d1d5db; vertical-align: top; }}
    .meta .label {{ width: 180px; font-weight: bold; background: #f3f4f6; }}
    h3 {{ margin: 22px 0 10px; font-size: 16px; }}
    ul {{ margin: 0 0 0 18px; padding: 0; }}
    p {{ margin: 0 0 12px; }}
    .footer {{ margin-top: 32px; font-size: 12px; color: #6b7280; }}
  </style>
</head>
<body>
  <div class="header">
    {logo_html}
    <h1>{html_lib.escape(building_name)}</h1>
    <h2>Minutes of the Meeting</h2>
    {'<div>' + html_lib.escape(building_address) + '</div>' if building_address else ''}
  </div>

  <table class="meta">
    <tr><td class="label">Meeting title</td><td>{html_lib.escape(meeting.get("title") or "Executive Committee Meeting")}</td></tr>
    <tr><td class="label">Meeting date</td><td>{html_lib.escape(meeting_date)}</td></tr>
    <tr><td class="label">Location</td><td>{html_lib.escape(meeting.get("location") or "TBC")}</td></tr>
    <tr><td class="label">Prepared by</td><td>{html_lib.escape(meeting.get("updated_by_name") or meeting.get("created_by_name") or "Portal user")}</td></tr>
  </table>

  <h3>Attendees</h3>
  <ul>{attendees_html}</ul>

  <h3>Agenda</h3>
  <ul>{agenda_html}</ul>

  <h3>Meeting notes</h3>
  {_minutes_text_to_html(meeting.get("minutes") or "")}

  <div class="footer">
    Generated by StrataOS for distribution by the Strata Manager.
  </div>
</body>
</html>"""
    return html.encode("utf-8")


MEETING_NOTIFICATION_ROLES = [
    UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
]
MEETING_STATUS_VALUES = {"scheduled", "completed", "archived"}


def _sanitize_meeting_payload(payload: dict) -> dict:
    """Generated function header.

    Function: _sanitize_meeting_payload
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    sanitized: dict = {}

    if "title" in payload and payload["title"] is not None:
        sanitized["title"] = html_lib.escape(payload["title"].strip())
    if "location" in payload and payload["location"] is not None:
        sanitized["location"] = html_lib.escape(payload["location"].strip())
    if "meeting_date" in payload and payload["meeting_date"] is not None:
        sanitized["meeting_date"] = payload["meeting_date"].strip()
    if "description" in payload and payload["description"] is not None:
        cleaned_description = nh3.clean(payload["description"].strip())
        sanitized["description"] = cleaned_description or None
    if "agenda" in payload and payload["agenda"] is not None:
        sanitized["agenda"] = [html_lib.escape(item.strip()) for item in payload["agenda"] if item.strip()]
    if "attendees" in payload and payload["attendees"] is not None:
        sanitized["attendees"] = [html_lib.escape(item.strip()) for item in payload["attendees"] if item.strip()]
    if "minutes" in payload and payload["minutes"] is not None:
        sanitized["minutes"] = nh3.clean(payload["minutes"].strip())
    if "status" in payload and payload["status"] is not None:
        sanitized["status"] = payload["status"]

    return sanitized


async def _meeting_email_notifications_enabled(user_id: str, building_id: str) -> bool:
    """Generated function header.

    Function: _meeting_email_notifications_enabled
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scoped_prefs = await db.email_preferences.find_one(
        {"user_id": user_id, "building_id": building_id},
        {"_id": 0, "notices_enabled": 1},
    )
    if scoped_prefs and scoped_prefs.get("notices_enabled") is False:
        return False

    legacy_prefs = await db.email_notification_preferences.find_one(
        {"user_id": user_id},
        {"_id": 0, "notices_enabled": 1},
    )
    if legacy_prefs and legacy_prefs.get("notices_enabled") is False:
        return False

    return True


async def _notify_committee_meeting_recipients(
        meeting: dict,
        building_id: str,
        actor_name: str,
        action: str,
) -> None:
    """Generated function header.

    Function: _notify_committee_meeting_recipients
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    recipients = await db.users.find(
        {
            "building_id": building_id,
            "role": {"$in": MEETING_NOTIFICATION_ROLES},
            "is_active": True,
            "is_approved": True,
            "status": {"$ne": "archived"},
        },
        {"_id": 0, "id": 1, "email": 1, "full_name": 1},
    ).to_list(50)

    if not recipients:
        return

    portal_url = _get_portal_url()
    meeting_link_path = f"/governance/meetings/notes?meetingId={quote_plus(meeting['id'])}"
    meeting_link = f"{portal_url}{meeting_link_path}"
    safe_title = html_lib.escape(meeting.get("title", "Committee Meeting"))
    safe_location = html_lib.escape(meeting.get("location", "TBC"))
    safe_actor = html_lib.escape(actor_name or "Portal user")
    safe_when = html_lib.escape(meeting.get("meeting_date", "TBC"))
    title = "Committee Meeting Scheduled" if action == "scheduled" else "Committee Meeting Updated"
    message = (
        f"{meeting.get('title', 'Committee Meeting')} is scheduled for {meeting.get('meeting_date', 'TBC')}"
        if action == "scheduled"
        else f"{meeting.get('title', 'Committee Meeting')} was updated by {actor_name or 'Portal user'}"
    )
    email_subject = f"{title} — {meeting.get('title', 'Committee Meeting')}"
    email_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;color:#1f2937">
      <h2 style="margin-bottom:8px">{html_lib.escape(title)}</h2>
      <p>{safe_actor} {('scheduled' if action == 'scheduled' else 'updated')} the following Executive Committee meeting.</p>
      <ul>
        <li><strong>Meeting:</strong> {safe_title}</li>
        <li><strong>Date &amp; time:</strong> {safe_when}</li>
        <li><strong>Location:</strong> {safe_location}</li>
      </ul>
      <p><a href="{html_lib.escape(meeting_link)}">Open meeting notes and minutes</a></p>
    </div>
    """
    email_text = (
        f"{title}\n\n"
        f"{actor_name or 'Portal user'} {('scheduled' if action == 'scheduled' else 'updated')} "
        f"{meeting.get('title', 'Committee Meeting')}.\n"
        f"When: {meeting.get('meeting_date', 'TBC')}\n"
        f"Where: {meeting.get('location', 'TBC')}\n"
        f"Open meeting notes: {meeting_link}"
    )

    email_tasks = []
    for recipient in recipients:
        await create_user_notification(
            user_id=recipient["id"],
            title=title,
            message=message,
            notification_type="meeting",
            link=meeting_link_path,
            building_id=building_id,
        )
        if recipient.get("email") and await _meeting_email_notifications_enabled(recipient["id"], building_id):
            email_tasks.append(
                send_email_async(
                    recipient["email"],
                    email_subject,
                    email_html,
                    email_text,
                )
            )

    if email_tasks:
        await asyncio.gather(*email_tasks, return_exceptions=True)


# ==================== MEETING ROUTES ====================


@api_router.post("/meetings", response_model=MeetingResponse)
async def create_meeting(
        meeting: MeetingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_meeting
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized to create meetings")

    meeting_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    meeting_data = _sanitize_meeting_payload(meeting.model_dump())

    meeting_doc = {
        "id": meeting_id,
        "building_id": building_id,
        **meeting_data,
        "minutes": None,
        "status": "scheduled",
        "created_by": current_user["id"],
        "created_by_name": current_user["full_name"],
        "created_at": now,
        "updated_at": now,
    }

    await db.meetings.insert_one(meeting_doc)
    await _notify_committee_meeting_recipients(
        meeting_doc,
        building_id=building_id,
        actor_name=current_user.get("full_name", ""),
        action="scheduled",
    )
    return MeetingResponse(**meeting_doc)


@api_router.get("/meetings", response_model=List[MeetingResponse])
async def get_meetings(
        status: Optional[str] = None,
        include_archived: bool = False,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_meetings
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_meetings:
        raise HTTPException(status_code=403, detail="Not authorized to view meetings")

    query = {"building_id": building_id}
    if status:
        query["status"] = status
    elif not include_archived:
        # By default exclude archived meetings
        query["status"] = {"$ne": "archived"}

    meetings = (
        await db.meetings.find(query, {"_id": 0}).sort("meeting_date", -1).to_list(100)
    )
    return [MeetingResponse(**m) for m in meetings]


@api_router.get("/meetings/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
        meeting_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_meeting
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    meeting = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    return MeetingResponse(**meeting)


@api_router.put("/meetings/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(
        meeting_id: str,
        meeting_update: Optional[MeetingUpdate] = None,
        minutes: Optional[str] = None,
        status: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_meeting
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    existing_meeting = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not existing_meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    update_dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if meeting_update is not None:
        update_dict.update(_sanitize_meeting_payload(meeting_update.model_dump(exclude_none=True)))
    if minutes is not None:
        update_dict["minutes"] = nh3.clean(minutes)
    if status is not None:
        update_dict["status"] = status
    if update_dict.get("status") is not None and update_dict["status"] not in MEETING_STATUS_VALUES:
        raise HTTPException(status_code=400, detail="Invalid meeting status")
    update_dict["updated_by"] = current_user["id"]
    update_dict["updated_by_name"] = current_user["full_name"]

    await db.meetings.update_one({"id": meeting_id, "building_id": building_id}, {"$set": update_dict})

    meeting = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    notify_fields = {"title", "description", "meeting_date", "location", "agenda", "attendees"}
    if notify_fields.intersection(update_dict.keys()):
        await _notify_committee_meeting_recipients(
            meeting,
            building_id=building_id,
            actor_name=current_user.get("full_name", ""),
            action="updated",
        )

    return MeetingResponse(**meeting)


@api_router.delete("/meetings/{meeting_id}")
async def delete_meeting(
        meeting_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_meeting
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.meetings.delete_one({"id": meeting_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Meeting not found")

    return {"message": "Meeting deleted"}


@api_router.put("/meetings/{meeting_id}/notes", response_model=MeetingResponse)
async def update_meeting_notes(
        meeting_id: str,
        notes_update: MeetingNotesUpdate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_meeting_notes
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    meeting = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    now = datetime.now(timezone.utc).isoformat()
    agenda = [html_lib.escape(item.strip()) for item in notes_update.agenda if item.strip()]
    attendees = [html_lib.escape(attendee.strip()) for attendee in notes_update.attendees if attendee.strip()]
    update_dict = {
        "agenda": agenda,
        "attendees": attendees,
        "minutes": nh3.clean(notes_update.minutes.strip()),
        "updated_at": now,
        "updated_by": current_user["id"],
        "updated_by_name": current_user["full_name"],
    }

    await db.meetings.update_one(
        {"id": meeting_id, "building_id": building_id},
        {"$set": update_dict},
    )

    updated = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not updated:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return MeetingResponse(**updated)


@api_router.get("/meetings/{meeting_id}/minutes-document")
async def download_meeting_minutes_document(
        meeting_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: download_meeting_minutes_document
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    meeting = await db.meetings.find_one({"id": meeting_id, "building_id": building_id}, {"_id": 0})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not meeting.get("minutes"):
        raise HTTPException(status_code=400, detail="Meeting notes have not been recorded yet")

    settings = await _get_general_settings_or_default(
        building_id,
        {"_id": 0, "building_name": 1, "building_address": 1, "logo_url": 1},
    )
    document_bytes = _build_meeting_minutes_document(meeting, settings)
    safe_title = re.sub(r"[^A-Za-z0-9_-]+", "_", meeting.get("title") or "meeting_minutes").strip(
        "_") or "meeting_minutes"
    filename = f"{safe_title}.doc"
    return StreamingResponse(
        io.BytesIO(document_bytes),
        media_type="application/msword",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ==================== OUTSTANDING ISSUES REGISTER ====================


@api_router.get("/outstanding-issues", response_model=List[OutstandingIssueResponse])
async def get_outstanding_issues(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get outstanding issues register.
    Performance Optimization⚡: Parallelized issue fetch.
    """
    if not _can_view_outstanding_issues(current_user):
        raise HTTPException(status_code=403, detail="Not authorized to view the outstanding issues register")

    issues = (
        await db.outstanding_issues.find({"building_id": building_id}, {"_id": 0})
        .sort("updated_at", -1)
        .to_list(500)
    )
    return [OutstandingIssueResponse(**issue) for issue in issues]


@api_router.post("/outstanding-issues", response_model=OutstandingIssueResponse)
async def create_outstanding_issue(
        issue: OutstandingIssueCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_outstanding_issue
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _can_edit_outstanding_issues(current_user):
        raise HTTPException(status_code=403, detail="Not authorized to edit the outstanding issues register")

    now = datetime.now(timezone.utc).isoformat()
    issue_doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "issue": html_lib.escape(issue.issue.strip()),
        "details": nh3.clean(issue.details.strip()),
        "status": _normalize_outstanding_issue_status(issue.status),
        "updates_notes": _clean_optional_html(issue.updates_notes),
        "strata_web_meeting_tba": html_lib.escape(issue.strata_web_meeting_tba.strip()) if issue.strata_web_meeting_tba else None,
        "chair_notes": _clean_optional_html(issue.chair_notes),
        "created_by": current_user["id"],
        "created_by_name": current_user["full_name"],
        "updated_by": current_user["id"],
        "updated_by_name": current_user["full_name"],
        "created_at": now,
        "updated_at": now,
    }
    await db.outstanding_issues.insert_one(issue_doc)
    return OutstandingIssueResponse(**issue_doc)


@api_router.put("/outstanding-issues/{issue_id}", response_model=OutstandingIssueResponse)
async def update_outstanding_issue(
        issue_id: str,
        issue_update: OutstandingIssueUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_outstanding_issue
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not _can_edit_outstanding_issues(current_user):
        raise HTTPException(status_code=403, detail="Not authorized to edit the outstanding issues register")

    existing = await db.outstanding_issues.find_one({"id": issue_id, "building_id": building_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Outstanding issue not found")

    update_dict = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": current_user["id"],
        "updated_by_name": current_user["full_name"],
    }
    if issue_update.issue is not None:
        update_dict["issue"] = html_lib.escape(issue_update.issue.strip())
    if issue_update.details is not None:
        update_dict["details"] = nh3.clean(issue_update.details.strip())
    if issue_update.status is not None:
        update_dict["status"] = _normalize_outstanding_issue_status(issue_update.status)
    if issue_update.updates_notes is not None:
        update_dict["updates_notes"] = _clean_optional_html(issue_update.updates_notes)
    if issue_update.strata_web_meeting_tba is not None:
        update_dict["strata_web_meeting_tba"] = html_lib.escape(issue_update.strata_web_meeting_tba.strip())
    if issue_update.chair_notes is not None:
        update_dict["chair_notes"] = _clean_optional_html(issue_update.chair_notes)

    await db.outstanding_issues.update_one(
        {"id": issue_id, "building_id": building_id},
        {"$set": update_dict},
    )

    updated = await db.outstanding_issues.find_one({"id": issue_id, "building_id": building_id}, {"_id": 0})
    if not updated:
        raise HTTPException(status_code=404, detail="Outstanding issue not found")
    return OutstandingIssueResponse(**updated)


# ==================== TODO ROUTES ====================


@api_router.post("/todos", response_model=TodoResponse)
async def create_todo(
        todo: TodoCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_todo
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized to create todos")

    todo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Get assigned user name if exists
    assigned_to_name = None
    if todo.assigned_to:
        assigned_user = await db.users.find_one({"id": todo.assigned_to}, {"_id": 0})
        if assigned_user:
            assigned_to_name = assigned_user["full_name"]

    # SECURITY: Sanitize user input to prevent Stored XSS
    todo_data = todo.model_dump()
    todo_data["title"] = html_lib.escape(todo_data.get("title", ""))
    if todo_data.get("description"):
        todo_data["description"] = nh3.clean(todo_data["description"])

    todo_doc = {
        "id": todo_id,
        "building_id": building_id,
        **todo_data,
        "assigned_to_name": assigned_to_name,
        "status": "pending",
        "created_by": current_user["id"],
        "created_at": now,
        "updated_at": now,
    }

    await db.todos.insert_one(todo_doc)
    return TodoResponse(**todo_doc)


@api_router.get("/todos", response_model=List[TodoResponse])
async def get_todos(
        status: Optional[str] = None,
        meeting_id: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: get_todos
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_view_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    query = {"building_id": building_id}
    if status:
        query["status"] = status
    if meeting_id:
        query["meeting_id"] = meeting_id

    todos = await db.todos.find(query, {"_id": 0}).sort("due_date", 1).to_list(1000)
    return [TodoResponse(**t) for t in todos]


@api_router.put("/todos/{todo_id}", response_model=TodoResponse)
async def update_todo(
        todo_id: str,
        status: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_todo
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if status:
        update_dict["status"] = status

    await db.todos.update_one({"id": todo_id, "building_id": building_id}, {"$set": update_dict})

    todo = await db.todos.find_one({"id": todo_id, "building_id": building_id}, {"_id": 0})
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")

    return TodoResponse(**todo)


@api_router.delete("/todos/{todo_id}")
async def delete_todo(
        todo_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_todo
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.todos.delete_one({"id": todo_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Todo not found")

    return {"message": "Todo deleted successfully"}


# ==================== FINANCE ROUTES ====================


# ==================== SCHEDULE ROUTES ====================


@api_router.post("/schedule", response_model=ScheduleResponse)
async def create_schedule(
        schedule: ScheduleCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_schedule
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    schedule_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    assigned_to_name = None
    if schedule.assigned_to:
        assigned_user = await db.users.find_one(
            {"id": schedule.assigned_to}, {"_id": 0}
        )
        if assigned_user:
            assigned_to_name = assigned_user["full_name"]

    schedule_doc = {
        "id": schedule_id,
        **schedule.model_dump(),
        "assigned_to_name": assigned_to_name,
        "status": "scheduled",
        "created_by": current_user["id"],
        "created_at": now,
    }

    await db.schedules.insert_one(schedule_doc)
    return ScheduleResponse(**schedule_doc)


@api_router.get("/schedule", response_model=List[ScheduleResponse])
async def get_schedules(
        schedule_type: Optional[str] = None,
        assigned_to: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
):
    """Generated function header.

    Function: get_schedules
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if (
            not permissions.can_view_schedule
            and current_user["role"] != UserRole.SERVICE_PROVIDER
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    query = {}
    if schedule_type:
        query["schedule_type"] = schedule_type

    # Service providers can only see their own schedules
    if current_user["role"] == UserRole.SERVICE_PROVIDER:
        query["assigned_to"] = current_user["id"]
    elif assigned_to:
        query["assigned_to"] = assigned_to

    schedules = (
        await db.schedules.find(query, {"_id": 0}).sort("start_time", 1).to_list(1000)
    )
    return [ScheduleResponse(**s) for s in schedules]


# ==================== BLOG ROUTES ====================
# @featuretrace:news — News/blog articles auto-created by cron_news_scraper.py or manually by admins.
# Layer: router
# Data flow: BlogPage.jsx → /api/blog → db.blog_posts (building-scoped).
# Scope param: "global" = all buildings; default = own building only.
# Related: frontend/src/pages/public/BlogPage.jsx (scope toggle, X-Building-ID header)
#           frontend/src/pages/dashboard/BlogManagementPage.jsx (admin CRUD + scrape trigger)
#           cron/cron_news_scraper.py (populates building_id on every insert)
#           POST /blog/scrape (manual trigger for cron_news_scraper)


@api_router.post("/blog/scrape", response_model=Dict[str, Any])
async def trigger_news_scrape(
        current_user: Dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Manually trigger the news scraper script. Only super_admin can access."""
    if current_user.get("role") != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="Only Super Admin can trigger news scraper"
        )

    import subprocess
    import sys

    script_path = os.path.join(os.path.dirname(__file__), "cron", "cron_news_scraper.py")
    run_env = os.environ.copy()
    run_env["BUILDING_ID"] = building_id

    now = datetime.now(timezone.utc)
    status = "success"
    error_msg = None
    articles_count = 0

    try:
        # asyncio.to_thread, NOT a bare subprocess.run: this handler is `async def`,
        # and a blocking call here stalls the whole event loop for up to the timeout
        # below (180s here, 300s for the property scraper). With uvicorn running
        # 4 workers, a handful of concurrent triggers would freeze the API for every
        # tenant, not just the admin who clicked the button.
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.path.dirname(__file__),
            env=run_env,
            timeout=180,
        )
        output = result.stdout

        # Parse articles count from scraper output
        for line in output.splitlines():
            if "SCRAPER_RESULT: articles_created=" in line:
                try:
                    articles_count = int(line.split("articles_created=")[1].strip())
                except (IndexError, ValueError):
                    pass

        # Write output to log file. Redacted: the child re-reads backend/.env via
        # load_dotenv(), so anything it prints can carry credentials.
        log_dir = ROOT_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "news_scraper.log", "a") as f:
            f.write(f"\n--- Run at {now.isoformat()} ---\n")
            f.write(redact_secrets(output))

    except subprocess.CalledProcessError as e:
        status = "error"
        # redact_secrets(): a pymongo/asyncpg connection failure in the child prints
        # the connection URI *including its password*, and this string is persisted
        # to scraper_run_logs.error_message and scraper_settings.news.error_message,
        # both of which the Scraper Settings admin UI reads back. Those two sinks —
        # plus the log file above — are the ones that were exposed.
        #
        # The HTTP body is already covered: _normalise_detail() in
        # utils/error_response.py discards the detail of any response with
        # status >= 500, so the 500 raised below never echoed this to the client.
        # Redacting here is therefore defence in depth for that path and the actual
        # fix for the log/database paths.
        error_msg = f"Exit code {e.returncode}: {redact_secrets(e.stderr)[-500:] if e.stderr else 'no output'}"
        logger.error(f"News scrape failed: {error_msg}")
        output = redact_secrets(e.stderr)
    except subprocess.TimeoutExpired:
        status = "error"
        error_msg = "Scraper timed out after 3 minutes"
        logger.error("News scraper timed out")
        output = error_msg
    except Exception as e:
        status = "error"
        # Redacted for the same reason as the CalledProcessError branch — a driver
        # exception's str() can embed the credential-bearing connection URI.
        error_msg = redact_secrets(str(e))
        logger.error(f"News scrape failed: {error_msg}")
        output = error_msg

    # Save run log to DB
    await db.scraper_run_logs.insert_one({
        "scraper": "news",
        "building_id": building_id,
        "status": status,
        "ran_at": now.isoformat(),
        "triggered_by": current_user.get("email", "unknown"),
        "items_count": articles_count,
        "error_message": error_msg,
    })

    # Update last_run in scraper settings
    await db.scraper_settings.update_one(
        {"building_id": building_id},
        {"$set": {
            "news.last_run": now.isoformat(),
            "news.status": status,
            "news.error_message": error_msg,
        }},
        upsert=True,
    )

    if status == "error":
        raise HTTPException(status_code=500, detail=error_msg)

    return {
        "status": "success",
        "message": "News scrape completed successfully",
        "articles_count": articles_count,
        "output": output[-2000:] if len(output) > 2000 else output,
    }


@api_router.post("/listings/scrape", response_model=Dict[str, Any])
async def trigger_property_scrape(
        current_user: Dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Manually trigger the property listings scraper. Only super_admin can access."""
    if current_user.get("role") != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="Only Super Admin can trigger property scraper"
        )

    import subprocess
    import sys

    script_path = os.path.join(os.path.dirname(__file__), "cron", "cron_property_scraper.py")
    run_env = os.environ.copy()
    run_env["BUILDING_ID"] = building_id

    now = datetime.now(timezone.utc)
    status = "success"
    error_msg = None
    listings_count = 0

    try:
        # asyncio.to_thread — see the news-scraper handler above. A bare
        # subprocess.run here blocks the event loop for up to the 300s timeout.
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.path.dirname(__file__),
            env=run_env,
            timeout=300,
        )
        output = result.stdout

        # Parse listings count from scraper output
        for line in output.splitlines():
            if "SCRAPER_RESULT: listings_created=" in line:
                try:
                    listings_count = int(line.split("listings_created=")[1].strip())
                except (IndexError, ValueError):
                    pass

        # Write output to log file (redacted — the child holds the full secret set).
        log_dir = ROOT_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "property_scraper.log", "a") as f:
            f.write(f"\n--- Run at {now.isoformat()} ---\n")
            f.write(redact_secrets(output))

    except subprocess.CalledProcessError as e:
        status = "error"
        # Redacted before it reaches scraper_run_logs / scraper_settings (both read
        # back by the Scraper Settings admin UI) — see the news-scraper handler for
        # the full rationale, including why the HTTP body was already covered.
        error_msg = f"Exit code {e.returncode}: {redact_secrets(e.stderr)[-500:] if e.stderr else 'no output'}"
        logger.error(f"Property scrape failed: {error_msg}")
        output = redact_secrets(e.stderr)
    except subprocess.TimeoutExpired:
        status = "error"
        error_msg = "Scraper timed out after 5 minutes"
        logger.error("Property scraper timed out")
        output = error_msg
    except Exception as e:
        status = "error"
        error_msg = redact_secrets(str(e))
        logger.error(f"Property scrape failed: {error_msg}")
        output = error_msg

    # Save run log to DB
    await db.scraper_run_logs.insert_one({
        "scraper": "property",
        "building_id": building_id,
        "status": status,
        "ran_at": now.isoformat(),
        "triggered_by": current_user.get("email", "unknown"),
        "items_count": listings_count,
        "error_message": error_msg,
    })

    # Update last_run in scraper settings
    await db.scraper_settings.update_one(
        {"building_id": building_id},
        {"$set": {
            "property.last_run": now.isoformat(),
            "property.status": status,
            "property.error_message": error_msg,
        }},
        upsert=True,
    )

    if status == "error":
        raise HTTPException(status_code=500, detail=error_msg)

    return {
        "status": "success",
        "message": "Property scrape completed successfully",
        "listings_count": listings_count,
        "output": output[-2000:] if len(output) > 2000 else output,
    }


@api_router.post("/blog", response_model=BlogPostResponse)
async def create_blog_post(
        post: BlogPostCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    # Owners, Tenants, EC, and Chairman can create blog posts (per requirements)
    """Generated function header.

    Function: create_blog_post
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    allowed_roles = [
        UserRole.OWNER,
        UserRole.TENANT,
        UserRole.EC_MEMBER, UserRole.SUPER_ADMIN,
    ]
    if _effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Not authorized to create articles")

    post_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Auto-expire in 1 year if not specified
    expires_at = post.expires_at or (now + timedelta(days=365)).isoformat()

    # SECURITY: sanitize HTML content fields with nh3; plain-text fields use strip()
    post_dict = post.model_dump()
    post_dict["title"] = (post_dict.get("title") or "").strip()
    post_dict["content"] = nh3.clean(post_dict.get("content", ""))
    if post_dict.get("excerpt"):
        post_dict["excerpt"] = nh3.clean(post_dict["excerpt"])

    post_doc = {
        "id": post_id,
        "building_id": building_id,
        **post_dict,
        "author_id": current_user["id"],
        "author_name": current_user["full_name"],
        "views": 0,
        "is_draft": not post.is_published,
        "expires_at": expires_at,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    await db.blog_posts.insert_one(post_doc)
    return BlogPostResponse(**post_doc)


@api_router.get("/blog", response_model=List[BlogPostResponse])
async def get_blog_posts(
        is_published: Optional[bool] = None,
        include_drafts: bool = False,
        include_expired: bool = False,  # Admins can pass this to see expired posts
        scope: Optional[str] = None,  # "global" = all buildings; default = own building only
        current_user: Optional[dict] = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400)
):
    """Generated function header.

    Function: get_blog_posts
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query: dict = {} if scope == "global" else {"building_id": building_id}
    now = datetime.now(timezone.utc).isoformat()

    is_admin = False
    if current_user:
        permissions = get_user_permissions(current_user)
        is_admin = permissions.can_manage_users

    expiry_filter = [{"expires_at": None}, {"expires_at": {"$gt": now}}]

    if not current_user:
        # Public: published, non-expired only
        query["is_published"] = True
        query["is_draft"] = {"$ne": True}
        query["$or"] = expiry_filter
    else:
        if include_drafts and is_admin:
            pass  # Admins see everything (expired too unless include_expired=False)
        elif include_drafts:
            query["$or"] = [
                {"is_published": True},
                {"author_id": current_user["id"]},
            ]
        else:
            query["is_published"] = True

        if is_published is not None:
            query["is_published"] = is_published

        # Apply expiry filter unless admin explicitly asks for expired posts
        if not (is_admin and include_expired):
            # Avoid overwriting an existing $or — wrap in $and if needed
            if "$or" in query:
                existing_or = query.pop("$or")
                query["$and"] = [
                    {"$or": existing_or},
                    {"$or": expiry_filter}
                ]
            else:
                query["$or"] = expiry_filter

    posts = (
        await db.blog_posts.find(query, {"_id": 0}).sort("created_at", -1).to_list(100)
    )
    return [BlogPostResponse(**p) for p in posts]


@api_router.get("/blog/{post_id}", response_model=BlogPostResponse)
async def get_blog_post(
        post_id: str,
        building_id: str = Depends(get_building_or_400)
):
    """Generated function header.

    Function: get_blog_post
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    post = await db.blog_posts.find_one({"id": post_id, "building_id": building_id}, {"_id": 0})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Increment views
    await db.blog_posts.update_one({"id": post_id, "building_id": building_id}, {"$inc": {"views": 1}})
    post["views"] += 1

    return BlogPostResponse(**post)


@api_router.put("/blog/{post_id}", response_model=BlogPostResponse)
async def update_blog_post(
        post_id: str,
        post_data: BlogPostCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_blog_post
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    post = await db.blog_posts.find_one({"id": post_id, "building_id": building_id})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Author or Admin can update
    permissions = get_user_permissions(current_user)
    if post["author_id"] != current_user["id"] and not permissions.can_manage_users:
        raise HTTPException(
            status_code=403, detail="Not authorized to update this article"
        )

    # SECURITY: sanitize HTML content fields with nh3; plain-text fields use strip()
    update_dict = post_data.model_dump()
    update_dict["title"] = (update_dict.get("title") or "").strip()
    update_dict["content"] = nh3.clean(update_dict.get("content", ""))
    if update_dict.get("excerpt"):
        update_dict["excerpt"] = nh3.clean(update_dict["excerpt"])

    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
    update_dict["is_draft"] = not post_data.is_published

    await db.blog_posts.update_one({"id": post_id, "building_id": building_id}, {"$set": update_dict})

    updated = await db.blog_posts.find_one({"id": post_id, "building_id": building_id}, {"_id": 0})
    return BlogPostResponse(**updated)


@api_router.delete("/blog/{post_id}")
async def delete_blog_post(
        post_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_blog_post
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_post_announcements:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.blog_posts.delete_one({"id": post_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Post not found")

    return {"message": "Post deleted successfully"}


# ==================== EC MEMBER ROUTES ====================


@api_router.post("/ec-members", response_model=ECMemberResponse)
async def create_ec_member(
        member: ECMemberCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_ec_member
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    member_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    member_doc = {
        "id": member_id,
        "building_id": building_id,
        **member.model_dump(),
        "created_at": now
    }

    await db.ec_members.insert_one(member_doc)
    return ECMemberResponse(**member_doc)


@api_router.get("/ec-members", response_model=List[ECMemberResponse])
async def get_ec_members(
        current_user: Optional[dict] = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400),
):
    """The building's executive committee. Public, with contact details masked.

    This is deliberately a public endpoint — the committee listing is part of the
    marketing site — but `get_building_or_400` resolves `building_id` from an
    UNVERIFIED `X-Building-ID` header or `?building_id=` query parameter, so an
    unauthenticated caller can name any building. Returning `email` and `phone` on
    that basis published every committee member's direct contact details for every
    building to anyone who could enumerate ids.

    Name, position, bio and image stay public: that is all the public HomePage
    renders. AboutPage's mailto:/tel: links need the contact fields, and it shows
    them to signed-in residents, who now get them and anonymous callers do not.
    Same masking shape as get_unit_occupants.
    """
    members = await db.ec_members.find({"building_id": building_id}, {"_id": 0}).sort("order", 1).to_list(100)
    reveal_contact = bool(current_user) and is_approved_user(current_user)
    if not reveal_contact:
        members = [{**m, "email": None, "phone": None} for m in members]
    return [ECMemberResponse(**m) for m in members]


@api_router.put("/ec-members/{member_id}", response_model=ECMemberResponse)
async def update_ec_member(
        member_id: str,
        member: ECMemberCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_ec_member
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.ec_members.update_one({"id": member_id, "building_id": building_id}, {"$set": member.model_dump()})

    updated = await db.ec_members.find_one({"id": member_id, "building_id": building_id}, {"_id": 0})
    if not updated:
        raise HTTPException(status_code=404, detail="Member not found")

    return ECMemberResponse(**updated)


@api_router.delete("/ec-members/{member_id}")
async def delete_ec_member(
        member_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_ec_member
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.ec_members.delete_one({"id": member_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Member not found")

    return {"message": "Member deleted successfully"}


# ==================== EMERGENCY SERVICES ROUTES ====================


@api_router.post("/emergency-services", response_model=EmergencyServiceResponse)
async def create_emergency_service(
        service: EmergencyServiceCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: create_emergency_service
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    service_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    service_doc = {
        "id": service_id,
        "building_id": building_id,
        **service.model_dump(),
        "created_at": now
    }

    await db.emergency_services.insert_one(service_doc)
    return EmergencyServiceResponse(**service_doc)


@api_router.get("/emergency-services", response_model=List[EmergencyServiceResponse])
async def get_emergency_services(
        category: Optional[str] = None,
        current_user: Optional[dict] = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400)
):
    """Generated function header.

    Function: get_emergency_services
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    query = {"building_id": building_id}
    if category:
        query["category"] = category

    # Privacy filtering: non-authenticated users only see non-private services
    if not current_user:
        query["is_private"] = {"$ne": True}

    services = (
        await db.emergency_services.find(query, {"_id": 0})
        .sort("order", 1)
        .to_list(100)
    )

    # Map available_24_7 to is_24_7 for backward compatibility
    for s in services:
        if "available_24_7" in s and "is_24_7" not in s:
            s["is_24_7"] = s.get("available_24_7", False)
        if "order" not in s:
            s["order"] = 0
        if "is_private" not in s:
            s["is_private"] = False

    return [EmergencyServiceResponse(**s) for s in services]


@api_router.put(
    "/emergency-services/{service_id}", response_model=EmergencyServiceResponse
)
async def update_emergency_service(
        service_id: str,
        service: EmergencyServiceCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_emergency_service
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    # Chairman, Super Admin and EC can manage emergency contacts
    allowed_roles = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]
    if _effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.emergency_services.update_one(
        {"id": service_id, "building_id": building_id}, {"$set": service.model_dump()}
    )

    updated = await db.emergency_services.find_one({"id": service_id, "building_id": building_id}, {"_id": 0})
    if not updated:
        raise HTTPException(status_code=404, detail="Service not found")

    return EmergencyServiceResponse(**updated)


@api_router.delete("/emergency-services/{service_id}")
async def delete_emergency_service(
        service_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: delete_emergency_service
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.emergency_services.delete_one({"id": service_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Service not found")

    return {"message": "Service deleted successfully"}


# ==================== SITE SETTINGS ROUTES ====================


@api_router.get("/settings")
async def get_site_settings(building_id: str = Depends(get_optional_building)):
    """Get site settings. Public endpoint — also used by unauthenticated /register page."""
    settings = await _get_general_settings(building_id, {"_id": 0})
    default_ip = "A vision by: Silverfox Technologies, Australia • Contact: gagneet@silverfoxtechnologies.com.au"

    if not settings:
        # Return defaults for a new building
        settings = {
            "building_id": building_id,
            "building_name": "New Community",
            "building_address": "",
            "building_description": "",
            "contact_email": "",
            "contact_phone": "",
            "hero_image": "",
            "about_content": "",
            "footer_text": "",
            "ip_string": default_ip,
            "financial_year_start_month": 7,
            "levy_collection_frequency": "quarterly",
            "levy_due_months": [3, 6, 9, 12],
            "levy_due_day_type": "first",
            "levy_due_day": 1,
            "interest_rate_per_month": 0.02,
            "penalty_amount": 50.0,
            "grace_period_days": 14,
            "gst_registered": True,
            "levy_gst_rate": 0.10,
            "timezone": "Australia/Sydney",
            "projection_horizon_years": 10,
        }

    # Ensure levy due day fields have sensible defaults for existing installations
    settings.setdefault("levy_due_day_type", "first")
    settings.setdefault("levy_due_day", 1)
    settings.setdefault("ip_string", default_ip)
    settings.setdefault("gst_registered", True)
    settings.setdefault("levy_gst_rate", 0.10)
    # Registration & approval timing defaults
    settings.setdefault("admin_auto_approve_minutes", 15)
    settings.setdefault("guest_escalation_hours", 2)
    settings.setdefault("tenant_escalation_hours", 48)
    settings.setdefault("token_validity_hours", 72)
    settings.setdefault("notify_bcc_email", os.environ.get("REGISTRATION_NOTIFY_BCC_EMAIL", ""))
    settings.setdefault("notify_bcc_name", "Building Administrator")
    settings.setdefault("projection_horizon_years", 10)
    # Help & Contact defaults
    settings.setdefault("ec_email", "")
    settings.setdefault("ec_contact_phone", "")
    settings.setdefault("handbook_url", "/user-guides/full_guide.html")
    settings.setdefault("unit_plan_url", "/user-guides/units_plan.html")
    settings.setdefault("inclusions_url", "")
    # Payment & bank details defaults (overridden by DB values when stored)
    settings.setdefault("bank_name", "")
    settings.setdefault("bank_bsb", "")
    settings.setdefault("bank_account_number", "")
    settings.setdefault("bank_account_name", "")
    settings.setdefault("deft_ref", "")
    settings.setdefault("bpay_biller_code", "")
    settings.setdefault("bpay_ref", "")
    settings.setdefault("aus_post_code", "")
    settings.setdefault("aus_post_ref", "")
    settings.setdefault("building_abn", "")
    settings.setdefault("levy_interest_rate_pa", 10.0)
    settings.setdefault("levy_notice_disclaimer", "")
    settings.setdefault("plan_number", "")
    settings.setdefault("strata_address", settings.get("building_address", ""))
    # True = matches the actual pre-existing sync default (see SiteSettingsUpdate comment above)
    settings.setdefault("bank_feed_auto_approve", True)

    rate_limit_settings = await db.site_settings.find_one(
        {"id": RATE_LIMIT_SETTING_ID}, {"_id": 0}
    ) or {}
    for key, default in RATE_LIMIT_DEFAULTS.items():
        settings[key] = rate_limit_settings.get(key, settings.get(key, default))
    settings["rate_limit_multiplier"] = rate_limit_settings.get(
        "rate_limit_multiplier",
        settings.get("rate_limit_multiplier", 1.0),
    )

    return settings


@api_router.put("/settings")
async def update_site_settings(
        settings: SiteSettingsUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update site settings. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Validate levy collection schedule settings
    if settings.levy_collection_frequency and settings.levy_due_months:
        frequency = settings.levy_collection_frequency
        months = settings.levy_due_months

        # Validate month numbers (1-12)
        if any(m < 1 or m > 12 for m in months):
            raise HTTPException(
                status_code=400, detail="Month numbers must be between 1 and 12"
            )

        # Validate number of months based on frequency
        if frequency == "monthly" and len(months) != 12:
            raise HTTPException(
                status_code=400, detail="Monthly frequency requires all 12 months"
            )
        elif frequency == "quarterly" and len(months) != 4:
            raise HTTPException(
                status_code=400, detail="Quarterly frequency requires exactly 4 months"
            )
        elif frequency == "half_yearly" and len(months) != 2:
            raise HTTPException(
                status_code=400,
                detail="Half yearly frequency requires exactly 2 months",
            )
        elif frequency == "yearly" and len(months) != 1:
            raise HTTPException(
                status_code=400, detail="Yearly frequency requires exactly 1 month"
            )

        # Check for duplicate months
        if len(months) != len(set(months)):
            raise HTTPException(
                status_code=400, detail="Duplicate months not allowed in due dates"
            )

    # Merge existing data with updates using exclude_unset=True.
    # This allows us to differentiate between fields omitted from the request (keep existing)
    # and fields explicitly sent as empty strings or null (update to empty).
    update_dict = settings.model_dump(exclude_unset=True)
    existing = await _get_general_settings(building_id, {"_id": 0}) or {}

    rate_limit_fields = set(RATE_LIMIT_DEFAULTS.keys()) | {"rate_limit_multiplier"}
    rate_limit_update = {
        k: update_dict.pop(k)
        for k in list(update_dict.keys())
        if k in rate_limit_fields
    }
    rate_limit_update = {k: v for k, v in rate_limit_update.items() if v is not None}

    if rate_limit_update:
        if current_user.get("role") != UserRole.SUPER_ADMIN:
            rate_limit_update = {}
        else:
            for key, value in rate_limit_update.items():
                if key == "rate_limit_multiplier":
                    if value <= 0:
                        raise HTTPException(status_code=400, detail="Rate limit multiplier must be greater than 0")
                else:
                    if value < 1:
                        raise HTTPException(status_code=400, detail=f"{key} must be at least 1 request per minute")

    # IP Protection: Only authorized admin can change the IP string
    if "ip_string" in update_dict:
        auth_email, _ = _get_auth_admin()
        if current_user.get("email") != auth_email:
            del update_dict["ip_string"]

    if "projection_horizon_years" in update_dict:
        if current_user.get("role") != UserRole.SUPER_ADMIN:
            del update_dict["projection_horizon_years"]

    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    await _upsert_general_settings(building_id, update_dict)

    if rate_limit_update:
        rate_limit_update["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.site_settings.update_one(
            {"id": RATE_LIMIT_SETTING_ID},
            {"$set": {"id": RATE_LIMIT_SETTING_ID, **rate_limit_update}},
            upsert=True,
        )
        await refresh_rate_limit_config()

    if (
            "projection_horizon_years" in update_dict
            and update_dict["projection_horizon_years"] != existing.get("projection_horizon_years")
    ):
        from services.special_levy_service import recompute_special_levy_forecast
        from services.levy_stability_service import recompute_levy_stability_snapshot

        await recompute_special_levy_forecast(building_id, force=True)
        await recompute_levy_stability_snapshot(building_id, force=True)

    # Auto-regenerate levy calendar events when any levy schedule field changes
    levy_schedule_fields = {
        "levy_due_months", "levy_due_day_type", "levy_due_day", "levy_due_custom_dates",
    }
    if levy_schedule_fields & set(update_dict.keys()):
        import calendar as cal_mod
        current_year = datetime.now().year
        updated_settings = await _get_general_settings(building_id, {"_id": 0}) or {}
        levy_months = updated_settings.get("levy_due_months", [3, 6, 9, 12])
        levy_due_day_type = updated_settings.get("levy_due_day_type", "first")
        levy_due_day = updated_settings.get("levy_due_day")
        levy_due_custom_dates = updated_settings.get("levy_due_custom_dates") or {}

        for yr in (current_year, current_year + 1):
            levy_dates = []
            for idx, month in enumerate(sorted(levy_months)):
                last_day = cal_mod.monthrange(yr, month)[1]
                if levy_due_day_type == "first":
                    day = 1
                elif levy_due_day_type == "middle":
                    day = 15
                elif levy_due_day_type == "last":
                    day = last_day
                elif levy_due_day_type == "custom":
                    m_str = str(month)
                    day = min(int(levy_due_custom_dates[m_str]), last_day) if m_str in levy_due_custom_dates else min(
                        levy_due_day or 1, last_day)
                else:
                    day = min(levy_due_day or 1, last_day)
                levy_dates.append({
                    "quarter": f"Q{idx + 1}",
                    "date": f"{yr}-{str(month).zfill(2)}-{str(day).zfill(2)}",
                    "title": f"Q{idx + 1} Levy Due - FY {yr}-{yr + 1}",
                })

            fy_suffix = f"FY {yr}-{yr + 1}"
            await db.events.delete_many({
                "building_id": building_id,
                "event_type": "levy_due",
                "title": {"$regex": fy_suffix},
            })
            now_iso = datetime.now(timezone.utc).isoformat()
            for levy in levy_dates:
                await db.events.insert_one({
                    "id": str(uuid.uuid4()),
                    "building_id": building_id,
                    "title": levy["title"],
                    "description": f"Quarterly strata levy payment due for {levy['quarter']}",
                    "event_type": "levy_due",
                    "start_date": levy["date"],
                    "end_date": None,
                    "location": None,
                    "is_recurring": True,
                    "recurrence_rule": "yearly",
                    "source": "system",
                    "source_url": None,
                    "is_public": True,
                    "created_by": current_user["id"],
                    "created_at": now_iso,
                })

    return await get_site_settings(building_id)


# ==================== LEGAL PAGES ROUTES ====================

LEGAL_PAGE_DEFAULTS = {
    "privacy-policy": "# Privacy Policy\n\nThis privacy policy explains how your strata building collects and uses your personal information.",
    "terms-of-use": "# Terms of Use\n\nBy using this strata management platform, you agree to these terms of use.",
}


@api_router.get("/legal-pages/{slug}")
async def get_legal_page(
        slug: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get building-specific legal page. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")
    doc = await db.legal_pages.find_one({"building_id": building_id, "slug": slug}, {"_id": 0})
    if not doc:
        return {
            "building_id": building_id,
            "slug": slug,
            "content": LEGAL_PAGE_DEFAULTS.get(slug, ""),
            "updated_at": None,
        }
    return doc


@api_router.put("/legal-pages/{slug}")
async def update_legal_page(
        slug: str,
        body: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update building-specific legal page. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")
    now = datetime.now(timezone.utc).isoformat()
    await db.legal_pages.update_one(
        {"building_id": building_id, "slug": slug},
        {
            "$set": {
                "building_id": building_id,
                "slug": slug,
                "content": body.get("content", ""),
                "updated_at": now,
                "updated_by": current_user["id"],
            }
        },
        upsert=True,
    )
    doc = await db.legal_pages.find_one({"building_id": building_id, "slug": slug}, {"_id": 0})
    return doc


# ==================== UNIT DISPLAY (NUMBERING) SETTINGS ====================


@api_router.get("/settings/unit-display")
async def get_unit_display_config(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Return the building's unit display prefix rules.

    Rules map numeric lot ranges to display prefixes, e.g. East Gate:
    UA 1-70 (apartments), TH 71-87 (townhouses), pad 3 → lot 87 = "TH087".
    Empty rules mean the building displays raw stored unit numbers.
    """
    return {"building_id": building_id, "rules": await _get_unit_display_rules(building_id)}


@api_router.put("/settings/unit-display")
async def update_unit_display_config(
        payload: UnitDisplayRulesUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Set the building's unit display prefix rules (onboarding/admin).

    Display-only configuration: changing rules never rewrites stored unit
    keys — canonical resolution (utils/unit_number.py) uses the rules to
    match user-entered values against existing ``units`` rows.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")
    rules = [r.model_dump() for r in payload.rules]
    saved = await _upsert_unit_display_rules(building_id, rules)
    return {"building_id": building_id, "rules": saved}


# ==================== SCRAPER SETTINGS ROUTES ====================


@api_router.get("/settings/scrapers")
async def get_scraper_settings(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get scraper settings, stats, and status. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super admin only")

    # Get settings from database (or use defaults)
    settings_doc = await db.scraper_settings.find_one({"building_id": building_id}, {"_id": 0})

    if not settings_doc:
        # Return default settings
        settings_doc = {
            "building_id": building_id,
            "news": {
                "enabled": True,
                "schedule": "0 1 * * 6",  # Weekly Saturday 1 AM
                "schedule_preset": "weekly",
                "method": "rss",
                "max_articles": 10,
                "min_content_length": 150,
                "relevance_threshold": 0.12,
                "status": "idle",
                "last_run": None,
                "next_run": None,
                "error_message": None,
            },
            "property": {
                "enabled": True,
                "schedule": "0 6 * * *",  # Daily 6 AM
                "schedule_preset": "daily",
                "max_listings_per_suburb": 10,
                "expiry_days": 30,
                "suburbs": ["Coombs", "Whitlam", "Wright", "Denman Prospect"],
                "property_sites": [
                    "realestate.com.au",
                    "domain.com.au",
                    "allhomes.com.au",
                    "zango.com.au",
                ],
                "status": "idle",
                "last_run": None,
                "next_run": None,
                "error_message": None,
            },
        }
        # Save defaults
        await db.scraper_settings.insert_one(settings_doc)

    # Fetch all stats concurrently — 8 independent DB operations run in parallel.
    # Only the latest 1 run record is needed for last_run/last_run_count; counts
    # are computed via count_documents (index scan, O(1)) instead of loading 100
    # records and filtering in Python.
    _q_news = {"building_id": building_id, "scraper": "news"}
    _q_prop = {"building_id": building_id, "scraper": "property"}
    (
        news_last,
        prop_last,
        news_total_runs,
        news_success_count,
        news_fail_count,
        prop_total_runs,
        prop_success_count,
        prop_fail_count,
        total_articles,
        total_listings,
    ) = await asyncio.gather(
        db.scraper_run_logs.find(_q_news).sort("ran_at", -1).to_list(1),
        db.scraper_run_logs.find(_q_prop).sort("ran_at", -1).to_list(1),
        db.scraper_run_logs.count_documents(_q_news),
        db.scraper_run_logs.count_documents({**_q_news, "status": "success"}),
        db.scraper_run_logs.count_documents({**_q_news, "status": "error"}),
        db.scraper_run_logs.count_documents(_q_prop),
        db.scraper_run_logs.count_documents({**_q_prop, "status": "success"}),
        db.scraper_run_logs.count_documents({**_q_prop, "status": "error"}),
        db.blog_posts.count_documents({"building_id": building_id, "author_id": "system"}),
        db.listings.count_documents({"building_id": building_id, "category": "property", "user_id": "system"}),
    )

    # Average items per successful run — fetch last 20 successful records for average.
    # Separated from the main gather to avoid always running; only needed if there are runs.
    news_avg, prop_avg = 0, 0
    if news_success_count:
        news_recent_ok = await db.scraper_run_logs.find(
            {**_q_news, "status": "success"}, {"items_count": 1, "_id": 0}
        ).sort("ran_at", -1).to_list(20)
        news_avg = round(sum(r.get("items_count", 0) for r in news_recent_ok) / len(news_recent_ok))
    if prop_success_count:
        prop_recent_ok = await db.scraper_run_logs.find(
            {**_q_prop, "status": "success"}, {"items_count": 1, "_id": 0}
        ).sort("ran_at", -1).to_list(20)
        prop_avg = round(sum(r.get("items_count", 0) for r in prop_recent_ok) / len(prop_recent_ok))

    stats = {
        "news": {
            "total_articles": total_articles,
            "total_runs": news_total_runs,
            "successful_runs": news_success_count,
            "failed_runs": news_fail_count,
            "success_rate": round(news_success_count / news_total_runs * 100) if news_total_runs else 100.0,
            "average_articles": news_avg,
            "last_run": news_last[0]["ran_at"] if news_last else settings_doc["news"].get("last_run"),
            "last_run_count": news_last[0].get("items_count", 0) if news_last else 0,
        },
        "property": {
            "total_listings": total_listings,
            "total_runs": prop_total_runs,
            "successful_runs": prop_success_count,
            "failed_runs": prop_fail_count,
            "success_rate": round(prop_success_count / prop_total_runs * 100) if prop_total_runs else 100.0,
            "average_listings": prop_avg,
            "last_run": prop_last[0]["ran_at"] if prop_last else settings_doc["property"].get("last_run"),
            "last_run_count": prop_last[0].get("items_count", 0) if prop_last else 0,
        },
    }

    return {
        "settings": {
            "news": settings_doc["news"],
            "property": settings_doc["property"],
        },
        "stats": stats,
    }


@api_router.put("/settings/scrapers")
async def update_scraper_settings(
        settings: ScraperSettingsUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update scraper settings. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super admin only")

    # Fetch existing doc WITH _id excluded — avoids "Immutable field: _id" error
    # when the document is passed back to $set.
    existing = await db.scraper_settings.find_one(
        {"building_id": building_id}, {"_id": 0}
    )

    if not existing:
        existing = {"building_id": building_id, "news": {}, "property": {}}

    # Operational fields written by the scraper trigger endpoints — they must be
    # preserved across user-initiated settings saves so last_run / status are
    # not reset to None every time the admin clicks "Save All Settings".
    if settings.news:
        new_news = settings.news.model_dump()
        old_news = existing.get("news") or {}
        for field in _SCRAPER_OPERATIONAL_FIELDS:
            if old_news.get(field) is not None:
                new_news[field] = old_news[field]
        existing["news"] = new_news

        # Mirror config into os.environ so the next *in-process* scraper call
        # picks up the changes (subprocess invocations always re-read .env anyway).
        os.environ["NEWS_SCRAPING_METHOD"] = settings.news.method
        os.environ["MAX_ARTICLES_PER_KEYWORD"] = str(settings.news.max_articles)
        os.environ["MIN_CONTENT_LENGTH"] = str(settings.news.min_content_length)
        os.environ["RELEVANCE_THRESHOLD"] = str(settings.news.relevance_threshold)

    if settings.property:
        new_property = settings.property.model_dump()
        old_property = existing.get("property") or {}
        for field in _SCRAPER_OPERATIONAL_FIELDS:
            if old_property.get(field) is not None:
                new_property[field] = old_property[field]
        existing["property"] = new_property

    # $set with a document that has no _id (projected out above) — safe for upsert.
    await db.scraper_settings.update_one(
        {"building_id": building_id}, {"$set": existing}, upsert=True
    )

    logger.info(f"Scraper settings updated by {current_user['email']}")

    # Pass both required arguments explicitly — get_scraper_settings uses FastAPI
    # dependency injection when called via HTTP, but here we call it directly so
    # we must supply both positional keyword arguments ourselves.
    return await get_scraper_settings(
        current_user=current_user, building_id=building_id
    )


@api_router.get("/settings/scrapers/{scraper}/logs")
async def get_scraper_logs(
        scraper: str,
        limit: int = Query(50, ge=1, le=500),
        current_user: dict = Depends(get_current_user),
):
    """Get scraper execution logs"""
    permissions = get_user_permissions(current_user)
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Super admin only")

    if scraper not in ["news", "property"]:
        raise HTTPException(status_code=400, detail="Invalid scraper type")

    # Read log file
    log_file = ROOT_DIR / "logs" / f"{scraper}_scraper.log"

    if not log_file.exists():
        return {"logs": [], "timestamp": datetime.now(timezone.utc).isoformat()}

    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
            raw_lines = [line.strip() for line in lines[-limit:] if line.strip()]

        # Parse log lines into structured {timestamp, level, message} objects.
        # Python logging format: "2026-04-03 10:25:31,000 - logger_name - LEVEL - message"
        # Section separators ("--- Run at ... ---") are emitted as INFO metadata lines.
        _LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        structured_logs = []
        for line in raw_lines:
            # Treat our own run-separator lines as metadata, not log records.
            if line.startswith("---") or line.startswith("==="):
                structured_logs.append({"timestamp": "", "level": "META", "message": line})
                continue

            parts = line.split(" - ", 3)
            # Standard 4-part format: timestamp - logger_name - LEVEL - message
            if len(parts) == 4 and parts[2].strip() in _LEVELS:
                structured_logs.append({
                    "timestamp": parts[0].strip(),
                    "level": parts[2].strip(),
                    "message": parts[3].strip(),
                })
            # 3-part format (no logger name): timestamp - LEVEL - message
            elif len(parts) == 3 and parts[1].strip() in _LEVELS:
                structured_logs.append({
                    "timestamp": parts[0].strip(),
                    "level": parts[1].strip(),
                    "message": parts[2].strip(),
                })
            else:
                # Unrecognised format — emit as-is with no timestamp.
                structured_logs.append({"timestamp": "", "level": "INFO", "message": line})
        logs = structured_logs
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        logs = [{"timestamp": "", "level": "ERROR", "message": f"Error reading log file: {str(e)}"}]

    return {"logs": logs, "timestamp": datetime.now(timezone.utc).isoformat()}


# ==================== STATISTICS ====================


@api_router.get("/stats/dashboard")
async def get_dashboard_stats(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        financial_year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get summarized stats for the main dashboard. Scoped to building."""
    permissions = get_user_permissions(current_user)

    stats = {}

    # Performance Optimization⚡: Parallelize independent database counts to reduce latency.
    now = datetime.now(timezone.utc).isoformat()
    tasks = {
        "active_listings": db.listings.count_documents({"building_id": building_id, "status": "active"}),
        "active_announcements": db.announcements.count_documents(
            {"building_id": building_id, "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}]}
        ),
    }

    if permissions.can_view_documents:
        tasks["total_documents"] = db.documents.count_documents({"building_id": building_id})

    if permissions.can_view_meetings:
        tasks["pending_todos"] = db.todos.count_documents({"building_id": building_id, "status": "pending"})
        tasks["upcoming_meetings"] = db.meetings.count_documents(
            {"building_id": building_id, "status": "scheduled"}
        )

    # Performance Optimization⚡: Identify the year and membership list in parallel before counting.
    # This reduces sequential database round-trips by hoisting I/O-bound tasks into a single block.
    init_tasks = []
    init_keys = []
    if permissions.can_manage_users:
        init_tasks.append(db.memberships.find({"building_id": building_id}).to_list(1000))
        init_keys.append("user_ids")
    if permissions.can_view_finances:
        from utils.finance_helpers import get_latest_ledger_year
        init_tasks.append(get_latest_ledger_year(building_id))
        init_keys.append("finance_year")

    init_results = await asyncio.gather(*init_tasks)
    init_data = dict(zip(init_keys, init_results))

    if permissions.can_manage_users:
        user_ids = [m["user_id"] for m in init_data.get("user_ids", [])]
        tasks["total_users"] = db.users.count_documents({"id": {"$in": user_ids}, "status": {"$ne": "archived"}})
        tasks["active_users"] = db.users.count_documents(
            {"id": {"$in": user_ids}, "is_active": True, "status": {"$ne": "archived"}}
        )

    # Stage 2: Parallelize all counts and finance stats
    if permissions.can_view_finances:
        from utils.finance_helpers import get_unit_ledger_stats
        year = init_data.get("finance_year") or "2025"
        tasks["finance_stats"] = get_unit_ledger_stats(year, building_id)

    keys = list(tasks.keys())
    results = await asyncio.gather(*tasks.values())
    for i, key in enumerate(keys):
        if key == "finance_stats":
            ls = results[i]
            stats["finance_balance"] = round(ls.get("total_paid", 0), 2)
            stats["levy_outstanding"] = round(ls.get("total_outstanding", 0), 2)
        else:
            stats[key] = results[i]

    if permissions.can_view_finances and "finance_balance" not in stats:
        stats["finance_balance"] = 0

    return stats


@api_router.get("/stats/building-kpis")
async def get_building_kpis(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        financial_year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get building-wide KPIs for EC and Manager dashboards. Scoped to building."""
    permissions = get_user_permissions(current_user)

    # Restrict to EC members, super admins, and strata managers
    # Use _effective_role() so temporarily-elevated owners (e.g. elevated to ec_member) pass this guard.
    if _effective_role(current_user) not in [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
    ]:
        raise HTTPException(status_code=403, detail="Access denied - EC members only")

    kpis = {}

    try:
        from utils.finance_helpers import (
            get_latest_levy_year,
            get_latest_ledger_year,
            get_unit_ledger_stats,
            get_arrears_metrics,
            get_arrears_unit_count,
            get_collection_rate_metrics,
            compute_period_due_dates,
        )
        from datetime import date as _date_cls, timedelta as _timedelta

        # Stage 1: Get latest year info and unit count in parallel
        if financial_year:
            year = financial_year
            total_units_task = db.units.count_documents({"building_id": building_id})
            total_units = await total_units_task
            # Verify data exists for requested year; fall back to latest available if not
            has_data = await db.annual_levies.find_one(
                {"year": year, "building_id": building_id}, {"_id": 1}
            )
            if not has_data:
                fallback_year = await get_latest_levy_year(building_id) or await get_latest_ledger_year(building_id)
                if fallback_year:
                    year = fallback_year
        else:
            ledger_year_task = get_latest_ledger_year(building_id)
            levy_year_task = get_latest_levy_year(building_id)
            total_units_task = db.units.count_documents({"building_id": building_id})

            ledger_year, levy_year, total_units = await asyncio.gather(
                ledger_year_task, levy_year_task, total_units_task
            )
            # Prefer levy_year (latest annual_levy = current FY) over ledger_year
            year = levy_year or ledger_year or "2025"

        # Stage 2: Fetch financial stats, fund balances, confirmed payments, settings, and expenses in parallel
        # Performance Optimization⚡: Collapse Stage 2 and Stage 3 into a single parallel block.
        # This reduces sequential database round-trips from O(3) to O(1) concurrent sets.
        ls_task = get_unit_ledger_stats(year, building_id)
        levy_doc_task = db.annual_levies.find_one({"year": year, "building_id": building_id})
        settings_task = _get_general_settings(building_id, {"_id": 0})
        confirmed_agg_task = _server_agg(db.levy_payments, [
            {"$match": {"building_id": building_id, "year": year, "status": "confirmed"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ], 1)
        admin_exp_task = _server_agg(db.expense_transactions, [
            {"$match": {"building_id": building_id, "financial_year": year, "fund_type_short": "admin"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ], 1)
        sinking_exp_task = _server_agg(db.expense_transactions, [
            {"$match": {"building_id": building_id, "financial_year": year, "fund_type_short": "sinking"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ], 1)

        ls, levy_doc, settings_doc, confirmed_agg, admin_exp_agg, sinking_exp_agg = await asyncio.gather(
            ls_task, levy_doc_task, settings_task, confirmed_agg_task, admin_exp_task, sinking_exp_task
        )

        total_levied = ls.get("total_levied", 0)
        # METRIC[total_opening_arrears]: sourced from get_unit_ledger_stats() → unit_levy_ledger aggregate.
        # Aggregation uses $admin_opening + $sinking_opening (filter > 0.01).
        # finance.py uses $opening_arrears field directly — both should agree for well-formed data.
        total_opening_arrears = ls.get("total_opening_arrears", 0)
        admin_bal = 0.0
        sinking_bal = 0.0
        admin_levy_annual = 0.0
        sinking_levy_annual = 0.0
        admin_opening_balance = 0.0
        sinking_opening_balance = 0.0
        if levy_doc:
            admin_bal = levy_doc.get("admin_fund", {}).get("closing_balance", 0)
            sinking_bal = levy_doc.get("sinking_fund", {}).get("closing_balance", 0)
            admin_levy_annual = levy_doc.get("admin_fund", {}).get(
                "total_income", 0
            ) or levy_doc.get("admin_levy_total", 0)
            sinking_levy_annual = levy_doc.get("sinking_fund", {}).get(
                "total_income", 0
            ) or levy_doc.get("sinking_levy_total", 0)
            admin_opening_balance = levy_doc.get("admin_fund", {}).get(
                "opening_balance", 0
            )
            sinking_opening_balance = levy_doc.get("sinking_fund", {}).get(
                "opening_balance", 0
            )

        # Authoritative source: unit_levy_ledger.total_paid (covers DEFT, bank, all channels).
        # levy_payments tracks portal-only payments — never used as primary total.
        ledger_total_paid = ls.get("total_paid", 0.0)
        portal_total_paid = confirmed_agg[0]["total"] if confirmed_agg else 0.0  # reference only
        confirmed_paid = ledger_total_paid

        # ── Live fund balances (computed from database transactions) ─────────
        # Formula: opening_balance + YTD levy income received − YTD expenses paid
        # All three components come from the database — fully reconcilable.
        #
        # Income:   unit_levy_ledger.admin_paid / sinking_paid (direct per-fund split,
        #           covers DEFT, BPAY, and portal — set by get_unit_ledger_stats).
        # Expenses: expense_transactions aggregated by fund_type_short + financial_year.
        # Opening:  annual_levies.admin_fund.opening_balance (= FY2025 closing, verified).
        #
        # Strata Mgmt system current_balance (annual_levies.xxx_fund.current_balance) is kept
        # as a cross-check field only; may be stale and includes interest income not yet
        # in our transaction data (~$2,756 gap expected = ~3 months interest at 4.5%).

        admin_ytd_income = ls.get("admin_paid", 0.0)
        sinking_ytd_income = ls.get("sinking_paid", 0.0)

        # YTD expenses are now pre-fetched in the Stage 2 parallel block above - Bolt ⚡
        admin_exp_ytd = admin_exp_agg[0]["total"] if admin_exp_agg else 0.0
        sinking_exp_ytd = sinking_exp_agg[0]["total"] if sinking_exp_agg else 0.0

        admin_fund_live_balance = round(admin_opening_balance + admin_ytd_income - admin_exp_ytd, 2)
        sinking_fund_live_balance = round(sinking_opening_balance + sinking_ytd_income - sinking_exp_ytd, 2)

        # Determine how many payment periods have passed their grace deadline
        grace_days = (
            int(settings_doc.get("grace_period_days", 14)) if settings_doc else 14
        )
        due_months = (
            settings_doc.get("levy_due_months", [3, 6, 9, 12])
            if settings_doc
            else [3, 6, 9, 12]
        )
        due_day_type = (
            settings_doc.get("levy_due_day_type", "first") if settings_doc else "first"
        )
        due_day = settings_doc.get("levy_due_day") if settings_doc else None
        custom_dates = (
            settings_doc.get("levy_due_custom_dates", {}) if settings_doc else {}
        )
        total_periods = len(due_months) if due_months else 4

        try:
            levy_year_int = int(year)
        except (ValueError, TypeError):
            levy_year_int = _date_cls.today().year

        computed_dates = compute_period_due_dates(
            levy_year_int,
            due_months,
            due_day_type,
            due_day,
            total_periods,
            custom_dates,
        )
        today = _date_cls.today()
        num_overdue = sum(
            1
            for d_str in computed_dates
            if _date_cls.fromisoformat(d_str) + _timedelta(days=grace_days) < today
        )
        in_grace_count = sum(
            1
            for d_str in computed_dates
            if _date_cls.fromisoformat(d_str) < today <= _date_cls.fromisoformat(d_str) + _timedelta(days=grace_days)
        )

        # ── Historical vs Current Year ────────────────────────────────────────
        # For completed years, confirmed_paid=0 in levy_payments (DEFT/BPAY external payers).
        # The net-position formula breaks for historical years — any formula using confirmed_paid
        # will show the entire year's levy as arrears (e.g. FY2025 showed -100% / $482K).
        # Fix: use next year's opening_arrears (= this year's unpaid carry-forward) instead.
        is_historical_year = levy_year_int < today.year

        if is_historical_year:
            # Completed year: next year's opening_arrears = unpaid amount from this year.
            # This is immune to DEFT/BPAY payment gap — carry-forward is set externally.
            next_year = str(levy_year_int + 1)
            next_ls = await get_unit_ledger_stats(next_year, building_id)
            closing_arrears = next_ls.get("total_opening_arrears", 0.0)
            total_arrears = closing_arrears
            # Count units by checking NEXT year's opening_balance > 0.01 (num_overdue=0 branch).
            # subtract_payments=False: next-year opening is a fixed historical balance — we don't
            # want to reduce it by current-year payments (those are for the new year, not the old one).
            units_in_arrears = await get_arrears_unit_count(
                next_year, 0, building_id, total_periods=total_periods, subtract_payments=False
            )
            # levy.collection.historical_year.v1 — see domain/finance/formulas/collection.py.
            # Clamped to [0, 100] — closing_arrears can exceed total_levied when carry-forward
            # arrears pre-date the ledger or were imported with different GST conventions.
            from services.finance_metrics.facade import get_historical_year_collection_rate
            collection_rate = float(get_historical_year_collection_rate(
                building_id=building_id,
                financial_year=year,
                levied_dollars=total_levied,
                closing_arrears_dollars=closing_arrears,
            ).value)
            net_position = total_levied - closing_arrears
        else:
            # Current year: "as-of-today" formula — what fraction of full-year obligations
            # (opening arrears + annual levy) has been confirmed in-platform.
            # This gives a 0–100% figure that never goes negative.
            if total_periods > 0:
                obligations_so_far = (
                        total_opening_arrears + (num_overdue / total_periods) * total_levied
                )
            else:
                obligations_so_far = total_opening_arrears
            net_position = (
                    confirmed_paid - obligations_so_far
            )  # kept for total_arrears reference
            # METRIC[total_obligations]: shared denominator — MUST match finance.py get_building_fund_overview.
            # total_levied is stored as the ANNUAL commitment (full year).
            # For mid-year collection rate we use the proportion already levied (due date reached).
            # num_periods_due counts quarters whose due date has passed (irrespective of grace).
            # If no computed_dates, assume all periods are due (conservative fallback).
            num_periods_due = (
                sum(1 for d_str in computed_dates if _date_cls.fromisoformat(d_str) <= today)
                if computed_dates else total_periods
            )
            # levy.collection.current_year.v1 — see domain/finance/formulas/collection.py.
            # net_balance-derived — always current (updated by bridge/CSV/portal).
            # confirmed_paid (total_paid) is stale for bridge-synced units; do NOT use as numerator.
            # net_collected = total_obligations - total_outstanding (what has been cleared via any channel).
            # Denominator = full annual levy + opening arrears — MUST match fund_health in finance.py.
            # Enforcement: tests/backend/test_metric_consistency.py::TestFundHealthVsCollectionRate
            from domain.finance.formulas.collection import current_year_collection_rate as _ccr
            from services.finance_metrics.mongo_adapter import dollars_to_cents as _d2c
            _collection_result = _ccr(
                opening_arrears_cents=_d2c(total_opening_arrears),
                levied_cents=_d2c(total_levied),
                outstanding_cents=_d2c(ls.get("total_outstanding", 0)),
            )
            total_obligations = round(_collection_result.total_obligations_cents / 100, 2)
            net_collected = round(_collection_result.net_collected_cents / 100, 2)
            collection_rate = float(_collection_result.collection_rate_pct)
            # 2026-08-03: previously raw ls.get("total_outstanding")/("units_owing") —
            # any positive net_balance, no grace-deadline awareness at all. Now routed
            # through the same canonical, grace-aware get_arrears_metrics() used by
            # /finance/summary, /finance/kpi-contract, /finance/levy-kpi, and
            # /arrears/detail, so this endpoint (feeding ArrearsRecoveryPage's
            # comparison card, ECDashboard, FinanceIntelligencePage, ManagementDashboard)
            # agrees with all four by construction instead of independently drifting.
            _arrears_metrics = await get_arrears_metrics(
                year, num_overdue, building_id, total_periods,
                subtract_payments=True, in_grace_periods=in_grace_count,
            )
            total_arrears = _arrears_metrics["total_amount"]
            units_in_arrears = _arrears_metrics["unit_count"]

        arrears_percentage = (
            (units_in_arrears / total_units * 100) if total_units > 0 else 0
        )

        # For historical years, total_obligations = total_levied (the year's full liability).
        if is_historical_year:
            total_obligations = round(total_levied, 2)

        # Pending approvals count for dashboard badge
        pending_approvals_count = await db.work_order_invoices.count_documents(
            {"building_id": building_id, "approval_status": "submitted"}
        )

        # Canonical due-date Collection Rate (GAP-FIN-035) — the ONLY figure that may be labelled
        # "Collection Rate" (numerator/denominator both scoped to charges due as-of-today; per-unit
        # clamped). ADDITIVE and non-breaking: the legacy `collection_rate` field below is the
        # full-year fund-health/coverage number and is deliberately left unchanged (its
        # cross-endpoint consistency test, TestFundHealthVsCollectionRate, still holds). Consumers
        # that want the true collection rate should read `due_date_collection_rate_pct`; the
        # inflated coverage number must be labelled "Fund Health", never "Collection Rate".
        try:
            _cr_metrics = await get_collection_rate_metrics(year, building_id)
            due_date_collection_rate_pct = round(float(_cr_metrics.get("collection_rate_pct", 0.0)), 2)
            collected_in_advance = _cr_metrics.get("collected_in_advance", 0.0)
        except Exception as _cr_exc:
            logger.warning(f"due-date collection rate unavailable for {building_id}/{year}: {_cr_exc}")
            due_date_collection_rate_pct = None
            collected_in_advance = None

        return {
            # METRIC[collection_rate]: return — consumed by ECDashboard.jsx,
            # FinanceIntelligencePage.jsx, ManagementDashboard.tsx,
            # CollectionRateDetailDialog.jsx, ArrearsRecoveryPage.jsx.
            #
            # This key now carries the DUE-DATE collection rate (metric 1). It previously
            # carried the full-year coverage figure — the same variable as
            # full_year_coverage_pct below — while every consumer displayed it under the
            # label "Collection Rate". CLAUDE.md is explicit that coverage is "never
            # allowed to be labelled Collection Rate anywhere in a UI or API response",
            # and the consumers are named right here, so renaming the field was not an
            # option: the fix is to make the value match the name.
            #
            # At East Gate the two differ by 0.03pp (96.40 vs 96.43), which is precisely
            # why it survived — the numbers agree until a building pays meaningfully
            # ahead, and then the coverage figure silently overstates collection.
            #
            # Falls back to coverage only when the due-date metric could not be computed,
            # so a dashboard shows a slightly wrong number rather than an empty tile; the
            # warning above records when that happens.
            "collection_rate": (
                due_date_collection_rate_pct
                if due_date_collection_rate_pct is not None
                else round(collection_rate, 2)
            ),
            "due_date_collection_rate_pct": due_date_collection_rate_pct,
            # Metric 2 — full-year coverage / fund health. Denominator is the FULL annual
            # levy plus opening arrears, so it legitimately includes amounts covering
            # instalments that are not yet due.
            "full_year_coverage_pct": round(collection_rate, 2),
            "collected_in_advance": collected_in_advance,
            "arrears_percentage": round(arrears_percentage, 2),
            "pending_approvals_count": pending_approvals_count,
            "units_in_arrears": units_in_arrears,
            "total_units": total_units,
            "total_arrears": round(total_arrears, 2),
            "total_levied": round(total_levied, 2),
            "total_obligations": round(total_obligations, 2),
            "total_paid": round(confirmed_paid, 2),
            "confirmed_paid": round(confirmed_paid, 2),
            "total_opening_arrears": round(total_opening_arrears, 2),
            "net_position": round(net_position, 2),
            "num_overdue_periods": num_overdue,
            "admin_fund_balance": round(admin_bal, 2),
            "sinking_fund_balance": round(sinking_bal, 2),
            "admin_fund_live_balance": admin_fund_live_balance,
            "sinking_fund_live_balance": sinking_fund_live_balance,
            "confirmed_admin_ytd": admin_ytd_income,
            "confirmed_sinking_ytd": sinking_ytd_income,
            "admin_opening_balance": admin_opening_balance,
            "sinking_opening_balance": sinking_opening_balance,
        }

    except Exception as e:
        logger.error(f"Error calculating building KPIs: {e}")
        return {
            "collection_rate": 0,
            "arrears_percentage": 0,
            "units_in_arrears": 0,
            "total_arrears": 0,
            "admin_fund_balance": 0,
            "sinking_fund_balance": 0,
            "admin_fund_live_balance": 0,
            "sinking_fund_live_balance": 0,
        }


@api_router.get("/stats/top-arrears")
async def get_top_arrears(
        limit: int = 10,
        financial_year: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get units with highest arrears for EC and Manager dashboards. Scoped to building."""
    # Restrict to EC members, super admins, and strata managers
    # Use _effective_role() so temporarily-elevated owners pass this guard.
    if _effective_role(current_user) not in [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
    ]:
        raise HTTPException(status_code=403, detail="Access denied - EC members only")

    try:
        from utils.finance_helpers import compute_period_due_dates
        from datetime import date as _date_cls, timedelta as _timedelta

        year_to_query = financial_year or str(datetime.now(timezone.utc).year)

        # Fetch settings for grace period and due dates
        settings_doc = await _get_general_settings(building_id, {"_id": 0})
        grace_days = (
            int(settings_doc.get("grace_period_days", 14)) if settings_doc else 14
        )
        due_months = (
            settings_doc.get("levy_due_months", [3, 6, 9, 12])
            if settings_doc
            else [3, 6, 9, 12]
        )
        due_day_type = (
            settings_doc.get("levy_due_day_type", "first") if settings_doc else "first"
        )
        due_day = settings_doc.get("levy_due_day") if settings_doc else None
        custom_dates = (
            settings_doc.get("levy_due_custom_dates", {}) if settings_doc else {}
        )

        # Use opening_arrears (prior-year carry-forward) to match the Arrears Recovery Board.
        # net_balance = total_levied - total_paid for the current FY — it includes future levies
        # (e.g. Q4 not due until Dec) which are NOT arrears. Using net_balance inflates amounts
        # and causes this chart to disagree with /intelligence/debt-recovery for the same units.
        # opening_arrears is the authoritative delinquency field set at FY start from the
        # prior year's closing balance and updated via DEFT/scraper imports.
        pipeline = [
            {
                "$match": {
                    "building_id": building_id,
                    "year": year_to_query,
                    "opening_arrears": {"$gt": 0.01},
                }
            },
            {
                "$project": {
                    "unit_number": 1,
                    "lot_number": 1,
                    "opening_balance": "$opening_arrears",
                }
            },
            {"$sort": {"opening_balance": -1}},
            {"$limit": limit},
        ]

        aggregated_results = await _server_agg(db.unit_levy_ledger, pipeline, limit)

        # Compute days_overdue from the most recent past-grace deadline of the PRIOR year.
        # opening_arrears is carry-forward from the prior levy year, so the overdue reference
        # is the last grace deadline of that prior year (e.g. Dec 15 2025 for FY2026 ledger).
        try:
            prior_year = int(year_to_query) - 1
            prior_dates = compute_period_due_dates(
                prior_year,
                due_months,
                due_day_type,
                due_day,
                len(due_months),
                custom_dates,
            )
            today = _date_cls.today()
            prior_grace_deadlines = sorted([
                _date_cls.fromisoformat(d) + _timedelta(days=grace_days)
                for d in prior_dates
            ])
            past_grace = [g for g in prior_grace_deadlines if today > g]
            if past_grace:
                days_overdue_base = (today - past_grace[-1]).days
            else:
                days_overdue_base = 0
        except Exception:
            days_overdue_base = 0
        months_overdue_base = max(0, round(days_overdue_base / 30))

        # Enrich with owner info via canonical chain: user_units → users → units.owner_name fallback
        # Use bulk lookup to avoid N+1 query pattern across all arrears units.
        _all_owners = await _get_all_unit_owners(building_id)
        units_map: dict[str, str] = {
            unit_num: (_all_owners.get(unit_num) or {}).get("owner_name", "Unknown")
            for unit_num in (r.get("unit_number") for r in aggregated_results if r.get("unit_number"))
        }

        now = datetime.now(timezone.utc)
        arrears_list = []
        for res in aggregated_results:
            balance = round(res.get("opening_balance", 0), 2)
            unit_num = res.get("unit_number", "")
            arrears_list.append(
                {
                    "unit": unit_num,
                    "lot_number": res.get("lot_number", ""),
                    "owner": units_map.get(unit_num, "Unknown"),
                    "amount": balance,
                    "months": months_overdue_base,
                    "days_overdue": days_overdue_base,
                    "last_contacted": None,  # Only set when a real contact event is recorded
                }
            )

        arrears_list.sort(key=lambda x: x["amount"], reverse=True)
        return arrears_list[:limit]

    except Exception as e:
        logger.error(f"Error fetching top arrears: {e}")
        return []


# ==================== INSURANCE CLAIM ROUTES ====================


@api_router.post("/requests/insurance-claims", response_model=InsuranceClaimResponse)
async def create_insurance_claim(
        data: InsuranceClaimCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new insurance claim. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "insurance-claim", version=1, stage="submission", db=db)
    claim_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    claim_doc = {
        "id": claim_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "submitted",
        "submitted_by": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "claim_number": f"CLM-{claim_id[:8].upper()}",
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "notes": [],
        "created_at": now,
        "updated_at": now,
    }

    await db.insurance_claims.insert_one(claim_doc)
    return InsuranceClaimResponse(**claim_doc)


@api_router.get(
    "/requests/insurance-claims", response_model=List[InsuranceClaimResponse]
)
async def get_insurance_claims(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List insurance claims for the building."""
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id}
    if not permissions.can_manage_requests:
        query["submitted_by"] = current_user["id"]

    if status:
        query["status"] = status

    claims = (
        await db.insurance_claims.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )
    return [InsuranceClaimResponse(**c) for c in claims]


@api_router.put("/requests/insurance-claims/{claim_id}/status")
async def update_insurance_claim_status(
        claim_id: str,
        status: str,
        notes: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update insurance claim status. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "insurance-claim", version=1, stage="review", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": status,
        "reviewed_by": current_user["id"],
        "reviewed_by_name": current_user["full_name"],
        "reviewed_at": now,
        "updated_at": now,
    }

    result = await db.insurance_claims.update_one({"id": claim_id, "building_id": building_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Claim not found")

    if notes:
        note_entry = {"by": current_user["full_name"], "at": now, "content": notes}
        await db.insurance_claims.update_one(
            {"id": claim_id, "building_id": building_id}, {"$push": {"notes": note_entry}}
        )

    return {"message": f"Insurance claim {status}"}


# ==================== INSURANCE ENQUIRY ROUTES ====================


@api_router.post(
    "/requests/insurance-enquiries", response_model=InsuranceEnquiryResponse
)
async def create_insurance_enquiry(
        data: InsuranceEnquiryCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new insurance enquiry. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "insurance-enquiry", version=1, stage="submission", db=db)
    enquiry_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    enquiry_doc = {
        "id": enquiry_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "pending",
        "submitted_by": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "answer": None,
        "answered_by": None,
        "answered_by_name": None,
        "answered_at": None,
        "created_at": now,
        "updated_at": now,
    }

    await db.insurance_enquiries.insert_one(enquiry_doc)
    return InsuranceEnquiryResponse(**enquiry_doc)


@api_router.get(
    "/requests/insurance-enquiries", response_model=List[InsuranceEnquiryResponse]
)
async def get_insurance_enquiries(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List insurance enquiries for the building."""
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id}
    if not permissions.can_manage_requests:
        query["submitted_by"] = current_user["id"]

    if status:
        query["status"] = status

    enquiries = (
        await db.insurance_enquiries.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )
    return [InsuranceEnquiryResponse(**e) for e in enquiries]


@api_router.put("/requests/insurance-enquiries/{enquiry_id}/answer")
async def answer_insurance_enquiry(
        enquiry_id: str,
        answer: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Answer insurance enquiry. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "insurance-enquiry", version=1, stage="review", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": "answered",
        "answer": answer,
        "answered_by": current_user["id"],
        "answered_by_name": current_user["full_name"],
        "answered_at": now,
        "updated_at": now,
    }

    result = await db.insurance_enquiries.update_one(
        {"id": enquiry_id, "building_id": building_id}, {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Enquiry not found")

    return {"message": "Enquiry answered successfully"}


# ==================== PET REQUEST ROUTES ====================


@api_router.post("/requests/pets", response_model=PetRequestResponse)
async def create_pet_request(
        data: PetRequestCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new pet request. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "pet", version=1, stage="submission", db=db)
    pet_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    pet_doc = {
        "id": pet_id,
        "building_id": building_id,
        **data.model_dump(exclude={"reason"}),
        "description": data.get_description(),
        "status": "pending",
        "conditions": None,
        "submitted_by": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "unit_number": current_user.get("unit_number", ""),
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "notes": [],
        "created_at": now,
        "updated_at": now,
    }

    await db.pet_requests.insert_one(pet_doc)

    # Create audit log
    asyncio.create_task(
        create_audit_log(
            action="created",
            resource_type="pet_request",
            resource_id=pet_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"pet_name": data.pet_name, "pet_type": data.pet_type},
            building_id=building_id
        )
    )

    return PetRequestResponse(**pet_doc)


@api_router.get("/requests/pets", response_model=List[PetRequestResponse])
async def get_pet_requests(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List pet requests for the building."""
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id}
    if not permissions.can_manage_requests:
        query["submitted_by"] = current_user["id"]

    if status:
        query["status"] = status

    pets = (
        await db.pet_requests.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )
    return [PetRequestResponse(**p) for p in pets]


@api_router.put("/requests/pets/{pet_id}/status")
async def update_pet_request_status(
        pet_id: str,
        status: str,
        conditions: Optional[str] = None,
        notes: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update pet request status. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "pet", version=1, stage="review", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": status,
        "reviewed_by": current_user["id"],
        "reviewed_by_name": current_user["full_name"],
        "reviewed_at": now,
        "updated_at": now,
    }

    if conditions:
        update_data["conditions"] = conditions

    result = await db.pet_requests.update_one({"id": pet_id, "building_id": building_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Pet request not found")

    # Create audit log
    asyncio.create_task(
        create_audit_log(
            action="status_updated",
            resource_type="pet_request",
            resource_id=pet_id,
            user_id=current_user["id"],
            user_name=current_user["full_name"],
            details={"status": status, "notes": notes},
        )
    )

    # Notify user
    pet_req = await db.pet_requests.find_one({"id": pet_id})
    if pet_req:
        asyncio.create_task(
            create_user_notification(
                user_id=pet_req["submitted_by"],
                title="Pet Request Updated",
                message=f"Your request for '{pet_req['pet_name']}' is now {status}",
                notification_type="pet_request",
                link="/requests",
            )
        )

    if notes:
        note_entry = {"by": current_user["full_name"], "at": now, "content": notes}
        await db.pet_requests.update_one(
            {"id": pet_id}, {"$push": {"notes": note_entry}}
        )

    return {"message": f"Pet request {status}"}


# ==================== ACCESS CONTROL REQUEST ROUTES ====================


@api_router.post(
    "/requests/access-control", response_model=AccessControlRequestResponse
)
async def create_access_control_request(
        data: AccessControlRequestCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new access control request. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "access-control", version=1, stage="submission", db=db)
    request_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    from services import access_lifecycle_service

    device_type = await access_lifecycle_service.get_requestable_device_type_by_key(
        building_id=building_id,
        current_user=current_user,
        type_key=data.access_type,
    )
    if data.quantity > device_type["max_quantity"]:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum quantity for {device_type['name']} is {device_type['max_quantity']}.",
        )
    estimated_cost = ((device_type["fee_cents"] + device_type["deposit_cents"]) * data.quantity) / 100

    request_doc = {
        "id": request_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "pending",
        "cost": estimated_cost,
        "device_type_id": device_type["device_type_id"],
        "device_name": device_type["name"],
        "fee_cents": device_type["fee_cents"],
        "deposit_cents": device_type["deposit_cents"],
        "estimated_total_cents": int(round(estimated_cost * 100)),
        "submitted_by": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "unit_number": current_user.get("unit_number", ""),
        "approved_by": None,
        "approved_by_name": None,
        "approved_at": None,
        "issued_at": None,
        "created_at": now,
        "updated_at": now,
    }

    await db.access_control_requests.insert_one(request_doc)
    return AccessControlRequestResponse(**request_doc)


@api_router.get(
    "/requests/access-control", response_model=List[AccessControlRequestResponse]
)
async def get_access_control_requests(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List access control requests for the building."""
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id}
    if not permissions.can_manage_requests:
        query["submitted_by"] = current_user["id"]

    if status:
        query["status"] = status

    requests = (
        await db.access_control_requests.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )
    return [AccessControlRequestResponse(**r) for r in requests]


@api_router.put("/requests/access-control/{request_id}/status")
async def update_access_control_status(
        request_id: str,
        status: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update access control request status. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "access-control", version=1, stage="fulfilment", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": status,
        "approved_by": current_user["id"],
        "approved_by_name": current_user["full_name"],
        "approved_at": now,
        "updated_at": now,
    }

    if status == "issued":
        update_data["issued_at"] = now

    result = await db.access_control_requests.update_one(
        {"id": request_id, "building_id": building_id}, {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")

    return {"message": f"Access control request {status}"}


# ==================== ALTERATION REQUEST ROUTES ====================


@api_router.post("/requests/alterations", response_model=AlterationRequestResponse)
async def create_alteration_request(
        data: AlterationRequestCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new alteration request. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "alterations", version=1, stage="submission", db=db)
    alteration_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    alteration_doc = {
        "id": alteration_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "pending",
        "conditions": None,
        "submitted_by": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "unit_number": current_user.get("unit_number", ""),
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "approval_notes": [],
        "created_at": now,
        "updated_at": now,
    }

    await db.alteration_requests.insert_one(alteration_doc)
    return AlterationRequestResponse(**alteration_doc)


@api_router.get("/requests/alterations", response_model=List[AlterationRequestResponse])
async def get_alteration_requests(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List alteration requests for the building."""
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id}
    if not permissions.can_manage_requests:
        query["submitted_by"] = current_user["id"]

    if status:
        query["status"] = status

    alterations = (
        await db.alteration_requests.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )
    return [AlterationRequestResponse(**a) for a in alterations]


@api_router.put("/requests/alterations/{alteration_id}/status")
async def update_alteration_status(
        alteration_id: str,
        status: str,
        conditions: Optional[str] = None,
        notes: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update alteration request status. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "alterations", version=1, stage="review", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": status,
        "reviewed_by": current_user["id"],
        "reviewed_by_name": current_user["full_name"],
        "reviewed_at": now,
        "updated_at": now,
    }

    if conditions:
        update_data["conditions"] = conditions

    result = await db.alteration_requests.update_one(
        {"id": alteration_id, "building_id": building_id}, {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")

    if notes:
        note_entry = {"by": current_user["full_name"], "at": now, "content": notes}
        await db.alteration_requests.update_one(
            {"id": alteration_id, "building_id": building_id}, {"$push": {"approval_notes": note_entry}}
        )

    return {"message": f"Alteration request {status}"}


# ==================== REIMBURSEMENT REQUEST ROUTES ====================


@api_router.post(
    "/requests/reimbursements", response_model=ReimbursementRequestResponse
)
async def create_reimbursement_request(
        data: ReimbursementRequestCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new reimbursement request. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "reimbursement", version=1, stage="submission", db=db)
    reimbursement_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    reimbursement_doc = {
        "id": reimbursement_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": "pending",
        "submitted_by": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "unit_number": current_user.get("unit_number", ""),
        "approved_by": None,
        "approved_by_name": None,
        "approved_at": None,
        "paid_at": None,
        "payment_reference": None,
        "approval_notes": [],
        "created_at": now,
        "updated_at": now,
    }

    await db.reimbursement_requests.insert_one(reimbursement_doc)
    return ReimbursementRequestResponse(**reimbursement_doc)


@api_router.get(
    "/requests/reimbursements", response_model=List[ReimbursementRequestResponse]
)
async def get_reimbursement_requests(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List reimbursement requests for the building."""
    permissions = get_user_permissions(current_user)

    query = {"building_id": building_id}
    if not permissions.can_manage_requests:
        query["submitted_by"] = current_user["id"]

    if status:
        query["status"] = status

    reimbursements = (
        await db.reimbursement_requests.find(query, {"_id": 0})
        .sort("created_at", -1)
        .to_list(100)
    )

    # Mask PII during impersonation
    is_impersonated = "impersonator_id" in current_user
    if is_impersonated:
        for r in reimbursements:
            r["submitted_by_name"] = "Resident"
            if r.get("bank_account_name"):
                r["bank_account_name"] = "REDACTED"
            if r.get("bank_bsb"):
                r["bank_bsb"] = "***-***"
            if r.get("bank_account_number"):
                r["bank_account_number"] = "********"

    return [ReimbursementRequestResponse(**r) for r in reimbursements]


@api_router.put("/requests/reimbursements/{reimbursement_id}/approve")
async def approve_reimbursement(
        reimbursement_id: str,
        notes: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Approve reimbursement request. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "reimbursement", version=1, stage="review", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": "approved",
        "approved_by": current_user["id"],
        "approved_by_name": current_user["full_name"],
        "approved_at": now,
        "updated_at": now,
    }

    result = await db.reimbursement_requests.update_one(
        {"id": reimbursement_id, "building_id": building_id}, {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")

    if notes:
        note_entry = {"by": current_user["full_name"], "at": now, "content": notes}
        await db.reimbursement_requests.update_one(
            {"id": reimbursement_id, "building_id": building_id}, {"$push": {"approval_notes": note_entry}}
        )

    return {"message": "Reimbursement approved successfully"}


@api_router.put("/requests/reimbursements/{reimbursement_id}/pay")
async def mark_reimbursement_paid(
        reimbursement_id: str,
        payment_reference: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Mark reimbursement request as paid. Scoped to building."""
    await enforce_request_policy(current_user, building_id, "reimbursement", version=1, stage="fulfilment", db=db)
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_finances:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": "paid",
        "paid_at": now,
        "payment_reference": payment_reference,
        "updated_at": now,
    }

    result = await db.reimbursement_requests.update_one(
        {"id": reimbursement_id, "building_id": building_id}, {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Request not found")

    return {"message": "Reimbursement marked as paid"}


# ==================== ABN VALIDATION ====================


def validate_abn_checksum(abn: str) -> bool:
    """Validate ABN using the official checksum algorithm"""
    abn = abn.replace(" ", "")
    if len(abn) != 11 or not abn.isdigit():
        return False

    weights = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    digits = [int(d) for d in abn]
    digits[0] -= 1  # Subtract 1 from first digit

    total = sum(d * w for d, w in zip(digits, weights))
    return total % 89 == 0


@api_router.get("/abn/validate/{abn}")
async def validate_abn(abn: str, current_user: dict = Depends(get_current_user)):
    """Validate an ABN and lookup business details from ABR"""
    import httpx

    abn_clean = abn.replace(" ", "")

    # First check checksum locally
    if not validate_abn_checksum(abn_clean):
        return {"valid": False, "error": "Invalid ABN checksum", "abn": abn_clean}

    # Lookup from ABR (free public API)
    try:
        # ABR provides a JSON endpoint
        url = f"https://abr.business.gov.au/json/AbnDetails.aspx?abn={abn_clean}&callback=callback"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10)

            if response.status_code == 200:
                # Parse JSONP response
                text = response.text
                if text.startswith("callback("):
                    import json

                    json_str = text[9:-1]  # Remove callback( and )
                    data = json.loads(json_str)

                    if data.get("Abn"):
                        return {
                            "valid": True,
                            "abn": data.get("Abn"),
                            "entity_name": data.get("EntityName", ""),
                            "entity_type": data.get("EntityTypeName", ""),
                            "status": data.get("AbnStatus", ""),
                            "gst_registered": data.get("Gst", "") != "",
                            "business_name": data.get("BusinessName", []),
                            "state": data.get("AddressState", ""),
                            "postcode": data.get("AddressPostcode", ""),
                        }
                    else:
                        return {
                            "valid": False,
                            "error": "ABN not found in ABR",
                            "abn": abn_clean,
                        }

    except Exception as e:
        logging.error(f"ABN lookup error: {e}")
        # Fall back to checksum validation only
        return {
            "valid": True,
            "abn": abn_clean,
            "entity_name": "Unable to verify - checksum valid",
            "note": "ABR lookup temporarily unavailable",
        }

    return {"valid": False, "error": "ABN lookup failed", "abn": abn_clean}


# ==================== EMAIL SETTINGS ROUTES ====================


@api_router.get("/email-settings")
async def get_email_settings_api(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get building-specific email settings."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    settings = await db.email_settings.find_one({"building_id": building_id}, {"_id": 0})
    if not settings:
        # Fallback to global defaults if no building settings exist
        settings = await get_email_settings()
    # Mask sensitive data
    if settings.get("resend_api_key"):
        settings["resend_api_key"] = (
            settings["resend_api_key"][:8] + "..."
            if len(settings["resend_api_key"]) > 8
            else "***"
        )
    if settings.get("sendgrid_api_key"):
        settings["sendgrid_api_key"] = (
            settings["sendgrid_api_key"][:8] + "..."
            if len(settings["sendgrid_api_key"]) > 8
            else "***"
        )
    if settings.get("smtp_password"):
        settings["smtp_password"] = "***"
    if settings.get("migadu_api_key"):
        key = settings["migadu_api_key"]
        settings["migadu_api_key"] = (key[:6] + "..." if len(key) > 6 else "***")
        settings["migadu_configured"] = True
    else:
        settings["migadu_configured"] = False

    return settings


@api_router.put("/email-settings")
async def update_email_settings_api(
        data: EmailSettingsUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update building-specific email settings."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {k: v for k, v in data.model_dump().items() if v is not None}
    update_dict["building_id"] = building_id

    # GAP-SEC-001: encrypt credential fields before storing
    for _cred in ("smtp_password", "mail_password", "resend_api_key", "sendgrid_api_key", "migadu_api_key"):
        if _cred in update_dict and update_dict[_cred] and not is_encrypted(update_dict[_cred]):
            update_dict[_cred] = encrypt_sensitive(update_dict[_cred])

    await db.email_settings.update_one(
        {"building_id": building_id}, {"$set": update_dict}, upsert=True
    )

    return {"message": "Email settings updated"}


@api_router.post("/email-settings/test")
async def test_email_settings(
        to_email: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: test_email_settings
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    html = """
    <div style="font-family: sans-serif; padding: 20px;">
        <h2>Test Email from StrataOS</h2>
        <p>This is a test email to verify your email configuration is working correctly.</p>
        <p>If you received this, your email settings are configured properly!</p>
    </div>
    """

    result = await send_email_async(to_email, "Test Email - StrataOS", html)
    return result


# ==================== ANNUAL BUDGET ROUTES ====================


# ==================== UNIT ENTITLEMENTS & LEVY CALCULATOR ====================


@api_router.post("/units", response_model=UnitEntitlementResponse)
async def create_unit(
        data: UnitEntitlementCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new unit. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    unit_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    unit_doc = {
        "id": unit_id,
        "building_id": building_id,
        **data.model_dump(),
        "quarterly_levy": 0,
        "annual_levy": 0,
        "created_at": now,
        "updated_at": now,
    }

    await db.units.insert_one(unit_doc)

    # C-1: every manually created unit needs a corresponding strata_owners record.
    # strata_owners is read by: levy ledger views, strata roll, financial reports,
    # and the lot_financial_summary service.  Without this record those views will
    # silently omit the new unit until the next Strata Web scraper run.
    # Phase G: also upsert core.lots so the Postgres lot register is complete —
    # deferred until the core.lots write path is established in Phase G.
    await db.strata_owners.insert_one({
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "unit_number": unit_doc.get("unit_number", ""),
        "lot": unit_doc.get("lot_number"),
        "unit": unit_doc.get("unit_number"),
        "owner_name": unit_doc.get("owner_name", ""),
        "owner_name_b": unit_doc.get("owner_name_b"),
        "uoe": unit_doc.get("entitlement_units", 0),
        "balance": 0.0,
        "status": "CURRENT",
        "user_id": None,
        "updated_at": now,
        "created_at": now,
    })

    return UnitEntitlementResponse(**unit_doc)


@api_router.get("/units", response_model=List[UnitEntitlementResponse])
async def get_units(
        skip: int = 0, limit: int = 200,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Get units with pagination support, scoped to the caller's building."""
    units = (
        await db.units.find({"building_id": building_id}, {"_id": 0})
        .sort("unit_number", 1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )
    result = []
    for u in units:
        try:
            result.append(UnitEntitlementResponse(**u))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Skipping unit %s — missing required fields: %s",
                u.get("unit_number", "<unknown>"), exc
            )
    return result


@api_router.get("/units/available")
async def get_available_units(
        current_user: Optional[dict] = Depends(get_optional_user),
        building_id: str = Depends(get_current_building)
):
    """
    DEPRECATED: Use /units/all instead
    Get ALL units with occupant information (multi-user system). Scoped to building.
    Shows all units - multiple users can register for same unit.
    Occupant details are masked for unauthenticated users.
    """
    # Get all units
    all_units = (
        await db.units.find(
            {"building_id": building_id},
            {
                "_id": 0,
                "lot_number": 1,
                "unit_number": 1,
                "unit_type": 1,
                "entitlement": 1,
            },
        )
        .sort("unit_number", 1)
        .to_list(200)
    )

    # Always fetch approved occupant counts (aggregate only — no PII)
    user_units = await db.user_units.find(
        {"building_id": building_id, "is_active": True}, {"_id": 0, "unit_number": 1, "role_at_unit": 1}
    ).to_list(1000)

    # Build occupant count per unit
    unit_occupants = {}
    for uu in user_units:
        unit_num = uu["unit_number"]
        if unit_num not in unit_occupants:
            unit_occupants[unit_num] = {"count": 0, "roles": []}
        unit_occupants[unit_num]["count"] += 1
        unit_occupants[unit_num]["roles"].append(uu["role_at_unit"])

    # Format response
    units_response = []
    for unit in all_units:
        occupants = unit_occupants.get(unit["unit_number"], {"count": 0, "roles": []})
        units_response.append(
            {
                "lot_number": unit["lot_number"],
                "unit_number": unit["unit_number"],
                "unit_type": unit.get("unit_type", "Unknown"),
                "entitlement": unit.get("entitlement", 0),
                "display_name": f"{unit['lot_number']} - {unit['unit_number']} ({unit.get('unit_type', 'Unknown')})",
                "occupant_count": occupants["count"],
                "occupant_roles": occupants["roles"],
            }
        )

    return units_response


@api_router.get("/units/all")
async def get_all_units_with_occupants(
        current_user: Optional[dict] = Depends(get_optional_user),
        building_id: str = Depends(get_building_or_400)
):
    """
    Get ALL units with occupant information. Scoped to building.
    Aggregate counts (owner/tenant/guest) are always returned — no PII exposed.
    Only approved occupants (user_units.is_active=True) are counted.
    """
    # Fetch units and active occupant relationships in parallel
    all_units_task = db.units.find(
        {"building_id": building_id},
        {"_id": 0, "lot_number": 1, "unit_number": 1, "unit_type": 1, "entitlement": 1},
    ).sort("unit_number", 1).to_list(200)

    user_units_task = db.user_units.find(
        {"building_id": building_id, "is_active": True}, {"_id": 0, "unit_number": 1, "role_at_unit": 1}
    ).to_list(1000)

    all_units, user_units = await asyncio.gather(all_units_task, user_units_task)

    # Build approved occupant counts per unit
    unit_occupants: dict = {}
    for uu in user_units:
        unit_num = uu["unit_number"]
        if unit_num not in unit_occupants:
            unit_occupants[unit_num] = {"count": 0, "owners": 0, "tenants": 0, "guests": 0}
        unit_occupants[unit_num]["count"] += 1
        role = uu.get("role_at_unit", "")
        if role == "owner":
            unit_occupants[unit_num]["owners"] += 1
        elif role == "tenant":
            unit_occupants[unit_num]["tenants"] += 1
        elif role == "guest":
            unit_occupants[unit_num]["guests"] += 1

    units_response = []
    for unit in all_units:
        occ = unit_occupants.get(
            unit["unit_number"], {"count": 0, "owners": 0, "tenants": 0, "guests": 0}
        )
        units_response.append(
            {
                "lot_number": unit["lot_number"],
                "unit_number": unit["unit_number"],
                "unit_type": unit.get("unit_type", "Unknown"),
                "entitlement": unit.get("entitlement", 0),
                "display_name": f"{unit['lot_number']} - {unit['unit_number']} ({unit.get('unit_type', 'Unknown')})",
                "occupant_count": occ["count"],
                "owner_count": occ["owners"],
                "tenant_count": occ["tenants"],
                "guest_count": occ["guests"],
            }
        )

    return units_response


@api_router.get("/units/{unit_number}/occupants")
async def get_unit_occupants(
        unit_number: str,
        current_user: dict = Depends(get_optional_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all current occupants of a specific unit. Scoped to building.
    Shows owners, tenants, and guests currently registered
    Public endpoint - for transparency during registration
    """
    # Performance Optimization⚡: Parallelize unit lookup and user-unit relations fetch to reduce latency.
    unit_task = db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
    user_units_task = db.user_units.find(
        {"building_id": building_id, "unit_number": unit_number, "is_active": True}, {"_id": 0}
    ).to_list(100)

    unit, user_units = await asyncio.gather(unit_task, user_units_task)

    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Performance Optimization⚡: Eliminate N+1 query pattern by batch-fetching all user documents.
    user_ids = list(set(uu["user_id"] for uu in user_units))
    users_list = await db.users.find(
        {"id": {"$in": user_ids}, "email": {"$ne": SYSTEM_HIDDEN_EMAIL}},
        {"_id": 0, "id": 1, "full_name": 1, "email": 1},
    ).to_list(len(user_ids))
    user_map = {u["id"]: u for u in users_list}

    # Get user details for each occupant
    occupants = []
    for uu in user_units:
        user = user_map.get(uu["user_id"])
        if user:
            # SECURITY FIX: Mask email address for unauthorized users to prevent PII disclosure
            is_authorized = False
            if current_user:
                # _effective_role, not the raw field: a temporarily elevated user
                # keeps role="owner" and carries effective_role="ec_member", so the
                # raw read masked emails from exactly the people elevation admits.
                # The duplicate UserRole.EC_MEMBER below was the chairman → ec_member
                # rename (commit 67fbc4a5) collapsing two entries into one.
                if _effective_role(current_user) in [
                    UserRole.SUPER_ADMIN,
                    UserRole.EC_MEMBER,
                    UserRole.STRATA_MANAGER,
                ]:
                    is_authorized = True
                elif current_user["id"] == user["id"]:
                    is_authorized = True

            email = user["email"]
            full_name = user["full_name"]
            if not is_authorized:
                # Aggressive masking for unauthorized requests to prevent unit-to-resident mapping (CWE-359)

                # Mask email: john.doe@example.com -> j***@example.com
                parts = email.split("@")
                if len(parts) == 2:
                    name_part, domain = parts
                    masked_name = name_part[0] + "***" if name_part else "***"
                    email = masked_name + "@" + domain
                else:
                    email = "****"

                # Mask full name: John Doe -> J*** D***
                name_parts = full_name.split()
                masked_name_parts = [p[0] + "***" if p else "***" for p in name_parts]
                full_name = " ".join(masked_name_parts)

            occupants.append(
                {
                    "user_id": user["id"],
                    "full_name": full_name,
                    "email": email,
                    "role_at_unit": uu["role_at_unit"],
                    "start_date": uu["start_date"],
                    "end_date": uu.get("end_date"),
                    "expiration_date": uu.get("expiration_date"),
                    "is_active": uu["is_active"],
                }
            )

    return {"unit": unit, "occupant_count": len(occupants), "occupants": occupants}


# ==================== BY-LAWS ENDPOINTS ====================


@api_router.get("/by-laws/current")
async def get_current_by_laws(building_id: str = Depends(get_building_or_400)):
    """
    Get the current by-laws document. Scoped to building.
    Public endpoint - users must read before acknowledging
    """
    by_laws = await db.by_laws.find_one({"building_id": building_id, "is_current": True}, {"_id": 0})
    if not by_laws:
        raise HTTPException(status_code=404, detail="No current by-laws found")

    return {
        "version": by_laws["version"],
        "content": by_laws["content"],
        "effective_date": by_laws["effective_date"],
        "last_updated": by_laws["last_updated"],
    }


@api_router.post("/by-laws/acknowledge")
async def acknowledge_by_laws(
        by_laws_version: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Acknowledge acceptance of by-laws. Scoped to building.
    Required for tenants and guests before registration approval
    """
    user_id = current_user["id"]
    today = datetime.now(timezone.utc).isoformat()

    # Mark old acknowledgments for this building as not current
    await db.by_laws_acknowledgments.update_many(
        {"user_id": user_id, "building_id": building_id}, {"$set": {"is_current": False}}
    )

    # Create new acknowledgment
    acknowledgment = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "building_id": building_id,
        "by_laws_version": by_laws_version,
        "acknowledged_date": today,
        "ip_address": None,  # TODO: Get from request
        "user_agent": None,
        "by_laws_content_snapshot": None,
        "is_current": True,
        "created_at": today,
    }

    await db.by_laws_acknowledgments.insert_one(acknowledgment)

    # Update user record (this might need to be membership-specific later)
    # For now, we update the user globally as having acknowledged for this building
    await db.users.update_one(
        {"id": user_id},
        {
            "$set": {
                "by_laws_acknowledged": True,
                "by_laws_version_acknowledged": by_laws_version,
                "by_laws_acknowledgment_date": today,
            }
        },
    )

    return {"message": "By-laws acknowledged successfully", "version": by_laws_version}


@api_router.get("/by-laws/my-acknowledgment")
async def get_my_acknowledgment(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Check if current user has acknowledged by-laws for this building"""
    acknowledgment = await db.by_laws_acknowledgments.find_one(
        {"user_id": current_user["id"], "building_id": building_id, "is_current": True}, {"_id": 0}
    )

    if not acknowledgment:
        return {"acknowledged": False, "version": None}

    return {
        "acknowledged": True,
        "version": acknowledgment["by_laws_version"],
        "acknowledged_date": acknowledgment["acknowledged_date"],
    }


# ==================== OCCUPANCY REPORT ENDPOINT ====================


@api_router.get("/admin/occupancy-report")
async def get_occupancy_report(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Generate comprehensive occupancy report for all units. Scoped to building.
    Shows owners, tenants, and guests for each unit
    Admin and Chairman only
    """
    # Check permission
    if _effective_role(current_user) not in ["super_admin", "strata_admin", "ec_member"]:
        raise HTTPException(
            status_code=403, detail="Not authorized to view occupancy report"
        )

    # Get all units for this building
    all_units = await db.units.find({"building_id": building_id}, {"_id": 0}).sort("unit_number", 1).to_list(200)

    # Get all active user-unit relationships for this building
    user_units = await db.user_units.find({"building_id": building_id, "is_active": True}, {"_id": 0}).to_list(1000)

    # Build occupancy data per unit
    unit_occupancy = {}
    for unit in all_units:
        unit_num = unit["unit_number"]
        unit_occupancy[unit_num] = {
            "unit": unit,
            "owners": [],
            "tenants": [],
            "guests": [],
        }

    # Get user details for each occupant - Optimized to avoid N+1 database calls
    user_ids = list(set(uu["user_id"] for uu in user_units))
    users_list = await db.users.find(
        {"id": {"$in": user_ids}, "email": {"$ne": SYSTEM_HIDDEN_EMAIL}},
        {"_id": 0, "id": 1, "full_name": 1, "email": 1},
    ).to_list(len(user_ids))
    user_map = {u["id"]: u for u in users_list}

    for uu in user_units:
        unit_num = uu["unit_number"]
        if unit_num not in unit_occupancy:
            continue

        user = user_map.get(uu["user_id"])

        if not user:
            continue

        occupant_info = {
            "user_id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "role_at_unit": uu["role_at_unit"],
            "start_date": uu["start_date"],
            "end_date": uu.get("end_date"),
            "expiration_date": uu.get("expiration_date"),
            "is_active": uu["is_active"],
        }

        role = uu["role_at_unit"]
        if role == "owner":
            unit_occupancy[unit_num]["owners"].append(occupant_info)
        elif role == "tenant":
            unit_occupancy[unit_num]["tenants"].append(occupant_info)
        elif role == "guest":
            unit_occupancy[unit_num]["guests"].append(occupant_info)

    # Build report
    units_report = []
    total_owners = 0
    total_tenants = 0
    total_guests = 0
    occupied_units = 0

    for unit_num, data in unit_occupancy.items():
        owner_count = len(data["owners"])
        tenant_count = len(data["tenants"])
        guest_count = len(data["guests"])
        total_occupants = owner_count + tenant_count + guest_count

        if total_occupants > 0:
            occupied_units += 1

        total_owners += owner_count
        total_tenants += tenant_count
        total_guests += guest_count

        units_report.append(
            {
                "unit_number": unit_num,
                "lot_number": data["unit"]["lot_number"],
                "unit_type": data["unit"].get("unit_type", "Unknown"),
                "owner_count": owner_count,
                "tenant_count": tenant_count,
                "guest_count": guest_count,
                "total_occupants": total_occupants,
                "owners": data["owners"],
                "tenants": data["tenants"],
                "guests": data["guests"],
            }
        )

    total_units = len(all_units)
    vacant_units = total_units - occupied_units
    occupancy_rate = (occupied_units / total_units * 100) if total_units > 0 else 0

    summary = {
        "total_units": total_units,
        "occupied_units": occupied_units,
        "vacant_units": vacant_units,
        "total_owners": total_owners,
        "total_tenants": total_tenants,
        "total_guests": total_guests,
        "total_occupants": total_owners + total_tenants + total_guests,
        "occupancy_rate": round(occupancy_rate, 2),
    }

    return {
        "summary": summary,
        "units": units_report,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": current_user["full_name"],
    }


# ==================== ADMIN APPROVAL WORKFLOWS ====================
# @featuretrace:owner-transfers — Owner transfer request CRUD and multi-approver workflow.
# Layer: router
# Data flow: OwnerTransfersPage → GET/POST/PATCH/PUT /owner-transfers* → owner_transfer_requests, ownership_transfer_log, user_units, users, memberships (scope param: building|global).
# Related: frontend/src/pages/dashboard/admin/OwnerTransfersPage.jsx


OWNER_TRANSFER_MANAGER_APPROVER_ROLES = {"super_admin", "strata_manager", "real_estate_agent"}
# 'chairman' is NOT a top-level user.role value (see rules/post-compact-critical.md) — a
# chairman is a user with role 'ec_member' and ec_position 'CHAIRMAN', so 'ec_member' alone
# already covers them via _effective_role().
OWNER_TRANSFER_EC_APPROVER_ROLES = {"ec_member", "strata_admin"}
OWNER_TRANSFER_REVIEWER_ROLES = (
        OWNER_TRANSFER_MANAGER_APPROVER_ROLES | OWNER_TRANSFER_EC_APPROVER_ROLES
)
OWNER_TRANSFER_PENDING_STATUSES = {"pending", "pending_second_approval"}
# Identifies portal-approved transfers in ownership_transfer_log.source;
# kept as a constant so typos surface at import time, not in audit queries.
OWNER_TRANSFER_LOG_SOURCE = "owner_transfer_request"


def _can_review_owner_transfer(user: dict) -> bool:
    """Generated function header.

    Function: _can_review_owner_transfer
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _effective_role(user) in OWNER_TRANSFER_REVIEWER_ROLES


def _can_staff_initiate_owner_transfer(user: dict) -> bool:
    """Generated function header.

    Function: _can_staff_initiate_owner_transfer
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _effective_role(user) in OWNER_TRANSFER_REVIEWER_ROLES


def _build_owner_transfer_review_entry(
        current_user: dict, action: str, review_notes: Optional[str], decided_at: str
) -> dict:
    """Generated function header.

    Function: _build_owner_transfer_review_entry
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {
        "user_id": current_user["id"],
        "full_name": current_user["full_name"],
        "role": _effective_role(current_user),
        "action": action,
        "review_notes": review_notes,
        "decided_at": decided_at,
    }


async def _resolve_owner_transfer_new_owner(
        new_owner_id: Optional[str], new_owner_email: Optional[str]
) -> dict:
    """Generated function header.

    Function: _resolve_owner_transfer_new_owner
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    owner_id = (new_owner_id or "").strip()
    owner_email = (new_owner_email or "").strip().lower()

    if not owner_id and not owner_email:
        raise HTTPException(
            status_code=400,
            detail="Provide either a new owner user ID or email address",
        )

    query = {"id": owner_id} if owner_id else {"email": owner_email}
    new_owner = await db.users.find_one(query, {"_id": 0})
    if not new_owner:
        if owner_id:
            # Explicit user-ID lookup that fails is a real error
            raise HTTPException(status_code=404, detail="New owner user not found")
        # Email not yet registered — provisional record; invite sent on approval
        return {"id": None, "email": owner_email, "full_name": None, "is_provisional": True}
    return new_owner


async def _get_owner_transfer_current_owner_info(
        building_id: str, unit_number: str
) -> List[dict]:
    """Resolve the current owner(s) of a unit for the manual create/edit transfer flows.

    Widened 2026-08-19: this used to hard-require a real active user_units row and
    404 otherwise. But ownership_transfer_detection_service.detect_and_create_portal_owner_transfer
    (the portal drift detector) creates transfers via _active_owner_info(), which
    already tolerates a legacy-fallback owner (units.owner_name, no user_units link
    at all) -- so a transfer could legitimately be CREATED for such a unit but could
    never be EDITED afterward, since this function's stricter re-derivation 404'd on
    every PATCH. Delegates to _active_owner_info() so both paths agree on what counts
    as "a current owner." Still 404s if genuinely nothing is known about the unit.
    """
    from services.ownership_transfer_detection_service import _active_owner_info

    owner_info = await _active_owner_info(db, building_id, unit_number)
    if not owner_info:
        raise HTTPException(
            status_code=404, detail="No current owners found for this unit"
        )

    return [
        {
            "user_id": owner["user_id"],
            "full_name": owner["full_name"],
            "email": owner.get("email"),
        }
        for owner in owner_info
    ]


async def _get_owner_transfer_accessible_units(
        current_user: dict, building_id: str
) -> List[dict]:
    """Generated function header.

    Function: _get_owner_transfer_accessible_units
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = _effective_role(current_user)
    projection = {
        "_id": 0,
        "unit_number": 1,
        "lot_number": 1,
        "owner_name": 1,
        "owner_name_b": 1,
    }

    if role in OWNER_TRANSFER_REVIEWER_ROLES:
        units = await db.units.find(
            {"building_id": building_id}, projection
        ).sort("unit_number", 1).to_list(500)
    else:
        owner_rels = await db.user_units.find(
            {
                "building_id": building_id,
                "user_id": current_user["id"],
                "role_at_unit": "owner",
                "is_active": True,
            },
            {"_id": 0, "unit_number": 1},
        ).to_list(50)
        unit_numbers = sorted(
            {rel["unit_number"] for rel in owner_rels if rel.get("unit_number")}
        )
        if not unit_numbers:
            return []
        units = await db.units.find(
            {"building_id": building_id, "unit_number": {"$in": unit_numbers}},
            projection,
        ).sort("unit_number", 1).to_list(len(unit_numbers))

    result = []
    for unit in units:
        owner_names = [
            name
            for name in [unit.get("owner_name"), unit.get("owner_name_b")]
            if name
        ]
        result.append(
            {
                "unit_number": unit.get("unit_number"),
                "lot_number": unit.get("lot_number"),
                "display_name": f"Unit {unit.get('unit_number')}",
                "owner_names": owner_names,
            }
        )

    result.sort(key=lambda item: str(item.get("unit_number") or ""))
    return result


# asyncio.create_task() does not itself keep a strong reference to the task it
# creates — if nothing else holds one, the task is eligible for garbage
# collection at any await point before it completes, silently disappearing
# with no exception raised and nothing logged (see the asyncio docs' own
# warning on this). This module-level set exists so fire-and-forget calls that
# matter (e.g. _write_postgres_ownership_period, below) can't be lost to that
# race. Found live 2026-07-24: TH078's owner-transfer approval (2026-07-16)
# fired _write_postgres_ownership_period via a bare asyncio.create_task() with
# no reference kept, and its Postgres write never happened — no error, no log
# line, nothing — leaving core.ownership_periods silently stale.
_PENDING_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> asyncio.Task:
    """asyncio.create_task() with a kept reference so the task can't be
    garbage-collected mid-execution. Use for background work whose completion
    matters (even though its result/exceptions are still not awaited by the
    caller) — not a substitute for awaiting work the caller depends on."""
    task = asyncio.create_task(coro)
    _PENDING_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_PENDING_BACKGROUND_TASKS.discard)
    return task


async def _sync_postgres_user_units_for_transfer(
        building_id: str,
        unit_number: str,
        outgoing_user_ids: list[str],
        new_owner_id: str | None,
        new_owner_name: str,
        new_owner_email: str,
        settlement_date: str,
) -> None:
    """Apply an approved transfer to core.user_units.

    Why this exists
    ---------------
    Approving a transfer used to write core.ownership_periods (via
    _write_postgres_ownership_period) and thirteen MongoDB collections, but
    NOTHING in core.user_units — no code anywhere closed a link there. For a
    building whose identity_core is promoted, that is the table
    list_active_users_for_scheme reads, so /admin/users kept listing sellers as
    current owners indefinitely while core.ownership_periods said, correctly,
    that they had sold. The two tables answered "who owns this lot?" differently
    and only one of them was maintained.

    Found live on East Gate TH078 (2026-08-27): ownership_periods had closed
    Olivia Rollings and Mark Raets at 2026-07-01 and opened Tavis Christian Hamer
    at 2026-07-02, while all three still held open user_units rows. The same audit
    found the mirror-image gap on six lots (UA019, UA023, UA031, UA050, UA059,
    UA065): a current ownership period with no user_units link at all, because the
    incoming owner never got one either.

    Why the two writes are in SEPARATE transactions
    -----------------------------------------------
    Retiring the sellers is the half that fixes the reported bug, and it must not
    be undone by a failure in the half that adds the buyer. core.user_units has a
    FOREIGN KEY on user_id -> core.users and a UNIQUE index on
    (user_id, lot_id, valid_from), and the buyer id reaching this function is not
    guaranteed to satisfy either:

      * the caller mints a fresh uuid4 for an "internal contact" owner
        (server.py, is_internal_contact_owner branch) that exists in MongoDB only,
        so the FK rejects it;
      * a legacy MongoDB user id may not be a UUID at all, so CAST(... AS UUID)
        raises before any FK is consulted;
      * a buyer who previously held this lot on the same valid_from collides with
        the unique index.

    In a single transaction any of those aborts the seller closure too, leaving
    exactly the stale state this function exists to prevent. Committing the
    closure first means the worst case is "sellers retired, buyer link missing" —
    visible, repairable, and strictly better than "nothing happened".

    Non-fatal, like its ownership_periods sibling: the MongoDB writes have already
    committed by the time this runs, so raising here would report a failure for a
    transfer that did happen. Failures are logged at ERROR — a silent skip is what
    let the drift accumulate unnoticed.
    """
    from datetime import date as _date

    closed = 0
    opened = False
    try:
        from db_postgres.repos import identity_repo as _id_repo
        from db_postgres.repos import ownership_repo as _own_repo
        from db_postgres.session import async_session_context, set_tenant as _set_tenant
        from sqlalchemy import text as sa_text

        scheme = await _id_repo.get_scheme_by_number(building_id)
        if not scheme:
            logger.warning(
                "_sync_postgres_user_units_for_transfer: scheme not found for building_id=%s — skipping",
                building_id,
            )
            return
        scheme_id, tenant_id = str(scheme["scheme_id"]), str(scheme["tenant_id"])

        try:
            end_date = _date.fromisoformat((settlement_date or "")[:10])
        except (ValueError, TypeError):
            end_date = _date.today()

        # ── Transaction 1: retire the sellers ────────────────────────────────
        # core.lots and core.user_units have no RLS bypass clause, so the real
        # tenant must be set before either is touched (CLAUDE.md footgun #8).
        async with async_session_context() as pg:
            await _set_tenant(pg, tenant_id)
            lot_id = await _own_repo.get_lot_id_by_number(pg, scheme_id, unit_number)
            if not lot_id:
                logger.warning(
                    "_sync_postgres_user_units_for_transfer: lot %s not in core.lots for scheme %s — skipping",
                    unit_number, scheme_id,
                )
                return

            # Only ids that are valid UUIDs can be compared against a uuid column.
            # A legacy MongoDB id is dropped here rather than raising mid-statement.
            outgoing_uuids = [str(uid) for uid in (outgoing_user_ids or []) if _is_uuid(uid)]
            skipped_outgoing = len(outgoing_user_ids or []) - len(outgoing_uuids)
            if skipped_outgoing:
                logger.warning(
                    "_sync_postgres_user_units_for_transfer: %d outgoing owner id(s) for unit=%s "
                    "are not UUIDs (legacy Mongo ids) and were not retired in Postgres",
                    skipped_outgoing, unit_number,
                )

            if outgoing_uuids:
                result = await pg.execute(
                    sa_text("""
                        UPDATE core.user_units
                           SET valid_to = :end_date
                         WHERE scheme_id = CAST(:scheme_id AS UUID)
                           AND lot_id = CAST(:lot_id AS UUID)
                           AND relationship = 'owner'
                           AND valid_to IS NULL
                           AND user_id::TEXT = ANY(:user_ids)
                    """),
                    {"end_date": end_date, "scheme_id": scheme_id,
                     "lot_id": lot_id, "user_ids": outgoing_uuids},
                )
                closed = result.rowcount or 0

                # The owner role assignment exists BECAUSE of the link, and
                # list_active_users_for_scheme accepts either one as proof of
                # membership — so closing only the link leaves the seller listed.
                # Scoped by NOT EXISTS so a seller who still holds another lot in
                # this scheme keeps their owner role. Elevated assignments (EC seat,
                # manager, admin staff) are never touched: they are appointments in
                # their own right, not consequences of holding a unit.
                await pg.execute(
                    sa_text("""
                        UPDATE core.user_role_assignments
                           SET is_active = FALSE
                         WHERE scheme_id = CAST(:scheme_id AS UUID)
                           AND user_id::TEXT = ANY(:user_ids)
                           AND is_active = TRUE
                           AND role = CAST('owner' AS core.user_role)
                           AND NOT EXISTS (
                                 SELECT 1 FROM core.user_units uu
                                  WHERE uu.scheme_id = CAST(:scheme_id AS UUID)
                                    AND uu.user_id = core.user_role_assignments.user_id
                                    AND uu.relationship = 'owner'
                                    AND uu.valid_to IS NULL)
                    """),
                    {"scheme_id": scheme_id, "user_ids": outgoing_uuids},
                )
        # async_session_context commits on clean exit; the closure is durable here.

        # ── Transaction 2: link the buyer ────────────────────────────────────
        if not new_owner_id:
            pass
        elif not _is_uuid(new_owner_id):
            # An internal-contact owner has no Postgres identity by design.
            logger.warning(
                "_sync_postgres_user_units_for_transfer: new owner id %r for unit=%s is not a UUID "
                "— sellers retired, no buyer link created",
                new_owner_id, unit_number,
            )
        else:
            async with async_session_context() as pg:
                await _set_tenant(pg, tenant_id)
                # core.users carries an RLS bypass clause, but under a real tenant
                # context a row belonging to another tenant is invisible
                # (CLAUDE.md footgun #11). Scoping the check to this tenant is
                # correct here: a buyer must belong to this scheme's tenant to be
                # linkable to its lots.
                buyer_exists = (await pg.execute(
                    sa_text("""SELECT 1 FROM core.users
                                WHERE user_id = CAST(:uid AS UUID)
                                  AND tenant_id = CAST(:tid AS UUID) LIMIT 1"""),
                    {"uid": str(new_owner_id), "tid": tenant_id},
                )).scalar()
                if not buyer_exists:
                    # Checked rather than caught: letting the FK raise would abort
                    # this transaction, and the log line would name a constraint
                    # instead of the actual cause.
                    logger.warning(
                        "_sync_postgres_user_units_for_transfer: new owner %s (%s) has no core.users row "
                        "in tenant %s for unit=%s — sellers retired, no buyer link created",
                        new_owner_id, new_owner_email or "no email", tenant_id, unit_number,
                    )
                else:
                    lot_id = await _own_repo.get_lot_id_by_number(pg, scheme_id, unit_number)
                    party_id = await _own_repo.upsert_owner_party(
                        pg, tenant_id, new_owner_name or new_owner_email, new_owner_email or None
                    )
                    # Two guards, for two different collisions:
                    #   WHERE NOT EXISTS — an already-open link for this buyer and
                    #     lot, i.e. a replayed approval. Keeps the run idempotent.
                    #   ON CONFLICT DO NOTHING — the UNIQUE index on
                    #     (user_id, lot_id, valid_from), which a CLOSED prior period
                    #     with the same settlement date would violate even though
                    #     NOT EXISTS passes (a buyer reacquiring a lot they once held).
                    result = await pg.execute(
                        sa_text("""
                            INSERT INTO core.user_units
                                (tenant_id, scheme_id, user_id, lot_id, party_id, relationship, valid_from)
                            SELECT CAST(:tenant_id AS UUID), CAST(:scheme_id AS UUID),
                                   CAST(:user_id AS UUID), CAST(:lot_id AS UUID),
                                   CAST(:party_id AS UUID), 'owner', :valid_from
                             WHERE NOT EXISTS (
                                   SELECT 1 FROM core.user_units
                                    WHERE scheme_id = CAST(:scheme_id AS UUID)
                                      AND lot_id = CAST(:lot_id AS UUID)
                                      AND user_id = CAST(:user_id AS UUID)
                                      AND relationship = 'owner'
                                      AND valid_to IS NULL)
                            ON CONFLICT (user_id, lot_id, valid_from) DO NOTHING
                        """),
                        {"tenant_id": tenant_id, "scheme_id": scheme_id, "user_id": str(new_owner_id),
                         "lot_id": lot_id, "party_id": str(party_id), "valid_from": end_date},
                    )
                    opened = bool(result.rowcount)

        logger.info(
            "_sync_postgres_user_units_for_transfer: unit=%s closed=%d opened=%s new_owner=%s",
            unit_number, closed, opened, new_owner_email or new_owner_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_sync_postgres_user_units_for_transfer: FAILED for unit=%s building=%s after "
            "closed=%d opened=%s: %s — core.user_units may be stale for this lot and "
            "/admin/users can keep showing the previous owner until repaired "
            "(backend/scripts/data_repair/retire_stale_owner_and_demo_accounts_20260827.py)",
            unit_number, building_id, closed, opened, exc,
        )


async def _write_postgres_ownership_period(
        building_id: str,
        unit_number: str,
        new_owner_name: str,
        new_owner_email: str,
        settlement_date: str,
) -> None:
    """Write the new ownership to Postgres core.ownership_periods.

    Phase G prep: called as a fire-and-forget task from
    _finalize_owner_transfer_approval() after all MongoDB writes succeed.

    Non-fatal — if the lot hasn't been onboarded to Postgres yet (core.lots
    missing), we log a warning and return without raising.  Phase G will make
    this write mandatory once all buildings are fully migrated.
    """
    try:
        from datetime import date as _date
        from db_postgres.repos import identity_repo as _id_repo
        from db_postgres.repos import ownership_repo as _own_repo
        from db_postgres.session import async_session_context

        scheme = await _id_repo.get_scheme_by_number(building_id)
        if not scheme:
            logger.warning(
                "_write_postgres_ownership_period: scheme not found for building_id=%s — skipping",
                building_id,
            )
            return

        scheme_id = str(scheme["scheme_id"])
        tenant_id = str(scheme["tenant_id"])

        async with async_session_context() as pg:
            from db_postgres.session import set_tenant as _set_tenant
            await _set_tenant(pg, tenant_id)

            lot_id = await _own_repo.get_lot_id_by_number(pg, scheme_id, unit_number)
            if not lot_id:
                logger.warning(
                    "_write_postgres_ownership_period: lot %s not in core.lots for scheme %s — skipping",
                    unit_number, scheme_id,
                )
                return

            # Parse settlement date
            try:
                valid_from = _date.fromisoformat(settlement_date[:10])
            except (ValueError, TypeError):
                valid_from = _date.today()

            # Upsert the owner as a party, then transition ownership periods.
            # No explicit commit needed: async_session_context() commits automatically
            # on successful context exit (session.py:46). An explicit commit here
            # would double-commit and violate the context manager's contract.
            party_id = await _own_repo.upsert_owner_party(
                pg, tenant_id, new_owner_name or new_owner_email, new_owner_email or None
            )
            closed_count = await _own_repo.close_ownership_period(pg, lot_id, valid_from, tenant_id)
            new_period_id = await _own_repo.open_ownership_period(
                pg, tenant_id, scheme_id, lot_id, party_id, valid_from
            )

        if new_period_id is None:
            # ON CONFLICT DO NOTHING suppressed the insert — almost always
            # because a prior period for this lot is still open (valid_to IS
            # NULL) and the EXCLUDE constraint rejects the overlap. This is
            # the exact failure mode found live for TH078: the old owner's
            # period was never closed, so the new owner's period silently
            # never got created either. Logged loudly (not the routine info
            # line below) so this can't recur invisibly again.
            logger.error(
                "_write_postgres_ownership_period: insert for unit=%s new_owner=%s "
                "valid_from=%s was suppressed (ON CONFLICT DO NOTHING) — closed_count=%d "
                "existing open periods for this lot were NOT successfully replaced; "
                "core.ownership_periods is now stale for this lot until manually fixed",
                unit_number, new_owner_name or new_owner_email, valid_from, closed_count,
            )
            return

        logger.info(
            "_write_postgres_ownership_period: recorded ownership of %s → %s from %s "
            "(closed %d prior period(s), new period=%s)",
            unit_number, new_owner_name or new_owner_email, valid_from, closed_count, new_period_id,
        )
    except Exception as exc:
        logger.warning(
            "_write_postgres_ownership_period: non-fatal error for unit %s: %s",
            unit_number, exc,
        )


async def _write_postgres_role_assignment(
        building_id: str,
        user_email: str,
        role: str,
        ec_position: str | None,
        granted_by: str | None,
) -> None:
    """Propagate a role/EC-position change to promoted Postgres core.user_role_assignments.

    Mirrors _write_postgres_ownership_period: fire-and-forget, non-fatal, gated on
    identity_core being postgres_write for this building. This closes the identity
    write-cutover gap (GAP-IDENTITY-UI-DB-001): /auth/me computes effective role
    from core.user_effective_role() (Postgres), so a role edit written only to
    Mongo would not take effect for a PG-session user. This makes the promoted
    Postgres store match what the admin just set in Mongo.

    No-op (returns without writing) when identity_core is not postgres_write for
    this building, or when the user has no core.users row yet — never an error.
    """
    try:
        from db_postgres.repos import identity_repo as _id_repo
        from services.domain_source_guard import require_domain_source

        decision = await require_domain_source(
            domain="identity_core",
            building_id=building_id,
            operation="write",
            requested_source="postgres",
        )
        if not decision.postgres_allowed:
            # identity_core not promoted to postgres_write for this building —
            # Mongo remains authoritative here, nothing to propagate.
            return

        scheme = await _id_repo.get_scheme_by_number(building_id)
        if not scheme:
            logger.warning(
                "_write_postgres_role_assignment: scheme not found for building_id=%s — skipping",
                building_id,
            )
            return

        pg_user = await _id_repo.find_user_by_email_for_admin(user_email)
        if not pg_user or not pg_user.get("id"):
            logger.warning(
                "_write_postgres_role_assignment: no core.users row for %s — skipping",
                user_email,
            )
            return

        await _id_repo.set_scheme_role(
            user_id=str(pg_user["id"]),
            tenant_id=str(scheme["tenant_id"]),
            scheme_id=str(scheme["scheme_id"]),
            role=str(role),
            ec_position=ec_position,
            granted_by=granted_by,
        )
        logger.info(
            "_write_postgres_role_assignment: set %s → role=%s ec_position=%s (scheme=%s)",
            user_email, role, ec_position, scheme["scheme_id"],
        )
    except Exception as exc:
        logger.warning(
            "_write_postgres_role_assignment: non-fatal error for %s: %s",
            user_email, exc,
        )


async def _sync_unit_owner_snapshot(
        building_id: str, unit_number: str, updated_at: str
) -> dict:
    """Generated function header.

    Function: _sync_unit_owner_snapshot
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    owner_links = await db.user_units.find(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "is_active": True,
        },
        {"_id": 0, "user_id": 1},
    ).to_list(10)

    owner_ids = [link["user_id"] for link in owner_links]
    owner_docs = []
    if owner_ids:
        owner_docs = await db.users.find(
            {"id": {"$in": owner_ids}}, {"_id": 0}
        ).to_list(len(owner_ids))

    owners_by_id = {doc["id"]: doc for doc in owner_docs}
    ordered_owners = []
    for link in owner_links:
        owner_doc = owners_by_id.get(link["user_id"])
        if not owner_doc:
            continue
        full_name = (
                owner_doc.get("full_name")
                or f"{owner_doc.get('first_name', '')} {owner_doc.get('last_name', '')}".strip()
        )
        ordered_owners.append(
            {
                "full_name": full_name,
                "email": owner_doc.get("email"),
                "phone": owner_doc.get("phone"),
            }
        )

    primary_owner = ordered_owners[0] if ordered_owners else {}
    secondary_owner = ordered_owners[1] if len(ordered_owners) > 1 else {}
    snapshot = {
        "owner_name": primary_owner.get("full_name", ""),
        "owner_email": primary_owner.get("email", ""),
        "owner_phone": primary_owner.get("phone", ""),
        "owner_name_b": secondary_owner.get("full_name", ""),
        "owner_email_b": secondary_owner.get("email", ""),
        "updated_at": updated_at,
    }
    # Propagate to both units (canonical display) and strata_owners (financial/roll fallback).
    # Both must stay in sync — strata_owners is read by levy ledger and strata roll views
    # that don't go through user_units→users.
    units_update = {"$set": snapshot}
    # Always build the strata_owners update dict regardless of whether owner_name
    # is truthy. If the user-record lookup failed and owner_name resolved to "",
    # we must still write "" to strata_owners — keeping the old owner name while
    # units is cleared would leave the strata roll inconsistent with the portal.
    strata_owners_update: dict = {
        "owner_name": snapshot.get("owner_name", ""),
        "owner_email": snapshot.get("owner_email", ""),
        "owner_name_b": snapshot.get("owner_name_b") or None,
        "updated_at": updated_at,
    }

    await asyncio.gather(
        db.units.update_one(
            {"building_id": building_id, "unit_number": unit_number},
            units_update,
        ),
        db.strata_owners.update_one(
            {"building_id": building_id, "unit_number": unit_number},
            {"$set": strata_owners_update},
        ),
    )
    return snapshot


async def _finalize_owner_transfer_approval(
        transfer: dict,
        action: str,
        review_notes: Optional[str],
        remove_owner_ids: List[str],
        current_user: dict,
        building_id: str,
        today: str,
        approval_history: List[dict],
) -> dict:
    """Generated function header.

    Function: _finalize_owner_transfer_approval
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    new_owner_id = transfer["new_owner"].get("user_id")
    new_owner_email = (transfer["new_owner"].get("email") or "").strip().lower()
    unit_number = transfer["unit_number"]
    is_internal_contact_owner = bool(
        transfer["new_owner"].get("is_internal_contact_email")
        or new_owner_email.endswith("@strataos.local")
    )

    if transfer.get("submitted_by_id") == current_user["id"]:
        raise HTTPException(
            status_code=400,
            detail="Request submitters cannot approve their own owner transfer",
        )

    if action == "approve_remove_old":
        valid_owner_ids = {owner["user_id"] for owner in transfer.get("old_owners", [])}
        invalid_owner_ids = sorted(set(remove_owner_ids) - valid_owner_ids)
        if invalid_owner_ids:
            raise HTTPException(
                status_code=400,
                detail="One or more selected owners do not belong to this transfer",
            )

    # Provisional owner: email was supplied but no portal account existed at request time.
    # Re-check in case they registered since; otherwise create an account and send an invite.
    # Also handle the case where new_owner_id is set but the user record no longer exists
    # (e.g. after a database reset) — recreate it so downstream writes succeed.
    if new_owner_id:
        existing_user = await db.users.find_one({"id": new_owner_id}, {"_id": 0})
        if not existing_user and new_owner_email:
            # User_id is known but the user record is gone — recreate a stub account.
            # The owner will need to do a password reset to regain access.
            await db.users.insert_one({
                "id": new_owner_id,
                "email": new_owner_email,
                "full_name": transfer["new_owner"].get("full_name") or "",
                "first_name": "",
                "last_name": "",
                "role": "owner",
                "building_id": building_id,
                "unit_number": unit_number,
                "is_approved": False,
                "status": "active",
                "requires_account_setup": True,
                "password_hash": hash_password(secrets.token_urlsafe(32)),
                "created_at": today,
                "updated_at": today,
            })
            # Membership reconciliation is handled once, unconditionally, later in
            # this function (the find_one/update_one-with-$addToSet block) — an
            # earlier duplicate insert here raced against it under stale/non-linearized
            # reads and could create two membership documents for the same user+building.
            logger.warning(
                "owner_transfer: recreated missing user record for %s (user_id=%s)",
                new_owner_email, new_owner_id,
            )

    if not new_owner_id:
        if not new_owner_email:
            raise HTTPException(status_code=400, detail="Transfer has no new owner email — cannot finalise")
        now_user = await db.users.find_one({"email": new_owner_email}, {"_id": 0})
        if now_user:
            new_owner_id = now_user["id"]
        else:
            new_owner_id = str(uuid.uuid4())
            await db.users.insert_one({
                "id": new_owner_id,
                "email": new_owner_email,
                "full_name": "",
                "first_name": "",
                "last_name": "",
                "role": "owner",
                "building_id": building_id,
                "unit_number": unit_number,
                "is_approved": False,
                "status": "active",
                "requires_account_setup": True,
                "password_hash": hash_password(secrets.token_urlsafe(32)),
                "created_at": today,
                "updated_at": today,
            })
            await db.memberships.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": new_owner_id,
                "building_id": building_id,
                "roles": ["owner"],
                "is_active": True,
                "is_primary": True,
                "units": [unit_number],
                "created_at": today,
            })
            invite_token = secrets.token_urlsafe(32)
            invite_expires = datetime.now(timezone.utc) + timedelta(hours=72)
            await db.password_resets.insert_one({
                "token": invite_token,
                "user_id": new_owner_id,
                "email": new_owner_email,
                "expires_at": invite_expires.isoformat(),
                "used": False,
            })
            frontend_url = _get_portal_url()
            invite_link = f"{frontend_url}/reset-password?token={invite_token}"
            html_body, text_body = get_email_template(
                "owner_transfer_invite",
                invite_link=invite_link,
                unit_number=unit_number,
            )
            await send_email_async(
                new_owner_email,
                f"You've been added as an owner — Unit {unit_number}",
                html_body,
                text_body,
            )
        # Write the resolved user_id back onto the transfer so the rest of this
        # function and any later audit queries have a consistent user_id.
        await db.owner_transfer_requests.update_one(
            {"id": transfer["id"]},
            {"$set": {"new_owner.user_id": new_owner_id, "new_owner.is_provisional": False}},
        )
        # M-2: link the new portal user_id to the existing strata_owners record so
        # financial and roll views can resolve portal identity from the scraper-sourced
        # strata roll without needing a full user_units join.
        await db.strata_owners.update_one(
            {"building_id": building_id, "unit_number": unit_number},
            {"$set": {"user_id": new_owner_id, "updated_at": today}},
        )

    # M-2 (unconditional): link the new owner's portal user_id to the strata roll record
    # regardless of whether they were provisional. The branch above only runs for
    # provisional owners; existing portal accounts previously left strata_owners unlinked.
    await db.strata_owners.update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"$set": {"user_id": new_owner_id, "updated_at": today}},
    )

    existing_membership = await db.memberships.find_one(
        {"user_id": new_owner_id, "building_id": building_id}, {"_id": 0}
    )
    if existing_membership:
        await db.memberships.update_one(
            {"user_id": new_owner_id, "building_id": building_id},
            {
                "$set": {"is_active": True, "updated_at": today},
                "$addToSet": {"roles": "owner", "units": unit_number},
            },
        )
    else:
        await db.memberships.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": new_owner_id,
            "building_id": building_id,
            "roles": ["owner"],
            "is_active": True,
            "is_primary": True,
            "units": [unit_number],
            "created_at": today,
            "updated_at": today,
        })

    existing_ownership = await db.user_units.find_one(
        {
            "building_id": building_id,
            "user_id": new_owner_id,
            "unit_number": unit_number,
            "role_at_unit": "owner",
            "is_active": True,
        }
    )

    if existing_ownership:
        raise HTTPException(
            status_code=400,
            detail="New owner already has an active ownership relationship for this unit",
        )

    # Resolve the list of owners being removed once; reused for deactivation,
    # archiving, notifications, and the history log entry.
    owners_to_remove = (
        (remove_owner_ids if remove_owner_ids else [o["user_id"] for o in transfer.get("old_owners", [])])
        if action == "approve_remove_old"
        else []
    )

    # Mutate existing owner records BEFORE inserting the new one to avoid
    # violating the unique_active_primary_owner_per_unit partial index.
    if owners_to_remove:
        await db.user_units.update_many(
            {
                "building_id": building_id,
                "user_id": {"$in": owners_to_remove},
                "unit_number": unit_number,
                "role_at_unit": "owner",
            },
            {"$set": {"is_active": False, "is_primary": False, "actual_end_date": today, "updated_at": today}},
        )

        # Batch archive in 3 DB round-trips regardless of list length — avoids
        # N×3 sequential ops from the old per-owner loop.
        still_active_docs = await _server_agg(db.user_units, [
            {"$match": {
                "building_id": building_id,
                "user_id": {"$in": owners_to_remove},
                "role_at_unit": "owner",
                "is_active": True,
            }},
            {"$group": {"_id": "$user_id"}},
        ], None)
        still_active_ids = {doc["_id"] for doc in still_active_docs}
        owners_needing_archive = [uid for uid in owners_to_remove if uid not in still_active_ids]

        if owners_needing_archive:
            await db.memberships.update_many(
                {"building_id": building_id, "user_id": {"$in": owners_needing_archive}},
                {"$set": {
                    "is_active": False,
                    "archived_at": today,
                    "archived_reason": "owner_transfer_complete",
                    "updated_at": today,
                }},
            )
            # Global archive only for users with no remaining memberships elsewhere —
            # same multi-building guard as the manual /users/{id}/archive endpoint.
            still_elsewhere = await _server_agg(db.memberships, [
                {"$match": {"user_id": {"$in": owners_needing_archive}, "is_active": True}},
                {"$group": {"_id": "$user_id"}},
            ], None)
            still_elsewhere_ids = {doc["_id"] for doc in still_elsewhere}
            fully_removed = [uid for uid in owners_needing_archive if uid not in still_elsewhere_ids]
            if fully_removed:
                await db.users.update_many(
                    {"id": {"$in": fully_removed}},
                    {"$set": {
                        "status": "archived",
                        "is_active": False,
                        "archived_at": today,
                        "archived_by": current_user.get("id", ""),
                        "archived_reason": "owner_transfer_complete",
                        "updated_at": today,
                    }},
                )
    else:
        # approve_keep_old: demote existing primary owners so the new owner
        # can be inserted as is_primary=True without violating the unique index.
        await db.user_units.update_many(
            {
                "building_id": building_id,
                "unit_number": unit_number,
                "role_at_unit": "owner",
                "is_active": True,
                "is_primary": True,
            },
            {"$set": {"is_primary": False, "updated_at": today}},
        )

    new_user_unit = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "user_id": new_owner_id,
        "unit_number": unit_number,
        "role_at_unit": "owner",
        "start_date": transfer.get("settlement_date") or today,
        "end_date": None,
        "actual_end_date": None,
        "is_active": True,
        "is_primary": True,
        "lease_document_id": None,
        "lease_start_date": None,
        "lease_end_date": None,
        "auto_expire_enabled": False,
        "expiration_date": None,
        "guest_type": None,
        "host_user_id": None,
        "approved_by": current_user["id"],
        "approved_date": today,
        "approval_notes": review_notes,
        "created_at": today,
        "updated_at": today,
    }
    start_date = transfer.get("settlement_date") or today

    # Write to user_units (MongoDB canonical) AND lot_ownerships (Phase-E canonical).
    # Both must stay in sync — lot_ownerships is the durable ownership table that
    # survives database resets; user_units is the live session-scope lookup.
    lo_result, _ = await asyncio.gather(
        db.lot_ownerships.update_one(
            {"building_id": building_id, "lot_id": unit_number, "is_active": True},
            {"$set": {
                "owner_contact_id": new_owner_id,
                "start_date": start_date,
                "updated_at": today,
            }},
        ),
        db.user_units.insert_one(new_user_unit),
    )
    # If no active lot_ownerships row existed (e.g. manually created unit that never
    # went through Phase-E migration), insert a minimal record so the durable table
    # stays complete.  This avoids the silent no-op that leaves cross-collection drift.
    if lo_result.matched_count == 0:
        await db.lot_ownerships.insert_one({
            "building_id": building_id,
            "lot_id": unit_number,
            "owner_contact_id": new_owner_id,
            "start_date": start_date,
            "end_date": None,
            "ownership_type": "owner_occupied",
            "is_active": True,
            "is_primary": True,
            "created_at": today,
            "updated_at": today,
        })

    # M-3 — Phase G prep: write to Postgres core.ownership_periods so the
    # bitemporal ownership table accumulates real-time data from this point
    # forward.  Non-fatal: if core.lots is not yet populated for this building
    # (not yet onboarded via CSV import), the helper logs a warning and returns.
    # Phase G: remove the try/except wrapper in _write_postgres_ownership_period
    # and make this a mandatory step once all buildings are fully migrated.
    # _fire_and_forget() (not a bare asyncio.create_task()) keeps a reference so
    # this can't be garbage-collected mid-execution — see its own docstring for
    # the live incident this fixes (TH078, 2026-07-24).
    _fire_and_forget(_write_postgres_ownership_period(
        building_id=building_id,
        unit_number=unit_number,
        new_owner_name=transfer["new_owner"].get("full_name") or "",
        new_owner_email=new_owner_email,
        settlement_date=start_date,
    ))

    # core.ownership_periods alone is not enough: core.user_units is the table the
    # promoted-building user list actually reads, and until 2026-08-27 no code path
    # ever closed a link there on transfer. See the helper's docstring.
    _fire_and_forget(_sync_postgres_user_units_for_transfer(
        building_id=building_id,
        unit_number=unit_number,
        outgoing_user_ids=owners_to_remove,
        new_owner_id=new_owner_id,
        new_owner_name=transfer["new_owner"].get("full_name") or "",
        new_owner_email=new_owner_email,
        settlement_date=start_date,
    ))

    user_activation_fields = {
        "role": "owner",
        "building_id": building_id,
        "unit_number": unit_number,
        "ownership_verified": True,
        "verification_documents": transfer.get("ownership_documents", []),
        "updated_at": today,
    }
    if is_internal_contact_owner:
        # Imported owner names can arrive before a real email/contact record is
        # known. Keep that synthetic contact out of login-capable user flows; the
        # active user_units row still lets owner display resolve to the approved
        # name until staff collect proper contact details.
        user_activation_fields.update({
            "status": "ownership_contact_only",
            "is_active": False,
            "is_approved": False,
            "requires_account_setup": True,
        })
    else:
        user_activation_fields.update({"status": "active", "is_active": True})

    # Fetch the pre-change unit snapshot concurrently with the role update to
    # avoid a sequential DB round-trip before the cascade diff is computed.
    old_unit_result, _ = await asyncio.gather(
        db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0}),
        db.users.update_one(
            {"id": new_owner_id},
            {"$set": user_activation_fields},
        ),
    )
    old_unit = old_unit_result or {}
    cascade_update = await _sync_unit_owner_snapshot(building_id, unit_number, today)

    # Run as background task — side-effects (strata roll sync, AGM proxy alerts,
    # EC member checks) must not block or roll back the approval if they fail.
    asyncio.create_task(_cascade_owner_change(
        building_id, unit_number, old_unit, cascade_update, current_user
    ))

    await db.owner_transfer_requests.update_one(
        {"id": transfer["id"], "building_id": building_id},
        {
            "$set": {
                "status": "approved",
                "reviewed_by": current_user["id"],
                "reviewed_by_name": current_user["full_name"],
                "reviewed_date": today,
                "review_notes": review_notes,
                "action_taken": action,
                "ownership_verified": True,
                "required_approvals": transfer.get("required_approvals", 1),
                "current_approvals": transfer.get("current_approvals", 0),
                "approval_mode": transfer.get("approval_mode"),
                "approval_history": approval_history,
                "pending_approval_action": None,
                "updated_at": today,
            }
        },
    )

    # Write to ownership_transfer_log so this approval appears in the History tab.
    prev_owner_names = ", ".join(
        o.get("full_name", "") for o in transfer.get("old_owners", []) if o.get("full_name")
    )
    await db.ownership_transfer_log.insert_one({
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "unit_number": unit_number,
        "transfer_date": transfer.get("settlement_date") or today,
        "previous_owner_name": prev_owner_names,
        "new_owner_name": transfer["new_owner"].get("full_name") or "",
        "new_owner_email": transfer["new_owner"].get("email"),
        "action": action,
        "source": OWNER_TRANSFER_LOG_SOURCE,
        "transfer_request_id": transfer["id"],
        "approved_by_name": current_user.get("full_name", ""),
        "review_notes": review_notes,
        "confidence": "verified",
        "status": "approved",
        "imported_at": today,
    })

    submitter_id = transfer.get("submitted_by_id")
    if submitter_id:
        asyncio.create_task(create_user_notification(
            user_id=submitter_id,
            title="Owner Transfer Approved",
            message=f"Your transfer request for Unit {unit_number} has been approved and processed.",
            notification_type="owner_transfer",
            link="/admin/owner-transfers",
            building_id=building_id,
        ))

    if new_owner_id and new_owner_id != submitter_id and not is_internal_contact_owner:
        asyncio.create_task(create_user_notification(
            user_id=new_owner_id,
            title="You've Been Added as an Owner",
            message=f"Ownership of Unit {unit_number} has been transferred to you. Welcome!",
            notification_type="owner_transfer",
            link="/dashboard",
            building_id=building_id,
        ))

    for old_owner_id in owners_to_remove:
        if old_owner_id != submitter_id:
            asyncio.create_task(create_user_notification(
                user_id=old_owner_id,
                title="Ownership Transferred",
                message=f"You have been removed as an owner of Unit {unit_number} following an approved transfer.",
                notification_type="owner_transfer",
                link="/admin/owner-transfers",
                building_id=building_id,
            ))

    return {
        "message": "Transfer approved and processed",
        "status": "approved",
        "action": action,
    }


@api_router.get("/owner-transfers/form-options")
async def get_owner_transfer_form_options(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Return the units the current user can start an owner transfer for, plus
    page capability flags for the frontend.
    """
    accessible_units = await _get_owner_transfer_accessible_units(
        current_user, building_id
    )
    return {
        "can_create": bool(accessible_units),
        "can_review": _can_review_owner_transfer(current_user),
        "can_view_history": _can_review_owner_transfer(current_user),
        "accessible_units": accessible_units,
    }


@api_router.post("/owner-transfers")
async def create_owner_transfer_request(
        transfer: OwnerTransferRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create an owner transfer request. Owners may lodge requests for their own
    units, and reviewer roles may lodge requests for any building unit.
    """
    unit_number = (transfer.unit_number or "").strip()
    if not unit_number:
        raise HTTPException(status_code=400, detail="Unit number is required")

    if not _can_staff_initiate_owner_transfer(current_user):
        requester_ownership = await db.user_units.find_one(
            {
                "building_id": building_id,
                "unit_number": unit_number,
                "user_id": current_user["id"],
                "role_at_unit": "owner",
                "is_active": True,
            }
        )
        if not requester_ownership:
            raise HTTPException(
                status_code=403,
                detail="Only current owners or authorised staff can create owner transfers for this unit",
            )

    new_owner = await _resolve_owner_transfer_new_owner(
        transfer.new_owner_id, transfer.new_owner_email
    )
    old_owners_info = await _get_owner_transfer_current_owner_info(
        building_id, unit_number
    )

    if new_owner.get("id") and any(owner["user_id"] == new_owner["id"] for owner in old_owners_info):
        raise HTTPException(
            status_code=400,
            detail="The selected new owner is already an active owner for this unit",
        )

    transfer_id = str(uuid.uuid4())
    today = datetime.now(timezone.utc).isoformat()
    transfer_request = {
        "id": transfer_id,
        "building_id": building_id,
        "unit_number": unit_number,
        "old_owners": old_owners_info,
        "new_owner": {
            "user_id": new_owner.get("id"),
            "full_name": new_owner.get("full_name")
                         or f"{new_owner.get('first_name', '')} {new_owner.get('last_name', '')}".strip()
                         or "",
            "email": new_owner.get("email"),
            "is_provisional": new_owner.get("is_provisional", False),
        },
        "settlement_date": transfer.settlement_date,
        "request_notes": transfer.request_notes,
        "ownership_documents": transfer.ownership_documents,
        "ownership_verified": False,
        "status": "pending",
        "required_approvals": 1,
        "current_approvals": 0,
        "approval_mode": None,
        "approval_history": [],
        "pending_approval_action": None,
        "requested_date": today,
        "submitted_by_id": current_user["id"],
        "submitted_by_name": current_user["full_name"],
        "submitted_by_role": _effective_role(current_user),
        "reviewed_by": None,
        "reviewed_by_name": None,
        "reviewed_date": None,
        "review_notes": None,
        "action_taken": None,
        "old_owners_notified": False,
        "new_owner_notified": False,
        "created_at": today,
        "updated_at": today,
    }

    await db.owner_transfer_requests.insert_one(transfer_request)

    submitter_name = current_user.get("full_name") or "An owner"
    asyncio.create_task(broadcast_user_notification(
        recipient_roles=list(OWNER_TRANSFER_REVIEWER_ROLES),
        title="New Owner Transfer Request",
        message=f"{submitter_name} has lodged a transfer request for Unit {unit_number}. Please review it.",
        notification_type="owner_transfer",
        link="/admin/owner-transfers",
        building_id=building_id,
    ))

    return {
        "message": "Owner transfer request created",
        "id": transfer_id,
        "status": "pending",
    }


@api_router.get("/owner-transfers")
async def get_owner_transfer_requests(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get all owner transfer requests. Scoped to building.
    Filter by status: pending, approved, rejected
    """
    query = {"building_id": building_id}
    if not _can_review_owner_transfer(current_user):
        query["submitted_by_id"] = current_user["id"]

    if status == "pending":
        query["status"] = {"$in": list(OWNER_TRANSFER_PENDING_STATUSES)}
    elif status:
        query["status"] = status

    transfers = (
        await db.owner_transfer_requests.find(query, {"_id": 0})
        .sort("requested_date", -1)
        .to_list(100)
    )
    return transfers


@api_router.get("/owner-transfers/history")
async def get_owner_transfer_history(
        unit_number: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get completed/historical owner transfers from the ownership_transfer_log.
    These are processed transfers imported from Strata Web or recorded during approval.
    Scoped to building.
    """
    if not _can_review_owner_transfer(current_user):
        raise HTTPException(
            status_code=403, detail="Not authorized to view transfer history"
        )

    query = {"building_id": building_id}
    if unit_number:
        query["unit_number"] = unit_number

    history = (
        await db.ownership_transfer_log.find(query, {"_id": 0})
        .sort("transfer_date", -1)
        .to_list(500)
    )
    return history


@api_router.patch("/owner-transfers/{transfer_id}")
async def update_owner_transfer_request(
        transfer_id: str,
        transfer_update: UpdateOwnerTransferRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Edit a pending owner transfer request before any approval has been recorded.
    The original submitter may edit their own request; reviewer roles may edit any
    still-pending request.
    """
    transfer = await db.owner_transfer_requests.find_one(
        {"id": transfer_id, "building_id": building_id}, {"_id": 0}
    )
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer request not found")

    if transfer["status"] != "pending" or transfer.get("current_approvals", 0) > 0:
        raise HTTPException(
            status_code=400,
            detail="Only untouched pending owner transfers can be edited",
        )

    can_edit = transfer.get("submitted_by_id") == current_user["id"] or _can_review_owner_transfer(current_user)
    if not can_edit:
        raise HTTPException(status_code=403, detail="Not authorized to edit this transfer")

    next_unit_number = (transfer_update.unit_number or transfer["unit_number"]).strip()
    if not next_unit_number:
        raise HTTPException(status_code=400, detail="Unit number is required")

    if not _can_staff_initiate_owner_transfer(current_user):
        requester_ownership = await db.user_units.find_one(
            {
                "building_id": building_id,
                "unit_number": next_unit_number,
                "user_id": current_user["id"],
                "role_at_unit": "owner",
                "is_active": True,
            }
        )
        if not requester_ownership:
            raise HTTPException(
                status_code=403,
                detail="Only current owners or authorised staff can edit owner transfers for this unit",
            )

    next_owner = await _resolve_owner_transfer_new_owner(
        transfer_update.new_owner_id or transfer["new_owner"]["user_id"],
        transfer_update.new_owner_email,
    )
    next_old_owners = await _get_owner_transfer_current_owner_info(
        building_id, next_unit_number
    )

    if any(owner["user_id"] == next_owner["id"] for owner in next_old_owners):
        raise HTTPException(
            status_code=400,
            detail="The selected new owner is already an active owner for this unit",
        )

    today = datetime.now(timezone.utc).isoformat()
    updated_fields = {
        "unit_number": next_unit_number,
        "old_owners": next_old_owners,
        "new_owner": {
            "user_id": next_owner["id"],
            "full_name": next_owner.get("full_name")
                         or f"{next_owner.get('first_name', '')} {next_owner.get('last_name', '')}".strip(),
            "email": next_owner.get("email"),
        },
        "settlement_date": (
            transfer_update.settlement_date
            if transfer_update.settlement_date is not None
            else transfer.get("settlement_date")
        ),
        "request_notes": (
            transfer_update.request_notes
            if transfer_update.request_notes is not None
            else transfer.get("request_notes")
        ),
        "updated_at": today,
    }
    if transfer_update.ownership_documents is not None:
        updated_fields["ownership_documents"] = transfer_update.ownership_documents

    await db.owner_transfer_requests.update_one(
        {"id": transfer_id, "building_id": building_id},
        {"$set": updated_fields},
    )

    return {
        "message": "Owner transfer request updated",
        "status": "pending",
    }


@api_router.put("/owner-transfers/{transfer_id}")
async def process_owner_transfer(
        transfer_id: str,
        process_request: ProcessOwnerTransferRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Approve or reject owner transfer request. Scoped to building.
    Actions: 'approve_keep_old', 'approve_remove_old', 'reject'
    """
    if not _can_review_owner_transfer(current_user):
        raise HTTPException(
            status_code=403, detail="Not authorized to process transfers"
        )

    action = process_request.action
    review_notes = process_request.review_notes
    remove_owner_ids = process_request.remove_owner_ids

    if action not in ["approve_keep_old", "approve_remove_old", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    # Get transfer request
    transfer = await db.owner_transfer_requests.find_one(
        {"id": transfer_id, "building_id": building_id}, {"_id": 0}
    )
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer request not found")

    if transfer["status"] not in OWNER_TRANSFER_PENDING_STATUSES:
        raise HTTPException(status_code=400, detail="Transfer already processed")

    today = datetime.now(timezone.utc).isoformat()
    review_entry = _build_owner_transfer_review_entry(
        current_user, action, review_notes, today
    )
    approval_history = list(transfer.get("approval_history", []))

    if action == "reject":
        if transfer.get("submitted_by_id") == current_user["id"]:
            raise HTTPException(
                status_code=400,
                detail="Request submitters cannot reject their own owner transfer",
            )
        approval_history.append(review_entry)
        await db.owner_transfer_requests.update_one(
            {"id": transfer_id, "building_id": building_id},
            {
                "$set": {
                    "status": "rejected",
                    "reviewed_by": current_user["id"],
                    "reviewed_by_name": current_user["full_name"],
                    "reviewed_date": today,
                    "review_notes": review_notes,
                    "action_taken": action,
                    "approval_history": approval_history,
                    "updated_at": today,
                }
            },
        )
        submitter_id = transfer.get("submitted_by_id")
        if submitter_id:
            asyncio.create_task(create_user_notification(
                user_id=submitter_id,
                title="Owner Transfer Request Rejected",
                message=f"Your transfer request for Unit {transfer['unit_number']} has been rejected. "
                        + (
                            f"Reason: {review_notes}" if review_notes else "Please contact building management for details."),
                notification_type="owner_transfer",
                link="/admin/owner-transfers",
                building_id=building_id,
            ))
        return {"message": "Transfer request rejected", "status": "rejected"}

    if any(entry.get("user_id") == current_user["id"] for entry in approval_history):
        raise HTTPException(
            status_code=400,
            detail="You have already reviewed this owner transfer",
        )

    reviewer_role = _effective_role(current_user)
    pending_action = transfer.get("pending_approval_action")
    if pending_action and pending_action != action and reviewer_role in OWNER_TRANSFER_EC_APPROVER_ROLES:
        raise HTTPException(
            status_code=400,
            detail="The second EC approval must use the same approval action as the first EC approval",
        )

    approval_history.append(review_entry)

    if reviewer_role in OWNER_TRANSFER_EC_APPROVER_ROLES:
        ec_approval_count = sum(
            1
            for entry in approval_history
            if entry.get("role") in OWNER_TRANSFER_EC_APPROVER_ROLES
            and entry.get("action") in {"approve_keep_old", "approve_remove_old"}
        )
        if ec_approval_count < 2:
            await db.owner_transfer_requests.update_one(
                {"id": transfer_id, "building_id": building_id},
                {
                    "$set": {
                        "status": "pending_second_approval",
                        "required_approvals": 2,
                        "current_approvals": ec_approval_count,
                        "approval_mode": "ec_dual",
                        "pending_approval_action": action,
                        "review_notes": review_notes,
                        "reviewed_by": current_user["id"],
                        "reviewed_by_name": current_user["full_name"],
                        "reviewed_date": today,
                        "approval_history": approval_history,
                        "updated_at": today,
                    }
                },
            )
            asyncio.create_task(broadcast_user_notification(
                recipient_roles=list(OWNER_TRANSFER_EC_APPROVER_ROLES),
                title="Second EC Approval Required",
                message=f"A transfer request for Unit {transfer['unit_number']} has received its first EC approval "
                        f"and is waiting for a second distinct EC approver.",
                notification_type="owner_transfer",
                link="/admin/owner-transfers",
                building_id=building_id,
            ))
            return {
                "message": "First EC approval recorded. A second EC approval is required before the transfer is processed.",
                "status": "pending_second_approval",
                "action": action,
                "required_approvals": 2,
                "current_approvals": ec_approval_count,
            }

        transfer["required_approvals"] = 2
        transfer["current_approvals"] = ec_approval_count
        transfer["approval_mode"] = "ec_dual"
        transfer["pending_approval_action"] = action
        return await _finalize_owner_transfer_approval(
            transfer,
            action,
            review_notes,
            remove_owner_ids,
            current_user,
            building_id,
            today,
            approval_history,
        )

    transfer["required_approvals"] = 1
    transfer["current_approvals"] = 1
    transfer["approval_mode"] = "manager"
    transfer["pending_approval_action"] = action
    return await _finalize_owner_transfer_approval(
        transfer,
        action,
        review_notes,
        remove_owner_ids,
        current_user,
        building_id,
        today,
        approval_history,
    )


@api_router.post("/tenant-renewals")
async def create_tenant_renewal_request(
        renewal: TenantRenewalRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Create a tenant renewal request. Scoped to building.
    Tenant can request renewal before expiration
    """
    user_unit_id = renewal.user_unit_id
    new_lease_document_id = renewal.new_lease_document_id
    requested_duration_days = renewal.requested_duration_days

    # Get the user-unit relationship
    user_unit = await db.user_units.find_one({"id": user_unit_id, "building_id": building_id}, {"_id": 0})
    if not user_unit:
        raise HTTPException(status_code=404, detail="Tenant relationship not found")

    if user_unit["role_at_unit"] != "tenant":
        raise HTTPException(status_code=400, detail="Only tenants can request renewal")

    if user_unit["user_id"] != current_user["id"]:
        raise HTTPException(
            status_code=403, detail="Not authorized to renew this tenancy"
        )

    # Safely parse expiration date
    expiration_str = user_unit.get("expiration_date")
    original_expiration = parse_datetime_safe(expiration_str, "expiration_date")

    # Calculate new expiration date
    from datetime import timedelta

    new_expiration = original_expiration + timedelta(days=requested_duration_days)

    # Find landlord (unit owner)
    landlord = await db.user_units.find_one(
        {
            "building_id": building_id,
            "unit_number": user_unit["unit_number"],
            "role_at_unit": "owner",
            "is_active": True,
        },
        {"_id": 0},
    )

    landlord_id = landlord["user_id"] if landlord else None

    # Create renewal request
    renewal_id = str(uuid.uuid4())
    today = datetime.now(timezone.utc).isoformat()

    renewal_request = {
        "id": renewal_id,
        "building_id": building_id,
        "user_id": current_user["id"],
        "tenant_name": current_user["full_name"],
        "tenant_email": current_user["email"],
        "unit_number": user_unit["unit_number"],
        "original_start_date": user_unit["start_date"],
        "original_expiration_date": user_unit["expiration_date"],
        "current_lease_document_id": user_unit.get("lease_document_id"),
        "requested_renewal_date": today,
        "requested_duration_days": requested_duration_days,
        "new_expiration_date": new_expiration.isoformat(),
        "new_lease_document_id": new_lease_document_id,
        "new_lease_terms": None,
        "status": "pending",
        "reviewed_by": None,
        "reviewed_date": None,
        "approval_notes": None,
        "rejection_reason": None,
        "landlord_user_id": landlord_id,
        "landlord_notified": False,
        "tenant_notified": False,
        "created_at": today,
        "updated_at": today,
    }

    await db.tenant_renewal_requests.insert_one(renewal_request)

    return {
        "message": "Renewal request submitted",
        "id": renewal_id,
        "new_expiration_date": new_expiration.isoformat(),
    }


@api_router.get("/tenant-renewals")
async def get_tenant_renewal_requests(
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get tenant renewal requests. Scoped to building.
    Admin/Chairman see all, tenants see their own, landlords see for their units
    """
    permissions = get_user_permissions(current_user)
    query = {"building_id": building_id}
    if status:
        query["status"] = status

    # Admin/Chairman see all
    if permissions.can_manage_requests:
        renewals = (
            await db.tenant_renewal_requests.find(query, {"_id": 0})
            .sort("requested_renewal_date", -1)
            .to_list(100)
        )
        return renewals

    # Tenants see their own
    if current_user["role"] == "tenant":
        query["user_id"] = current_user["id"]
        renewals = (
            await db.tenant_renewal_requests.find(query, {"_id": 0})
            .sort("requested_renewal_date", -1)
            .to_list(100)
        )
        return renewals

    # Owners see renewals for their units
    if current_user["role"] == "owner":
        # Get units owned by this user for this building
        owned_units = await db.user_units.find(
            {"building_id": building_id, "user_id": current_user["id"], "role_at_unit": "owner", "is_active": True},
            {"_id": 0, "unit_number": 1},
        ).to_list(20)

        unit_numbers = [u["unit_number"] for u in owned_units]
        query["unit_number"] = {"$in": unit_numbers}
        renewals = (
            await db.tenant_renewal_requests.find(query, {"_id": 0})
            .sort("requested_renewal_date", -1)
            .to_list(100)
        )
        return renewals

    return []


@api_router.put("/tenant-renewals/{renewal_id}")
async def process_tenant_renewal(
        renewal_id: str,
        process_request: ProcessTenantRenewalRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Approve or reject tenant renewal request. Scoped to building.
    Actions: 'approve', 'reject'
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(
            status_code=403, detail="Not authorized to process renewals"
        )

    action = process_request.action
    notes = process_request.review_notes
    new_expiration_date = process_request.custom_expiration_date

    if action not in ["approve", "reject"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    # Get renewal request
    renewal = await db.tenant_renewal_requests.find_one({"id": renewal_id, "building_id": building_id}, {"_id": 0})
    if not renewal:
        raise HTTPException(status_code=404, detail="Renewal request not found")

    if renewal["status"] != "pending":
        raise HTTPException(status_code=400, detail="Renewal already processed")

    today = datetime.now(timezone.utc).isoformat()

    if action == "reject":
        # Update renewal request status
        await db.tenant_renewal_requests.update_one(
            {"id": renewal_id, "building_id": building_id},
            {
                "$set": {
                    "status": "rejected",
                    "reviewed_by": current_user["id"],
                    "reviewed_date": today,
                    "rejection_reason": notes,
                    "updated_at": today,
                }
            },
        )
        return {"message": "Renewal request rejected", "status": "rejected"}

    # Approve renewal
    expiration_date = (
        new_expiration_date if new_expiration_date else renewal["new_expiration_date"]
    )

    # Find the user-unit relationship
    user_unit = await db.user_units.find_one(
        {
            "building_id": building_id,
            "user_id": renewal["user_id"],
            "unit_number": renewal["unit_number"],
            "role_at_unit": "tenant",
        },
        {"_id": 0},
    )

    if user_unit:
        # Update expiration date
        await db.user_units.update_one(
            {"id": user_unit["id"], "building_id": building_id},
            {
                "$set": {
                    "expiration_date": expiration_date,
                    "lease_document_id": renewal.get("new_lease_document_id")
                                         or user_unit.get("lease_document_id"),
                    "updated_at": today,
                }
            },
        )

        # Reset renewal flag - this is global per user, but maybe it should be membership-specific
        await db.users.update_one(
            {"id": renewal["user_id"]},
            {"$set": {"requires_renewal": False, "renewal_reminder_sent": False}},
        )

    # Update renewal request
    await db.tenant_renewal_requests.update_one(
        {"id": renewal_id, "building_id": building_id},
        {
            "$set": {
                "status": "approved",
                "reviewed_by": current_user["id"],
                "reviewed_date": today,
                "approval_notes": notes,
                "updated_at": today,
            }
        },
    )

    return {
        "message": "Renewal approved",
        "status": "approved",
        "new_expiration_date": expiration_date,
    }


@api_router.get("/admin/expired-users")
async def get_expired_users(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. Note the inline check below still admits
        # ec_member; the capability does not, and the additive guard means the
        # tighter of the two decides. That is the settled "EC member is not user
        # administration" position, applied here rather than argued about.
        _cap: dict = Depends(require_capability("building.people.view", building_from_context=True)),
):
    """
    Get list of expired tenants and guests. Scoped to building.
    """
    if _effective_role(current_user) not in ["super_admin", "strata_admin", "ec_member", "strata_manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    today = datetime.now(timezone.utc).isoformat()

    # Performance Optimization⚡: Parallelized lookups and batch-fetched related data to eliminate N+1 query pattern.
    # This reduces potential database round-trips from O(N) to O(1).
    tasks = [
        db.user_units.find(
            {
                "building_id": building_id,
                "expiration_date": {"$lt": today},
                "is_active": True,
                "role_at_unit": "tenant",
            },
            {"_id": 0},
        )
        .sort("expiration_date", 1)
        .to_list(200),
        db.user_units.find(
            {"building_id": building_id, "end_date": {"$lt": today}, "is_active": True, "role_at_unit": "guest"},
            {"_id": 0},
        )
        .sort("end_date", 1)
        .to_list(200),
    ]

    # Performance Optimization⚡: Parallelize initial expiration lookups
    # expired_tenants_task = db.user_units.find({
    #     "expiration_date": {"$lt": today},
    #     "is_active": True,
    #     "role_at_unit": "tenant"
    # }, {"_id": 0}).sort("expiration_date", 1).to_list(200)

    # expired_guests_task = db.user_units.find({
    #     "end_date": {"$lt": today},
    #     "is_active": True,
    #     "role_at_unit": "guest"
    # }, {"_id": 0}).sort("end_date", 1).to_list(200)

    # expired_tenants, expired_guests = await asyncio.gather(expired_tenants_task, expired_guests_task)

    expired_tenants, expired_guests = await asyncio.gather(*tasks)
    expired = expired_tenants + expired_guests
    if not expired:
        return []

    # Performance Optimization⚡: Eliminate N+1 patterns by batch fetching users and renewal requests
    # Removed redundant duplicate fetch block.
    user_ids = list(set(rel["user_id"] for rel in expired))
    users_task = db.users.find({"id": {"$in": user_ids}}, {"_id": 0}).to_list(
        len(user_ids)
    )

    # Collect unique filters for renewal request checking
    renewal_filters = [
        {
            "user_id": rel["user_id"],
            "unit_number": rel["unit_number"],
            "status": "pending",
        }
        for rel in expired
        if rel["role_at_unit"] == "tenant"
    ]

    if renewal_filters:
        # Use precise $or filter to match specific user-unit combinations
        renewals_task = db.tenant_renewal_requests.find(
            {"$or": renewal_filters}, {"user_id": 1, "unit_number": 1}
        ).to_list(len(renewal_filters))
        users_list, renewals_list = await asyncio.gather(users_task, renewals_task)
    else:
        users_list = await users_task
        renewals_list = []

    # Create maps for efficient lookup
    user_map = {u["id"]: u for u in users_list}
    renewal_map = {(r["user_id"], r["unit_number"]): True for r in renewals_list}

    # Enrich with user data
    expired_users = []
    for rel in expired:
        user = user_map.get(rel["user_id"])
        if user:
            # Get the appropriate expiration field
            expiry_field = (
                "expiration_date" if rel["role_at_unit"] == "tenant" else "end_date"
            )
            expiry_value = rel.get(expiry_field)

            if expiry_value:
                # Calculate days since expiration
                exp_date = parse_datetime_safe(expiry_value, expiry_field)
                days_since = (datetime.now(timezone.utc) - exp_date).days

                # Check for renewal request (only for tenants)
                has_renewal = renewal_map.get(
                    (rel["user_id"], rel["unit_number"]), False
                )
                # has_renewal = (rel["user_id"], rel["unit_number"]) in renewal_set

                expired_users.append(
                    {
                        "user_unit_id": rel["id"],
                        "user_id": rel["user_id"],
                        "full_name": user["full_name"],
                        "email": user["email"],
                        "unit_number": rel["unit_number"],
                        "role_at_unit": rel["role_at_unit"],
                        "expiration_date": expiry_value,
                        "days_since_expiration": days_since,
                        "has_renewal_request": has_renewal,
                        "is_active": user.get("is_active", True),
                    }
                )

    return expired_users


class ReactivateUserRequest(BaseModel):
    user_unit_id: str
    new_expiration_date: str
    reason: Optional[str] = None


@api_router.post("/admin/reactivate-user")
async def reactivate_expired_user(
        data: ReactivateUserRequest,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
        # GAP-SEC-005 group 1. Reactivating an expired account is people
        # management, not onboarding review.
        _cap: dict = Depends(require_capability("building.people.manage", building_from_context=True)),
):
    """Generated function header.

    Function: reactivate_expired_user
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    user_unit_id = data.user_unit_id
    new_expiration_date = data.new_expiration_date
    reason = data.reason
    """
    Manually reactivate an expired user. Scoped to building.
    Requires new expiration date
    """
    if _effective_role(current_user) not in ["super_admin", "ec_member", "strata_admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get user-unit relationship
    user_unit = await db.user_units.find_one({"id": user_unit_id, "building_id": building_id}, {"_id": 0})
    if not user_unit:
        raise HTTPException(status_code=404, detail="User-unit relationship not found")

    today = datetime.now(timezone.utc).isoformat()

    # Validate new expiration date is in future (with Z handling)
    new_exp = parse_datetime_safe(new_expiration_date, "new_expiration_date")
    if new_exp <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400, detail="New expiration date must be in the future"
        )

    # Determine which field to update based on role
    expiry_field = (
        "expiration_date" if user_unit["role_at_unit"] == "tenant" else "end_date"
    )

    # Update user-unit relationship
    await db.user_units.update_one(
        {"id": user_unit_id, "building_id": building_id},
        {
            "$set": {
                expiry_field: new_expiration_date,
                "is_active": True,
                "updated_at": today,
            }
        },
    )

    # Reactivate user account (global, but relationship is building-specific)
    await db.users.update_one(
        {"id": user_unit["user_id"]},
        {
            "$set": {
                "is_active": True,
                "requires_renewal": False,
                "renewal_reminder_sent": False,
            }
        },
    )

    return {
        "message": "User reactivated successfully",
        "user_id": user_unit["user_id"],
        "new_expiration_date": new_expiration_date,
        "reason": reason,
    }


@api_router.put("/units/{unit_id}", response_model=UnitEntitlementResponse)
async def update_unit(
        unit_id: str,
        data: UnitEntitlementCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Generated function header.

    Function: update_unit
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = data.model_dump()
    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Sentinel 🛡️: Enforce BOLA protection by scoping update to the current building context
    update_result = await db.units.update_one(
        {"id": unit_id, "building_id": building_id},
        {"$set": update_dict},
    )
    if update_result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Unit not found in this building")

    unit = await db.units.find_one({"id": unit_id, "building_id": building_id}, {"_id": 0})
    return UnitEntitlementResponse(**unit)


# ==================== RESIDENT DIRECTORY ====================
# @featuretrace:resident-directory-chat — Directory listing and click-to-chat entrypoint.
# Layer: router
# Data flow: ResidentDirectoryPage -> GET /directory -> db.memberships + db.users + db.units (building-scoped).
# Related: frontend/src/pages/dashboard/ResidentDirectoryPage.tsx, backend/routers/communication.py


@api_router.get("/directory")
async def get_resident_directory(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get the resident directory. Scoped to building.

    Performance Optimization⚡: Consolidated 2 database round-trips into 1 single
    aggregation pipeline starting from memberships. This eliminates the need
    to fetch and pass a potentially large list of user_ids.
    """
    pipeline = [
        # 1. Start with memberships to ensure strict building context
        {"$match": {"building_id": building_id, "is_active": True}},

        # 2. Join with user profiles
        {"$lookup": {
            "from": "users",
            "localField": "user_id",
            "foreignField": "id",
            "as": "user_info"
        }},
        {"$unwind": "$user_info"},

        # 3. Filter for active users who opted into the directory
        {"$match": {
            "user_info.is_active": True,
            "user_info.unit_number": {"$exists": True, "$nin": [None, ""]},
            "user_info.email": {"$ne": SYSTEM_HIDDEN_EMAIL},
            "$or": [
                {"user_info.directory_visible": True},
                {"user_info.directory_visible": {"$exists": False}},
            ]
        }},

        # 4. Join with unit details (filtered by building_id for security)
        {"$lookup": {
            "from": "units",
            "let": {"u_num": "$user_info.unit_number"},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$unit_number", "$$u_num"]},
                            {"$eq": ["$building_id", building_id]}
                        ]
                    }
                }}
            ],
            "as": "unit_details"
        }},
        {"$match": {"unit_details": {"$ne": []}}},
        {"$addFields": {"unit": {"$arrayElemAt": ["$unit_details", 0]}}},

        # 5. Project fields according to visibility preferences and schema
        {"$project": {
            "_id": 0,
            "id": "$user_info.id",
            # Keep chat_user_id explicit so future directory display IDs can
            # diverge without breaking the legacy Mongo conversation API.
            "chat_user_id": "$user_info.id",
            "full_name": "$user_info.full_name",
            "building_id": building_id,
            "unit_number": "$user_info.unit_number",
            "unit_type": "$unit.unit_type",
            "move_in_date": "$user_info.move_in_date",
            "entitlement_units": {"$ifNull": ["$unit.entitlement", "$unit.entitlement_units"]},
            "annual_levy": "$unit.annual_levy",
            "email": {"$cond": [{"$eq": ["$user_info.email_visible", True]}, "$user_info.email", "$$REMOVE"]},
            "phone": {"$cond": [{"$eq": ["$user_info.phone_visible", True]}, "$user_info.phone", "$$REMOVE"]}
        }},

        # 6. Sort by unit number
        {"$sort": {"unit_number": 1}}
    ]

    results = await _server_agg(db.memberships, pipeline, 1000)

    # Mask PII during impersonation
    if "impersonator_id" in current_user:
        for res in results:
            res["full_name"] = "Resident"
            if res.get("email"):
                res["email"] = mask_email(res["email"])
            if res.get("phone"):
                res["phone"] = mask_phone(res["phone"])

    return results


@api_router.put("/directory/settings")
async def update_directory_settings(
        directory_visible: bool = True,
        email_visible: bool = False,
        phone_visible: bool = False,
        move_in_date: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update directory settings. Scoped to building."""
    update_dict = {
        "directory_visible": directory_visible,
        "email_visible": email_visible,
        "phone_visible": phone_visible,
    }
    if move_in_date:
        update_dict["move_in_date"] = move_in_date

    await db.users.update_one({"id": current_user["id"]}, {"$set": update_dict})
    return {"message": "Directory settings updated"}


# ==================== AGM VOTING ====================


@api_router.post("/agm/trigger-alert")
async def trigger_agm_alert(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Trigger alert after AGM completion. Scoped to building."""
    # Authorized roles for triggering AGM alerts
    if _effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get building info for the email
    building = await db.buildings.find_one({"id": building_id})
    building_name = building.get("name", "Building") if building else "Building"

    # Send email to Super Admin
    admin_email = "admin@silverfoxtechnologies.com.au"  # System admin email
    subject = f"AGM Completed - {building_name} - EC Details Update Required"
    safe_full_name = html_lib.escape(current_user["full_name"])
    html_content = f"""
    <h2>AGM Completed</h2>
    <p>Hello Admin,</p>
    <p>An AGM for <strong>{building_name}</strong> has been marked as completed by {safe_full_name}.</p>
    <p>Please log in to the system to update the Executive Committee member details as per the latest election results.</p>
    <p>Thank you,<br>Silverfox Technologies Platform</p>
    """

    await send_email_async(admin_email, subject, html_content)

    # Create notification for Super Admin
    super_admins = await db.users.find(
        {"role": UserRole.SUPER_ADMIN, "is_active": True, "status": {"$ne": "archived"}}
    ).to_list(10)
    for admin in super_admins:
        await create_user_notification(
            user_id=admin["id"],
            building_id=building_id,
            title="Update EC Members",
            message=f"AGM completed for {building_name}. Please update Executive Committee members.",
            notification_type="general",
            link="/about"
        )

    return {"message": "Alert sent successfully"}


@api_router.post("/agm", response_model=AGMResponse)
async def create_agm(
        data: AGMCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new AGM. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    agm_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    agm_data = data.model_dump()
    agm_data["title"] = html_lib.escape(agm_data.get("title", ""))
    agm_data["location"] = html_lib.escape(agm_data.get("location", ""))
    if agm_data.get("agenda"):
        agm_data["agenda"] = [html_lib.escape(a) for a in agm_data["agenda"]]

    agm_doc = {
        "id": agm_id,
        "building_id": building_id,
        **agm_data,
        "status": "upcoming",
        "motions": [],
        "created_at": now,
    }

    await db.agm.insert_one(agm_doc)
    return AGMResponse(**agm_doc)


@api_router.get("/agm", response_model=List[AGMResponse])
async def get_agms(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List AGMs for the building."""
    # Resilient list build lives in models.community.coalesce_agm_response (shared
    # with routers/meetings.py) so a single legacy/dirty AGM doc — e.g. a null
    # agenda, which both 500'd this endpoint and surfaced client-side as "can't
    # access property 'length'" — never takes down the whole /governance/agm page.
    # Kept out of server.py's body deliberately (see the GAP-NAV consolidation task).
    from models.community import coalesce_agm_response
    agms = await db.agm.find({"building_id": building_id}, {"_id": 0}).sort("date", -1).to_list(20)
    return [r for a in agms if (r := coalesce_agm_response(a)) is not None]


@api_router.post("/agm/{agm_id}/motions", response_model=AGMMotionResponse)
async def create_motion(
        agm_id: str,
        data: AGMMotionCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Create a motion for an AGM. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Verify AGM belongs to this building
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")

    motion_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    motion_doc = {
        "id": motion_id,
        "building_id": building_id,
        "agm_id": agm_id,
        "title": html_lib.escape(data.title),
        "description": nh3.clean(data.description),
        "motion_type": html_lib.escape(data.motion_type),
        "votes_for": 0,
        "votes_against": 0,
        "votes_abstain": 0,
        "status": "pending",
        "voters": [],
        "created_at": now,
    }

    await db.agm_motions.insert_one(motion_doc)
    return AGMMotionResponse(**motion_doc)


@api_router.get("/agm/{agm_id}/motions", response_model=List[AGMMotionResponse])
async def get_motions(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List motions for an AGM. Scoped to building."""
    motions = await db.agm_motions.find({"agm_id": agm_id, "building_id": building_id}, {"_id": 0}).to_list(50)
    return [AGMMotionResponse(**m) for m in motions]


# @featuretrace:evoting — AGM ballot cast: BOLA-guarded per-lot vote with NSW proxy cap enforcement.
# Layer: router
# Data flow: frontend/src/pages/dashboard/AGMPage.jsx → POST /agm/motions/{id}/vote
#             → db.agm_votes ($setOnInsert), db.agm_motions ($inc) (building-scoped)
# Related: backend/routers/voting.py (proxy submit/revoke, quorum, close-ballot, evidence-pack)
#           backend/services/proxy_validator.py (cap enforcement)
#           backend/domain/jurisdictional_rules.py (NSW cap thresholds from nsw.json)
# Toggle: evoting
# Collection: agm_votes, agm_motions, agm, agm_attendance, user_units, units, buildings
@api_router.post("/agm/motions/{motion_id}/vote")
async def cast_vote(
        motion_id: str,
        data: AGMVoteCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Cast a vote on an AGM motion. Scoped to building.
    Sentinel 🛡️: Enforces BOLA protection and lot-based voting integrity.
    """
    allowed_votes = ["for", "against", "abstain"]
    if data.vote not in allowed_votes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid vote. Must be one of: {', '.join(allowed_votes)}",
        )

    motion = await db.agm_motions.find_one({"id": motion_id, "building_id": building_id}, {"_id": 0})
    if not motion:
        raise HTTPException(status_code=404, detail="Motion not found")

    agm_id = motion["agm_id"]
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id}, {"_id": 0})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")

    # Determine the target unit for this vote
    target_unit = data.proxy_for or current_user.get("unit_number")
    if not target_unit:
        raise HTTPException(status_code=400, detail="Target unit number required for voting")

    # Sentinel 🛡️: BOLA Protection — Verify that the voter is authorized for the target unit.
    if _effective_role(current_user) != UserRole.SUPER_ADMIN:
        # 1. Check direct ownership
        is_owner = await db.user_units.find_one({
            "building_id": building_id,
            "user_id": current_user["id"],
            "unit_number": target_unit,
            "is_active": True,
            "$or": [
                {"role_at_unit": "owner"},
                {"role_at_unit": {"$exists": False}},
                {"role_at_unit": None},
            ],
        })

        authorized = bool(is_owner)

        # 2. Check proxy authorization if not the direct owner.
        # Find all owners of target_unit first, then check if any of them granted proxy to
        # the current user. This handles units with multiple owners correctly — find_one on
        # proxy_id alone would return an arbitrary grantor and reject valid proxy votes.
        if not authorized:
            target_unit_owners = await db.user_units.find({
                "building_id": building_id,
                "unit_number": target_unit,
                "is_active": True,
                "$or": [
                    {"role_at_unit": "owner"},
                    {"role_at_unit": {"$exists": False}},
                    {"role_at_unit": None},
                ],
            }).to_list(length=None)
            owner_ids = [o["user_id"] for o in target_unit_owners if o.get("user_id")]

            if owner_ids:
                proxy_authorized = await db.agm_attendance.find_one({
                    "building_id": building_id,
                    "agm_id": agm_id,
                    "proxy_id": current_user["id"],
                    "user_id": {"$in": owner_ids},
                })
                if proxy_authorized:
                    authorized = True

        if not authorized:
            raise HTTPException(
                status_code=403,
                detail=f"You are not authorized to vote for Unit {target_unit}"
            )

    # F-3 FIX: AGM status gate runs BEFORE the proxy cap check so a closed-AGM
    # submission receives the correct 400 ("AGM not in progress"), not a misleading 422.
    # Live voting only allowed when AGM is in progress; pre-voting allowed for upcoming.
    if not data.is_pre_vote and agm.get("status") != "in_progress":
        raise HTTPException(
            status_code=400, detail="Live voting only allowed when AGM is in progress"
        )
    if data.is_pre_vote and agm.get("status") not in ["upcoming", "in_progress"]:
        raise HTTPException(
            status_code=400, detail="Pre-voting only allowed before or during the AGM"
        )

    # GAP-GOV-001: NSW proxy cap check at vote time (belt-and-suspenders).
    # Enforced at proxy-submission time too (proxy_validator.py); this re-check
    # catches any concurrent-submission races.  Only runs when proxy_for is set
    # (proxy holder casting on behalf of a lot owner).
    #
    # F-1 FIX: Guard against None returns from rule_engine methods — the contract
    #   allows None when a jurisdiction has no cap configured.  Treat None as
    #   "no cap applies" (skip) rather than letting it blow up with TypeError.
    # F-2 FIX: When grantor_ids is EMPTY (no user_units records for the target
    #   unit, e.g. Excel-imported lots), fall back to $nin: [] (no exclusions —
    #   i.e. count all holder proxies for this AGM) rather than $exists: True.
    #   $nin: [] matches ALL documents (no element is "in" an empty set) — so every
    #   proxy held by this holder for this AGM is counted.  The $exists: True fallback
    #   was also semantically correct but required a comment explaining why; $nin: []
    #   is self-documenting.  NOTE: super_admin bypasses the BOLA block above and
    #   can reach here with an empty grantor_ids set — that is intentional; the
    #   cap still applies to super_admins acting as proxy holders (SSMA 2015 s.60
    #   does not exempt scheme officers from the numerical cap).
    if data.proxy_for:
        building = await db.buildings.find_one(
            {"id": building_id}, {"jurisdiction": 1, "_id": 0}
        )
        jurisdiction = (building.get("jurisdiction") or "ACT").upper() if building else "ACT"
        if jurisdiction == "NSW":
            from domain.jurisdictional_rules import rule_engine as _rule_engine
            total_lots = await db.units.count_documents({"building_id": building_id})
            if total_lots > 0:
                size_threshold = _rule_engine.proxy_cap_scheme_size_threshold("NSW")
                cap: int | None = None
                cap_desc: str = ""

                if total_lots < size_threshold:
                    # Small-scheme rule (< 20 lots): max 1 proxy per holder.
                    raw_small = _rule_engine.proxy_cap_small_scheme_lots("NSW")
                    if raw_small is not None:  # F-1: skip if jurisdiction has no small-scheme cap
                        cap = int(raw_small)
                        cap_desc = (
                            f"small-scheme rule (< {size_threshold} lots): max {cap} proxy"
                        )
                else:
                    # Large-scheme rule (≥ 20 lots): max floor(total_lots × pct%) proxies.
                    raw_pct = _rule_engine.proxy_cap_scheme_pct("NSW")
                    if raw_pct is not None:  # F-1: skip if jurisdiction has no pct cap
                        # Use float() + / to match proxy_validator.py formula exactly.
                        # Decimal("5") → float 5.0; handles fractional % without truncation.
                        cap = max(1, int(total_lots * float(raw_pct) / 100))
                        cap_desc = (
                            f"{raw_pct}% of {total_lots} lots = {cap} proxy slot(s)"
                        )

                if cap is not None:
                    # Resolve the owners of the unit being voted for so we can
                    # EXCLUDE this grantor from the existing-proxies count.
                    # (The holder may already hold a proxy for this same lot from
                    # a prior submission; counting it would wrongly trigger the cap.)
                    grantor_unit_owners = await db.user_units.find({
                        "building_id": building_id,
                        "unit_number": data.proxy_for,
                        "is_active": True,
                        "$or": [
                            {"role_at_unit": "owner"},
                            {"role_at_unit": {"$exists": False}},
                            {"role_at_unit": None},
                        ],
                    }).to_list(length=None)
                    grantor_ids = [
                        o["user_id"]
                        for o in grantor_unit_owners
                        if o.get("user_id")
                    ]

                    # F-2 FIX: Use $nin with grantor_ids (may be []).
                    # $nin: [] matches all documents (no exclusions) — correct when
                    # the target unit has no registered owners (Excel-imported lot).
                    existing_count = await db.agm_attendance.count_documents({
                        "building_id": building_id,
                        "agm_id": agm_id,
                        "proxy_id": current_user["id"],
                        "user_id": {"$nin": grantor_ids},
                    })
                    if existing_count >= cap:
                        # `existing_count >= cap` is the correct threshold:
                        # `existing_count` excludes this grantor, so hitting `cap`
                        # means adding this proxy would give the holder cap+1 total
                        # (or cap if the grantor already has a proxy with this holder).
                        # Either way the statutory limit is met or exceeded.
                        raise HTTPException(
                            status_code=422,
                            detail=(
                                f"Proxy cap exceeded: "
                                f"'{current_user.get('full_name', current_user['id'])}' "
                                f"already holds proxies for {existing_count} lot(s). "
                                f"NSW cap ({cap_desc}). "
                                "Strata Schemes Management Act 2015 s.60."
                            ),
                        )

    now = datetime.now(timezone.utc).isoformat()

    # Record individual vote for audit trail
    vote_doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "motion_id": motion_id,
        "agm_id": agm_id,
        "user_id": current_user["id"],
        "user_name": current_user.get("full_name"),
        "unit_number": target_unit,
        "vote": data.vote,
        "proxy_for": data.proxy_for,
        "is_pre_vote": data.is_pre_vote,
        "created_at": now,
    }

    # Sentinel 🛡️: Atomic per-lot voting integrity — $setOnInsert upsert eliminates the
    # find_one → insert_one race condition that could allow duplicate votes under concurrency.
    result = await db.agm_votes.update_one(
        {"building_id": building_id, "motion_id": motion_id, "unit_number": target_unit},
        {"$setOnInsert": vote_doc},
        upsert=True
    )
    if result.upserted_id is None:
        raise HTTPException(status_code=409, detail=f"A vote has already been cast for Unit {target_unit}")

    # Update aggregate counts in motion doc
    vote_field = f"votes_{data.vote}"
    await db.agm_motions.update_one(
        {"id": motion_id, "building_id": building_id},
        {"$inc": {vote_field: 1}, "$push": {"voters": current_user["id"]}},
    )

    return {"message": "Vote recorded"}


@api_router.put("/agm/{agm_id}")
async def update_agm_status(
        agm_id: str,
        payload: dict,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Update AGM status (upcoming → in_progress → completed). Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    allowed_statuses = ["upcoming", "in_progress", "completed"]
    new_status = payload.get("status")
    if new_status and new_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(allowed_statuses)}",
        )

    update_dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if new_status:
        update_dict["status"] = new_status

    result = await db.agm.update_one({"id": agm_id, "building_id": building_id}, {"$set": update_dict})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="AGM not found")

    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id}, {"_id": 0})
    return AGMResponse(**agm)


@api_router.get("/agm/{agm_id}/attendance")
async def get_agm_attendance(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """
    Get attendance list for an AGM with enriched user details.
    Owners see all attendance records for their own unit (so co-owners can both vote).
    Only one vote per unit is counted (attending takes priority; most recent otherwise).
    Managers see all attendees for the entire building.
    """
    # 1. Validate AGM exists — scoped to building to prevent cross-tenant enumeration
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")

    manager_roles = [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER,
        UserRole.STRATA_MANAGER,
    ]
    _role = current_user.get("effective_role") or current_user.get("role", "guest")
    is_manager = _role in manager_roles

    # Build initial match query — building_id scoping enforced explicitly
    initial_query: dict = {"agm_id": agm_id, "building_id": building_id}

    # Performance Note: 'users.id' is a unique indexed field, ensuring O(1) lookup per record.
    # The pipeline is limited to 200 records to maintain consistent performance.
    pipeline: list = [
        {"$match": initial_query},
        # Join with users collection for attendee details
        {
            "$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_details",
            }
        },
        # Join with users collection for proxy details (if any)
        {
            "$lookup": {
                "from": "users",
                "localField": "proxy_id",
                "foreignField": "id",
                "as": "proxy_details",
            }
        },
        {
            "$addFields": {
                "user_info": {"$arrayElemAt": ["$user_details", 0]},
                "proxy_info": {"$arrayElemAt": ["$proxy_details", 0]},
            }
        },
        # Data Integrity: Filter out orphaned records where the referenced user no longer exists
        {"$match": {"user_info": {"$ne": None}}},
    ]

    # For non-managers: filter to own unit only (shows all co-owners of the same unit)
    if not is_manager:
        user_unit = current_user.get("unit_number")
        pipeline.append({"$match": {"user_info.unit_number": user_unit}})

    pipeline.append({
        "$project": {
            "_id": 0,
            "agm_id": 1,
            "user_id": 1,
            "status": 1,
            "proxy_id": 1,
            "confirmed_by_admin": 1,
            "confirmed_at": 1,
            "updated_at": 1,
            "full_name": "$user_info.full_name",
            "unit_number": "$user_info.unit_number",
            "proxy_name": "$proxy_info.full_name",
            "proxy_unit": "$proxy_info.unit_number",
        }
    })

    attendance = await _server_agg(db.agm_attendance, pipeline, 200)

    # Per-unit vote deduplication:
    # Each unit has exactly ONE vote regardless of how many owners registered attendance.
    # Rule: attending takes priority over apology; ties broken by most recently updated.
    from collections import defaultdict
    unit_groups: dict = defaultdict(list)
    for record in attendance:
        unit_groups[record.get("unit_number")].append(record)

    for unit_num, records in unit_groups.items():
        if len(records) == 1:
            records[0]["unit_vote_counted"] = True
        else:
            attending = [r for r in records if r.get("status") == "attending"]
            candidates = attending if attending else records
            # Mixed datetime/str across writers would raise TypeError here the moment
            # two candidates disagreed on shape — see models/timestamps.
            rep = max(candidates, key=lambda r: timestamp_sort_key(r.get("updated_at")))
            for r in records:
                r["unit_vote_counted"] = r is rep

    return attendance


@api_router.post("/agm/{agm_id}/attendance")
async def update_agm_attendance(
        agm_id: str,
        data: AGMAttendanceUpdate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Record or update attendance status for a user. Scoped to building."""
    manager_roles = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]
    if current_user["id"] != data.user_id and _effective_role(current_user) not in manager_roles:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update attendance for another user",
        )

    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id}, {"_id": 0})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")
    if agm.get("status") == "completed":
        raise HTTPException(
            status_code=400, detail="Cannot update attendance for a completed AGM"
        )

    allowed_statuses = ["attending", "apology"]
    if data.status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(allowed_statuses)}",
        )

    now = datetime.now(timezone.utc).isoformat()
    attendance_doc = {
        "agm_id": agm_id,
        "building_id": building_id,
        "user_id": data.user_id,
        "status": data.status,
        "proxy_id": data.proxy_id,
        "confirmed_by_admin": False,
        "updated_at": now,
    }

    await db.agm_attendance.update_one(
        {"agm_id": agm_id, "building_id": building_id, "user_id": data.user_id},
        {"$set": attendance_doc},
        upsert=True,
    )
    return {"message": "Attendance updated"}


@api_router.post("/agm/{agm_id}/attendance/{user_id}/confirm")
async def confirm_agm_attendance(
        agm_id: str,
        user_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Admin physically confirms a user is present at the AGM. Scoped to building."""
    manager_roles = [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER, UserRole.STRATA_MANAGER]
    if _effective_role(current_user) not in manager_roles:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.agm_attendance.update_one(
        {"agm_id": agm_id, "building_id": building_id, "user_id": user_id},
        {
            "$set": {
                "confirmed_by_admin": True,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    return {"message": "Attendance confirmed"}


@api_router.get("/agm/{agm_id}/results")
async def get_agm_results(
        agm_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get voting results. Super Admin sees individual votes; others see aggregated counts. Scoped to building."""
    # Verify AGM belongs to this building
    agm = await db.agm.find_one({"id": agm_id, "building_id": building_id})
    if not agm:
        raise HTTPException(status_code=404, detail="AGM not found")

    if current_user["role"] == UserRole.SUPER_ADMIN:
        motions = await db.agm_motions.find({"agm_id": agm_id}, {"_id": 0}).to_list(50)
        votes = await db.agm_votes.find({"agm_id": agm_id}, {"_id": 0}).to_list(1000)
        return {"detailed": True, "motions": motions, "votes": votes}

    motions = await db.agm_motions.find(
        {"agm_id": agm_id}, {"_id": 0, "voters": 0}
    ).to_list(50)
    return {"detailed": False, "motions": motions}


# ==================== BUILDING DEFECTS ====================
# POST /defects and GET /defects are handled by routers/defects_register.py (GAP-MNT-001).
# That router adds warranty clock tracking, category/severity filters, full CRUD,
# warranty-summary aggregate, notes, and photos endpoints.
# The legacy DefectCreate / DefectResponse models below remain for the
# PUT /defects/{id}/status endpoint until it is migrated to the new router.


@api_router.put("/defects/{defect_id}/status")
async def update_defect_status(
        defect_id: str,
        status: str,
        resolution_notes: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Update defect status. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    update_dict = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if resolution_notes:
        update_dict["resolution_notes"] = resolution_notes
    if status in ["resolved", "closed"]:
        update_dict["resolved_date"] = datetime.now(timezone.utc).isoformat()

    result = await db.defects.update_one({"id": defect_id, "building_id": building_id}, {"$set": update_dict})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Defect not found")

    return {"message": "Defect status updated"}


# ==================== MOVE IN/OUT BOOKINGS ====================


@api_router.post("/move-bookings", response_model=MoveBookingResponse)
async def create_move_booking(
        data: MoveBookingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Create a move in/out booking. Scoped to building."""
    booking_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    booking_data = data.model_dump()
    if data.moving_company:
        booking_data["moving_company"] = html_lib.escape(data.moving_company)
    if data.notes:
        booking_data["notes"] = nh3.clean(data.notes)

    booking_doc = {
        "id": booking_id,
        "building_id": building_id,
        **booking_data,
        "status": "pending",
        "created_by": current_user["id"],
        "created_at": now,
    }

    await db.move_bookings.insert_one(booking_doc)
    return MoveBookingResponse(**booking_doc)


@api_router.get("/move-bookings", response_model=List[MoveBookingResponse])
async def get_move_bookings(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List move bookings. Scoped to building."""
    permissions = get_user_permissions(current_user)

    if permissions.can_manage_requests:
        bookings = (
            await db.move_bookings.find({"building_id": building_id}, {"_id": 0})
            .sort("scheduled_date", -1)
            .to_list(100)
        )
    else:
        bookings = (
            await db.move_bookings.find({"building_id": building_id, "created_by": current_user["id"]}, {"_id": 0})
            .sort("scheduled_date", -1)
            .to_list(20)
        )

    return [MoveBookingResponse(**b) for b in bookings]


@api_router.put("/move-bookings/{booking_id}/status")
async def update_move_booking_status(
        booking_id: str,
        status: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Update move booking status. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.move_bookings.update_one(
        {"id": booking_id, "building_id": building_id},
        {"$set": {"status": status}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Booking not found")

    return {"message": "Booking status updated"}


# ==================== AMENITY BOOKINGS ====================


@api_router.post("/amenity-bookings", response_model=AmenityBookingResponse)
async def create_amenity_booking(
        data: AmenityBookingCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Create an amenity booking. Scoped to building."""
    # Check for conflicts
    existing = await db.amenity_bookings.find_one(
        {
            "building_id": building_id,
            "amenity_type": data.amenity_type,
            "date": data.date,
            "status": "confirmed",
            "$or": [
                {
                    "start_time": {"$lt": data.end_time},
                    "end_time": {"$gt": data.start_time},
                }
            ],
        }
    )

    if existing:
        raise HTTPException(status_code=400, detail="Time slot already booked")

    booking_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    booking_data = data.model_dump()
    if data.notes:
        booking_data["notes"] = nh3.clean(data.notes)

    booking_doc = {
        "id": booking_id,
        "building_id": building_id,
        **booking_data,
        "status": "confirmed",
        "booked_by": current_user["id"],
        "booked_by_name": current_user.get("full_name"),
        "unit_number": current_user.get("unit_number", ""),
        "created_at": now,
    }

    await db.amenity_bookings.insert_one(booking_doc)
    return AmenityBookingResponse(**booking_doc)


@api_router.get("/amenity-bookings", response_model=List[AmenityBookingResponse])
async def get_amenity_bookings(
        amenity_type: Optional[str] = None,
        date: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List amenity bookings. Scoped to building."""
    query = {"building_id": building_id}
    if amenity_type:
        query["amenity_type"] = amenity_type
    if date:
        query["date"] = date

    bookings = (
        await db.amenity_bookings.find(query, {"_id": 0}).sort("date", -1).to_list(100)
    )
    return [AmenityBookingResponse(**b) for b in bookings]


@api_router.delete("/amenity-bookings/{booking_id}")
async def cancel_amenity_booking(
        booking_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Cancel an amenity booking.
    Test data (is_test_data=True) is hard-deleted for cleanup.
    Real bookings are soft-cancelled to satisfy ACT/NSW 7-year retention (Unit Titles Act s.115).
    """
    booking = await db.amenity_bookings.find_one({"id": booking_id, "building_id": building_id}, {"_id": 0})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    permissions = get_user_permissions(current_user)
    if booking["booked_by"] != current_user["id"] and not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Not authorized")

    if booking.get("is_test_data"):
        await db.amenity_bookings.delete_one({"id": booking_id, "building_id": building_id})
    else:
        now = datetime.now(timezone.utc).isoformat()
        await db.amenity_bookings.update_one(
            {"id": booking_id, "building_id": building_id},
            {"$set": {"status": "cancelled", "cancelled_at": now, "cancelled_by": current_user["id"]}}
        )
    return {"message": "Booking cancelled"}


@api_router.delete("/move-bookings/{booking_id}")
async def delete_move_booking(
        booking_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a move booking. Managers only.
    Test data (is_test_data=True) is hard-deleted for cleanup.
    Real bookings are soft-cancelled to satisfy ACT/NSW 7-year retention (Unit Titles Act s.115).
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_requests:
        raise HTTPException(status_code=403, detail="Not authorized")

    booking = await db.move_bookings.find_one({"id": booking_id, "building_id": building_id}, {"_id": 0})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.get("is_test_data"):
        await db.move_bookings.delete_one({"id": booking_id, "building_id": building_id})
    else:
        now = datetime.now(timezone.utc).isoformat()
        await db.move_bookings.update_one(
            {"id": booking_id, "building_id": building_id},
            {"$set": {"status": "cancelled", "cancelled_at": now, "cancelled_by": current_user["id"]}}
        )
    return {"message": "Booking cancelled"}


# ==================== KEY & FOB REGISTER ====================


class KeyFobCreate(BaseModel):
    unit_number: str
    key_type: str  # entry_key, garage_fob, letterbox_key, common_area_fob, master_key, other
    serial_number: Optional[str] = None
    issued_to_name: str
    issued_to_user_id: Optional[str] = None
    issued_date: Optional[str] = None
    notes: Optional[str] = None


class KeyFobUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[str] = None  # active, returned, lost, replacement
    issued_to_name: Optional[str] = None
    issued_to_user_id: Optional[str] = None
    issued_date: Optional[str] = None
    returned_date: Optional[str] = None
    notes: Optional[str] = None
    serial_number: Optional[str] = None


class KeyFobResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    building_id: str
    unit_number: str
    key_type: str
    serial_number: Optional[str] = None
    issued_to_name: str
    issued_to_user_id: Optional[str] = None
    issued_date: Optional[str] = None
    returned_date: Optional[str] = None
    status: str = "active"
    notes: Optional[str] = None
    created_at: str
    updated_at: str
    created_by_name: Optional[str] = None


_KEY_FOB_MANAGER_ROLES = {"super_admin", "strata_admin", "ec_member", "strata_manager"}


@api_router.get("/building/keys-fobs", response_model=List[KeyFobResponse])
async def list_key_fobs(
        unit_number: Optional[str] = None,
        key_type: Optional[str] = None,
        status: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """List all key/fob assignments. Managers see all; owners see their unit only."""
    role = _effective_role(current_user)
    query: dict = {"building_id": building_id}

    if role not in _KEY_FOB_MANAGER_ROLES:
        unit = current_user.get("unit_number")
        if not unit:
            return []
        query["unit_number"] = unit
    else:
        if unit_number:
            query["unit_number"] = unit_number

    if key_type:
        query["key_type"] = key_type
    if status:
        query["status"] = status

    results = await db.key_fob_register.find(query, {"_id": 0}).sort("unit_number", 1).to_list(500)
    return [KeyFobResponse(**r) for r in results]


@api_router.post("/building/keys-fobs", response_model=KeyFobResponse)
async def issue_key_fob(
        data: KeyFobCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Issue (register) a new key or fob to a unit. Admin/chairman only."""
    if _effective_role(current_user) not in _KEY_FOB_MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        **data.model_dump(),
        "status": "active",
        "returned_date": None,
        "created_at": now,
        "updated_at": now,
        "created_by": current_user["id"],
        "created_by_name": current_user.get("full_name", ""),
    }
    if not doc.get("issued_date"):
        doc["issued_date"] = now[:10]

    await db.key_fob_register.insert_one(doc)
    return KeyFobResponse(**doc)


@api_router.put("/building/keys-fobs/{fob_id}", response_model=KeyFobResponse)
async def update_key_fob(
        fob_id: str,
        data: KeyFobUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Update a key/fob record (return, mark lost, reassign). Admin only."""
    if _effective_role(current_user) not in _KEY_FOB_MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    doc = await db.key_fob_register.find_one({"id": fob_id, "building_id": building_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Key/Fob record not found")

    updates = data.model_dump(exclude_unset=True)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.key_fob_register.update_one({"id": fob_id}, {"$set": updates})
    doc.update(updates)
    doc.pop("_id", None)
    return KeyFobResponse(**doc)


@api_router.delete("/building/keys-fobs/{fob_id}")
async def delete_key_fob(
        fob_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Permanently remove a key/fob record. Super admin only."""
    if _effective_role(current_user) not in {"super_admin", "ec_member", "strata_admin"}:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.key_fob_register.delete_one({"id": fob_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Key/Fob record not found")
    return {"message": "Record deleted"}


# ==================== PARCEL NOTIFICATIONS ====================


@api_router.post("/parcels", response_model=ParcelResponse)
async def log_parcel(
        data: ParcelCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Log a received or expected parcel. Staff log received parcels; owners self-register expected deliveries."""
    permissions = get_user_permissions(current_user)
    role = current_user.get("role", "")

    is_staff = permissions.can_manage_users or role == "service_provider"
    is_resident = role in ("owner", "ec_member", "strata_admin", "tenant")

    if not is_staff and not is_resident:
        raise HTTPException(status_code=403, detail="Not authorized")

    parcel_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    parcel_data = data.model_dump()
    parcel_data["unit_number"] = html_lib.escape(data.unit_number)
    parcel_data["carrier"] = html_lib.escape(data.carrier)
    if data.tracking_number:
        parcel_data["tracking_number"] = html_lib.escape(data.tracking_number)
    if data.description:
        parcel_data["description"] = nh3.clean(data.description)
    if data.storage_location:
        parcel_data["storage_location"] = html_lib.escape(data.storage_location)

    if is_resident:
        # Always use the resident's DB unit — ignore body unit_number to avoid
        # false 403s when the submitted value has a minor formatting difference
        unit = current_user.get("unit_number")
        if not unit:
            raise HTTPException(status_code=400, detail="Your account has no unit assigned")
        parcel_data["unit_number"] = unit
        parcel_status = "expected"
    else:
        parcel_status = "received"

    parcel_doc = {
        "id": parcel_id,
        "building_id": building_id,
        **parcel_data,
        "status": parcel_status,
        "received_date": now,
        "collected_date": None,
        "logged_by": current_user["id"],
        "created_at": now,
    }

    await db.parcels.insert_one(parcel_doc)

    # TODO: Send notification to resident

    return ParcelResponse(**parcel_doc)


@api_router.get("/parcels", response_model=List[ParcelResponse])
async def get_parcels(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List parcels. Scoped to building."""
    permissions = get_user_permissions(current_user)

    if permissions.can_manage_users:
        parcels = (
            await db.parcels.find({"building_id": building_id}, {"_id": 0}).sort("received_date", -1).to_list(200)
        )
    else:
        parcels = (
            await db.parcels.find(
                {"building_id": building_id, "unit_number": current_user.get("unit_number")}, {"_id": 0}
            )
            .sort("received_date", -1)
            .to_list(50)
        )

    return [ParcelResponse(**p) for p in parcels]


@api_router.put("/parcels/{parcel_id}/collected")
async def mark_parcel_collected(
        parcel_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Mark parcel as collected. Scoped to building."""
    parcel = await db.parcels.find_one({"id": parcel_id, "building_id": building_id}, {"_id": 0})
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel not found")

    # SECURITY: Enforce BOLA protection. Only staff or the recipient can mark as collected.
    permissions = get_user_permissions(current_user)
    is_staff = permissions.can_manage_users or current_user.get("role") == "service_provider"
    is_recipient = parcel.get("unit_number") == current_user.get("unit_number")

    if not is_staff and not is_recipient:
        raise HTTPException(
            status_code=403, detail="Not authorized to mark this parcel as collected"
        )

    now = datetime.now(timezone.utc).isoformat()
    await db.parcels.update_one(
        {"id": parcel_id, "building_id": building_id},
        {"$set": {"status": "collected", "collected_date": now}},
    )

    return {"message": "Parcel marked as collected"}


@api_router.get("/parcels/{parcel_id}/track")
async def track_parcel_status(
        parcel_id: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
):
    """Live or simulated carrier tracking for a parcel. Scoped to building."""
    parcel = await db.parcels.find_one(
        {"id": parcel_id, "building_id": building_id}, {"_id": 0}
    )
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel not found")

    permissions = get_user_permissions(current_user)
    if (
            not permissions.can_manage_users
            and current_user.get("role") != "service_provider"
            and parcel.get("unit_number") != current_user.get("unit_number")
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    tracking_number = parcel.get("tracking_number")
    carrier = parcel.get("carrier", "other")

    if not tracking_number:
        return {
            "parcel_id": parcel_id,
            "carrier": carrier,
            "tracking_number": None,
            "message": "No tracking number recorded for this parcel.",
            "tracking_url": None,
        }

    from services.courier_tracking_service import track_parcel
    result = await track_parcel(carrier, tracking_number)
    return result.to_dict()


_DEFAULT_AMENITIES = [
    {"key": "pool", "label": "Swimming Pool", "icon": "Waves", "description": "Outdoor swimming pool"},
    {"key": "gym", "label": "Gym", "icon": "Dumbbell", "description": "Fitness centre"},
]


@api_router.get("/amenities")
async def list_amenities(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """List all amenities for this building. Falls back to defaults if none configured."""
    amenities = await db.building_amenities.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(100)
    if not amenities:
        return {"amenities": _DEFAULT_AMENITIES, "using_defaults": True}
    return {"amenities": amenities, "using_defaults": False}


@api_router.post("/amenities")
async def add_amenity(
        data: AmenityCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """Add a new amenity. Requires manager permissions."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Manager role required")

    existing = await db.building_amenities.find_one({"building_id": building_id, "key": data.key})
    if existing:
        raise HTTPException(status_code=409, detail="Amenity with this key already exists")

    amenity = {
        "id": str(uuid.uuid4()),
        "key": data.key,
        "label": data.label,
        "icon": data.icon,
        "description": data.description or "",
        "building_id": building_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": current_user["id"],
    }
    await db.building_amenities.insert_one(amenity)
    amenity.pop("_id", None)
    return amenity


@api_router.delete("/amenities/{amenity_key}")
async def remove_amenity(
        amenity_key: str,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building),
        _feature: dict = Depends(require_feature("bookings")),
):
    """Remove an amenity by key. Requires manager permissions."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_users:
        raise HTTPException(status_code=403, detail="Manager role required")

    result = await db.building_amenities.delete_one({"building_id": building_id, "key": amenity_key})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Amenity not found")
    return {"message": "Amenity removed"}


# ==================== COMMUNITY EVENTS ====================


@api_router.post("/events", response_model=CommunityEventResponse)
async def create_event(
        data: CommunityEventCreate,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Create a community event. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # SECURITY: Sanitize user input to prevent Stored XSS
    event_data = data.model_dump()
    event_data["title"] = html_lib.escape(data.title)
    if data.location:
        event_data["location"] = html_lib.escape(data.location)
    if data.description:
        event_data["description"] = nh3.clean(data.description)

    event_doc = {
        "id": event_id,
        "building_id": building_id,
        **event_data,
        "created_by": current_user["id"],
        "created_at": now,
    }

    await db.events.insert_one(event_doc)
    return CommunityEventResponse(**event_doc)


@api_router.get("/events", response_model=List[CommunityEventResponse])
async def get_events(
        month: Optional[str] = None,
        event_type: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """List community events. Scoped to building."""
    permissions = get_user_permissions(current_user)
    user_id = current_user.get("id", "")
    user_role = current_user.get("role", "")
    all_events = []
    date_regex = f"^{re.escape(month)}" if month else None

    # 1. Direct events from db.events (manual entries, levy dates, etc.)
    query = {"building_id": building_id}
    if date_regex:
        query["start_date"] = {"$regex": date_regex}
    if event_type:
        query["event_type"] = event_type
    # Non-managers only see public events
    if not permissions.can_manage_meetings:
        query["is_public"] = True

    # Performance Optimization⚡: Parallelized event fetching to reduce calendar load latency.
    direct_task = db.events.find(query, {"_id": 0}).to_list(200)

    meetings_needed = permissions.can_view_meetings and (
            not event_type or event_type == "meeting"
    )
    meeting_task = (
        db.meetings.find(
            {
                "building_id": building_id,
                "status": {"$ne": "archived"},
                **({"meeting_date": {"$regex": date_regex}} if date_regex else {}),
            },
            {"_id": 0}
        ).to_list(100)
        if meetings_needed
        else asyncio.to_thread(list)
    )

    agms_needed = permissions.can_view_meetings and (
            not event_type or event_type == "agm"
    )
    agm_task = (
        db.agm.find(
            {"building_id": building_id, "date": {"$regex": date_regex}} if date_regex else {
                "building_id": building_id}, {"_id": 0}
        ).to_list(20)
        if agms_needed
        else asyncio.to_thread(list)
    )

    announcements_needed = not event_type or event_type in ("community", "announcement")
    ann_query: dict = {"building_id": building_id, "expires_at": {"$exists": True, "$ne": None, "$type": "string"}}
    if date_regex:
        ann_query["expires_at"] = {"$regex": date_regex}
    if not permissions.can_post_announcements:
        ann_query["$or"] = [
            {"is_public": True},
            {"target_roles": user_role},
            {"target_users": user_id},
        ]
    ann_task = (
        db.announcements.find(ann_query, {"_id": 0}).to_list(50)
        if announcements_needed
        else asyncio.to_thread(list)
    )

    direct_events, meetings, agms, announcements = await asyncio.gather(
        direct_task, meeting_task, agm_task, ann_task
    )
    all_events.extend(direct_events)

    # 2. Process Committee meetings
    if meetings_needed:
        for m in meetings:
            all_events.append(
                {
                    "id": f"meeting_{m['id']}",
                    "title": m.get("title", "Committee Meeting"),
                    "description": m.get("description"),
                    "event_type": "meeting",
                    "start_date": m.get("meeting_date", ""),
                    "end_date": None,
                    "location": m.get("location"),
                    "is_recurring": False,
                    "recurrence_rule": None,
                    "source": "meetings",
                    "source_url": None,
                    "is_public": False,
                    "created_by": m.get("created_by", ""),
                    "created_at": m.get("created_at", ""),
                }
            )

    # 3. Process AGMs
    if agms_needed:
        for a in agms:
            agenda_items = a.get("agenda", [])
            # Extract URL from agenda if present
            source_url = next(
                (item for item in agenda_items if item.startswith("http")), None
            )
            agenda_text = (
                "\n".join(item for item in agenda_items if not item.startswith("http"))
                if agenda_items
                else None
            )
            all_events.append(
                {
                    "id": f"agm_{a['id']}",
                    "title": a.get("title", "Annual General Meeting"),
                    "description": agenda_text,
                    "event_type": "agm",
                    "start_date": a.get("date", ""),
                    "end_date": None,
                    "location": a.get("location"),
                    "is_recurring": False,
                    "recurrence_rule": None,
                    "source": "agm",
                    "source_url": source_url,
                    "is_public": True,
                    "created_by": "",
                    "created_at": a.get("created_at", ""),
                }
            )

    # 4. Process Announcements
    if announcements_needed:
        for ann in announcements:
            expires = ann.get("expires_at")
            if expires:
                all_events.append(
                    {
                        "id": f"ann_{ann['id']}",
                        "title": f"[Notice] {ann.get('title', 'Announcement')}",
                        "description": ann.get("message"),
                        "event_type": "announcement",
                        "start_date": expires,
                        "end_date": None,
                        "location": None,
                        "is_recurring": False,
                        "recurrence_rule": None,
                        "source": "announcements",
                        "source_url": None,
                        "is_public": ann.get("is_public", False),
                        "created_by": ann.get("created_by", ""),
                        "created_at": ann.get("created_at", ""),
                    }
                )

    # Sort all combined events by start_date ascending
    all_events.sort(key=lambda x: x.get("start_date", ""))
    return [CommunityEventResponse(**e) for e in all_events]


@api_router.delete("/events/{event_id}")
async def delete_event(
        event_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a community event. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.events.delete_one({"id": event_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")

    return {"message": "Event deleted"}


@api_router.post("/events/generate-levy-dates")
async def generate_levy_due_dates(
        year: int = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Auto-generate levy due date events for a calendar year.
    Reads levy_due_months, levy_due_day_type, levy_due_custom_dates from settings.
    Upserts: deletes stale levy events for the year then re-inserts with correct dates.
    """
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    if not year:
        year = datetime.now().year

    # Read levy schedule from settings; fallback to ACT default (Mar/Jun/Sep/Dec, day 1)
    settings_doc = await _get_general_settings(building_id, {"_id": 0})
    levy_months = (
        settings_doc.get("levy_due_months", [3, 6, 9, 12])
        if settings_doc
        else [3, 6, 9, 12]
    )
    levy_due_day_type = settings_doc.get("levy_due_day_type", "first") if settings_doc else "first"
    levy_due_day = settings_doc.get("levy_due_day") if settings_doc else None
    levy_due_custom_dates = (
        settings_doc.get("levy_due_custom_dates") or {} if settings_doc else {}
    )

    import calendar as cal_mod

    levy_dates = []
    for idx, month in enumerate(sorted(levy_months)):
        q_label = f"Q{idx + 1}"
        last_day = cal_mod.monthrange(year, month)[1]

        if levy_due_day_type == "first":
            day = 1
        elif levy_due_day_type == "middle":
            day = 15
        elif levy_due_day_type == "last":
            day = last_day
        elif levy_due_day_type == "custom":
            m_str = str(month)
            if m_str in levy_due_custom_dates:
                day = min(int(levy_due_custom_dates[m_str]), last_day)
            else:
                day = min(levy_due_day or 1, last_day)
        else:
            day = min(levy_due_day or 1, last_day)

        date_str = f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
        levy_dates.append(
            {
                "quarter": q_label,
                "date": date_str,
                "title": f"{q_label} Levy Due - FY {year}-{year + 1}",
            }
        )

    # Upsert: delete ALL existing levy_due events for this building+year (stale or wrong),
    # then re-insert with dates from current settings.
    fy_suffix = f"FY {year}-{year + 1}"
    await db.events.delete_many({
        "building_id": building_id,
        "event_type": "levy_due",
        "title": {"$regex": fy_suffix},
    })

    now_iso = datetime.now(timezone.utc).isoformat()

    # Performance Optimization⚡: Parallelize event inserts while maintaining individual error granularity.
    # Using asyncio.gather reduces cumulative I/O wait time from O(N) to O(1) concurrent requests.
    async def _insert_levy_event(levy_item):
        """Generated function header.

        Function: _insert_levy_event
        Path: backend/server.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        event_doc = {
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "title": levy_item["title"],
            "description": f"Quarterly strata levy payment due for {levy_item['quarter']}",
            "event_type": "levy_due",
            "start_date": levy_item["date"],
            "end_date": None,
            "location": None,
            "is_recurring": True,
            "recurrence_rule": "yearly",
            "source": "system",
            "source_url": None,
            "is_public": True,
            "created_by": current_user["id"],
            "created_at": now_iso,
        }
        await db.events.insert_one(event_doc)

    results = await asyncio.gather(*[_insert_levy_event(l) for l in levy_dates], return_exceptions=True)

    error_count = 0
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            error_count += 1
            logger.error(f"Failed to generate levy event '{levy_dates[i]['title']}': {res}")

    return {"message": f"Generated {len(levy_dates) - error_count} levy due date events for FY {year}-{year + 1}"}


@api_router.post("/events/sync-land-tax")
async def sync_land_tax_events(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Sync ACT Land Tax due dates to calendar for a tenanted unit owner.
    ACT Land Tax quarters: Q1=31 Aug, Q2=30 Nov, Q3=28 Feb, Q4=31 May.
    Idempotent — only inserts events that don't already exist.
    """
    unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Only proceed if unit is tenanted
    has_tenant = (
            unit.get("has_tenant") or unit.get("tenant_name") or unit.get("tenant_email")
    )
    if not has_tenant:
        return {
            "message": "Unit is not tenanted; no land tax events added",
            "created": 0,
        }

    year = datetime.now().year
    import calendar as cal_mod

    # ACT Land Tax: Q1=Aug 31, Q2=Nov 30, Q3=Feb 28/29, Q4=May 31
    land_tax_quarters = [
        ("Q1", year, 8, 31),
        ("Q2", year, 11, 30),
        ("Q3", year + 1, 2, 28),
        ("Q4", year + 1, 5, 31),
    ]

    created = 0
    for q_label, q_year, month, day in land_tax_quarters:
        max_day = cal_mod.monthrange(q_year, month)[1]
        day = min(day, max_day)
        date_str = f"{q_year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
        title = f"Land Tax {q_label} Due - {unit_number} FY {year}-{year + 1}"
        existing = await db.events.find_one({"building_id": building_id, "title": title})
        if not existing:
            event_doc = {
                "id": str(uuid.uuid4()),
                "building_id": building_id,
                "title": title,
                "description": f"ACT Land Tax {q_label} instalment due for unit {unit_number}",
                "event_type": "land_tax",
                "start_date": date_str,
                "end_date": None,
                "location": None,
                "is_recurring": False,
                "recurrence_rule": None,
                "source": "system",
                "source_url": None,
                "is_public": False,
                "unit_number": unit_number,
                "created_by": current_user["id"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.events.insert_one(event_doc)
            created += 1

    return {
        "message": f"Synced {created} land tax events for unit {unit_number}",
        "created": created,
    }


@api_router.post("/events/scrape-community")
async def scrape_community_events(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Scrape community events from local Canberra pages. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_meetings:
        raise HTTPException(status_code=403, detail="Not authorized")

    events_created = 0
    sources = [
        {
            "name": "Molonglo Valley Community",
            "url": "https://www.communitycouncil.com.au/molonglo",
        },
        {"name": "Denman Prospect", "url": "https://www.denmanprospect.com.au/events"},
    ]

    # Note: Full scraping would require proper web scraping implementation
    # For now, return a placeholder response
    return {
        "message": f"Scraped {events_created} events from community sources",
        "note": "Facebook integration requires Graph API setup. Contact admin for configuration.",
        "sources_checked": [s["name"] for s in sources],
    }


# ==================== NOTIFICATIONS & LEVY REMINDERS ====================


@api_router.post("/notifications/send", response_model=NotificationResponse)
async def send_notification(
        data: NotificationCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Send notifications to building residents. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if (
            not permissions.can_send_notifications
            and not permissions.can_post_announcements
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    notif_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Filter users by building membership
    memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
    building_user_ids = [m["user_id"] for m in memberships]

    # Determine recipients
    recipients_list = []
    if "all" in data.recipients:
        users = await db.users.find(
            {"id": {"$in": building_user_ids}, "is_active": True, "status": {"$ne": "archived"}},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(1000)
        recipients_list = users
    elif "owners" in data.recipients:
        users = await db.users.find(
            {"id": {"$in": building_user_ids}, "is_active": True, "role": "owner", "status": {"$ne": "archived"}},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(1000)
        recipients_list = users
    elif "tenants" in data.recipients:
        users = await db.users.find(
            {"id": {"$in": building_user_ids}, "is_active": True, "role": "tenant", "status": {"$ne": "archived"}},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(1000)
        recipients_list = users
    else:
        # Only include requested users who are members of this building
        target_ids = [uid for uid in data.recipients if uid in building_user_ids]
        users = await db.users.find(
            {"id": {"$in": target_ids}},
            {"_id": 0, "id": 1, "email": 1, "phone": 1},
        ).to_list(len(target_ids) + 1)
        recipients_list = users

    sent_count = 0
    failed_count = 0

    # Send via configured channels
    # Performance Optimization⚡: Parallelize email dispatch using asyncio.gather to reduce latency and avoid blocking the event loop.
    email_tasks = []
    target_emails = []

    # Pre-generate HTML content only if email channel is requested
    html = None
    if "email" in data.channels:
        settings_doc = await _get_general_settings_or_default(
            building_id,
            {"_id": 0},
            fallback_building_id=DEFAULT_BUILDING_ID,
            settings_db=db,
        )
        safe_building_name = html_lib.escape(settings_doc.get("building_name") or "Building")
        safe_building_address = html_lib.escape(settings_doc.get("building_address") or "")
        footer_html = (
            f"{safe_building_name}<br>{safe_building_address}"
            if safe_building_address
            else safe_building_name
        )
        safe_title = html_lib.escape(str(data.title or ""))
        safe_message = html_lib.escape(str(data.message or ""))
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a365d;">{safe_title}</h2>
            <p>{safe_message}</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="color: #718096; font-size: 12px;">
                {footer_html}
            </p>
        </div>
        """

    for recipient in recipients_list:
        try:
            # Track if at least one channel succeeds for this recipient
            recipient_sent = False
            recipient_failed = False

            if "email" in data.channels and recipient.get("email") and html:
                target_emails.append(recipient["email"])
                # send_email_async handles provider selection (Resend/SMTP) and API keys internally
                email_tasks.append(
                    send_email_async(
                        to_email=recipient["email"],
                        subject=data.title,
                        html_content=html
                    )
                )
            elif "email" in data.channels and recipient.get("email"):
                # Logic error protection: email requested but html template missing
                recipient_failed = True

            if "sms" in data.channels and recipient.get("phone"):
                # SMS would be sent via Twilio - placeholder
                logger.info(f"SMS to {recipient.get('phone')}: {data.title}")
                recipient_sent = True

            if "whatsapp" in data.channels and recipient.get("phone"):
                # WhatsApp would be sent via Twilio/WhatsApp Business API - placeholder
                logger.info(f"WhatsApp to {recipient.get('phone')}: {data.title}")
                recipient_sent = True

            # Increment counters for non-async channels
            if recipient_sent:
                sent_count += 1
            if recipient_failed:
                failed_count += 1

        except Exception as e:
            logger.error(f"Initial setup failed for notification to {recipient.get('email')}: {e}")
            failed_count += 1

    if email_tasks:
        results = await asyncio.gather(*email_tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Notification task failed for {target_emails[i]}: {res}")
                failed_count += 1
            elif isinstance(res, dict) and res.get("success"):
                sent_count += 1
            else:
                error_msg = res.get("error") if isinstance(res, dict) else str(res)
                logger.error(f"Notification delivery failed for {target_emails[i]}: {error_msg}")
                failed_count += 1

    # Determine status based on sent vs failed counts
    final_status = "failed"
    if sent_count > 0 and failed_count > 0:
        final_status = "partial_success"
    elif sent_count > 0:
        final_status = "sent"

    notif_doc = {
        "id": notif_id,
        "building_id": building_id,
        **data.model_dump(),
        "status": final_status,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "created_by": current_user["id"],
        "created_at": now,
        "sent_at": now if sent_count > 0 else None,
    }

    await db.notifications.insert_one(notif_doc)
    return NotificationResponse(**notif_doc)


@api_router.post("/notifications/levy-reminder")
async def send_levy_reminder(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Send levy reminder to all owners. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_send_notifications:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get next levy due date
    today = datetime.now().date()
    levy_events = await db.events.find({"building_id": building_id, "event_type": "levy_due"}, {"_id": 0}).to_list(
        10
    )

    next_levy = None
    for event in levy_events:
        event_date = datetime.fromisoformat(event["start_date"]).date()
        if event_date > today:
            next_levy = event
            break

    if not next_levy:
        return {"message": "No upcoming levy due dates found"}

    # Get levy rates from annual_levies (new schema) - Performance Optimization⚡
    from utils.finance_helpers import get_latest_levy_year, get_levy_rates

    year = await get_latest_levy_year(building_id) or str(today.year)
    levy_rates = await get_levy_rates(year, building_id)

    # Filter users by building membership
    memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
    building_user_ids = [m["user_id"] for m in memberships]

    # Parallel fetch units and owners to avoid N+1 queries - Performance Optimization⚡
    units_task = db.units.find({"building_id": building_id}, {"_id": 0}).to_list(200)
    owners_task = db.users.find(
        {
            "id": {"$in": building_user_ids},
            # 'chairman' is not a top-level role — a chairman is a user with role='ec_member'
            # and ec_position='CHAIRMAN' (see rules/post-compact-critical.md), already covered
            # by the 'ec_member' entry below.
            "role": {"$in": ["owner", "strata_admin", "ec_member", "super_admin"]},
            "is_active": True,
            "status": {"$ne": "archived"},
        },
        {"_id": 0, "email": 1, "mail_username": 1, "full_name": 1, "unit_number": 1},
    ).to_list(1000)

    units, owners = await asyncio.gather(units_task, owners_task)
    settings_doc = await _get_general_settings_or_default(
        building_id,
        {"_id": 0},
        fallback_building_id=DEFAULT_BUILDING_ID,
        settings_db=db,
    )
    safe_building_name = html_lib.escape(settings_doc.get("building_name") or "Building")
    safe_building_address = html_lib.escape(settings_doc.get("building_address") or "")
    footer_html = (
        f"{safe_building_name} | {safe_building_address}"
        if safe_building_address
        else safe_building_name
    )

    # Pre-map units by owner_email for O(1) lookup inside loop - Bolt ⚡
    unit_map = {u.get("owner_email"): u for u in units if u.get("owner_email")}
    email_tasks = []
    # Limit concurrent network I/O tasks - Bolt ⚡
    semaphore = asyncio.Semaphore(10)

    async def _throttled_send(recipient, subject, html_content):
        """Generated function header.

        Function: _throttled_send
        Path: backend/server.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        async with semaphore:
            return await send_email_async(
                to_email=recipient,
                subject=subject,
                html_content=html_content
            )

    for owner in owners:
        # Bolt ⚡: Ensure owner object is handled robustly
        owner = owner or {}
        unit = unit_map.get(owner.get("email"))

        # Calculate levy from UOE × levy_per_uoe rates (new schema)
        entitlement = unit.get("entitlement", 115) if unit else 115
        admin_annual = round(levy_rates.get("admin_annual", 0) * entitlement, 2)
        sinking_annual = round(levy_rates.get("sinking_annual", 0) * entitlement, 2)
        annual_levy = admin_annual + sinking_annual
        quarterly_levy = round(annual_levy / 4, 2)

        # Collect both login email + eastgate alias (deduplicated)
        recipient_emails = list(
            {e for e in [owner.get("email"), owner.get("mail_username")] if e}
        )

        if recipient_emails:
            safe_full_name = html_lib.escape(str(owner.get("full_name") or "Resident"))
            safe_due_date = html_lib.escape(str(next_levy.get("start_date") or ""))
            safe_unit_number = html_lib.escape(str(owner.get("unit_number") or "N/A"))
            html = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #1a365d;">Levy Payment Reminder</h2>
                <p>Dear {safe_full_name},</p>
                <p>This is a friendly reminder that your quarterly strata levy is due on <strong>{safe_due_date}</strong>.</p>

                <div style="background: #f7fafc; padding: 15px; border-radius: 8px; margin: 20px 0;">
                    <p style="margin: 5px 0;"><strong>Amount Due:</strong> ${quarterly_levy:,.2f}</p>
                    <p style="margin: 5px 0;"><strong>Due Date:</strong> {safe_due_date}</p>
                    <p style="margin: 5px 0;"><strong>Unit:</strong> {safe_unit_number}</p>
                </div>

                <h3 style="color: #2d3748;">Payment Methods</h3>
                <ul style="color: #4a5568;">
                    <li><strong>BPAY:</strong> Biller Code XXXXXX, Ref: Your Unit Number</li>
                    <li><strong>DEFT:</strong> <a href="https://www.dfrportal.com.au">www.dfrportal.com.au</a></li>
                    <li><strong>Direct Debit:</strong> Contact strata manager</li>
                    <li><strong>Credit Card:</strong> 2% surcharge applies</li>
                </ul>

                <p>If you have already made this payment, please disregard this notice.</p>

                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                <p style="color: #718096; font-size: 12px;">
                    {footer_html}<br>
                    This is an automated reminder from your strata management system.
                </p>
            </div>
            """
            subject = f"Levy Reminder - Due {next_levy['start_date']}"
            for recipient in recipient_emails:
                # Performance Optimization⚡: Parallelized email dispatch using asyncio.gather.
                # Throttled by semaphore to prevent resource exhaustion.
                email_tasks.append(_throttled_send(recipient, subject, html))

    sent_count = 0
    if email_tasks:
        results = await asyncio.gather(*email_tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Levy reminder email task raised exception: {res}")
            elif isinstance(res, dict) and res.get("success"):
                sent_count += 1
            else:
                error_msg = res.get("error") if isinstance(res, dict) else str(res)
                logger.error(f"Levy reminder email delivery failed: {error_msg}")

    return {
        "message": f"Sent {sent_count} levy reminders",
        "next_due_date": next_levy["start_date"],
    }


# ==================== AUTOMATED LEVY REMINDER SETTINGS ====================


@api_router.get("/notifications", response_model=List[NotificationResponse])
async def get_notifications(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """List sent notifications. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if (
            not permissions.can_send_notifications
            and not permissions.can_post_announcements
    ):
        raise HTTPException(status_code=403, detail="Not authorized")

    notifications = (
        await db.notifications.find({"building_id": building_id}, {"_id": 0}).sort("created_at", -1).to_list(100)
    )
    return [NotificationResponse(**n) for n in notifications]


# ==================== COMPLIANCE CHECKLIST MANAGEMENT ====================


@api_router.get("/compliance-items", response_model=List[ComplianceItemResponse])
async def get_compliance_items(
        status: Optional[str] = None,
        category: Optional[str] = None,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get compliance checklist items. Scoped to building."""
    # Allow owners read-only access; restrict updates to EC members and above
    allowed_roles = [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
        UserRole.OWNER,
    ]
    if _effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Access denied")

    query = {"building_id": building_id}
    if status:
        query["status"] = status
    if category:
        query["category"] = category

    # Performance Optimization⚡: Using a single aggregation pipeline to fetch items and enrich with user names.
    # This eliminates the N+1 query problem where 2 additional queries were executed for each item.
    pipeline = [
        {"$match": query},
        {"$sort": {"due_date": 1}},
        {"$limit": 200},
        {
            "$lookup": {
                "from": "users",
                "localField": "assigned_to",
                "foreignField": "id",
                "as": "assigned_user_info",
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "created_by",
                "foreignField": "id",
                "as": "creator_info",
            }
        },
        {
            "$addFields": {
                # Older Mongo compliance records predate these response-required fields.
                # Normalize the read payload only; new writes still persist explicit values.
                "category": {"$ifNull": ["$category", "general"]},
                "priority": {"$ifNull": ["$priority", "medium"]},
                "created_by": {"$ifNull": ["$created_by", "system"]},
                "assigned_to_name": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$assigned_user_info.full_name", 0]},
                        None,
                    ]
                },
                "created_by_name": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$creator_info.full_name", 0]},
                        None,
                    ]
                },
            }
        },
        {"$project": {"_id": 0, "assigned_user_info": 0, "creator_info": 0}},
    ]

    items = await _server_agg(db.compliance_items, pipeline, 200)
    return [ComplianceItemResponse(**item) for item in items]


@api_router.post("/compliance-items", response_model=ComplianceItemResponse)
async def create_compliance_item(
        data: ComplianceItemCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new compliance checklist item (Chairman and Super Admin only). Scoped to building."""
    # Restrict to chairman and super admin
    if _effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
        raise HTTPException(
            status_code=403,
            detail="Only Chairman or Super Admin can create compliance items",
        )

    item = {
        "id": str(uuid.uuid4()),
        "building_id": building_id,
        "title": data.title,
        "description": data.description,
        "category": data.category,
        "status": "pending",
        "due_date": data.due_date,
        "completed_date": None,
        "assigned_to": data.assigned_to,
        "priority": data.priority,
        "recurrence": data.recurrence,
        "notes": None,
        "created_by": current_user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    await db.compliance_items.insert_one(item)

    # Get user names
    if item.get("assigned_to"):
        assigned_user = await db.users.find_one(
            {"id": item["assigned_to"]}, {"full_name": 1}
        )
        if assigned_user:
            item["assigned_to_name"] = assigned_user.get("full_name")

    creator = await db.users.find_one({"id": item["created_by"]}, {"full_name": 1})
    if creator:
        item["created_by_name"] = creator.get("full_name")

    return ComplianceItemResponse(**item)


@api_router.put("/compliance-items/{item_id}", response_model=ComplianceItemResponse)
async def update_compliance_item(
        item_id: str,
        data: ComplianceItemUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update a compliance checklist item. Scoped to building."""
    # Restrict to EC members, chairman, and super admin
    if _effective_role(current_user) not in [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
    ]:
        raise HTTPException(status_code=403, detail="Access denied - EC members only")

    # Get existing item
    item = await db.compliance_items.find_one({"id": item_id, "building_id": building_id}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Compliance item not found")

    # Build update data
    update_data = {"updated_at": datetime.now(timezone.utc).isoformat()}

    if data.title is not None:
        update_data["title"] = data.title
    if data.description is not None:
        update_data["description"] = data.description
    if data.category is not None:
        update_data["category"] = data.category
    if data.status is not None:
        update_data["status"] = data.status
        # Set completed_date if status is completed
        if data.status == "completed" and not item.get("completed_date"):
            update_data["completed_date"] = datetime.now(timezone.utc).isoformat()
    if data.due_date is not None:
        update_data["due_date"] = data.due_date
    if data.completed_date is not None:
        update_data["completed_date"] = data.completed_date
    if data.assigned_to is not None:
        update_data["assigned_to"] = data.assigned_to
    if data.priority is not None:
        update_data["priority"] = data.priority
    if data.notes is not None:
        update_data["notes"] = data.notes

    # Update in database
    await db.compliance_items.update_one({"id": item_id, "building_id": building_id}, {"$set": update_data})

    # Create audit log for history tracking
    audit_details = {k: v for k, v in data.model_dump().items() if v is not None}
    await create_audit_log(
        action="updated",
        resource_type="compliance_item",
        resource_id=item_id,
        user_id=current_user["id"],
        user_name=current_user["full_name"],
        details=audit_details,
        building_id=building_id
    )

    # Get updated item
    updated_item = await db.compliance_items.find_one({"id": item_id, "building_id": building_id}, {"_id": 0})

    # Enrich with user names
    if updated_item.get("assigned_to"):
        assigned_user = await db.users.find_one(
            {"id": updated_item["assigned_to"]}, {"full_name": 1}
        )
        if assigned_user:
            updated_item["assigned_to_name"] = assigned_user.get("full_name")

    if updated_item.get("created_by"):
        creator = await db.users.find_one(
            {"id": updated_item["created_by"]}, {"full_name": 1}
        )
        if creator:
            updated_item["created_by_name"] = creator.get("full_name")

    return ComplianceItemResponse(**updated_item)


@api_router.get("/compliance-items/{item_id}/history")
async def get_compliance_item_history(
        item_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get history of updates for a compliance item from audit logs. Scoped to building."""
    # Allow owners read-only access to history
    allowed_roles = [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
        UserRole.OWNER,
    ]
    if _effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Access denied")

    logs = (
        await db.audit_logs.find(
            {"building_id": building_id, "resource_type": "compliance_item", "resource_id": item_id}, {"_id": 0}
        )
        .sort("created_at", -1)
        .to_list(100)
    )

    return logs


@api_router.delete("/compliance-items/{item_id}")
async def delete_compliance_item(
        item_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Delete a compliance checklist item (Chairman and Super Admin only). Scoped to building."""
    # Restrict to chairman and super admin
    if _effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
        raise HTTPException(
            status_code=403,
            detail="Only Chairman or Super Admin can delete compliance items",
        )

    result = await db.compliance_items.delete_one({"id": item_id, "building_id": building_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Compliance item not found")

    return {"message": "Compliance item deleted successfully"}


# ==================== DOCUMENT PERMISSION MANAGEMENT ====================


@api_router.put("/documents/{doc_id}/permissions")
async def update_document_permissions(
        doc_id: str,
        data: DocumentPermissionUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update document permissions. Scoped to building."""
    permissions = get_user_permissions(current_user)
    if not permissions.can_manage_document_permissions:
        raise HTTPException(
            status_code=403,
            detail="Only Chairman or Super Admin can manage document permissions",
        )

    result = await db.documents.update_one(
        {"id": doc_id, "building_id": building_id},
        {
            "$set": {
                "access_level": data.access_level,
                "allowed_roles": data.allowed_roles,
                "allowed_users": data.allowed_users,
                "permissions_updated_by": current_user["id"],
                "permissions_updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"message": "Document permissions updated"}


@api_router.get("/documents/{doc_id}/can-access")
async def check_document_access(
        doc_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Check if current user can access a specific document. Scoped to building."""
    doc = await db.documents.find_one({"id": doc_id, "building_id": building_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    user_role = current_user.get("role", "guest")
    user_id = current_user.get("id")
    access_level = doc.get("access_level", "all_members")

    # Chairman and Super Admin always have access
    if user_role in [UserRole.EC_MEMBER, UserRole.SUPER_ADMIN]:
        return {"can_access": True, "can_edit": True, "reason": "Admin access"}

    # Check specific user permissions
    if user_id in doc.get("allowed_users", []):
        return {"can_access": True, "can_edit": True, "reason": "Specific permission"}

    # Check role-based access
    access_rules = {
        "all_members": {
            "view": ["owner", "tenant", "ec_member", "strata_admin", "super_admin"],
            "edit": [],
        },
        "owners_view": {
            "view": ["owner", "ec_member", "strata_admin", "super_admin"],
            "edit": [],
        },
        "owners_edit": {
            "view": ["owner", "ec_member", "strata_admin", "super_admin"],
            "edit": ["owner", "ec_member", "strata_admin", "super_admin"],
        },
        "ec_view": {"view": ["ec_member", "strata_admin", "super_admin"], "edit": []},
        "ec_edit": {
            "view": ["ec_member", "strata_admin", "super_admin"],
            "edit": ["ec_member", "strata_admin", "super_admin"],
        },
        "chairman_only": {
            "view": ["strata_admin", "super_admin"],
            "edit": ["strata_admin", "super_admin"],
        },
    }

    rules = access_rules.get(access_level, access_rules["all_members"])
    can_view = user_role in rules["view"]
    can_edit = user_role in rules["edit"]

    return {"can_access": can_view, "can_edit": can_edit, "access_level": access_level}


# ==================== FINANCIAL PROJECTIONS ====================


# ==================== LEVY PAYMENTS ====================


# ==================== OWNERS/UNITS MANAGEMENT ROUTES ====================


@api_router.post("/owners-units", response_model=OwnerUnitResponse)
async def create_owner_unit(
        owner_unit: OwnerUnitCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a new owner/unit record (Super Admin only). Scoped to building."""
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check if unit already exists for this building
    existing = await db.units.find_one({"building_id": building_id, "unit_number": owner_unit.unit_number})
    if existing:
        raise HTTPException(status_code=400, detail="Unit already exists")

    owner_unit_dict = owner_unit.model_dump()
    owner_unit_dict["id"] = str(uuid.uuid4())
    owner_unit_dict["building_id"] = building_id
    owner_unit_dict["created_at"] = datetime.now(timezone.utc).isoformat()
    owner_unit_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
    owner_unit_dict["permissions"] = {}

    # Calculate totals if fund data provided
    if owner_unit_dict.get("admin_fund") and owner_unit_dict.get("sinking_fund"):
        admin_fund = owner_unit_dict["admin_fund"]
        sinking_fund = owner_unit_dict["sinking_fund"]
        owner_unit_dict["total_levied"] = (
                admin_fund.get("levied", 0)
                + admin_fund.get("special_levy", 0)
                + sinking_fund.get("levied", 0)
                + sinking_fund.get("special_levy", 0)
        )
        owner_unit_dict["total_paid"] = admin_fund.get("paid", 0) + sinking_fund.get(
            "paid", 0
        )
        owner_unit_dict["net_balance"] = admin_fund.get(
            "closing_balance", 0
        ) + sinking_fund.get("closing_balance", 0)

    await db.units.insert_one(owner_unit_dict)
    return OwnerUnitResponse(**owner_unit_dict)


@api_router.get("/owners-units", response_model=List[OwnerUnitResponse])
async def get_owners_units(
        skip: int = 0,
        limit: int = 100,
        search: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get all owner/unit records. Owners see their own unit only; EC/admin see all. Scoped to building."""
    allowed_roles = [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
        UserRole.OWNER,
    ]
    if _effective_role(current_user) not in allowed_roles:
        raise HTTPException(status_code=403, detail="Access denied")

    base_query = {"building_id": building_id}
    # Owners may only see their own unit
    if _effective_role(current_user) == UserRole.OWNER:
        base_query["unit_number"] = current_user.get("unit_number", "")

    # Performance Optimization⚡: Parallelize independent DB queries to reduce latency and remove redundant fallback.
    # Consolidates Stage 1 (Initial fetches) and Stage 2 (Dependent fetches) with asyncio.gather.
    from utils.finance_helpers import (
        get_latest_levy_year,
        get_latest_ledger_year,
        get_levy_rates,
    )

    # Stage 1: Initial fetches in parallel
    units_cursor = db.units.find(base_query, {"_id": 0})
    if search:
        units_task = units_cursor.to_list(None)
    else:
        units_task = units_cursor.skip(skip).limit(limit).to_list(limit)
    settings_task = _get_general_settings(building_id, {"_id": 0})
    levy_year_task = get_latest_levy_year(building_id)
    ledger_year_task = get_latest_ledger_year(building_id)

    # Performance Optimization⚡: Added return_exceptions=True and graceful error handling for parallel tasks.
    results = await asyncio.gather(
        units_task,
        settings_task,
        levy_year_task,
        ledger_year_task,
        return_exceptions=True,
    )

    # Handle units_task result - critical for proceeding
    units = results[0]
    if isinstance(units, Exception):
        logger.error(f"Error fetching units: {units}")
        raise HTTPException(
            status_code=500, detail="Error fetching units from database"
        )

    # Handle optional results
    settings = results[1] if not isinstance(results[1], Exception) else {}
    levy_year_res = results[2] if not isinstance(results[2], Exception) else None
    ledger_year_res = results[3] if not isinstance(results[3], Exception) else None

    if not units:
        return []

    _canonical_owners = await _get_all_unit_owners(building_id)
    if search:
        normalized_search = search.lower()
        filtered_units = []
        for unit in units:
            unit_num = str(unit.get("unit_number", ""))
            canonical = _canonical_owners.get(unit_num, {})
            owner_candidates = [
                canonical.get("owner_name"),
                canonical.get("owner_name_b"),
                unit.get("owner_name"),
                unit.get("owner_name_b"),
                unit.get("owner_email"),
                unit.get("owner_email_b"),
            ]
            text_matches = any(
                normalized_search in str(candidate).lower()
                for candidate in owner_candidates
                if candidate
            )
            if (
                    text_matches
                    or normalized_search in unit_num.lower()
                    or normalized_search in str(unit.get("lot_number", "")).lower()
            ):
                filtered_units.append(unit)
        units = filtered_units

    if search:
        units = units[skip: skip + limit]

        if not units:
            return []

    levy_frequency = (
        settings.get("levy_collection_frequency", "quarterly")
        if isinstance(settings, dict)
        else "quarterly"
    )
    payments_per_year = {
        "monthly": 12,
        "quarterly": 4,
        "half_yearly": 2,
        "yearly": 1,
    }.get(levy_frequency, 4)

    levy_year = levy_year_res or "2026"
    ledger_year = ledger_year_res or levy_year

    # Stage 2: Dependent fetches in parallel
    unit_numbers = [u.get("unit_number", "") for u in units]
    rates_task = get_levy_rates(levy_year, building_id)
    ledger_entries_task = db.unit_levy_ledger.find(
        {"building_id": building_id, "year": ledger_year, "unit_number": {"$in": unit_numbers}}, {"_id": 0}
    ).to_list(len(unit_numbers) + 10)
    # Bulk-fetch confirmed/pending levy_payments so we can adjust balance_owing
    # for payments not yet reconciled into the unit_levy_ledger.
    levy_payments_bulk_task = db.levy_payments.find(
        {
            "building_id": building_id,
            "year": ledger_year,
            "unit_number": {"$in": unit_numbers},
            "status": {"$in": ["confirmed", "pending_verification"]},
        },
        {"_id": 0, "unit_number": 1, "amount": 1},
    ).to_list(len(unit_numbers) * 8)

    # Check for active registered users on the platform (any resident role)
    # Filter by building membership
    memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
    building_user_ids = [m["user_id"] for m in memberships]

    active_users_task = db.users.find(
        {
            "id": {"$in": building_user_ids},
            "unit_number": {"$in": unit_numbers},
            "role": {
                "$in": [
                    UserRole.OWNER,
                    UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
                ]
            },
            "is_active": True,
            "is_approved": True,
        },
        {"unit_number": 1},
    ).to_list(None)

    # Derive tenanted status from active tenant registrations
    active_tenants_task = db.users.find(
        {
            "id": {"$in": building_user_ids},
            "unit_number": {"$in": unit_numbers},
            "role": UserRole.TENANT,
            "is_active": True,
            "is_approved": True,
        },
        {"unit_number": 1},
    ).to_list(None)

    # Performance Optimization⚡: Parallelize with error handling
    results_s2 = await asyncio.gather(
        rates_task,
        ledger_entries_task,
        active_users_task,
        active_tenants_task,
        levy_payments_bulk_task,
        return_exceptions=True,
    )

    levy_rates = (
        results_s2[0]
        if not isinstance(results_s2[0], Exception)
        else {
            "admin_annual": 0,
            "admin_quarterly": 0,
            "sinking_annual": 0,
            "sinking_quarterly": 0,
        }
    )
    ledger_entries = results_s2[1] if not isinstance(results_s2[1], Exception) else []
    active_users = results_s2[2] if not isinstance(results_s2[2], Exception) else []
    active_tenants = results_s2[3] if not isinstance(results_s2[3], Exception) else []
    levy_payments_bulk = results_s2[4] if not isinstance(results_s2[4], Exception) else []

    ledger_map = {e["unit_number"]: e for e in ledger_entries if isinstance(e, dict)}
    # Sum confirmed/pending levy_payments per unit for balance adjustment
    _lp_sum: dict = {}
    for _lp in (levy_payments_bulk or []):
        _un = _lp.get("unit_number", "")
        _lp_sum[_un] = round(_lp_sum.get(_un, 0.0) + _lp.get("amount", 0.0), 2)
    users_on_platform = {
        u.get("unit_number") for u in active_users if u.get("unit_number")
    }
    tenanted_units = {
        u.get("unit_number") for u in active_tenants if u.get("unit_number")
    }

    # Map unit data to OwnerUnitResponse format
    # Impersonation check
    is_impersonated = "impersonator_id" in current_user

    # Pre-compute period due dates once for all units in this request
    from utils.finance_helpers import (
        compute_period_due_dates as _cpdd_list,
        compute_period_installment_amounts as _compute_period_installment_amounts,
        compute_next_estimated_payment as _compute_next_estimated_payment,
        normalize_effective_total_paid as _normalize_effective_total_paid,
    )

    _list_due_dates = []
    _list_grace_days = (
        int(settings.get("grace_period_days", 14))
        if isinstance(settings, dict)
        else 14
    )
    _list_interest_rate_per_month = (
        float(settings.get("interest_rate_per_month", 0.0) or 0.0)
        if isinstance(settings, dict)
        else 0.0
    )
    _list_penalty_amount = (
        float(settings.get("penalty_amount", 0.0) or 0.0)
        if isinstance(settings, dict)
        else 0.0
    )
    try:
        _list_levy_due_months = (
            settings.get("levy_due_months", [3, 6, 9, 12])
            if isinstance(settings, dict)
            else [3, 6, 9, 12]
        )
        _list_levy_due_day_type = (
            settings.get("levy_due_day_type", "first")
            if isinstance(settings, dict)
            else "first"
        )
        _list_levy_due_day = (
            settings.get("levy_due_day") if isinstance(settings, dict) else None
        )
        _list_levy_due_custom = (
            settings.get("levy_due_custom_dates") if isinstance(settings, dict) else None
        )
        _list_levy_year_int = (
            int(ledger_year) if str(ledger_year).isdigit() else datetime.now().year
        )
        _list_due_dates = _cpdd_list(
            _list_levy_year_int,
            _list_levy_due_months,
            _list_levy_due_day_type,
            _list_levy_due_day,
            payments_per_year,
            _list_levy_due_custom,
        )
    except Exception:
        _list_due_dates = []

    owners_units = []
    for unit in units:
        uoe = unit.get("entitlement", 0)
        admin_annual = round(levy_rates.get("admin_annual", 0) * uoe, 2)
        sinking_annual = round(levy_rates.get("sinking_annual", 0) * uoe, 2)
        annual_levy = admin_annual + sinking_annual
        period_levy = round(annual_levy / payments_per_year, 2)
        period_amounts = _compute_period_installment_amounts(
            annual_levy,
            payments_per_year,
        )

        # Get ledger data for this unit (historical actuals from ledger_year)
        unit_num = unit.get("unit_number", "")
        ledger = ledger_map.get(unit_num, {})
        net_balance = ledger.get("net_balance", 0.0)
        # Adjust balance for confirmed/pending levy_payments not yet in ledger
        _lp_total = _lp_sum.get(unit_num, 0.0)
        opening_arrears = round(
            ledger.get("admin_opening", 0.0) + ledger.get("sinking_opening", 0.0), 2
        )
        carry_forward_balance = round(
            ledger.get("total_opening", opening_arrears) or opening_arrears,
            2,
        )
        _effective_total_paid = _normalize_effective_total_paid(
            ledger_total_paid=ledger.get("effective_total_paid", ledger.get("total_paid", 0.0)),
            live_payments_total=_lp_total,
            carry_forward_balance=carry_forward_balance,
        )
        _l_unreconciled = max(0.0, _effective_total_paid - ledger.get("total_paid", 0.0))
        effective_net_balance = net_balance - _l_unreconciled
        balance_owing = max(0.0, effective_net_balance)
        balance_credit = abs(min(0.0, effective_net_balance))

        # Format lot number: pad to 3 chars, removing existing "LOT" prefix
        lot = str(unit.get("lot_number", ""))
        clean_lot = re.sub(r"^LOT\s*", "", lot, flags=re.IGNORECASE)
        formatted_lot = clean_lot.zfill(3) if clean_lot else ""

        # PII Masking during impersonation
        _canonical = _canonical_owners.get(unit_num, {})
        owner_name = _canonical.get("owner_name") or unit.get("owner_name")
        owner_name_b = _canonical.get("owner_name_b") or unit.get("owner_name_b")
        owner_email = _canonical.get("owner_email") or unit.get("owner_email")
        owner_email_b = unit.get("owner_email_b")
        tenant_name = unit.get("tenant_name")
        tenant_email = unit.get("tenant_email")

        if is_impersonated:
            owner_name = "Resident"
            if owner_name_b:
                owner_name_b = "Resident"
            owner_email = mask_email(owner_email)
            if owner_email_b:
                owner_email_b = mask_email(owner_email_b)
            tenant_name = "Resident"
            tenant_email = mask_email(tenant_email)

        # Unified next-payment logic (same formula as single-unit endpoint):
        # before / within grace -> next unfunded current-year instalment + carried prior-year arrears
        # after grace          -> overdue shortfall + next period levy + current
        #                        interest / fixed penalty from Site Settings + carried prior-year arrears
        # Use bulk levy_payments sum so unreconciled payments are reflected here too.
        _next_payment_data = _compute_next_estimated_payment(
            effective_total_paid=_effective_total_paid,
            opening_arrears=opening_arrears,
            period_levy=period_levy,
            due_dates=_list_due_dates,
            period_amounts=period_amounts,
            today=datetime.now(),
            grace_period_days=_list_grace_days,
            interest_rate_per_month=_list_interest_rate_per_month,
            penalty_amount=_list_penalty_amount,
        )
        _list_next_payment = _next_payment_data["next_payment_adjusted"]
        _list_next_due = _next_payment_data["next_due_date"]

        owner_unit = {
            "id": unit.get("id", str(uuid.uuid4())),
            "lot_number": formatted_lot,
            "unit_number": unit_num,
            "unit_type": unit.get("unit_type", "apartment"),
            "owner_name": owner_name or "Unknown",
            "owner_name_b": owner_name_b,
            "owner_email": owner_email,
            "owner_email_b": owner_email_b,
            "bedrooms": unit.get("bedrooms"),
            "bathrooms": unit.get("bathrooms"),
            "garage_spaces": unit.get("car_spaces"),
            "level": unit.get("level"),
            "unit_entitlement": int(uoe),
            # Use the stored is_owner_occupied flag as the single source of truth.
            # An active tenant registration acts as a fallback only when the field
            # has never been explicitly set on the unit document.
            "is_owner_occupied": unit.get("is_owner_occupied", unit_num not in tenanted_units),
            "occupancy_type": unit.get("occupancy_type", "rented" if unit_num in tenanted_units else "owner_occupied"),
            "tenant_name": tenant_name,
            "tenant_email": tenant_email,
            "purchase_date": unit.get("purchase_date"),
            "approval_date": unit.get("approval_date"),
            "notes": unit.get("notes"),
            "permissions": unit.get("permissions", {}),
            "is_on_platform": unit_num in users_on_platform,
            "admin_fund": {
                "annual": admin_annual,
                "quarterly": round(admin_annual / 4, 2),
            },
            "sinking_fund": {
                "annual": sinking_annual,
                "quarterly": round(sinking_annual / 4, 2),
            },
            "total_levied": annual_levy if annual_levy > 0 else ledger.get("total_levied", 0.0),
            # full-year committed amount (rates × UOE)
            "total_paid": _effective_total_paid,
            "net_balance": net_balance,
            "balance_owing": balance_owing,
            "balance_credit": balance_credit,
            "opening_arrears": opening_arrears,
            "carry_forward_arrears": round(
                max(0.0, _next_payment_data.get("carry_forward_arrears", max(0.0, opening_arrears))), 2),
            "outstanding_current": round(_next_payment_data.get("outstanding_current", 0.0), 2),
            "period_levy": round(period_levy, 2),
            # Computed above: accounts for prior-year credit/arrears + in-year payments
            "next_payment_adjusted": _list_next_payment,
            "next_due_date": _list_next_due,
            "admin_closing_balance": ledger.get("admin_closing", 0.0),
            "sinking_closing_balance": ledger.get("sinking_closing", 0.0),
            # _iso() coerces a datetime (some seed/import paths write native datetime
            # objects instead of ISO strings) to a string — OwnerUnitResponse requires
            # str and raises a 500 ValidationError otherwise. unit.get(key, fallback)
            # only uses the fallback when the key is ABSENT, not when it's present with
            # the wrong type, so the raw datetime previously passed straight through.
            "created_at": _iso(unit.get("created_at")) or datetime.now(timezone.utc).isoformat(),
            "updated_at": _iso(unit.get("updated_at")) or datetime.now(timezone.utc).isoformat(),
        }
        owners_units.append(owner_unit)

    return [OwnerUnitResponse(**ou) for ou in owners_units]


@api_router.get("/owners-units/{unit_number}", response_model=OwnerUnitResponse)
async def get_owner_unit(
        unit_number: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        financial_year: Optional[str] = None,
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """Get owner/unit record by unit number with optional year filtering. Scoped to building."""
    # Owners can view their own unit; admins/managers/EC see any unit
    _UNIT_ADMIN_ROLES = frozenset({
        UserRole.SUPER_ADMIN,
        UserRole.STRATA_ADMIN,
        UserRole.EC_MEMBER,
        UserRole.STRATA_MANAGER,
    })
    if _effective_role(current_user) not in _UNIT_ADMIN_ROLES:
        # Check if this is the user's own unit
        if unit_number != current_user.get("unit_number"):
            raise HTTPException(status_code=403, detail="Access denied")

    # Performance Optimization⚡: Parallelize independent DB queries to reduce latency.
    # Consolidates Stage 1 (Years, Settings, Initial unit) and Stage 2 (Rates, Ledgers, Badges) with asyncio.gather.
    from utils.finance_helpers import (
        get_latest_levy_year,
        get_latest_ledger_year,
        get_levy_rates,
    )

    # Stage 1: Parallel fetches for base data
    unit_task = db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
    settings_task = _get_general_settings(building_id, {"_id": 0})
    listing_count_task = db.listings.count_documents({"building_id": building_id, "created_by": current_user["id"]})

    # Performance Optimization⚡: Added return_exceptions=True and graceful error handling for parallel tasks.
    if not financial_year:
        levy_year_task = get_latest_levy_year(building_id)
        ledger_year_task = get_latest_ledger_year(building_id)
        results_s1 = await asyncio.gather(
            unit_task,
            settings_task,
            listing_count_task,
            levy_year_task,
            ledger_year_task,
            return_exceptions=True,
        )
        unit = results_s1[0] if not isinstance(results_s1[0], Exception) else None
        settings = results_s1[1] if not isinstance(results_s1[1], Exception) else {}
        listing_count = results_s1[2] if not isinstance(results_s1[2], Exception) else 0
        levy_year_res = (
            results_s1[3] if not isinstance(results_s1[3], Exception) else None
        )
        ledger_year_res = (
            results_s1[4] if not isinstance(results_s1[4], Exception) else None
        )

        levy_year = levy_year_res or "2026"
        ledger_year = ledger_year_res or levy_year
    else:
        results_s1 = await asyncio.gather(
            unit_task, settings_task, listing_count_task, return_exceptions=True
        )
        unit = results_s1[0] if not isinstance(results_s1[0], Exception) else None
        settings = results_s1[1] if not isinstance(results_s1[1], Exception) else {}
        listing_count = results_s1[2] if not isinstance(results_s1[2], Exception) else 0
        levy_year = financial_year
        ledger_year = financial_year

    if not unit:
        raise HTTPException(status_code=404, detail="Owner/unit not found")

    # Stage 2: Parallel fetches for dependent financial data
    unit_query = (
        {"$or": [{"unit_number": str(unit_number)}, {"unit_number": int(unit_number)}]}
        if str(unit_number).isdigit()
        else {"unit_number": str(unit_number)}
    )
    prev_year_str = str(int(ledger_year) - 1) if ledger_year.isdigit() else None

    # Filter by building membership
    memberships = await db.memberships.find({"building_id": building_id}).to_list(None)
    building_user_ids = [m["user_id"] for m in memberships]

    rates_task = get_levy_rates(levy_year, building_id)
    levy_doc_task = db.annual_levies.find_one(
        {"building_id": building_id, "year": levy_year}, {"_id": 0}
    )
    ledger_task = db.unit_levy_ledger.find_one(
        {"building_id": building_id, **unit_query, "year": ledger_year}, {"_id": 0}
    )
    active_user_task = db.users.find_one(
        {
            "id": {"$in": building_user_ids},
            "unit_number": unit_number,
            "role": UserRole.OWNER,
            "is_active": True,
            "is_approved": True,
        },
        {"_id": 1},
    )
    # Fetch live levy payments (confirmed + pending) so dashboard reflects self-reported DEFT/BPAY
    levy_payments_task = db.levy_payments.find(
        {
            "building_id": building_id,
            "unit_number": unit_number,
            "year": ledger_year,
            "status": {"$in": ["confirmed", "pending_verification"]},
        },
        {"_id": 0, "amount": 1},
    ).to_list(20)

    # Performance Optimization⚡: Added return_exceptions=True and graceful error handling for parallel tasks.
    if prev_year_str:
        prev_ledger_task = db.unit_levy_ledger.find_one(
            {"building_id": building_id, **unit_query, "year": prev_year_str}, {"_id": 0}
        )
        results_s2 = await asyncio.gather(
            rates_task,
            levy_doc_task,
            ledger_task,
            prev_ledger_task,
            active_user_task,
            levy_payments_task,
            return_exceptions=True,
        )
        levy_rates = (
            results_s2[0]
            if not isinstance(results_s2[0], Exception)
            else {
                "admin_annual": 0,
                "admin_quarterly": 0,
                "sinking_annual": 0,
                "sinking_quarterly": 0,
            }
        )
        levy = results_s2[1] if not isinstance(results_s2[1], Exception) else None
        ledger_res = results_s2[2] if not isinstance(results_s2[2], Exception) else {}
        ledger = ledger_res or {}
        prev_ledger = results_s2[3] if not isinstance(results_s2[3], Exception) else {}
        is_on_platform = results_s2[4] is not None and not isinstance(
            results_s2[4], Exception
        )
        levy_payments_docs = (
            results_s2[5] if not isinstance(results_s2[5], Exception) else []
        )
    else:
        results_s2 = await asyncio.gather(
            rates_task,
            levy_doc_task,
            ledger_task,
            active_user_task,
            levy_payments_task,
            return_exceptions=True,
        )
        levy_rates = (
            results_s2[0]
            if not isinstance(results_s2[0], Exception)
            else {
                "admin_annual": 0,
                "admin_quarterly": 0,
                "sinking_annual": 0,
                "sinking_quarterly": 0,
            }
        )
        levy = results_s2[1] if not isinstance(results_s2[1], Exception) else None
        ledger_res = results_s2[2] if not isinstance(results_s2[2], Exception) else {}
        ledger = ledger_res or {}
        prev_ledger = None
        is_on_platform = results_s2[3] is not None and not isinstance(
            results_s2[3], Exception
        )
        levy_payments_docs = (
            results_s2[4] if not isinstance(results_s2[4], Exception) else []
        )

    # Use live levy_payments total when available (more real-time than ledger for current year)
    direct_payments_total = round(
        sum(p.get("amount", 0) for p in (levy_payments_docs or [])), 2
    )

    from utils.finance_helpers import (
        compute_period_due_dates as _cpdd,
        compute_period_installment_amounts as _compute_period_installment_amounts,
        compute_next_estimated_payment as _compute_next_estimated_payment,
        compute_remaining_payment_obligation as _compute_remaining_payment_obligation,
        normalize_effective_total_paid as _normalize_effective_total_paid,
    )

    # Derive levy from UOE × rates
    levy_frequency = (
        settings.get("levy_collection_frequency", "quarterly")
        if settings
        else "quarterly"
    )
    payments_per_year = {
        "monthly": 12,
        "quarterly": 4,
        "half_yearly": 2,
        "yearly": 1,
    }.get(levy_frequency, 4)

    uoe = unit.get("entitlement", 0)
    admin_annual = round(levy_rates.get("admin_annual", 0) * uoe, 2)
    sinking_annual = round(levy_rates.get("sinking_annual", 0) * uoe, 2)
    annual_levy = admin_annual + sinking_annual
    period_levy = round(annual_levy / payments_per_year, 2)
    period_amounts = _compute_period_installment_amounts(
        annual_levy,
        payments_per_year,
    )

    # Use current year's opening_arrears as the carry-forward (post-reconciliation).
    # prev_ledger.net_balance is a DB snapshot that may not reflect payments made in
    # Jan/Feb before the financial year starts (e.g. owners settling FY2025 balance early).
    # opening_arrears is derived from: Civium_balance − Q1_levy and accurately represents
    # the actual carry-forward after all transition-period payments.
    if ledger and ledger.get("opening_arrears", 0.0) > 0:
        prev_year_balance = round(ledger.get("opening_arrears", 0.0), 2)
    elif ledger and ledger.get("total_opening") not in (None, ""):
        prev_year_balance = round(ledger.get("total_opening", 0.0), 2)
    elif prev_ledger:
        # Fallback: use prior-year net_balance (works for credit carry-forwards)
        prev_year_balance = round(prev_ledger.get("net_balance", 0.0), 2)
    else:
        prev_year_balance = 0.0

    # If no ledger data found for the selected year, synthesize from previous year closing
    # balances + current year levy rates (covers current/future years not yet in ledger)
    if not ledger and ledger_year.isdigit():
        # Optimization Bolt ⚡: prev_ledger is already fetched if it exists
        synth_source = prev_ledger or {}
        admin_opening = round(synth_source.get("admin_closing", 0.0), 2)
        sinking_opening = round(synth_source.get("sinking_closing", 0.0), 2)
        ledger = {
            "admin_opening": admin_opening,
            "admin_levied": admin_annual,
            "admin_paid": 0.0,
            "admin_closing": round(admin_opening + admin_annual, 2),
            "sinking_opening": sinking_opening,
            "sinking_levied": sinking_annual,
            "sinking_paid": 0.0,
            "sinking_closing": round(sinking_opening + sinking_annual, 2),
            "total_levied": annual_levy,
            "total_paid": 0.0,
            # net_balance = carry-forward from previous year only; current-year levies not yet due
            "net_balance": round(synth_source.get("net_balance", 0.0), 2),
        }

    effective_total_paid = _normalize_effective_total_paid(
        ledger_total_paid=ledger.get("total_paid", 0.0),
        live_payments_total=direct_payments_total,
        carry_forward_balance=prev_year_balance,
    )

    net_balance = ledger.get("net_balance", 0.0)

    # Adjust balance for confirmed levy_payments not yet reconciled into the ledger.
    # effective_total_paid = max(ledger.total_paid, live levy_payments sum).
    # If the owner has made a confirmed payment that hasn't been posted to the ledger
    # yet, the raw net_balance overstates what they still owe.  Both balance_owing
    # and next_payment_adjusted must use the same effective basis so they agree.
    _unreconciled = max(0.0, effective_total_paid - ledger.get("total_paid", 0.0))
    effective_net_balance = net_balance - _unreconciled
    balance_owing = max(0.0, effective_net_balance)
    balance_credit = abs(min(0.0, effective_net_balance))

    # True carry-forward debt (excludes current-year undue levies)
    opening_arrears = round(
        ledger.get("admin_opening", 0.0) + ledger.get("sinking_opening", 0.0), 2
    )

    # Compute period due dates and status for badge/sub-label logic
    _due_dates = []  # initialised before try so payment logic below can always reference it
    _today = datetime.now()  # defined here so both try block and payment logic use it
    _grace_days = int(settings.get("grace_period_days", 14)) if settings else 14
    _grace = timedelta(days=_grace_days)
    _interest_rate_per_month = float(settings.get("interest_rate_per_month", 0.0) or 0.0) if settings else 0.0
    _penalty_amount = float(settings.get("penalty_amount", 0.0) or 0.0) if settings else 0.0
    try:
        _levy_due_months = (
            settings.get("levy_due_months", [3, 6, 9, 12])
            if settings
            else [3, 6, 9, 12]
        )
        _levy_due_day_type = (
            settings.get("levy_due_day_type", "first") if settings else "first"
        )
        _levy_due_day = settings.get("levy_due_day") if settings else None
        _levy_due_custom = settings.get("levy_due_custom_dates") if settings else None
        _levy_year_int = (
            int(ledger_year) if str(ledger_year).isdigit() else datetime.now().year
        )
        _due_dates = _cpdd(
            _levy_year_int,
            _levy_due_months,
            _levy_due_day_type,
            _levy_due_day,
            payments_per_year,
            _levy_due_custom,
        )
        _overdue_dates = [
            d for d in _due_dates if datetime.strptime(d, "%Y-%m-%d") + _grace < _today
        ]
        _upcoming_dates = [
            d for d in _due_dates if datetime.strptime(d, "%Y-%m-%d") >= _today
        ]
        # Current due date: first upcoming, or last past if all overdue
        _current_due = (
            _upcoming_dates[0]
            if _upcoming_dates
            else (_due_dates[-1] if _due_dates else None)
        )
        period_status_val = {
            "any_overdue": len(_overdue_dates) > 0,
            "overdue_count": len(_overdue_dates),
            "total_periods": payments_per_year,
            "grace_period_days": _grace_days,
            "current_due_date": _current_due,
        }
        next_due_date_val = _current_due
    except Exception:
        period_status_val = {
            "any_overdue": False,
            "overdue_count": 0,
            "grace_period_days": _grace_days,
            "current_due_date": None,
        }
        next_due_date_val = None

    # ── Next Estimated Payment & Next Due Date ──────────────────────────────
    # Current-year instalments are funded only by current-year payments plus any
    # prior-year credit carried in. Prior-year arrears remain additive on top of
    # the next amount due instead of reducing the count of funded periods.
    #
    #   funding_base = effective_total_paid + max(0, -opening_arrears)
    #   carry_arrears = max(0, opening_arrears)
    #   periods_funded = floor(max(0, funding_base) / period_levy)
    #   outstanding_current = (periods_funded + 1) × period_levy − funding_base
    #
    #   Before / within grace:
    #     next_payment = outstanding_current + carry_arrears
    #     next_due     = due_dates[periods_funded]
    #
    #   After grace:
    #     next_payment = outstanding_current + carry_arrears + next period levy
    #                  + current interest + fixed penalty from Site Settings
    #     next_due     = due_dates[periods_funded + 1]
    #
    # Example (period_levy = $1,530.30, opening_arrears = $200):
    #   paid $1,530.30 before grace → Q1 fully funded, next due = Q2,
    #   next payment = $1,530.30 + $200 carry-forward arrears.
    _next_payment_data = _compute_next_estimated_payment(
        effective_total_paid=effective_total_paid,
        opening_arrears=opening_arrears,
        period_levy=period_levy,
        due_dates=_due_dates,
        period_amounts=period_amounts,
        today=_today,
        grace_period_days=_grace_days,
        interest_rate_per_month=_interest_rate_per_month,
        penalty_amount=_penalty_amount,
    )
    _next_payment = _next_payment_data["next_payment_adjusted"]
    next_due_date_val = _next_payment_data["next_due_date"]

    # Calculate badges (on-the-fly)
    badges = []
    # 1. Fully Paid badge
    if net_balance <= 0:
        badges.append(
            {
                "id": "paid_up",
                "label": f"Fully Paid {ledger_year}",
                "icon": "check-circle",
                "description": f"All {ledger_year} levies paid in full",
            }
        )

    # 2. Community Contributor badge (listing_count already fetched in Stage 1 Bolt ⚡)
    if listing_count > 0:
        badges.append(
            {
                "id": "marketplace_seller",
                "label": "Community Seller",
                "icon": "shopping-bag",
                "description": "Contributed to the community marketplace",
            }
        )

    # Personal Financial Projection Widget Data
    # effective_total_paid includes self-reported DEFT/BPAY payments pending verification
    yearly_forecast = {
        "total_levies": annual_levy,
        "paid_so_far": round(effective_total_paid, 2),
        "remaining": round(
            max(
                0.0,
                _compute_remaining_payment_obligation(
                    total_annual=annual_levy,
                    total_paid=effective_total_paid,
                    prev_year_balance=prev_year_balance,
                ),
            ),
            2,
        ),
        "next_due_dates": [
            ps.get("due_date")
            for ps in (levy.get("payment_schedule", []) if levy else [])
            if ps.get("due_date", "") > datetime.now().isoformat()
        ][:3],
    }

    # Mask PII during impersonation
    is_impersonated = "impersonator_id" in current_user

    # Format lot number: pad to 3 chars, removing existing "LOT" prefix
    lot = str(unit.get("lot_number", ""))
    clean_lot = re.sub(r"^LOT\s*", "", lot, flags=re.IGNORECASE)
    formatted_lot = clean_lot.zfill(3) if clean_lot else ""

    # PII Masking during impersonation
    _canonical = await _get_owner_info(unit.get("unit_number", unit_number), building_id)
    owner_name = _canonical.get("owner_name") or unit.get("owner_name")
    owner_name_b = _canonical.get("owner_name_b") or unit.get("owner_name_b")
    owner_email = _canonical.get("owner_email") or unit.get("owner_email")
    owner_email_b = _canonical.get("owner_email_b") or unit.get("owner_email_b")
    tenant_name = unit.get("tenant_name")
    tenant_email = unit.get("tenant_email")

    if is_impersonated:
        owner_name = "Resident"
        if owner_name_b:
            owner_name_b = "Resident"
        owner_email = mask_email(owner_email)
        if owner_email_b:
            owner_email_b = mask_email(owner_email_b)
        tenant_name = "Resident"
        tenant_email = mask_email(tenant_email)

    owner_unit = {
        "id": unit.get("id", str(uuid.uuid4())),
        "lot_number": formatted_lot,
        "unit_number": unit.get("unit_number", ""),
        "unit_type": unit.get("unit_type", "apartment"),
        "owner_name": owner_name or "Unknown",
        "owner_name_b": owner_name_b,
        "owner_email": owner_email,
        "owner_email_b": owner_email_b,
        "bedrooms": unit.get("bedrooms"),
        "bathrooms": unit.get("bathrooms"),
        "garage_spaces": unit.get("car_spaces"),
        "level": unit.get("level"),
        "unit_entitlement": int(uoe),
        "is_owner_occupied": unit.get("is_owner_occupied", True),
        "occupancy_type": unit.get("occupancy_type",
                                   "owner_occupied" if unit.get("is_owner_occupied", True) else "rented"),
        "tenant_name": tenant_name,
        "tenant_email": tenant_email,
        "purchase_date": unit.get("purchase_date"),
        "approval_date": unit.get("approval_date"),
        "notes": unit.get("notes"),
        "permissions": unit.get("permissions", {}),
        "is_on_platform": is_on_platform,
        "admin_fund": {
            "annual": admin_annual,
            "quarterly": round(admin_annual / 4, 2),
            # Per-fund actuals from unit_levy_ledger (0 if ledger not found)
            "opening_balance": ledger.get("admin_opening", 0.0),
            "levied": ledger.get("admin_levied", 0.0),
            "paid": ledger.get("admin_paid", 0.0),
            "closing_balance": ledger.get("admin_closing", 0.0),
        },
        "sinking_fund": {
            "annual": sinking_annual,
            "quarterly": round(sinking_annual / 4, 2),
            "opening_balance": ledger.get("sinking_opening", 0.0),
            "levied": ledger.get("sinking_levied", 0.0),
            "paid": ledger.get("sinking_paid", 0.0),
            "closing_balance": ledger.get("sinking_closing", 0.0),
        },
        # Display the full-year levy commitment on owner-facing screens, not only the
        # levied-to-date ledger amount for the selected period.
        "total_levied": annual_levy if annual_levy > 0 else ledger.get("total_levied", 0.0),
        "total_paid": round(effective_total_paid, 2),
        "net_balance": net_balance,
        "balance_owing": balance_owing,
        "balance_credit": balance_credit,
        "opening_arrears": opening_arrears,
        "carry_forward_arrears": round(
            max(0.0, _next_payment_data.get("carry_forward_arrears", max(0.0, opening_arrears))), 2),
        "outstanding_current": round(_next_payment_data.get("outstanding_current", 0.0), 2),
        "period_levy": round(period_levy, 2),
        # Computed above: accounts for prior-year credit + in-year payments
        "next_payment_adjusted": _next_payment,
        "next_due_date": next_due_date_val,
        "period_status": period_status_val,
        "admin_closing_balance": ledger.get("admin_closing", 0.0),
        "sinking_closing_balance": ledger.get("sinking_closing", 0.0),
        "yearly_forecast": yearly_forecast,
        "badges": badges,
        # Pass arrears_metadata through so DCA status, notice sent, and payment plan
        # are visible on the unit detail page without a separate API call
        "arrears_metadata": unit.get("arrears_metadata"),
        # _iso() coerces a datetime (some seed/import paths write native datetime
        # objects instead of ISO strings) to a string — OwnerUnitResponse requires str
        # and raises a 500 ValidationError otherwise. Confirmed live on UPDEMO5
        # (demo tenant): unit.get(key, fallback) only uses the fallback when the key is
        # ABSENT, not when present with the wrong type, so the raw datetime previously
        # passed straight through untouched.
        "created_at": _iso(unit.get("created_at")) or datetime.now(timezone.utc).isoformat(),
        "updated_at": _iso(unit.get("updated_at")) or datetime.now(timezone.utc).isoformat(),
    }

    return OwnerUnitResponse(**owner_unit)


# @featuretrace:owner-transfers — cascade side-effects when a unit's owner/occupancy changes.
# Layer: router
# Data flow: OwnersUnitsPage → PUT /api/owners-units/{unit} → units, strata_owners,
#            agm_attendance, ec_members, chat_groups, rental_certificates (building-scoped).
# Related: frontend/src/app/(dashboard)/dashboard/owners-units/page.tsx
#          frontend/src/app/(dashboard)/dashboard/admin/strata-roll/page.tsx
#          frontend/src/app/(dashboard)/dashboard/arrears/page.tsx
#          backend/routers/finance.py  (arrears provenance fields)
async def _cascade_owner_change(
        building_id: str,
        unit_number: str,
        old_unit: dict,
        update_dict: dict,
        actor: dict,
) -> None:
    """
    Side-effects triggered when owner/occupancy data changes on a unit.
    Called as asyncio.create_task() AFTER db.units has been written so old_unit
    holds the pre-update snapshot.

    1. Sync strata_owners (always) — prevents stale strata roll.
    2. Owner name changed → snapshot inherited arrears, flag AGM proxies,
       alert if old owner is on EC list, remove from unit-type chat groups.
    3. Unit flips tenanted→owner-occupied → supersede issued rental certificates.
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── 1. Sync strata_owners ───────────────────────────────────────────────
    # Only update owner-display and UOE fields.
    # strata_owners.lot / .unit are integer lot numbers set at import time;
    # units.lot_number is a STRING like "LOT71" — do NOT overwrite integer fields.
    _owner_a = (update_dict.get("owner_name") or old_unit.get("owner_name") or "")
    _owner_b = (update_dict.get("owner_name_b") or old_unit.get("owner_name_b") or "")
    new_owner_name = format_owner_names(_owner_a, _owner_b)
    new_uoe = (
            update_dict.get("entitlement")
            or update_dict.get("unit_entitlement")
            or old_unit.get("entitlement")
            or old_unit.get("unit_entitlement")
            or 0
    )
    # upsert=False: strata_owners.lot/unit require integer values we don't have here;
    # only update the record if it already exists from a prior strata sync.
    await db.strata_owners.update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {"$set": {"owner": new_owner_name, "uoe": new_uoe, "updated_at": now}},
        upsert=False,
    )

    # ── 2. Owner name changed → ownership transfer ──────────────────────────
    old_name = (old_unit.get("owner_name") or "").strip()
    new_name = (update_dict.get("owner_name") or old_name).strip()
    owner_changed = bool(old_name and new_name and old_name != new_name)

    if owner_changed:
        # 2a. Snapshot inherited arrears — compute from current ledger + payments (authoritative),
        # not from stale units.balance_owing.  Derive the current year from the running clock.
        _transfer_year = str(datetime.now(timezone.utc).year)
        _ledger_doc = await db.unit_levy_ledger.find_one(
            {"building_id": building_id, "year": _transfer_year, "unit_number": unit_number},
            {"_id": 0, "net_balance": 1, "total_paid": 1},
        )
        _live_payments = await db.levy_payments.find(
            {"building_id": building_id, "year": _transfer_year, "unit_number": unit_number,
             "status": {"$in": ["confirmed", "pending_verification"]}},
            {"_id": 0, "amount": 1},
        ).to_list(None)
        _live_paid_sum = sum(float(p.get("amount", 0)) for p in _live_payments)
        if _ledger_doc:
            _net = float(_ledger_doc.get("net_balance", 0))
            _ledger_paid = float(_ledger_doc.get("total_paid", 0))
            _unreconciled = max(0.0, _live_paid_sum - _ledger_paid)
            prev_owing = max(0.0, round(_net - _unreconciled, 2))
        else:
            prev_owing = 0.0
        if prev_owing > 0:
            await db.units.update_one(
                {"building_id": building_id, "unit_number": unit_number},
                {"$set": {
                    "arrears_metadata.previous_owner": old_name,
                    "arrears_metadata.previous_owner_b": old_unit.get("owner_name_b") or "",
                    "arrears_metadata.inherited_arrears": round(prev_owing, 2),
                    "arrears_metadata.transferred_at": now,
                }},
            )

        # 2b. Find users linked to this unit via user_units (authoritative mapping).
        # Do NOT use db.users filtered by role — EC members/chairman who own units
        # have role != "owner" and would be missed.
        old_unit_links = await db.user_units.find(
            {"building_id": building_id, "unit_number": unit_number, "is_active": True},
            {"_id": 0, "user_id": 1},
        ).to_list(10)
        old_user_ids = [r["user_id"] for r in old_unit_links]

        # 2c. Find building admins for notifications (query once, reuse below)
        admin_users = await db.users.find(
            {"role": {"$in": [UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER]},
             "is_active": True},
            {"_id": 0, "id": 1},
        ).to_list(50)
        # Scope to this building via memberships
        building_member_ids = {
            m["user_id"] for m in
            await db.memberships.find(
                {"building_id": building_id, "is_active": True},
                {"_id": 0, "user_id": 1},
            ).to_list(200)
        }
        admin_ids = [a["id"] for a in admin_users if a["id"] in building_member_ids]

        # 2d. Flag any AGM proxies on upcoming AGMs for the old owner users
        # AGM status "upcoming" is the only pre-meeting status used in this system.
        if old_user_ids:
            active_agms = await db.agm.find(
                {"building_id": building_id, "status": "upcoming"},
                {"_id": 0, "id": 1, "title": 1},
            ).to_list(5)

            for agm_doc in active_agms:
                proxy_found = False
                for uid in old_user_ids:
                    proxy_rec = await db.agm_attendance.find_one({
                        "building_id": building_id,
                        "agm_id": agm_doc["id"],
                        "user_id": uid,
                        "proxy_id": {"$exists": True, "$ne": None},
                    })
                    if proxy_rec:
                        proxy_found = True
                        break
                if proxy_found:
                    for admin_id in admin_ids:
                        await create_user_notification(
                            admin_id,
                            f"AGM Proxy Review Required — Unit {unit_number}",
                            (f"Ownership of Unit {unit_number} transferred from {old_name} "
                             f"to {new_name}. The previous owner has a proxy registered "
                             f"for '{agm_doc['title']}'. Please review and void if required."),
                            "warning",
                            link="/governance/agm",
                            building_id=building_id,
                        )

        # 2e. Alert admins if old owner appears on EC members list
        old_email = old_unit.get("owner_email")
        ec_or_clause = [{"name": old_name}]
        if old_email:
            ec_or_clause.append({"email": old_email})
        ec_member = await db.ec_members.find_one(
            {"building_id": building_id, "$or": ec_or_clause}
        )
        if ec_member:
            for admin_id in admin_ids:
                await create_user_notification(
                    admin_id,
                    f"EC Member Record Needs Review — Unit {unit_number}",
                    (f"Unit {unit_number} ownership changed from {old_name} to {new_name}. "
                     f"{old_name} is currently listed as EC {ec_member.get('position', 'Member')}. "
                     f"Please update the EC Members list and the /about page."),
                    "warning",
                    link="/governance/ec-members",
                    building_id=building_id,
                )

        # 2f. Remove old users from unit-based chat groups (apartments/townhouses).
        # Filter on type=="unit_based" covers both old-style (unit_types key) and
        # new-style (unit_pattern key) groups — do NOT filter on a specific nested key.
        for uid in old_user_ids:
            await db.chat_groups.update_many(
                {"building_id": building_id, "type": "unit_based", "members.user_id": uid},
                {"$pull": {"members": {"user_id": uid}}},
            )

    # ── 3. Tenanted → owner-occupied: supersede issued rental certs ─────────
    was_owner_occupied = bool(old_unit.get("is_owner_occupied", False))
    is_now_owner_occupied = bool(update_dict.get("is_owner_occupied", was_owner_occupied))
    if not was_owner_occupied and is_now_owner_occupied:
        # building_id MUST be in filter — multi-tenant isolation
        await db.rental_certificates.update_many(
            {"building_id": building_id, "unit_number": unit_number, "status": "issued"},
            {"$set": {
                "status": "superseded",
                "superseded_at": now,
                "superseded_reason": (
                    f"Unit {unit_number} became owner-occupied on {now[:10]}. "
                    f"Previous tenant rental certificate superseded automatically."
                ),
                "superseded_by": actor.get("id", "system"),
            }},
        )


@api_router.put("/owners-units/{unit_number}", response_model=OwnerUnitResponse)
async def update_owner_unit(
        unit_number: str,
        updates: OwnerUnitUpdate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Update owner/unit record (Super Admin only). Scoped to building."""
    if _effective_role(current_user) != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    owner_unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
    if not owner_unit:
        raise HTTPException(status_code=404, detail="Owner/unit not found")

    update_dict = {k: v for k, v in updates.model_dump().items() if v is not None}

    owner_identity_fields = {"owner_name", "owner_name_b", "owner_email", "owner_email_b"}
    attempted_owner_fields = owner_identity_fields & set(update_dict.keys())
    if attempted_owner_fields:
        raise HTTPException(
            status_code=400,
            detail=(
                "Owner identity is managed through the ownership transfer workflow. "
                "Update unit metadata here and use /owner-transfers for ownership changes."
            ),
        )

    # Keep is_owner_occupied and occupancy_type in sync (single source of truth).
    if "is_owner_occupied" in update_dict and "occupancy_type" not in update_dict:
        update_dict["occupancy_type"] = "owner_occupied" if update_dict["is_owner_occupied"] else "rented"
    elif "occupancy_type" in update_dict and "is_owner_occupied" not in update_dict:
        update_dict["is_owner_occupied"] = update_dict["occupancy_type"] != "rented"

    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.units.update_one({"building_id": building_id, "unit_number": unit_number}, {"$set": update_dict})

    # --- Cascade side-effects (strata roll sync, AGM proxy alerts, etc.) ---
    asyncio.create_task(_cascade_owner_change(
        building_id, unit_number, owner_unit, update_dict, current_user
    ))

    # --- Single source of truth: sync balance fields → unit_levy_ledger ---
    # When opening_arrears is updated on a unit, the ledger opening balances must
    # reflect that change so the dashboard arrears calculation stays accurate.
    BALANCE_SYNC_FIELDS = {"opening_arrears", "balance_owing", "balance_credit"}
    if BALANCE_SYNC_FIELDS & set(update_dict.keys()):
        current_year = str(datetime.now(timezone.utc).year)
        updated_unit_doc = await db.units.find_one(
            {"building_id": building_id, "unit_number": unit_number},
            {"_id": 0, "opening_arrears": 1}
        )
        new_opening = float(updated_unit_doc.get("opening_arrears", 0) or 0)

        # Derive admin/sinking split from annual_levies rates for the current year
        # Fetch all relevant fields in a single query to avoid redundant re-query on fallback.
        levy_doc = await db.annual_levies.find_one(
            {"building_id": building_id, "year": current_year},
            {"_id": 0, "admin_levy_per_uoe_annual": 1, "sinking_levy_per_uoe_annual": 1,
             "admin_fund": 1, "sinking_fund": 1}
        )
        if levy_doc:
            from utils.finance_helpers import get_levy_proposed_amounts

            _admin_income, _sinking_income = get_levy_proposed_amounts(levy_doc)
            _total = _admin_income + _sinking_income
            admin_frac = (_admin_income / _total) if _total > 0 else 0.774
        else:
            admin_frac = 0.774  # last-resort: FY2026 ratio ($340,870/$440,375)

        new_admin_opening = round(new_opening * admin_frac, 2)
        new_sinking_opening = round(new_opening - new_admin_opening, 2)

        await db.unit_levy_ledger.update_one(
            {"building_id": building_id, "year": current_year, "unit_number": unit_number},
            {"$set": {
                "admin_opening": new_admin_opening,
                "sinking_opening": new_sinking_opening,
                # Keep opening_arrears in sync — the arrears formula prefers this field
                "opening_arrears": round(new_admin_opening + new_sinking_opening, 2),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
    # --- End sync ---

    updated_unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
    return OwnerUnitResponse(**updated_unit)


@api_router.post("/owners-units/sync-arrears")
async def sync_arrears_to_ledger(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """
    Admin: Bulk-sync units.opening_arrears → unit_levy_ledger opening balances.

    Run this after any bulk financial import (CSV upload, Excel import, etc.) to
    ensure the dashboard arrears calculation is consistent with the strata roll.
    Super Admin only.
    """
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    current_year = str(datetime.now(timezone.utc).year)

    # Get admin/sinking ratio from annual_levies — always derived from actual rates
    levy_doc = await db.annual_levies.find_one(
        {"building_id": building_id, "year": current_year},
        {"_id": 0, "admin_levy_per_uoe_annual": 1, "sinking_levy_per_uoe_annual": 1,
         "admin_fund": 1, "sinking_fund": 1}
    )
    if levy_doc:
        from utils.finance_helpers import get_levy_proposed_amounts

        _admin_income, _sinking_income = get_levy_proposed_amounts(levy_doc)
        _total = _admin_income + _sinking_income
        admin_frac = (_admin_income / _total) if _total > 0 else 0.5
    else:
        raise HTTPException(status_code=404,
                            detail=f"No annual_levies record for building {building_id} year {current_year}. "
                                   f"Cannot derive admin/sinking split without rate data.")

    # Load all units with their current opening_arrears
    units = await db.units.find(
        {"building_id": building_id},
        {"_id": 0, "unit_number": 1, "opening_arrears": 1}
    ).to_list(200)

    updated_count = 0
    skipped_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for unit in units:
        unit_number = unit["unit_number"]
        opening = float(unit.get("opening_arrears", 0) or 0)
        admin_opening = round(opening * admin_frac, 2)
        sinking_opening = round(opening - admin_opening, 2)

        result = await db.unit_levy_ledger.update_one(
            {"building_id": building_id, "year": current_year, "unit_number": unit_number},
            {"$set": {
                "admin_opening": admin_opening,
                "sinking_opening": sinking_opening,
                # opening_arrears must stay in sync — the arrears formula prefers this field
                "opening_arrears": round(admin_opening + sinking_opening, 2),
                "updated_at": now
            }}
        )
        if result.matched_count > 0:
            updated_count += 1
        else:
            skipped_count += 1  # No ledger entry for this unit/year (normal for units with no history)

    return {
        "status": "ok",
        "year": current_year,
        "building_id": building_id,
        "units_updated": updated_count,
        "units_skipped": skipped_count,
        "message": f"Synced opening arrears from units → unit_levy_ledger for {updated_count} units"
    }


@api_router.delete("/owners-units/{unit_number}")
async def delete_owner_unit(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Delete owner/unit record (Super Admin only)"""
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    result = await db.units.delete_one({"building_id": building_id, "unit_number": unit_number})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Owner/unit not found")

    return {"message": "Owner/unit deleted successfully"}


@api_router.get("/units/{unit_number}/market-valuation")
async def get_unit_market_valuation(
        unit_number: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get the owner-estimated market price for a unit."""
    resident_roles = [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
        UserRole.OWNER,
    ]
    if _effective_role(current_user) not in resident_roles:
        raise HTTPException(status_code=403, detail="Access denied")
    if (
            _effective_role(current_user) == UserRole.OWNER
            and current_user.get("unit_number") != unit_number
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    unit = await db.units.find_one(
        {"building_id": building_id, "unit_number": unit_number},
        {
            "_id": 0,
            "unit_number": 1,
            "estimated_market_price": 1,
            "market_price_updated_at": 1,
            "market_price_notes": 1,
        },
    )
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    return {
        "unit_number": unit_number,
        "estimated_market_price": unit.get("estimated_market_price"),
        "market_price_updated_at": unit.get("market_price_updated_at"),
        "market_price_notes": unit.get("market_price_notes", ""),
    }


@api_router.put("/units/{unit_number}/market-valuation")
async def set_unit_market_valuation(
        unit_number: str,
        body: dict,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Save the owner-estimated market price for a unit."""
    resident_roles = [
        UserRole.SUPER_ADMIN,
        UserRole.EC_MEMBER, UserRole.STRATA_MANAGER,
        UserRole.OWNER,
    ]
    if _effective_role(current_user) not in resident_roles:
        raise HTTPException(status_code=403, detail="Access denied")
    if (
            _effective_role(current_user) == UserRole.OWNER
            and current_user.get("unit_number") != unit_number
    ):
        raise HTTPException(status_code=403, detail="Access denied")

    unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number})
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    raw_price = body.get("estimated_market_price")
    if raw_price is None:
        raise HTTPException(
            status_code=400, detail="estimated_market_price is required"
        )

    try:
        price = float(raw_price)
        if price <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400, detail="estimated_market_price must be a positive number"
        )

    notes = str(body.get("market_price_notes", ""))[:500]
    now = datetime.now(timezone.utc).isoformat()

    await db.units.update_one(
        {"building_id": building_id, "unit_number": unit_number},
        {
            "$set": {
                "estimated_market_price": price,
                "market_price_notes": notes,
                "market_price_updated_at": now,
                "updated_at": now,
            }
        },
    )

    return {
        "unit_number": unit_number,
        "estimated_market_price": price,
        "market_price_updated_at": now,
        "market_price_notes": notes,
    }


@api_router.post("/owners-units/import-from-pdf")
async def import_owners_from_pdf(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Import owners/units data from parsed PDF (Super Admin only).

    DISABLED — the importer this endpoint drives is not safe to run as-is.

    The script it invokes (`scripts/finances/parse_and_import_owners.py`) was moved
    to `scripts/archive/finances/` and no longer exists at this path, so every call
    has been failing with a FileNotFoundError dressed up as a generic 500. The UI
    button in OwnersUnitsPage still calls it.

    Do NOT "fix" this by repointing the path at the archived copy. That script:
      * contains ZERO building scoping — no building_id, no plan_id, anywhere; and
      * begins its write with `await collection.delete_many({})` on `owners_units`,
        an UNFILTERED wipe of the whole collection.
    Repointing it would let a super_admin acting in one building destroy every
    building's owner records, then repopulate them from a PDF hardcoded to East
    Gate. It also takes no building argument, so there is nothing to scope it with.

    Restoring this feature means rewriting the importer to be building-scoped and
    to upsert rather than wipe. Tracked in GAP-SEC-001. Until then it fails fast
    and honestly instead of emitting a confusing 500.

    Status choice: 410, not the more obvious 501. `_normalise_detail()` in
    utils/error_response.py deliberately discards the detail of any response with
    `status_code >= 500` so that server-side failures cannot echo provider,
    database or stack information — which means a 501 would reach the admin as a
    bare "Something went wrong on our side." and tell them nothing. 410 Gone is
    both accurate (the importer really is gone) and below that threshold, so the
    explanation below actually arrives. The dict detail sets an explicit error
    code rather than falling back to UNKNOWN_ERROR.
    """
    if current_user.get("role") != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")

    raise HTTPException(
        status_code=410,
        detail={
            "code": "IMPORTER_DISABLED",
            "message": (
                "The PDF owners/units importer is disabled. The underlying script is "
                "archived and is not building-scoped — running it would affect every "
                "building. See GAP-SEC-001."
            ),
            "retryable": False,
        },
    )


# ==================== PAYMENT PROCESSING ROUTES ====================


@api_router.post("/payments/create-intent", response_model=PaymentIntentResponse)
async def create_payment_intent(
        data: PaymentIntentCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Create a Stripe payment intent for levy payment"""

    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=503, detail="Payment processing is not configured"
        )

    # Verify unit ownership or EC/admin access
    if current_user.get("unit_number") != str(data.unit_number):
        if _effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
            raise HTTPException(
                status_code=403, detail="You can only pay for your own unit"
            )

    # Get owner/unit information
    owner_unit = await db.units.find_one({"building_id": building_id, "unit_number": data.unit_number}, {"_id": 0})
    if not owner_unit:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Mock boundary: Stripe is a live financial provider, so this call is refused
    # while financial_services_mock is on for the building. Refusing loudly rather
    # than returning a fake intent: a caller that thinks it has a payment intent and
    # does not is worse than a caller told it cannot have one.
    from services.financial_mock_mode import assert_live_financial_call_allowed

    await assert_live_financial_call_allowed(building_id, "Stripe payment intent")

    try:
        # Create Stripe payment intent
        intent = stripe.PaymentIntent.create(
            amount=round(
                data.amount * 100
            ),  # Convert to cents (round avoids float truncation)
            currency="aud",
            description=f"{data.description} - Unit {data.unit_number}",
            metadata={
                "unit_number": data.unit_number,
                "levy_period": data.levy_period,
                "owner_id": current_user["id"],
                "admin_fund_amount": data.admin_fund_amount or 0,
                "sinking_fund_amount": data.sinking_fund_amount or 0,
            },
        )

        # Store payment record
        payment_record = {
            "id": str(uuid.uuid4()),
            "building_id": building_id,
            "payment_intent_id": intent.id,
            "unit_number": data.unit_number,
            "owner_id": current_user["id"],
            "owner_name": current_user.get("full_name", ""),
            "amount": data.amount,
            "currency": "AUD",
            "status": "pending",
            "payment_method": "card",
            "levy_period": data.levy_period,
            "admin_fund_amount": data.admin_fund_amount or (data.amount / 2),
            "sinking_fund_amount": data.sinking_fund_amount or (data.amount / 2),
            "receipt_url": None,
            "receipt_sent": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }

        await db.payments.insert_one(payment_record)

        return PaymentIntentResponse(
            client_secret=intent.client_secret,
            payment_intent_id=intent.id,
            amount=data.amount,
            currency="AUD",
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Payment intent creation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payment intent")


@api_router.post("/payments/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle the event
    if event["type"] == "payment_intent.succeeded":
        payment_intent = event["data"]["object"]

        # Update payment record
        await db.payments.update_one(
            {"payment_intent_id": payment_intent["id"]},
            {
                "$set": {
                    "status": "completed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

        # Update owner/unit balance
        metadata = payment_intent.get("metadata", {})
        building_id = metadata.get("building_id")
        unit_number = metadata.get("unit_number", "")  # Keep as string, not int
        admin_amount = float(metadata.get("admin_fund_amount", 0))
        sinking_amount = float(metadata.get("sinking_fund_amount", 0))
        # Use actual charged amount from PaymentIntent (cents → dollars) as the source of truth.
        # If metadata amounts are both 0 (e.g. old intents or miscalculation), fall back to
        # splitting the actual amount ~77/23 (typical admin/sinking levy ratio).
        actual_total = payment_intent.get("amount", 0) / 100
        if admin_amount == 0 and sinking_amount == 0 and actual_total > 0:
            admin_amount = round(actual_total * 0.7737, 2)
            sinking_amount = round(actual_total - admin_amount, 2)
        total_amount = (
            actual_total if actual_total > 0 else (admin_amount + sinking_amount)
        )

        if unit_number and building_id:
            # Update units collection (the primary source of truth for dashboard)
            unit = await db.units.find_one({"building_id": building_id, "unit_number": unit_number}, {"_id": 0})
            if unit:
                # Update fund balances in units collection
                admin_fund = unit.get("admin_fund", {})
                sinking_fund = unit.get("sinking_fund", {})

                admin_fund["paid"] = admin_fund.get("paid", 0) + admin_amount
                admin_fund["closing_balance"] = (
                        admin_fund.get("closing_balance", 0) - admin_amount
                )

                sinking_fund["paid"] = sinking_fund.get("paid", 0) + sinking_amount
                sinking_fund["closing_balance"] = (
                        sinking_fund.get("closing_balance", 0) - sinking_amount
                )

                # Update unit_levy_ledger to reflect payment
                from utils.finance_helpers import get_latest_levy_year

                payment_year = str(datetime.now(timezone.utc).year)
                ledger_doc = await db.unit_levy_ledger.find_one(
                    {
                        "building_id": building_id,
                        "unit_number": unit_number,
                        "year": payment_year,
                    }
                )
                if ledger_doc:
                    new_total_paid = ledger_doc.get("total_paid", 0) + total_amount
                    new_admin_paid = ledger_doc.get("admin_paid", 0) + admin_amount
                    new_sinking_paid = (
                            ledger_doc.get("sinking_paid", 0) + sinking_amount
                    )
                    new_net_balance = ledger_doc.get("total_levied", 0) - new_total_paid
                    await db.unit_levy_ledger.update_one(
                        {
                            "building_id": building_id,
                            "unit_number": unit_number,
                            "year": payment_year,
                        },
                        {
                            "$set": {
                                "admin_paid": round(new_admin_paid, 2),
                                "sinking_paid": round(new_sinking_paid, 2),
                                "total_paid": round(new_total_paid, 2),
                                "net_balance": round(new_net_balance, 2),
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            }
                        },
                    )

                # Record payment in levy_payments collection (new schema)
                now = datetime.now(timezone.utc).isoformat()
                levy_payment = {
                    "id": str(uuid.uuid4()),
                    "building_id": building_id,
                    "unit_number": unit_number,
                    "amount": total_amount,
                    "payment_method": "credit_card",
                    "payment_reference": payment_intent.get("id", ""),
                    "quarter": "Q"
                               + str((datetime.now(timezone.utc).month - 1) // 3 + 1),
                    "year": str(datetime.now(timezone.utc).year),
                    "fund_type": None,
                    "status": "confirmed",
                    "notes": f"Stripe: Admin ${admin_amount:.2f}, Sinking ${sinking_amount:.2f}",
                    "receipt_number": f"STRIPE-{payment_intent.get('id', uuid.uuid4())[:8].upper()}",
                    "paid_by": (
                        unit.get("owner_name", "System")
                        if "unit" in locals()
                        else "System"
                    ),
                    "confirmed_by": "stripe_webhook",
                    "confirmed_at": now,
                    "created_at": now,
                }
                await db.levy_payments.insert_one(levy_payment)

        # TODO: Generate and email receipt

    return {"status": "success"}


@api_router.get("/payments/history", response_model=List[PaymentResponse])
async def get_payment_history(
        unit_number: Optional[int] = None,
        skip: int = 0,
        limit: int = 50,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Get payment history"""

    query = {"building_id": building_id}

    # Owners can only see their own payments; EC members and above can see all
    if _effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
        query["owner_id"] = current_user["id"]
    elif unit_number:
        query["unit_number"] = unit_number

    payments = (
        await db.payments.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(limit)
    )

    return [PaymentResponse(**p) for p in payments]


@api_router.get("/payments/{payment_id}/receipt")
async def download_receipt(
        payment_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building)
):
    """Download payment receipt as PDF"""

    payment = await db.payments.find_one({"id": payment_id, "building_id": building_id}, {"_id": 0})
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Check ownership
    if payment["owner_id"] != current_user["id"]:
        if _effective_role(current_user) not in [UserRole.SUPER_ADMIN, UserRole.EC_MEMBER]:
            raise HTTPException(status_code=403, detail="Access denied")

    # Generate PDF receipt
    settings_doc = await _get_general_settings_or_default(
        building_id,
        {"_id": 0},
        fallback_building_id=DEFAULT_BUILDING_ID,
        settings_db=db,
    )
    receipt_contact = settings_doc.get("contact_email") or settings_doc.get("strata_manager_email") or _get_portal_url()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()

    # Title
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontSize=20,
        spaceAfter=30,
    )
    elements.append(Paragraph("PAYMENT RECEIPT", title_style))
    elements.append(Spacer(1, 20))

    # Receipt details
    data = [
        ["Receipt #:", payment["id"][:12].upper()],
        ["Date:", datetime.fromisoformat(payment["created_at"]).strftime("%d %B %Y")],
        ["Unit Number:", str(payment["unit_number"])],
        ["Owner:", payment["owner_name"]],
        ["Levy Period:", payment["levy_period"]],
        ["", ""],
        ["Admin Fund:", f"${payment['admin_fund_amount']:.2f}"],
        ["Sinking Fund:", f"${payment['sinking_fund_amount']:.2f}"],
        ["", ""],
        ["Total Amount Paid:", f"${payment['amount']:.2f}"],
        ["Payment Method:", payment["payment_method"].title()],
        ["Status:", payment["status"].upper()],
    ]

    table = Table(data, colWidths=[4 * cm, 6 * cm])
    table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("FONTNAME", (0, 9), (-1, 9), "Helvetica-Bold"),
                ("FONTSIZE", (0, 9), (-1, 9), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEABOVE", (0, 9), (-1, 9), 2, colors.black),
            ]
        )
    )

    elements.append(table)
    elements.append(Spacer(1, 30))

    # Footer
    footer = Paragraph(
        f"Thank you for your payment. For queries, contact {html_lib.escape(str(receipt_contact))}",
        styles["Normal"],
    )
    elements.append(footer)

    doc.build(elements)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=receipt_{payment_id[:12]}.pdf"
        },
    )


# ==================== ROOT ROUTE ====================


@api_router.get("/")
async def root():
    """Generated function header.

    Function: root
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return {"message": "StrataOS Strata Management API", "version": "2.0.0"}


# ==================== CHAT USER LIST ENDPOINT ====================


@api_router.get("/chat/users", response_model=List[dict])
async def get_chat_users(
        current_user: dict = Depends(get_approved_user),
        building_id: str = Depends(get_current_building)
):
    """
    Get list of approved users for chat group invitations. Scoped to building.
    Only accessible to approved users. Honors directory visibility settings.
    """
    # Performance Optimization⚡: Using a single aggregation pipeline with $lookup to join memberships and users.
    # This eliminates sequential database round-trips and filters the current user at the DB level.
    pipeline = [
        {"$match": {"building_id": building_id}},
        {
            "$lookup": {
                "from": "users",
                "localField": "user_id",
                "foreignField": "id",
                "as": "user_info",
            }
        },
        {"$unwind": "$user_info"},
        {
            "$match": {
                "user_info.id": {"$ne": current_user.get("id")},
                "user_info.is_approved": True,
                "user_info.is_active": True,
                "$or": [
                    {"user_info.directory_visible": True},
                    {"user_info.directory_visible": {"$exists": False}},
                ],
            }
        },
    ]

    # Exclude system admin email if current user is not system admin
    if current_user.get("email") != SYSTEM_HIDDEN_EMAIL:
        pipeline[-1]["$match"]["user_info.email"] = {"$ne": SYSTEM_HIDDEN_EMAIL}

    pipeline.append(
        {
            "$project": {
                "_id": 0,
                "id": "$user_info.id",
                "full_name": "$user_info.full_name",
                "role": "$user_info.role",
                "unit_number": "$user_info.unit_number",
            }
        }
    )

    users = await _server_agg(db.memberships, pipeline, 1000)
    return users


# Include feature toggles router if available
if FEATURE_TOGGLES_ROUTER_AVAILABLE:
    api_router.include_router(feature_toggles_router, tags=["Feature Toggles"])

# Include chat router if available
if CHAT_ROUTER_AVAILABLE:
    api_router.include_router(chat_router, tags=["Chat Groups"])

# Include communication router if available (notices, announcements, messages)
if COMMUNICATION_ROUTER_AVAILABLE:
    api_router.include_router(communication_router, tags=["Communication"])

# Include maintenance router if available
if MAINTENANCE_ROUTER_AVAILABLE:
    api_router.include_router(maintenance_router, tags=["Maintenance"])

# Include notifications router if available
if NOTIFICATIONS_ROUTER_AVAILABLE:
    api_router.include_router(notifications_router, tags=["Notifications"])

# Include finance router if available (new clean architecture)
if FINANCE_ROUTER_AVAILABLE:
    api_router.include_router(finance_router, tags=["Finance"])

# Include finance reports router if available
if FINANCE_REPORTS_ROUTER_AVAILABLE:
    api_router.include_router(finance_reports_router, tags=["Finance Reports"])

# Include financial onboarding router if available
if FINANCIAL_ONBOARDING_ROUTER_AVAILABLE:
    api_router.include_router(financial_onboarding_router, tags=["Financial Onboarding"])

# Include analytics router if available
if ANALYTICS_ROUTER_AVAILABLE:
    api_router.include_router(analytics_router, tags=["Analytics"])

# Include BI analytics router (canonical fact-table endpoints)
try:
    from routers.bi import router as bi_router
    api_router.include_router(bi_router, tags=["BI Analytics"])
except ImportError as e:
    logger.warning(f"BI Analytics router not available: {e}")

# Include market router if available
if MARKET_ROUTER_AVAILABLE:
    api_router.include_router(market_router, tags=["Market"])

try:
    from routers.market_intelligence import router as market_intelligence_router

    api_router.include_router(market_intelligence_router, tags=["Market Intelligence"])
except Exception as _e:
    logger.warning(f"Market Intelligence router not loaded: {_e}")

# Include the router in the main app

# Include council rates router if available
if COUNCIL_RATES_ROUTER_AVAILABLE:
    api_router.include_router(council_rates_router, tags=["Council Rates"])

# Include reconciliation router if available
if RECONCILIATION_ROUTER_AVAILABLE:
    api_router.include_router(reconciliation_router, tags=["Reconciliation"])

# Include water bills router if available
if WATER_BILLS_ROUTER_AVAILABLE:
    api_router.include_router(water_bills_router, tags=["Water Bills"])

# Include GST & BAS ledger router if available
if GST_BAS_ROUTER_AVAILABLE:
    api_router.include_router(gst_bas_router, tags=["GST & BAS"])

# Include Capital Funding & Special Levy preview router if available (GAP-FIN-034)
if CAPITAL_FUNDING_ROUTER_AVAILABLE:
    api_router.include_router(capital_funding_router, tags=["Capital Funding"])

# Include per-building Strata Web portal connection config router if available
if STRATA_WEB_PORTAL_ROUTER_AVAILABLE:
    api_router.include_router(strata_web_portal_router, tags=["Strata Web Portal"])

try:
    from routers.utilities_workflows import router as utilities_workflows_router

    api_router.include_router(utilities_workflows_router)
    logger.info("Utility Workflows router loaded")
except Exception as e:
    logger.warning(f"Utility Workflows router not loaded: {e}")

# Include utilities router if available
if UTILITIES_ROUTER_AVAILABLE:
    api_router.include_router(utilities_router, tags=["Utilities"])

# Include finance intelligence router if available
if FINANCE_INTELLIGENCE_ROUTER_AVAILABLE:
    api_router.include_router(
        finance_intelligence_router, tags=["Finance Intelligence"]
    )

# Include rental certificates router if available (ACT s.119A)
if RENTAL_CERTS_ROUTER_AVAILABLE:
    api_router.include_router(rental_certs_router, tags=["Rental Certificates"])

# Include PPM router if available
if PPM_ROUTER_AVAILABLE:
    api_router.include_router(ppm_router, tags=["PPM"])

# Include building router if available
if BUILDING_ROUTER_AVAILABLE:
    api_router.include_router(building_router, tags=["Building Assets"])

# Include digital twin router
try:
    from routers.digital_twin import router as digital_twin_router

    api_router.include_router(digital_twin_router, tags=["Digital Twin"])
except ImportError:
    logger.warning("Digital Twin router not available")

# Include intelligence router
try:
    from routers.intelligence import router as intelligence_router

    api_router.include_router(intelligence_router, tags=["Intelligence"])
except ImportError:
    logger.warning("Intelligence router not available")

# Include security router if available
if SECURITY_ROUTER_AVAILABLE:
    api_router.include_router(security_router, tags=["Security"])

# Include outbox admin router if available
if OUTBOX_ADMIN_ROUTER_AVAILABLE:
    api_router.include_router(outbox_admin_router, tags=["Outbox Admin"])

# Include cutover control plane admin router if available
if CUTOVER_ADMIN_ROUTER_AVAILABLE:
    api_router.include_router(cutover_admin_router, tags=["Cutover Control Plane"])

# Include super-admin cross-feature diagnostics router if available
if ADMIN_DIAGNOSTICS_ROUTER_AVAILABLE:
    api_router.include_router(admin_diagnostics_router, tags=["Admin Diagnostics"])

# Include work orders router if available
if WORK_ORDERS_ROUTER_AVAILABLE:
    api_router.include_router(work_orders_router)

# Include insurance claims router if available
if INSURANCE_CLAIMS_ROUTER_AVAILABLE:
    api_router.include_router(insurance_claims_router)

# Include compliance routers (NSW, Privacy, WHS, Manager Contracts, Decisions)
if COMPLIANCE_ROUTERS_AVAILABLE:
    api_router.include_router(nsw_compliance_router, tags=["NSW Compliance"])
    api_router.include_router(privacy_compliance_router, tags=["Privacy Compliance"])
    api_router.include_router(whs_router, tags=["WHS Compliance"])
    api_router.include_router(manager_contracts_router, tags=["Manager Contracts"])
    api_router.include_router(decisions_router, tags=["Decision Register"])

# GAP-JUR-NSW-013: Building Manager Duties Register (SSMA 2015 s.46B)
if BUILDING_MANAGER_DUTIES_ROUTER_AVAILABLE:
    api_router.include_router(building_manager_duties_router, tags=["NSW Compliance"])
    logger.info("Building manager duties register router loaded (GAP-JUR-NSW-013)")

# GAP-JUR-NSW-005: NSW Initial Maintenance Schedule (SSMA 2015 Schedule 3)
if NSW_IMS_ROUTER_AVAILABLE:
    api_router.include_router(nsw_ims_router, tags=["NSW Compliance"])
    logger.info("NSW initial maintenance schedule router loaded (GAP-JUR-NSW-005)")

# Include auth router — provides /auth/register with rate limiting
if AUTH_ROUTER_AVAILABLE:
    api_router.include_router(auth_router, tags=["Auth"])

# Include external API router (versioned, API-key authenticated)
try:
    from routers.external_api import router as external_api_router

    api_router.include_router(external_api_router, tags=["External API v1"])
except ImportError as e:
    logger.warning(f"External API router not available: {e}")

# Include OwnerHub router (landlord platform + tenancy management)
try:
    from routers.ownerhub import router as ownerhub_router

    api_router.include_router(ownerhub_router, tags=["OwnerHub"])
except ImportError as e:
    logger.warning(f"OwnerHub router not available: {e}")

# Include Building Financial Stress router
try:
    from routers.building_stress import router as building_stress_router

    api_router.include_router(building_stress_router, tags=["Building Stress"])
except ImportError as e:
    logger.warning(f"Building Stress router not available: {e}")

# Include RBAC router (new RBAC + ABAC authorization system)
try:
    from routers.rbac import router as rbac_router

    api_router.include_router(rbac_router, tags=["RBAC"])
except ImportError as e:
    logger.warning(f"RBAC router not available: {e}")

# Include Staff Management router
try:
    from routers.staff_management import router as staff_management_router

    api_router.include_router(staff_management_router, tags=["Staff Management"])
except ImportError as e:
    logger.warning(f"Staff management router not available: {e}")

# Include Tenant Maintenance Portal router
try:
    from routers.tenant_maintenance import router as tenant_maintenance_router

    api_router.include_router(tenant_maintenance_router, tags=["Tenant Maintenance"])
except ImportError as e:
    logger.warning(f"Tenant Maintenance router not available: {e}")

# Include Global Building Risk Index router
try:
    from routers.risk_index import router as risk_index_router

    api_router.include_router(risk_index_router, tags=["Building Risk Index"])
except ImportError as e:
    logger.warning(f"Building Risk Index router not available: {e}")

try:
    from routers.financial_import import portal_snapshots_router, router as financial_import_router

    api_router.include_router(financial_import_router)
    api_router.include_router(portal_snapshots_router)
    logger.info("Financial Import router loaded")
except Exception as e:
    logger.warning(f"Financial Import router not loaded: {e}")

# ── Phase 1-3 Gap Closure Routers ─────────────────────────────────────────────
try:
    from routers.trust_accounting import router as trust_accounting_router

    api_router.include_router(trust_accounting_router, tags=["Trust Accounting"])
    logger.info("Trust Accounting router loaded")
except Exception as e:
    logger.warning(f"Trust Accounting router not loaded: {e}")

try:
    from routers.trust_phase1 import router as trust_phase1_router

    api_router.include_router(trust_phase1_router)
    logger.info("Trust Accounting Phase 1 router loaded")
except Exception as e:
    logger.warning(f"Trust Accounting Phase 1 router not loaded: {e}")

try:
    from routers.bank_feeds import router as bank_feeds_router

    api_router.include_router(bank_feeds_router, tags=["Bank Feeds"])
    logger.info("Bank Feeds router loaded")
except Exception as e:
    logger.warning(f"Bank Feeds router not loaded: {e}")

try:
    from routers.organisations import _buildings_router

    # organisations_router (POST/GET/PUT /organisations/*) removed — legacy Mongo CRUD
    # superseded by sm_organisations router (Task H, Phase F-Zero cleanup).
    # _buildings_router kept: GET /buildings/accessible is still used by FeatureTogglesPage.
    api_router.include_router(_buildings_router, tags=["Buildings"])
    logger.info("Buildings (accessible) router loaded")
except Exception as e:
    logger.warning(f"Buildings router not loaded: {e}")

try:
    from routers.matching import router as matching_router

    api_router.include_router(matching_router, tags=["3-Way Matching"])
    logger.info("3-Way Matching router loaded")
except Exception as e:
    logger.warning(f"Matching router not loaded: {e}")

try:
    from routers.financial_matching import router as financial_matching_router

    api_router.include_router(financial_matching_router, tags=["Financial Matching"])
    logger.info("Financial Matching router loaded")
except Exception as e:
    logger.warning(f"Financial Matching router not loaded: {e}")

try:
    from routers.finance_reconciliation import router as finance_reconciliation_router

    api_router.include_router(finance_reconciliation_router, tags=["Finance Reconciliation"])
    logger.info("Finance Reconciliation router loaded")
except Exception as e:
    logger.warning(f"Finance Reconciliation router not loaded: {e}")

try:
    from routers.ap_supplier_upload import router as ap_supplier_upload_router

    api_router.include_router(ap_supplier_upload_router, tags=["AP Supplier Upload"])
    logger.info("AP Supplier Upload router loaded")
except Exception as e:
    logger.warning(f"AP Supplier Upload router not loaded: {e}")

try:
    from routers.ocr import router as ocr_router

    api_router.include_router(ocr_router, tags=["OCR"])
    logger.info("OCR router loaded")
except Exception as e:
    logger.warning(f"OCR router not loaded: {e}")

try:
    from routers.ap_approval import router as ap_approval_router

    api_router.include_router(ap_approval_router, tags=["AP Approval"])
    logger.info("AP Approval router loaded")
except Exception as e:
    logger.warning(f"AP Approval router not loaded: {e}")

try:
    from routers.jurisdictional_rules_router import router as jurisdictional_rules_router

    api_router.include_router(jurisdictional_rules_router, tags=["Jurisdictional Rules"])
    logger.info("Jurisdictional Rules router loaded")
except Exception as e:
    logger.warning(f"Jurisdictional Rules router not loaded: {e}")

try:
    from routers.settlement_adjustment import router as settlement_adjustment_router

    api_router.include_router(settlement_adjustment_router, tags=["Settlement Adjustment"])
    logger.info("Settlement Adjustment router loaded")
except Exception as e:
    logger.warning(f"Settlement Adjustment router not loaded: {e}")

try:
    from routers.insurance import router as insurance_mgmt_router

    api_router.include_router(insurance_mgmt_router, tags=["Insurance Management"])
    logger.info("Insurance Management router loaded")
except Exception as e:
    logger.warning(f"Insurance Management router not loaded: {e}")

try:
    from routers.audit_management import router as audit_mgmt_router

    api_router.include_router(audit_mgmt_router, tags=["Financial Audit"])
    logger.info("Audit Management router loaded")
except Exception as e:
    logger.warning(f"Audit Management router not loaded: {e}")

try:
    from routers.compliance_registers import router as compliance_registers_router

    api_router.include_router(compliance_registers_router, tags=["Compliance Registers"])
    logger.info("Compliance Registers router loaded")
except Exception as e:
    logger.warning(f"Compliance Registers router not loaded: {e}")

try:
    from routers.mri_migration import router as mri_migration_router

    api_router.include_router(mri_migration_router, tags=["MRI Migration"])
    logger.info("MRI Migration router loaded")
except Exception as e:
    logger.warning(f"MRI Migration router not loaded: {e}")

try:
    from routers.trust_reconciliation import router as trust_reconciliation_router

    api_router.include_router(trust_reconciliation_router)
    logger.info("Trust Reconciliation router loaded")
except Exception as e:
    logger.warning(f"Trust Reconciliation router not loaded: {e}")

try:
    from routers.investor_intelligence import router as investor_intelligence_router

    api_router.include_router(investor_intelligence_router, tags=["Investor Intelligence"])
    logger.info("Investor Intelligence router loaded")
except Exception as e:
    logger.warning(f"Investor Intelligence router not loaded: {e}")

try:
    from routers.insurance_lending import router as insurance_lending_router

    api_router.include_router(insurance_lending_router, tags=["Insurance & Lending Intelligence"])
    logger.info("Insurance & Lending Intelligence router loaded")
except Exception as e:
    logger.warning(f"Insurance & Lending Intelligence router not loaded: {e}")

try:
    from routers.proposals import router as proposals_router

    api_router.include_router(proposals_router)
    logger.info("Proposals router loaded")
except Exception as e:
    logger.warning(f"Proposals router not loaded: {e}")

try:
    from routers.savings import router as savings_router

    api_router.include_router(savings_router)
    logger.info("Savings router loaded")
except Exception as e:
    logger.warning(f"Savings router not loaded: {e}")

try:
    from routers.occupancy import router as occupancy_router

    api_router.include_router(occupancy_router)
    logger.info("Occupancy Intelligence router loaded")
except Exception as e:
    logger.warning(f"Occupancy Intelligence router not loaded: {e}")

try:
    from routers.volunteer import router as volunteer_router

    api_router.include_router(volunteer_router)
    logger.info("Volunteer router loaded")
except Exception as e:
    logger.warning(f"Volunteer router not loaded: {e}")

try:
    from routers.request_catalogue import router as request_catalogue_router

    api_router.include_router(request_catalogue_router)
    logger.info("Request Catalogue router loaded")
except Exception as e:
    logger.warning(f"Request Catalogue router not loaded: {e}")

try:
    from routers.workflow_requests import router as workflow_requests_router

    api_router.include_router(workflow_requests_router)

    logger.info("Workflow Requests router loaded")
except Exception as e:
    logger.warning(f"Workflow Requests router not loaded: {e}")

try:
    from routers.ops_cases import router as ops_cases_router

    api_router.include_router(ops_cases_router)
    logger.info("Ops Cases router loaded")
except Exception as e:
    logger.warning(f"Ops Cases router not loaded: {e}")

try:
    from routers.communications_intake import router as communications_intake_router

    api_router.include_router(communications_intake_router)
    logger.info("Communications Intake router loaded")
except Exception as e:
    logger.warning(f"Communications Intake router not loaded: {e}")

try:
    from routers.ops_repairs import router as ops_repairs_router

    api_router.include_router(ops_repairs_router)
    logger.info("Ops Repairs router loaded")
except Exception as e:
    logger.warning(f"Ops Repairs router not loaded: {e}")

try:
    from routers.access_lifecycle import router as access_lifecycle_router

    api_router.include_router(access_lifecycle_router)
    logger.info("Access Lifecycle router loaded")
except Exception as e:
    logger.warning(f"Access Lifecycle router not loaded: {e}")

try:
    from routers.communications_campaigns import router as communications_campaigns_router

    api_router.include_router(communications_campaigns_router)
    logger.info("Communications Campaigns router loaded")
except Exception as e:
    logger.warning(f"Communications Campaigns router not loaded: {e}")

try:
    from routers.ai_review import router as ai_review_router

    api_router.include_router(ai_review_router)
    logger.info("AI Review router loaded")
except Exception as e:
    logger.warning(f"AI Review router not loaded: {e}")

try:
    from routers.community_dashboard import router as community_dashboard_router

    api_router.include_router(community_dashboard_router)
    logger.info("Community Dashboard router loaded")
except Exception as e:
    logger.warning(f"Community Dashboard router not loaded: {e}")

try:
    from routers.totp import router as totp_router

    api_router.include_router(totp_router, tags=["TOTP"])
    logger.info("TOTP router loaded")
except Exception as e:
    logger.warning(f"TOTP router not loaded: {e}")

try:
    from routers.sse import router as sse_router

    api_router.include_router(sse_router, tags=["SSE"])
    logger.info("SSE router loaded")
except Exception as e:
    logger.warning(f"SSE router not loaded: {e}")

try:
    from routers.engagement import router as engagement_router

    api_router.include_router(engagement_router, tags=["Engagement"])
    logger.info("Engagement router loaded")
except Exception as e:
    logger.warning(f"Engagement router not loaded: {e}")

try:
    from routers.email_ingestion import router as email_ingestion_router

    api_router.include_router(email_ingestion_router, tags=["Email Ingestion"])
    logger.info("Email ingestion router loaded")
except Exception as e:
    logger.warning(f"Email ingestion router not loaded: {e}")

try:
    from routers.workflows import router as workflows_router

    api_router.include_router(workflows_router, tags=["Workflow Governance"])
    logger.info("Workflows router loaded")
except Exception as e:
    logger.warning(f"Workflows router not loaded: {e}")

try:
    from routers.powerhouse_conversations import router as powerhouse_foundation_router

    api_router.include_router(powerhouse_foundation_router, tags=["Powerhouse Foundation"])
    logger.info("Powerhouse foundation router loaded")
except Exception as e:
    logger.warning(f"Powerhouse foundation router not loaded: {e}")

try:
    from routers.powerhouse_status import router as powerhouse_status_router

    api_router.include_router(powerhouse_status_router, tags=["Powerhouse Visibility"])
    logger.info("Powerhouse visibility router loaded")
except Exception as e:
    logger.warning(f"Powerhouse visibility router not loaded: {e}")

try:
    from routers.building_integrations import router as building_integrations_router

    api_router.include_router(building_integrations_router)
    logger.info("Building integrations router loaded")
except Exception as e:
    logger.warning(f"Building integrations router not available: {e}")

try:
    from routers.portfolio import router as portfolio_router

    api_router.include_router(portfolio_router, tags=["Portfolio"])
    logger.info("Portfolio router loaded")
except Exception as e:
    logger.warning(f"Portfolio router not loaded: {e}")

try:
    from routers.owner_finance import router as owner_finance_router

    api_router.include_router(owner_finance_router, tags=["Owner Finance"])
    logger.info("Owner finance router loaded")
except Exception as e:
    logger.warning(f"Owner finance router not loaded: {e}")

try:
    from routers.polls import router as polls_router

    api_router.include_router(polls_router, tags=["Polls"])
    logger.info("Polls router loaded")
except Exception as e:
    logger.warning(f"Polls router not loaded: {e}")

try:
    from routers.group_buy import router as group_buy_router

    api_router.include_router(group_buy_router, tags=["Group Buy"])
    logger.info("Group buy router loaded")
except Exception as e:
    logger.warning(f"Group buy router not loaded: {e}")

try:
    from routers.nav_badges import router as nav_badges_router

    api_router.include_router(nav_badges_router, tags=["Navigation"])
    logger.info("Nav badges router loaded")
except Exception as e:
    logger.warning(f"Nav badges router not loaded: {e}")
try:
    from routers.navigation import router as navigation_router

    api_router.include_router(navigation_router, tags=["Navigation"])
    logger.info("Navigation router loaded")
except Exception as e:
    logger.warning(f"Navigation router not loaded: {e}")
try:
    from routers.residency import router as residency_router

    api_router.include_router(residency_router, tags=["Residency"])
    logger.info("Residency router loaded")
except Exception as e:
    logger.warning(f"Residency router not loaded: {e}")
try:
    from routers.safety import router as safety_router

    api_router.include_router(safety_router, tags=["Safety"])
    logger.info("Safety router loaded")
except Exception as e:
    logger.warning(f"Safety router not loaded: {e}")
try:
    from routers.strata_sync import router as strata_sync_router

    api_router.include_router(strata_sync_router, tags=["Strata Sync"])
    logger.info("Strata sync router loaded")
except Exception as e:
    logger.warning(f"Strata sync router not loaded: {e}")

# Include Scheme Classes router (Class A / Class B levy split)
try:
    from routers.scheme_classes import router as scheme_classes_router

    api_router.include_router(scheme_classes_router, tags=["Scheme Classes"])
    logger.info("Scheme Classes router loaded")
except Exception as e:
    logger.warning(f"Scheme Classes router not loaded: {e}")

# Include Levy Scenario Modeller router
try:
    from routers.levy_scenarios import router as levy_scenarios_router

    api_router.include_router(levy_scenarios_router, tags=["Levy Scenarios"])
    logger.info("Levy Scenarios router loaded")
except Exception as e:
    logger.warning(f"Levy Scenarios router not loaded: {e}")

# Include Document Converter router (MarkItDown)
try:
    from routers.document_converter import router as document_converter_router

    api_router.include_router(document_converter_router, tags=["Document Converter"])
    logger.info("Document Converter router loaded")
except Exception as e:
    logger.warning(f"Document Converter router not loaded: {e}")

# Document Annotations — highlight + comment layer (react-pdf-highlighter-extended)
try:
    from routers.document_annotations import router as document_annotations_router

    api_router.include_router(document_annotations_router, tags=["Document Annotations"])
    logger.info("Document Annotations router loaded")
except Exception as e:
    logger.warning(f"Document Annotations router not loaded: {e}")

# Letters — WeasyPrint HTML/CSS → PDF owner letters (levy reminder, AGM, general notice)
try:
    from routers.letters import router as letters_router

    api_router.include_router(letters_router, tags=["Letters"])
    logger.info("Letters router loaded")
except Exception as e:
    logger.warning(f"Letters router not loaded: {e}")

# GAP-FIN-012: Levy reminder cadence settings + manual trigger
try:
    from routers.levy_reminders import router as levy_reminders_router

    api_router.include_router(levy_reminders_router, tags=["Levy Reminders"])
    logger.info("Levy Reminders router loaded")
except Exception as e:
    logger.warning(f"Levy Reminders router not loaded: {e}")

# GAP-GOV-007: Conflict-of-interest register
try:
    from routers.conflict_of_interest import router as conflict_of_interest_router

    api_router.include_router(conflict_of_interest_router, tags=["Governance"])
    logger.info("Conflict of Interest router loaded")
except Exception as e:
    logger.warning(f"Conflict of Interest router not loaded: {e}")

# GAP-COM-008: Essential services log
try:
    from routers.essential_services import router as essential_services_router

    api_router.include_router(essential_services_router, tags=["Compliance"])
    logger.info("Essential Services router loaded")
except Exception as e:
    logger.warning(f"Essential Services router not loaded: {e}")

# GAP-COM-005: Pool safety inspection register
try:
    from routers.pool_safety import router as pool_safety_router

    api_router.include_router(pool_safety_router, tags=["Compliance"])
    logger.info("Pool Safety router loaded")
except Exception as e:
    logger.warning(f"Pool Safety router not loaded: {e}")

# GAP-FIN-006: Predictive arrears risk scoring
try:
    from routers.arrears_risk import router as arrears_risk_router

    api_router.include_router(arrears_risk_router, tags=["Finance"])
    logger.info("Arrears Risk router loaded")
except Exception as e:
    logger.warning(f"Arrears Risk router not loaded: {e}")

# GAP-JUR-NSW-002: NSW Form 1 payment plan (ss.83A-83C SSMA 2015)
try:
    from routers.payment_plans import router as payment_plans_router

    api_router.include_router(payment_plans_router, tags=["Finance"])
    logger.info("Payment Plans router loaded")
except Exception as e:
    logger.warning(f"Payment Plans router not loaded: {e}")

# GAP-OPS-004: Arrears recovery state machine (reminder → LOD → solicitor)
try:
    from routers.arrears_recovery import router as arrears_recovery_router

    api_router.include_router(arrears_recovery_router, tags=["Finance"])
    logger.info("Arrears Recovery router loaded")
except Exception as e:
    logger.warning(f"Arrears Recovery router not loaded: {e}")

# GAP-OPS-002: Document request workflow (ACT UTMA s.116 / NSW SSMA s.182)
try:
    from routers.document_requests import router as document_requests_router

    api_router.include_router(document_requests_router, tags=["Documents"])
    logger.info("Document Requests router loaded")
except Exception as e:
    logger.warning(f"Document Requests router not loaded: {e}")

# GAP-OPS-005: By-law breach and dispute workflow (ACAT/NCAT evidence trail)
try:
    from routers.benefit_groups import router as benefit_groups_router
    api_router.include_router(benefit_groups_router, tags=["Benefit Groups"])
    logger.info("Benefit Groups router loaded")
except ImportError as e:
    logger.warning(f"Benefit Groups router not available: {e}")

try:
    from routers.by_law_breach import router as by_law_breach_router

    api_router.include_router(by_law_breach_router, tags=["Community"])
    logger.info("By-law Breach router loaded")
except Exception as e:
    logger.warning(f"By-law Breach router not loaded: {e}")

# GAP-OPS-006: Manager-to-manager handover workflow
try:
    from routers.building_handovers import router as building_handovers_router

    api_router.include_router(building_handovers_router, tags=["Buildings"])
    logger.info("Building Handovers router loaded")
except Exception as e:
    logger.warning(f"Building Handovers router not loaded: {e}")

# GAP-FIN-011: Special payment approval workflow (>$10k non-ABA)
try:
    from routers.special_payments import router as special_payments_router

    api_router.include_router(special_payments_router, tags=["Finance"])
    logger.info("Special Payments router loaded")
except Exception as e:
    logger.warning(f"Special Payments router not loaded: {e}")

try:
    from routers.trial_request import router as trial_request_router

    api_router.include_router(trial_request_router, tags=["Public"])
    logger.info("Trial Request router loaded")
except Exception as e:
    logger.warning(f"Trial Request router not loaded: {e}")

try:
    from routers.invoices import router as invoices_router

    api_router.include_router(invoices_router, tags=["Invoice OCR"])
    logger.info("Invoice OCR router loaded")
except Exception as e:
    logger.warning(f"Invoice OCR router not loaded: {e}")

# ── Financial Integration Layer v2 — mock provider routers ───────────────────
# These endpoints are always registered but gated in business logic by the
# financial_integration_layer_v2 feature toggle per building. The routers
# themselves enforce role guards (strata_manager / chairman / super_admin).
try:
    from integrations.mocks.routers.bank_feed_router import router as mock_bank_feed_router

    api_router.include_router(mock_bank_feed_router, tags=["Mock Bank Feed"])
    logger.info("Mock bank feed router loaded")
except Exception as e:
    logger.warning(f"Mock bank feed router not loaded: {e}")

# @featuretrace:demo_bank — Demo Bank provider router (stateful bank-feed emulator).
# Layer: router
try:
    from routers.demo_bank import router as demo_bank_router

    api_router.include_router(demo_bank_router, tags=["Demo Bank"])
    logger.info("Demo Bank router loaded")
except ImportError as e:
    logger.warning(f"Demo Bank router not loaded: {e}")

try:
    from routers.voting import router as voting_router

    api_router.include_router(voting_router, tags=["Voting"])
    logger.info("Voting (e-voting / ballot audit) router loaded")
except ImportError as e:
    logger.warning(f"Voting router not available: {e}")

try:
    from integrations.mocks.routers.accounting_router import router as mock_accounting_router

    api_router.include_router(mock_accounting_router, tags=["Mock Accounting"])
    logger.info("Mock accounting router loaded")
except Exception as e:
    logger.warning(f"Mock accounting router not loaded: {e}")

try:
    from routers.onboarding import router as onboarding_router

    api_router.include_router(onboarding_router, tags=["Onboarding"])
    logger.info("Onboarding router loaded")
except ImportError as e:
    logger.warning(f"Onboarding router not available: {e}")

try:
    from routers.sm_organisations import router as sm_organisations_router

    api_router.include_router(sm_organisations_router, tags=["SM Organisations"])
    logger.info("SM organisations router loaded")
except ImportError as e:
    logger.warning(f"SM organisations router not available: {e}")

try:
    from routers.management_hierarchy import router as management_hierarchy_router

    api_router.include_router(management_hierarchy_router, tags=["Management Hierarchy"])
    logger.info("Management hierarchy router loaded")
except ImportError as e:
    logger.warning(f"Management hierarchy router not available: {e}")

try:
    from routers.admin_invitations import router as admin_invitations_router

    api_router.include_router(admin_invitations_router, tags=["Invitations"])
    logger.info("Admin invitations router loaded")
except ImportError as e:
    logger.warning(f"Admin invitations router not available: {e}")

try:
    from routers.joint_owner_review import router as joint_owner_review_router

    api_router.include_router(joint_owner_review_router, tags=["Ownership"])
    logger.info("Joint owner review router loaded")
except ImportError as e:
    logger.warning(f"Joint owner review router not available: {e}")

# GAP-MNT-001: Defects register with statutory warranty clocks
try:
    from routers.defects_register import router as defects_register_router

    api_router.include_router(defects_register_router, tags=["Defects Register"])
    logger.info("Defects Register router loaded")
except ImportError as e:
    logger.warning(f"Defects Register router not available: {e}")

# GAP-COMMS-003 Phase 2: outbound message queue console
try:
    from routers.outbound_messages import router as outbound_messages_router

    api_router.include_router(outbound_messages_router, tags=["Outbound Message Queue"])
    logger.info("Outbound Message Queue router loaded")
except ImportError as e:
    logger.warning(f"Outbound Message Queue router not available: {e}")

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Field-masking obligations (ACL plan §5, GAP-SEC-005).
#
# require_capability() records the Decision's obligations on request.state; this
# middleware applies them to the serialised JSON on the way out. It is the
# enforcement floor: masking deliberately does not happen in the route, both so
# it cannot be forgotten and because masking before FastAPI's response_model
# validation would reject the WITHHELD sentinel on a numeric field.
#
# Inert for any request that never made an authorisation decision, which is
# still most of them during the Phase 5 migration.
#
# Deliberately NOT wrapped in the try/except ImportError pattern the routers use.
# A router that fails to import 404s, which is loud. A masking middleware that
# fails to import would start the server serving unmasked owner contact details,
# per-lot arrears and bank fields, and log a single warning nobody reads. For a
# control whose failure mode is silent disclosure, refusing to boot is correct.
from services.obligation_enforcement import ObligationEnforcementMiddleware  # noqa: E402

app.add_middleware(ObligationEnforcementMiddleware)
logger.info("Obligation enforcement middleware loaded")


@app.on_event("shutdown")
async def shutdown_db_client():
    """Generated function header.

    Function: shutdown_db_client
    Path: backend/server.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await client.close()


# ==================== AUTOMATED LEVY REMINDERS ====================


async def check_and_send_levy_reminders():
    """
    Background task that checks daily and sends levy reminders.
    Iterates through all buildings to process building-specific schedules.
    """
    reminder_days = [14, 7]  # Default days before due date

    while True:
        try:
            today = datetime.now().date()
            buildings = await db.buildings.find({"is_active": True, "is_archived": {"$ne": True}}).to_list(None)

            for building in buildings:
                building_id = building["id"]
                building_name = building.get("name", "Your Residence")
                try:
                    from services.levy_reminder_settings_service import get_levy_reminder_settings

                    settings = await get_levy_reminder_settings(building_id, settings_db=db)
                    if settings and settings.get("enabled", True):
                        reminder_days_config = settings.get("pre_due_days", reminder_days)

                        # Check upcoming levy due dates for this building
                        levy_events = await db.events.find(
                            {"building_id": building_id, "event_type": "levy_due"}, {"_id": 0}
                        ).to_list(10)

                        for event in levy_events:
                            try:
                                event_date = datetime.fromisoformat(event["start_date"]).date()
                                days_until = (event_date - today).days

                                if days_until in reminder_days_config and days_until > 0:
                                    # Check if already sent for this building/date/day combo
                                    already_sent = await db.auto_reminders_log.find_one(
                                        {
                                            "building_id": building_id,
                                            "due_date": event["start_date"],
                                            "days_before": days_until,
                                            "sent_date": today.isoformat(),
                                        }
                                    )

                                    if not already_sent:
                                        # Filter users by building membership
                                        memberships = await db.memberships.find({"building_id": building_id}).to_list(
                                            None)
                                        building_user_ids = [m["user_id"] for m in memberships]

                                        # Send reminders
                                        owners = await db.users.find(
                                            {
                                                "id": {"$in": building_user_ids},
                                                # 'chairman' is not a top-level role — a chairman is a
                                                # user with role='ec_member' and ec_position='CHAIRMAN'
                                                # (see rules/post-compact-critical.md), already covered
                                                # by the 'ec_member' entry below.
                                                "role": {
                                                    "$in": [
                                                        "owner",
                                                        "ec_member", "strata_admin",
                                                        "super_admin",
                                                    ]
                                                },
                                                "is_active": True,
                                            },
                                            {"_id": 0},
                                        ).to_list(1000)
                                        units = await db.units.find({"building_id": building_id}, {"_id": 0}).to_list(
                                            200)

                                        from utils.finance_helpers import (
                                            get_latest_levy_year,
                                            get_levy_rates,
                                        )

                                        sched_year = await get_latest_levy_year(building_id) or str(today.year)
                                        sched_rates = await get_levy_rates(sched_year, building_id)

                                        # Pre-map units by owner_email for O(1) lookup inside loop
                                        unit_map = {
                                            u.get("owner_email"): u
                                            for u in units
                                            if u.get("owner_email")
                                        }
                                        email_tasks = []

                                        for owner in owners:
                                            owner = owner or {}
                                            unit = unit_map.get(owner.get("email"))
                                            entitlement = (
                                                unit.get("entitlement", 115) if unit else 115
                                            )
                                            admin_annual = round(
                                                sched_rates.get("admin_annual", 0)
                                                * entitlement,
                                                2,
                                            )
                                            sinking_annual = round(
                                                sched_rates.get("sinking_annual", 0)
                                                * entitlement,
                                                2,
                                            )
                                            annual_levy = admin_annual + sinking_annual
                                            quarterly_levy = round(annual_levy / 4, 2)

                                            # Collect login email, eastgate alias, AND secondary owner email (deduplicated)
                                            recipient_emails = list(
                                                {
                                                    e
                                                    for e in [
                                                    owner.get("email"),
                                                    owner.get("mail_username"),
                                                    (
                                                        unit.get("owner_email_b")
                                                        if unit
                                                        else None
                                                    ),
                                                ]
                                                    if e
                                                }
                                            )

                                            if recipient_emails:
                                                safe_full_name = html_lib.escape(
                                                    str(owner.get("full_name") or "Resident"))
                                                safe_days_until = html_lib.escape(str(days_until))
                                                safe_due_date = html_lib.escape(str(event.get("start_date") or ""))
                                                safe_unit_number = html_lib.escape(
                                                    str(owner.get("unit_number") or "N/A"))
                                                html = f"""
                                                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                                                    <h2 style="color: #1a365d;">Levy Payment Reminder</h2>
                                                    <p>Dear {safe_full_name},</p>
                                                    <p>This is an automated reminder that your quarterly strata levy for <strong>{building_name}</strong> is due in <strong>{safe_days_until} days</strong> on <strong>{safe_due_date}</strong>.</p>
                                                    <div style="background: #f7fafc; padding: 15px; border-radius: 8px; margin: 20px 0;">
                                                        <p style="margin: 5px 0;"><strong>Amount Due:</strong> ${quarterly_levy:,.2f}</p>
                                                        <p style="margin: 5px 0;"><strong>Due Date:</strong> {safe_due_date}</p>
                                                        <p style="margin: 5px 0;"><strong>Unit:</strong> {safe_unit_number}</p>
                                                    </div>
                                                    <p>Visit the Levy Payments portal for payment methods.</p>
                                                    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                                                    <p style="color: #718096; font-size: 12px;">{building_name} | Automated Reminder</p>
                                                </div>
                                                """
                                                subject = f"Levy Reminder - Due in {days_until} days ({event['start_date']})"
                                                for recipient in recipient_emails:
                                                    email_tasks.append(
                                                        send_email_async(
                                                            to_email=recipient,
                                                            subject=subject,
                                                            html_content=html,
                                                        )
                                                    )

                                        sent_count = 0
                                        if email_tasks:
                                            results = await asyncio.gather(
                                                *email_tasks, return_exceptions=True
                                            )
                                            for res in results:
                                                if isinstance(res, Exception):
                                                    logger.error(
                                                        f"Auto levy reminder email task raised exception: {res}"
                                                    )
                                                elif isinstance(res, dict) and res.get("success"):
                                                    sent_count += 1

                                        # Log the reminder
                                        await db.auto_reminders_log.insert_one(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "building_id": building_id,
                                                "due_date": event["start_date"],
                                                "days_before": days_until,
                                                "sent_date": today.isoformat(),
                                                "sent_count": sent_count,
                                                "created_at": datetime.now(timezone.utc).isoformat(),
                                            }
                                        )
                                        logger.info(
                                            f"Auto levy reminder: Sent {sent_count} reminders for building {building_id}, due date {event['start_date']} ({days_until} days before)"
                                        )

                                elif days_until < 0:
                                    # Levy is overdue. Check if we've notified recently for overdue.
                                    if days_until in [-1, -7, -30]:
                                        already_sent_overdue = (
                                            await db.auto_reminders_log.find_one(
                                                {
                                                    "building_id": building_id,
                                                    "due_date": event["start_date"],
                                                    "days_before": days_until,
                                                    "sent_date": today.isoformat(),
                                                }
                                            )
                                        )

                                        if not already_sent_overdue:
                                            # Find all units in this building with net_balance > 0 (in arrears) from unit_levy_ledger
                                            from utils.finance_helpers import (
                                                get_latest_levy_year,
                                            )

                                            arrears_year = await get_latest_levy_year(building_id) or str(today.year)
                                            units_with_arrears = await db.unit_levy_ledger.find(
                                                {
                                                    "building_id": building_id,
                                                    "year": arrears_year,
                                                    "net_balance": {"$gt": 0},
                                                },
                                                {"_id": 0},
                                            ).to_list(200)

                                            # Bolt ⚡ Optimization: Eliminate N+1 query pattern by batch fetching owners
                                            arrears_unit_numbers = [u["unit_number"] for u in units_with_arrears]
                                            all_owners = await db.user_units.find(
                                                {
                                                    "building_id": building_id,
                                                    "unit_number": {"$in": arrears_unit_numbers},
                                                    "role_at_unit": "owner",
                                                    "is_active": True,
                                                }
                                            ).to_list(2000)

                                            # Map owners to units for O(1) lookup
                                            unit_to_owners = defaultdict(list)
                                            for ou in all_owners:
                                                unit_to_owners[ou["unit_number"]].append(ou)

                                            notifications = []
                                            for unit_doc in units_with_arrears:
                                                owners = unit_to_owners.get(unit_doc["unit_number"], [])
                                                for ou in owners:
                                                    notifications.append({
                                                        "user_id": ou["user_id"],
                                                        "title": "Levy Payment Overdue",
                                                        "message": f"Your levy payment for Unit {unit_doc['unit_number']} is {abs(days_until)} days overdue. Current balance owing: ${unit_doc.get('net_balance', 0):,.2f}",
                                                        "notification_type": "levy",
                                                        "link": "/financials/levy-payments"
                                                    })

                                            sent_overdue_count = 0
                                            if notifications:
                                                # Bolt ⚡ Optimization: Use batch notification creation
                                                sent_overdue_count = await create_notifications_batch(notifications,
                                                                                                      building_id=building_id)

                                            if sent_overdue_count > 0:
                                                await db.auto_reminders_log.insert_one(
                                                    {
                                                        "id": str(uuid.uuid4()),
                                                        "building_id": building_id,
                                                        "due_date": event["start_date"],
                                                        "days_before": days_until,
                                                        "sent_date": today.isoformat(),
                                                        "sent_count": sent_overdue_count,
                                                        "type": "overdue",
                                                        "created_at": datetime.now(timezone.utc).isoformat(),
                                                    }
                                                )
                                                logger.info(
                                                    f"Auto levy overdue: Sent {sent_overdue_count} notifications for building {building_id}, due date {event['start_date']} ({abs(days_until)} days past)"
                                                )

                            except Exception as e:
                                logger.error(f"Error processing levy event for building {building_id}: {e}")
                except Exception as e:
                    logger.error(f"Error processing levy event for building {building_id}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Levy reminder scheduler error: {e}")

        # Check once per day (86400 seconds)
        await asyncio.sleep(86400)


@app.on_event("startup")
async def start_authorisation_audit_writer():
    """Start the single writer that appends authorisation decisions to the audit chain.

    core.audit_events is hash-chained, so it has exactly one tip. Letting every
    request write its own row would put them all in a queue behind that tip —
    survivable at today's guarded-route count and not at the ~1400 GAP-SEC-005
    will produce. Handlers enqueue; this task drains and chains in order.
    """
    from services.authorisation_audit import start_writer

    start_writer()


@app.on_event("shutdown")
async def stop_authorisation_audit_writer():
    """Drain the audit queue on the way down so the tail is not lost."""
    from services.authorisation_audit import stop_writer

    await stop_writer()


@app.on_event("startup")
async def startup_event():
    """Start the automated levy reminder scheduler and initialize chat groups."""
    # Ensure critical indexes exist for performance - Added by Bolt ⚡
    try:
        # Identity (users, user_units, memberships) lives in Postgres after the
        # Mongo→PG cutover. Index creation for those collections has been
        # removed — calling create_index on a non-existent Mongo collection
        # silently auto-creates an empty collection on every restart, which
        # was the source of the "Mongo identity collections keep coming back"
        # symptom after we dropped them.
        await db.units.create_index("unit_number")
        await db.events.create_index("start_date")
        await db.meetings.create_index("meeting_date")
        await db.agm.create_index("date")
        await db.announcements.create_index("expires_at")
        # Bolt ⚡: Optimize fetching last message for group chats
        await db.group_messages.create_index([("group_id", 1), ("created_at", -1)])
        # Bolt ⚡: Optimize chat group lookups and membership filtering
        await db.chat_groups.create_index("id", unique=True)
        await db.chat_groups.create_index([("members.user_id", 1), ("archived", 1)])
        # Bolt ⚡: Optimize work order related queries
        await db.work_order_quotes.create_index("work_order_id")
        await db.work_order_approvals.create_index("work_order_id")
        await db.work_order_invoices.create_index("work_order_id")
        # Bolt ⚡: Optimize maintenance request filters
        await db.maintenance_requests.create_index("submitted_by")
        await db.maintenance_requests.create_index("assigned_contractor")

        # Performance Optimization⚡: High-frequency dashboard and audit query indexes
        await db.user_notifications.create_index([("user_id", 1), ("is_read", 1), ("created_at", -1)])
        await db.audit_logs.create_index([("resource_type", 1), ("created_at", -1)])
        # NOTE: levy_payments and unit_levy_ledger indexes (with building_id prefix) are
        # managed by financial_repository.ensure_indexes() called below. Legacy 2-key
        # duplicates removed by migration_021_index_optimisations.py.
        # Bolt ⚡: Optimize login audit and messaging performance
        await db.login_audit_logs.create_index([("user_id", 1), ("status", 1), ("attempted_at", -1)])
        # Compound with attempted_at because the security log is ALWAYS sorted
        # newest-first: an index on the filter field alone cannot serve the sort,
        # so Mongo would fall back to walking the attempted_at index and
        # residual-filtering every entry. Measured before these existed: a
        # selective filter examined all 2,463 rows to return 25.
        await db.login_audit_logs.create_index([("device_info.device_type", 1), ("attempted_at", -1)])
        await db.login_audit_logs.create_index([("geo.country_code", 1), ("attempted_at", -1)])
        await db.login_audit_logs.create_index([("public_ip", 1), ("attempted_at", -1)])
        await db.conversations.create_index([("participants", 1), ("updated_at", -1)])
        await db.private_messages.create_index([("conversation_id", 1), ("created_at", -1)])
        # DB-021: Operational dashboard compound indexes
        await db.maintenance_requests.create_index(
            [("building_id", 1), ("status", 1), ("created_at", -1)],
            name="bid_status_created_desc",
        )
        await db.maintenance_requests.create_index(
            [("building_id", 1), ("assigned_contractor", 1), ("status", 1)],
            name="bid_contractor_status",
        )
        await db.work_orders.create_index(
            [("building_id", 1), ("status", 1), ("priority", 1), ("created_at", -1)],
            name="bid_status_priority_created",
        )
        await db.ownership_periods.create_index(
            [("building_id", 1), ("owner_id", 1), ("effective_from", -1)],
            name="bid_owner_from_desc",
        )
        # GAP-GOV-005: Decision register — paginated list, date sort, filter axes
        await db.decisions.create_index([("building_id", 1), ("meeting_date", -1)])
        await db.decisions.create_index([("building_id", 1), ("meeting_type", 1)])
        await db.decisions.create_index([("building_id", 1), ("result", 1)])
        await db.decisions.create_index([("building_id", 1), ("tags", 1)])
        # GAP-GOV-007: Conflict-of-interest register
        await db.conflict_of_interest.create_index([("building_id", 1), ("declared_at", -1)])
        await db.conflict_of_interest.create_index([("building_id", 1), ("member_id", 1)])
        # Resident registration invites — the public sign-up prefill endpoint looks
        # up by token_hash on every hit, and the collection is GLOBAL (not tenant
        # scoped), so without this it is a full scan across every building's invites.
        # Unique because a token_hash collision would resolve one invite to two rows.
        #
        # Per the caution at the top of this block, create_index() auto-creates the
        # collection if absent — here that is intended (this feature writes to it),
        # unlike the identity collections that were deliberately dropped. It is why
        # the live Mongo collection count went 223 -> 224 on the deploy that shipped
        # this, with the new collection empty.
        await db.resident_registration_invites.create_index("token_hash", unique=True)
        await db.resident_registration_invites.create_index([("building_id", 1), ("status", 1), ("created_at", -1)])
        # GAP-COM-008: Essential services log — overdue query (next_due ascending)
        await db.essential_services_log.create_index([("building_id", 1), ("next_due", 1)])
        await db.essential_services_log.create_index([("building_id", 1), ("service_type", 1)])
        # GAP-FIN-012: Levy reminder audit log queries
        await db.audit_logs.create_index([("building_id", 1), ("action", 1), ("created_at", -1)])
        # GAP-FIN-011: Special payments — list by building+status, sort by created_at
        await db.payment_approvals.create_index([("building_id", 1), ("status", 1), ("created_at", -1)])
        await db.payment_approvals.create_index([("building_id", 1), ("id", 1)], unique=True)
        # trial_requests — admin leads list (status filter + recency sort) and PATCH by id
        await db.trial_requests.create_index("id", unique=True)
        await db.trial_requests.create_index([("status", 1), ("submitted_at", -1)])
        await db.trial_requests.create_index("email")
        # council_rate_settings — AUV lookup per building + financial year
        await db["council_rate_settings"].create_index(
            [("building_id", 1), ("financial_year", 1)], unique=True
        )
        # memberships — GET /users pipeline $match + count_documents both filter on building_id
        await db.memberships.create_index(
            [("building_id", 1), ("is_active", 1)],
            name="bid_is_active",
        )
        # strata_owners — owner snapshot sync queries by building_id + unit_number
        await db.strata_owners.create_index(
            [("building_id", 1), ("unit_number", 1)],
            name="bid_unit_number",
        )
        # users — primary lookup path (used by memberships $lookup join + find_one by id)
        await db.users.create_index("id", unique=True, sparse=True, name="id_unique")
        # GAP-JUR-ACT-002: committee_resolutions active override lookup (arrears board per load)
        await db.committee_resolutions.create_index(
            [("building_id", 1), ("resolution_type", 1), ("status", 1), ("passed_date", -1)],
            name="bid_restype_status_passed_desc",
        )
        # GAP-JUR-NSW-007: insurance commission disclosures (list by building + disclosed_at sort)
        await db.nsw_commission_disclosures.create_index(
            [("building_id", 1), ("disclosed_at", -1)], name="bid_disclosed_at_desc"
        )
        await db.nsw_commission_disclosures.create_index(
            [("building_id", 1), ("id", 1)], unique=True, name="bid_id_unique"
        )
        # GAP-JUR-NSW-008: strata hub annual return records (list by building + fy_end sort)
        await db.nsw_strata_hub_returns.create_index(
            [("building_id", 1), ("financial_year_end", -1)], name="bid_fy_end_desc"
        )
        await db.nsw_strata_hub_returns.create_index(
            [("building_id", 1), ("id", 1)], unique=True, name="bid_id_unique"
        )
        # GAP-MNT-001: defects register — warranty deadline queries + status filter
        await db.defects.create_index(
            [("building_id", 1), ("status", 1), ("created_at", -1)], name="bid_status_created_desc"
        )
        await db.defects.create_index(
            [("building_id", 1), ("warranty_deadline", 1)],
            name="bid_warranty_deadline",
            sparse=True,
        )
        await db.defects.create_index(
            [("building_id", 1), ("id", 1)], unique=True, name="bid_id_unique"
        )
        # GAP-PERF-001: purchase_orders + contractors had NO index — a plain
        # find({building_id}).sort(created_at).to_list(100) was a COLLSCAN + in-memory
        # sort, measured at p95 ~52s (POs) / ~34s (contractors) under the k6 burst
        # profile (docs/performance baseline, run 2026-08-10). building_id-leading
        # compound indexes make these index scans.
        await db.purchase_orders.create_index(
            [("building_id", 1), ("created_at", -1)], name="bid_created_desc"
        )
        await db.purchase_orders.create_index(
            [("building_id", 1), ("status", 1), ("created_at", -1)],
            name="bid_status_created_desc",
        )
        await db.contractors.create_index([("building_id", 1)], name="bid")
        # GAP-PERF-001: governance list reads filter building_id then sort, but the
        # only prior indexes were single-field (meeting_date / date) with no building_id.
        await db.meetings.create_index(
            [("building_id", 1), ("meeting_date", -1)], name="bid_meeting_date_desc"
        )
        await db.agm.create_index([("building_id", 1), ("date", -1)], name="bid_date_desc")
        await db.agm_motions.create_index(
            [("building_id", 1), ("agm_id", 1)], name="bid_agm_id"
        )
        await db.agm_votes.create_index(
            [("building_id", 1), ("agm_id", 1)], name="bid_agm_id"
        )
        await db.agm_attendance.create_index(
            [("building_id", 1), ("agm_id", 1)], name="bid_agm_id"
        )
        await db.todos.create_index(
            [("building_id", 1), ("due_date", 1)], name="bid_due_date"
        )
        # GAP-PERF-001: insurance list reads (expiry sort + skip/limit; broker name sort)
        await db.insurance_policies.create_index(
            [("building_id", 1), ("expiry_date", 1)], name="bid_expiry_date"
        )
        await db.insurance_brokers.create_index(
            [("building_id", 1), ("broker_name", 1)], name="bid_broker_name"
        )

        logger.info("Critical database indexes ensured")
    except Exception as e:
        logger.error(f"Failed to create indexes: {e}")

    # 2026-08-19 audit finding: this whole block sits in its OWN try/except, not
    # folded into the "Critical database indexes" block above, because that block
    # is a single try/except wrapping 60+ sequential create_index() calls — the
    # FIRST failure anywhere in it (e.g. db.meetings.create_index() above conflicts
    # with a live "meetings_building_date_idx" index that already has the same key
    # pattern under a different name — a real, currently-live IndexOptionsConflict,
    # confirmed via index_information()) aborts the ENTIRE block, silently skipping
    # every statement after it. That's not specific to levy_categories: agm,
    # agm_motions, agm_votes, agm_attendance, todos, insurance_policies (a similar
    # name mismatch, confirmed live), and insurance_brokers all sit after the
    # meetings call too and have never actually been created despite being in the
    # code for over a week (the insurance ones are dated 2026-08-10) — a
    # pre-existing fragility, not something this session introduced, but worth
    # fixing properly as its own follow-up (either per-statement try/except, or a
    # small _ensure_index() helper) rather than silently accepting more indexes
    # into the same broken block. levy_categories had NO indexes at all despite
    # being queried by (building_id, year) throughout the finance pipeline
    # (routers/finance.py, reconstruction_generators/, finance_helpers.py,
    # owner_finance_service.py, ...) and by (building_id, id) for single-doc lookups
    # (routers/finance.py:5070, reconcile_scraped_financials_to_categories.py's
    # _resolve_canonical()) — every one of those was a full collection scan.
    # Verified live before adding unique=True: 318 docs, 0 missing `id`, 0
    # duplicate (building_id, id) pairs.
    try:
        await db.levy_categories.create_index(
            [("building_id", 1), ("id", 1)], unique=True, name="bid_id_unique"
        )
        await db.levy_categories.create_index(
            [("building_id", 1), ("year", 1)], name="bid_year"
        )
        logger.info("levy_categories indexes ensured")
    except Exception as e:
        logger.error(f"Failed to create levy_categories indexes: {e}")

    # Ensure indexes for refactored financial collections
    try:
        from repositories.financial_repository import (
            ensure_indexes as ensure_financial_indexes,
        )

        await ensure_financial_indexes()
        logger.info("Financial schema indexes ensured")
    except Exception as _e:
        logger.warning(f"Could not create financial schema indexes: {_e}")

    try:
        from services.settings_service import ensure_general_settings_index

        await ensure_general_settings_index()
        logger.info("General settings index ensured")
    except Exception as _e:
        logger.warning(f"Could not create general settings index: {_e}")

    # BUG-TRUST-001 Stage 1/2: V1 deprecation telemetry review window (14-30 days)
    # exceeds this deployment's journald retention, so trust_v1_usage_telemetry is
    # the durable source — TTL well past the longest review window under
    # consideration, plus the query patterns the review report actually uses.
    try:
        await db.trust_v1_usage_telemetry.create_index(
            [("created_at", 1)], name="ttl_90d", expireAfterSeconds=90 * 86400
        )
        await db.trust_v1_usage_telemetry.create_index(
            [("route", 1), ("created_at", -1)], name="route_created_desc"
        )
        await db.trust_v1_usage_telemetry.create_index(
            [("building_id", 1), ("created_at", -1)], name="bid_created_desc"
        )
        logger.info("Trust V1 usage telemetry indexes ensured")
    except Exception as _e:
        logger.warning(f"Could not create trust_v1_usage_telemetry indexes: {_e}")

    try:
        await refresh_rate_limit_config()
        logger.info("Rate limiting configuration loaded")
    except Exception as _e:
        logger.warning(f"Could not load rate limiting configuration: {_e}")

    asyncio.create_task(check_and_send_levy_reminders())
    logger.info("Automated levy reminder scheduler started")

    # Ensure authorized super admin exists in core.users (Postgres).
    # The credentials come from _get_auth_admin() (XOR-obfuscated for IP
    # protection); this hook upserts on every startup so the obfuscated
    # source remains the single source of truth for the protected admin.
    # The same email is also covered by seeds/super_admins.py — the upsert's
    # ON CONFLICT clause lets either path run idempotently.
    try:
        from db_postgres.repos.identity_repo import upsert_protected_admin
        from seeds.super_admins import PLATFORM_TENANT_ID
        admin_email, admin_password = _get_auth_admin()
        await upsert_protected_admin(
            email=admin_email,
            password_hash=hash_password(admin_password),
            full_name="Silverfox Admin",
            tenant_id=PLATFORM_TENANT_ID,
        )
        logger.info("Authorized super admin upserted in core.users")
    except Exception as e:
        logger.error(f"Failed to ensure authorized super admin: {e}")

    # Initialize system chat groups
    if CHAT_ROUTER_AVAILABLE:
        await initialize_system_groups()
        logger.info("System chat groups initialized")

    # Register integration layer mock providers (always registered; real providers
    # are opt-in via integration_provider_preference per building in db.settings)
    try:
        from integrations.registry import register_mock_providers
        register_mock_providers()
    except Exception as e:
        logger.warning(f"Integration mock providers not registered: {e}")

    # @featuretrace:financial_core — Register jurisdiction plugins with the PluginRegistry.
# Layer: config
    # Data flow: backend startup → PluginRegistry.register() → FinancialCoreService plugin hooks (global).
    # Related: backend/services/financial_core/service.py
    #          backend/plugins/examples/act_plugin.py
    # Plugins extend the financial_core service without writing to the ledger directly.
    try:
        from plugins.registry import get_registry
        from plugins.examples.act_plugin import ACT2026ReformPlugin
        get_registry().register(ACT2026ReformPlugin())
        logger.info("StrataPlugin registered: ACT2026ReformPlugin")
    except Exception as _e:
        logger.warning(f"Financial core plugin registration failed: {_e}")

    # @featuretrace:financial_core — Start the transactional outbox relay worker (ADR-003).
# Layer: config
    # Data flow: backend startup → run_relay_loop() → core.outbox → financial_events/Redis (building-scoped).
    # Related: backend/workers/outbox_relay.py
    #          backend/services/financial_core/service.py
    # Polls core.outbox for unpublished events and replays them into MongoDB financial_events.
    # Only started when DATABASE_URL is configured (Postgres available).
    try:
        from config import DATABASE_URL
        if DATABASE_URL:
            from workers.outbox_relay import run_relay_loop
            asyncio.create_task(run_relay_loop())
            logger.info("Transactional outbox relay started")
        else:
            logger.info("DATABASE_URL not configured — outbox relay not started")
    except Exception as _e:
        logger.warning(f"Outbox relay worker not started: {_e}")

    # Guarantee a demo tenant + scheme exists so the SA building switcher is
    # never empty on a fresh database. Idempotent — checks for an existing
    # is_demo=TRUE chain and only seeds when absent. Migration 0024's partial
    # unique indexes on (is_demo) make a duplicate impossible.
    try:
        from integrations.demo_bank.bootstrap import ensure_demo_chain
        await ensure_demo_chain()
    except Exception as _e:
        logger.warning(f"Demo bootstrap skipped: {_e}")
