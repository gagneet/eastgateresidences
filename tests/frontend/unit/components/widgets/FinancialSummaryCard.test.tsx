import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';
import FinancialSummaryCard from '@/components/widgets/FinancialSummaryCard';

const BASE_DATA = {
    unit_number: '42',
    admin_fund: {opening_balance: 0, levied: 1530.30, paid: 1530.30, closing_balance: 0},
    sinking_fund: {opening_balance: 0, levied: 500, paid: 500, closing_balance: 0},
    total_levied: 2030.30,
    total_paid: 2030.30,
    net_balance: 0,
    next_due_date: null,
    period_levy: 1530.30,
    period_status: {any_overdue: false, overdue_count: 0, current_due_date: null},
    opening_arrears: 0,
    next_payment_adjusted: 0,
};

function renderCard(overrides: object) {
    return render(<FinancialSummaryCard financialData={{...BASE_DATA, ...overrides}} onPayClick={null} error={null}/>);
}

describe('FinancialSummaryCard — Next Estimated Payment sub-label', () => {
    // ── Priority 1 ──────────────────────────────────────────────────────────
    it('shows "fully paid" when next payment is zero and not overdue', () => {
        renderCard({next_payment_adjusted: 0, period_status: {any_overdue: false}});
        expect(screen.getByText('All levies fully paid for this financial year')).toBeInTheDocument();
    });

    // ── Priority 2 — credit (must fire even when any_overdue is true) ────────
    it('shows credit message when owner has prior-year credit', () => {
        renderCard({
            opening_arrears: -300,
            next_payment_adjusted: 1230.30,
            period_status: {any_overdue: false},
        });
        expect(screen.getByText(/\$300\.00 prior-year credit applied to next payment/)).toBeInTheDocument();
    });

    it('shows credit message even when any_overdue calendar flag is true', () => {
        // Regression: calendar-overdue flag must NOT override a genuine credit balance.
        renderCard({
            opening_arrears: -300,
            next_payment_adjusted: 1230.30,
            period_status: {any_overdue: true},
        });
        expect(screen.getByText(/prior-year credit applied/)).toBeInTheDocument();
        expect(screen.queryByText(/overdue/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/Missed instalment/)).not.toBeInTheDocument();
    });

    // ── Priority 3a — overdue with prior-year arrears ────────────────────────
    it('shows dollar-amount overdue message when prior-year arrears carried in and next > one period', () => {
        // nextAmt = 1530.30 (one period) + 200 (arrears) = 1730.30 → overdueAmt = 200
        // net_balance > 0 so the owner genuinely owes money (Priority 3 guard passes).
        renderCard({
            opening_arrears: 200,
            net_balance: 1730.30,
            next_payment_adjusted: 1730.30,
            period_levy: 1530.30,
            period_status: {any_overdue: true},
        });
        expect(screen.getByText(/\$200\.00 overdue from prior period included/)).toBeInTheDocument();
    });

    it('shows arrears-included fallback when prior arrears but overdueAmt rounds to zero', () => {
        // nextAmt == period_levy so overdueAmt = 0, but oa > 0
        renderCard({
            opening_arrears: 200,
            net_balance: 1530.30,
            next_payment_adjusted: 1530.30,
            period_levy: 1530.30,
            period_status: {any_overdue: true},
        });
        expect(screen.getByText(/\$200\.00 prior-year arrears included in payment/)).toBeInTheDocument();
    });

    // ── Priority 3b — overdue with no prior-year component ───────────────────
    it('shows "Missed instalment" when overdue, genuinely owing, and next amount exceeds one period', () => {
        // After-grace rollup: backend adds Q4 on top of missed Q3 → nextAmt ≈ 2 × period_levy.
        // net_balance > 0 confirms the owner actually owes (not just calendar noise).
        renderCard({
            opening_arrears: 0,
            net_balance: 3060.60,
            next_payment_adjusted: 3060.60,   // 2 × 1530.30
            period_levy: 1530.30,
            period_status: {any_overdue: true},
        });
        expect(screen.getByText(/Missed instalment included — payment overdue/)).toBeInTheDocument();
    });

    // ── Regression — credit owner must NOT see "Missed instalment" ───────────
    it('does NOT show "Missed instalment" when calendar-overdue but net_balance is zero (paid in full)', () => {
        // Reported bug: owner paid prior levy in full → net_balance = 0; any_overdue is
        // calendar noise. Priority 3 must be gated by net_balance > 0.
        renderCard({
            opening_arrears: 0,
            net_balance: 0,
            next_payment_adjusted: 1530.30,
            period_levy: 1530.30,
            period_status: {any_overdue: true},
        });
        expect(screen.queryByText(/Missed instalment/)).not.toBeInTheDocument();
    });

    it('does NOT show "Missed instalment" when calendar-overdue but net_balance is negative (in credit)', () => {
        renderCard({
            opening_arrears: 0,
            net_balance: -200,
            next_payment_adjusted: 1330.30,
            period_levy: 1530.30,
            period_status: {any_overdue: true},
        });
        expect(screen.queryByText(/Missed instalment/)).not.toBeInTheDocument();
    });

    it('does NOT show "Missed instalment" when overdue, owing, but next amount is exactly one period (not yet post-grace rollup)', () => {
        // Owner owes one period but backend has not yet applied the post-grace rollup.
        // nextAmt ≤ pl means no missed instalment is bundled in.
        renderCard({
            opening_arrears: 0,
            net_balance: 1530.30,
            next_payment_adjusted: 1530.30,
            period_levy: 1530.30,
            period_status: {any_overdue: true},
        });
        expect(screen.queryByText(/Missed instalment/)).not.toBeInTheDocument();
    });

    // ── Priority 1 — fully paid even when calendar-overdue ───────────────────
    it('shows "fully paid" when next payment is zero and net_balance is zero even if calendar-overdue', () => {
        renderCard({
            next_payment_adjusted: 0,
            net_balance: 0,
            period_status: {any_overdue: true},
        });
        expect(screen.getByText('All levies fully paid for this financial year')).toBeInTheDocument();
    });

    // ── Priority 4a — prior-year arrears NOT yet calendar-overdue ────────────
    it('shows "prior-year arrears carried forward" when not overdue but oa > 0', () => {
        renderCard({
            opening_arrears: 150,
            next_payment_adjusted: 1680.30,
            period_status: {any_overdue: false},
        });
        expect(screen.getByText(/\$150\.00 prior-year arrears carried forward/)).toBeInTheDocument();
    });

    // ── Priority 4b — in-year advance payment ───────────────────────────────
    it('shows advance payment credited when owner has overpaid current period', () => {
        // paid = 2000, period_levy = 1530.30 → periodsFullyFunded = 1, partialCredit ≈ 469.70
        renderCard({
            opening_arrears: 0,
            total_paid: 2000,
            period_levy: 1530.30,
            next_payment_adjusted: 1060.60,
            period_status: {any_overdue: false},
        });
        expect(screen.getByText(/advance payment credited/)).toBeInTheDocument();
    });

    // ── Priority 4c — current instalments paid on time ──────────────────────
    it('shows "Levy amount paid in full" when owner is current: no arrears, not overdue, next instalment upcoming', () => {
        // Owner paid Q1+Q2 exactly (net_balance = 2×pl, future quarters still owing but
        // nothing is overdue).  This is the normal on-time-paying owner.
        renderCard({
            opening_arrears: 0,
            net_balance: 3060.60,   // 2 × 1530.30 still owing (Q3+Q4 not yet due)
            total_paid: 3060.60,
            period_levy: 1530.30,
            next_payment_adjusted: 1530.30,
            period_status: {any_overdue: false},
        });
        expect(screen.getByText('Levy amount paid in full')).toBeInTheDocument();
    });

    it('does NOT show "Levy amount paid in full" when any_overdue is true', () => {
        // isOverdue guard must block label 4c even if no other label applied.
        renderCard({
            opening_arrears: 0,
            net_balance: 3060.60,
            total_paid: 3060.60,
            period_levy: 1530.30,
            next_payment_adjusted: 1530.30,
            period_status: {any_overdue: true},
        });
        expect(screen.queryByText('Levy amount paid in full')).not.toBeInTheDocument();
    });

    it('does NOT show "Levy amount paid in full" when owner has an advance partial credit', () => {
        // inYearPortion > 0 → "advance payment credited" wins; labels[b] fills first so
        // labels.length > 0 and condition 4c is skipped.
        renderCard({
            opening_arrears: 0,
            net_balance: 1060.60,
            total_paid: 2000,
            period_levy: 1530.30,
            next_payment_adjusted: 1060.60,
            period_status: {any_overdue: false},
        });
        expect(screen.queryByText('Levy amount paid in full')).not.toBeInTheDocument();
        expect(screen.getByText(/advance payment credited/)).toBeInTheDocument();
    });

    // ── No sub-label when nothing special ───────────────────────────────────
    it('renders no sub-label when next payment is one plain period with no adjustments', () => {
        renderCard({
            opening_arrears: 0,
            total_paid: 0,
            period_levy: 1530.30,
            next_payment_adjusted: 1530.30,
            period_status: {any_overdue: false},
        });
        expect(screen.queryByText(/credit|arrears|overdue|advance|Missed/i)).not.toBeInTheDocument();
    });
});
