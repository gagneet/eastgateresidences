export interface AllocationRule {
    allocation_type: "unit_entitlement" | "equal_split" | "usage_based" | "fixed_percentage";
    notes?: string;
}

export interface BenefitGroup {
    id: string;
    building_id: string;
    name: string;
    description?: string;
    lot_numbers: string[];
    allocation_rule: AllocationRule;
}

export interface Zone {
    id: string;
    building_id: string;
    name: string;
    description?: string;
}

export interface Facility {
    id: string;
    building_id: string;
    name: string;
    category: string;
    zone_id?: string;
    benefit_group_id?: string;
    notes?: string;
    health_score: number;
}

export interface BuildingAsset {
    id: string;
    building_id: string;
    name: string;
    category: string;
    facility_id?: string;
    zone_id?: string;
    benefit_group_id?: string;
    installation_date?: string;
    expected_lifespan_years: number;
    replacement_cost_estimate: number;
    maintenance_frequency_months: number;
    last_service_date?: string;
    risk_score: number;
    health_score: number;
    notes?: string;
}

export interface IntelligenceSummary {
    building_id: string;
    asset_health_score: number;
    high_risk_assets_count: number;
    expected_maintenance_12m: number;
    capital_replacement_5y: number;
    levy_risk: "low" | "medium" | "high";
    anomalies_detected_count: number;
    maintenance_anomalies_count?: number;
    attention_needed?: MaintenanceAttention[];
    upcoming_capital_items?: CapitalWorkItem[];
    capital_shock_risk?: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | null;
    capital_shock_recommendation?: string | null;
    levy_fairness_score?: number | null;
}

/** One month of the served maintenance forecast. */
export interface MaintenanceForecastMonth {
    /** 1-12. */
    month: number;
    /** Short month name, e.g. "Jul". */
    label: string;
    predicted_cost: number;
}

/**
 * How the annual forecast was split across twelve months.
 *
 * `historical_seasonality` — measured from this building's own completed work
 *   orders, so the shape means something.
 * `even_spread` — too little history to see a pattern; the annual figure divided
 *   by twelve. A stated assumption, and the UI must say so rather than let a flat
 *   line read as a prediction.
 */
export type MaintenanceMonthlyBasis = "historical_seasonality" | "even_spread";

/** One completed financial year of ACTUAL maintenance spend, from the building's
 *  own imported category actuals. Real money, not a model. */
export interface MaintenanceSpendYear {
    /** Financial-year label as stored, e.g. "2024". */
    year: string;
    actual_cost: number;
    /** How many expense categories contributed — a data-quality signal. */
    category_count: number;
}

export interface MaintenanceForecast {
    building_id: string;
    year: number;
    predicted_repairs_count: number;
    /**
     * The ASSET-RISK model: 2% of each asset's replacement cost, scaled by risk
     * score. It answers "what do the assets imply?" and is NOT anchored to what
     * the building has actually spent — for East Gate it reads $26,765/yr against
     * $147k-$191k of real maintenance spend. Always show it alongside
     * `history_based_forecast` rather than on its own.
     */
    predicted_cost: number;
    high_risk_assets: string[];
    confidence_score?: number;
    /** Absent (not an array of zeros) when there is no annual figure to distribute. */
    monthly_breakdown?: MaintenanceForecastMonth[] | null;
    monthly_basis?: MaintenanceMonthlyBasis;
    monthly_sample_size?: number;
    /** Actual spend per year. Empty array = no history; render that differently
     *  from a history of zero. */
    annual_history?: MaintenanceSpendYear[];
    /** Trailing mean over COMPLETED years only, or null when there are fewer than
     *  three. Null means "not enough history", never "zero expected". */
    history_based_forecast?: number | null;
    /** Human-readable statement of what `history_based_forecast` averaged. */
    history_forecast_basis?: string | null;
}

export interface CapitalWorkItem {
    building_id: string;
    asset_id: string;
    asset_name: string;
    replacement_year: number;
    estimated_cost: number;
}

export interface MaintenanceAttention {
    asset_id: string;
    asset_name: string;
    anomaly_type: string;
    repairs_last_12m: number;
    repair_cost_last_12m: number;
    repair_to_replacement_ratio: number;
    recommendation: string;
    last_repair_date?: string;
}

export interface LevyFairnessResult {
    building_id: string;
    computed_at: string;
    current_fairness_score: number;
    benefit_fairness_score: number;
    impact_by_group: Array<{
        group: string;
        current_total: number;
        benefit_total: number;
        change_pct: number;
    }>;
    current_distribution: {
        total: number;
        groups: Array<{ group: string; total: number; share: number }>;
    };
    benefit_distribution: {
        total: number;
        groups: Array<{ group: string; total: number; share: number }>;
        facility_breakdown: Array<{
            facility_id: string;
            facility_name: string;
            benefit_group_id?: string;
            annual_cost: number;
            cost_components: { maintenance: number; capital: number; historical: number };
        }>;
    };
}

export interface CapitalShockRisk {
    building_id: string;
    computed_at: string;
    risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    /**
     * Null when the building has no financial baseline (no annual_levies document, or
     * no sinking_fund.closing_balance on it). The backend deliberately does NOT
     * substitute a default — it used to return a hardcoded 150000, which rendered as a
     * real balance beside $0 income and $0 expenses.
     *
     * Branch on `reserve_balance_available` before formatting: never show $0.00 for
     * this, because zero and unknown are different states.
     */
    reserve_balance: number | null;
    /** False => `reserve_balance` is unknown and must not be rendered as a dollar figure. */
    reserve_balance_available?: boolean;
    plan_source?: string;
    plan_start_year?: number | null;
    plan_end_year?: number | null;
    capital_events?: Array<{
        year: number;
        label: string;
        description?: string;
        amount: number;
        severity: string;
    }>;
    reserve_target_months?: number;
    annual_expenses?: number;
    reserve_projection: Array<{
        year: number;
        reserve_before_capital: number;
        capital_cost: number;
        reserve_after_capital: number;
    }>;
    capital_shock_index?: {
        baseline_spend: number;
        next_shock_year?: number | null;
        next_shock?: {
            year: number;
            capital_spend: number;
            baseline_spend: number;
            capital_shock_index: number;
            risk_level: string;
            shock_category: string;
        } | null;
        rows: Array<{
            year: number;
            capital_spend: number;
            baseline_spend: number;
            capital_shock_index: number;
            risk_level: string;
            shock_category: string;
        }>;
    };
    reserve_collapse?: {
        start_year: number;
        initial_reserve: number;
        collapse_year?: number | null;
        years_to_collapse?: number | null;
        risk_level: string;
        risk_label: string;
        minimum_balance: number;
        minimum_balance_year?: number | null;
        timeline: Array<{
            year: number;
            opening_balance: number;
            contribution: number;
            expenditure: number;
            closing_balance: number;
            is_actual?: boolean;
            is_major_capital_year?: boolean;
        }>;
    };
    funding_adequacy?: {
        sfar?: number | null;
        status: string;
        status_label: string;
        current_reserve: number;
        future_contributions: number;
        capital_required: number;
        available_funding: number;
        funding_gap: number;
        timeline: Array<{
            year: number;
            reserve_balance: number;
            remaining_capital: number;
            sfar?: number | null;
            status: string;
            status_label: string;
        }>;
    };
    risks: Array<{
        year: number;
        projected_reserve: number;
        deficit: number;
        capital_items: CapitalWorkItem[];
    }>;
    max_deficit: number;
    recommended_levy_increase_pct?: number | null;
    recommendation?: string | null;
}

export interface LevySimulationProjection {
    year: number;
    levy_required: number;
    operating_cost: number;
    capital_expenditure: number;
    projected_reserve: number;
    levy_increase_pct: number;
}

export interface LevySimulationResult {
    building_id: string;
    recommendation: string;
    projections: LevySimulationProjection[];
}

export interface SpecialLevyForecast {
    forecast_id: string;
    building_id?: string;
    year: number;
    probability: number;
    median_amount: number;
    p90_amount: number;
    p95_amount: number;
    worst_case: number;
    simulation_runs: number;
    generated_at: string;
    horizon_years: number;
    shock_year_distribution: Record<string, number>;
    reserve_fan: Array<{
        year: number;
        p50: number;
        p75: number;
        p90: number;
    }>;
    per_unit_distribution: number[];
    thresholds: Record<string, number>;
    group_summary: Array<{
        group: string;
        unit_count: number;
        probability: number;
        median_amount: number;
        p90_amount: number;
        p95_amount: number;
        worst_case: number;
    }>;
    explanation?: string | null;
}

export interface LevyStabilitySnapshot {
    id: string;
    building_id?: string;
    year: number;
    reserve_score: number;
    shock_score: number;
    volatility_score: number;
    funding_score: number;
    levy_stability_score: number;
    generated_at: string;
    horizon_years: number;
    components: Record<string, number>;
    group_scores: Array<{
        group: string;
        levy_stability_score: number;
        reserve_score: number;
        shock_score: number;
        volatility_score: number;
        funding_score: number;
    }>;
    explanation?: string | null;
}
