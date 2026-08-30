# Multi-Building Guide

This platform supports multiple strata buildings in one application. Each building is treated as its own tenant and is
identified by `building_id`.

## What this means for residents and staff

- You only see data for the building you are signed into.
- Shared features such as finance, notices, maintenance, documents, and requests are available through the same app
  experience across buildings.
- Building-specific details such as logos, contact details, levy timing, GST settings, and enabled features come from
  that building's Site Settings and feature configuration.

## Switching building context

If your account belongs to more than one building, use the building selector shown during sign-in or when the app
prompts for context. After a building is selected:

1. the app stores the active building context
2. API calls are scoped to that building
3. dashboards, branding, and permissions refresh for that tenant

## Finance note

Owner-facing levy amounts are calculated from the building's Administrative Fund and Sinking Fund values plus the GST
settings configured for that building. This means two buildings can share the same feature set while showing different
billing labels or levy totals.

## Administration note

When onboarding a new building, the preferred approach is:

1. create the building record
2. configure Site Settings
3. seed or import tenant-safe data
4. enable or override feature toggles as needed

The goal is one shared platform with strict building isolation, not separate codebases per property.
