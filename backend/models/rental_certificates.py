"""
Certificate Models.

ACT Unit Titles (Management) Act 2011:
  - Section 119A  — rental certificate (RENTAL type) — mandatory from 9 Jan 2025, fee cap $342
  - Section 119   — pre-sale information certificate (SALE type) — on request from purchaser's solicitor

NSW Strata Schemes Management Act 2015:
  - Section 184   — pre-sale certificate (NSW_S184 type) — ordered by purchaser's solicitor,
                    fee capped under NSW Fair Trading regulations

All types share the same collection (rental_certificates) and the same request/issue workflow.
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, List


class RentalCertificateStatus:
    """Lifecycle status of a certificate."""
    REQUESTED = "requested"  # submitted, awaiting manager action
    ISSUED = "issued"  # generated and available for download
    SUPERSEDED = "superseded"  # replaced by a newer certificate
    PENDING_PAYMENT = "pending_payment"  # external order awaiting fee payment


class RentalCertificateType:
    """
    Certificate type determines which statutory form is generated and which fee applies.
    SALE and RENTAL cover ACT s.119A variants.
    NSW_S184 covers NSW SSMA 2015 s.184 pre-sale information certificates.
    """
    SALE = "sale"  # ACT s.119A — pre-sale, ordered by owner or purchaser's solicitor
    RENTAL = "rental"  # ACT s.119A — rental, provided to incoming tenant before lease
    UPDATE = "update"  # Corrected re-issue of an existing certificate
    NSW_S184 = "nsw_s184"  # NSW SSMA 2015 s.184 — pre-sale information certificate


# Fee schedule (current as at July 2025)
CERTIFICATE_FEE_SCHEDULE: dict = {
    RentalCertificateType.SALE: 332.00,
    RentalCertificateType.RENTAL: 332.00,
    RentalCertificateType.UPDATE: 165.00,
    RentalCertificateType.NSW_S184: 342.00,  # NSW Fair Trading fee cap
}


class RentalCertificateRequest(BaseModel):
    """Fields submitted by the owner/solicitor when requesting a certificate."""
    unit_number: str
    tenant_name: str  # purchaser's solicitor name for SALE/NSW_S184; tenant name for RENTAL
    lease_start_date: str  # settlement date for SALE/NSW_S184; lease start for RENTAL
    lease_end_date: Optional[str] = None
    additional_notes: Optional[str] = None
    certificate_type: str = RentalCertificateType.SALE
    # ACT rental-type specific
    tenancy_start_date: Optional[str] = None
    weekly_rent: Optional[float] = None
    # NSW s.184 specific — requester details (purchaser's solicitor)
    requester_firm: Optional[str] = None  # solicitor's firm name
    requester_email: Optional[str] = None  # for certificate delivery
    requester_reference: Optional[str] = None  # solicitor's file reference
    # Fee payment — tracked for external (conveyancer) orders
    fee_paid: bool = False
    fee_payment_method: Optional[str] = None  # "online" | "invoice" | "waived"
    payment_reference: Optional[str] = None


class RentalCertificateIssueRequest(BaseModel):
    """
    Additional fields provided by EC/admin when issuing the certificate.
    Financial and EC data is auto-populated from the database.
    """
    insurance_insurer: Optional[str] = None
    insurance_coverage_amount: Optional[float] = None
    insurance_public_liability: Optional[str] = "$10,000,000"
    insurance_expiry: Optional[str] = None
    known_defects: Optional[str] = "None at time of issue"
    special_levies_pending: Optional[str] = "None pending at time of issue"
    common_property_notes: Optional[str] = None
    strata_manager_name: Optional[str] = None
    strata_manager_contact: Optional[str] = None
    strata_manager_licence: Optional[str] = None


class RentalCertificateUpdateRequest(BaseModel):
    """Fields that can be updated on an issued certificate."""
    insurance_insurer: Optional[str] = None
    insurance_coverage_amount: Optional[float] = None
    insurance_public_liability: Optional[str] = None
    insurance_expiry: Optional[str] = None
    known_defects: Optional[str] = None
    special_levies_pending: Optional[str] = None
    common_property_notes: Optional[str] = None
    strata_manager_name: Optional[str] = None
    strata_manager_contact: Optional[str] = None
    strata_manager_licence: Optional[str] = None
    additional_notes: Optional[str] = None


class RentalCertificateResponse(BaseModel):
    """Full rental certificate response model."""
    model_config = ConfigDict(extra="ignore")

    id: str
    cert_number: str  # Format: RC-YYYY-NNNN
    unit_number: str
    tenant_name: str
    lease_start_date: str
    lease_end_date: Optional[str] = None
    status: str

    # Request metadata
    requested_by: str
    requested_by_name: str
    requested_at: str

    # Issue metadata
    issued_by: Optional[str] = None
    issued_by_name: Optional[str] = None
    issued_at: Optional[str] = None
    expiry_date: Optional[str] = None  # issued_at + 5 years

    # Auto-populated property snapshot (set on issue)
    owner_name: Optional[str] = None
    owner_name_b: Optional[str] = None  # Second owner if joint ownership
    lot_number: Optional[str] = None
    unit_entitlement: Optional[int] = None
    property_type: Optional[str] = None

    # Auto-populated financial snapshot
    levy_quarterly: Optional[float] = None
    levy_annual: Optional[float] = None
    arrears_amount: float = 0.0
    admin_budget_current: Optional[float] = None
    sinking_fund_balance: Optional[float] = None

    # EC snapshot at time of issue
    ec_members_snapshot: Optional[List[dict]] = None

    # Admin-provided fields
    insurance_insurer: Optional[str] = None
    insurance_coverage_amount: Optional[float] = None
    insurance_public_liability: Optional[str] = None
    insurance_expiry: Optional[str] = None
    known_defects: Optional[str] = None
    special_levies_pending: Optional[str] = None
    common_property_notes: Optional[str] = None
    strata_manager_name: Optional[str] = None
    strata_manager_contact: Optional[str] = None
    strata_manager_licence: Optional[str] = None
    additional_notes: Optional[str] = None

    # Certificate type and fee
    certificate_type: str = RentalCertificateType.SALE
    fee_aud: Optional[float] = None
    fee_paid: bool = False
    fee_payment_method: Optional[str] = None
    payment_reference: Optional[str] = None

    # ACT rental-type specific
    tenancy_start_date: Optional[str] = None
    weekly_rent: Optional[float] = None

    # NSW s.184 specific fields (auto-populated on issue for NSW buildings)
    strata_plan_number: Optional[str] = None  # NSW strata plan number
    registered_address: Optional[str] = None  # registered address of common property
    scheme_class: Optional[str] = None  # residential / commercial / mixed
    managing_agent_disclosure: Optional[str] = None  # managing agent name + licence
    by_law_amendments_since: Optional[str] = None  # date of last by-law amendment
    recent_decisions_summary: Optional[str] = None  # key decisions affecting the lot
    pending_special_levies: Optional[str] = None  # any special levies voted but not yet levied
    current_contracts_summary: Optional[str] = None  # major contracts in force

    # Requester details for external (conveyancer) orders
    requester_firm: Optional[str] = None
    requester_email: Optional[str] = None
    requester_reference: Optional[str] = None

    created_at: str
    updated_at: str
