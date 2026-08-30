"use client"

// @featuretrace:dashboard-v2 — App-router Owner dashboard wiring all v2 components (design source:
// tasks/new-dashboard/owner.jsx prototype, removed post-ship 2026-07-12 — see git history).
// Layer: frontend
// Data flow: page.tsx → DashboardData (owner_overview, levy_allocation, capital_shock, benchmarks, activities, v2_extras) → OwnerDashboard (building-scoped). Per-unit overlay via unit_number param.
// Related: frontend/src/components/dashboard/BuildingStrengthCard.tsx
//           frontend/src/components/dashboard/ThisWeekActions.tsx
//           frontend/src/components/dashboard/TrueCostBreakdown.tsx
//           frontend/src/components/dashboard/MarketIntelExpanded.tsx
//           frontend/src/components/dashboard/YourRequestsCard.tsx
//           frontend/src/components/dashboard/CommunityPulseFeed.tsx
// Toggle: ft_dashboard_v2

import React, {useState} from "react"
import PaymentStreakCard from "@/components/dashboard/PaymentStreakCard"
import LevyAllocationDonut from "@/components/dashboard/LevyAllocationDonut"
import CapitalForMeCard from "@/components/dashboard/CapitalForMeCard"
import BuildingStrengthCard, {StrengthItem} from "@/components/dashboard/BuildingStrengthCard"
import ThisWeekActions, {WeekAction} from "@/components/dashboard/ThisWeekActions"
import TrueCostBreakdown, {CostCategory} from "@/components/dashboard/TrueCostBreakdown"
import MarketIntelExpanded from "@/components/dashboard/MarketIntelExpanded"
import YourRequestsCard, {OwnerRequest, EmergencyContact} from "@/components/dashboard/YourRequestsCard"
import CommunityPulseFeed from "@/components/dashboard/CommunityPulseFeed"
import DashboardFooterCue from "@/components/dashboard/DashboardFooterCue"
import DashboardDetailModal from "@/components/dashboard/DashboardDetailModal"
import {useAuth} from "@/contexts/AuthContext"
import {useTaxSummary} from "@/hooks/useTaxSummary"
import {useRouter} from "next/navigation"
import {motion} from "framer-motion"
import {formatCurrency} from "@/lib/utils"
import {Button} from "@/components/ui/button"
import {Flame, Vote, Sparkles} from "lucide-react"
// This card is fed by GET /workflow-requests (Smart Requests), so both its "New
// request" button and its fallback link must stay inside that domain. They
// previously pointed at /maintenance, whose work orders live in a different
// collection — anything raised there never appeared back in this list.
import {isTerminalRequestStatus, REQUEST_QUEUE_HREF} from "@/lib/requests/requestScope"
import {isReserveResilient} from '@/lib/reserve-projection';

interface DashboardProps {
    data: any
    selectedYear?: string
}

const FALLBACK_EMERGENCY_CONTACTS: EmergencyContact[] = [];
/**
 * @generated FunctionHeader
 * Function: OwnerDashboard
 * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function OwnerDashboard({data, selectedYear}: DashboardProps) {
    const {user, api, availableYears} = useAuth();
    const router = useRouter();
    const {downloadTaxSummary} = useTaxSummary(api);
    const activeUnitNumber = data?.active_unit_number || user?.unit_number || null;
    const [detail, setDetail] = useState<{
        title: string;
        description?: string;
        actionLabel?: string;
        actionHref?: string;
        content: React.ReactNode;
    } | null>(null);
    /**
     * @generated FunctionHeader
     * Function: openDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openDetail = (nextDetail: {
        title: string;
        description?: string;
        actionLabel?: string;
        actionHref?: string;
        content: React.ReactNode;
    }) => setDetail(nextDetail);
    /**
     * @generated FunctionHeader
     * Function: openDetailRoute
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openDetailRoute = () => {
        if (detail?.actionHref) {
            router.push(detail.actionHref);
            setDetail(null);
        }
    };

    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const ownerOverview = data?.owner_overview;
    const levyStatus = {quarters: ownerOverview?.quarters || []};
    const overdueQuarters = (levyStatus?.quarters as any[])?.filter(
        (q: any) => q.status === 'overdue' || q.status === 'partial'
    ) || [];
    const hasOverdue = overdueQuarters.length > 0;

    const currentYear = availableYears?.length > 0 ? Math.max(...availableYears.map(Number)) : new Date().getFullYear();
    const isPreviousYear = selectedYear ? Number(selectedYear) < currentYear : false;
    const effectiveHasOverdue = isPreviousYear ? false : hasOverdue;

    const nextUnpaidQuarter = (levyStatus?.quarters as any[])?.find(
        (q: any) => q.status !== 'paid' && q.due_date && new Date(q.due_date) >= today
    );
    const nextKnownQuarterDate = ((levyStatus?.quarters as any[]) || [])
        .map((q: any) => q?.due_date)
        .filter((dueDate: string | undefined): dueDate is string => Boolean(dueDate))
        .find((dueDate: string) => new Date(dueDate) >= today);

    let nextDueDateObj: Date | null = null;
    if (ownerOverview?.next_due_date) {
        nextDueDateObj = new Date(ownerOverview.next_due_date + 'T00:00:00');
    } else if (nextUnpaidQuarter?.due_date) {
        nextDueDateObj = new Date(nextUnpaidQuarter.due_date);
    } else if (nextKnownQuarterDate) {
        nextDueDateObj = new Date(nextKnownQuarterDate);
    }
    const nextDueDate = nextDueDateObj
        ? nextDueDateObj.toLocaleDateString('en-AU', {day: 'numeric', month: 'short', year: 'numeric'})
        : 'Not scheduled';
    const daysUntilNextDue = nextDueDateObj
        ? Math.max(0, Math.round((nextDueDateObj.getTime() - today.getTime()) / 86_400_000))
        : null;

    const buildingOverview = data?.building_overview;
    // `?? 0` here turned an unreported fund health into a real score of 0, which the
    // strength card then presented as a confident Grade D. Unknown stays unknown.
    const bldgFundHealth: number | null = buildingOverview?.fund_health ?? null;
    const bldgLeviesPaidPct = buildingOverview?.levies_paid_pct ?? 0;

    // total_paid is NOT reliably scoped to one year (Mongo unit_levy_ledger fallback path) --
    // confirmed live 2026-08-01 via that exact document's own reconciliation_note: it is
    // "back-solved from the portal's live outstanding balance... cumulative payment history
    // through the scrape date, not payments received within this calendar year specifically."
    // One real unit showed total_paid=$28,783.04 against a $7,090.04 annual levy for the SAME
    // nominal year. paid_this_year (backend-computed as total_levied - net_balance, i.e. what's
    // been charged so far minus what's still owed/plus what's overpaid) is the correctly-scoped
    // figure -- verified against the same unit's own independently-reported bank-side amount
    // ($3,800.00) exactly. Falls back to total_paid only if an older API response without the
    // new field is ever cached.
    const totalPaidThisYear = ownerOverview?.paid_this_year ?? ownerOverview?.total_paid ?? 0;
    const adminFundAnnual = ownerOverview?.admin_fund?.annual || 0;
    const sinkingFundAnnual = ownerOverview?.sinking_fund?.annual || 0;
    const totalAnnualLevy = adminFundAnnual + sinkingFundAnnual;
    const totalLevied = ownerOverview?.total_levied || totalAnnualLevy;
    const progress = totalAnnualLevy > 0
        ? Math.min(100, Math.round((totalPaidThisYear / totalAnnualLevy) * 100))
        : totalLevied > 0 ? Math.min(100, Math.round((totalPaidThisYear / totalLevied) * 100)) : 0;
    // How much of the FULL YEAR's annual levy is left to pay, including instalments not yet
    // due/charged -- a "budget remaining" figure, distinct from balance_owing (arrears against
    // what's been charged SO FAR only, which can read $0 mid-year even with 2 of 4 quarters
    // still to come). Both totalAnnualLevy and totalPaidThisYear are now correctly year-scoped,
    // so this simple subtraction is meaningful again.
    const remaining = Math.max(0, (totalAnnualLevy || totalLevied) - totalPaidThisYear);
    // "vs Building avg" must compare like with like. bldgLeviesPaidPct (backend
    // levies_paid_pct) is (levied-so-far - outstanding) / levied-so-far -- collected
    // against what's actually been INVOICED this year to date, not the full annual
    // budget. `progress` above is deliberately annual-budget-based (a legitimate,
    // different "how much of the whole year have I covered" ring display) -- diffing
    // it directly against bldgLeviesPaidPct compares two different denominators.
    // A unit paying ahead of the invoicing schedule (e.g. paid for a quarter not yet
    // even raised) reads LOWER on the annual-budget basis than on the YTD-invoiced
    // basis purely from the denominator mismatch, showing as "behind" the building
    // average when they are, if anything, ahead of it. Confirmed live 2026-08-01
    // (Lot 63 / UA063): paid 3 of 4 quarters (in credit against what's invoiced so
    // far), yet showed "-20pts behind" purely from this basis mismatch.
    const progressVsInvoiced = totalLevied > 0
        ? Math.min(100, Math.round((totalPaidThisYear / totalLevied) * 100))
        : 0;
    // Round to avoid float jitter like "+12.347000000000001pts"
    const aheadOfAvg = Math.round(progressVsInvoiced - bldgLeviesPaidPct);

    const nextInstalment = ownerOverview?.next_payment_adjusted
        ?? ownerOverview?.next_payment_amount
        ?? (nextUnpaidQuarter ? (nextUnpaidQuarter.outstanding ?? nextUnpaidQuarter.amount_due ?? 0) : 0);

    // Use owner's own open requests count (not building-wide triage total).
    const rawOwnerWorkflowRequests: any[] = Array.isArray(data?.owner_workflow_requests) ? data.owner_workflow_requests : [];
    // isTerminalRequestStatus mirrors the router's `_TERMINAL_STATUSES`, which
    // includes auto_resolved. Listing the statuses inline here without it counted
    // deflected requests as still open, disagreeing with both MyRequestsTab and the
    // classic owner dashboard about the same owner's open count.
    const openMaintenanceFromPreview = rawOwnerWorkflowRequests.filter(
        (r: any) => !isTerminalRequestStatus(r.status)
    ).length;
    const openMaintenance = Number.isFinite(data?.owner_workflow_open_count)
        ? Number(data.owner_workflow_open_count)
        : openMaintenanceFromPreview;
    const nextMeeting = data?.nextMeeting || null;
    const nextMeetingDate = nextMeeting?.date ? new Date(nextMeeting.date).toLocaleDateString('en-AU', {
        day: 'numeric',
        month: 'short'
    }) : null;

    const streakData = data?.streak_data;
    const levyAllocation = data?.levy_allocation;
    // entitlement_pct from streak service = (unit_UOE / total_building_UOE) * 100, confirmed 0-100%.
    // ownerOverview.unit_entitlement is a raw integer UOE count (e.g. 134) — NOT a percentage.
    // Multiplying it by 100 would produce 13,400, so that branch is removed. Fall back to 0
    // (triggers the CapitalForMeCard "unavailable" placeholder) when streak data is absent.
    const entitlementPct = streakData?.entitlement_pct ?? 0;
    const rawAllocationCategories = Array.isArray(levyAllocation?.categories) ? levyAllocation.categories : [];
    const ownerAnnualTotal = totalAnnualLevy > 0 ? totalAnnualLevy : (ownerOverview?.total_levied || 0);
    const levyAllocationTotal = Number(levyAllocation?.total_annual ?? 0);
    const allocationTotal = ownerAnnualTotal > 0 ? ownerAnnualTotal : levyAllocationTotal;
    const allocationCategories = rawAllocationCategories.map((c: any) => {
        const amount = Number(c?.amount ?? 0);
        const pctFromAmount = allocationTotal > 0 ? (amount / allocationTotal) * 100 : 0;
        const pct = Number(c?.pct ?? pctFromAmount ?? 0);
        const amountFromPct = allocationTotal > 0 ? Number(((allocationTotal * pct) / 100).toFixed(2)) : amount;
        return {
            ...c,
            pct,
            amount: amountFromPct,
        };
    });

    const capitalShockRows = (data?.capital_shock?.capital_shock_index?.rows || []).map((r: any) => ({
        year: r.year,
        description: r.description ?? r.label ?? r.category,
        estimated_cost: r.capital_spend ?? r.estimated_cost,
        severity: r.risk_level,
    }));
    const nextShock = data?.capital_shock?.capital_shock_index?.next_shock;

    // v2 demo extras seeded by demo_customer.py (graceful fallbacks when missing)
    const extras = data?.dashboard_v2_extras ?? {};
    const emergencyContacts: EmergencyContact[] = Array.isArray(extras?.emergency_contacts)
        ? extras.emergency_contacts
        : FALLBACK_EMERGENCY_CONTACTS;
    // marketSignals: prefer seeded unit-level estimate; supplement with suburb data from
    // /analytics/market-snapshot when unit estimate is absent (non-demo buildings).
    const _rawSignals    = extras?.market_signals ?? {};
    const _marketSnap    = data?.market_snapshot ?? {};
    // Spread rawSignals first so the explicit fallback assignments below can override
    // any null/undefined values the API sends. If spread comes last it wins over the
    // computed fallbacks (null spreads override non-null computed values).
    const marketSignals  = {
        ..._rawSignals,
        suburb_median:   _rawSignals.suburb_median   ?? _marketSnap.median_price  ?? 0,
        yoy_pct:         _rawSignals.yoy_pct         ?? _marketSnap.growth_yoy    ?? null,
        rental_yield_pct: _rawSignals.rental_yield_pct ?? _marketSnap.rental_yield  ?? null,
        days_on_market:  _rawSignals.days_on_market  ?? _marketSnap.days_on_market ?? null,
    };
    const unitTco = data?.unit_tco;
    const seededCostCategories: CostCategory[] = Array.isArray(extras?.cost_categories) ? extras.cost_categories : [];

    // True-cost categories: prefer /owner-hub/unit-tco because it is the
    // canonical consolidated TCO calculation. dashboard_v2_extras are only a
    // presentational/demo fallback when the canonical endpoint is unavailable.
    // capital_replacement is intentionally excluded here because OwnerHub
    // treats it as informational; adding it would double-count sinking levies.
    const costCategories: CostCategory[] = unitTco ? [
        {name: 'Strata levies', annual: Number(unitTco.strata_levies ?? totalAnnualLevy ?? 0), color: '#4F46E5'},
        {name: 'Council rates', annual: Number(unitTco.council_rates ?? 0), color: '#F59E0B'},
        {name: 'Land tax', annual: Number(unitTco.land_tax ?? 0), color: '#E11D48'},
        {name: 'Water charges', annual: Number(unitTco.water_charges ?? 0), color: '#0EA5E9'},
        {name: 'Mortgage interest', annual: Number(unitTco.mortgage_interest ?? 0), color: '#7C3AED'},
    ].filter((category) => Number(category.annual) > 0) : [
        {name: 'Strata levies', annual: totalAnnualLevy, color: '#4F46E5'} as CostCategory,
        ...seededCostCategories.filter((c) => c.name !== 'Strata levies'),
    ];
    const trueCostTotal = costCategories.reduce((s, c) => s + Number(c.annual || 0), 0);

    // This-week actions are derived from real signals; nothing fabricated.
    const weekActions: WeekAction[] = [];
    if (effectiveHasOverdue) {
        weekActions.push({
            id: 'levy-overdue',
            tone: 'rose',
            icon: 'dollar',
            title: `${overdueQuarters.length} levy quarter${overdueQuarters.length > 1 ? 's' : ''} overdue`,
            sub: `Clear arrears to restore your on-time streak`,
            cta: 'Pay now',
            href: '/financials/levy-payments',
        });
    } else if (nextDueDateObj) {
        weekActions.push({
            id: 'levy-next',
            tone: 'amber',
            icon: 'dollar',
            title: daysUntilNextDue != null ? `Next levy due in ${daysUntilNextDue} days` : 'Next levy due',
            sub: `${formatCurrency(nextInstalment)} · ${nextDueDate}`,
            cta: 'Review',
            href: '/financials/levy-payments',
        });
    }
    if (nextMeeting?.date) {
        weekActions.push({
            id: 'next-meeting',
            tone: 'indigo',
            icon: 'vote',
            title: `${nextMeeting.title || 'Owners meeting'} on ${nextMeetingDate}`,
            sub: `Agenda and proxies open before the meeting`,
            cta: 'Open',
            href: '/governance/meetings',
        });
    }
    if (openMaintenance > 0) {
        weekActions.push({
            id: 'maintenance-open',
            tone: 'slate',
            icon: 'wrench',
            title: `${openMaintenance} maintenance request${openMaintenance === 1 ? '' : 's'} in flight`,
            sub: 'Track progress or add a photo',
            cta: 'View',
            href: '/maintenance',
        });
    }
    if (nextShock && (nextShock as any).year) {
        const shock = nextShock as any;
        weekActions.push({
            id: 'capital-upcoming',
            tone: 'indigo',
            icon: 'users',
            title: `Capital project flagged for FY ${shock.year}`,
            sub: shock.description || 'Sinking fund covers your share',
            cta: 'See impact',
            href: '/intelligence/building',
        });
    }

    // Owner-specific requests: prefer real API data; fall back to seeded extras for demo buildings.
    const liveOwnerRequests: OwnerRequest[] = rawOwnerWorkflowRequests
        .filter((r: any) => !isTerminalRequestStatus(r.status))
        .map((r: any): OwnerRequest => ({
            id: r.id,
            reference: r.request_number,
            // Normalise status: overdue → "overdue", awaiting review → "awaiting_owner", else "in_progress"
            status: r.sla_breached || r.status === 'overdue'
                ? 'overdue'
                : ['waiting_for_approval', 'triaged', 'waiting_for_info', 'awaiting_owner'].includes(r.status)
                    ? 'awaiting_owner'
                    : 'in_progress',
            title: r.title || r.subject || 'Maintenance request',
            summary: r.description ? String(r.description).slice(0, 80) : undefined,
            needs_reply: Boolean(r.needs_reply || r.needs_human_review || r.sla_breached || r.status === 'awaiting_owner'),
            href: `/requests/${r.id}`,
        }));
    // Use live requests if the endpoint returned any; otherwise show seeded demo data.
    const ownerRequests: OwnerRequest[] = liveOwnerRequests.length > 0
        ? liveOwnerRequests
        : Array.isArray(extras?.owner_requests) ? extras.owner_requests : [];

    // Building Strength signals — computed from already-prefetched data, no fabrication.
    const compliancePct = data?.compliance?.percentage ?? null;
    const insuranceLine = extras?.insurance?.renews_on
        ? `Renews ${extras.insurance.renews_on}${extras.insurance.no_premium_hike ? ' · no premium hike' : ''}`
        : 'Renewal date unconfirmed';
    // `.every()` on an empty array is `true`, so the previous inline version told every
    // building with NO forecast that its "reserves stay positive through 10-year
    // forecast" — and its `?? 0` told a building with an unknown balance that the
    // forecast "dips below zero". Both verdicts were invented from absent data.
    // isReserveResilient() returns null when nothing is known, which renders as unknown.
    const sinkingResilient = isReserveResilient(data?.sinking_fund_forecast?.projection);
    // undefined (not loaded / not reported) stays undefined. Defaulting to 0 here made
    // "No SLA breaches in the last 30 days" the answer for a building that had simply
    // never reported any figure — a clean bill of health manufactured out of silence.
    const slaBreached = data?.maintenance?.sla_breaches ?? extras?.maintenance?.sla_breaches ?? null;
    const strengthItems: StrengthItem[] = [
        {
            k: 'Funds healthy',
            // Both inputs must be known before this can be a verdict either way.
            ok: (sinkingResilient == null || bldgFundHealth == null)
                ? null
                : (sinkingResilient && bldgFundHealth >= 60),
            detail: bldgFundHealth == null && sinkingResilient == null
                ? 'No reserve forecast or fund health reported yet'
                : sinkingResilient == null
                ? 'No reserve forecast on record — configure the capital plan to see this'
                : sinkingResilient
                    ? 'Reserves stay positive through 10-year forecast'
                    : 'Forecast dips below zero — review reserve plan',
        },
        {
            k: 'Compliance',
            // Was `ok: true` when the percentage was unknown — an unknown compliance
            // position rendered as a green tick, which is the most dangerous direction
            // for this particular signal to fail in.
            ok: compliancePct == null ? null : compliancePct >= 90,
            detail: compliancePct == null ? 'No compliance summary available yet' : `${Math.round(compliancePct)}% of certs current`,
        },
        {
            k: 'Maintenance SLA',
            ok: slaBreached == null ? null : Number(slaBreached) === 0,
            detail: slaBreached == null
                ? 'No SLA data reported for this building'
                : Number(slaBreached) === 0
                    ? 'No SLA breaches in the last 30 days'
                    : `${slaBreached} SLA breach${Number(slaBreached) === 1 ? '' : 'es'} active`,
        },
        {
            k: 'Insurance',
            // An absent renewal date is unknown, not a failure: it says nothing about
            // whether the building is insured, only that we were not told when it renews.
            ok: extras?.insurance?.renews_on ? true : null,
            detail: insuranceLine,
        },
    ];
    const buildingHealthScore = bldgFundHealth == null ? null : Math.round(bldgFundHealth);
    const buildingHealthDelta = Number(buildingOverview?.fund_health_delta ?? 0);

    // Footer cue — only render with a real comparison to avoid invented numbers.
    const footerBenchmark = extras?.market_signals?.median_levy_pct_delta;
    /**
     * @generated FunctionHeader
     * Function: handleWeekActionNav
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleWeekActionNav = (action: WeekAction) => {
        if (action.href) router.push(action.href);
    };
    /**
     * @generated FunctionHeader
     * Function: handleRequest
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRequest = (r: OwnerRequest) => {
        if (r.href) router.push(r.href);
        // No id on a seeded/demo row — fall back to the request tracking list
        // rather than /maintenance, which is a different collection entirely from
        // the workflow requests this card lists.
        else router.push(REQUEST_QUEUE_HREF);
    };
    /**
     * @generated FunctionHeader
     * Function: openOwnerStandingDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openOwnerStandingDetail = () => openDetail({
        title: `Unit ${activeUnitNumber || '—'} levy standing`,
        description: `FY ${selectedYear || currentYear} payment detail`,
        actionLabel: 'Open levy payments',
        actionHref: '/financials/levy-payments',
        content: (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Paid</div><div className="text-xl font-black text-slate-900">{formatCurrency(totalPaidThisYear)}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Remaining</div><div className="text-xl font-black text-slate-900">{formatCurrency(remaining)}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Next due</div><div className="text-xl font-black text-slate-900">{formatCurrency(nextInstalment)}</div><div className="text-xs font-semibold text-slate-500">{nextDueDate}</div></div>
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openStrengthDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openStrengthDetail = () => openDetail({
        title: "Building strength",
        description: "Signals affecting your asset",
        actionLabel: "Open intelligence",
        actionHref: "/intelligence/building",
        content: (
            <div className="space-y-2">
                {strengthItems.map((item) => (
                    <div key={item.k} className="rounded-xl ring-1 ring-slate-200 p-3">
                        <div className="text-sm font-black text-slate-900">{item.k}</div>
                        <div className="text-xs font-semibold text-slate-500 mt-1">{item.detail}</div>
                    </div>
                ))}
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openAllocationDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openAllocationDetail = (category?: any) => openDetail({
        title: category ? category.name : "Levy allocation",
        description: category ? "Selected levy category" : `FY ${selectedYear || currentYear} annual levy allocation`,
        actionLabel: "Open my finances",
        actionHref: "/financials/my-finances",
        content: category ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Annual amount</div><div className="text-xl font-black text-slate-900">{formatCurrency(category.amount ?? 0)}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Share</div><div className="text-xl font-black text-slate-900">{Number(category.pct ?? 0).toFixed(1)}%</div></div>
            </div>
        ) : (
            <div className="space-y-2">
                {allocationCategories.map((category: any) => (
                    <div key={category.name} className="flex items-center justify-between rounded-xl ring-1 ring-slate-200 p-3 text-sm">
                        <span className="font-bold text-slate-700">{category.name}</span>
                        <span className="font-black text-slate-900">{formatCurrency(category.amount ?? 0)} · {Number(category.pct ?? 0).toFixed(1)}%</span>
                    </div>
                ))}
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openCostDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCostDetail = (category: CostCategory) => {
        const total = costCategories.reduce((sum, item) => sum + Number(item.annual || 0), 0);
        const pct = total > 0 ? (Number(category.annual || 0) / total) * 100 : 0;
        openDetail({
            title: category.name,
            description: "Ownership cost detail",
            actionLabel: "Open owner hub",
            actionHref: "/owner-hub/tco",
            content: (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Annual cost</div><div className="text-xl font-black text-slate-900">{formatCurrency(category.annual)}</div></div>
                    <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Share of total</div><div className="text-xl font-black text-slate-900">{pct.toFixed(1)}%</div></div>
                </div>
            ),
        });
    };
    /**
     * @generated FunctionHeader
     * Function: openMarketDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openMarketDetail = (payload?: any) => openDetail({
        title: payload?.unit ? `Comparable sale ${payload.unit}` : "Market intelligence",
        description: payload?.unit ? "Recent comparable sale" : "Estimate and benchmark detail",
        actionLabel: "Open market intelligence",
        actionHref: "/intelligence/market",
        content: payload?.unit ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Price</div><div className="text-xl font-black text-slate-900">{formatCurrency(payload.price)}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Bedrooms</div><div className="text-xl font-black text-slate-900">{payload.bedrooms || '—'}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Sold</div><div className="text-xl font-black text-slate-900">{payload.sold_date ? new Date(payload.sold_date).toLocaleDateString('en-AU') : '—'}</div></div>
            </div>
        ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Estimate</div><div className="text-xl font-black text-slate-900">{formatCurrency(marketSignals?.estimate || 0)}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Suburb median</div><div className="text-xl font-black text-slate-900">{formatCurrency(marketSignals?.suburb_median || 0)}</div></div>
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openCapitalDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCapitalDetail = (event: any) => openDetail({
        title: `Capital works FY ${event.year || '—'}`,
        description: event.description || "Upcoming capital project",
        actionLabel: "Open capital plan",
        actionHref: "/intelligence/capital-planner",
        content: (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Project cost</div><div className="text-xl font-black text-slate-900">{formatCurrency(event.estimated_cost || 0)}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Your share</div><div className="text-xl font-black text-slate-900">{formatCurrency(event.myShare || 0)}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Severity</div><div className="text-xl font-black text-slate-900 capitalize">{event.severity || 'normal'}</div></div>
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openCommunityDetail
     * Path: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCommunityDetail = (item: any) => openDetail({
        title: item.title || item.message || "Community update",
        description: item.kind || item.type || "Activity",
        actionLabel: "Open community",
        actionHref: "/community",
        content: <p className="text-sm font-semibold text-slate-600">{item.message || item.title || "This activity came from the building feed."}</p>,
    });

    return (
        <div className="space-y-10">
            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6" aria-label="Your property intelligence snapshot">
                <motion.div
                    initial={{opacity: 0, y: -20}}
                    animate={{opacity: 1, y: 0}}
                    className="xl:col-span-7 rounded-3xl bg-slate-900 text-white overflow-hidden relative p-6 md:p-7 shadow-[0_20px_50px_rgba(0,0,0,0.16)]"
                    role="button"
                    tabIndex={0}
                    onClick={openOwnerStandingDetail}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            openOwnerStandingDetail();
                        }
                    }}
                >
                    <div className="absolute inset-0 bg-indigo-600/10 blur-3xl pointer-events-none"/>
                    <div className="relative z-10">
                        <div className="flex items-center justify-between mb-5 gap-3 flex-wrap">
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-[0.12em] ring-1 bg-white/10 text-white ring-white/15">
                                Your standing · Unit {activeUnitNumber || '—'}
                            </span>
                            <div className="flex items-center gap-2 flex-wrap" aria-label="Owner badges">
                                {(streakData?.streak ?? 0) >= 1 && (
                                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 ring-1 ring-emerald-500/30 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-emerald-300">
                                        <Flame size={12}/> On-time × {streakData.streak}
                                    </span>
                                )}
                                {extras?.badges?.active_voter && (
                                    <span className="inline-flex items-center gap-1 rounded-full bg-indigo-500/15 ring-1 ring-indigo-500/30 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-indigo-200">
                                        <Vote size={12}/> Active voter
                                    </span>
                                )}
                                {extras?.badges?.event_host && (
                                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 ring-1 ring-amber-500/30 px-2 py-1 text-[10px] font-bold uppercase tracking-widest text-amber-300">
                                        <Sparkles size={12}/> Event host
                                    </span>
                                )}
                            </div>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-12 gap-6 items-center">
                            <div className="md:col-span-4 flex flex-col items-center">
                                <div className="relative w-32 h-32 flex-shrink-0" aria-label={`${progress}% of annual levy paid`}>
                                    <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90" aria-hidden="true">
                                        <circle cx="50" cy="50" r="39" fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="10"/>
                                        <circle cx="50" cy="50" r="39" fill="none" stroke="#34D399" strokeWidth="10" strokeLinecap="round" strokeDasharray={`${(progress / 100) * 245} 245`}/>
                                    </svg>
                                    <div className="absolute inset-0 grid place-items-center text-center">
                                        <div>
                                            <div className="text-3xl font-black">{progress}%</div>
                                            <div className="text-[9px] font-black uppercase tracking-widest text-white/40">Paid · FY{selectedYear ? String(selectedYear).slice(-2) : ''}</div>
                                        </div>
                                    </div>
                                </div>
                                <div className="mt-3 text-center">
                                    <div className="text-[10px] font-bold uppercase tracking-widest text-white/70">vs Building avg</div>
                                    <div className={`text-sm font-bold ${aheadOfAvg >= 0 ? 'text-emerald-300' : 'text-amber-300'}`}>
                                        {aheadOfAvg >= 0 ? '+' : ''}{aheadOfAvg}pts {aheadOfAvg >= 0 ? 'ahead' : 'behind'}
                                    </div>
                                </div>
                            </div>

                            <div className="md:col-span-4 space-y-3">
                                <div>
                                    <div className="text-[10px] font-bold uppercase tracking-widest text-white/40">Paid to date</div>
                                    <div className="text-3xl font-black">{formatCurrency(totalPaidThisYear)}</div>
                                    {/* FY-qualified, not just "annual" -- this amount is specific to the selected year and
                                        is not the same figure every year (levies are re-set annually), which read as
                                        inconsistent/wrong across years without the year label. Matches the existing
                                        "Paid · FY{YY}" abbreviation used just above in this same card. */}
                                    <div className="text-xs text-white/60 mt-0.5">of {formatCurrency(totalAnnualLevy || totalLevied)} FY{selectedYear ? String(selectedYear).slice(-2) : ''} annual · {formatCurrency(remaining)} remaining</div>
                                </div>
                                <div className="border-t border-white/10 pt-3">
                                    <div className="text-[10px] font-bold uppercase tracking-widest text-white/40">Next due</div>
                                    <div className="text-xl font-black">{formatCurrency(nextInstalment)}</div>
                                    <div className="text-xs text-white/60 mt-0.5">{nextDueDate}</div>
                                </div>
                            </div>

                            <div className="md:col-span-4">
                                <PaymentStreakCard data={streakData} totalLots={data?.stats?.total_lots} dark/>
                            </div>
                        </div>

                        <div className="mt-6 flex flex-wrap gap-3">
                            <Button className="bg-white text-slate-900 hover:bg-slate-100 font-black rounded-2xl" onClick={(e) => { e.stopPropagation(); router.push('/financials/levy-payments'); }}>
                                Quick Pay {formatCurrency(nextInstalment)}
                            </Button>
                            <Button variant="outline" className="bg-transparent text-white border-white/20 hover:bg-white/10 font-black rounded-2xl" onClick={(e) => { e.stopPropagation(); router.push('/financials/my-finances'); }}>
                                My Finances
                            </Button>
                            <Button variant="outline" className="bg-transparent text-white border-white/20 hover:bg-white/10 font-black rounded-2xl"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        if (activeUnitNumber) {
                                            downloadTaxSummary(activeUnitNumber, selectedYear ?? new Date().getFullYear());
                                        }
                                    }}
                                    aria-label="Download tax summary PDF">
                                Tax summary
                            </Button>
                        </div>
                    </div>
                </motion.div>

                <BuildingStrengthCard
                    className="xl:col-span-5"
                    score={buildingHealthScore}
                    delta={buildingHealthDelta}
                    items={strengthItems}
                    onBreakdown={openStrengthDetail}
                />
            </section>

            <ThisWeekActions
                items={weekActions}
                estimatedMinutes={weekActions.length > 0 ? weekActions.length * 2 : undefined}
                onNavigate={handleWeekActionNav}
            />

            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6" aria-label="Where your money goes and the total cost of ownership">
                <LevyAllocationDonut
                    className="xl:col-span-5"
                    categories={allocationCategories}
                    totalAnnual={allocationTotal}
                    year={selectedYear || data?.selectedYear}
                    onCategorySelect={openAllocationDetail}
                    onOpenDetails={() => openAllocationDetail()}
                />
                <TrueCostBreakdown
                    className="xl:col-span-7"
                    categories={costCategories}
                    yoyDelta={extras?.cost_yoy_delta_pct ?? null}
                    onCategorySelect={openCostDetail}
                />
            </section>

            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6" aria-label="Market intelligence and capital works that affect you">
                <MarketIntelExpanded
                    className="xl:col-span-7"
                    signals={marketSignals}
                    onRefine={() => openMarketDetail()}
                    onSignalSelect={openMarketDetail}
                />
                <CapitalForMeCard
                    className="xl:col-span-5"
                    shockRows={capitalShockRows}
                    entitlementPct={entitlementPct}
                    onEventSelect={openCapitalDetail}
                    onOpenPlan={() => router.push('/intelligence/capital-planner')}
                />
            </section>

            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6" aria-label="Community pulse and your maintenance requests">
                <CommunityPulseFeed
                    className="xl:col-span-7"
                    items={data?.activities || []}
                    onItemSelect={openCommunityDetail}
                />
                <YourRequestsCard
                    className="xl:col-span-5"
                    requests={ownerRequests}
                    emergencyContacts={emergencyContacts}
                    onNewRequest={() => router.push('/requests/new')}
                    onRequest={handleRequest}
                />
            </section>

            <DashboardDetailModal
                isOpen={Boolean(detail)}
                onClose={() => setDetail(null)}
                title={detail?.title || ''}
                description={detail?.description}
                actionLabel={detail?.actionLabel}
                onAction={openDetailRoute}
            >
                {detail?.content}
            </DashboardDetailModal>

            {footerBenchmark != null && (
                <DashboardFooterCue>
                    Your levy is{' '}
                    <span className={`${footerBenchmark < 0 ? 'text-emerald-600' : 'text-rose-600'} font-black`}>
                        {Math.abs(footerBenchmark)}% {footerBenchmark < 0 ? 'below' : 'above'}
                    </span>{' '}
                    the {extras?.market_signals?.benchmark_label || 'ACT median'} for similar units in this suburb.
                </DashboardFooterCue>
            )}
            {trueCostTotal === 0 && footerBenchmark == null && (
                <DashboardFooterCue dotColor="#94a3b8">
                    Connect your council, water, insurance and market data feeds to unlock true-cost and market signals.
                </DashboardFooterCue>
            )}
        </div>
    )
}

export default OwnerDashboard;
