"""
Seeds navigation_configs collection with role defaults.
Runs once globally (not per-building — configs are role-level defaults).
Also seeds new feature toggles if they don't already exist.

Usage: python3 backend/seeds/navigation_configs.py
"""
import os
import sys
from pathlib import Path

import asyncio
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from datetime import datetime, timezone

# Anchored to backend/.env (not CWD-relative) — matches seeds/demo_customer.py.
# Without this, MONGO_URL/DB_NAME silently fall back to an unauthenticated
# localhost default instead of the real credentialed connection string.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
DB_NAME = os.environ.get("DB_NAME", "strataos_production")

NEW_FEATURE_TOGGLES = [
    {"feature_key": "progressive_nav", "feature_name": "Progressive Navigation System",
     "category": "system", "is_enabled": True,
     "description": "Three-layer progressive navigation with simple/advanced modes"},
    {"feature_key": "adaptive_nav", "feature_name": "Adaptive Navigation AI",
     "category": "system", "is_enabled": True,
     "description": "Reorders advanced menu items based on user behaviour"},
    {"feature_key": "nav_discovery_nudges", "feature_name": "Feature Discovery Nudges",
     "category": "system", "is_enabled": True,
     "description": "Periodic prompts to explore unused features"},
    {"feature_key": "nav_customisation", "feature_name": "Navigation Customisation",
     "category": "system", "is_enabled": True,
     "description": "Allow users to pin, hide, and reorder nav items"},
    {"feature_key": "capital_funding_workspace", "feature_name": "Capital Funding & Special Levy Workspace",
     "category": "financial", "is_enabled": False,
     "description": "Preview-only major works funding calculator, governance gate and levy notice batch workspace"},
    {"feature_key": "fund_collections_by_unit_type_report", "feature_name": "Fund Collections by Unit Type",
     "category": "financial", "is_enabled": True,
     "description": "Life-to-date Admin/Sinking Fund collected totals broken down by unit type"},
]

NAV_CONFIGS = {
    "super_admin": {
        "simple_items": [
            {"id": "dashboard", "label": "Dashboard", "route": "/dashboard", "icon": "LayoutDashboard",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "users", "label": "Users", "route": "/admin/users", "icon": "Users", "feature_flag": "",
             "permission_flag": "can_manage_users", "badge_source": "", "priority": 2},
            {"id": "finance", "label": "Finance", "route": "/financials/overview", "icon": "DollarSign",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3},
            {"id": "maintenance", "label": "Maintenance", "route": "/maintenance", "icon": "Wrench",
             "feature_flag": "", "permission_flag": "", "badge_source": "approvals_pending", "priority": 4},
            {"id": "system", "label": "System", "route": "/admin", "icon": "Settings", "feature_flag": "",
             "permission_flag": "can_manage_settings", "badge_source": "", "priority": 5},
        ],
        "advanced_items": [
            # Operations
            {"id": "requests", "label": "Requests", "route": "/requests/new", "icon": "MessageSquare",
             "feature_flag": "smart_requests", "permission_flag": "", "badge_source": "requests_overdue", "priority": 1,
             "discovery_hint": "All maintenance & change requests", "nudge_trigger": "open_requests_gte_5"},
            {"id": "approvals", "label": "Approvals", "route": "/requests/my-approvals", "icon": "CheckSquare",
             "feature_flag": "", "permission_flag": "", "badge_source": "approvals_pending", "priority": 2,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "compliance", "label": "Compliance", "route": "/compliance", "icon": "ShieldCheck",
             "feature_flag": "", "permission_flag": "", "badge_source": "compliance_overdue", "priority": 3,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "reports", "label": "Reports", "route": "/reports", "icon": "FileText", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 4, "discovery_hint": "", "nudge_trigger": ""},
            # Governance
            {"id": "meetings", "label": "Meetings", "route": "/governance/meetings", "icon": "Calendar",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 5, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "bylaws", "label": "By-laws", "route": "/governance/bylaws", "icon": "BookOpen", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 6, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "proposals", "label": "Proposals", "route": "/governance/proposals", "icon": "Vote",
             "feature_flag": "", "permission_flag": "", "badge_source": "proposals_open_vote", "priority": 7,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "ec-members", "label": "EC members", "route": "/governance/ec-members", "icon": "UserCheck",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 8, "discovery_hint": "",
             "nudge_trigger": ""},
            # Communication & community
            {"id": "notices", "label": "Notices", "route": "/community/notices", "icon": "Bell", "feature_flag": "",
             "permission_flag": "", "badge_source": "notices_unread", "priority": 9, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "community", "label": "Community", "route": "/community", "icon": "Users",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 10, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "events", "label": "Events", "route": "/community/events", "icon": "Calendar", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 11, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "marketplace", "label": "Marketplace", "route": "/community/marketplace", "icon": "ShoppingBag",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 12, "discovery_hint": "",
             "nudge_trigger": ""},
            # Documents & finance
            {"id": "documents", "label": "Documents", "route": "/documents", "icon": "FolderOpen",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 13, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "levies", "label": "Levy management", "route": "/financials/levy-payments", "icon": "CreditCard",
             "feature_flag": "", "permission_flag": "", "badge_source": "levy_due_soon", "priority": 14,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "insurance", "label": "Insurance", "route": "/insurance", "icon": "Shield",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 15, "discovery_hint": "",
             "nudge_trigger": ""},
            # Admin
            {"id": "settings", "label": "Settings", "route": "/settings", "icon": "Settings",
             "feature_flag": "", "permission_flag": "can_manage_settings", "badge_source": "", "priority": 16,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "feature-toggles", "label": "Feature toggles", "route": "/admin/feature-toggles",
             "icon": "ToggleLeft", "feature_flag": "", "permission_flag": "can_manage_settings", "badge_source": "",
             "priority": 17, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "audit-logs", "label": "Audit logs", "route": "/admin/audit-logs", "icon": "FileText",
             "feature_flag": "", "permission_flag": "can_view_audit", "badge_source": "", "priority": 18,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "portfolio", "label": "Portfolio", "route": "/management/portfolio", "icon": "Building2",
             "feature_flag": "portfolio_dashboard", "permission_flag": "", "badge_source": "", "priority": 19,
             "discovery_hint": "All buildings in one view", "nudge_trigger": "multiple_buildings_accessible"},
            {"id": "building-onboarding", "label": "Onboard Building",
             "route": "/admin/portfolio/onboard", "icon": "ClipboardCheck",
             "feature_flag": "building_onboarding", "permission_flag": "", "badge_source": "", "priority": 20,
             "discovery_hint": "Run the full scheme onboarding workflow", "nudge_trigger": ""},
            {"id": "quick-building-setup", "label": "Quick Building Setup",
             "route": "/admin/buildings/onboard", "icon": "Building2",
             "feature_flag": "building_onboarding", "permission_flag": "", "badge_source": "", "priority": 21,
             "discovery_hint": "Create a scheme shell and import a Strata Roll CSV", "nudge_trigger": ""},
            {"id": "financial-onboarding", "label": "Financial Onboarding",
             "route": "/admin/financial-onboarding", "icon": "Upload",
             "feature_flag": "historical_financial_reconstruction", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 22,
             "discovery_hint": "Complete historical financial onboarding through Demo Bank staging",
             "nudge_trigger": ""},
            {"id": "workflows", "label": "Workflows", "route": "/admin/workflows", "icon": "GitBranch",
             "feature_flag": "workflow_governance", "permission_flag": "", "badge_source": "", "priority": 23,
             "discovery_hint": "See which automations ran today", "nudge_trigger": ""},
            {"id": "gst-bas-ledger", "label": "GST & BAS Ledger", "route": "/admin/gst-bas-ledger",
             "icon": "Receipt", "feature_flag": "gst_bas_ledger", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 24,
             "discovery_hint": "BAS worksheet, output tax, input tax credits", "nudge_trigger": ""},
            {"id": "financial-reports", "label": "Financial reports", "route": "/reports?report=general-ledger",
             "icon": "BookOpenCheck", "feature_flag": "finance", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 25,
             "discovery_hint": "Aged levy receivables and general ledger by Levy Financial Year",
             "nudge_trigger": ""},
            {"id": "capital-funding", "label": "Capital funding", "route": "/financials/capital-funding",
             "icon": "Landmark", "feature_flag": "capital_funding_workspace",
             "permission_flag": "can_manage_finances", "badge_source": "", "priority": 26,
             "discovery_hint": "Model major works funding and special levy notice previews",
             "nudge_trigger": ""},
            {"id": "fund-collections-by-type", "label": "Fund collections by unit type",
             "route": "/financials/fund-collections-by-type", "icon": "PieChart",
             "feature_flag": "fund_collections_by_unit_type_report", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 27,
             "discovery_hint": "Life-to-date Admin/Sinking Fund collections by Apartment/Townhouse",
             "nudge_trigger": ""},
        ]
    },
    "strata_manager": {
        "simple_items": [
            {"id": "dashboard", "label": "Dashboard", "route": "/dashboard", "icon": "LayoutDashboard",
             "feature_flag": "", "permission_flag": "", "badge_source": "sla_breached", "priority": 1},
            {"id": "requests", "label": "Requests", "route": "/requests/new", "icon": "MessageSquare",
             "feature_flag": "smart_requests", "permission_flag": "", "badge_source": "requests_overdue",
             "priority": 2},
            {"id": "finance", "label": "Finance", "route": "/financials/overview", "icon": "DollarSign",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3},
            {"id": "maintenance", "label": "Maintenance", "route": "/maintenance", "icon": "Wrench",
             "feature_flag": "", "permission_flag": "", "badge_source": "approvals_pending", "priority": 4},
            {"id": "compliance", "label": "Compliance", "route": "/compliance", "icon": "ShieldCheck",
             "feature_flag": "", "permission_flag": "", "badge_source": "compliance_overdue", "priority": 5},
        ],
        "advanced_items": [
            {"id": "portfolio", "label": "Portfolio", "route": "/management/portfolio", "icon": "Building2",
             "feature_flag": "portfolio_dashboard", "permission_flag": "", "badge_source": "", "priority": 1,
             "discovery_hint": "All buildings in one view", "nudge_trigger": "multiple_buildings_accessible"},
            {"id": "building-onboarding", "label": "Onboard Building",
             "route": "/admin/portfolio/onboard", "icon": "ClipboardCheck",
             "feature_flag": "building_onboarding", "permission_flag": "", "badge_source": "", "priority": 2,
             "discovery_hint": "Run the full scheme onboarding workflow", "nudge_trigger": ""},
            {"id": "quick-building-setup", "label": "Quick Building Setup",
             "route": "/admin/buildings/onboard", "icon": "Building2",
             "feature_flag": "building_onboarding", "permission_flag": "", "badge_source": "", "priority": 3,
             "discovery_hint": "Create a scheme shell and import a Strata Roll CSV", "nudge_trigger": ""},
            {"id": "financial-onboarding", "label": "Financial Onboarding",
             "route": "/admin/financial-onboarding", "icon": "Upload",
             "feature_flag": "historical_financial_reconstruction", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 4,
             "discovery_hint": "Complete historical financial onboarding through Demo Bank staging",
             "nudge_trigger": ""},
            {"id": "workflows", "label": "Workflows", "route": "/admin/workflows", "icon": "GitBranch",
             "feature_flag": "workflow_governance", "permission_flag": "", "badge_source": "", "priority": 5,
             "discovery_hint": "See which automations ran today", "nudge_trigger": "open_requests_gte_5"},
            {"id": "users", "label": "Users & roles", "route": "/admin/users", "icon": "UserCog",
             "feature_flag": "", "permission_flag": "can_manage_users", "badge_source": "", "priority": 6,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "reports", "label": "Reports", "route": "/reports", "icon": "FileText", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 7, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "settings", "label": "Settings", "route": "/settings", "icon": "Settings",
             "feature_flag": "", "permission_flag": "can_manage_settings", "badge_source": "", "priority": 8,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "levies", "label": "Levy management", "route": "/financials/levy-payments", "icon": "CreditCard",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 9, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "meetings", "label": "Meetings", "route": "/governance/meetings", "icon": "Calendar",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 10, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "bylaws", "label": "By-laws", "route": "/governance/bylaws", "icon": "BookOpen", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 11, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "documents", "label": "Documents", "route": "/documents", "icon": "FolderOpen",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 12, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "insurance", "label": "Insurance", "route": "/insurance", "icon": "Shield",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 13, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "gst-bas-ledger", "label": "GST & BAS Ledger", "route": "/admin/gst-bas-ledger",
             "icon": "Receipt", "feature_flag": "gst_bas_ledger", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 14,
             "discovery_hint": "BAS worksheet, output tax, input tax credits", "nudge_trigger": ""},
            {"id": "financial-reports", "label": "Financial reports", "route": "/reports?report=general-ledger",
             "icon": "BookOpenCheck", "feature_flag": "finance", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 15,
             "discovery_hint": "Aged levy receivables and general ledger by Levy Financial Year",
             "nudge_trigger": ""},
            {"id": "capital-funding", "label": "Capital funding", "route": "/financials/capital-funding",
             "icon": "Landmark", "feature_flag": "capital_funding_workspace",
             "permission_flag": "can_manage_finances", "badge_source": "", "priority": 16,
             "discovery_hint": "Model major works funding and special levy notice previews",
             "nudge_trigger": ""},
            {"id": "fund-collections-by-type", "label": "Fund collections by unit type",
             "route": "/financials/fund-collections-by-type", "icon": "PieChart",
             "feature_flag": "fund_collections_by_unit_type_report", "permission_flag": "can_manage_finances",
             "badge_source": "", "priority": 17,
             "discovery_hint": "Life-to-date Admin/Sinking Fund collections by Apartment/Townhouse",
             "nudge_trigger": ""},
        ]
    },
    "ec_member": {
        "simple_items": [
            {"id": "dashboard", "label": "Dashboard", "route": "/dashboard", "icon": "LayoutDashboard",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "approvals", "label": "Approvals", "route": "/requests/my-approvals", "icon": "CheckSquare",
             "feature_flag": "", "permission_flag": "", "badge_source": "approvals_pending", "priority": 2},
            {"id": "meetings", "label": "Meetings", "route": "/governance/meetings", "icon": "Calendar",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3},
            {"id": "maintenance", "label": "Maintenance", "route": "/maintenance", "icon": "Wrench",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4},
            {"id": "community", "label": "Community", "route": "/community", "icon": "Users",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 5},
        ],
        "advanced_items": [
            {"id": "finance", "label": "Finance", "route": "/financials/overview", "icon": "DollarSign",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1,
             "discovery_hint": "See full financial position", "nudge_trigger": ""},
            {"id": "financial-reports", "label": "Financial reports", "route": "/reports",
             "icon": "BookOpenCheck", "feature_flag": "finance", "permission_flag": "",
             "badge_source": "", "priority": 2,
             "discovery_hint": "Aged levy receivables and general ledger reports", "nudge_trigger": ""},
            {"id": "capital-funding-review", "label": "Capital funding", "route": "/financials/capital-funding",
             "icon": "Landmark", "feature_flag": "capital_funding_workspace", "permission_flag": "",
             "badge_source": "", "priority": 3,
             "discovery_hint": "Review major works funding scenarios and meeting evidence", "nudge_trigger": ""},
            {"id": "fund-collections-by-type", "label": "Fund collections by unit type",
             "route": "/financials/fund-collections-by-type", "icon": "PieChart",
             "feature_flag": "fund_collections_by_unit_type_report", "permission_flag": "", "badge_source": "",
             "priority": 100,
             "discovery_hint": "Life-to-date Admin/Sinking Fund collections by Apartment/Townhouse",
             "nudge_trigger": ""},
            {"id": "bylaws", "label": "By-laws", "route": "/governance/bylaws", "icon": "BookOpen", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 4, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "compliance", "label": "Compliance", "route": "/compliance", "icon": "ShieldCheck",
             "feature_flag": "", "permission_flag": "", "badge_source": "compliance_overdue", "priority": 5,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "documents", "label": "Documents", "route": "/documents", "icon": "FolderOpen",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 6, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "proposals", "label": "Proposals", "route": "/governance/proposals", "icon": "Vote",
             "feature_flag": "", "permission_flag": "", "badge_source": "proposals_open_vote", "priority": 7,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "owner-view", "label": "My owner view", "route": "/owner-view", "icon": "Home",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 8,
             "discovery_hint": "See your own levy and property data", "nudge_trigger": ""},
        ]
    },
    "owner": {
        "simple_items": [
            {"id": "home", "label": "Home", "route": "/dashboard", "icon": "Home", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "my-levies", "label": "My levies", "route": "/financials/levy-payments", "icon": "CreditCard",
             "feature_flag": "", "permission_flag": "", "badge_source": "levy_due_soon", "priority": 2},
            {"id": "requests", "label": "Requests", "route": "/requests/new", "icon": "MessageSquare",
             "feature_flag": "smart_requests", "permission_flag": "", "badge_source": "requests_new", "priority": 3},
            {"id": "notices", "label": "Notices", "route": "/community/notices", "icon": "Bell", "feature_flag": "",
             "permission_flag": "", "badge_source": "notices_unread", "priority": 4},
            {"id": "vote", "label": "Vote", "route": "/governance/proposals", "icon": "Vote", "feature_flag": "",
             "permission_flag": "", "badge_source": "proposals_open_vote", "priority": 5},
        ],
        "advanced_items": [
            {"id": "my-finances", "label": "My finances", "route": "/financials/my-finances", "icon": "PieChart",
             "feature_flag": "my_finances", "permission_flag": "", "badge_source": "", "priority": 1,
             "discovery_hint": "See exactly where your levy goes", "nudge_trigger": "levy_page_visits_7d_gte_3"},
            {"id": "financial-reports", "label": "Financial reports",
             "route": "/reports", "icon": "BookOpenCheck",
             "feature_flag": "finance", "permission_flag": "", "badge_source": "", "priority": 2,
             "discovery_hint": "See your aged levy receivables report", "nudge_trigger": ""},
            {"id": "capital-funding-elections", "label": "Capital funding votes",
             "route": "/financials/capital-funding/elections", "icon": "Vote",
             "feature_flag": "capital_funding_owner_elections", "permission_flag": "",
             "badge_source": "", "priority": 3,
             "discovery_hint": "Review major works funding options and owner elections", "nudge_trigger": ""},
            {"id": "documents", "label": "Documents", "route": "/documents", "icon": "FolderOpen",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "community", "label": "Community", "route": "/community", "icon": "Users",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 5, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "savings", "label": "Savings tracker", "route": "/financials/savings", "icon": "PiggyBank",
             "feature_flag": "volunteer_credits", "permission_flag": "", "badge_source": "", "priority": 6,
             "discovery_hint": "See what your community saved this year",
             "nudge_trigger": "savings_event_created_last_7d"},
            {"id": "marketplace", "label": "Marketplace", "route": "/community/marketplace", "icon": "ShoppingBag",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 7, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "events", "label": "Events", "route": "/community/events", "icon": "Calendar", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 8, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "bookings", "label": "Bookings", "route": "/community/bookings", "icon": "BookMarked",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 9, "discovery_hint": "",
             "nudge_trigger": ""},
        ]
    },
    "tenant": {
        "simple_items": [
            {"id": "home", "label": "Home", "route": "/dashboard", "icon": "Home", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "requests", "label": "Requests", "route": "/requests/new", "icon": "MessageSquare",
             "feature_flag": "smart_requests", "permission_flag": "", "badge_source": "requests_new", "priority": 2},
            {"id": "notices", "label": "Notices", "route": "/community/notices", "icon": "Bell", "feature_flag": "",
             "permission_flag": "", "badge_source": "notices_unread", "priority": 3},
            {"id": "parcels", "label": "Parcels", "route": "/community/parcels", "icon": "Package", "feature_flag": "",
             "permission_flag": "", "badge_source": "parcels_waiting", "priority": 4},
            {"id": "chat", "label": "Chat", "route": "/community/chat", "icon": "MessageCircle", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 5},
        ],
        "advanced_items": [
            {"id": "events", "label": "Events", "route": "/community/events", "icon": "Calendar", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 1, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "marketplace", "label": "Marketplace", "route": "/community/marketplace", "icon": "ShoppingBag",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 2, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "bookings", "label": "Bookings", "route": "/community/bookings", "icon": "BookMarked",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "volunteer-credits", "label": "Volunteer credits", "route": "/community/volunteer", "icon": "Star",
             "feature_flag": "volunteer_credits", "permission_flag": "", "badge_source": "", "priority": 4,
             "discovery_hint": "Earn levy credits for helping out", "nudge_trigger": ""},
            {"id": "tenancy-passport", "label": "My passport", "route": "/profile/passport", "icon": "IdCard",
             "feature_flag": "tenancy_passport", "permission_flag": "", "badge_source": "", "priority": 5,
             "discovery_hint": "Your portable residency record", "nudge_trigger": ""},
            {"id": "suburb-radar", "label": "Suburb radar", "route": "/intelligence/suburb-radar", "icon": "Map",
             "feature_flag": "suburb_radar", "permission_flag": "", "badge_source": "", "priority": 6,
             "discovery_hint": "Weekly local intelligence", "nudge_trigger": ""},
            {"id": "bylaws", "label": "By-laws", "route": "/governance/bylaws", "icon": "BookOpen", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 7, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "documents", "label": "Documents", "route": "/documents", "icon": "FolderOpen",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 8, "discovery_hint": "",
             "nudge_trigger": ""},
        ]
    },
    "real_estate_agent": {
        "simple_items": [
            {"id": "dashboard", "label": "Dashboard", "route": "/dashboard", "icon": "LayoutDashboard",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "my-tenants", "label": "My tenants", "route": "/real-estate", "icon": "Users",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 2},
            {"id": "maintenance", "label": "Maintenance", "route": "/maintenance", "icon": "Wrench",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3},
            {"id": "notices", "label": "Notices", "route": "/community/notices", "icon": "Bell", "feature_flag": "",
             "permission_flag": "", "badge_source": "notices_unread", "priority": 4},
            {"id": "certificates", "label": "Certificates", "route": "/requests/rental-certificates", "icon": "Award",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 5},
        ],
        "advanced_items": [
            {"id": "lease-renewals", "label": "Lease renewals", "route": "/real-estate/renewals",
             "icon": "RefreshCw", "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1,
             "discovery_hint": "Track upcoming lease expiries", "nudge_trigger": ""},
            {"id": "inspections", "label": "Inspections", "route": "/owner-hub/inspections",
             "icon": "ClipboardCheck", "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 2,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "documents", "label": "Documents", "route": "/documents", "icon": "FolderOpen",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "owner-reports", "label": "Owner reports", "route": "/reports",
             "icon": "FileText", "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4,
             "discovery_hint": "", "nudge_trigger": ""},
        ]
    },
    "admin_staff": {
        "simple_items": [
            {"id": "dashboard", "label": "Dashboard", "route": "/dashboard", "icon": "LayoutDashboard",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "parcels", "label": "Parcels", "route": "/community/parcels", "icon": "Package", "feature_flag": "",
             "permission_flag": "", "badge_source": "parcels_waiting", "priority": 2},
            {"id": "visitors", "label": "Visitors", "route": "/community/bookings/visitors", "icon": "UserCheck",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3},
            {"id": "bookings", "label": "Bookings", "route": "/community/bookings", "icon": "BookMarked",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4},
            {"id": "notices", "label": "Notices", "route": "/community/notices", "icon": "Bell", "feature_flag": "",
             "permission_flag": "", "badge_source": "notices_unread", "priority": 5},
        ],
        "advanced_items": [
            {"id": "requests-log", "label": "Requests log", "route": "/requests", "icon": "MessageSquare",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "keys", "label": "Key register", "route": "/requests/access-control", "icon": "Key", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 2, "discovery_hint": "", "nudge_trigger": ""},
            {"id": "moves", "label": "Move in/out", "route": "/community/bookings/moves", "icon": "ArrowLeftRight",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "directory", "label": "Resident directory", "route": "/community/directory", "icon": "Users",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "incidents", "label": "Incident report", "route": "/requests/new", "icon": "AlertTriangle",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 5, "discovery_hint": "",
             "nudge_trigger": ""},
        ]
    },
    "service_provider": {
        "simple_items": [
            {"id": "dashboard", "label": "Dashboard", "route": "/dashboard", "icon": "LayoutDashboard",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "my-work-orders", "label": "My work orders", "route": "/maintenance/work-orders",
             "icon": "Wrench", "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 2},
            {"id": "invoices", "label": "Invoices", "route": "/financials/invoices", "icon": "FileText",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3},
            {"id": "documents", "label": "Documents", "route": "/documents", "icon": "FolderOpen",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4},
            {"id": "profile", "label": "Profile", "route": "/profile", "icon": "User", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 5},
        ],
        "advanced_items": [
            {"id": "compliance-docs", "label": "My compliance docs", "route": "/compliance",
             "icon": "ShieldCheck", "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1,
             "discovery_hint": "Keep insurances and licences current", "nudge_trigger": ""},
            {"id": "purchase-orders", "label": "Purchase orders", "route": "/maintenance/purchase-orders",
             "icon": "ShoppingCart", "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 2,
             "discovery_hint": "", "nudge_trigger": ""},
            {"id": "schedule", "label": "Schedule", "route": "/maintenance/schedule", "icon": "Calendar",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3, "discovery_hint": "",
             "nudge_trigger": ""},
        ]
    },
    "guest": {
        "simple_items": [
            {"id": "home", "label": "Home", "route": "/dashboard", "icon": "Home", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 1},
            {"id": "notices", "label": "Notices", "route": "/community/notices", "icon": "Bell", "feature_flag": "",
             "permission_flag": "", "badge_source": "notices_unread", "priority": 2},
            {"id": "requests", "label": "Requests", "route": "/requests", "icon": "MessageSquare",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 3},
            {"id": "bookings", "label": "Bookings", "route": "/community/bookings", "icon": "BookMarked",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4},
            {"id": "my-stay", "label": "My stay", "route": "/community/bookings/my-stay", "icon": "Calendar",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 5},
        ],
        "advanced_items": [
            {"id": "directory", "label": "Resident directory", "route": "/community/directory", "icon": "Users",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "events", "label": "Community events", "route": "/community/events", "icon": "Calendar",
             "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 2, "discovery_hint": "",
             "nudge_trigger": ""},
            {"id": "chat", "label": "Chat", "route": "/community/chat", "icon": "MessageCircle", "feature_flag": "",
             "permission_flag": "", "badge_source": "", "priority": 3, "discovery_hint": "", "nudge_trigger": ""},
        ]
    },
}

LEGACY_NAV_ROLES = ("chairman", "reception")


def _advanced_items_excluding(role: str, excluded_ids: set[str]) -> list[dict]:
    """Clone another role's advanced items, excluding IDs promoted elsewhere."""
    return [
        deepcopy(item)
        for item in NAV_CONFIGS[role]["advanced_items"]
        if item["id"] not in excluded_ids
    ]


# Keep organisation-admin navigation separate from EC/chairperson navigation so
# a chairperson cannot inherit company-wide controls.
NAV_CONFIGS["strata_admin"] = {
    "simple_items": [
        {"id": "dashboard", "label": "Organisation dashboard", "route": "/dashboard", "icon": "LayoutDashboard",
         "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1},
        {"id": "portfolio", "label": "Portfolio", "route": "/management/portfolio", "icon": "Building2",
         "feature_flag": "portfolio_dashboard", "permission_flag": "", "badge_source": "", "priority": 2},
        {"id": "users", "label": "Users & roles", "route": "/admin/users", "icon": "UserCog",
         "feature_flag": "", "permission_flag": "can_manage_users", "badge_source": "", "priority": 3},
        {"id": "reports", "label": "Reports", "route": "/reports", "icon": "FileText",
         "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 4},
        {"id": "settings", "label": "Organisation settings", "route": "/settings", "icon": "Settings",
         "feature_flag": "", "permission_flag": "can_manage_settings", "badge_source": "", "priority": 5},
    ],
    "advanced_items": [
        {"id": "finance", "label": "Finance oversight", "route": "/financials/overview", "icon": "DollarSign",
         "feature_flag": "", "permission_flag": "", "badge_source": "", "priority": 1,
         "discovery_hint": "Portfolio finance position", "nudge_trigger": ""},
        {"id": "capital-funding", "label": "Capital funding", "route": "/financials/capital-funding",
         "icon": "Landmark", "feature_flag": "capital_funding_workspace",
         "permission_flag": "can_manage_finances", "badge_source": "", "priority": 2,
         "discovery_hint": "Model major works funding and special levy notice previews", "nudge_trigger": ""},
        # fund-collections-by-type is NOT listed here directly — it's inherited via
        # _advanced_items_excluding("strata_manager", ...) below, same as
        # financial-reports/levies/etc. Adding it here too would duplicate it.
        {"id": "compliance", "label": "Compliance oversight", "route": "/compliance", "icon": "ShieldCheck",
         "feature_flag": "", "permission_flag": "", "badge_source": "compliance_overdue", "priority": 3,
         "discovery_hint": "", "nudge_trigger": ""},
        {"id": "maintenance", "label": "Maintenance oversight", "route": "/maintenance", "icon": "Wrench",
         "feature_flag": "", "permission_flag": "", "badge_source": "requests_overdue", "priority": 4,
         "discovery_hint": "", "nudge_trigger": ""},
        *_advanced_items_excluding(
            "strata_manager",
            {"portfolio", "users", "reports", "settings", "capital-funding"},
        ),
    ],
}


async def seed():
    """Generated function header.

    Function: seed
    Path: backend/seeds/navigation_configs.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]

    now = datetime.now(timezone.utc).isoformat()

    # 1. Remove legacy role configs that must not be served.
    cleanup = await db.navigation_configs.delete_many({"role": {"$in": list(LEGACY_NAV_ROLES)}})
    if cleanup.deleted_count:
        print(f"  removed legacy nav configs: {cleanup.deleted_count}")

    # 2. Seed navigation configs (upsert by role). Do not depend on live user
    # rows; new roles must have navigation before their first user is created.
    seeded = 0
    for role, config in NAV_CONFIGS.items():
        result = await db.navigation_configs.update_one(
            {"role": role},
            {
                "$set": {
                    "id": f"nav-{role}",
                    "role": role,
                    "version": 1,
                    "simple_items": config["simple_items"],
                    "advanced_items": config["advanced_items"],
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now}
            },
            upsert=True
        )
        action = "upserted" if result.upserted_id else "updated"
        print(f"  {action}: nav config for role={role}")
        seeded += 1

    # 3. Seed feature toggles (check existence first — never overwrite)
    toggle_count_before = await db.feature_toggles.count_documents({})
    existing_keys = set(await db.feature_toggles.distinct("feature_key"))
    for toggle in NEW_FEATURE_TOGGLES:
        if toggle["feature_key"] not in existing_keys:
            await db.feature_toggles.insert_one({
                **toggle, "created_at": now, "updated_at": now, "is_test_data": False
            })
            print(f"  inserted toggle: {toggle['feature_key']}")
        else:
            print(f"  skip toggle (exists): {toggle['feature_key']}")

    toggle_count_after = await db.feature_toggles.count_documents({})
    print(f"\nSummary:")
    print(f"  Navigation configs seeded: {seeded}")
    print(f"  Feature toggles: {toggle_count_before} → {toggle_count_after}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(seed())
