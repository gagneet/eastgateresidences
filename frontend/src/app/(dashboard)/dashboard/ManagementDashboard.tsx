"use client"

// @featuretrace:dashboard-v2 — App-router Management dashboard wiring all v2 components (design
// source: tasks/new-dashboard/manager.jsx prototype, removed post-ship 2026-07-12 — see git history).
// Layer: frontend
// Data flow: page.tsx → DashboardData (stats, building_overview, sinking_fund_forecast, capital_shock, levy_fairness, maintenance_spend_trend, compliance, activities) → ManagementDashboard (building-scoped).
// Related: frontend/src/components/dashboard/CashPositionCard.tsx
//           frontend/src/components/dashboard/LevyFairnessCard.tsx
//           frontend/src/components/dashboard/VendorScorecardCard.tsx
// Toggle: ft_dashboard_v2

import React, {useEffect, useState} from "react"
import {ActivityFeedPremium} from "@/components/dashboard/premium"
import {useAuth} from "@/contexts/AuthContext"
import {useRouter} from "next/navigation"
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card"
import SinceLastVisit from "@/components/dashboard/SinceLastVisit"
import TriageQueue from "@/components/dashboard/TriageQueue"
import PulseScoreCard, {AXIS_HELP, AXIS_UNAVAILABLE_HELP} from "@/components/dashboard/PulseScoreCard"
import {pulseAxesFrom, useBuildingPulse} from "@/hooks/useBuildingPulse"
import ReserveRunwayChart from "@/components/dashboard/ReserveRunwayChart"
import CompactCalendar from "@/components/dashboard/CompactCalendar"
import CashPositionCard from "@/components/dashboard/CashPositionCard"
import LevyFairnessCard from "@/components/dashboard/LevyFairnessCard"
import VendorScorecardCard, {VendorRow} from "@/components/dashboard/VendorScorecardCard"
// /requests renders the form catalogue and reads only ?tab=; linking to
// /requests?status=overdue alone dropped the filter and landed managers on the
// catalogue, so the SLA card looked broken. These constants are the only
// sanctioned way to link into the tracking list.
import {OVERDUE_REQUEST_QUEUE_HREF, REQUEST_QUEUE_HREF} from "@/lib/requests/requestScope"
import ExpensesBySupplierCard from "@/components/dashboard/ExpensesBySupplierCard"
import DashboardFooterCue from "@/components/dashboard/DashboardFooterCue"
import DashboardDetailModal from "@/components/dashboard/DashboardDetailModal"
import {firstMoneyValue, fundDisplayBalance} from "@/lib/moneyNormalization"
import {formatCurrency} from "@/lib/utils"
import {FileSpreadsheet} from "lucide-react"
import {normaliseReserveProjection} from "@/lib/reserve-projection"

interface DashboardProps {
    data: any
    selectedYear?: string
}
/**
 * @generated FunctionHeader
 * Function: ManagementDashboard
 * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
// Bounded recent-activity window used only when the account has no previous login.
// Kept small on purpose: /analytics/diff-since scans per collection over this range.
const FIRST_VISIT_WINDOW_DAYS = 7;

export function ManagementDashboard({data, selectedYear}: DashboardProps) {
    const {user, api} = useAuth();
    const router = useRouter();

    const [diffSince, setDiffSince] = useState<any | null>(null);
    const [triageItems, setTriageItems] = useState<any[]>([]);
    const [triageStats, setTriageStats] = useState<any | null>(null);
    const [ppmUpcoming, setPpmUpcoming] = useState<any[]>([]);
    const [supplierSpend, setSupplierSpend] = useState<any | null>(null);
    // null = no previous login on this account, i.e. a first visit. Not 1: that told a
    // brand-new user they had been away a day and that the feed below postdated a visit
    // that never happened.
    const [daysSinceLastVisit, setDaysSinceLastVisit] = useState<number | null>(null);
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
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
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
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openDetailRoute = () => {
        if (detail?.actionHref) {
            router.push(detail.actionHref);
            setDetail(null);
        }
    };

    // collection_rate is levies_paid_pct (0-100) from /finance/building-overview.
    // null when the rate could not be determined. The previous fallback was 0, which
    // renders "0% collected" on a building that may be collecting perfectly well —
    // absence presented as a measurement, and the worst possible direction for this
    // particular number.
    const collectionRate: number | null = data?.stats?.collection_rate != null
        ? Math.round(Number(data.stats.collection_rate) * 100) / 100
        : (data?.stats?.total_levied && data?.stats?.total_collected
            ? Math.round((data.stats.total_collected / data.stats.total_levied) * 100)
            : null);

    const totalArrears = data?.stats?.total_arrears ?? 0;
    const arrearsUnits = data?.stats?.units_in_arrears ?? 0;

    const pendingUsers = data?.stats?.pending_users ?? 0;

    const lastLoginAt = (user as any)?.last_login_at;

    useEffect(() => {
        const now = new Date().getTime();
        const lastLoginMs = lastLoginAt ? new Date(lastLoginAt).getTime() : NaN;
        const haveValidLastLogin = Number.isFinite(lastLoginMs);
        if (!haveValidLastLogin) {
            // Covers both "never logged in" and an unparseable timestamp. The card then
            // renders "1st visit · Welcome" rather than inventing an elapsed time.
            setDaysSinceLastVisit(null);
        } else {
            const ms = now - lastLoginMs;
            setDaysSinceLastVisit(Math.max(1, Math.round(ms / 86_400_000)));
        }

        // A local "mark all read" override takes priority over the account-level
        // last_login_at so clicking the button actually narrows the diff window on
        // the next visit, mirroring the old Manager dashboard's localStorage pattern.
        let markReadOverride: string | null = null;
        try {
            markReadOverride = localStorage.getItem('mgr_last_visit_override');
        } catch {
            // private-browsing/storage-denied — fall through to lastLoginAt
        }
        // Window for /analytics/diff-since.
        //
        // The fallback was `now - 3 days`, which is an invented visit date: the heading
        // says "since your last visit" over a window nobody chose. For a genuine first
        // visit the card is relabelled "Recent activity", so the window only has to be
        // an honest, BOUNDED recent slice.
        //
        // FIRST_VISIT_WINDOW_DAYS is capped deliberately. diff-since scans activity per
        // collection over the window, so the cost scales with it — an unbounded or
        // very wide default would make the slowest query on this dashboard the one
        // serving the user with the least context to act on. Seven days is one
        // management cycle and keeps the scan small.
        const lastSeen = (markReadOverride && (!lastLoginAt || markReadOverride > lastLoginAt))
            ? markReadOverride
            : lastLoginAt || new Date(Date.now() - FIRST_VISIT_WINDOW_DAYS * 86_400_000).toISOString();
        const yearParam = selectedYear ? `&year=${encodeURIComponent(selectedYear)}` : "";
        const sinceParam = `?since=${encodeURIComponent(lastSeen)}${yearParam}`;

        api.get(`/analytics/diff-since${sinceParam}`)
            .then(res => setDiffSince(res.data))
            .catch(() => setDiffSince(null));

        api.get("/workflow-requests?status=overdue&limit=8")
            .then(res => {
                const list = Array.isArray(res.data) ? res.data : (res.data?.items || []);
                setTriageItems(list);
            })
            .catch(() => setTriageItems([]));

        api.get("/workflow-requests/stats/triage")
            .then(res => setTriageStats(res.data))
            .catch(() => setTriageStats(null));

        // /ppm/dashboard (used for the PPM Health KPI elsewhere) only returns counts —
        // /ppm/upcoming is the real source of individual scheduled items for the calendar.
        api.get("/ppm/upcoming?days=60")
            .then(res => setPpmUpcoming(Array.isArray(res.data) ? res.data : []))
            .catch(() => setPpmUpcoming([]));

        // Expenses by supplier — GL spend from finance expense_transactions (distinct from the
        // maintenance-work-order vendor scorecard).
        api.get("/analytics/expenses-by-supplier?months=12")
            .then(res => setSupplierSpend(res.data || null))
            .catch(() => setSupplierSpend(null));
    }, [api, lastLoginAt, selectedYear]);

    const diffItems: any[] = Array.isArray(diffSince?.items) ? diffSince.items : [];

    // Governance: compliance % as the base (0–100, confirmed from analytics.py:905),
    // minus 5 pts per overdue triage item, minus 10 pts if there are pending user approvals.
    // Fallback is 0 (not 80) when compliance data is absent — we must not invent a score.
    const overdueTriageCount = triageItems.filter(it => it.sla_breached || it.status === 'overdue').length;
    // Building Pulse comes from the BACKEND, not from a formula invented here.
    //
    // This block used to average five locally-derived axes, and
    // ManagerDashboard used a different weighted formula — the same building
    // showed 26/100 here and 31/100 there on identical data. Reproduced exactly:
    //
    //   here:  (0 + 0 + 100 + 0 + 30) / 5                       = 26  (mean)
    //   there: 0*0.35 + 0*0.30 + 100*0.20 + 95*0.10 + 24*0.05   = 31  (weighted)
    //
    // Worse than the disagreement: the aggregate fell back to
    // `data.finance_health.score` — a FINANCE metric rendered under a BUILDING
    // HEALTH label. The codebase already has a hard rule about precisely this
    // (Collection Rate and Fund Health must never share a label); this was the
    // same mistake in a different place.
    //
    // CLAUDE.md: frontends render backend-calculated view models; no page
    // computes a metric independently of the one canonical service.
    const {
        score: buildingPulseScore,
        grade: backendPulseGrade,
        components: pulseComponents,
        unavailableComponents: pulseUnavailable,
    } = useBuildingPulse();
    const pulseAxes = pulseAxesFrom(pulseComponents);
    // /analytics/levy-benchmarks returns {year, Building, ACTMedian, Admin, Sinking, ...} —
    // no collection_rate/total/total_collected field exists, so this always resolved to an
    // empty series and the sparkline never rendered. `Building` (total annual levy) is the
    // only real historical series available here; it's a $ total, not the 0-100 score, so —
    // matching the old Manager dashboard's PulseScoreCard usage — delta is left at 0 rather
    // than showing a raw dollar difference mislabeled as a score-point "this week" change.
    const pulseTrend = Array.isArray(data?.benchmarks)
        ? data.benchmarks.map((row: any) => Number(row.Building ?? 0)).filter((n: number) => Number.isFinite(n)).slice(-8)
        : [];
    const pulseDelta = 0;
    // Grade comes from the backend alongside the score. Two thresholds tables
    // would drift the same way the two score formulas did.
    const pulseGrade = backendPulseGrade ?? "–";

    const reserveProjection = normaliseReserveProjection(data?.sinking_fund_forecast?.projection);
    const capitalShockRows = data?.capital_shock?.capital_shock_index?.rows || [];
    const complianceWatchItems = data?.compliance?.upcoming || data?.compliance?.items || [];
    // maintenance_schedules docs (GET /ppm/upcoming) use next_due_date/description —
    // /ppm/dashboard (never fetched by this page anyway) only returns counts, no items.
    const calendarItems = [
        ...ppmUpcoming.slice(0, 4).map((task: any) => ({
            date: task.next_due_date,
            kind: "PPM",
            title: task.description || "PPM task",
            href: "/maintenance?tab=ppm",
        })),
        ...(complianceWatchItems as any[]).slice(0, 3).map((item: any) => ({
            date: item.due_date || item.due,
            kind: "INSP",
            title: item.label || item.title || "Compliance task",
            href: "/compliance",
        })),
    ];

    const vendorRows: VendorRow[] = (() => {
        const trend = Array.isArray(data?.maintenance_spend_trend) ? data.maintenance_spend_trend : [];
        const byVendor: Record<string, any> = {};
        trend.forEach((row: any) => {
            const name = row.vendor || row.vendor_name || "Unassigned";
            if (!byVendor[name]) byVendor[name] = {name, spend: 0, jobs: 0, on_time_pct: null, _trend: []};
            byVendor[name].spend += Number(row.spend || row.cost || 0);
            byVendor[name].jobs += Number(row.jobs || 1);
            if (row.on_time != null) byVendor[name].on_time_pct = Number(row.on_time);
            else if (row.on_time_pct != null) byVendor[name].on_time_pct = Number(row.on_time_pct);
            byVendor[name]._trend.push(Number(row.jobs ?? row.spend ?? 0));
        });
        return (Object.values(byVendor) as any[])
            .map((v) => ({
                name: v.name,
                jobs: v.jobs,
                on_time_pct: v.on_time_pct,
                spend: v.spend,
                trend: v._trend.slice(-7),
            }))
            .sort((a, b) => b.spend - a.spend)
            .slice(0, 5);
    })();
    /**
     * @generated FunctionHeader
     * Function: routeForPulseAxis
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const routeForPulseAxis = (axis: string) => {
        const key = axis.toLowerCase();
        // Backend axis names (financial/maintenance/compliance/engagement/dispute)
        // plus the old local ones, so a click never lands nowhere.
        if (key.includes("financial") || key.includes("cash")) return "/financials/overview";
        if (key.includes("compliance")) return "/compliance";
        if (key.includes("maintenance")) return "/maintenance";
        if (key.includes("dispute") || key.includes("governance")) return "/governance/meetings";
        if (key.includes("engagement") || key.includes("community")) return "/community";
        return "/community";
    };
    /**
     * @generated FunctionHeader
     * Function: openPulseDetail
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openPulseDetail = (axis?: any) => {
        const selectedAxes = axis ? [axis] : pulseAxes;
        openDetail({
            title: axis ? `${axis.k} pulse` : "Building Pulse",
            description: axis ? "Selected health-axis detail" : "Composite score and component axes",
            actionLabel: axis ? "Open related page" : "Open intelligence",
            actionHref: axis ? routeForPulseAxis(axis.k) : "/intelligence/building",
            content: (
                <div className="space-y-3">
                    <div className="rounded-2xl bg-slate-900 text-white p-4">
                        <div className="text-[10px] font-black uppercase tracking-widest text-white/50">Overall score</div>
                        <div className="text-3xl font-black">{buildingPulseScore}/100 · Grade {pulseGrade}</div>
                        <div className="text-xs text-white/60 mt-1">{pulseDelta > 0 ? "+" : ""}{pulseDelta} points since the previous trend point.</div>
                    </div>
                    {selectedAxes.map((item: any) => {
                        // Same rule as the card: an unavailable axis must not render as
                        // "null/100" with an empty bar, which reads as a measured zero.
                        const known = typeof item.v === "number" && Number.isFinite(item.v);
                        return (
                            <div key={item.k} className="rounded-xl ring-1 ring-border p-3">
                                <div className="flex items-center justify-between text-sm font-black text-foreground">
                                    <span>{item.k}</span>
                                    <span className={known ? "" : "text-muted-foreground"}>{known ? `${item.v}/100` : "NA"}</span>
                                </div>
                                <div className="mt-2 h-2 rounded-full bg-muted overflow-hidden">
                                    {known ? (
                                        <div className="h-full rounded-full"
                                             style={{width: `${Math.max(0, Math.min(100, item.v))}%`, background: item.color}}/>
                                    ) : (
                                        <div className="h-full w-full bg-[repeating-linear-gradient(45deg,rgba(100,116,139,0.25)_0px,rgba(100,116,139,0.25)_2px,transparent_2px,transparent_5px)]"/>
                                    )}
                                </div>
                                {/* The reasoning, in place. Shared with the card's tooltip so the
                                    two cannot drift; the modal has the room to show it in full. */}
                                <p className="text-xs text-muted-foreground mt-2">
                                    {known
                                        ? AXIS_HELP[item.k]
                                        : `Not available. ${AXIS_UNAVAILABLE_HELP[item.k] || "No data is recorded for this axis yet, so it is excluded from the score rather than counted as zero."} ${AXIS_HELP[item.k] || ""}`}
                                </p>
                            </div>
                        );
                    })}
                </div>
            ),
        });
    };
    /**
     * @generated FunctionHeader
     * Function: openSinceDetail
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openSinceDetail = (item: any) => {
        const kind = String(item.kind || "").toLowerCase();
        const href = kind.includes("arrears") ? "/intelligence/debt-recovery"
            : kind.includes("cash") ? "/financials/overview"
            : kind.includes("sla") ? OVERDUE_REQUEST_QUEUE_HREF
                : kind.includes("req") ? REQUEST_QUEUE_HREF
                : kind.includes("comp") ? "/compliance"
                    : kind.includes("agm") || kind.includes("meeting") ? "/governance/meetings"
                        : "/community";
        openDetail({
            title: item.label || "Dashboard update",
            description: `${item.kind || "Update"} · since your last visit`,
            actionLabel: "Open source",
            actionHref: href,
            content: (
                <div className="space-y-3">
                    <div className="rounded-2xl ring-1 ring-slate-200 bg-white p-4">
                        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Current value</div>
                        <div className="text-2xl font-black text-slate-900 mt-1">{item.value || "—"}</div>
                    </div>
                    <p className="text-sm font-semibold text-slate-600">This update came from the dashboard diff feed for changes since your previous login.</p>
                </div>
            ),
        });
    };
    /**
     * @generated FunctionHeader
     * Function: openReserveDetail
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openReserveDetail = (row: any) => openDetail({
        title: `Sinking fund FY ${row?.year ?? "—"}`,
        description: "Reserve forecast detail",
        actionLabel: "Open 10-year plan",
        actionHref: "/intelligence/projections",
        content: (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl ring-1 ring-slate-200 p-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Closing balance</div>
                    <div className="text-lg font-black text-slate-900">{formatCurrency(row?.closing_balance ?? 0)}</div>
                </div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Contributions</div>
                    <div className="text-lg font-black text-slate-900">{row?.contributions == null ? "—" : formatCurrency(row.contributions)}</div>
                </div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Capital works</div>
                    <div className="text-lg font-black text-slate-900">{row?.capital_works == null ? "—" : formatCurrency(row.capital_works)}</div>
                </div>
                {row?.shock_label && <div className="sm:col-span-3 rounded-xl bg-rose-50 ring-1 ring-rose-200 p-3 text-sm font-bold text-rose-700">{row.shock_label}</div>}
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openVendorDetail
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openVendorDetail = (vendor: VendorRow) => openDetail({
        title: vendor.name,
        description: "Vendor scorecard detail",
        actionLabel: "Open vendors",
        actionHref: "/maintenance?tab=contractors",
        content: (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Jobs</div><div className="text-xl font-black text-slate-900">{vendor.jobs}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">On-time</div><div className="text-xl font-black text-slate-900">{vendor.on_time_pct == null ? "—" : `${vendor.on_time_pct}%`}</div></div>
                <div className="rounded-xl ring-1 ring-slate-200 p-3"><div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Spend 12mo</div><div className="text-xl font-black text-slate-900">{formatCurrency(vendor.spend)}</div></div>
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openActivityDetail
     * Path: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    // Every activity used to send the reader to /community regardless of what it
    // was, so a "Document uploaded: Levy Notice - TH083" entry landed on the
    // community page — which has nothing to do with documents and, for a building
    // whose community data has been cleared, is simply empty. The backend already
    // emits `type` (and `entity_id`) for exactly this; the frontend was discarding
    // both. Mirrors routeForPulseAxis above.
    const routeForActivity = (activity: any) => {
        switch (activity?.type) {
            case "document":
                return "/documents";
            case "maintenance":
                return "/maintenance";
            case "announcement":
                // /announcements does not exist; notices is where they live.
                return "/community/notices";
            case "marketplace":
                return "/community/marketplace";
            case "booking":
                return "/community/bookings";
            case "levy_due":
                return "/financials/overview";
            default:
                return "/community";
        }
    };

    const ACTIVITY_ACTION_LABELS: Record<string, string> = {
        document: "Open documents",
        maintenance: "Open maintenance",
        announcement: "Open notices",
        marketplace: "Open marketplace",
        booking: "Open bookings",
        levy_due: "Open finance",
    };

    const openActivityDetail = (activity: any) => openDetail({
        title: activity.title || "Community update",
        description: activity.type || "Activity",
        actionLabel: ACTIVITY_ACTION_LABELS[activity?.type] || "Open community",
        actionHref: routeForActivity(activity),
        content: (
            <div className="space-y-3 text-sm font-semibold text-slate-600">
                <p>{activity.visibility ? `Visibility: ${activity.visibility}` : "This item was published into the building activity feed."}</p>
                {activity.created_at && <p>Created {new Date(activity.created_at).toLocaleString("en-AU")}</p>}
            </div>
        ),
    });

    // Cash Position trend signals (sparklines)
    const adminFundLive = firstMoneyValue(
        data?.stats?.admin_fund_live_balance,
        fundDisplayBalance(data?.building_overview?.admin_fund),
    ) ?? 0;
    const sinkingFundLive = firstMoneyValue(
        data?.stats?.sinking_fund_live_balance,
        fundDisplayBalance(data?.building_overview?.sinking_fund),
    ) ?? 0;
    const adminTrend: number[] = Array.isArray(data?.building_overview?.admin_fund_trend)
        ? data.building_overview.admin_fund_trend
        : [];
    const sinkingTrend: number[] = Array.isArray(data?.building_overview?.sinking_fund_trend)
        ? data.building_overview.sinking_fund_trend
        : (Array.isArray(reserveProjection)
            ? reserveProjection.slice(0, 6).map((r: any) => Number(r.closing_balance) / 1000 || 0)
            : []);
    const arrearsMomentum: number[] = Array.isArray(data?.building_overview?.arrears_trend)
        ? data.building_overview.arrears_trend
        : [];
    const arrearsDeltaPct = data?.building_overview?.arrears_delta_pct ?? null;
    const collectionDeltaPct = data?.building_overview?.collection_delta_pct ?? null;
    const collectionTargetPct = data?.building_overview?.collection_target_pct ?? null;
    const adminFundTarget = data?.building_overview?.admin_fund_target ?? null;
    const sinkingFundTarget = data?.building_overview?.sinking_fund_target ?? null;

    // Levy fairness — extract drivers + group impacts gracefully
    const fairness = data?.levy_fairness;
    const fairnessScore = fairness?.lbfi?.current_score ?? fairness?.lei_score ?? fairness?.score ?? fairness?.lbfi_score ?? null;
    // Mongo path (_group_key() in levy_fairness_service.py) returns singular Title Case
    // keys: "Apartment", "Townhouse", "Villa", "Retail", "Commercial", "Other". PG path
    // returns "Unit X" per-lot labels, which need no mapping (fall through unchanged).
    const FAIRNESS_GROUP_LABELS: Record<string, string> = {
        Apartment: "Apartments",
        Townhouse: "Townhouses",
        Villa: "Villas",
        Retail: "Retail",
        Commercial: "Commercial",
        Other: "Other Lots",
    };
    const fairnessImpacts = Array.isArray(fairness?.impact_by_group)
        ? fairness.impact_by_group.map((g: any) => {
            const raw = g.group_name || g.group || g.name || "";
            return {
                group_name: FAIRNESS_GROUP_LABELS[raw] ?? (raw || "Group"),
                net_subsidy: Number(g.net_subsidy ?? g.net ?? 0),
            };
        })
        : [];
    const fairnessDrivers = Array.isArray(fairness?.top_drivers ?? fairness?.drivers)
        ? (fairness.top_drivers ?? fairness.drivers).map((d: any) => ({
            name: d.name || d.label || 'Driver',
            amount: Number(d.annual_amount ?? d.amount ?? 0),
            share_pct: d.share_pct ?? d.pct ?? null,
        }))
        : [];

    return (
        <div className="space-y-8 pb-12">
            <div className="flex justify-end">
                <button
                    type="button"
                    onClick={() => router.push("/reports")}
                    className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
                >
                    <FileSpreadsheet className="h-4 w-4"/>
                    Reports
                </button>
            </div>
            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6">
                <PulseScoreCard
                    className="xl:col-span-5"
                    score={buildingPulseScore}
                    delta={pulseDelta}
                    grade={pulseGrade}
                    breakdown={pulseAxes}
                    unavailableComponents={pulseUnavailable}
                    trend={pulseTrend}
                    onOpenDetails={() => openPulseDetail()}
                    onSelectAxis={openPulseDetail}
                    onSelectTrend={() => router.push("/financials/levy-kpi")}
                />
                <SinceLastVisit
                    className="xl:col-span-7"
                    daysSince={daysSinceLastVisit}
                    items={diffItems}
                    onItemSelect={openSinceDetail}
                    onMarkRead={() => {
                        try {
                            localStorage.setItem('mgr_last_visit_override', new Date().toISOString());
                        } catch {
                            // private-browsing/storage-denied — button still no-ops safely
                        }
                    }}
                />
            </section>

            <TriageQueue
                items={triageItems}
                stats={triageStats}
                queueHref={OVERDUE_REQUEST_QUEUE_HREF}
                onAction={(item) => router.push(item?.id ? `/requests/${item.id}` : OVERDUE_REQUEST_QUEUE_HREF)}
            />

            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6">
                <CashPositionCard
                    className="xl:col-span-5"
                    admin={{balance: adminFundLive, trend: adminTrend, target: adminFundTarget}}
                    sinking={{balance: sinkingFundLive, trend: sinkingTrend, target: sinkingFundTarget}}
                    arrears={{
                        total: totalArrears,
                        units: arrearsUnits,
                        delta_pct: arrearsDeltaPct,
                        momentum: arrearsMomentum,
                    }}
                    collectionRatePct={collectionRate}
                    collectionDeltaPct={collectionDeltaPct}
                    collectionTargetPct={collectionTargetPct}
                    onOpenAdmin={() => openDetail({title: 'Admin Fund', description: 'Live administration fund position', actionLabel: 'Open finance', actionHref: '/financials/overview', content: <p className="text-sm font-semibold text-slate-600">Current live balance is <strong>{formatCurrency(adminFundLive)}</strong>{adminFundTarget != null ? ` against a target of ${formatCurrency(adminFundTarget)}.` : '.'}</p>})}
                    onOpenSinking={() => openDetail({title: 'Sinking Fund', description: 'Live sinking fund position', actionLabel: 'Open projections', actionHref: '/intelligence/projections', content: <p className="text-sm font-semibold text-slate-600">Current live balance is <strong>{formatCurrency(sinkingFundLive)}</strong>{sinkingFundTarget != null ? ` against a target of ${formatCurrency(sinkingFundTarget)}.` : '.'}</p>})}
                    onOpenArrears={() => openDetail({title: 'Arrears and collection', description: 'Outstanding levies and collection momentum', actionLabel: 'Open aged receivables', actionHref: '/reports', content: <p className="text-sm font-semibold text-slate-600"><strong>{formatCurrency(totalArrears)}</strong> outstanding across <strong>{arrearsUnits}</strong> unit{arrearsUnits === 1 ? '' : 's'} with collection at <strong>{collectionRate == null ? 'an unknown rate' : `${collectionRate.toFixed(2)}%`}</strong>.</p>})}
                />

                <LevyFairnessCard
                    className="xl:col-span-4"
                    score={fairnessScore}
                    groupImpacts={fairnessImpacts}
                    drivers={fairnessDrivers}
                    onClick={() => router.push('/intelligence/levy-fairness')}
                />

                <CompactCalendar className="xl:col-span-3" items={calendarItems}/>
            </section>

            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6">
                <ReserveRunwayChart className="xl:col-span-8" projection={reserveProjection} shocks={capitalShockRows} onSelectYear={openReserveDetail}/>
                <Card className="xl:col-span-4 rounded-3xl border-slate-200 shadow-sm">
                    <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                            <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">Compliance Watchlist</div>
                            {selectedYear && (
                                <span className="text-[10px] font-black text-indigo-600 bg-indigo-50 ring-1 ring-indigo-200 px-2 py-0.5 rounded-full uppercase tracking-widest">
                                    FY {selectedYear}
                                </span>
                            )}
                        </div>
                        <CardTitle className="text-lg font-black">FY Watchlist</CardTitle>
                        {data?.compliance?.fy_start && (
                            <div className="text-[11px] text-slate-400 font-semibold">
                                {data.compliance.fy_start} – {data.compliance.fy_end}
                            </div>
                        )}
                    </CardHeader>
                    <CardContent className="space-y-2">
                        {(complianceWatchItems as any[]).length > 0 ? (complianceWatchItems as any[]).slice(0, 6).map((item: any, index: number) => {
                            const daysLeft = item.days_left ?? item.daysLeft;
                            const overdue = item.status === 'overdue' || Number(daysLeft) < 0;
                            const soon = !overdue && Number(daysLeft) <= 30;
                            const isPPM = item.source === 'ppm';
                            const href = isPPM ? '/maintenance?tab=ppm' : '/compliance';
                            return (
                                <button key={index} onClick={() => router.push(href)} className={`w-full flex items-center justify-between rounded-xl ring-1 p-3 text-left ${overdue ? 'bg-rose-50 ring-rose-200' : soon ? 'bg-amber-50 ring-amber-200' : 'bg-white ring-slate-200'}`}>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-1.5">
                                            <span className={`text-[9px] font-black uppercase tracking-wider px-1.5 py-0.5 rounded ${isPPM ? 'bg-violet-100 text-violet-700' : 'bg-sky-100 text-sky-700'}`}>
                                                {isPPM ? 'PPM' : 'COMP'}
                                            </span>
                                            <div className="text-sm font-bold text-slate-900 truncate">{item.label || item.title || 'Item'}</div>
                                        </div>
                                        <div className="text-[11px] text-slate-500 mt-0.5">Due {item.due_date || item.due || '—'}</div>
                                    </div>
                                    <span className={`ml-3 text-xs font-black shrink-0 ${overdue ? 'text-rose-700' : soon ? 'text-amber-700' : 'text-slate-500'}`}>
                                        {daysLeft == null ? '—' : Number(daysLeft) < 0 ? `${Math.abs(Number(daysLeft))}d late` : `${daysLeft}d`}
                                    </span>
                                </button>
                            );
                        }) : (
                            <div className="text-sm font-semibold text-slate-400 py-6 text-center">No items due this financial year.</div>
                        )}
                    </CardContent>
                </Card>
            </section>

            <section className="grid grid-cols-1 xl:grid-cols-12 gap-6">
                <VendorScorecardCard
                    className="xl:col-span-7"
                    vendors={vendorRows}
                    onSelect={openVendorDetail}
                    onAll={() => router.push('/maintenance?tab=contractors')}
                />
                <div className="xl:col-span-5">
                    <ActivityFeedPremium activities={data?.activities || []} onActivitySelect={openActivityDetail}/>
                </div>
            </section>

            <section className="grid grid-cols-1 gap-6">
                {/* Expenses by supplier — GL spend (finance expense_transactions), distinct from the
                    maintenance-work-order vendor scorecard above. */}
                <ExpensesBySupplierCard
                    suppliers={supplierSpend?.suppliers || []}
                    windowMonths={supplierSpend?.window_months || 12}
                    onAll={() => router.push('/financials/overview')}
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

            <DashboardFooterCue>
                Auto-digest sends each Friday at 5pm to subscribed committee members. ·{' '}
                <button
                    type="button"
                    onClick={() => router.push('/notifications')}
                    className="text-indigo-600 font-bold hover:underline focus:underline focus:outline-none"
                >
                    Edit cadence
                </button>
            </DashboardFooterCue>
        </div>
    )
}

export default ManagementDashboard;
