"""
Monte Carlo Simulation Engine — Predictive Risk Modeling

Runs simulations with specified distributions for:
  - Inflation (Triangular: 2%, 3%, 5%)
  - Maintenance Spikes (Lognormal: 1.0, 0.25)
  - Capital Works Overruns (Triangular: 1.0, 1.15, 1.40)
  - Insurance Premium Growth (Normal: 6%, 3%)
"""
import random
from datetime import datetime

import numpy as np
from typing import Dict, Any, List, Optional


def run_monte_carlo_levy_simulation(
        annual_budget: float,
        current_reserve: float,
        capital_schedule_10y: List[Dict[str, float]],
        num_simulations: int = 1000,
        years: int = 10
) -> Dict[str, Any]:
    """
    Runs Monte Carlo simulation to predict the probability of a reserve deficit.
    Returns: P50, P90, P95 values and special levy probability.
    """
    results = []
    deficits = 0
    base_year = datetime.now().year

    for _ in range(num_simulations):
        reserve = current_reserve
        sim_budget = annual_budget
        sim_failed = False

        for year_idx in range(1, years + 1):
            # 1. Apply Inflation (Triangular 2, 3, 5%)
            inflation = random.triangular(0.02, 0.05, 0.03)
            sim_budget *= (1 + inflation)

            # 2. Maintenance Spikes (Lognormal: mean 1.0, sigma 0.25)
            # Portions of budget for maintenance
            maint_multiplier = np.random.lognormal(mean=0, sigma=0.25)

            # 3. Capital Works Overruns (Triangular 1.0, 1.15, 1.40)
            cap_overrun = random.triangular(1.0, 1.40, 1.15)

            # 4. Insurance Premium Growth (Normal: mean 6%, std dev 3%)
            insurance_growth = np.random.normal(0.06, 0.03)

            # Extract cap works for this year
            target_year = base_year + year_idx
            cap_cost_this_year = sum(
                item.get("estimated_cost", 0)
                for item in capital_schedule_10y
                if item.get("replacement_year") == target_year
            )

            # Expenses = Admin (inc insurance) + Maint + Capital
            maint_portion = sim_budget * 0.35 * maint_multiplier
            admin_portion = sim_budget * 0.65 * (1 + insurance_growth)

            expenses = admin_portion + maint_portion + (cap_cost_this_year * cap_overrun)

            # Income = sim_budget
            # Reserve_new = Reserve_old + Income - Expenses
            reserve = reserve + sim_budget - expenses

            if reserve < 0:
                sim_failed = True

        results.append(reserve)
        if sim_failed:
            deficits += 1

    results.sort()
    # Percentiles: low-reserve tail
    p50 = results[int(num_simulations * 0.50)]
    p90 = results[int(num_simulations * 0.90)]
    p95 = results[int(num_simulations * 0.95)]

    return {
        "p50": p50,
        "p90": p90,
        "p95": p95,
        "special_levy_probability": round((deficits / num_simulations) * 100, 1),
        "num_simulations": num_simulations,
        "distribution": results[::max(1, num_simulations // 50)]  # sample ~50 points for charts
    }


def run_special_levy_simulation(
        annual_budget: float,
        current_reserve: float,
        capital_schedule_by_year: Dict[int, float],
        allocation_shares_by_year: Dict[int, Dict[str, float]],
        group_units: Dict[str, List[str]],
        num_simulations: int = 1000,
        years: int = 10,
        base_year: Optional[int] = None,
        loan: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Monte Carlo simulation for special levy prediction with timing and per-unit distributions.
    """
    if base_year is None:
        base_year = datetime.now().year

    reserve_by_year: Dict[int, List[float]] = {}
    shock_year_counts: Dict[int, int] = {}
    per_unit_amounts: List[float] = []
    total_shock_amounts: List[float] = []
    group_amounts: Dict[str, List[float]] = {g: [] for g in group_units}

    loan_amount = float((loan or {}).get("amount", 0) or 0)
    loan_rate = float((loan or {}).get("interest_rate", 0) or 0)
    loan_term = int((loan or {}).get("term_years", 0) or 0)
    annual_payment = 0.0
    if loan_amount > 0 and loan_term > 0:
        if loan_rate > 0:
            r = loan_rate
            annual_payment = loan_amount * (r * (1 + r) ** loan_term) / ((1 + r) ** loan_term - 1)
        else:
            annual_payment = loan_amount / loan_term

    for _ in range(num_simulations):
        reserve = current_reserve + (loan_amount if loan_amount > 0 else 0)
        sim_budget = annual_budget
        shock_year = None
        shock_amount = 0.0

        for year_idx in range(1, years + 1):
            year = base_year + year_idx
            reserve_by_year.setdefault(year, [])

            inflation = random.triangular(0.02, 0.05, 0.03)
            sim_budget *= (1 + inflation)

            maint_multiplier = np.random.lognormal(mean=0, sigma=0.25)
            cap_overrun = random.triangular(1.10, 1.40, 1.20)
            insurance_growth = max(0.0, np.random.normal(0.06, 0.03))
            utility_growth = max(0.0, np.random.normal(0.04, 0.02))

            cap_cost = capital_schedule_by_year.get(year, 0) * cap_overrun
            maint_portion = sim_budget * 0.35 * maint_multiplier
            admin_portion = sim_budget * 0.65 * (1 + insurance_growth + utility_growth)

            expenses = admin_portion + maint_portion + cap_cost
            if annual_payment > 0 and year_idx <= loan_term:
                expenses += annual_payment

            reserve = reserve + sim_budget - expenses
            reserve_by_year[year].append(reserve)

            if shock_year is None and reserve < 0:
                shock_year = year
                shock_amount = abs(reserve)

        total_shock_amounts.append(shock_amount if shock_year else 0.0)
        if shock_year:
            shock_year_counts[shock_year] = shock_year_counts.get(shock_year, 0) + 1

        share_map = allocation_shares_by_year.get(shock_year, {}) if shock_year else {}
        if share_map:
            median_share = np.median(list(share_map.values()))
        else:
            median_share = 0.0

        per_unit_amounts.append(shock_amount * median_share if shock_year else 0.0)

        for group, units in group_units.items():
            if not shock_year or not share_map:
                group_amounts[group].append(0.0)
                continue
            group_shares = [share_map.get(u) for u in units if u in share_map]
            if group_shares:
                group_median_share = np.median(group_shares)
                group_amounts[group].append(shock_amount * group_median_share)
            else:
                group_amounts[group].append(0.0)

    reserve_fan = []
    for year, values in reserve_by_year.items():
        if values:
            reserve_fan.append({
                "year": year,
                "p50": float(np.percentile(values, 50)),
                "p75": float(np.percentile(values, 75)),
                "p90": float(np.percentile(values, 90)),
            })
    reserve_fan.sort(key=lambda x: x["year"])

    return {
        "total_shock_amounts": total_shock_amounts,
        "per_unit_amounts": per_unit_amounts,
        "shock_year_counts": shock_year_counts,
        "reserve_fan": reserve_fan,
        "group_amounts": group_amounts,
    }
