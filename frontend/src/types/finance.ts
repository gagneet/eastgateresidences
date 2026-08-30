export interface AnnualFundSummary {
    levy_income: number;
    other_income: number;
    total_income: number;
    total_expenses: number;
    opening_balance: number;
    closing_balance: number;
    surplus_deficit: number;
}

export interface PaymentScheduleEntry {
    quarter: string;
    due_date: string;
}

export interface AnnualLevyResponse {
    id: string;
    year: string;
    status: "proposed" | "actual";
    plan_id: string;
    total_uoe: number;
    admin_fund: AnnualFundSummary;
    sinking_fund: AnnualFundSummary;
    payment_schedule: PaymentScheduleEntry[];
    admin_levy_per_uoe_annual: number;
    admin_levy_per_uoe_quarterly: number;
    sinking_levy_per_uoe_annual: number;
    sinking_levy_per_uoe_quarterly: number;
    created_at: string;
    updated_at: string;
}

export interface LevyCategoryResponse {
    id: string;
    year: string;
    fund_type: "administrative" | "sinking";
    name: string;
    budgeted_amount: number;
    actual_amount: number;
    description?: string | null;
    plan_id: string;
    status: "proposed" | "actual";
    created_at: string;
    updated_at: string;
}

export interface UnitLevyLedgerResponse {
    id: string;
    plan_id: string;
    year: string;
    unit_number: string;
    lot_number: string;
    uoe: number;
    property_type: string;
    admin_opening: number;
    admin_levied: number;
    admin_paid: number;
    admin_closing: number;
    sinking_opening: number;
    sinking_levied: number;
    sinking_paid: number;
    sinking_closing: number;
    total_levied: number;
    total_paid: number;
    net_balance: number;
    special_levy_ids: string[];
    created_at: string;
    updated_at: string;
}

export interface LevyPaymentResponse {
    id: string;
    unit_number: string;
    amount: number;
    payment_method: string;
    payment_reference?: string | null;
    quarter: string;
    year: string;
    fund_type?: "administrative" | "sinking" | null;
    payment_type?: "standard" | "partial" | "advance" | null;
    credit_amount?: number | null;
    status: "pending_verification" | "confirmed" | "rejected" | "failed";
    notes?: string | null;
    receipt_number: string;
    paid_by?: string | null;
    confirmed_by?: string | null;
    confirmed_at?: string | null;
    rejected_by?: string | null;
    rejected_at?: string | null;
    rejection_reason?: string | null;
    created_at: string;
}

export interface ExpenseTransactionResponse {
    id: string;
    plan_id: string;
    financial_year: string;
    date: string;
    amount: number;
    category_id: string;
    description?: string | null;
    fund_type: "administrative" | "sinking";
    supplier_name: string;
    invoice_number?: string | null;
    is_gst_inclusive: boolean;
    gst_amount: number;
    asset_id?: string | null;
    facility_id?: string | null;
    zone_id?: string | null;
    benefit_group_id?: string | null;
    created_at: string;
    updated_at: string;
    created_by: string;
}
