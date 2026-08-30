"""Seed Phase 1 manager-OS reference data into the Acme demo scheme.

Usage:
    cd backend
    python3 seeds/phase1_ops_foundations.py

Requires:
    - alembic upgrade head already applied
    - the Acme demo customer seed (`seeds/demo_customer.py`) already loaded
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

from db_postgres.session import async_session_context


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
_u5 = lambda key: str(uuid.uuid5(_NS, f"phase1-foundations:{key}"))  # noqa: E731

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"
# Must match ACME_SCHEME_NUMBER in seeds/demo_customer.py, renumbered 2026-08-20.
ACME_SCHEME_NUMBER = "UPDEMO5"


async def _resolve_demo_scheme(session) -> tuple[str, str]:
    """Generated function header.

    Function: _resolve_demo_scheme
    Path: backend/seeds/phase1_ops_foundations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await session.execute(
        text(
            """
            SELECT tenant_id, scheme_id
            FROM core.schemes
            WHERE scheme_number = :scheme_number
            """
        ),
        {"scheme_number": ACME_SCHEME_NUMBER},
    )
    result = row.first()
    if not result:
        raise RuntimeError(
            "Acme demo scheme not found. Run `python3 seeds/demo_customer.py` first."
        )
    return str(result.tenant_id), str(result.scheme_id)


async def seed_phase1_foundations() -> None:
    """Generated function header.

    Function: seed_phase1_foundations
    Path: backend/seeds/phase1_ops_foundations.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": _BYPASS_UUID})
        tenant_id, scheme_id = await _resolve_demo_scheme(session)
        await session.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})

        access_device_types = [
            ("garage_fob", "Garage Fob", "Standard vehicle-access fob", 8500, 5000, True, False),
            ("building_fob", "Building Fob", "Common-area pedestrian access fob", 6500, 5000, True, False),
            ("access_card", "Access Card", "Lift and entry card for residents and contractors", 2500, 0, False, True),
            ("garage_remote", "Garage Remote", "Remote transmitter for basement gates", 12000, 5000, True, True),
        ]
        for type_key, name, description, fee_cents, deposit_cents, requires_return, requires_approval in access_device_types:
            await session.execute(
                text(
                    """
                    INSERT INTO access.access_device_types
                        (id, tenant_id, scheme_id, type_key, name, description,
                         fee_cents, deposit_cents, requires_return, requires_approval,
                         lifecycle_policy, is_active, source_system, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :scheme_id, :type_key, :name, :description,
                         :fee_cents, :deposit_cents, :requires_return, :requires_approval,
                         '{}'::jsonb, TRUE, 'seed:phase1_ops_foundations', now(), now())
                    ON CONFLICT (id) DO UPDATE
                        SET name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            fee_cents = EXCLUDED.fee_cents,
                            deposit_cents = EXCLUDED.deposit_cents,
                            requires_return = EXCLUDED.requires_return,
                            requires_approval = EXCLUDED.requires_approval,
                            updated_at = now()
                    """
                ),
                {
                    "id": _u5(f"access-device-type:{type_key}"),
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "type_key": type_key,
                    "name": name,
                    "description": description,
                    "fee_cents": fee_cents,
                    "deposit_cents": deposit_cents,
                    "requires_return": requires_return,
                    "requires_approval": requires_approval,
                },
            )

        intake_sources = [
            ("email_inbound", "Inbound Email", "Owner, tenant, EC, or contractor email intake", "email"),
            ("portal_request", "Resident Portal", "Resident-submitted request via the dashboard", "portal"),
            ("phone_call", "Phone Call", "Phone triage entered by staff", "phone"),
            ("preventive_schedule", "Preventive Schedule", "Recurring maintenance or compliance schedule", "cron"),
        ]
        for source_key, source_name, description, channel_type in intake_sources:
            await session.execute(
                text(
                    """
                    INSERT INTO ops.service_request_intake_sources
                        (id, tenant_id, scheme_id, source_key, source_name, description,
                         channel_type, is_active, source_system, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :scheme_id, :source_key, :source_name, :description,
                         :channel_type, TRUE, 'seed:phase1_ops_foundations', now(), now())
                    ON CONFLICT (id) DO UPDATE
                        SET source_name = EXCLUDED.source_name,
                            description = EXCLUDED.description,
                            channel_type = EXCLUDED.channel_type,
                            updated_at = now()
                    """
                ),
                {
                    "id": _u5(f"intake-source:{source_key}"),
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "source_key": source_key,
                    "source_name": source_name,
                    "description": description,
                    "channel_type": channel_type,
                },
            )

        draft_templates = [
            (
                "newsletter-monthly",
                "Monthly Community Newsletter",
                "Your monthly building update",
                "<p>Highlights from your building, upcoming works, and important reminders.</p>",
            ),
            (
                "planned-disruption",
                "Planned Disruption Notice",
                "Upcoming works affecting your building",
                "<p>This notice explains the impact window, affected areas, and who to contact.</p>",
            ),
        ]
        for draft_key, title, subject, body in draft_templates:
            await session.execute(
                text(
                    """
                    INSERT INTO communications.communication_drafts
                        (id, tenant_id, scheme_id, title, subject, body, draft_kind,
                         is_template, status, risk_level, pii_redacted,
                         source_system, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :scheme_id, :title, :subject, :body, 'template',
                         TRUE, 'approved', 'medium', TRUE,
                         'seed:phase1_ops_foundations', now(), now())
                    ON CONFLICT (id) DO UPDATE
                        SET title = EXCLUDED.title,
                            subject = EXCLUDED.subject,
                            body = EXCLUDED.body,
                            updated_at = now()
                    """
                ),
                {
                    "id": _u5(f"draft-template:{draft_key}"),
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "title": title,
                    "subject": subject,
                    "body": body,
                },
            )

        anomaly_rows = [
            ("illegal_dumping", "Illegal dumping behind loading bay", "high"),
            ("water_usage_spike", "Unexpected spike in common-area water use", "medium"),
            ("common_power_spike", "Lift-room electricity consumption anomaly", "medium"),
        ]
        for anomaly_type, title, severity in anomaly_rows:
            await session.execute(
                text(
                    """
                    INSERT INTO finance.utility_anomalies
                        (id, tenant_id, scheme_id, anomaly_type, severity, status, title,
                         approval_required, source_system, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :scheme_id, :anomaly_type, :severity, 'open', :title,
                         TRUE, 'seed:phase1_ops_foundations', now(), now())
                    ON CONFLICT (id) DO UPDATE
                        SET title = EXCLUDED.title,
                            severity = EXCLUDED.severity,
                            updated_at = now()
                    """
                ),
                {
                    "id": _u5(f"utility-anomaly:{anomaly_type}"),
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "anomaly_type": anomaly_type,
                    "severity": severity,
                    "title": title,
                },
            )

        profile_id = _u5("sustainability-profile")
        await session.execute(
            text(
                """
                INSERT INTO sustainability.building_sustainability_profiles
                    (id, tenant_id, scheme_id, overall_score, emissions_baseline_kg,
                     solar_readiness_level, water_efficiency_level, lighting_efficiency_level,
                     notes, source_system, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :scheme_id, 68.5, 84250.0,
                     'medium', 'medium', 'low',
                     'Seeded Phase 1 sustainability baseline for the Acme demo scheme.',
                     'seed:phase1_ops_foundations', now(), now())
                ON CONFLICT (id) DO UPDATE
                    SET overall_score = EXCLUDED.overall_score,
                        emissions_baseline_kg = EXCLUDED.emissions_baseline_kg,
                        notes = EXCLUDED.notes,
                        updated_at = now()
                """
            ),
            {"id": profile_id, "tenant_id": tenant_id, "scheme_id": scheme_id},
        )

        project_id = _u5("energy-project:led-upgrade")
        await session.execute(
            text(
                """
                INSERT INTO sustainability.energy_efficiency_projects
                    (id, tenant_id, scheme_id, profile_id, project_type, title, description,
                     status, capex_estimate_cents, payback_months, emissions_savings_kg,
                     source_system, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :scheme_id, :profile_id, 'lighting_upgrade',
                     'Common-area LED and sensor upgrade',
                     'Replace hallway and basement fittings with LEDs and occupancy controls.',
                     'approved', 1850000, 26, 12450.0,
                     'seed:phase1_ops_foundations', now(), now())
                ON CONFLICT (id) DO UPDATE
                    SET description = EXCLUDED.description,
                        status = EXCLUDED.status,
                        capex_estimate_cents = EXCLUDED.capex_estimate_cents,
                        payback_months = EXCLUDED.payback_months,
                        emissions_savings_kg = EXCLUDED.emissions_savings_kg,
                        updated_at = now()
                """
            ),
            {
                "id": project_id,
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "profile_id": profile_id,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO sustainability.sustainability_project_benefit_cases
                    (id, tenant_id, scheme_id, project_id, scenario_name, assumptions,
                     benefit_summary, npv_cents, roi_percentage, source_system, created_at, updated_at)
                VALUES
                    (:id, :tenant_id, :scheme_id, :project_id, 'base_case',
                     '{"electricity_tariff_cents": 28, "after_hours_reduction_pct": 35}'::jsonb,
                     'Reduced common-area power spend and resident disruption from manual switching.',
                     920000, 18.75, 'seed:phase1_ops_foundations', now(), now())
                ON CONFLICT (id) DO UPDATE
                    SET assumptions = EXCLUDED.assumptions,
                        benefit_summary = EXCLUDED.benefit_summary,
                        npv_cents = EXCLUDED.npv_cents,
                        roi_percentage = EXCLUDED.roi_percentage,
                        updated_at = now()
                """
            ),
            {
                "id": _u5("benefit-case:led-upgrade"),
                "tenant_id": tenant_id,
                "scheme_id": scheme_id,
                "project_id": project_id,
            },
        )

        approval_policies = [
            ("medium_risk_communications", 0, ["strata_manager", "ec_member"]),
            ("access_device_disablement", 0, ["strata_manager", "super_admin"]),
            ("ai_recommendation_high_risk", 0, ["strata_manager", "ec_member"]),
        ]
        for policy_code, threshold_cents, required_roles in approval_policies:
            await session.execute(
                text(
                    """
                    INSERT INTO core.approval_policies
                        (approval_policy_id, tenant_id, scheme_id, policy_code, threshold_cents,
                         required_roles, requires_webauthn, status, created_at)
                    VALUES
                        (:id, :tenant_id, :scheme_id, :policy_code, :threshold_cents,
                         :required_roles, FALSE, 'active', now())
                    ON CONFLICT (approval_policy_id) DO UPDATE
                        SET policy_code = EXCLUDED.policy_code,
                            threshold_cents = EXCLUDED.threshold_cents,
                            required_roles = EXCLUDED.required_roles
                    """
                ),
                {
                    "id": _u5(f"approval-policy:{policy_code}"),
                    "tenant_id": tenant_id,
                    "scheme_id": scheme_id,
                    "policy_code": policy_code,
                    "threshold_cents": threshold_cents,
                    "required_roles": required_roles,
                },
            )

        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed_phase1_foundations())
