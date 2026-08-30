// AUTO-GENERATED FILE. Do not edit manually.
// Source: scripts/generate_interactive_mindmap.py

export type MindmapNodeKind =
  | "root"
  | "domain"
  | "feature"
  | "api"
  | "collection"
  | "ui"
  | "job"
  | "risk";

export type MindmapPanel = {
  summary: string;
  frontend?: string[];
  apis?: string[];
  collections?: string[];
  fields?: string[];
  jobs?: string[];
  docs?: string[];
  issues?: string[];
};

export type MindmapNode = {
  id: string;
  label: string;
  kind: MindmapNodeKind;
  group: string;
  detail: string;
  x: number;
  y: number;
  panel: MindmapPanel;
};

export type MindmapEdge = {
  id: string;
  source: string;
  target: string;
  label?: string;
};

export type MindmapDoc = {
  label: string;
  category: string;
  href: string;
  source: string;
};

export const mindmapDocs: MindmapDoc[] = [
  {
    "label": "00 \u2014 Master Mindmap",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/00_master_mindmap.md",
    "source": "docs/architecture/mindmap/00_master_mindmap.md"
  },
  {
    "label": "01 \u2014 Identity Access",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/01_identity_access.md",
    "source": "docs/architecture/mindmap/01_identity_access.md"
  },
  {
    "label": "02 \u2014 Tenant Foundation",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/02_tenant_foundation.md",
    "source": "docs/architecture/mindmap/02_tenant_foundation.md"
  },
  {
    "label": "03 \u2014 Finance Levies",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/03_finance_levies.md",
    "source": "docs/architecture/mindmap/03_finance_levies.md"
  },
  {
    "label": "03 \u2014 Onboarding Flow",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/03_onboarding_flow.md",
    "source": "docs/architecture/mindmap/03_onboarding_flow.md"
  },
  {
    "label": "04 \u2014 Finance Trust Banking",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/04_finance_trust_banking.md",
    "source": "docs/architecture/mindmap/04_finance_trust_banking.md"
  },
  {
    "label": "05 \u2014 Finance Ap Invoices",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/05_finance_ap_invoices.md",
    "source": "docs/architecture/mindmap/05_finance_ap_invoices.md"
  },
  {
    "label": "06 \u2014 Maintenance Operations",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/06_maintenance_operations.md",
    "source": "docs/architecture/mindmap/06_maintenance_operations.md"
  },
  {
    "label": "07 \u2014 Governance Decisions",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/07_governance_decisions.md",
    "source": "docs/architecture/mindmap/07_governance_decisions.md"
  },
  {
    "label": "08 \u2014 Compliance Risk Insurance",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/08_compliance_risk_insurance.md",
    "source": "docs/architecture/mindmap/08_compliance_risk_insurance.md"
  },
  {
    "label": "09 \u2014 Community Communication",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/09_community_communication.md",
    "source": "docs/architecture/mindmap/09_community_communication.md"
  },
  {
    "label": "10 \u2014 Documents Records",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/10_documents_records.md",
    "source": "docs/architecture/mindmap/10_documents_records.md"
  },
  {
    "label": "11 \u2014 Intelligence Integrations",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/11_intelligence_integrations.md",
    "source": "docs/architecture/mindmap/11_intelligence_integrations.md"
  },
  {
    "label": "12 \u2014 Postgresql Financial Core",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/12_postgresql_financial_core.md",
    "source": "docs/architecture/mindmap/12_postgresql_financial_core.md"
  },
  {
    "label": "13 \u2014 System Architecture Overview",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/13_system_architecture_overview.md",
    "source": "docs/architecture/mindmap/13_system_architecture_overview.md"
  },
  {
    "label": "14 \u2014 Financial Flow Levy Lifecycle",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/14_financial_flow_levy_lifecycle.md",
    "source": "docs/architecture/mindmap/14_financial_flow_levy_lifecycle.md"
  },
  {
    "label": "15 \u2014 Roles Permissions Features",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/15_roles_permissions_features.md",
    "source": "docs/architecture/mindmap/15_roles_permissions_features.md"
  },
  {
    "label": "16 \u2014 Multi Tenant Scoping",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/16_multi_tenant_scoping.md",
    "source": "docs/architecture/mindmap/16_multi_tenant_scoping.md"
  },
  {
    "label": "17 \u2014 Phase F Prime Cutover",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/17_phase_f_prime_cutover.md",
    "source": "docs/architecture/mindmap/17_phase_f_prime_cutover.md"
  },
  {
    "label": "18 \u2014 Management Hierarchy",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/18_management_hierarchy.md",
    "source": "docs/architecture/mindmap/18_management_hierarchy.md"
  },
  {
    "label": "19 \u2014 Bi Analytics Phase2",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/19_bi_analytics_phase2.md",
    "source": "docs/architecture/mindmap/19_bi_analytics_phase2.md"
  },
  {
    "label": "98 \u2014 Background Jobs",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/98_background_jobs.md",
    "source": "docs/architecture/mindmap/98_background_jobs.md"
  },
  {
    "label": "99 \u2014 Cross Reference",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/99_cross_reference.md",
    "source": "docs/architecture/mindmap/99_cross_reference.md"
  },
  {
    "label": "Mindmap README",
    "category": "Mindmap Domains",
    "href": "/tech-docs/mindmap/README.md",
    "source": "docs/architecture/mindmap/README.md"
  },
  {
    "label": "Audit Addendum 2026 04 30",
    "category": "Modules",
    "href": "/tech-docs/AUDIT_ADDENDUM_2026-04-30.html",
    "source": "frontend/public/tech-docs/AUDIT_ADDENDUM_2026-04-30.html"
  },
  {
    "label": "Audit Addendum 2026 05 07 Roles",
    "category": "Modules",
    "href": "/tech-docs/AUDIT_ADDENDUM_2026-05-07_ROLES.html",
    "source": "frontend/public/tech-docs/AUDIT_ADDENDUM_2026-05-07_ROLES.html"
  },
  {
    "label": "Ap Pipeline",
    "category": "Modules",
    "href": "/tech-docs/ap-pipeline.html",
    "source": "frontend/public/tech-docs/ap-pipeline.html"
  },
  {
    "label": "Api Authentication Multitenant",
    "category": "Modules",
    "href": "/tech-docs/api-authentication-multitenant.html",
    "source": "frontend/public/tech-docs/api-authentication-multitenant.html"
  },
  {
    "label": "Api Reference",
    "category": "API & Reference",
    "href": "/tech-docs/api-reference.md",
    "source": "frontend/public/tech-docs/api-reference.md"
  },
  {
    "label": "Api",
    "category": "API & Reference",
    "href": "/tech-docs/api.html",
    "source": "frontend/public/tech-docs/api.html"
  },
  {
    "label": "Architecture",
    "category": "Architecture",
    "href": "/tech-docs/architecture.html",
    "source": "frontend/public/tech-docs/architecture.html"
  },
  {
    "label": "Auth",
    "category": "Security & Access",
    "href": "/tech-docs/auth.html",
    "source": "frontend/public/tech-docs/auth.html"
  },
  {
    "label": "Backend",
    "category": "Architecture",
    "href": "/tech-docs/backend.html",
    "source": "frontend/public/tech-docs/backend.html"
  },
  {
    "label": "Bank Feeds Demo Bank",
    "category": "Modules",
    "href": "/tech-docs/bank-feeds-demo-bank.html",
    "source": "frontend/public/tech-docs/bank-feeds-demo-bank.html"
  },
  {
    "label": "Bi Analytics Building Api",
    "category": "Modules",
    "href": "/tech-docs/bi-analytics-building-api.html",
    "source": "frontend/public/tech-docs/bi-analytics-building-api.html"
  },
  {
    "label": "Bi Analytics Overview",
    "category": "Modules",
    "href": "/tech-docs/bi-analytics-overview.html",
    "source": "frontend/public/tech-docs/bi-analytics-overview.html"
  },
  {
    "label": "Bi Analytics Owner Api",
    "category": "Modules",
    "href": "/tech-docs/bi-analytics-owner-api.html",
    "source": "frontend/public/tech-docs/bi-analytics-owner-api.html"
  },
  {
    "label": "Bi Analytics Portfolio Api",
    "category": "Modules",
    "href": "/tech-docs/bi-analytics-portfolio-api.html",
    "source": "frontend/public/tech-docs/bi-analytics-portfolio-api.html"
  },
  {
    "label": "Capital Funding Special Levies",
    "category": "Modules",
    "href": "/tech-docs/capital-funding-special-levies.html",
    "source": "frontend/public/tech-docs/capital-funding-special-levies.html"
  },
  {
    "label": "Compliance Modules",
    "category": "Modules",
    "href": "/tech-docs/compliance-modules.html",
    "source": "frontend/public/tech-docs/compliance-modules.html"
  },
  {
    "label": "Compliance",
    "category": "Modules",
    "href": "/tech-docs/compliance.html",
    "source": "frontend/public/tech-docs/compliance.html"
  },
  {
    "label": "Council Rates Per Building",
    "category": "Modules",
    "href": "/tech-docs/council-rates-per-building.html",
    "source": "frontend/public/tech-docs/council-rates-per-building.html"
  },
  {
    "label": "Dashboard V2 Extras Api",
    "category": "Modules",
    "href": "/tech-docs/dashboard-v2-extras-api.html",
    "source": "frontend/public/tech-docs/dashboard-v2-extras-api.html"
  },
  {
    "label": "Database",
    "category": "Architecture",
    "href": "/tech-docs/database.html",
    "source": "frontend/public/tech-docs/database.html"
  },
  {
    "label": "Demo Customer",
    "category": "Modules",
    "href": "/tech-docs/demo-customer.html",
    "source": "frontend/public/tech-docs/demo-customer.html"
  },
  {
    "label": "Deployment",
    "category": "Architecture",
    "href": "/tech-docs/deployment.html",
    "source": "frontend/public/tech-docs/deployment.html"
  },
  {
    "label": "Deployment",
    "category": "Architecture",
    "href": "/tech-docs/deployment.md",
    "source": "frontend/public/tech-docs/deployment.md"
  },
  {
    "label": "Document Converter",
    "category": "Modules",
    "href": "/tech-docs/document-converter.html",
    "source": "frontend/public/tech-docs/document-converter.html"
  },
  {
    "label": "Document Ocr",
    "category": "Modules",
    "href": "/tech-docs/document-ocr.html",
    "source": "frontend/public/tech-docs/document-ocr.html"
  },
  {
    "label": "East Gate Import",
    "category": "Modules",
    "href": "/tech-docs/east-gate-import.html",
    "source": "frontend/public/tech-docs/east-gate-import.html"
  },
  {
    "label": "External Api",
    "category": "API & Reference",
    "href": "/tech-docs/external-api.html",
    "source": "frontend/public/tech-docs/external-api.html"
  },
  {
    "label": "Feature Toggles",
    "category": "Security & Access",
    "href": "/tech-docs/feature-toggles.html",
    "source": "frontend/public/tech-docs/feature-toggles.html"
  },
  {
    "label": "File Upload Security",
    "category": "Security & Access",
    "href": "/tech-docs/file-upload-security.html",
    "source": "frontend/public/tech-docs/file-upload-security.html"
  },
  {
    "label": "Finance Api Apr2026",
    "category": "API & Reference",
    "href": "/tech-docs/finance-api-apr2026.html",
    "source": "frontend/public/tech-docs/finance-api-apr2026.html"
  },
  {
    "label": "Finance Extensions",
    "category": "Modules",
    "href": "/tech-docs/finance-extensions.html",
    "source": "frontend/public/tech-docs/finance-extensions.html"
  },
  {
    "label": "Financial Data Consolidation Map",
    "category": "Modules",
    "href": "/tech-docs/financial-data-consolidation-map.md",
    "source": "frontend/public/tech-docs/financial-data-consolidation-map.md"
  },
  {
    "label": "Frontend",
    "category": "Architecture",
    "href": "/tech-docs/frontend.html",
    "source": "frontend/public/tech-docs/frontend.html"
  },
  {
    "label": "Genesis Posting Outbox Relay",
    "category": "Modules",
    "href": "/tech-docs/genesis_posting_outbox_relay.md",
    "source": "frontend/public/tech-docs/genesis_posting_outbox_relay.md"
  },
  {
    "label": "Historical Reconstruction",
    "category": "Modules",
    "href": "/tech-docs/historical-reconstruction.html",
    "source": "frontend/public/tech-docs/historical-reconstruction.html"
  },
  {
    "label": "Identity Ownership Data Consolidation Map",
    "category": "Modules",
    "href": "/tech-docs/identity-ownership-data-consolidation-map.md",
    "source": "frontend/public/tech-docs/identity-ownership-data-consolidation-map.md"
  },
  {
    "label": "Intelligence Module",
    "category": "Modules",
    "href": "/tech-docs/intelligence-module.html",
    "source": "frontend/public/tech-docs/intelligence-module.html"
  },
  {
    "label": "Invoices Ocr",
    "category": "Modules",
    "href": "/tech-docs/invoices-ocr.html",
    "source": "frontend/public/tech-docs/invoices-ocr.html"
  },
  {
    "label": "Jurisdictional Rule Engine",
    "category": "Operations",
    "href": "/tech-docs/jurisdictional-rule-engine.html",
    "source": "frontend/public/tech-docs/jurisdictional-rule-engine.html"
  },
  {
    "label": "Levy Fairness",
    "category": "Modules",
    "href": "/tech-docs/levy-fairness.html",
    "source": "frontend/public/tech-docs/levy-fairness.html"
  },
  {
    "label": "Maintenance Pdf Generation",
    "category": "Modules",
    "href": "/tech-docs/maintenance-pdf-generation.html",
    "source": "frontend/public/tech-docs/maintenance-pdf-generation.html"
  },
  {
    "label": "Management Hierarchy Api",
    "category": "Modules",
    "href": "/tech-docs/management-hierarchy-api.html",
    "source": "frontend/public/tech-docs/management-hierarchy-api.html"
  },
  {
    "label": "Management Hierarchy",
    "category": "Modules",
    "href": "/tech-docs/management-hierarchy.html",
    "source": "frontend/public/tech-docs/management-hierarchy.html"
  },
  {
    "label": "Manager Contracts Api",
    "category": "Modules",
    "href": "/tech-docs/manager-contracts-api.html",
    "source": "frontend/public/tech-docs/manager-contracts-api.html"
  },
  {
    "label": "Matching Engine",
    "category": "Modules",
    "href": "/tech-docs/matching-engine.html",
    "source": "frontend/public/tech-docs/matching-engine.html"
  },
  {
    "label": "Multi Unit Ownership",
    "category": "Modules",
    "href": "/tech-docs/multi-unit-ownership.html",
    "source": "frontend/public/tech-docs/multi-unit-ownership.html"
  },
  {
    "label": "Occupancy Bi Data Consolidation Map",
    "category": "Modules",
    "href": "/tech-docs/occupancy-bi-data-consolidation-map.md",
    "source": "frontend/public/tech-docs/occupancy-bi-data-consolidation-map.md"
  },
  {
    "label": "Onboarding Financial Pipeline",
    "category": "Modules",
    "href": "/tech-docs/onboarding-financial-pipeline.html",
    "source": "frontend/public/tech-docs/onboarding-financial-pipeline.html"
  },
  {
    "label": "Onboarding Workflow",
    "category": "Modules",
    "href": "/tech-docs/onboarding-workflow.html",
    "source": "frontend/public/tech-docs/onboarding-workflow.html"
  },
  {
    "label": "Owner Finance Api",
    "category": "API & Reference",
    "href": "/tech-docs/owner_finance_api.html",
    "source": "frontend/public/tech-docs/owner_finance_api.html"
  },
  {
    "label": "Permissions",
    "category": "Security & Access",
    "href": "/tech-docs/permissions.md",
    "source": "frontend/public/tech-docs/permissions.md"
  },
  {
    "label": "Phase1 Completion Checklist",
    "category": "Operations",
    "href": "/tech-docs/phase1-completion-checklist.md",
    "source": "frontend/public/tech-docs/phase1-completion-checklist.md"
  },
  {
    "label": "Phase2 Api Corrections",
    "category": "Operations",
    "href": "/tech-docs/phase2-api-corrections.md",
    "source": "frontend/public/tech-docs/phase2-api-corrections.md"
  },
  {
    "label": "Phase F Prime Financial Core",
    "category": "Modules",
    "href": "/tech-docs/phase_f_prime_financial_core.md",
    "source": "frontend/public/tech-docs/phase_f_prime_financial_core.md"
  },
  {
    "label": "Portfolio Api",
    "category": "API & Reference",
    "href": "/tech-docs/portfolio_api.html",
    "source": "frontend/public/tech-docs/portfolio_api.html"
  },
  {
    "label": "Postgresql Cutover Schema Walkthrough 2026 06 01",
    "category": "Modules",
    "href": "/tech-docs/postgresql-cutover-schema-walkthrough-2026-06-01.md",
    "source": "frontend/public/tech-docs/postgresql-cutover-schema-walkthrough-2026-06-01.md"
  },
  {
    "label": "Postgresql Financial Core",
    "category": "Modules",
    "href": "/tech-docs/postgresql-financial-core.html",
    "source": "frontend/public/tech-docs/postgresql-financial-core.html"
  },
  {
    "label": "Postgresql Live Data Inventory 2026 07 24",
    "category": "Modules",
    "href": "/tech-docs/postgresql-live-data-inventory-2026-07-24.md",
    "source": "frontend/public/tech-docs/postgresql-live-data-inventory-2026-07-24.md"
  },
  {
    "label": "Postgresql Schema And Drift Map",
    "category": "Modules",
    "href": "/tech-docs/postgresql-schema-and-drift-map.md",
    "source": "frontend/public/tech-docs/postgresql-schema-and-drift-map.md"
  },
  {
    "label": "Powerhouse Coexistence",
    "category": "Modules",
    "href": "/tech-docs/powerhouse-coexistence.md",
    "source": "frontend/public/tech-docs/powerhouse-coexistence.md"
  },
  {
    "label": "Ppm Module",
    "category": "Modules",
    "href": "/tech-docs/ppm-module.html",
    "source": "frontend/public/tech-docs/ppm-module.html"
  },
  {
    "label": "Request Catalogue",
    "category": "Modules",
    "href": "/tech-docs/request-catalogue.html",
    "source": "frontend/public/tech-docs/request-catalogue.html"
  },
  {
    "label": "Scheduler Architecture",
    "category": "Architecture",
    "href": "/tech-docs/scheduler-architecture.html",
    "source": "frontend/public/tech-docs/scheduler-architecture.html"
  },
  {
    "label": "Schema Reference",
    "category": "Architecture",
    "href": "/tech-docs/schema-reference.md",
    "source": "frontend/public/tech-docs/schema-reference.md"
  },
  {
    "label": "Security Hardening",
    "category": "Security & Access",
    "href": "/tech-docs/security-hardening.html",
    "source": "frontend/public/tech-docs/security-hardening.html"
  },
  {
    "label": "Seed Reference",
    "category": "Operations",
    "href": "/tech-docs/seed-reference.html",
    "source": "frontend/public/tech-docs/seed-reference.html"
  },
  {
    "label": "Sm Organisations Api",
    "category": "Modules",
    "href": "/tech-docs/sm-organisations-api.html",
    "source": "frontend/public/tech-docs/sm-organisations-api.html"
  },
  {
    "label": "Smart Requests Workflow",
    "category": "Modules",
    "href": "/tech-docs/smart_requests_workflow.html",
    "source": "frontend/public/tech-docs/smart_requests_workflow.html"
  },
  {
    "label": "Sse",
    "category": "Operations",
    "href": "/tech-docs/sse.html",
    "source": "frontend/public/tech-docs/sse.html"
  },
  {
    "label": "Strata Sync",
    "category": "Operations",
    "href": "/tech-docs/strata-sync.html",
    "source": "frontend/public/tech-docs/strata-sync.html"
  },
  {
    "label": "Swagger",
    "category": "Modules",
    "href": "/tech-docs/swagger.html",
    "source": "frontend/public/tech-docs/swagger.html"
  },
  {
    "label": "Testing",
    "category": "Operations",
    "href": "/tech-docs/testing.html",
    "source": "frontend/public/tech-docs/testing.html"
  },
  {
    "label": "Totp 2Fa",
    "category": "Security & Access",
    "href": "/tech-docs/totp-2fa.html",
    "source": "frontend/public/tech-docs/totp-2fa.html"
  },
  {
    "label": "Trust Accounting",
    "category": "Modules",
    "href": "/tech-docs/trust-accounting.html",
    "source": "frontend/public/tech-docs/trust-accounting.html"
  },
  {
    "label": "Trust Accounting",
    "category": "Modules",
    "href": "/tech-docs/trust-accounting.md",
    "source": "frontend/public/tech-docs/trust-accounting.md"
  },
  {
    "label": "Trust Dual Approval",
    "category": "Modules",
    "href": "/tech-docs/trust_dual_approval.html",
    "source": "frontend/public/tech-docs/trust_dual_approval.html"
  },
  {
    "label": "Unit Numbering Owner Finance",
    "category": "Modules",
    "href": "/tech-docs/unit-numbering-owner-finance.html",
    "source": "frontend/public/tech-docs/unit-numbering-owner-finance.html"
  },
  {
    "label": "Work Orders Maintenance Data Consolidation Map",
    "category": "Modules",
    "href": "/tech-docs/work-orders-maintenance-data-consolidation-map.md",
    "source": "frontend/public/tech-docs/work-orders-maintenance-data-consolidation-map.md"
  },
  {
    "label": "Workflow Governance Api",
    "category": "API & Reference",
    "href": "/tech-docs/workflow_governance_api.html",
    "source": "frontend/public/tech-docs/workflow_governance_api.html"
  },
  {
    "label": "Workflows",
    "category": "Modules",
    "href": "/tech-docs/workflows.html",
    "source": "frontend/public/tech-docs/workflows.html"
  }
];

export const interactiveMindmapNodes: MindmapNode[] = [
  {
    "id": "root",
    "label": "StrataOS Platform",
    "kind": "root",
    "group": "Platform",
    "detail": "Auto-generated architecture graph from backend routers, frontend API usage, mindmap docs, cron/workers, and 170 Mongo collections.",
    "x": 0,
    "y": 0,
    "panel": {
      "summary": "Regenerate with: npm run mindmap:generate from frontend or python scripts/generate_interactive_mindmap.py from repo root.",
      "docs": [
        "docs/architecture/mindmap/README.md",
        "docs/architecture/mindmap/00_master_mindmap.md",
        "docs/architecture/database_schema_full_2026.json",
        "docs/architecture/strata_management_master_architecture.md"
      ]
    }
  },
  {
    "id": "db-schema",
    "label": "Mongo Schema Snapshot",
    "kind": "collection",
    "group": "Data",
    "detail": "170 collections loaded from schema JSON or live Mongo.",
    "x": 0,
    "y": 260,
    "panel": {
      "summary": "Use live Mongo by setting MONGO_URI and MONGO_DB before running the generator; otherwise the checked-in schema JSON is used.",
      "docs": [
        "docs/architecture/database_schema_full_2026.json"
      ]
    }
  },
  {
    "id": "background-jobs",
    "label": "Cron & Workers",
    "kind": "job",
    "group": "Ops",
    "detail": "Recurring cron jobs, worker loops and async queues discovered under backend/cron and backend/workers.",
    "x": 340,
    "y": 260,
    "panel": {
      "summary": "Cross-cutting execution layer. Individual jobs are listed on domain nodes when classified.",
      "docs": [
        "docs/architecture/mindmap/98_background_jobs.md",
        "deploy/install.sh"
      ]
    }
  },
  {
    "id": "community_communication",
    "label": "Domain 09 \u2014 Community & Communication",
    "kind": "domain",
    "group": "Experience",
    "detail": "Generated from docs/architecture/mindmap/09_community_communication.md.",
    "x": 0,
    "y": -620,
    "panel": {
      "summary": "156 APIs \u00b7 130 collections \u00b7 3 frontend files \u00b7 3 jobs.",
      "frontend": [
        "frontend/src/components/dashboard/MorningCard.tsx",
        "frontend/src/app/(app)/intelligence/market/page.tsx",
        "frontend/src/app/(public)/marketplace/[listingId]/page.tsx"
      ],
      "apis": [
        "POST /defects",
        "GET /defects",
        "PUT /defects/{defect_id}/status",
        "POST /move-bookings",
        "GET /move-bookings",
        "PUT /move-bookings/{booking_id}/status",
        "DELETE /move-bookings/{booking_id}",
        "POST /amenity-bookings",
        "GET /amenity-bookings",
        "DELETE /amenity-bookings/{booking_id}",
        "GET /amenities",
        "POST /amenities",
        "DELETE /amenities/{amenity_key}",
        "POST /parcels",
        "GET /parcels",
        "PUT /parcels/{parcel_id}/collected",
        "GET /parcels/{parcel_id}/track",
        "POST /chat-groups"
      ],
      "collections": [
        "email_sent_log",
        "push_sent_log",
        "ChatGroupCreate",
        "ChatGroupUpdate",
        "ChatMessageCreate",
        "chat_groups",
        "group_messages",
        "memberships",
        "users",
        "buildings",
        "_id",
        "id",
        "name",
        "is_archived",
        "member_count",
        "last_message_at",
        "unread_count",
        "conversations"
      ],
      "fields": [
        "amenity_bookings._id",
        "amenity_bookings.amenity_type",
        "amenity_bookings.booked_by",
        "amenity_bookings.booked_by_name",
        "amenity_bookings.building_id",
        "amenity_bookings.created_at",
        "amenity_bookings.date",
        "amenity_bookings.end_time",
        "announcements._id",
        "announcements.building_id",
        "announcements.content",
        "announcements.created_at",
        "announcements.created_by",
        "announcements.created_by_name",
        "announcements.expires_at",
        "announcements.history",
        "chat_groups._id",
        "chat_groups.building_id"
      ],
      "jobs": [
        "backend/cron/cron_notification_cleanup.py",
        "backend/cron/cron_notification_digest.py",
        "backend/workers/notification_worker.py"
      ],
      "docs": [
        "docs/architecture/mindmap/09_community_communication.md"
      ],
      "issues": []
    }
  },
  {
    "id": "compliance_risk_insurance",
    "label": "Domain 08 \u2014 Compliance, Risk & Insurance",
    "kind": "domain",
    "group": "Risk",
    "detail": "Generated from docs/architecture/mindmap/08_compliance_risk_insurance.md.",
    "x": 335,
    "y": -521,
    "panel": {
      "summary": "84 APIs \u00b7 91 collections \u00b7 2 frontend files \u00b7 2 jobs.",
      "frontend": [
        "frontend/src/pages/dashboard/SafetyFeedPage.tsx",
        "frontend/src/pages/dashboard/AgencyBIAnalyticsPage.tsx"
      ],
      "apis": [
        "POST /compliance/registers",
        "GET /compliance/registers",
        "GET /compliance/registers/{register_id}",
        "POST /compliance/registers/{register_id}/items",
        "PUT /compliance/registers/{register_id}/items/{item_id}",
        "GET /compliance/events",
        "GET /compliance/dashboard",
        "GET /essential-services/overdue",
        "GET /essential-services/{entry_id}",
        "PUT /essential-services/{entry_id}",
        "POST /insurance/policies",
        "GET /insurance/policies",
        "GET /insurance/policies/{policy_id}",
        "PUT /insurance/policies/{policy_id}",
        "DELETE /insurance/policies/{policy_id}",
        "GET /insurance/compliance",
        "GET /insurance/agm-disclosure",
        "GET /insurance/expiring"
      ],
      "collections": [
        "document_hash",
        "compliance_registers",
        "rental_certificates",
        "super_admin",
        "strata_admin",
        "strata_manager",
        "compliance_register_items",
        "compliance_events",
        "compliance_items",
        "_id",
        "id",
        "category",
        "requirement",
        "frequency",
        "next_due_at",
        "last_completed_at",
        "responsible_role",
        "status"
      ],
      "fields": [
        "capital_shock_risks._id",
        "capital_shock_risks.annual_expenses",
        "capital_shock_risks.building_id",
        "capital_shock_risks.capital_events",
        "capital_shock_risks.capital_shock_index",
        "capital_shock_risks.computed_at",
        "capital_shock_risks.created_at",
        "capital_shock_risks.five_year_capital_forecast",
        "compliance_items._id",
        "compliance_items.assigned_to",
        "compliance_items.building_id",
        "compliance_items.category",
        "compliance_items.completed_date",
        "compliance_items.created_at",
        "compliance_items.created_by",
        "compliance_items.description",
        "essential_services_log._id",
        "essential_services_log.building_id"
      ],
      "jobs": [
        "backend/cron/cron_insurance.py",
        "backend/cron/cron_nsw_compliance.py"
      ],
      "docs": [
        "docs/architecture/mindmap/08_compliance_risk_insurance.md"
      ],
      "issues": []
    }
  },
  {
    "id": "documents_records",
    "label": "Domain 10 \u2014 Documents & Records",
    "kind": "domain",
    "group": "Records",
    "detail": "Generated from docs/architecture/mindmap/10_documents_records.md.",
    "x": 563,
    "y": -257,
    "panel": {
      "summary": "42 APIs \u00b7 75 collections \u00b7 3 frontend files \u00b7 1 jobs.",
      "frontend": [
        "frontend/src/components/documents/PDFPreviewDialog.tsx",
        "frontend/src/components/documents/PDFAnnotatorDialog.tsx",
        "frontend/src/pages/dashboard/DocumentsPage.tsx"
      ],
      "apis": [
        "GET /documents/{doc_id}/annotations",
        "POST /documents/{doc_id}/annotations",
        "PUT /documents/{doc_id}/annotations/{ann_id}",
        "DELETE /documents/{doc_id}/annotations/{ann_id}",
        "POST /document-converter/convert",
        "POST /document-converter/convert-url",
        "GET /document-converter/history",
        "GET /document-converter/supported-formats",
        "GET /document-requests/overdue",
        "GET /document-requests/{request_id}",
        "POST /document-requests/{request_id}/fulfil",
        "POST /document-requests/{request_id}/deny",
        "POST /document-requests/{request_id}/cancel",
        "POST /document-requests/auto-fulfil",
        "POST /documents",
        "GET /documents",
        "GET /documents/{doc_id}",
        "DELETE /documents/{doc_id}"
      ],
      "collections": [
        "content_hash",
        "retention_class",
        "documents",
        "document_folders",
        "occupancy_status",
        "occupancy_snapshots",
        "can_upload_documents",
        "can_manage_document_permissions",
        "DocumentCreate",
        "DocumentMove",
        "DocumentRename",
        "DocumentPermission",
        "FolderCreate",
        "FolderMove",
        "_id",
        "id",
        "filename",
        "mime_type"
      ],
      "fields": [
        "document_folders._id",
        "document_folders.building_id",
        "document_folders.color",
        "document_folders.created_at",
        "document_folders.created_by",
        "document_folders.created_by_name",
        "document_folders.description",
        "document_folders.id",
        "documents._id",
        "documents.building_id",
        "documents.category",
        "documents.content_type",
        "documents.created_at",
        "documents.expires_at",
        "documents.id",
        "documents.is_private",
        "occupancy_snapshots._id",
        "occupancy_snapshots.building_id"
      ],
      "jobs": [
        "backend/cron/cron_occupancy_recompute.py"
      ],
      "docs": [
        "docs/architecture/mindmap/10_documents_records.md"
      ],
      "issues": []
    }
  },
  {
    "id": "finance_ap_invoices",
    "label": "Domain 05 \u2014 Finance: Accounts Payable & Invoices",
    "kind": "domain",
    "group": "Finance",
    "detail": "Generated from docs/architecture/mindmap/05_finance_ap_invoices.md.",
    "x": 613,
    "y": 88,
    "panel": {
      "summary": "27 APIs \u00b7 31 collections \u00b7 2 frontend files \u00b7 1 jobs.",
      "frontend": [
        "frontend/src/pages/supplier/InvoiceUploadPage.tsx",
        "frontend/src/pages/dashboard/financial/APApprovalQueuePage.tsx"
      ],
      "apis": [
        "GET /ap/invoices",
        "GET /ap/invoices/{invoice_id}",
        "POST /ap/invoices/{invoice_id}/submit",
        "POST /ap/invoices/{invoice_id}/validate",
        "POST /ap/invoices/{invoice_id}/approve",
        "POST /ap/invoices/{invoice_id}/reject",
        "POST /ap/invoices/{invoice_id}/schedule",
        "POST /ap/supplier-upload/",
        "POST /invoices/upload-and-parse",
        "GET /invoices",
        "GET /invoices/{invoice_id}",
        "POST /invoices/{invoice_id}/confirm",
        "DELETE /invoices/{invoice_id}",
        "GET /invoices/{invoice_id}/receipt",
        "GET /settings/ocr-provider",
        "PATCH /settings/ocr-provider",
        "GET /admin/settings/global-ocr",
        "PATCH /admin/settings/global-ocr"
      ],
      "collections": [
        "invoice_documents",
        "FinancialCoreService",
        "can_submit_supplier_invoice",
        "super_admin",
        "strata_admin",
        "strata_manager",
        "ec_member",
        "require_feature",
        "financial_anomalies",
        "financial_forecasts",
        "lot_financial_summary",
        "sinking_fund_plan",
        "sinking_fund_capital_events",
        "recurring_bill_templates",
        "financial_documents",
        "auto",
        "invoice_ocr",
        "financial_integration_layer_v2"
      ],
      "fields": [
        "invoices._id",
        "invoices.amount",
        "invoices.asset_id",
        "invoices.building_id",
        "invoices.created_at",
        "invoices.facility_id",
        "invoices.id",
        "invoices.payment_status",
        "registration_approval_tokens._id",
        "registration_approval_tokens.action",
        "registration_approval_tokens.created_at",
        "registration_approval_tokens.created_for",
        "registration_approval_tokens.expires_at",
        "registration_approval_tokens.token",
        "registration_approval_tokens.used",
        "registration_approval_tokens.used_at",
        "subsidy_map_cache._id",
        "subsidy_map_cache.building_id"
      ],
      "jobs": [
        "backend/cron/cron_approval_escalation.py"
      ],
      "docs": [
        "docs/architecture/mindmap/05_finance_ap_invoices.md"
      ],
      "issues": []
    }
  },
  {
    "id": "finance_levies",
    "label": "Domain 03 \u2014 Finance: Levies & Owner Billing",
    "kind": "domain",
    "group": "Finance",
    "detail": "Generated from docs/architecture/mindmap/03_finance_levies.md.",
    "x": 468,
    "y": 406,
    "panel": {
      "summary": "223 APIs \u00b7 131 collections \u00b7 13 frontend files \u00b7 4 jobs.",
      "frontend": [
        "frontend/src/components/finance/LevyKpiDialog.tsx",
        "frontend/src/components/levy/ScenarioSnapshotPanel.tsx",
        "frontend/src/components/levy/FairnessAuditLog.tsx",
        "frontend/src/components/levy/CrossSubsidyTable.tsx",
        "frontend/src/pages/dashboard/LevyPaymentPage.tsx",
        "frontend/src/pages/dashboard/FinancePage.tsx",
        "frontend/src/pages/dashboard/BIAnalyticsPage.tsx",
        "frontend/src/pages/dashboard/intelligence/LevyScenariosPage.tsx",
        "frontend/src/app/(app)/reports/page.tsx",
        "frontend/src/app/(app)/intelligence/building/page.tsx",
        "frontend/src/app/(app)/financials/council-rates/page.tsx",
        "frontend/src/app/(app)/financials/levy-kpi/page.tsx"
      ],
      "apis": [
        "GET /arrears-recovery/summary",
        "GET /arrears-recovery/{unit_number}",
        "POST /arrears-recovery/{unit_number}/action",
        "POST /arrears-recovery/{unit_number}/suppress",
        "POST /arrears-recovery/{unit_number}/resume",
        "POST /arrears-recovery/run-automation",
        "GET /arrears-risk/summary",
        "GET /arrears-risk/{unit_number}",
        "GET /council-rates/settings",
        "PUT /council-rates/settings",
        "GET /council-rates/{unit_number}",
        "POST /council-rates/{unit_number}/payments",
        "GET /council-rates/{unit_number}/payments",
        "GET /years",
        "GET /annual-levies",
        "GET /annual-levies/{year}",
        "PUT /annual-levies/{year}",
        "GET /finance/calculation-registry"
      ],
      "collections": [
        "unit_levy_ledger",
        "annual_levies",
        "levy_payments",
        "levy_categories",
        "LevyReadService",
        "BudgetReadService",
        "ArrearsReadService",
        "OwnerLedgerReadService",
        "FinancialCoreService",
        "ec_member",
        "strata_admin",
        "payment_plans",
        "arrears_recovery_actions",
        "expense_transactions",
        "income_transactions",
        "levy_income",
        "str",
        "UnitFinanceDetailPage"
      ],
      "fields": [
        "council_rates._id",
        "council_rates.auv",
        "council_rates.block_auv",
        "council_rates.building_id",
        "council_rates.cached_at",
        "council_rates.fesl",
        "council_rates.financial_year",
        "council_rates.fixed_charge",
        "finance._id",
        "finance.amount",
        "finance.building_id",
        "finance.category",
        "finance.created_at",
        "finance.created_by",
        "finance.data_source",
        "finance.date",
        "levy_categories._id",
        "levy_categories.actual_amount"
      ],
      "jobs": [
        "backend/cron/cron_arrears_recovery.py",
        "backend/cron/cron_finance_recompute.py",
        "backend/cron/cron_finance_shadow_traffic.py",
        "backend/cron/cron_levy_reminders.py"
      ],
      "docs": [
        "docs/architecture/mindmap/03_finance_levies.md"
      ],
      "issues": [
        "FIN-PG-CUTOVER-08** \u2014 Consolidate mutating finance writes (levy_payments, payment_plans, arrears, expense/income, special_levies, anomalies) into `routers/trust.py`.",
        "FIN-PG-CUTOVER-13** *(new)* \u2014 Remove the duplicate inline finance routes: `arrears_risk.py` and `server.py /arrears/*` legacy paths fold into `arrears_recovery.py`; `finance.py /payment-plans` POST/GET fold into `payment_plans.py`; `finance.py /levy-reminder-settings` and `/levy-reminder-log` fold into `levy_reminders.py`."
      ]
    }
  },
  {
    "id": "finance_trust_banking",
    "label": "Domain 04 \u2014 Finance: Trust, Banking & Reconciliation",
    "kind": "domain",
    "group": "Finance",
    "detail": "Generated from docs/architecture/mindmap/04_finance_trust_banking.md.",
    "x": 174,
    "y": 594,
    "panel": {
      "summary": "116 APIs \u00b7 137 collections \u00b7 7 frontend files \u00b7 1 jobs.",
      "frontend": [
        "frontend/src/components/trust/TrustTransactionLedger.tsx",
        "frontend/src/pages/dashboard/TrustBankAccountsPage.tsx",
        "frontend/src/pages/dashboard/financial/HistoricalReconstructionPage.tsx",
        "frontend/src/pages/dashboard/financial/MatchingReviewPage.tsx",
        "frontend/src/pages/dashboard/financial/ReconciliationPage.tsx",
        "frontend/src/pages/dashboard/admin/DemoBankTransactionsPage.tsx",
        "frontend/src/app/(public)/register/update/page.tsx"
      ],
      "apis": [
        "POST /bank-feeds/ingest",
        "POST /bank-feeds/sync",
        "POST /bank-feeds/rematch",
        "GET /bank-feeds/transactions",
        "GET /bank-feeds/transactions/{tx_id}",
        "PATCH /bank-feeds/transactions/{tx_id}/match",
        "GET /bank-feeds/runs",
        "GET /bank-feeds/strata-years/summary",
        "GET /bank-feeds/strata-years/summary/pg",
        "POST /demo-bank/import/csv",
        "POST /demo-bank/import/strata_web",
        "POST /demo-bank/strata-web/infer-candidates",
        "POST /demo-bank/transactions/manual",
        "GET /demo-bank/accounts",
        "GET /demo-bank/accounts/{account_ref}/balance",
        "GET /demo-bank/accounts/{account_ref}/transactions",
        "GET /demo-bank/import-batches",
        "GET /demo-bank/import-batches/{batch_id}"
      ],
      "collections": [
        "trust_transactions_v2",
        "trust_levy_schedules_v2",
        "levy_payments",
        "unit_levy_ledger",
        "super_admin",
        "strata_manager",
        "strata_admin",
        "ec_member",
        "financial_integration_layer_v2",
        "financial_pg_writes_enabled",
        "financial_shadow_reads_enabled",
        "financial_pg_reads_enabled",
        "external_api_finance_pg_enabled",
        "trust_pg_ledger_enabled",
        "trust_reconciliation_pg_enabled",
        "bank_integration_abstraction_enabled",
        "onboarding_current_balance_adapters_enabled",
        "trust_accounts"
      ],
      "fields": [
        "bank_accounts._id",
        "bank_accounts.account_name",
        "bank_accounts.account_number",
        "bank_accounts.admin_balance",
        "bank_accounts.balance_date",
        "bank_accounts.bsb",
        "bank_accounts.building_id",
        "bank_accounts.created_at",
        "bank_reconciliations._id",
        "bank_reconciliations.account_name",
        "bank_reconciliations.account_number",
        "bank_reconciliations.admin_balance",
        "bank_reconciliations.as_of_date",
        "bank_reconciliations.bsb",
        "bank_reconciliations.building_id",
        "bank_reconciliations.created_at",
        "trust_accounts_v2._id",
        "trust_accounts_v2.account_category"
      ],
      "jobs": [
        "backend/cron/cron_trust_interest.py"
      ],
      "docs": [
        "docs/architecture/mindmap/04_finance_trust_banking.md"
      ],
      "issues": [
        "FIN-PG-CUTOVER-08** \u2014 Consolidate `trust_accounting.py` + `trust_phase1.py` + `trust_reconciliation.py` into a single `trust.py`. Tests and frontend already polymorphic on response shape."
      ]
    }
  },
  {
    "id": "governance_decisions",
    "label": "Domain 07 \u2014 Governance & Decisions",
    "kind": "domain",
    "group": "Governance",
    "detail": "Generated from docs/architecture/mindmap/07_governance_decisions.md.",
    "x": -174,
    "y": 594,
    "panel": {
      "summary": "65 APIs \u00b7 90 collections \u00b7 3 frontend files \u00b7 0 jobs.",
      "frontend": [
        "frontend/src/pages/dashboard/MeetingsPage.tsx",
        "frontend/src/pages/dashboard/ByLawBreachPage.tsx",
        "frontend/src/pages/dashboard/admin/LettersPage.tsx"
      ],
      "apis": [
        "GET /audit/required",
        "POST /audit/records",
        "GET /audit/records",
        "GET /audit/records/{record_id}",
        "PUT /audit/records/{record_id}",
        "GET /audit/agm-checklist",
        "POST /by-law-breach/reports",
        "GET /by-law-breach/reports",
        "GET /by-law-breach/search-help",
        "GET /by-law-breach/reports/{report_id}",
        "POST /by-law-breach/reports/{report_id}/acknowledge",
        "POST /by-law-breach/reports/{report_id}/notice",
        "POST /by-law-breach/reports/{report_id}/status",
        "GET /by-law-breach/reports/{report_id}/export",
        "GET /conflict-of-interest/{declaration_id}",
        "PUT /conflict-of-interest/{declaration_id}",
        "DELETE /conflict-of-interest/{declaration_id}",
        "GET /decisions/search"
      ],
      "collections": [
        "strata_manager",
        "MeetingCreate",
        "AGMCreate",
        "AGMMotionCreate",
        "AGMVoteCreate",
        "TodoCreate",
        "meetings",
        "agm",
        "agm_attendance",
        "agm_motions",
        "agm_votes",
        "todos",
        "users",
        "user_units",
        "_id",
        "id",
        "title",
        "meeting_type"
      ],
      "fields": [
        "agm._id",
        "agm.agenda",
        "agm.building_id",
        "agm.created_at",
        "agm.date",
        "agm.id",
        "agm.location",
        "agm.motions",
        "agm_attendance._id",
        "agm_attendance.agm_id",
        "agm_attendance.building_id",
        "agm_attendance.confirmed_at",
        "agm_attendance.confirmed_by_admin",
        "agm_attendance.proxy_id",
        "agm_attendance.status",
        "agm_attendance.updated_at",
        "audit_logs._id",
        "audit_logs.action"
      ],
      "jobs": [],
      "docs": [
        "docs/architecture/mindmap/07_governance_decisions.md"
      ],
      "issues": []
    }
  },
  {
    "id": "identity_access",
    "label": "Domain 01 \u2014 Identity & Access",
    "kind": "domain",
    "group": "Core",
    "detail": "Generated from docs/architecture/mindmap/01_identity_access.md.",
    "x": -468,
    "y": 406,
    "panel": {
      "summary": "65 APIs \u00b7 64 collections \u00b7 6 frontend files \u00b7 0 jobs.",
      "frontend": [
        "frontend/src/components/dashboard/premium/UtilityComparisonCard.tsx",
        "frontend/src/pages/dashboard/ProfilePage.tsx",
        "frontend/src/app/(app)/profile/passport/page.tsx",
        "frontend/src/app/(public)/reset-password/page.tsx",
        "frontend/src/app/(public)/forgot-password/page.tsx",
        "frontend/src/app/(public)/login/totp-challenge/page.tsx"
      ],
      "apis": [
        "POST /auth/registration-invites",
        "GET /auth/registration-invites/{token}",
        "POST /auth/register",
        "POST /auth/login",
        "GET /auth/me",
        "POST /auth/email-preference",
        "GET /auth/memberships",
        "POST /auth/select-building",
        "POST /auth/impersonate",
        "POST /auth/forgot-password",
        "POST /auth/reset-password",
        "POST /auth/change-password",
        "GET /auth/registration-decision",
        "POST /auth/registration-decision",
        "GET /mail/access",
        "PUT /mail/update-password",
        "PUT /mail/admin-update-password",
        "POST /admin/reset-user-passwords"
      ],
      "collections": [
        "users",
        "memberships",
        "user_units",
        "roles",
        "permissions",
        "user_roles",
        "relationship_tuples",
        "login_audit_logs",
        "password_resets",
        "password_change_audit",
        "registration_approval_tokens",
        "flagged_ips",
        "ec_member",
        "strata_admin",
        "financial_integration_layer_v2",
        "find_user_for_auth",
        "IdentityService",
        "UserCreate"
      ],
      "fields": [
        "memberships._id",
        "memberships.building_id",
        "memberships.created_at",
        "memberships.id",
        "memberships.is_active",
        "memberships.is_primary",
        "memberships.roles",
        "memberships.units",
        "role_permissions._id",
        "role_permissions.created_at",
        "role_permissions.id",
        "role_permissions.permission_id",
        "role_permissions.role_id",
        "role_permissions.role_name",
        "role_permissions.slug",
        "role_permissions.updated_at",
        "roles._id",
        "roles.base_role"
      ],
      "jobs": [],
      "docs": [
        "docs/architecture/mindmap/01_identity_access.md"
      ],
      "issues": []
    }
  },
  {
    "id": "intelligence_integrations",
    "label": "Domain 11 \u2014 Intelligence, Analytics & Integrations",
    "kind": "domain",
    "group": "Intelligence",
    "detail": "Generated from docs/architecture/mindmap/11_intelligence_integrations.md.",
    "x": -613,
    "y": 88,
    "panel": {
      "summary": "182 APIs \u00b7 115 collections \u00b7 4 frontend files \u00b7 5 jobs.",
      "frontend": [
        "frontend/src/pages/dashboard/RegularDashboard.tsx",
        "frontend/src/pages/dashboard/OwnerDashboard.tsx",
        "frontend/src/pages/dashboard/ManagerBIAnalyticsPage.tsx",
        "frontend/src/app/(app)/owner-view/page.tsx"
      ],
      "apis": [
        "GET /analytics/sinking-fund-forecast",
        "GET /analytics/maintenance/spend-trend",
        "GET /analytics/expenses-by-supplier",
        "GET /analytics/maintenance/heatmap",
        "GET /analytics/maintenance/stats",
        "GET /analytics/maintenance-stats",
        "GET /analytics/activities",
        "GET /analytics/market-snapshot",
        "GET /analytics/personal-badges",
        "GET /analytics/compliance-summary",
        "GET /analytics/marketplace-stats",
        "GET /analytics/levy-benchmarks",
        "GET /analytics/expense-breakdown",
        "GET /analytics/budget-variance",
        "GET /analytics/occupancy-trends",
        "GET /analytics/fund-forecasts",
        "GET /analytics/compliance-details",
        "GET /analytics/utility-usage/{unit_number}"
      ],
      "collections": [
        "analytics_snapshots",
        "intelligence_summary",
        "external_api_finance_pg_enabled",
        "Owner",
        "EC",
        "Manager",
        "SuperAdmin",
        "RealEstate",
        "Tenant",
        "Regular",
        "activities",
        "amenity_bookings",
        "announcements",
        "annual_levies",
        "compliance_items",
        "documents",
        "levy_categories",
        "listings"
      ],
      "fields": [
        "financial_forecasts._id",
        "financial_forecasts.assumptions",
        "financial_forecasts.building_id",
        "financial_forecasts.category",
        "financial_forecasts.confidence_score",
        "financial_forecasts.created_at",
        "financial_forecasts.financial_year",
        "financial_forecasts.fund_type",
        "intelligence_summary._id",
        "intelligence_summary.anomalies_detected_count",
        "intelligence_summary.asset_health_score",
        "intelligence_summary.attention_needed",
        "intelligence_summary.building_id",
        "intelligence_summary.capital_replacement_5y",
        "intelligence_summary.capital_shock_recommendation",
        "intelligence_summary.capital_shock_risk",
        "strata_sync_jobs._id",
        "strata_sync_jobs.building_id"
      ],
      "jobs": [
        "backend/cron/cron_pm_scheduler.py",
        "backend/workers/analytics_worker.py",
        "backend/workers/change_stream_worker.py",
        "backend/workers/scheduler.py",
        "backend/workers/temporal_worker.py"
      ],
      "docs": [
        "docs/architecture/mindmap/11_intelligence_integrations.md"
      ],
      "issues": []
    }
  },
  {
    "id": "maintenance_operations",
    "label": "Domain 06 \u2014 Maintenance & Operations",
    "kind": "domain",
    "group": "Operations",
    "detail": "Generated from docs/architecture/mindmap/06_maintenance_operations.md.",
    "x": -563,
    "y": -257,
    "panel": {
      "summary": "107 APIs \u00b7 99 collections \u00b7 7 frontend files \u00b7 2 jobs.",
      "frontend": [
        "frontend/src/pages/dashboard/DashboardHome.tsx",
        "frontend/src/pages/dashboard/MaintenancePage.tsx",
        "frontend/src/pages/dashboard/powerhouse/PowerhouseAutomationRulesPage.tsx",
        "frontend/src/pages/dashboard/powerhouse/PowerhouseWorkflowInstancePage.tsx",
        "frontend/src/pages/dashboard/requests/MyRequestsTab.tsx",
        "frontend/src/app/(app)/maintenance/[id]/page.tsx",
        "frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx"
      ],
      "apis": [
        "GET /defects/warranty-summary",
        "GET /defects/{defect_id}",
        "PUT /defects/{defect_id}",
        "DELETE /defects/{defect_id}",
        "POST /defects/{defect_id}/notes",
        "POST /defects/{defect_id}/photos",
        "POST /maintenance",
        "GET /maintenance",
        "GET /maintenance/{req_id}",
        "PUT /maintenance/{req_id}/status",
        "POST /maintenance/{req_id}/notes",
        "DELETE /maintenance/{req_id}",
        "POST /contractors",
        "GET /contractors",
        "POST /contractors/{contractor_id}/rate",
        "PUT /contractors/{contractor_id}",
        "POST /purchase-orders",
        "GET /purchase-orders"
      ],
      "collections": [
        "super_admin",
        "chairman",
        "strata_manager",
        "ec_member",
        "SmartRequestPayload",
        "WorkflowRequestStatusUpdate",
        "workflow_requests",
        "workflow_runs",
        "workflow_sla_overrides",
        "audit_logs",
        "users",
        "awaiting_count",
        "overdue_count",
        "in_progress_count",
        "request_type",
        "is_test_data",
        "MaintenanceRequestCreate",
        "WorkOrderCreate"
      ],
      "fields": [
        "asset_health_scores._id",
        "asset_health_scores.asset_id",
        "asset_health_scores.building_id",
        "asset_health_scores.health_score",
        "asset_health_scores.replacement_cost",
        "asset_health_scores.risk_score",
        "asset_health_scores.updated_at",
        "asset_templates._id",
        "asset_templates.building_id",
        "asset_templates.category",
        "asset_templates.expected_lifespan_years",
        "asset_templates.maintenance_frequency_months",
        "asset_templates.name",
        "asset_templates.replacement_cost_estimate",
        "defects._id",
        "defects.building_id",
        "defects.contractor_id",
        "defects.created_at"
      ],
      "jobs": [
        "backend/cron/cron_maintenance_intelligence.py",
        "backend/cron/cron_ppm_notifications.py"
      ],
      "docs": [
        "docs/architecture/mindmap/06_maintenance_operations.md"
      ],
      "issues": []
    }
  },
  {
    "id": "tenant_foundation",
    "label": "Domain 02 \u2014 Tenant Foundation",
    "kind": "domain",
    "group": "Core",
    "detail": "Generated from docs/architecture/mindmap/02_tenant_foundation.md.",
    "x": -335,
    "y": -521,
    "panel": {
      "summary": "155 APIs \u00b7 127 collections \u00b7 23 frontend files \u00b7 1 jobs.",
      "frontend": [
        "frontend/src/contexts/AuthContext.tsx",
        "frontend/src/contexts/IntegrityContext.tsx",
        "frontend/src/contexts/NavigationContext.tsx",
        "frontend/src/components/widgets/BuildingRiskWidget.tsx",
        "frontend/src/components/management/ManagementModelSelector.tsx",
        "frontend/src/components/layout/Footer.tsx",
        "frontend/src/components/layout/DashboardLayout.tsx",
        "frontend/src/components/layout/BuildingSwitcher.tsx",
        "frontend/src/components/dashboard/BuildingPulseWidget.tsx",
        "frontend/src/pages/public/BuildingPublicPage.tsx",
        "frontend/src/pages/dashboard/TrustAccountingPage.tsx",
        "frontend/src/pages/dashboard/PlatformBIAnalyticsPage.tsx"
      ],
      "apis": [
        "GET /building/assets/pet-register",
        "GET /building/assets/strata-roll",
        "GET /building/assets/strata-roll/pdf",
        "GET /building/assets/{asset_id}",
        "PUT /building/assets/{asset_id}",
        "DELETE /building/assets/{asset_id}",
        "GET /handovers/{handover_id}",
        "PUT /handovers/{handover_id}/checklist/{item_index}",
        "POST /handovers/{handover_id}/export",
        "POST /handovers/{handover_id}/complete",
        "POST /handovers/{handover_id}/cancel",
        "GET /buildings/{building_id}/integrations/mock-mode",
        "PUT /buildings/{building_id}/integrations/mock-mode/{feature_key}",
        "GET /nsw/building-manager-duties/summary",
        "GET /nsw/building-manager-duties/{entry_id}",
        "PUT /nsw/building-manager-duties/{entry_id}",
        "DELETE /nsw/building-manager-duties/{entry_id}",
        "GET /building-stress/snapshot"
      ],
      "collections": [
        "buildings",
        "settings",
        "site_settings",
        "feature_toggles",
        "user_feature_access",
        "organisations",
        "organisation_buildings",
        "organisation_members",
        "scheme_classes",
        "scheme_class_history",
        "class_category_allocations",
        "legal_pages",
        "navigation_configs",
        "user_nav_preferences",
        "adaptive_nav_scores",
        "building_id",
        "0024",
        "is_demo"
      ],
      "fields": [
        "building_assets._id",
        "building_assets.benefit_group_id",
        "building_assets.building_id",
        "building_assets.category",
        "building_assets.expected_lifespan_years",
        "building_assets.facility_id",
        "building_assets.health_score",
        "building_assets.id",
        "building_financial_health._id",
        "building_financial_health.building_id",
        "building_financial_health.computed_at",
        "building_financial_health.mitigation_options",
        "building_financial_health.month",
        "building_financial_health.new_risk_flags",
        "building_financial_health.overall_risk_score",
        "building_financial_health.projected_deficit_year",
        "building_financial_health_p2._id",
        "building_financial_health_p2.building_id"
      ],
      "jobs": [
        "backend/workers/arq_settings.py"
      ],
      "docs": [
        "docs/architecture/mindmap/02_tenant_foundation.md"
      ],
      "issues": []
    }
  }
];

export const interactiveMindmapEdges: MindmapEdge[] = [
  {
    "id": "root-community_communication",
    "source": "root",
    "target": "community_communication"
  },
  {
    "id": "schema-community_communication",
    "source": "db-schema",
    "target": "community_communication",
    "label": "collections"
  },
  {
    "id": "jobs-community_communication",
    "source": "background-jobs",
    "target": "community_communication",
    "label": "jobs"
  },
  {
    "id": "root-compliance_risk_insurance",
    "source": "root",
    "target": "compliance_risk_insurance"
  },
  {
    "id": "schema-compliance_risk_insurance",
    "source": "db-schema",
    "target": "compliance_risk_insurance",
    "label": "collections"
  },
  {
    "id": "jobs-compliance_risk_insurance",
    "source": "background-jobs",
    "target": "compliance_risk_insurance",
    "label": "jobs"
  },
  {
    "id": "root-documents_records",
    "source": "root",
    "target": "documents_records"
  },
  {
    "id": "schema-documents_records",
    "source": "db-schema",
    "target": "documents_records",
    "label": "collections"
  },
  {
    "id": "jobs-documents_records",
    "source": "background-jobs",
    "target": "documents_records",
    "label": "jobs"
  },
  {
    "id": "root-finance_ap_invoices",
    "source": "root",
    "target": "finance_ap_invoices"
  },
  {
    "id": "schema-finance_ap_invoices",
    "source": "db-schema",
    "target": "finance_ap_invoices",
    "label": "collections"
  },
  {
    "id": "jobs-finance_ap_invoices",
    "source": "background-jobs",
    "target": "finance_ap_invoices",
    "label": "jobs"
  },
  {
    "id": "root-finance_levies",
    "source": "root",
    "target": "finance_levies"
  },
  {
    "id": "schema-finance_levies",
    "source": "db-schema",
    "target": "finance_levies",
    "label": "collections"
  },
  {
    "id": "jobs-finance_levies",
    "source": "background-jobs",
    "target": "finance_levies",
    "label": "jobs"
  },
  {
    "id": "root-finance_trust_banking",
    "source": "root",
    "target": "finance_trust_banking"
  },
  {
    "id": "schema-finance_trust_banking",
    "source": "db-schema",
    "target": "finance_trust_banking",
    "label": "collections"
  },
  {
    "id": "jobs-finance_trust_banking",
    "source": "background-jobs",
    "target": "finance_trust_banking",
    "label": "jobs"
  },
  {
    "id": "root-governance_decisions",
    "source": "root",
    "target": "governance_decisions"
  },
  {
    "id": "schema-governance_decisions",
    "source": "db-schema",
    "target": "governance_decisions",
    "label": "collections"
  },
  {
    "id": "root-identity_access",
    "source": "root",
    "target": "identity_access"
  },
  {
    "id": "schema-identity_access",
    "source": "db-schema",
    "target": "identity_access",
    "label": "collections"
  },
  {
    "id": "root-intelligence_integrations",
    "source": "root",
    "target": "intelligence_integrations"
  },
  {
    "id": "schema-intelligence_integrations",
    "source": "db-schema",
    "target": "intelligence_integrations",
    "label": "collections"
  },
  {
    "id": "jobs-intelligence_integrations",
    "source": "background-jobs",
    "target": "intelligence_integrations",
    "label": "jobs"
  },
  {
    "id": "root-maintenance_operations",
    "source": "root",
    "target": "maintenance_operations"
  },
  {
    "id": "schema-maintenance_operations",
    "source": "db-schema",
    "target": "maintenance_operations",
    "label": "collections"
  },
  {
    "id": "jobs-maintenance_operations",
    "source": "background-jobs",
    "target": "maintenance_operations",
    "label": "jobs"
  },
  {
    "id": "root-tenant_foundation",
    "source": "root",
    "target": "tenant_foundation"
  },
  {
    "id": "schema-tenant_foundation",
    "source": "db-schema",
    "target": "tenant_foundation",
    "label": "collections"
  },
  {
    "id": "jobs-tenant_foundation",
    "source": "background-jobs",
    "target": "tenant_foundation",
    "label": "jobs"
  },
  {
    "id": "tenant-finance",
    "source": "tenant_foundation",
    "target": "finance_levies",
    "label": "building_id"
  },
  {
    "id": "finance-trust",
    "source": "finance_levies",
    "target": "finance_trust_banking",
    "label": "payments"
  },
  {
    "id": "maintenance-ap",
    "source": "maintenance_operations",
    "target": "finance_ap_invoices",
    "label": "invoices"
  },
  {
    "id": "documents-governance",
    "source": "documents_records",
    "target": "governance_decisions",
    "label": "minutes"
  },
  {
    "id": "risk-governance",
    "source": "compliance_risk_insurance",
    "target": "governance_decisions",
    "label": "compliance"
  },
  {
    "id": "intelligence-finance",
    "source": "intelligence_integrations",
    "target": "finance_levies",
    "label": "forecasts"
  },
  {
    "id": "community-tenant",
    "source": "community_communication",
    "target": "tenant_foundation",
    "label": "badges"
  }
];
