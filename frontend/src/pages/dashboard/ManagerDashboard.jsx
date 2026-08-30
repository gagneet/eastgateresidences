// @featuretrace:dashboard-v2 — Classic (pages-layer) Management Dashboard: building cockpit for strata managers/admins with year-linked KPIs, cash position, fairness index, triage queue, and reserve runway.
// Layer: frontend
// Data flow: /management/classic → ManagerDashboard → /finance/building-overview, /stats/building-kpis, /analytics/*, /intelligence/* → PostgreSQL ledger + building-scoped analytics.
// Related: frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx  (App-router counterpart)
//           frontend/src/app/(dashboard)/management/classic/page.tsx  (route wrapper)
//           frontend/src/pages/dashboard/OwnerDashboard.tsx
// Toggle: ft_dashboard_v2
// Scope: (building-scoped)

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import YearSelector from '../../components/widgets/YearSelector';
import KPIStatCard from '../../components/dashboard/KPIStatCard';
import ChartCard from '../../components/dashboard/ChartCard';
// v2 card replacements — drop GaugeCard (replaced by LevyFairnessCard's ring gauge)
import CashPositionCard from '../../components/dashboard/CashPositionCard';
import LevyFairnessCard from '../../components/dashboard/LevyFairnessCard';
import VendorScorecardCard from '../../components/dashboard/VendorScorecardCard';
import ExpensesBySupplierCard from '../../components/dashboard/ExpensesBySupplierCard';
import ActivityFeed from '../../components/dashboard/ActivityFeed';
// Dashboard v2 components
import PulseScoreCard from '../../components/dashboard/PulseScoreCard';
import {pulseAxesFrom, useBuildingPulse} from '../../hooks/useBuildingPulse';
import SinceLastVisit from '../../components/dashboard/SinceLastVisit';
import TriageQueue from '../../components/dashboard/TriageQueue';
import ReserveRunwayChart from '../../components/dashboard/ReserveRunwayChart';
import CompactCalendar from '../../components/dashboard/CompactCalendar';
import DashboardFooterCue from '../../components/dashboard/DashboardFooterCue';
import DashboardDetailModal from '../../components/dashboard/DashboardDetailModal';
import {firstMoneyValue, fundDisplayBalance} from '../../lib/moneyNormalization';
import { formatCurrency } from '../../lib/utils';
// /requests renders the request FORM CATALOGUE and reads only ?tab=; the tracking
// list is behind ?tab=my-requests. These constants are the only sanctioned way to
// link into it.
import {OVERDUE_REQUEST_QUEUE_HREF, REQUEST_QUEUE_HREF} from '../../lib/requests/requestScope';
import {
    Activity,
    AlertTriangle,
    ArrowRight,
    ArrowUpRight,
    Briefcase,
    Building,
    Calendar,
    Clock,
    DollarSign,
    FileSpreadsheet,
    Search,
    Settings,
    Shield,
    ShieldCheck,
    ShoppingBag,
    TrendingUp,
    Users,
    Wrench,
    Zap
} from 'lucide-react';
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis
} from 'recharts';
import { motion } from 'framer-motion';
import { normaliseReserveProjection } from '@/lib/reserve-projection';
/**
 * @generated FunctionHeader
 * Function: summariseArrearsDetail
 * Path: frontend/src/pages/dashboard/ManagerDashboard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function summariseArrearsDetail(rows) {
    if (!Array.isArray(rows)) {
        return null;
    }
    return {
        total: rows.reduce((sum, row) => sum + Number(row?.total_arrears || 0), 0),
        units: rows.length,
    };
}
/**
 * Modernized Manager Dashboard
 * Full building cockpit for Strata Managers / Admins
 */
const ManagerDashboard = () => {
    const {user, api, token, hasPermission, isAdmin, selectedYear, setSelectedYear} = useAuth();
    const router = useRouter();

    const [loading, setLoading] = useState(true);

    // Data States
    const [kpiData, setKpiData] = useState(null);
    const [buildingOverview, setBuildingOverview] = useState(null);
    const [portalBankBalances, setPortalBankBalances] = useState(null);
    const [arrearsDetailSummary, setArrearsDetailSummary] = useState(null);
    const [maintenanceStats, setMaintenanceStats] = useState(null);
    const [activities, setActivities] = useState([]);
    const [triageRequests, setTriageRequests] = useState([]);
    const [triageStats, setTriageStats] = useState(null);
    const [levyTrendData, setLevyTrendData] = useState([]);
    const [userStats, setUserStats] = useState(null);
    const [sinkingFundForecast, setSinkingFundForecast] = useState(null);
    const [complianceSummary, setComplianceSummary] = useState(null);
    const [spendTrend, setSpendTrend] = useState([]);
    const [supplierSpend, setSupplierSpend] = useState(null);
    const [ppmDashboard, setPpmDashboard] = useState(null);
    const [ppmUpcoming, setPpmUpcoming] = useState([]);
    // v2 additions
    const [diff, setDiff] = useState(null);
    const [fairness, setFairness] = useState(null);
    const [capitalShock, setCapitalShock] = useState(null);
    const [detail, setDetail] = useState(null);
    const [lastVisitTs] = useState(() => {
        try { return localStorage.getItem('mgr_last_visit') ?? null; } catch { return null; }
    });

    // Record visit timestamp on mount
    useEffect(() => {
        try { localStorage.setItem('mgr_last_visit', new Date().toISOString()); } catch { /* ok */ }
    }, []);

    // Dashboard fan-out.
    //
    // PERF (2026-08-24): these 20 calls used to run as 20 sequential `await`s, so the
    // page cost the SUM of every endpoint (~1.9 s of backend time on localhost, and
    // 20x the round-trip on a real network) before the first pixel of real content.
    // They are independent, so they now all start together and the page paints as
    // soon as the above-the-fold wave lands; the rest stream into their own cards.
    // Every derived value in the render is already null-guarded (`?.` / `??`), which
    // is what makes releasing the skeleton early safe.
    const fetchDashboardData = useCallback(async () => {
        if (!token || !selectedYear) return;
        setLoading(true);

        const yearParam = `?year=${selectedYear}`;
        const kpiParam = `?financial_year=${selectedYear}`;

        // Each task owns its own failure: one dead endpoint must never blank the
        // rest of the cockpit, exactly as the previous per-call try/catch did.
        const task = async (label, fn) => {
            try {
                await fn();
            } catch (err) {
                console.error(`Failed to fetch ${label}:`, err);
            }
        };

        // --- Above-the-fold wave: the hero KPI/fund/arrears cards. ---
        //
        // /arrears/detail is one of the slower calls here, but it belongs in this wave
        // rather than the secondary one. The arrears card reads
        //   arrearsDetailSummary?.total ?? buildingOverview?.total_outstanding ?? ...
        // so deferring it would paint the building-overview figure first and then snap
        // to the /arrears/detail figure — two DIFFERENT bases for the same dollar amount,
        // seconds apart. CLAUDE.md's arrears rules call that mismatch out specifically;
        // a slightly later first paint is much cheaper than a finance number that
        // visibly changes under the reader.
        const critical = [
            task('KPIs', async () => {
                const kpiRes = await api.get(`/stats/building-kpis${kpiParam}`);
                setKpiData(kpiRes.data);
            }),
            // Balances, arrears and collection rate come from /finance/building-overview
            // (the PostgreSQL ledger-backed contract). Do not use /finance/summary here:
            // that legacy endpoint can still mix old collections into dashboard values.
            task('building finance overview', async () => {
                try {
                    const overviewRes = await api.get(`/finance/building-overview${yearParam}`);
                    setBuildingOverview(overviewRes.data);
                } catch (err) {
                    setBuildingOverview(null);
                    throw err;
                }
            }),
            task('portal bank balances', async () => {
                try {
                    const portalBankRes = await api.get('/finance/portal-bank-balances');
                    setPortalBankBalances(portalBankRes.data);
                } catch (err) {
                    setPortalBankBalances(null);
                    throw err;
                }
            }),
            task('arrears detail', async () => {
                try {
                    const arrearsRes = await api.get('/arrears/detail');
                    setArrearsDetailSummary(summariseArrearsDetail(arrearsRes.data));
                } catch (err) {
                    setArrearsDetailSummary(null);
                    throw err;
                }
            }),
        ];

        // --- Secondary wave: charts, feeds and panels further down the page. ---
        const secondary = [
            task('maintenance stats', async () => {
                const maintRes = await api.get('/analytics/maintenance-stats');
                setMaintenanceStats(maintRes.data);
            }),
            task('activities', async () => {
                const activityRes = await api.get('/analytics/activities?limit=15');
                setActivities(activityRes.data || []);
            }),
            task('triage requests', async () => {
                const [triageRes, overdueRes] = await Promise.all([
                    api.get('/workflow-requests?status=awaiting_review'),
                    api.get('/workflow-requests?status=overdue'),
                ]);
                setTriageRequests([...overdueRes.data, ...triageRes.data].slice(0, 5));
            }),
            task('triage stats', async () => {
                const statsRes = await api.get('/workflow-requests/stats/triage');
                setTriageStats(statsRes.data);
            }),
            // Building-wide levy actuals 2021-2026.
            task('levy benchmarks', async () => {
                const benchmarkRes = await api.get(`/analytics/levy-benchmarks${kpiParam}`);
                setLevyTrendData(benchmarkRes.data || []);
            }),
            task('admin stats', async () => {
                const adminStatsRes = await api.get('/admin/stats');
                setUserStats(adminStatsRes.data);
            }),
            task('sinking fund forecast', async () => {
                const forecastRes = await api.get('/analytics/sinking-fund-forecast?years=10');
                setSinkingFundForecast(forecastRes.data);
            }),
            task('compliance summary', async () => {
                const compRes = await api.get('/analytics/compliance-summary');
                setComplianceSummary(compRes.data);
            }),
            task('maintenance spend trend', async () => {
                const spendRes = await api.get('/analytics/maintenance/spend-trend');
                setSpendTrend(spendRes.data || []);
            }),
            // GL spend from finance expense_transactions — distinct from the
            // maintenance-work-order vendor scorecard above.
            task('expenses by supplier', async () => {
                const supplierRes = await api.get('/analytics/expenses-by-supplier?months=12');
                setSupplierSpend(supplierRes.data || null);
            }),
            task('PPM dashboard', async () => {
                const ppmRes = await api.get('/ppm/dashboard');
                setPpmDashboard(ppmRes.data);
            }),
            // PPMDashboardSummary (above) only returns counts, no item list —
            // /ppm/upcoming is the real source for individual scheduled items.
            task('upcoming PPM items', async () => {
                const ppmUpcomingRes = await api.get('/ppm/upcoming?days=60');
                setPpmUpcoming(Array.isArray(ppmUpcomingRes.data) ? ppmUpcomingRes.data : []);
            }),
            task('diff-since', async () => {
                const since = lastVisitTs ?? new Date(Date.now() - 3 * 86_400_000).toISOString();
                const diffYearParam = selectedYear ? `&year=${encodeURIComponent(selectedYear)}` : "";
                const diffRes = await api.get(
                    `/analytics/diff-since?since=${encodeURIComponent(since)}${diffYearParam}`
                );
                setDiff(diffRes.data);
            }),
            task('levy fairness', async () => {
                const fairnessRes = await api.get('/intelligence/levy-fairness');
                setFairness(fairnessRes.data);
            }),
            // Drives the ReserveRunwayChart shocks prop.
            task('capital shock', async () => {
                const shockRes = await api.get('/intelligence/capital-shock');
                setCapitalShock(shockRes.data);
            }),
        ];

        // Both waves are already in flight; only the critical one gates first paint.
        await Promise.all(critical);
        setLoading(false);
        await Promise.all(secondary);
    }, [api, lastVisitTs, selectedYear, token]);

    useEffect(() => {
        fetchDashboardData();
    }, [fetchDashboardData]);

    const quickActions = [
        {label: 'Finances', icon: DollarSign, href: '/financials/overview', color: 'bg-emerald-50 text-emerald-600'},
        {label: 'Users', icon: Users, href: '/admin/users', color: 'bg-blue-50 text-blue-600'},
        {label: 'Units', icon: Building, href: '/admin/owners-units', color: 'bg-indigo-50 text-indigo-600'},
        {label: 'Maintenance', icon: Wrench, href: '/maintenance', color: 'bg-orange-50 text-orange-600'},
        {label: 'Meetings', icon: Calendar, href: '/governance/meetings', color: 'bg-purple-50 text-purple-600'},
        {label: 'Admin', icon: Shield, href: '/admin/console', color: 'bg-slate-50 text-slate-600'},
    ];

    // Building Pulse comes from the BACKEND, not from a formula invented here.
    //
    // This block used to compute its own weighted score, and ManagementDashboard
    // computed a different one — the same building showed 31/100 here and 26/100
    // there on identical data. Reproduced exactly:
    //
    //   here:  0*0.35 + 0*0.30 + 100*0.20 + 95*0.10 + 24*0.05  = 31  (weighted)
    //   there: (0 + 0 + 100 + 0 + 30) / 5                       = 26  (mean)
    //
    // Two of the four divergences were bugs rather than differences of opinion:
    // the governance axis here could never score below 50 whatever the building
    // was doing (`Math.max(50, ...)`), and the other page fell back to
    // finance_health.score — a finance metric under a health label.
    //
    // CLAUDE.md: frontends render backend-calculated view models; no page
    // computes a metric independently of the one canonical service. That service
    // is health_score_service.compute_building_health_score.
    const {
        score: pulseScore,
        grade: pulseGrade,
        components: pulseComponents,
        unavailableComponents: pulseUnavailable,
    } = useBuildingPulse();
    const pulseAxes = pulseAxesFrom(pulseComponents);

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[600px]">
                <div className="text-center">
                    <motion.div animate={{rotate: 360}} transition={{repeat: Infinity, duration: 1, ease: "linear"}}
                                className="h-12 w-12 border-4 border-primary border-t-transparent rounded-full mx-auto"/>
                    <p className="mt-4 text-slate-500 font-medium">Loading Management Cockpit...</p>
                </div>
            </div>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: openDetail
     * Path: frontend/src/pages/dashboard/ManagerDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openDetail = (nextDetail) => setDetail(nextDetail);
    /**
     * @generated FunctionHeader
     * Function: openDetailRoute
     * Path: frontend/src/pages/dashboard/ManagerDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openDetailRoute = () => {
        if (detail?.actionHref) {
            router.push(detail.actionHref);
            setDetail(null);
        }
    };

    // Keep the classic dashboard numerically aligned with the current app-router
    // dashboard: fund and collection metrics come from /finance/building-overview;
    // the arrears display uses /arrears/detail because that is the true prior-year
    // arrears contract linked by the card.
    // GAP-DASH-001 P0-3: the fallback must be the canonical due-date rate, never kpiData.collection_rate
    // (which is full-year Fund Health / coverage and would show a mislabelled ~99% under "Collection Rate").
    const collectionRate = buildingOverview?.levies_paid_pct ?? kpiData?.due_date_collection_rate_pct ?? 0;
    const totalArrears = arrearsDetailSummary?.total ?? buildingOverview?.total_outstanding ?? kpiData?.total_arrears ?? 0;
    const arrearsUnits = arrearsDetailSummary?.units ?? buildingOverview?.units_in_arrears ?? kpiData?.units_in_arrears ?? 0;
    const adminFundLive = firstMoneyValue(
        portalBankBalances?.totals?.admin_balance,
        buildingOverview?.admin_fund_live_balance,
        fundDisplayBalance(buildingOverview?.admin_fund),
    ) ?? 0;
    const sinkingFundLive = firstMoneyValue(
        portalBankBalances?.totals?.sinking_balance,
        buildingOverview?.sinking_fund_live_balance,
        fundDisplayBalance(buildingOverview?.sinking_fund),
    ) ?? 0;
    const adminTrend = Array.isArray(buildingOverview?.admin_fund_trend) ? buildingOverview.admin_fund_trend : [];
    const sinkingTrend = Array.isArray(buildingOverview?.sinking_fund_trend)
        ? buildingOverview.sinking_fund_trend
        : (sinkingFundForecast?.projection?.slice(0, 6).map(p => p.closing_balance ?? p.balance ?? 0) ?? []);
    const arrearsMomentum = Array.isArray(buildingOverview?.arrears_trend) ? buildingOverview.arrears_trend : [];
    // Shared with the new Management cockpit — one normaliser, one set of aliases.
    const reserveProjection = normaliseReserveProjection(sinkingFundForecast?.projection);

    // Build compact calendar items from available data.
    // maintenance_schedules docs (GET /ppm/upcoming) use next_due_date/description —
    // PPMDashboardSummary (/ppm/dashboard) has no item list, only counts.
    const calendarItems = [
        ...ppmUpcoming.slice(0, 4).map(t => ({
            date: t.next_due_date,
            kind: 'PPM',
            title: t.description ?? 'PPM task',
            href: '/maintenance?tab=ppm',
        })),
        ...(complianceSummary?.upcoming ?? []).slice(0, 3).map(c => ({
            date: c.due_date,
            kind: 'INSP',
            title: c.label ?? c.title ?? 'Compliance task',
            href: '/compliance',
        })),
    ];

    // Build compliance watchlist from complianceSummary
    const complianceItems = complianceSummary?.upcoming ?? complianceSummary?.items ?? [];

    // Build vendor rows from spend trend grouped by vendor.
    // Neither the Mongo nor the Postgres path of /analytics/maintenance/spend-trend tracks
    // on-time completion yet (no due/completion timestamps on ops.work_orders), so `on_time`
    // is omitted server-side. Expose it as on_time_pct=null when not present so
    // VendorScorecardCard shows "—" rather than a fabricated percentage.
    const vendorRows = (() => {
        if (!spendTrend || !Array.isArray(spendTrend)) return [];
        const byVendor = {};
        spendTrend.forEach(r => {
            const v = r.vendor ?? r.vendor_name ?? 'Unknown';
            if (!byVendor[v]) byVendor[v] = { name: v, spend: 0, jobs: 0, on_time_pct: null, trend: [] };
            byVendor[v].spend += r.spend ?? 0;
            byVendor[v].jobs += r.jobs ?? 1;
            // "Volume trend" column plots job VOLUME, not spend (matches the new dashboard).
            byVendor[v].trend.push(r.jobs ?? 1);
            if (r.on_time != null) byVendor[v].on_time_pct = r.on_time;
        });
        return Object.values(byVendor).sort((a, b) => b.spend - a.spend).slice(0, 5);
    })();

    // Fairness card data — prefer lbfi.current_score (API v2), fall back to legacy flat fields.
    const fairnessScore = fairness?.lbfi?.current_score ?? fairness?.lei_score ?? fairness?.score ?? fairness?.lbfi_score ?? null;
    const fairnessGrade = fairness?.grade ?? (fairnessScore >= 88 ? 'Good' : fairnessScore >= 75 ? 'Watch' : fairnessScore != null ? 'Review' : undefined);
    // Build group impacts: prefer impact_by_group array (v2), fall back to legacy overpay/underpay fields.
    const fairnessGroupImpacts = (() => {
        if (Array.isArray(fairness?.impact_by_group)) {
            return fairness.impact_by_group.map(g => ({
                group_name: g.group ?? g.group_name ?? g.label ?? '—',
                net_subsidy: g.net_subsidy ?? g.delta ?? 0,
            }));
        }
        const groups = [];
        if (fairness?.overpay_group) groups.push({ group_name: fairness.overpay_group, net_subsidy: fairness?.overpay_amount ?? 1 });
        if (fairness?.underpay_group) groups.push({ group_name: fairness.underpay_group, net_subsidy: -(fairness?.underpay_amount ?? 1) });
        return groups;
    })();
    const fairnessDrivers = fairness?.top_drivers ?? fairness?.drivers ?? [];

    // Levy fairness group labels — translate internal group keys to human-readable names.
    // Mongo path (_group_key() in levy_fairness_service.py) returns singular Title Case
    // keys: "Apartment", "Townhouse", "Villa", "Retail", "Commercial", "Other". PG path
    // returns "Unit X" per-lot labels, which need no mapping (fall through unchanged).
    const FAIRNESS_GROUP_LABELS = {
        Apartment: 'Apartments', Townhouse: 'Townhouses', Villa: 'Villas',
        Retail: 'Retail', Commercial: 'Commercial', Other: 'Other Lots',
    };
    const fairnessGroupImpactsLabelled = fairnessGroupImpacts.map(g => ({
        ...g,
        group_name: FAIRNESS_GROUP_LABELS[g.group_name] ?? g.group_name,
    }));


    // Use Admin + Sinking totals per year for the pulse trend sparkline
    const pulseTrend  = levyTrendData.map(r => (r.Admin ?? r.admin ?? 0) + (r.Sinking ?? r.sinking ?? 0)).filter(n => Number.isFinite(n)).slice(-8);
    /**
     * @generated FunctionHeader
     * Function: routeForPulseAxis
     * Path: frontend/src/pages/dashboard/ManagerDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const routeForPulseAxis = (axis) => {
        // Axis names now come from the backend health score
        // (financial/maintenance/compliance/engagement/dispute), not the old
        // locally-invented set (cash/compliance/maintenance/governance/community).
        // Both vocabularies are matched so the card keeps working either way —
        // an axis whose click goes nowhere is exactly the dead-tile problem the
        // clickable-card rule exists to prevent.
        const key = String(axis || "").toLowerCase();
        if (key.includes("financial") || key.includes("cash")) return "/financials/overview";
        if (key.includes("compliance")) return "/compliance";
        if (key.includes("maintenance")) return "/maintenance";
        if (key.includes("dispute") || key.includes("governance")) return "/governance/meetings";
        if (key.includes("engagement") || key.includes("community")) return "/community";
        return "/community";
    };
    /**
     * @generated FunctionHeader
     * Function: routeForDiffItem
     * Path: frontend/src/pages/dashboard/ManagerDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const routeForDiffItem = (item) => {
        const kind = String(item?.kind || item?.label || "").toLowerCase();
        if (kind.includes("arrears")) return "/intelligence/debt-recovery";
        if (kind.includes("sla")) return OVERDUE_REQUEST_QUEUE_HREF;
        if (kind.includes("request")) return REQUEST_QUEUE_HREF;
        if (kind.includes("cash")) return "/financials/overview";
        if (kind.includes("agm") || kind.includes("meeting")) return "/governance/meetings";
        if (kind.includes("compliance")) return "/compliance";
        return "/community";
    };
    /**
     * @generated FunctionHeader
     * Function: routeForComplianceItem
     * Path: frontend/src/pages/dashboard/ManagerDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const routeForComplianceItem = (item) => (
        item?.source === "ppm" ? "/maintenance?tab=ppm" : "/compliance"
    );

    return (
        <div className="space-y-6 pb-12" data-testid="manager-dashboard">
            {/* Property Pulse Header */}
            <motion.div
                initial={{opacity: 0, y: -20}}
                animate={{opacity: 1, y: 0}}
                className="bg-slate-900 rounded-3xl p-1 shadow-[0_20px_50px_rgba(0,0,0,0.15)] overflow-hidden relative"
            >
                <div
                    className="absolute top-0 left-1/4 w-1/2 h-full bg-primary/20 blur-[100px] animate-pulse pointer-events-none"/>
                <div
                    className="bg-slate-800/40 backdrop-blur-2xl rounded-[22px] px-8 py-5 flex flex-wrap items-center justify-between gap-8 relative z-10 border border-white/5">
                    <div className="flex items-center gap-8">
                        <div className="hidden sm:flex flex-col cursor-pointer group"
                             onClick={() => router.push('/financials/overview')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Collection</span>
                            <span className="text-sm font-black text-white flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)]"/>
                                {collectionRate}%
              </span>
                        </div>
                        <div className="flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/admin/users')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Pending</span>
                            <span
                                className="text-sm font-black text-orange-400">{userStats?.pending_users || 0} Users</span>
                        </div>
                        <div className="flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/maintenance')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Active Works</span>
                            <span
                                className="text-sm font-black text-white">{maintenanceStats?.open_requests || 0} Open</span>
                        </div>
                        <div className="hidden lg:flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/financials/overview')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Invoices</span>
                            <span
                                className="text-sm font-black text-red-400">{userStats?.pending_invoices_count || 0} Active</span>
                        </div>
                    </div>
                    <div className="flex items-center gap-3">
                        <YearSelector/>
                        <Button size="lg" variant="outline" onClick={() => router.push('/reports')}
                                className="bg-transparent text-white border-white/20 hover:bg-white/10 font-black rounded-2xl px-5">
                            <FileSpreadsheet className="h-4 w-4"/>
                            Reports
                        </Button>
                        <Button size="lg" onClick={() => router.push('/owner-view')}
                                className="bg-white text-slate-900 hover:bg-primary hover:text-white font-black rounded-2xl shadow-xl transition-all duration-300 px-8">
                            Resident View
                        </Button>
                    </div>
                </div>
            </motion.div>

            {/* ── Row 1: Building Pulse + Since Last Visit ──────────────────── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.05}}>
                <div className="grid grid-cols-12 gap-6">
                    <PulseScoreCard
                        className="col-span-12 lg:col-span-5"
                        score={pulseScore}
                        delta={0}
                        grade={pulseGrade}
                        breakdown={pulseAxes}
                        unavailableComponents={pulseUnavailable}
                        trend={pulseTrend}
                        onOpenDetails={() => router.push("/intelligence/building")}
                        onSelectAxis={(axis) => router.push(routeForPulseAxis(axis.k))}
                        onSelectTrend={() => router.push("/financials/levy-kpi")}
                    />
                    <SinceLastVisit
                        className="col-span-12 lg:col-span-7"
                        daysSince={diff?.days_since ?? 1}
                        items={diff?.items ?? []}
                        onItemSelect={(item) => router.push(routeForDiffItem(item))}
                        onMarkRead={() => {
                            try { localStorage.setItem('mgr_last_visit', new Date().toISOString()); } catch {}
                        }}
                    />
                </div>
            </motion.div>

            {/* ── Row 2: Triage Queue ───────────────────────────────────────── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.1}}>
                <TriageQueue
                    items={triageRequests}
                    stats={triageStats}
                    queueHref={OVERDUE_REQUEST_QUEUE_HREF}
                    onAction={(item) => router.push(item.id ? "/requests/" + item.id : OVERDUE_REQUEST_QUEUE_HREF)}
                />
            </motion.div>

            {/* ── Row 3: Cash Position + Levy Fairness + Calendar ──────────── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.15}}>
                <div className="grid grid-cols-12 gap-6">
                    {/* Cash Position — v2 component with SparklineMini trend lines */}
                    <CashPositionCard
                        className="col-span-12 lg:col-span-5"
                        admin={{
                            balance: adminFundLive,
                            trend: adminTrend,
                            target: buildingOverview?.admin_fund_target ?? null,
                        }}
                        sinking={{
                            balance: sinkingFundLive,
                            trend: sinkingTrend,
                            target: buildingOverview?.sinking_fund_target ?? null,
                        }}
                        arrears={{
                            total: totalArrears,
                            units: arrearsUnits,
                            delta_pct: buildingOverview?.arrears_delta_pct ?? null,
                            momentum: arrearsMomentum,
                        }}
                        collectionRatePct={collectionRate}
                        collectionDeltaPct={buildingOverview?.collection_delta_pct ?? null}
                        collectionTargetPct={buildingOverview?.collection_target_pct ?? null}
                        onOpenAdmin={() => openDetail({
                            title: 'Admin Fund',
                            description: 'Live administration fund position',
                            actionLabel: 'Open finance',
                            actionHref: '/financials/overview',
                            content: <p className="text-sm font-semibold text-slate-600">Current live balance is <strong>{formatCurrency(adminFundLive)}</strong>{buildingOverview?.admin_fund_target != null ? ` against a target of ${formatCurrency(buildingOverview.admin_fund_target)}.` : '.'}</p>,
                        })}
                        onOpenSinking={() => openDetail({
                            title: 'Sinking Fund',
                            description: 'Live sinking fund position',
                            actionLabel: 'Open projections',
                            actionHref: '/intelligence/projections',
                            content: <p className="text-sm font-semibold text-slate-600">Current live balance is <strong>{formatCurrency(sinkingFundLive)}</strong>{buildingOverview?.sinking_fund_target != null ? ` against a target of ${formatCurrency(buildingOverview.sinking_fund_target)}.` : '.'}</p>,
                        })}
                        onOpenArrears={() => openDetail({
                            title: 'Arrears and collection',
                            description: 'Outstanding levies and collection momentum',
                            actionLabel: 'Open arrears',
                            actionHref: '/intelligence/debt-recovery',
                            content: <p className="text-sm font-semibold text-slate-600"><strong>{formatCurrency(totalArrears)}</strong> outstanding across <strong>{arrearsUnits}</strong> unit{arrearsUnits === 1 ? '' : 's'} with collection at <strong>{Number(collectionRate || 0).toFixed(2)}%</strong>.</p>,
                        })}
                    />

                    {/* Levy Fairness Index — v2 component with ring gauge + driver bars */}
                    <LevyFairnessCard
                        className="col-span-12 lg:col-span-4"
                        score={fairnessScore}
                        grade={fairnessGrade}
                        groupImpacts={fairnessGroupImpactsLabelled}
                        drivers={fairnessDrivers}
                        onClick={() => router.push('/intelligence/levy-fairness')}
                    />

                    {/* Compact Calendar */}
                    <CompactCalendar
                        className="col-span-12 lg:col-span-3"
                        items={calendarItems}
                    />
                </div>
            </motion.div>

            {/* ── Row 4: Reserve Runway + Compliance Watch ─────────────────── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.2}}>
                <div className="grid grid-cols-12 gap-6">
                    <ReserveRunwayChart
                        className="col-span-12 lg:col-span-8"
                        projection={reserveProjection}
                        shocks={capitalShock?.capital_shock_index?.rows}
                        onSelectYear={(row) => router.push("/intelligence/projections?year=" + encodeURIComponent(row?.year ?? selectedYear ?? ""))}
                    />

                    {/* Compliance Watchlist */}
                    <Card className="col-span-12 lg:col-span-4 border-none shadow-md overflow-hidden">
                        <CardHeader className="pb-2">
                            <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">Compliance watch</div>
                            <CardTitle className="text-lg font-black">Next 100 days</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {complianceItems.length === 0 ? (
                                <div className="text-sm text-slate-400 font-semibold py-4 text-center">
                                    {complianceSummary ? 'All clear — no upcoming items' : 'Loading...'}
                                </div>
                            ) : (
                                complianceItems.slice(0, 6).map((c, i) => {
                                    const isOverdue = c.status === 'overdue' || (c.days_left != null && c.days_left < 0);
                                    const isSoon = !isOverdue && c.days_left != null && c.days_left <= 30;
                                    const bg = isOverdue ? 'bg-rose-50 ring-rose-200' : isSoon ? 'bg-amber-50 ring-amber-200' : 'bg-white ring-slate-200';
                                    const dayText = c.days_left == null ? c.due ?? '' : c.days_left < 0 ? `${Math.abs(c.days_left)}d late` : `${c.days_left}d`;
                                    return (
                                        <button key={i} type="button" onClick={() => router.push(routeForComplianceItem(c))} className={`w-full flex items-center gap-3 rounded-xl ring-1 ${bg} p-3 text-left hover:ring-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-400`}>
                                            <div className="flex-1 min-w-0">
                                                <div className="text-sm font-bold text-slate-900 truncate">{c.label ?? c.title ?? 'Compliance item'}</div>
                                                <div className="text-[11px] text-slate-500">Due {c.due_date ?? c.due ?? '—'}</div>
                                            </div>
                                            <div className={`text-xs font-black ${isOverdue ? 'text-rose-700' : isSoon ? 'text-amber-700' : 'text-slate-500'}`}>
                                                {dayText}
                                            </div>
                                        </button>
                                    );
                                })
                            )}
                        </CardContent>
                    </Card>
                </div>
            </motion.div>

            {/* ── Row 5: Vendor Scorecard + Activity Feed ───────────────────── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.25}}>
                <div className="grid grid-cols-12 gap-6">
                    {/* Vendor Scorecard — v2 component with spend sparklines + on-time colour coding */}
                    <VendorScorecardCard
                        className="col-span-12 lg:col-span-7"
                        vendors={vendorRows}
                        onSelect={() => router.push('/maintenance?tab=contractors')}
                        onAll={() => router.push('/maintenance?tab=contractors')}
                    />

                    {/* Activity Feed */}
                    <div className="col-span-12 lg:col-span-5">
                        <ActivityFeed activities={activities} delay={0.1}/>
                    </div>
                </div>
            </motion.div>

            {/* ── Row 5b: Expenses by supplier (GL spend — distinct from vendor performance) ── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.28}}>
                <div className="grid grid-cols-12 gap-6">
                    <ExpensesBySupplierCard
                        className="col-span-12"
                        suppliers={supplierSpend?.suppliers || []}
                        windowMonths={supplierSpend?.window_months || 12}
                        onAll={() => router.push('/financials/overview')}
                    />
                </div>
            </motion.div>

            {/* ── Row 6: KPI stat strip ─────────────────────────────────────── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.3}}>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                    {/* Direct grid child (not wrapped in <Link>) so it stretches to the same cell
                        height as its siblings — a Link wrapper renders inline and sized the card
                        differently. Navigation via onClick, matching the other tiles. */}
                    <KPIStatCard title="Approvals" value={userStats?.pending_invoices_count || 0} subtext="Pending review" icon={ShieldCheck} colorClass="bg-purple-50 text-purple-600" delay={0} onClick={() => router.push('/requests/my-approvals')}/>
                    <KPIStatCard title="Active Maintenance" value={maintenanceStats?.open_requests || 0} subtext="Building-wide" icon={Wrench} colorClass="bg-orange-50 text-orange-600" delay={0} onClick={() => router.push('/maintenance')}/>
                    <KPIStatCard title="Safety Compliance" value={complianceSummary?.percentage != null ? `${complianceSummary.percentage}%` : '—'} subtext={complianceSummary?.total ? `${complianceSummary.completed ?? 0}/${complianceSummary.total} tasks` : 'No compliance tasks tracked'} icon={ShieldCheck} colorClass={complianceSummary?.overdue > 0 ? 'bg-red-50 text-red-600' : 'bg-emerald-50 text-emerald-600'} delay={0} onClick={() => router.push('/compliance')}/>
                    {/* health_score is null when the building tracks NO compliance items. That is
                        not 100% and must not render green — an untracked building looked perfectly
                        compliant on this tile, which is the worst failure mode for a safety metric. */}
                    <KPIStatCard title="PPM Health" value={ppmDashboard?.health_score != null ? `${ppmDashboard.health_score}%` : '—'} subtext={!ppmDashboard ? 'Loading' : ppmDashboard.health_score == null ? 'No compliance items tracked' : `${ppmDashboard.overdue_count} overdue`} icon={ShieldCheck} colorClass={ppmDashboard?.health_score == null ? 'bg-slate-50 text-slate-500' : ppmDashboard.health_score >= 80 ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'} delay={0} onClick={() => router.push('/maintenance?tab=ppm')}/>
                    {/* KPIStatCard's trend convention is generic (positive=green/up = "good"). The
                        backend's trend_pct is the opposite for resolution time: negative = improving
                        (faster), positive = worsening (slower) — so the sign is inverted here to keep
                        "faster resolution" showing as the green/good badge. */}
                    <KPIStatCard title="Resolution Time" value={maintenanceStats?.avg_resolution_days != null ? `${maintenanceStats.avg_resolution_days}d` : '—'} subtext={maintenanceStats?.avg_resolution_days != null ? 'Avg. this month' : 'No resolved requests yet'} trend={maintenanceStats?.trend_pct != null ? -maintenanceStats.trend_pct : undefined} icon={Clock} colorClass="bg-blue-50 text-blue-600" delay={0} onClick={() => router.push('/maintenance')}/>
                    <KPIStatCard title="Active Listings" value={userStats?.active_listings || 0} subtext="Community marketplace" icon={ShoppingBag} colorClass="bg-purple-50 text-purple-600" delay={0} onClick={() => router.push('/community/marketplace')}/>
                </div>
            </motion.div>

            {/* ── Row 7: Revenue trend chart ────────────────────────────────── */}
            <motion.div initial={{opacity: 0, y: 16}} animate={{opacity: 1, y: 0}} transition={{delay: 0.35}}>
                <ChartCard
                    title="Annual Levies Raised by Fund"
                    description="Building-wide levy budget raised each year (admin vs sinking) — amounts levied, not cash collected"
                    delay={0}
                    onClick={() => router.push('/financials/overview')}
                    actionLabel="Open finance"
                    detailTitle="Annual Levies Raised by Fund"
                    detailDescription="Amounts levied per year by fund (budgeted/raised, not cash collected)"
                    detailContent={(() => {
                        const latest = levyTrendData?.[levyTrendData.length - 1] || {};
                        const adminTotal = levyTrendData.reduce((sum, row) => sum + Number(row.Admin ?? row.admin ?? 0), 0);
                        const sinkingTotal = levyTrendData.reduce((sum, row) => sum + Number(row.Sinking ?? row.sinking ?? 0), 0);
                        return (
                            <div className="space-y-4">
                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                                    <div className="rounded-xl bg-white ring-1 ring-slate-200 p-3">
                                        <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">Latest year</div>
                                        <div className="text-lg font-black text-slate-900">{latest.year || '—'}</div>
                                    </div>
                                    <div className="rounded-xl bg-white ring-1 ring-emerald-200 p-3">
                                        <div className="text-[10px] font-black uppercase tracking-widest text-emerald-600">Admin fund</div>
                                        <div className="text-lg font-black text-slate-900">{formatCurrency(latest.Admin ?? latest.admin ?? 0)}</div>
                                    </div>
                                    <div className="rounded-xl bg-white ring-1 ring-violet-200 p-3">
                                        <div className="text-[10px] font-black uppercase tracking-widest text-violet-600">Sinking fund</div>
                                        <div className="text-lg font-black text-slate-900">{formatCurrency(latest.Sinking ?? latest.sinking ?? 0)}</div>
                                    </div>
                                </div>
                                <p className="text-sm font-semibold text-slate-600">
                                    The chart compares the annual levy amounts raised (budgeted, not cash collected) across the admin and sinking funds. Across the loaded series, admin totals {formatCurrency(adminTotal)} and sinking totals {formatCurrency(sinkingTotal)}.
                                </p>
                            </div>
                        );
                    })()}
                >
                    <AreaChart data={levyTrendData} margin={{top: 10, right: 10, left: -10, bottom: 0}}>
                        <defs>
                            <linearGradient id="mgColorAdmin" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#16A34A" stopOpacity={0.25}/><stop offset="95%" stopColor="#16A34A" stopOpacity={0}/>
                            </linearGradient>
                            <linearGradient id="mgColorSinking" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#7C3AED" stopOpacity={0.25}/><stop offset="95%" stopColor="#7C3AED" stopOpacity={0}/>
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9"/>
                        <XAxis dataKey="year" axisLine={false} tickLine={false} tick={{fill: '#64748b', fontSize: 12, fontWeight: 600}} dy={10}/>
                        <YAxis axisLine={false} tickLine={false} tick={{fill: '#64748b', fontSize: 12, fontWeight: 600}} tickFormatter={(v) => `$${(v/1000).toFixed(0)}k`}/>
                        <RechartsTooltip contentStyle={{borderRadius: '12px', border: 'none', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)', padding: '12px'}} formatter={(v, n) => [formatCurrency(v), n]}/>
                        <Legend wrapperStyle={{paddingTop: '8px', fontSize: '12px', fontWeight: 700}}/>
                        <Area type="monotone" dataKey="Admin" name="Admin Fund" stroke="#16A34A" fillOpacity={1} fill="url(#mgColorAdmin)" strokeWidth={2} animationDuration={1200}/>
                        <Area type="monotone" dataKey="Sinking" name="Sinking Fund" stroke="#7C3AED" fillOpacity={1} fill="url(#mgColorSinking)" strokeWidth={2} animationDuration={1200}/>
                    </AreaChart>
                </ChartCard>
            </motion.div>

            {/* ── Row 8: Quick-action tools ─────────────────────────────────── */}
            <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} transition={{delay: 0.4}}>
                <Card className="border-none shadow-lg bg-white overflow-hidden">
                    <CardHeader className="bg-slate-50/50 border-b">
                        <CardTitle className="text-lg font-bold">Management Cockpit Tools</CardTitle>
                    </CardHeader>
                    <CardContent className="p-6">
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                            {quickActions.map((action, index) => (
                                <button key={index} onClick={() => router.push(action.href)}
                                    className="flex flex-col items-center justify-center p-6 rounded-3xl border border-slate-50 bg-white hover:border-primary/20 hover:bg-primary/5 transition-all duration-300 group shadow-sm hover:shadow-xl hover:-translate-y-1">
                                    <div className={`p-4 rounded-2xl ${action.color} mb-4 group-hover:scale-110 transition-transform duration-300 shadow-sm group-hover:shadow-md`}>
                                        <action.icon size={28} strokeWidth={2.5}/>
                                    </div>
                                    <span className="font-black text-[11px] text-slate-500 group-hover:text-primary uppercase tracking-widest transition-colors">{action.label}</span>
                                </button>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            </motion.div>

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
    );
};

export default ManagerDashboard;
