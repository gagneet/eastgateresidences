"use client";
// @ts-nocheck
// @featuretrace:levy — Per-unit levy ledger drill-down: opening arrears, total paid, balance due,
//                      DCA/notice/payment-plan status.
// Layer: frontend
// Data flow: this page -> GET /owners-units/{unit}, GET /levy-status/{unit}?year=, GET /levy-payments
//            -> finance.py -> unit_levy_ledger + levy_payments (building-scoped).
// Related: backend/routers/finance.py
//           frontend/src/pages/dashboard/FinancePage.tsx
//           frontend/src/pages/dashboard/CollectionRatePage.jsx
// Collection: unit_levy_ledger, levy_payments
import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger, } from '../../components/ui/tabs';
import {
    AlertTriangle,
    ArrowLeft,
    CheckCircle2,
    CreditCard,
    Droplets,
    FileText,
    History,
    Landmark,
    Loader2,
    Scale,
    ShieldCheck,
} from 'lucide-react';
import { formatCurrency } from '../../lib/utils';
import YearSelector from '../../components/widgets/YearSelector';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const statusBadge = (status) => {
    const map = {
        paid: <Badge className="text-xs bg-green-100 text-green-800 border-green-200">Paid</Badge>,
        pending: <Badge className="text-xs bg-blue-100 text-blue-800 border-blue-200">Pending</Badge>,
        overdue: <Badge variant="destructive" className="text-xs">Overdue</Badge>,
        partial: <Badge className="text-xs bg-amber-100 text-amber-800 border-amber-200">Partial</Badge>,
        confirmed: <Badge className="text-xs bg-green-100 text-green-800 border-green-200">Confirmed</Badge>,
        rejected: <Badge className="text-xs bg-red-100 text-red-800 border-red-200">Rejected</Badge>,
        unpaid: <Badge variant="outline" className="text-xs text-red-700 border-red-200">Unpaid</Badge>,
    };
    return map[ status ] || <Badge variant="secondary" className="text-xs">{status}</Badge>;
};

const CURRENT_FY = new Date().getFullYear();
const LEVY_YEARS = Array.from({length: CURRENT_FY - 2021 + 1}, (_, i) => String(2021 + i));
// ─── Overview Tab ─────────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: OverviewTab
 * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const OverviewTab = ({unitInfo, levyStatus, levyYear, setLevyYear, handleSendNotice, handleReferDCA, canManage}) => {
    // GAP-FIN-014: operational balance fields must come from /levy-status ledger
    // fields only — no silent fallback to /owners-units (unitInfo), which is a
    // non-canonical mirror that can go stale. When the selected year has no
    // ledger row, show an explicit data-quality warning instead of stale numbers.
    const hasLedgerData = !!levyStatus && !levyStatus.error && levyStatus.ledger != null;
    const openingArrears = hasLedgerData ? ( levyStatus?.opening_arrears ?? Math.max(0, levyStatus?.prev_year_balance ?? 0) ) : 0;
    // Year-scoped paid (current levy year) — retained for the "Paid This Year" derivation.
    const totalPaid = hasLedgerData ? ( levyStatus?.total_paid ?? 0 ) : 0;
    // Lifetime cumulative paid across ALL levy years — what the "Total Paid" tile shows.
    // Falls back to the year-scoped figure for older backends that don't return it yet.
    const totalPaidAllYears = hasLedgerData ? ( levyStatus?.total_paid_all_years ?? totalPaid ) : 0;
    const paidThisYear = hasLedgerData ? ( levyStatus?.paid_this_year ?? totalPaid ) : 0;
    const annualLevy = hasLedgerData ? ( levyStatus?.annual_levy ?? 0 ) : 0;
    // GAP-FIN-047 item (a): "Balance Due" must reflect the authoritative net_balance-based
    // owing/credit (balance_owing/balance_credit), NOT the legacy full-annual balance_due —
    // which overstated mid-year obligations and showed a credit-ahead unit as owing. Fall
    // back to the legacy fields only for older backends that don't return the new ones.
    const legacyBalanceDue = hasLedgerData ? ( levyStatus?.balance_due ?? 0 ) : 0;
    const legacyCreditBalance = hasLedgerData ? ( levyStatus?.credit_balance ?? 0 ) : 0;
    const balanceOwing = hasLedgerData ? ( levyStatus?.balance_owing ?? legacyBalanceDue ) : 0;
    const balanceCredit = hasLedgerData ? ( levyStatus?.balance_credit ?? legacyCreditBalance ) : 0;
    const inCredit = balanceCredit > 0.005;

    return (
        <div className="space-y-6">
            {!hasLedgerData && (
                <div
                    data-testid="ledger-missing-warning"
                    className="flex items-start gap-2 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-amber-800"
                >
                    <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5"/>
                    <div className="text-sm">
                        <p className="font-semibold">No ledger data for FY {levyYear}</p>
                        <p className="mt-1">
                            The figures below are unavailable for this year — the levy ledger has no
                            record for this unit yet. This is not the same as a nil balance; select a
                            different year or check with your strata manager.
                        </p>
                    </div>
                </div>
            )}
            {/* Recovery Status Bar */}
            {canManage && (
                <div className="flex items-center justify-between p-4 rounded-2xl border bg-white shadow-sm">
                    <div className="flex items-center gap-6">
                        <div>
                            <p className="text-[10px] font-black uppercase text-slate-400 mb-1">DCA Status</p>
                            <Badge className={`${
                                unitInfo?.arrears_metadata?.dca_status === 'referred' ? 'bg-amber-100 text-amber-700' :
                                    unitInfo?.arrears_metadata?.dca_status === 'eligible' ? 'bg-rose-100 text-rose-700' :
                                        'bg-slate-100 text-slate-500'
                            } border-none`}>
                                {unitInfo?.arrears_metadata?.dca_status?.toUpperCase() || 'NONE'}
                            </Badge>
                        </div>
                        <div>
                            <p className="text-[10px] font-black uppercase text-slate-400 mb-1">Notice Sent</p>
                            <p className="text-sm font-bold text-slate-700">
                                {unitInfo?.arrears_metadata?.first_notice_sent_at ?
                                    new Date(unitInfo.arrears_metadata.first_notice_sent_at).toLocaleDateString('en-AU') :
                                    'Not Sent'}
                            </p>
                        </div>
                        <div>
                            <p className="text-[10px] font-black uppercase text-slate-400 mb-1">Payment Plan</p>
                            {unitInfo?.arrears_metadata?.has_active_payment_plan ? (
                                <Badge className="bg-emerald-100 text-emerald-700 border-none">ACTIVE</Badge>
                            ) : (
                                <Badge variant="outline" className="text-slate-400">NONE</Badge>
                            )}
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button size="sm" variant="outline" onClick={() => handleSendNotice()}
                                className="h-8 text-xs font-bold gap-1">
                            <FileText size={14}/> Send Notice
                        </Button>
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={unitInfo?.arrears_metadata?.dca_status === 'referred'}
                            onClick={() => handleReferDCA()}
                            className="h-8 text-xs font-bold gap-1 text-rose-600 border-rose-100 hover:bg-rose-50"
                        >
                            <Scale size={14}/> Refer DCA
                        </Button>
                    </div>
                </div>
            )}

            {/* Unit summary */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div className="p-4 rounded-2xl border bg-indigo-50">
                    <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Annual Levy</p>
                    <p className="text-xl font-black text-slate-900">{hasLedgerData ? formatCurrency(annualLevy) : 'No data'}</p>
                    <p className="text-xs text-slate-500">FY {levyYear}</p>
                </div>
                <div className="p-4 rounded-2xl border bg-amber-50">
                    <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Opening Arrears</p>
                    <p className={`text-xl font-black ${!hasLedgerData ? 'text-slate-400' : openingArrears > 0 ? 'text-rose-600' : openingArrears < 0 ? 'text-emerald-600' : 'text-slate-400'}`}>
                        {hasLedgerData ? formatCurrency(Math.abs(openingArrears)) : 'No data'}
                    </p>
                    <p className="text-xs text-slate-500">{!hasLedgerData ? 'No ledger row for this year' : openingArrears > 0 ? 'Prior levy-year debt carried into this year' : openingArrears < 0 ? 'Credit carried into this year' : 'No prior-year carry-forward'}</p>
                </div>
                <div className="p-4 rounded-2xl border bg-emerald-50">
                    <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Total Paid</p>
                    <p className="text-xl font-black text-emerald-700">{hasLedgerData ? formatCurrency(totalPaidAllYears) : 'No data'}</p>
                    <p className="text-xs text-slate-500">Cumulative paid across all levy years</p>
                </div>
                <div className="p-4 rounded-2xl border bg-teal-50">
                    <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Paid This Year</p>
                    <p className="text-xl font-black text-teal-700">{hasLedgerData ? formatCurrency(paidThisYear) : 'No data'}</p>
                    <p className="text-xs text-slate-500">Applied to FY {levyYear} levy, excluding credit</p>
                </div>
                <div
                    className={`p-4 rounded-2xl border-2 ${!hasLedgerData ? 'bg-slate-50 border-slate-200' : balanceOwing > 0.005 ? 'bg-rose-50 border-rose-200' : 'bg-green-50 border-green-200'}`}>
                    <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">Balance Due</p>
                    <p className={`text-xl font-black ${!hasLedgerData ? 'text-slate-400' : balanceOwing > 0.005 ? 'text-rose-600' : 'text-green-600'}`}>
                        {!hasLedgerData ? 'No data' : balanceOwing > 0.005 ? formatCurrency(balanceOwing) : inCredit ? formatCurrency(balanceCredit) : 'Nil'}
                    </p>
                    <p className="text-xs text-slate-500">
                        {!hasLedgerData ? 'No ledger row for this year' : balanceOwing > 0.005 ? 'Outstanding' : inCredit ? 'In credit' : 'All paid up'}
                    </p>
                </div>
            </div>

            {/* Owner-transfer paid split — shown ONLY when a previous owner actually
                contributed to this FY's receipts (i.e. a mid-year ownership transfer
                happened). FY-scoped by-owner receipts from Postgres finance.receipts;
                deliberately distinct from the cumulative "Total Paid" tile above, so it
                is never presented as a breakdown of that lifetime figure. Backend returns
                null (not 0) when the split is unknown — the `> 0.01` guard also suppresses
                the card for the no-transfer / unknown cases. */}
            {hasLedgerData && (levyStatus?.paid_by_previous_owners ?? 0) > 0.01 && (
                <div className="p-4 rounded-2xl border border-sky-200 bg-sky-50/60">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <p className="text-xs font-black uppercase tracking-widest text-sky-700">Payments This Year by Owner</p>
                        {levyStatus?.current_owner_since && (
                            <span className="text-[10px] text-slate-500">
                                Current owner since {new Date(levyStatus.current_owner_since).toLocaleDateString('en-AU', {day: 'numeric', month: 'short', year: 'numeric'})}
                            </span>
                        )}
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-4">
                        <div>
                            <p className="text-[10px] text-slate-500">Paid by current owner</p>
                            <p className="font-black text-slate-800">{formatCurrency(levyStatus.paid_by_current_owner || 0)}</p>
                        </div>
                        <div>
                            <p className="text-[10px] text-slate-500">Paid by previous owner(s)</p>
                            <p className="font-black text-slate-800">{formatCurrency(levyStatus.paid_by_previous_owners || 0)}</p>
                        </div>
                    </div>
                    <p className="text-[10px] text-slate-500 mt-2">
                        Levy receipts recorded in FY {levyYear}, split by who held title when each was paid —
                        separate from the cumulative &ldquo;Total Paid&rdquo; above.
                    </p>
                </div>
            )}

            {/* GAP-FIN-054: LIFETIME (all-years) split of the cumulative "Total Paid".
                Shown only when a previous owner actually contributed over the unit's life
                (a real ownership transfer). current + previous == the "Total Paid" tile to
                the cent (PG supplies the tenure-based attribution ratio; the magnitude is the
                Total Paid figure). Backend returns null when the split is unknown (PG
                unavailable/unpromoted, no ownership_periods row) — the `> 0.01` guard also
                hides it for no-transfer units. Distinct from the FY card above. */}
            {hasLedgerData && (levyStatus?.paid_by_previous_owners_lifetime ?? 0) > 0.01 && (
                <div className="p-4 rounded-2xl border border-indigo-200 bg-indigo-50/60">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <p className="text-xs font-black uppercase tracking-widest text-indigo-700">Lifetime Payments by Owner</p>
                        {levyStatus?.lifetime_split_since && (
                            <span className="text-[10px] text-slate-500">
                                Current owner since {new Date(levyStatus.lifetime_split_since).toLocaleDateString('en-AU', {day: 'numeric', month: 'short', year: 'numeric'})}
                            </span>
                        )}
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-4">
                        <div>
                            <p className="text-[10px] text-slate-500">Paid by current owner</p>
                            <p className="font-black text-slate-800">{formatCurrency(levyStatus.paid_by_current_owner_lifetime || 0)}</p>
                        </div>
                        <div>
                            <p className="text-[10px] text-slate-500">Paid by previous owner(s)</p>
                            <p className="font-black text-slate-800">{formatCurrency(levyStatus.paid_by_previous_owners_lifetime || 0)}</p>
                        </div>
                    </div>
                    <p className="text-[10px] text-slate-500 mt-2">
                        Cumulative levy receipts across all years (Admin + Sinking + GST), split by who held title
                        when each was paid. Together these equal the &ldquo;Total Paid&rdquo; of {formatCurrency(totalPaidAllYears)}.
                    </p>
                </div>
            )}

            {/* Estimated arrears interest + late fees (computed when no actual interest
                transactions exist — e.g. East Gate's nil-rate + $55/14-day late-fee model). */}
            {hasLedgerData && ( ( levyStatus?.computed_interest || 0 ) > 0.01 || ( levyStatus?.computed_penalty || 0 ) > 0.01 ) && (
                <div className="p-4 rounded-2xl border border-amber-200 bg-amber-50/60">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <p className="text-xs font-black uppercase tracking-widest text-amber-700">Estimated Interest &amp; Late Fees</p>
                        <span className="text-[10px] text-amber-600">Computed — no interest transactions on record</span>
                    </div>
                    <div className="mt-2 grid grid-cols-3 gap-4">
                        <div>
                            <p className="text-[10px] text-slate-500">Interest</p>
                            <p className="font-black text-slate-800">{formatCurrency(levyStatus.computed_interest || 0)}</p>
                        </div>
                        <div>
                            <p className="text-[10px] text-slate-500">Late fees</p>
                            <p className="font-black text-slate-800">{formatCurrency(levyStatus.computed_penalty || 0)}</p>
                        </div>
                        <div>
                            <p className="text-[10px] text-slate-500">Total (est.)</p>
                            <p className="font-black text-amber-700">{formatCurrency(levyStatus.computed_charges_total || 0)}</p>
                        </div>
                    </div>
                    <p className="text-[10px] text-slate-500 mt-2">
                        Estimated from overdue periods and this building's interest/late-fee policy —
                        for guidance only, not a posted charge.
                    </p>
                </div>
            )}

            {/* Period status */}
            {levyStatus?.period_status && (
                <div className="p-4 rounded-xl border bg-slate-50">
                    <p className="text-xs font-bold text-slate-500 mb-3">Period Status</p>
                    <div className="flex flex-wrap gap-3">
                        {levyStatus.period_status.any_overdue ? (
                            <Badge variant="destructive" className="gap-1"><AlertTriangle size={12}/> Overdue periods
                                detected</Badge>
                        ) : (
                            <Badge className="bg-green-100 text-green-800 gap-1"><CheckCircle2 size={12}/> No overdue
                                periods</Badge>
                        )}
                        {levyStatus.period_status.grace_period_days > 0 && (
                            <Badge variant="outline" className="bg-blue-50 text-blue-700">
                                {levyStatus.period_status.grace_period_days}-day grace period applies
                            </Badge>
                        )}
                    </div>
                </div>
            )}

            {/* Strata Web portal cross-check — shown only when portal snapshot exists */}
            {levyStatus?.portal_balance != null && (
                <div className="p-4 rounded-xl border border-violet-100 bg-violet-50/40">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                        <p className="text-xs font-bold text-violet-700">Strata Web Portal Balance (Cross-check)</p>
                        {levyStatus.portal_synced_at && (
                            <p className="text-[10px] text-slate-400">
                                Synced {new Date(levyStatus.portal_synced_at).toLocaleDateString('en-AU', {
                                day: 'numeric',
                                month: 'short',
                                year: 'numeric'
                            })}
                            </p>
                        )}
                    </div>
                    <div className="mt-2 flex items-baseline gap-3">
                        <p className={`text-lg font-black ${
                            levyStatus.portal_balance > 0 ? 'text-rose-600' :
                                levyStatus.portal_balance < 0 ? 'text-emerald-600' :
                                    'text-slate-500'
                        }`}>
                            {levyStatus.portal_balance > 0
                                ? formatCurrency(levyStatus.portal_balance)
                                : levyStatus.portal_balance < 0
                                    ? `${formatCurrency(Math.abs(levyStatus.portal_balance))} CR`
                                    : 'Clear'}
                        </p>
                        {levyStatus.portal_status && (
                            <Badge className={`text-[10px] border-none ${
                                levyStatus.portal_status === 'ARREARS' ? 'bg-rose-100 text-rose-700' :
                                    levyStatus.portal_status === 'CREDIT' ? 'bg-emerald-100 text-emerald-700' :
                                        'bg-slate-100 text-slate-600'
                            }`}>{levyStatus.portal_status}</Badge>
                        )}
                    </div>
                    <p className="text-[10px] text-slate-400 mt-1">
                        Informational only — does not replace levy system balance above
                    </p>
                </div>
            )}

            {/* Owner info */}
            {unitInfo && (
                <div className="p-4 rounded-xl border">
                    <p className="text-xs font-bold text-slate-500 mb-3">Unit Details</p>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
                        <div>
                            <p className="text-xs text-slate-400">{unitInfo.owner_name_b ? 'Owners' : 'Owner'}</p>
                            <p className="font-semibold">
                                {[unitInfo.owner_name, unitInfo.owner_name_b].filter(Boolean).join(' & ') || '—'}
                            </p>
                        </div>
                        <div><p className="text-xs text-slate-400">Property Type</p><p
                            className="font-semibold capitalize">{unitInfo.property_type || unitInfo.unit_type || '—'}</p>
                        </div>
                        <div><p className="text-xs text-slate-400">Entitlement (UOE)</p><p
                            className="font-semibold">{unitInfo.unit_entitlement || '—'}</p></div>
                        <div><p className="text-xs text-slate-400">Bedrooms</p><p
                            className="font-semibold">{unitInfo.bedrooms || '—'}</p></div>
                        <div><p className="text-xs text-slate-400">Parking</p><p
                            className="font-semibold">{unitInfo.garage_spaces ?? unitInfo.parking_spaces ?? '—'}</p>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
// ─── Levy History Tab ─────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: LevyHistoryTab
 * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const LevyHistoryTab = ({unitNumber, api, currentLevyStatus, levyYear, levyYears}) => {
    const [ledgers, setLedgers] = useState({});
    const [loading, setLoading] = useState(true);
    const [expandedYear, setExpandedYear] = useState(null);
    const yearsList = (Array.isArray(levyYears) && levyYears.length) ? levyYears : LEVY_YEARS;

    useEffect(() => {
        if (!unitNumber) return;
        setLoading(true);
        Promise.allSettled(yearsList.map(yr =>
            api.get(`/unit-levy-ledger/${unitNumber}?year=${yr}`)
        )).then(results => {
            const map = {};
            results.forEach((r, i) => {
                if (r.status === 'fulfilled' && r.value.data && !r.value.data.error) {
                    // API returns an array when year param is specified — extract first element
                    const entry = Array.isArray(r.value.data) ? r.value.data[ 0 ] : r.value.data;
                    if (entry) map[ yearsList[ i ] ] = entry;
                }
            });
            setLedgers(map);
        }).finally(() => setLoading(false));
    }, [unitNumber, api, yearsList.join(',')]);

    if (loading) return <div className="space-y-3">{[...Array(3)].map((_, i) => <div key={i}
                                                                                     className="h-20 bg-slate-50 animate-pulse rounded-xl"/>)}</div>;

    // A year's opening balance should carry forward from the prior year's closing. Where it does
    // NOT (a "chain break" — e.g. a current year whose opening was back-solved from a portal
    // snapshot), the row's closing genuinely cannot be reconciled from opening+levied+interest−paid,
    // and any "implied interest" for it would just be absorbing that gap. Detect these years ONCE,
    // across the whole history, so we can (a) suppress a misleading implied-interest figure only for
    // those years and (b) show a single consolidated data-quality notice instead of one per row.
    const chainBreakYears = yearsList.filter(yr => {
        const led = ledgers[ yr ];
        const prev = ledgers[ String(Number(yr) - 1) ];
        if (!led || !prev) return false;
        return Math.abs(( led.admin_opening || 0 ) - ( prev.admin_closing || 0 )) > 0.01
            || Math.abs(( led.sinking_opening || 0 ) - ( prev.sinking_closing || 0 )) > 0.01;
    });

    return (
        <div className="space-y-4">
            {chainBreakYears.length > 0 && (
                <div
                    data-testid="chain-break-notice"
                    className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-800"
                >
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5"/>
                    <span>
                        Opening balances for FY {chainBreakYears.join(', ')} do not carry forward from
                        the prior year's closing (they were sourced from a portal snapshot rather than
                        chained). Figures for those years are shown as stored.
                    </span>
                </div>
            )}
            {yearsList.map(yr => {
                const ledger = ledgers[ yr ];
                const isCurrentYear = yr === levyYear;
                const hasQuarterlyData = isCurrentYear && currentLevyStatus?.quarters?.length > 0;

                if (!ledger) return (
                    <div key={yr} className="p-4 rounded-xl border bg-slate-50 text-slate-400 text-sm">FY {yr} — No
                        data</div>
                );
                return (
                    <div key={yr} className="rounded-xl border overflow-hidden">
                        <div className="px-4 py-3 bg-slate-50 flex items-center justify-between">
                            <p className="font-black text-sm">FY {yr}</p>
                            <div className="flex items-center gap-2">
                                <Badge variant="outline" className="text-xs">{ledger.status || 'actual'}</Badge>
                                {hasQuarterlyData && (
                                    <button
                                        onClick={() => setExpandedYear(expandedYear === yr ? null : yr)}
                                        className="text-xs text-indigo-600 font-bold hover:underline"
                                    >
                                        {expandedYear === yr ? 'Hide Quarters ▲' : 'Show Quarters ▼'}
                                    </button>
                                )}
                            </div>
                        </div>
                        {( () => {
                            // Interest/Adjustment comes from REAL transactions: the interest (and any
                            // late-payment penalty) an owner paid is recorded as levy_payments /
                            // financial_transactions with an "interest" type, which the backend
                            // aggregates per year/fund and returns on ledger.admin_interest /
                            // sinking_interest (a portal-sourced stored value wins when present). We
                            // show that figure directly and no longer infer interest from a
                            // closing-balance gap. Where a building's history was reconstructed from
                            // budgets only and has no interest transactions yet, this is 0.
                            const adminOpening = ledger.admin_opening || 0;
                            const sinkingOpening = ledger.sinking_opening || 0;
                            const adminInterest = ledger.admin_interest || 0;
                            const sinkingInterest = ledger.sinking_interest || 0;
                            const hasAdminInterest = Math.abs(adminInterest) > 0.01;
                            const hasSinkingInterest = Math.abs(sinkingInterest) > 0.01;
                            /**
                             * @generated FunctionHeader
                             * Function: fmtOpening
                             * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
                             *
                             * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
                             */
                            const fmtOpening = (val) => {
                                if (val < -0.01) return <span
                                    className="text-emerald-600">{formatCurrency(Math.abs(val))} CR</span>;
                                return formatCurrency(val);
                            };

                            return (
                                <div className="divide-y">
                                    {/* Admin fund row */}
                                    <div
                                        className={`grid gap-0 divide-x ${hasAdminInterest ? 'grid-cols-2 md:grid-cols-5' : 'grid-cols-2 md:grid-cols-4'}`}>
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Admin Opening</p>
                                            <p className="font-bold text-sm">{fmtOpening(adminOpening)}</p>
                                        </div>
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Admin Levied</p>
                                            <p className="font-bold text-sm">{formatCurrency(ledger.admin_levied || 0)}</p>
                                        </div>
                                        {hasAdminInterest && (
                                            <div className="p-3 bg-amber-50/60">
                                                <p className="text-xs text-slate-400">Admin Interest/Adj</p>
                                                <p className={`font-bold text-sm ${adminInterest > 0 ? 'text-amber-700' : 'text-emerald-600'}`}>
                                                    {adminInterest > 0 ? formatCurrency(adminInterest) : `${formatCurrency(Math.abs(adminInterest))} CR`}
                                                </p>
                                            </div>
                                        )}
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Admin Paid</p>
                                            <p className="font-bold text-sm text-emerald-700">{formatCurrency(ledger.admin_paid || 0)}</p>
                                        </div>
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Admin Closing</p>
                                            <p className={`font-bold text-sm ${( ledger.admin_closing || 0 ) > 0.01 ? 'text-rose-600' : ( ledger.admin_closing || 0 ) < -0.01 ? 'text-emerald-600' : ''}`}>
                                                {( ledger.admin_closing || 0 ) < -0.01
                                                    ? `${formatCurrency(Math.abs(ledger.admin_closing))} CR`
                                                    : formatCurrency(ledger.admin_closing || 0)}
                                            </p>
                                        </div>
                                    </div>
                                    {/* Sinking fund row */}
                                    <div
                                        className={`grid gap-0 divide-x bg-slate-50/40 ${hasSinkingInterest ? 'grid-cols-2 md:grid-cols-5' : 'grid-cols-2 md:grid-cols-4'}`}>
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Sinking Opening</p>
                                            <p className="font-bold text-sm">{fmtOpening(sinkingOpening)}</p>
                                        </div>
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Sinking Levied</p>
                                            <p className="font-bold text-sm">{formatCurrency(ledger.sinking_levied || 0)}</p>
                                        </div>
                                        {hasSinkingInterest && (
                                            <div className="p-3 bg-amber-50/60">
                                                <p className="text-xs text-slate-400">Sinking Interest/Adj</p>
                                                <p className={`font-bold text-sm ${sinkingInterest > 0 ? 'text-amber-700' : 'text-emerald-600'}`}>
                                                    {sinkingInterest > 0 ? formatCurrency(sinkingInterest) : `${formatCurrency(Math.abs(sinkingInterest))} CR`}
                                                </p>
                                            </div>
                                        )}
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Sinking Paid</p>
                                            <p className="font-bold text-sm text-emerald-700">{formatCurrency(ledger.sinking_paid || 0)}</p>
                                        </div>
                                        <div className="p-3">
                                            <p className="text-xs text-slate-400">Sinking Closing</p>
                                            <p className={`font-bold text-sm ${( ledger.sinking_closing || 0 ) > 0.01 ? 'text-rose-600' : ( ledger.sinking_closing || 0 ) < -0.01 ? 'text-emerald-600' : ''}`}>
                                                {( ledger.sinking_closing || 0 ) < -0.01
                                                    ? `${formatCurrency(Math.abs(ledger.sinking_closing))} CR`
                                                    : formatCurrency(ledger.sinking_closing || 0)}
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            );
                        } )()}

                        {/* Quarterly breakdown for current year */}
                        {hasQuarterlyData && expandedYear === yr && (
                            <div className="border-t bg-indigo-50/30 px-4 py-3">
                                <p className="text-xs font-black text-slate-500 uppercase tracking-widest mb-3">Quarterly
                                    Breakdown</p>
                                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                                    {currentLevyStatus.quarters.map((q) => (
                                        <div key={q.quarter} className="rounded-xl border bg-white p-3 space-y-1">
                                            <div className="flex items-center justify-between mb-1">
                                                <p className="text-xs font-black text-slate-700">{q.quarter}</p>
                                                <Badge
                                                    className={`text-[10px] border-none ${
                                                        q.status === 'paid' ? 'bg-emerald-100 text-emerald-700' :
                                                            q.status === 'partial' ? 'bg-amber-100 text-amber-700' :
                                                                q.status === 'overdue' ? 'bg-rose-100 text-rose-700' :
                                                                    'bg-slate-100 text-slate-500'
                                                    }`}
                                                >{q.status === 'not_yet_due' ? 'Not yet due' : (q.status || 'pending')}</Badge>
                                            </div>
                                            <div className="text-xs text-slate-500">
                                                Due: {q.due_date ? new Date(q.due_date + 'T00:00:00').toLocaleDateString('en-AU', {
                                                day: 'numeric',
                                                month: 'short'
                                            }) : '—'}
                                            </div>
                                            {( () => {
                                                // Levy this quarter = the period's OWN levy (not the
                                                // cumulative gross). Paid = real receipts tagged to
                                                // this quarter. Credit applied = amount rolled in
                                                // from another period (amount_paid was adjusted by the
                                                // credit-rollforward pass). Arrears = cumulative
                                                // outstanding at this quarter (gross due − paid).
                                                const standardDue = ( q.standard_amount != null ? q.standard_amount : q.amount_due ) || 0;
                                                const actualPaid = q.actual_paid != null ? q.actual_paid : ( q.amount_paid || q.confirmed_amount || 0 );
                                                const creditApplied = Math.round(( ( q.amount_paid || 0 ) - actualPaid ) * 100) / 100;
                                                const outstanding = Math.round(( ( q.amount_due || 0 ) - ( q.amount_paid || 0 ) ) * 100) / 100;
                                                return (
                                                    <>
                                                        <div className="flex justify-between text-xs">
                                                            <span className="text-slate-400">Levy this quarter</span>
                                                            <span className="font-bold">{formatCurrency(standardDue)}</span>
                                                        </div>
                                                        {actualPaid > 0.01 && (
                                                            <div className="flex justify-between text-xs">
                                                                <span className="text-slate-400">Paid</span>
                                                                <span className="font-bold text-emerald-700">{formatCurrency(actualPaid)}</span>
                                                            </div>
                                                        )}
                                                        {creditApplied > 0.01 && (
                                                            <div className="flex justify-between text-xs">
                                                                <span className="text-slate-400">Credit applied</span>
                                                                <span className="font-bold text-emerald-600">{formatCurrency(creditApplied)}</span>
                                                            </div>
                                                        )}
                                                        {q.pending_amount > 0 && (
                                                            <div className="flex justify-between text-xs">
                                                                <span className="text-slate-400">Pending</span>
                                                                <span className="font-bold text-amber-600">{formatCurrency(q.pending_amount)}</span>
                                                            </div>
                                                        )}
                                                        <div className="flex justify-between text-xs border-t pt-1 mt-1">
                                                            <span className="text-slate-400">{outstanding < -0.01 ? 'In credit' : 'Arrears'}</span>
                                                            <span className={`font-bold ${outstanding > 0.01 ? 'text-rose-600' : 'text-emerald-600'}`}>
                                                                {outstanding > 0.01 ? formatCurrency(outstanding) : outstanding < -0.01 ? `${formatCurrency(Math.abs(outstanding))} CR` : 'Nil'}
                                                            </span>
                                                        </div>
                                                        <div className="flex justify-between text-[10px] text-slate-400">
                                                            <span>Balance to date</span>
                                                            <span>{formatCurrency(q.amount_due || 0)}</span>
                                                        </div>
                                                    </>
                                                );
                                            } )()}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};
// ─── Payments Tab ─────────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: PaymentsTab
 * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PaymentsTab = ({unitNumber, api, canManage, levyYear}) => {
    const [payments, setPayments] = useState([]);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(null);

    const fetchPayments = useCallback(async () => {
        if (!unitNumber) return;
        setLoading(true);
        try {
            const res = await api.get(`/levy-payments?unit_number=${unitNumber}&limit=50`);
            setPayments(Array.isArray(res.data) ? res.data : []);
        } catch {
            setPayments([]);
        } finally {
            setLoading(false);
        }
    }, [unitNumber, api]);

    useEffect(() => {
        fetchPayments();
    }, [fetchPayments]);
    /**
     * @generated FunctionHeader
     * Function: handleVerify
     * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleVerify = async (paymentId) => {
        setActionLoading(paymentId);
        try {
            await api.patch(`/levy-payments/${paymentId}/verify`, {notes: 'Verified from unit detail page'});
            toast.success('Payment verified');
            fetchPayments();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to verify');
        } finally {
            setActionLoading(null);
        }
    };

    if (loading) return <div className="h-32 bg-slate-50 animate-pulse rounded-xl"/>;

    return (
        <div className="space-y-4">
            {payments.length === 0 ? (
                <div className="p-8 text-center text-slate-400 border rounded-xl bg-slate-50">
                    No payment records found for this unit.
                </div>
            ) : (
                <div className="border rounded-xl overflow-hidden">
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-slate-50">
                                <TableHead className="text-xs font-black uppercase">Date</TableHead>
                                <TableHead className="text-xs font-black uppercase">Period</TableHead>
                                <TableHead className="text-xs font-black uppercase">Method</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Amount</TableHead>
                                <TableHead className="text-xs font-black uppercase text-center">Status</TableHead>
                                {canManage && <TableHead className="w-20"/>}
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {payments.map(p => (
                                <TableRow key={p.id} className="hover:bg-slate-50/50">
                                    <TableCell
                                        className="text-sm">{p.payment_date ? new Date(p.payment_date + 'T00:00:00').toLocaleDateString('en-AU') : '—'}</TableCell>
                                    <TableCell className="text-sm">{p.quarter} {p.year}</TableCell>
                                    <TableCell
                                        className="text-sm text-slate-500 capitalize">{( p.payment_method || '' ).replace('_', ' ')}</TableCell>
                                    <TableCell className="text-right font-bold">{formatCurrency(p.amount)}</TableCell>
                                    <TableCell className="text-center">{statusBadge(p.status)}</TableCell>
                                    {canManage && (
                                        <TableCell>
                                            {p.status === 'pending' && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="text-xs h-7 text-emerald-700 border-emerald-200 hover:bg-emerald-50"
                                                    disabled={actionLoading === p.id}
                                                    onClick={() => handleVerify(p.id)}
                                                >
                                                    {actionLoading === p.id ?
                                                        <Loader2 className="h-3 w-3 animate-spin"/> :
                                                        <ShieldCheck className="h-3 w-3"/>}
                                                </Button>
                                            )}
                                        </TableCell>
                                    )}
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}
        </div>
    );
};
// ─── Council Rates Tab ────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: CouncilRatesTab
 * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CouncilRatesTab = ({unitNumber, api}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!unitNumber) return;
        // Compute current ACT financial year: Jul-Jun (e.g. July 2025 → "2025-26")
        const now = new Date();
        const yr = now.getFullYear();
        const month = now.getMonth(); // 0=Jan
        const fyStart = month >= 6 ? yr : yr - 1;
        const financialYear = `${fyStart}-${String(fyStart + 1).slice(-2)}`;
        api.get(`/council-rates/${unitNumber}?financial_year=${financialYear}`)
            .then(res => setData(res.data))
            .catch(() => setData(null))
            .finally(() => setLoading(false));
    }, [unitNumber, api]);

    if (loading) return <div className="h-32 bg-slate-50 animate-pulse rounded-xl"/>;

    if (!data || data.source === 'unavailable') return (
        <div className="p-8 text-center text-slate-400 border rounded-xl bg-slate-50">
            <p className="font-semibold mb-1">ACT Revenue data unavailable for this unit</p>
            <p className="text-xs">The ACT Revenue API did not return rates data. Rates may not be configured, or the
                unit may not be registered.</p>
        </div>
    );

    // Build quarterly breakdown from individual fields (API doesn't return quarterly_breakdown array).
    // Derive fyStart from data.financial_year (e.g., "2025-26" → 2025) so labels always match
    // the FY the API actually returned, even if rendered across the July 1 FY boundary.
    const fyStart = data.financial_year
        ? parseInt(data.financial_year.split('-')[ 0 ], 10)
        : ( new Date().getMonth() >= 6 ? new Date().getFullYear() : new Date().getFullYear() - 1 );
    const quarterly = [
        {label: 'Q1 (Jul–Sep)', due_date: `${fyStart}-08-31`, amount: data.rates_q1},
        {label: 'Q2 (Oct–Dec)', due_date: `${fyStart}-11-30`, amount: data.rates_q2},
        {label: 'Q3 (Jan–Mar)', due_date: `${fyStart + 1}-02-28`, amount: data.rates_q3},
        {label: 'Q4 (Apr–Jun)', due_date: `${fyStart + 1}-05-31`, amount: data.rates_q4},
    ];

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div className="p-4 rounded-xl border">
                    <p className="text-xs text-slate-400 mb-1">AUV</p>
                    <p className="font-black text-lg">{formatCurrency(data.auv || data.block_auv || 0)}</p>
                    <p className="text-xs text-slate-500">Assessed Unimproved Value</p>
                </div>
                <div className="p-4 rounded-xl border">
                    <p className="text-xs text-slate-400 mb-1">Annual Rates</p>
                    <p className="font-black text-lg">{formatCurrency(data.total_rates || 0)}</p>
                    <p className="text-xs text-slate-500">Total for FY</p>
                </div>
                <div className="p-4 rounded-xl border">
                    <p className="text-xs text-slate-400 mb-1">Land Tax</p>
                    <p className="font-black text-lg">{data.land_tax_applicable ? formatCurrency(data.land_tax_total || 0) : 'N/A'}</p>
                    <p className="text-xs text-slate-500">{data.land_tax_applicable ? data.land_tax_note || 'Investment property' : 'Owner-occupied — exempt'}</p>
                </div>
            </div>

            {data.rates_q1 != null && (
                <div className="rounded-xl border overflow-hidden">
                    <div className="px-4 py-3 bg-slate-50">
                        <p className="font-black text-sm">Quarterly Instalment Schedule (ACT
                            FY {fyStart}–{String(fyStart + 1).slice(-2)})</p>
                    </div>
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-slate-50/50">
                                <TableHead className="text-xs font-black uppercase">Quarter</TableHead>
                                <TableHead className="text-xs font-black uppercase">Due Date</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Amount</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {quarterly.map((q, i) => (
                                <TableRow key={i}>
                                    <TableCell className="font-bold text-sm">{q.label}</TableCell>
                                    <TableCell
                                        className="text-sm">{q.due_date ? new Date(q.due_date + 'T00:00:00').toLocaleDateString('en-AU', {
                                        day: 'numeric',
                                        month: 'long',
                                        year: 'numeric'
                                    }) : '—'}</TableCell>
                                    <TableCell
                                        className="text-right font-bold">{formatCurrency(q.amount || 0)}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {data.source && (
                <p className="text-xs text-slate-400">Source: {data.source}{data._is_estimated_block_auv ? ' (estimated block AUV)' : ''}</p>
            )}
        </div>
    );
};
// ─── Water Bills Tab ──────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: WaterBillsTab
 * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const WaterBillsTab = ({unitNumber, api}) => {
    const [bills, setBills] = useState([]);
    const [estimates, setEstimates] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!unitNumber) return;
        setLoading(true);
        // Actual bills (previous years / manually entered) AND the current-year derived
        // Supply & Sewerage estimates. Icon Water has no public API, so usage charges only
        // exist once a real quarterly bill is entered. Until then the current year shows only
        // the fixed ACT regulated Supply + Sewerage daily charges (GST-free), computed by the
        // backend /water-bills/quarterly-estimates endpoint.
        Promise.allSettled([
            api.get(`/water-bills/${unitNumber}?limit=12`),
            api.get(`/water-bills/quarterly-estimates?year=${CURRENT_FY}`),
        ]).then(([billsRes, estRes]) => {
            if (billsRes.status === 'fulfilled') {
                const d = billsRes.value.data;
                setBills(Array.isArray(d) ? d : ( d?.bills || [] ));
            } else {
                setBills([]);
            }
            if (estRes.status === 'fulfilled') {
                setEstimates(Array.isArray(estRes.value.data?.estimates) ? estRes.value.data.estimates : []);
            } else {
                setEstimates([]);
            }
        }).finally(() => setLoading(false));
    }, [unitNumber, api]);

    if (loading) return <div className="h-32 bg-slate-50 animate-pulse rounded-xl"/>;

    // A bill already recorded for the current year means the derived estimate for that
    // quarter is superseded — don't show both. Match on the billing period's calendar year.
    const currentYearBillQuarters = new Set(
        bills
            .filter(b => String(b.billing_period_start || '').startsWith(String(CURRENT_FY)))
            .map(b => ( b.quarter || '' ).split(' ')[ 0 ])  // "Q1 2026" → "Q1"
    );
    const pendingEstimates = estimates.filter(e => !currentYearBillQuarters.has(e.quarter));

    return (
        <div className="space-y-6">
            {/* Derived Supply & Sewerage for the current year (fixed ACT daily rates). */}
            {pendingEstimates.length > 0 && (
                <div className="rounded-xl border border-sky-200 overflow-hidden">
                    <div className="px-4 py-3 bg-sky-50 flex items-center justify-between flex-wrap gap-2">
                        <p className="font-black text-sm text-sky-900">
                            Derived Supply &amp; Sewerage — {CURRENT_FY} (estimated)
                        </p>
                        <p className="text-[10px] text-sky-700">
                            Fixed ACT regulated daily charges · GST-free · usage added when the Icon Water bill arrives
                        </p>
                    </div>
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-slate-50">
                                <TableHead className="text-xs font-black uppercase">Quarter</TableHead>
                                <TableHead className="text-xs font-black uppercase">Period</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Water Supply</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Sewerage</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Supply Total</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {pendingEstimates.map(e => (
                                <TableRow key={e.quarter} className="hover:bg-slate-50/50">
                                    <TableCell className="font-bold text-sm">{e.quarter}</TableCell>
                                    <TableCell className="text-xs text-slate-500">{e.label || `${e.start_date} – ${e.end_date}`}</TableCell>
                                    <TableCell className="text-right text-sm">{formatCurrency(e.water_supply_charge || 0)}</TableCell>
                                    <TableCell className="text-right text-sm">{formatCurrency(e.sewerage_supply_charge || 0)}</TableCell>
                                    <TableCell className="text-right font-bold">{formatCurrency(e.supply_total || 0)}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {/* Actual recorded bills (previous years and any manually entered). */}
            {bills.length === 0 ? (
                pendingEstimates.length === 0 && (
                    <div className="p-8 text-center text-slate-400 border rounded-xl bg-slate-50">
                        No Icon Water bills found for this unit. Bills are added by your strata manager when received
                        from Icon Water.
                    </div>
                )
            ) : (
                <div className="border rounded-xl overflow-hidden">
                    <div className="px-4 py-3 bg-slate-50">
                        <p className="font-black text-sm">Recorded Bills</p>
                    </div>
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-slate-50">
                                <TableHead className="text-xs font-black uppercase">Quarter</TableHead>
                                <TableHead className="text-xs font-black uppercase">Period</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Supply</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Usage</TableHead>
                                <TableHead className="text-xs font-black uppercase text-right">Total</TableHead>
                                <TableHead className="text-xs font-black uppercase text-center">Status</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {bills.map(b => (
                                <TableRow key={b.id} className="hover:bg-slate-50/50">
                                    <TableCell className="font-bold text-sm">{b.quarter}</TableCell>
                                    <TableCell
                                        className="text-xs text-slate-500">{b.billing_period_start} – {b.billing_period_end}</TableCell>
                                    <TableCell
                                        className="text-right text-sm">{formatCurrency(b.supply_breakdown?.supply_total || b.supply_charge || 0)}</TableCell>
                                    <TableCell
                                        className="text-right text-sm">{( b.water_usage_kl ?? b.usage_kl ) != null ? `${b.water_usage_kl ?? b.usage_kl} kL` : '—'}</TableCell>
                                    <TableCell className="text-right font-bold">{formatCurrency(b.amount || 0)}</TableCell>
                                    <TableCell className="text-center">{statusBadge(b.status)}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}
        </div>
    );
};
// ─── Main Page ────────────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: UnitFinanceDetailPage
 * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UnitFinanceDetailPage = () => {
    const params = useParams();
    const router = useRouter();
    const {api, user, hasPermission, availableYears} = useAuth();

    // Canonical levy-year list: prefer the building's real available years from
    // AuthContext (fetched from /years, the same source every other finance page
    // uses); fall back to the module-level 2021→current range only when the
    // context list hasn't loaded. Sorted ascending so the Levy History tab reads
    // oldest→newest, matching prior behaviour.
    const levyYears = React.useMemo(() => {
        const base = (Array.isArray(availableYears) && availableYears.length)
            ? availableYears.map(String)
            : LEVY_YEARS;
        return Array.from(new Set(base)).sort();
    }, [availableYears]);

    const unitNumber = params?.unit_number;
    const [unitInfo, setUnitInfo] = useState(null);
    const [levyStatus, setLevyStatus] = useState(null);
    // Was hardcoded to '2026' — silently wrong on every 1 Jan without a code change.
    // CURRENT_FY (module-level, already used for the LEVY_YEARS dropdown above) is the
    // single source of truth for "this calendar year" on this page.
    const [levyYear, setLevyYear] = useState(String(CURRENT_FY));
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState('overview');
    const [actionLoading, setActionLoading] = useState(false);

    const canManage = hasPermission('can_manage_finances');

    // Load unit info + levy status
    const fetchData = useCallback(async () => {
        if (!unitNumber) return;
        setLoading(true);
        try {
            const [unitRes, levyRes] = await Promise.allSettled([
                api.get(`/owners-units/${unitNumber}`),
                api.get(`/levy-status/${unitNumber}?year=${levyYear}`),
            ]);
            if (unitRes.status === 'fulfilled') {
                setUnitInfo(unitRes.value.data || null);
            }
            if (levyRes.status === 'fulfilled' && !levyRes.value.data?.error) {
                setLevyStatus(levyRes.value.data);
            }
        } finally {
            setLoading(false);
        }
    }, [unitNumber, api, levyYear]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    /**
     * @generated FunctionHeader
     * Function: handleSendNotice
     * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSendNotice = async () => {
        setActionLoading(true);
        try {
            const res = await api.post(`/arrears/${unitNumber}/send-notice?year=${levyYear}`, {}, {responseType: 'blob'});
            const url = window.URL.createObjectURL(new Blob([res.data]));
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', `Arrears_Notice_${unitNumber}_${levyYear}.pdf`);
            document.body.appendChild(link);
            link.click();
            toast.success("Arrears notice generated");
            fetchData();
        } catch (err) {
            toast.error("Failed to generate notice");
        } finally {
            setActionLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleReferDCA
     * Path: frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReferDCA = async () => {
        if (!window.confirm(`Refer Unit ${unitNumber} to Debt Collection Agency?`)) return;
        setActionLoading(true);
        try {
            await api.post(`/arrears/${unitNumber}/refer-dca`);
            toast.success("Referred to DCA");
            fetchData();
        } catch (err) {
            toast.error("DCA referral failed");
        } finally {
            setActionLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <Loader2 className="h-8 w-8 animate-spin text-indigo-600"/>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-slate-50/50 p-6 md:p-10 space-y-6 pb-24">
            {/* Header */}
            <div className="flex items-center gap-4">
                <button
                    onClick={() => router.back()}
                    className="p-2 rounded-xl border bg-white hover:bg-slate-50 transition-colors"
                >
                    <ArrowLeft size={18} className="text-slate-500"/>
                </button>
                <div>
                    <div className="flex items-center gap-3">
                        <h1 className="text-2xl font-black text-slate-900">Unit {unitNumber}</h1>
                        {unitInfo && (
                            <Badge variant="outline" className="capitalize text-xs">
                                {unitInfo.property_type || unitInfo.unit_type || 'Unit'}
                            </Badge>
                        )}
                    </div>
                    {unitInfo?.owner_name && (
                        <p className="text-slate-500 text-sm font-medium mt-0.5">
                            {[unitInfo.owner_name, unitInfo.owner_name_b].filter(Boolean).join(' & ')}
                        </p>
                    )}
                </div>

                <div className="ml-auto flex items-center gap-2">
                    {/* Canonical shared levy-year selector (shadcn Select + formatYear label:
                        "2026" for calendar-year buildings, "2025-26" for Jul–Jun buildings) —
                        consistent with every other finance page. Driven by this page's own
                        levyYear state via controlled props. */}
                    <YearSelector
                        value={levyYear}
                        onValueChange={setLevyYear}
                        years={levyYears}
                    />
                    {canManage && (
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() => router.push(`/financials/levy-payments?unit=${unitNumber}`)}
                            className="rounded-xl text-xs font-bold"
                        >
                            <CreditCard className="mr-1 h-3.5 w-3.5"/>
                            Manage Payments
                        </Button>
                    )}
                </div>
            </div>

            <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
                This screen shows the lot levy ledger only. Trust cash balances and reconciliations live in the Trust
                Accounting area.
            </div>

            {/* Tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
                <TabsList className="bg-white border rounded-2xl p-1.5 gap-1 flex-wrap h-auto">
                    <TabsTrigger value="overview"
                                 className="rounded-xl text-xs font-black uppercase tracking-wide px-4 py-2 data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Overview</TabsTrigger>
                    <TabsTrigger value="history"
                                 className="rounded-xl text-xs font-black uppercase tracking-wide px-4 py-2 data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Levy
                        History</TabsTrigger>
                    <TabsTrigger value="payments"
                                 className="rounded-xl text-xs font-black uppercase tracking-wide px-4 py-2 data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Payments</TabsTrigger>
                    {canManage && <TabsTrigger value="contact"
                                               className="rounded-xl text-xs font-black uppercase tracking-wide px-4 py-2 data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Contact
                        Log</TabsTrigger>}
                    <TabsTrigger value="rates"
                                 className="rounded-xl text-xs font-black uppercase tracking-wide px-4 py-2 data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Council
                        Rates</TabsTrigger>
                    <TabsTrigger value="water"
                                 className="rounded-xl text-xs font-black uppercase tracking-wide px-4 py-2 data-[state=active]:bg-indigo-600 data-[state=active]:text-white">Water
                        Bills</TabsTrigger>
                </TabsList>

                <TabsContent value="overview">
                    <Card className="rounded-2xl border-0 shadow-sm">
                        <CardContent className="p-6">
                            <OverviewTab
                                unitInfo={unitInfo}
                                levyStatus={levyStatus}
                                levyYear={levyYear}
                                setLevyYear={setLevyYear}
                                handleSendNotice={handleSendNotice}
                                handleReferDCA={handleReferDCA}
                                canManage={canManage}
                            />
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="history">
                    <Card className="rounded-2xl border-0 shadow-sm">
                        <CardHeader className="pb-4">
                            <CardTitle className="text-base">Levy Ledger History (Admin + Sinking)</CardTitle>
                            <CardDescription>Opening balance, levied, paid, and closing balance per year per
                                fund.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <LevyHistoryTab unitNumber={unitNumber} api={api} currentLevyStatus={levyStatus}
                                            levyYear={levyYear} levyYears={levyYears}/>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="payments">
                    <Card className="rounded-2xl border-0 shadow-sm">
                        <CardHeader className="pb-4">
                            <CardTitle className="text-base">Payment Records</CardTitle>
                            <CardDescription>All recorded levy payments for this unit. External DEFT/BPAY payments may
                                not appear.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <PaymentsTab unitNumber={unitNumber} api={api} canManage={canManage} levyYear={levyYear}/>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="rates">
                    <Card className="rounded-2xl border-0 shadow-sm">
                        <CardHeader className="pb-4">
                            <CardTitle className="text-base flex items-center gap-2"><Landmark size={16}/>Council Rates
                                & Land Tax</CardTitle>
                            <CardDescription>ACT council rates and land tax data for this unit.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <CouncilRatesTab unitNumber={unitNumber} api={api}/>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="contact">
                    <Card className="rounded-2xl border-0 shadow-sm">
                        <CardHeader className="pb-4">
                            <CardTitle className="text-base flex items-center gap-2"><History size={16}/>Contact &
                                Recovery History</CardTitle>
                            <CardDescription>Audit of all notices sent and manual recovery notes.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                {unitInfo?.arrears_metadata?.contact_log?.length > 0 ? (
                                    unitInfo.arrears_metadata.contact_log.map((log, i) => (
                                        <div key={i} className="p-3 rounded-xl border bg-slate-50 relative group">
                                            <div className="flex items-center justify-between mb-1">
                                                <Badge variant="outline"
                                                       className="text-[10px] uppercase font-black">{log.method}</Badge>
                                                <span className="text-[10px] text-slate-400 font-medium">
                          {new Date(log.date).toLocaleString('en-AU')}
                        </span>
                                            </div>
                                            <p className="text-sm text-slate-700 leading-relaxed">{log.description}</p>
                                            <p className="text-[10px] text-slate-400 mt-2 font-bold uppercase tracking-tight">BY: {log.performed_by_name || log.performed_by}</p>
                                        </div>
                                    ))
                                ) : (
                                    <p className="text-center text-slate-400 py-8 text-sm">No contact history
                                        recorded.</p>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="water">
                    <Card className="rounded-2xl border-0 shadow-sm">
                        <CardHeader className="pb-4">
                            <CardTitle className="text-base flex items-center gap-2"><Droplets size={16}/>Icon Water
                                Bills</CardTitle>
                            <CardDescription>Quarterly water supply and usage bills from Icon Water.</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <WaterBillsTab unitNumber={unitNumber} api={api}/>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
};

export default UnitFinanceDetailPage;
