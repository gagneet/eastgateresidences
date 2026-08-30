"""
GST & BAS (Business Activity Statement) Pydantic models.

Australian Owners Corporation GST rules:
  - OC is treated as a NON-PROFIT BODY for GST purposes (ATO ruling)
  - Compulsory GST registration threshold for OCs = $150,000 annual turnover
    (NOT $75,000 — the standard business threshold; OCs qualify for the higher
     non-profit threshold per ATO GST Act s.188-25)
  - Optional GST registration is available below $150,000 if the OC chooses
  - If registered: levies (admin + sinking) attract 10% GST
  - If registered: OC can claim input tax credits (ITCs) on GST-inclusive expenses
  - Water (Icon Water ACT) = GST-free supply — no GST, no credits
  - Council rates = GST-free — no GST, no credits
  - Electricity, cleaning, repairs, management fees, insurance = GST applies (credits claimable)

BAS filed quarterly (or annually for small turnover):
  G1  = Total sales including GST
  1A  = GST on sales (output tax)
  1B  = GST credits (input tax credits)
  Net = 1A − 1B = net GST payable to ATO

Income tax (annual — July filing):
  - OC is treated as a PUBLIC COMPANY for income tax purposes
  - Mutual income (levies from lot owners) = exempt under the mutuality principle
  - Non-mutual income (bank interest on trust accounts, rental of common areas
    to non-members, visitor parking fees) = ASSESSABLE and taxable at company rate
  - If assessable income > $0: must file ATO Company Tax Return (NAT 0656)
    by 31 October (or via tax agent extension)
  - ABN required for GST registration; TFN required for tax return lodgement
  - Records must be retained for a minimum of 5 years from date of lodgement
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class GSTSettings(BaseModel):
    """Building-level GST configuration."""
    gst_registered: bool
    levy_gst_rate: float  # e.g. 0.10
    effective_gst_rate: float  # 0.0 if not registered
    gst_label: str  # e.g. "GST (10%)"
    gst_multiplier: float  # e.g. 1.10


class GSTLineItem(BaseModel):
    """A single transaction line for the GST ledger (expense or GST-free item)."""
    date: Optional[str] = None
    description: Optional[str] = None
    supplier_name: Optional[str] = None
    invoice_number: Optional[str] = None
    category_name: Optional[str] = None
    fund_type: Optional[str] = None  # "administrative" | "sinking"
    amount_ex_gst: float = 0.0
    gst_amount: float = 0.0
    amount_inc_gst: float = 0.0
    is_gst_free: bool = False


class GSTOutputTaxRow(BaseModel):
    """Output tax (GST collected) for one period — levy income breakdown."""
    quarter: str  # "Q1" | "Q2" | "Q3" | "Q4" | "Annual"
    period_start: str  # ISO date
    period_end: str  # ISO date
    admin_levy_ex_gst: float
    sinking_levy_ex_gst: float
    total_levy_ex_gst: float
    gst_rate: float  # e.g. 0.10
    gst_amount: float  # output tax for the period
    total_levy_inc_gst: float


class BASWorksheet(BaseModel):
    """
    ATO BAS (Business Activity Statement) worksheet fields.

    Field descriptions (ATO GST return):
      G1  = Total sales including GST (levy income inc-GST + other income)
      G2  = Export sales (always 0 for a residential strata OC)
      G3  = Other GST-free sales (0 — water/rates are GST-free PURCHASES, not sales)
      G10 = Capital purchases including GST
      G11 = Non-capital purchases including GST (sum of GST-inclusive expenses)
      1A  = GST on sales = output tax
      1B  = GST credits = input tax credits (ITCs)
      Net = 1A − 1B
    """
    financial_year: str
    quarter: str
    period_start: str
    period_end: str
    # G-fields
    G1: float = 0.0
    G2: float = 0.0  # always 0.0 for OC
    G3: float = 0.0  # always 0.0 — water/rates are purchases not sales
    G10: float = 0.0  # capital purchases
    G11: float = 0.0  # non-capital GST-inclusive purchases
    # Tax fields
    field_1A: float = 0.0  # GST on sales (output tax)
    field_1B: float = 0.0  # GST credits (input tax credits)
    net_9: float = 0.0  # 1A − 1B; positive = owes ATO; negative = refund
    gst_registered: bool = True
    notes: List[str] = []


class GSTQuarterlySummary(BaseModel):
    """GST summary for a single quarter."""
    quarter: str
    period_start: str
    period_end: str
    levy_ex_gst: float = 0.0
    levy_inc_gst: float = 0.0
    output_tax: float = 0.0
    itc_total: float = 0.0
    water_total: float = 0.0
    council_rates_total: float = 0.0
    gst_free_total: float = 0.0
    net_gst_payable: float = 0.0


class GSTSummary(BaseModel):
    """
    Full GST summary for a financial year or quarter.

    All amounts in AUD. Single source of truth — derived from:
      - annual_levies collection (output tax on levies)
      - expense_transactions collection (input tax credits)
      - water_bills + council_rates (GST-free informational)
    """
    financial_year: str
    quarter: str  # "Annual" or "Q1"–"Q4"
    period_start: str
    period_end: str
    gst_registered: bool
    effective_gst_rate: float
    # Output tax (GST collected on levies)
    levy_ex_gst: float = 0.0
    levy_inc_gst: float = 0.0
    output_tax: float = 0.0
    # Input tax credits (claimable GST on expenses)
    itc_total: float = 0.0
    itc_line_items: List[GSTLineItem] = []
    # GST-free (informational only — no credits)
    water_total: float = 0.0
    council_rates_total: float = 0.0
    gst_free_total: float = 0.0
    # Net
    net_gst_payable: float = 0.0
    # Per-quarter breakdown (populated when quarter="Annual")
    quarterly: List[GSTQuarterlySummary] = []


class GSTFreeSection(BaseModel):
    """GST-free items response — water bills + council rates."""
    water_bills: List[GSTLineItem] = []
    water_total: float = 0.0
    council_rates: List[GSTLineItem] = []
    council_rates_total: float = 0.0
    gst_free_total: float = 0.0
    note: str = (
        "Icon Water (ACT) utility charges and ACT council rates are GST-free. "
        "No input tax credits (ITCs) are available on these items."
    )


class GSTCategoryBreakdown(BaseModel):
    """Single category row in the input-tax response."""
    category: str
    itc_total: float
    items: List[GSTLineItem] = []


class GSTInputTaxResponse(BaseModel):
    """Response model for GET /gst/input-tax."""
    financial_year: str
    itc_total: float
    line_items: List[GSTLineItem] = []
    by_category: List[GSTCategoryBreakdown] = []


class GSTQuarterlyWorksheetsResponse(BaseModel):
    """Response model for GET /gst/quarterly-worksheets."""
    financial_year: str
    quarterly: List[BASWorksheet] = []
    annual: BASWorksheet


class GSTIncomeTaxPrep(BaseModel):
    """
    Annual income tax preparation summary for CPA / ATO filing (July).

    OC is treated as a public company for income tax.
    Mutual income (levies) is exempt; non-mutual income (interest, rentals) is assessable.
    File ATO Company Tax Return (NAT 0656) if total_assessable_income > 0.
    """
    financial_year: str
    # Mutual income (exempt)
    levy_income_ex_gst: float = 0.0  # admin + sinking levies — EXEMPT
    # Non-mutual income (assessable)
    interest_income: float = 0.0  # trust account bank interest — TAXABLE
    other_non_mutual_income: float = 0.0  # common area rentals, visitor fees — TAXABLE
    total_assessable_income: float = 0.0  # = interest_income + other_non_mutual_income
    # Deductible expenses against assessable income
    management_fees: float = 0.0
    other_deductible_expenses: float = 0.0
    total_deductions: float = 0.0
    net_taxable_income: float = 0.0  # total_assessable_income - total_deductions
    # Metadata
    source_note: str = (
        "Interest income sourced from trust_transactions_v2 (type=interest_income). "
        "Mutual income (levies) is exempt under the mutuality principle. "
        "File ATO Company Tax Return NAT 0656 with your CPA if total_assessable_income > 0."
    )
    items: List[GSTLineItem] = []  # individual trust interest transactions


__all__ = [
    "GSTSettings",
    "GSTLineItem",
    "GSTOutputTaxRow",
    "BASWorksheet",
    "GSTQuarterlySummary",
    "GSTSummary",
    "GSTFreeSection",
    "GSTCategoryBreakdown",
    "GSTInputTaxResponse",
    "GSTQuarterlyWorksheetsResponse",
    "GSTIncomeTaxPrep",
]
