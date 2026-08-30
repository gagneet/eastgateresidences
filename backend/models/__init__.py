"""
Pydantic models for East Gate Residences Strata Management System.

This package contains all data models organized by domain:
- user: User authentication, roles, permissions
- document: Document management and access control
- finance: Financial entries, budgets, projections, levy management
- maintenance: Maintenance requests, contractors, purchase orders, invoices
- communication: Messages, announcements, conversations
- community: Meetings, todos, blog posts, EC members, AGM, events
- booking: Defects, move bookings, amenity bookings, parcels
- listing: Marketplace listings
- notification: Notification management
- settings: Site settings, email configuration, schedules
"""

# Booking models
from .booking import (
    DefectCreate,
    DefectResponse,
    MoveBookingCreate,
    MoveBookingResponse,
    AmenityBookingCreate,
    AmenityBookingResponse,
    ParcelCreate,
    ParcelResponse,
)
# Communication models
from .communication import (
    MessageCreate,
    MessageResponse,
    AnnouncementCreate,
    AnnouncementResponse,
    ConversationCreate,
    ConversationResponse,
    PrivateMessageCreate,
    PrivateMessageResponse,
)
# Community models
from .community import (
    MeetingCreate,
    MeetingResponse,
    TodoCreate,
    TodoResponse,
    BlogPostCreate,
    BlogPostResponse,
    ECMemberCreate,
    ECMemberResponse,
    EmergencyServiceCreate,
    EmergencyServiceResponse,
    AGMMotionCreate,
    AGMMotionResponse,
    AGMVoteCreate,
    AGMCreate,
    AGMResponse,
    CommunityEventCreate,
    CommunityEventResponse,
    ResidentDirectoryEntry,
)
# Document models
from .document import (
    DocumentAccess,
    DocumentCategory,
    DocumentCreate,
    DocumentResponse,
    DocumentPermissionUpdate,
)
# Finance models (new clean architecture 2026-02-19)
from .finance import (
    AnnualFundSummary,
    PaymentScheduleEntry,
    AnnualLevyCreate,
    AnnualLevyResponse,
    LevyCategoryCreate,
    LevyCategoryResponse,
    LevyCategoryUpdate,
    UnitLevyLedgerResponse,
    LevyPaymentCreate,
    LevyPaymentResponse,
    ProjectionAssumptions,
    FinancialProjectionCreate,
    FinancialProjectionResponse,
)
# Listing models
from .listing import (
    ListingType,
    ListingCreate,
    ListingResponse,
)
# Maintenance models
from .maintenance import (
    MaintenanceStatus,
    MaintenanceRequestCreate,
    MaintenanceRequestResponse,
    ContractorCreate,
    ContractorResponse,
    PurchaseOrderCreate,
    PurchaseOrderResponse,
    InvoiceCreate,
    InvoiceResponse,
)
# Notification models
from .notification import (
    NotificationCreate,
    NotificationResponse,
    UserNotification,
    UserNotificationResponse,
)
# Settings models
from .settings import (
    SiteSettingsUpdate,
    EmailSettingsUpdate,
    ScheduleCreate,
    ScheduleResponse,
)
# User models
from .user import (
    UserRole,
    Permission,
    DEFAULT_PERMISSIONS,
    UserBase,
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
    AuthResponse,
    PasswordResetRequest,
    PasswordResetConfirm,
)

__all__ = [
    # User
    "UserRole",
    "Permission",
    "DEFAULT_PERMISSIONS",
    "UserBase",
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserUpdate",
    "AuthResponse",
    "PasswordResetRequest",
    "PasswordResetConfirm",
    # Document
    "DocumentAccess",
    "DocumentCategory",
    "DocumentCreate",
    "DocumentResponse",
    "DocumentPermissionUpdate",
    # Finance
    "FinanceEntryCreate",
    "FinanceEntryResponse",
    "BudgetCategory",
    "AnnualBudgetCreate",
    "AnnualBudgetResponse",
    "ProjectionAssumptions",
    "FinancialProjectionCreate",
    "FinancialProjectionResponse",
    "LevyPaymentCreate",
    "LevyPaymentResponse",
    "PaymentMethodConfig",
    "UnitEntitlementCreate",
    "UnitEntitlementResponse",
    "LevyCalculation",
    # Maintenance
    "MaintenanceStatus",
    "MaintenanceRequestCreate",
    "MaintenanceRequestResponse",
    "ContractorCreate",
    "ContractorResponse",
    "PurchaseOrderCreate",
    "PurchaseOrderResponse",
    "InvoiceCreate",
    "InvoiceResponse",
    # Communication
    "MessageCreate",
    "MessageResponse",
    "AnnouncementCreate",
    "AnnouncementResponse",
    "ConversationCreate",
    "ConversationResponse",
    "PrivateMessageCreate",
    "PrivateMessageResponse",
    # Community
    "MeetingCreate",
    "MeetingResponse",
    "TodoCreate",
    "TodoResponse",
    "BlogPostCreate",
    "BlogPostResponse",
    "ECMemberCreate",
    "ECMemberResponse",
    "EmergencyServiceCreate",
    "EmergencyServiceResponse",
    "AGMMotionCreate",
    "AGMMotionResponse",
    "AGMVoteCreate",
    "AGMCreate",
    "AGMResponse",
    "CommunityEventCreate",
    "CommunityEventResponse",
    "ResidentDirectoryEntry",
    # Booking
    "DefectCreate",
    "DefectResponse",
    "MoveBookingCreate",
    "MoveBookingResponse",
    "AmenityBookingCreate",
    "AmenityBookingResponse",
    "ParcelCreate",
    "ParcelResponse",
    # Listing
    "ListingType",
    "ListingCreate",
    "ListingResponse",
    # Notification
    "NotificationCreate",
    "NotificationResponse",
    "UserNotification",
    "UserNotificationResponse",
    # Settings
    "SiteSettingsUpdate",
    "EmailSettingsUpdate",
    "ScheduleCreate",
    "ScheduleResponse",
]
