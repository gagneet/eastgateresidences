"""
ABA File Generator — Australian Banking Association DE Format

Generates ABA (Direct Entry) files for batch payments in Australian banking.
Specification: BECS (Bulk Electronic Clearing System) DE format as used by
all Australian financial institutions.

Format overview:
  Record 0: Descriptive Record      (1 per file)
  Record 1: Detail Record           (1 per payment)
  Record 7: File Total Record       (1 per file)

Each record is exactly 120 characters wide.
Reference: https://www.cemtexaba.com/aba-format/cemtex-aba-file-format-details

Usage:
    from utils.aba_generator import generate_aba_file
    aba_text = generate_aba_file(batch_doc)
    # aba_text is the full ABA file content as a string
"""
from __future__ import annotations

from datetime import datetime

from typing import Any, Dict, List


class ABAValidationError(ValueError):
    """Raised when batch data fails ABA format validation."""


# ─── Record builders ──────────────────────────────────────────────────────────

def _pad(value: str, length: int, pad_char: str = " ", align: str = "left") -> str:
    """Pad or truncate a string to exactly *length* characters."""
    value = str(value)[:length]
    if align == "left":
        return value.ljust(length, pad_char)
    return value.rjust(length, pad_char)


def _clean_bsb(bsb: str) -> str:
    """Normalise a BSB to 6-digit format (strip hyphens, validate)."""
    bsb = bsb.replace("-", "").replace(" ", "").strip()
    if len(bsb) != 6 or not bsb.isdigit():
        raise ABAValidationError(f"Invalid BSB: '{bsb}'. Must be 6 digits.")
    return bsb


def _format_bsb(bsb: str) -> str:
    """Format BSB as 'XXX-XXX' (7 chars)."""
    bsb = _clean_bsb(bsb)
    return f"{bsb[:3]}-{bsb[3:]}"


def _build_descriptive_record(
        bsb: str,
        account_number: str,
        bank_code: str,
        user_name: str,
        user_id: str,
        description: str,
        processing_date: str,
) -> str:
    """Build Record Type 0 — Descriptive Record (120 chars)."""
    # Field widths (columns 1-120, 1-indexed):
    # 1      : record type "0"
    # 2-18   : blank (17) — BSB/account fields are blank for the descriptive record
    # 19-20  : reel sequence number (2)  — written as "01"
    # 21-23  : user FI abbreviation (3)  e.g. "ANZ", "NAB", "WBC", "CBA"
    # 24-30  : blank (7)
    # 31-56  : user preferred specification = user name (26)
    # 57-62  : user ID (APCA) (6)  right-justified zero-filled
    # 63-74  : entry description (12)
    # 75-80  : processing date DDMMYY (6)
    # 81-120 : blank (40)

    record = (
            "0"  # col 1: record type
            + " " * 17  # cols 2-18: blank
            + "01"  # cols 19-20: reel sequence
            + _pad(bank_code[:3].upper(), 3)  # cols 21-23: bank code
            + " " * 7  # cols 24-30: blank
            + _pad(user_name[:26], 26)  # cols 31-56: user name
            + _pad(str(user_id)[:6], 6, "0", "right")  # cols 57-62: APCA user ID
            + _pad(description[:12], 12)  # cols 63-74: description
            + _pad(processing_date[:6], 6)  # cols 75-80: DDMMYY
            + " " * 40  # cols 81-120: blank
    )
    if len(record) != 120:
        raise ABAValidationError(f"Descriptive record length {len(record)} != 120")
    return record


def _build_detail_record(
        bsb: str,
        account_number: str,
        indicator: str,
        transaction_code: str,
        amount_cents: int,
        account_name: str,
        lodgement_ref: str,
        trace_bsb: str,
        trace_account: str,
        remitter_name: str,
        withholding_tax_cents: int = 0,
) -> str:
    """Build Record Type 1 — Detail Record (120 chars).

    transaction_code:
      13 = Externally initiated debit
      50 = Initiated by the user (Credit) — most common for supplier payments
      51 = Pension / benefit — not used here
      53 = Payroll — not used here
    """
    amount_str = _pad(str(amount_cents), 10, "0", "right")
    withholding_str = _pad(str(withholding_tax_cents), 8, "0", "right")

    record = (
            "1"  # col 1: record type
            + _format_bsb(bsb)  # cols 2-8: BSB (7)
            + _pad(account_number[:9].replace(" ", "").replace("-", ""), 9)  # cols 9-17
            + _pad(indicator[:1], 1)  # col 18: indicator
            + _pad(transaction_code[:2], 2)  # cols 19-20: tx code
            + amount_str  # cols 21-30: amount (cents)
            + _pad(account_name[:32], 32)  # cols 31-62: title
            + _pad(lodgement_ref[:18], 18)  # cols 63-80: lodgement
            + _format_bsb(trace_bsb)  # cols 81-87: trace BSB
            + _pad(trace_account[:9].replace(" ", ""), 9)  # cols 88-96: trace account
            + _pad(remitter_name[:16], 16)  # cols 97-112: remitter
            + withholding_str  # cols 113-120: WHT
    )
    if len(record) != 120:
        raise ABAValidationError(f"Detail record length {len(record)} != 120")
    return record


def _build_file_total_record(
        net_total_cents: int,
        credit_total_cents: int,
        debit_total_cents: int,
        record_count: int,
) -> str:
    """Build Record Type 7 — File Total Record (120 chars)."""
    bsb_filler = "999-999"
    net_str = _pad(str(abs(net_total_cents)), 10, "0", "right")
    credit_str = _pad(str(credit_total_cents), 10, "0", "right")
    debit_str = _pad(str(debit_total_cents), 10, "0", "right")
    count_str = _pad(str(record_count), 6, "0", "right")

    record = (
            "7"  # col 1: record type
            + bsb_filler  # cols 2-8: 999-999
            + " " * 12  # cols 9-20: blank
            + net_str  # cols 21-30: net total (unsigned)
            + credit_str  # cols 31-40: credit total
            + debit_str  # cols 41-50: debit total
            + " " * 24  # cols 51-74: blank
            + count_str  # cols 75-80: count of type-1 records
            + " " * 40  # cols 81-120: blank
    )
    if len(record) != 120:
        raise ABAValidationError(f"File total record length {len(record)} != 120")
    return record


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_aba_file(batch_doc: Dict[str, Any]) -> str:
    """Generate a complete ABA file string from a payment batch document.

    *batch_doc* is the MongoDB document from ``trust_ledger_batches``.

    Required batch fields:
      - building_id
      - description (≤12 chars used)
      - payment_date  (ISO-8601 date, e.g. "2026-03-17")
      - items[]
        - bsb
        - account_number
        - account_name (payee title)
        - amount  (float, AUD)
        - lodgement_ref  (≤18 chars)

    Optional batch fields (use sensible defaults if absent):
      - originating_bsb
      - originating_account
      - originating_name
      - apca_user_id
      - bank_code

    Returns the full ABA file as a multi-line string (lines separated by \\n).
    """
    items: List[Dict[str, Any]] = batch_doc.get("items", [])
    if not items:
        raise ABAValidationError("Batch has no payment items.")

    # Parse processing date
    payment_date_raw: str = batch_doc.get("payment_date", "")
    try:
        pd = datetime.strptime(payment_date_raw[:10], "%Y-%m-%d")
        processing_date = pd.strftime("%d%m%y")
    except ValueError:
        processing_date = datetime.utcnow().strftime("%d%m%y")

    # Originator details (building's bank account — use sensible defaults)
    orig_bsb = batch_doc.get("originating_bsb", "000-000")
    orig_account = batch_doc.get("originating_account", "000000000")
    orig_name = batch_doc.get("originating_name", "Eastgate OC")[:26]
    apca_id = str(batch_doc.get("apca_user_id", "000000"))[:6]
    bank_code = batch_doc.get("bank_code", "SFX")[:3]  # 3-char bank abbreviation
    description = batch_doc.get("description", "PAYMENT")[:12]

    lines: List[str] = []

    # Record 0
    lines.append(_build_descriptive_record(
        bsb=orig_bsb,
        account_number=orig_account,
        bank_code=bank_code,
        user_name=orig_name,
        user_id=apca_id,
        description=description,
        processing_date=processing_date,
    ))

    # Record 1s — one per payment item
    credit_total_cents = 0
    debit_total_cents = 0

    for item in items:
        amount_cents = int(round(float(item.get("amount", 0)) * 100))
        if amount_cents <= 0:
            raise ABAValidationError(f"Payment amount must be positive: {item.get('amount')}")

        # All outgoing payments from OC are credits (50 = credit)
        tx_code = item.get("transaction_code", "50")
        if tx_code == "50":
            credit_total_cents += amount_cents
        else:
            debit_total_cents += amount_cents

        lines.append(_build_detail_record(
            bsb=item.get("bsb", "000-000"),
            account_number=item.get("account_number", "000000000"),
            indicator=" ",
            transaction_code=tx_code,
            amount_cents=amount_cents,
            account_name=item.get("account_name", item.get("payee_name", "UNKNOWN"))[:32],
            lodgement_ref=item.get("lodgement_ref", item.get("reference", ""))[:18],
            trace_bsb=orig_bsb,
            trace_account=orig_account,
            remitter_name=orig_name[:16],
            withholding_tax_cents=0,
        ))

    # Record 7
    net_total_cents = credit_total_cents - debit_total_cents
    lines.append(_build_file_total_record(
        net_total_cents=net_total_cents,
        credit_total_cents=credit_total_cents,
        debit_total_cents=debit_total_cents,
        record_count=len(items),
    ))

    return "\n".join(lines)
