# Feature Toggle Dependency Intelligence

**Audience:** Super Administrators  
**Updated:** 2026-03-31

---

## Overview

The Feature Toggles admin page now understands **dependency relationships** between features. When a "parent" feature is
disabled, all features that depend on it will be visually marked as **Blocked** and their toggle switches will be greyed
out.

---

## Understanding Feature Dependencies

Every feature in the system may declare one or more **parent features** it requires. For example:

- **Levy Fairness Engine** requires `Finance Management`
- **Levy Fairness — Owner View** requires `Levy Fairness Engine`
- **Work Orders** requires `Maintenance Requests`
- **Private Messages** requires `Community Chat`

If you disable a parent, all its children cannot function even if they remain "enabled" in the database — the UI will
reflect this with an **amber Blocked badge**.

---

## Visual Indicators

| Indicator                             | Meaning                                                       |
|---------------------------------------|---------------------------------------------------------------|
| Green left border + **Active** badge  | Feature is enabled and all parents are enabled                |
| Grey left border + **Inactive** badge | Feature has been manually disabled                            |
| Amber left border + **Blocked** badge | Feature is enabled but a parent is disabled                   |
| `Link2` icon + "N dependents" badge   | This feature has N child features that depend on it           |
| "requires N parents" badge (dashed)   | This feature has parent dependencies, all currently satisfied |

---

## Disabling a Parent Feature

When you toggle OFF a feature that has **enabled child features**, a confirmation dialog will appear:

1. **"Disable only [Feature Name]"** — Turns off just this feature. Child features stay enabled in the DB but will be
   shown as Blocked in the UI. Re-enabling the parent will automatically restore them.

2. **"Disable all (N)"** — Turns off this feature AND all its currently-enabled dependents in one operation. Use this
   for a clean shutdown of an entire feature cluster.

3. **Cancel** — Aborts the operation; no changes made.

---

## Enabling a Blocked Feature

If you try to enable a feature that is **Blocked** (amber), you will see a warning toast:

> "Cannot enable this feature — Enable [Parent Name] first."

You must enable the parent feature before the child can be activated.

---

## Complete Dependency Tree

```
finance (root)
├── approvals
├── council_rates
├── water_bills
├── levy_payments
├── financial_projections
├── collection_rate
├── arrears_recovery
├── spending_categories
├── financial_data_import
├── financial_year_import
├── savings_ledger
├── insurance_claims (also requires: maintenance)
├── finance_intelligence
│   ├── building_financial_stress
│   ├── stress_score
│   ├── investor_dashboard
│   └── insurance_lending_hooks
└── levy_fairness
    ├── levy_fairness_owner
    ├── levy_fairness_explain
    ├── levy_fairness_confidence
    ├── levy_fairness_distribution
    ├── levy_fairness_snapshots
    ├── levy_fairness_audit
    ├── levy_fairness_cross_subsidy
    └── subsidy_map

maintenance (root)
├── work_orders
├── defects
├── insurance_claims (also requires: finance)
└── asset_register
    ├── maintenance_intelligence
    └── digital_twin

email (root)
├── webmail
├── email_preferences
├── manual_email
└── manage_mail_passwords

chat (root)
└── private_messages

announcements (root)
└── notices

news (root)
└── blog_management

user_management (root)
├── owner_name_verification
├── tenant_approvals
├── change_requests
├── owner_transfers
├── tenant_renewals
├── expired_accounts
├── user_permissions
└── user_feature_permissions

owner_units_management (root)
├── strata_roll
└── rental_certificates (also requires: compliance)

meetings (root)
└── proposals

compliance (root)
└── rental_certificates (also requires: owner_units_management)

owner_hub (root)
├── property_health_score
├── true_cost_ownership (also requires: finance)
├── tenant_maintenance_portal (also requires: maintenance)
└── real_estate_agent_portal

strata_market_intelligence (root)
└── building_risk_index
```

---

## Standalone Root Features (no dependencies)

These features operate independently: `documents`, `bookings`, `emergency`, `events`, `marketplace`, `parcels`,
`directory`, `schedule`, `requests`, `pet_register`, `notifications_management`, `audit_logs`, `sidebar`, `user_guide`,
`impersonation`, `rate_limiting`, `external_api`, `notification_cleanup`, `site_settings`, `admin_console`,
`email_settings`, `security_ip_logs`, `scraper_settings`, `building_health_score`, `smart_requests`, `volunteer`

---

## Technical Notes

- **depends_on** is stored in MongoDB alongside each feature toggle record
- The dependency check is performed entirely in the browser using data returned by `GET /feature-toggles/`
- The API's `PUT /feature-toggles/{key}` returns `affected_dependents` when disabling (informational, not auto-cascaded)
- Cascade-disabling is an explicit frontend-driven multi-PUT operation
- Per-building overrides follow the same dependency rules
