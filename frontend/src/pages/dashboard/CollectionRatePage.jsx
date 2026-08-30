"use client";
// @ts-nocheck
// @featuretrace:levy — Collection rate gauge + unit-level performance table.
// Layer: frontend
// Data flow: this page -> GET /finance/kpi-contract?year=, GET /finance/summary?year=,
//            GET /unit-levy-ledger?year=&limit=200 -> finance.py -> unit_levy_ledger (building-scoped).
// Related: backend/routers/finance.py
//           frontend/src/pages/dashboard/FinancePage.tsx
// Collection: unit_levy_ledger

// GAP-FIN-014: building-wide collectionRate/inGracePct/arrearsPct/notYetDue and the Unit Status
// Distribution chart are now sourced from GET /finance/kpi-contract (backend-derived, ledger-quality
// reconciled against canonical `units`) — see kpiContract.collection_mix / kpiContract.unit_counts
// below. This page falls back to the legacy client-side total_paid/total_levied calc only if the
// kpi-contract call fails, so the page still renders something rather than going blank.
// The per-row FULLY PAID/PARTIAL/UNPAID badges in the Unit-Level Performance table remain
// client-side, computed per ledger row from total_paid/total_levied — there is no backend
// per-unit-status array in the kpi-contract (it's a building-wide aggregate contract), and each
// row's own ledger fields are still ledger-derived data, just not backend-pre-computed per row.
import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Legend,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip as RechartsTooltip,
    XAxis,
    YAxis
} from 'recharts';
import { ArrowLeft, ChevronRight, Download, Filter, Info } from 'lucide-react';
import Link from 'next/link';
import { formatCurrency } from '../../lib/utils';
import YearSelector from '../../components/widgets/YearSelector';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../components/ui/tooltip';
import {
    buildCollectionRateSummary,
    clampedCollectionPercentage,
    collectionPercentage
} from '../../lib/finance/financeTransforms';
/**
 * @generated FunctionHeader
 * Function: CollectionRatePage
 * Path: frontend/src/pages/dashboard/CollectionRatePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CollectionRatePage = () => {
    const router = useRouter();
    const {api, selectedYear} = useAuth();
    const [loading, setLoading] = useState(true);
    const [stats, setStats] = useState(null);
    const [ledger, setLedger] = useState([]);
    const [kpiContract, setKpiContract] = useState(null);

    const fetchData = useCallback(async () => {
        if (!selectedYear) return;
        setLoading(true);
        try {
            // Isolate each endpoint (GAP-FIN-033) — a single failing call (e.g. a
            // building/year with no reconciled data yet) must not blank every figure
            // on this page. Mirrors the isolation FinancePage.tsx already got.
            const [summaryRes, ledgerRes, kpiRes] = await Promise.all([
                api.get(`/finance/summary?year=${selectedYear}`).catch((err) => {
                    console.error("Failed to fetch /finance/summary", err);
                    return {data: null};
                }),
                api.get(`/unit-levy-ledger?year=${selectedYear}&limit=200`).catch((err) => {
                    console.error("Failed to fetch /unit-levy-ledger", err);
                    return {data: []};
                }),
                api.get(`/finance/kpi-contract?year=${selectedYear}`).catch(() => ({data: null})),
            ]);
            setStats(summaryRes.data || {});
            setLedger(ledgerRes.data || []);
            setKpiContract(kpiRes.data || null);
        } catch (err) {
            console.error("Failed to fetch collection data", err);
        } finally {
            setLoading(false);
        }
    }, [api, selectedYear]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const ls = stats?.unit_ledger_summary || {};
    const igs = stats?.in_grace_summary || {};
    const ps = stats?.portal_summary || null;  // Strata Web portal snapshot (may be null before first scrape)
    const uc = kpiContract?.unit_counts || null;
    const portalCrossCheck = kpiContract?.portal_cross_check || null;
    const portalClearUnits = ps ? Number(ps.clear_count || 0) + Number(ps.credit_count || 0) : 0;

    // GAP-FIN-014: prefer the backend kpi-contract (ledger-quality-reconciled,
    // building-wide) for every aggregate figure on this page. Only fall back to
    // the legacy client-side calc when the kpi-contract call failed/is unavailable,
    // so the page still renders something instead of going blank.
    const collectionSummary = buildCollectionRateSummary(stats, kpiContract);
    const annualLevyTotal = collectionSummary.annualLevyTotal;
    const totalCollectedYtd = collectionSummary.totalCollectedYtd;
    const collectionRate = collectionSummary.collectionRate;
    const inGraceAmount = collectionSummary.inGraceAmount;
    const trueArrearsAmount = collectionSummary.trueArrearsAmount;
    const inGracePct = collectionSummary.inGracePct;
    const arrearsPct = collectionSummary.arrearsPct;
    const graceDays = igs.grace_period_days || 14;
    const inGracePeriods = igs.in_grace_periods || [];
    const notYetDueAmount = collectionSummary.notYetDueAmount;
    const notYetDuePct = collectionSummary.notYetDuePct;
    // GAP-FIN-035 (2026-08-03): unapplied credit + receipts for periods not
    // yet due. Displayed as its own sub-text — never folded into
    // collectionRate above, per docs/architecture/financial-summary-analysis-of-issues.md Rule 53.
    const collectedInAdvance = collectionSummary.collectedInAdvance;
    /**
     * @generated FunctionHeader
     * Function: getUnitType
     * Path: frontend/src/pages/dashboard/CollectionRatePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getUnitType = (entry) => {
        if (entry.property_type && entry.property_type !== '') return entry.property_type;
        if (entry.unit_number?.startsWith('TH')) return 'Townhouse';
        if (entry.unit_number?.startsWith('UA')) return 'Apartment';
        return '—';
    };

    const chartData = [
        {name: 'Collected', value: totalCollectedYtd || 0, color: '#10b981'},
        {name: 'In Grace Period', value: inGraceAmount, color: '#f59e0b'},
        {name: 'True Arrears', value: trueArrearsAmount, color: '#f43f5e'},
        {name: 'Not Yet Due', value: notYetDueAmount, color: '#94a3b8'},
    ].filter(d => d.value > 0);

    const statusDistribution = [
        {name: 'Paid Up', value: uc?.paid_up ?? (ls.units_paid_up || 0), color: '#10b981'},
        {name: 'Owing', value: uc?.owing ?? (ls.units_owing || 0), color: '#f43f5e'},
        {name: 'In Credit', value: uc?.credit ?? (ls.units_credit || 0), color: '#3b82f6'},
    ];

    if (loading) return (
        <div className="flex items-center justify-center min-h-[400px]">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
        </div>
    );

    return (
        <TooltipProvider>
        <div className="min-h-screen bg-slate-50/50 p-6 md:p-10 space-y-6 pb-24">
            {/* Breadcrumbs */}
            <nav className="flex items-center gap-2 text-xs font-bold text-slate-400 uppercase tracking-widest mb-4">
                <Link href="/financials/overview" className="hover:text-indigo-600 transition-colors">Financials</Link>
                <ChevronRight size={10}/>
                <Link href="/financials/overview" className="hover:text-indigo-600 transition-colors">Finance</Link>
                <ChevronRight size={10}/>
                <span className="text-slate-900">Collection Rate Analysis</span>
            </nav>

            {/* Header */}
            <div className="flex flex-col md:flex-row items-start md:items-center gap-4 justify-between">
                <div className="flex items-center gap-4">
                    <button
                        onClick={() => router.back()}
                        className="p-2 rounded-xl border bg-white hover:bg-slate-50 transition-colors"
                    >
                        <ArrowLeft size={18} className="text-slate-500"/>
                    </button>
                    <div>
                        <h1 className="text-2xl font-black text-slate-900">Collection Rate Analysis</h1>
                        <p className="text-slate-500 text-sm font-medium mt-0.5">FY {selectedYear} Performance
                            Metrics</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <YearSelector/>
                    <Button variant="outline" className="rounded-xl text-xs font-bold gap-2">
                        <Download size={14}/> Download Report
                    </Button>
                </div>
            </div>

            {/* Primary Metrics */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-2 bg-indigo-600"/>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-[10px] font-black uppercase tracking-widest text-slate-400">Overall
                            Collection Rate</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-baseline gap-2">
                            <span className="text-4xl font-black text-slate-900">{collectionRate.toFixed(1)}%</span>
                        </div>
                        <div className="mt-4 h-2 w-full bg-slate-100 rounded-full overflow-hidden">
                            <div className="h-full bg-indigo-600 rounded-full" style={{width: `${collectionRate}%`}}/>
                        </div>
                        <p className="text-xs text-slate-500 mt-2">Of amounts due as of today</p>
                        {collectedInAdvance > 0 && (
                            <p className="text-[10px] text-blue-600 font-medium mt-1">
                                + {formatCurrency(collectedInAdvance)} collected in advance for future periods,
                                excluded from this rate
                            </p>
                        )}
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-2 bg-emerald-500"/>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-[10px] font-black uppercase tracking-widest text-slate-400">Total
                            Collected</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-black text-slate-900">{formatCurrency(totalCollectedYtd)}</p>
                        <p className="text-xs text-slate-500 mt-1">Collected YTD
                            (of {formatCurrency(annualLevyTotal)} annual)</p>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-2 bg-rose-500"/>
                    <CardHeader className="pb-2">
                        <CardTitle className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-slate-400">
                            True Arrears
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Info className="h-3 w-3 text-slate-300 cursor-help" aria-label="What is True Arrears?"/>
                                </TooltipTrigger>
                                <TooltipContent className="text-xs max-w-xs normal-case tracking-normal font-medium">
                                    Total outstanding levies, minus the portion still within the {graceDays}-day
                                    grace window. This is the ledger figure and is authoritative — if the Strata
                                    Web Portal snapshot below shows a different number, the ledger figure here is
                                    the one to trust.
                                </TooltipContent>
                            </Tooltip>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-4xl font-black text-rose-600">{formatCurrency(trueArrearsAmount)}</p>
                        <p className="text-xs text-slate-500 mt-1">{ls.units_owing || 0} units with balance owing</p>
                        <p className="text-[10px] text-rose-500 font-semibold mt-1">{arrearsPct.toFixed(1)}% of total
                            levied</p>
                        {inGraceAmount > 0 && (
                            <p className="text-[10px] text-amber-600 font-medium mt-1">
                                excl. {formatCurrency(inGraceAmount)} still within grace window
                            </p>
                        )}
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-2 bg-amber-500"/>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-[10px] font-black uppercase tracking-widest text-slate-400">In Grace
                            Period</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {inGraceAmount > 0 ? (
                            <>
                                <div className="flex items-baseline gap-2">
                                    <p className="text-4xl font-black text-amber-600">{formatCurrency(inGraceAmount)}</p>
                                </div>
                                <p className="text-xs text-slate-500 mt-1">
                                    {inGracePct.toFixed(1)}% of total levied
                                </p>
                                <div className="mt-3 flex flex-wrap gap-1">
                                    {inGracePeriods.map(p => (
                                        <Badge key={p}
                                               className="bg-amber-100 text-amber-700 border-none text-[10px]">{p} pending</Badge>
                                    ))}
                                </div>
                                <p className="text-[10px] text-slate-400 mt-2">
                                    Within {graceDays}-day grace window — may still be processing
                                </p>
                            </>
                        ) : (
                            <>
                                <p className="text-4xl font-black text-slate-300">$0.00</p>
                                <p className="text-xs text-slate-400 mt-1">No payments currently in grace window</p>
                            </>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* Strata Web Portal Snapshot — cross-check only, not used in calculations */}
            {ps && (
                <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                    <div className="h-1 bg-violet-500"/>
                    <CardContent className="p-5">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                            <div>
                                <p className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-1">
                                    Strata Web Portal Snapshot
                                </p>
                                <p className="text-xs text-slate-500">
                                    Read-only cross-check from last scraper run
                                    {ps.updated_at && (
                                        <> · {new Date(ps.updated_at).toLocaleDateString('en-AU', {
                                            day: 'numeric',
                                            month: 'short',
                                            year: 'numeric'
                                        })}</>
                                    )}
                                </p>
                            </div>
                            {ps.risk_level && (
                                <span className={`text-[10px] font-black px-2 py-1 rounded-full ${
                                    ps.risk_level === 'LOW' ? 'bg-emerald-100 text-emerald-700' :
                                        ps.risk_level === 'MEDIUM' ? 'bg-amber-100 text-amber-700' :
                                            'bg-rose-100 text-rose-700'
                                }`}>{ps.risk_level} RISK</span>
                            )}
                        </div>
                        <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
                            <div>
                                <p className="text-[10px] text-slate-400 uppercase tracking-wide">Portal Snapshot Arrears</p>
                                <p className="text-lg font-black text-rose-600">{formatCurrency(ps.arrears_total || 0)}</p>
                                <p className="text-[10px] text-slate-400">cross-check only - {ps.arrears_count || 0} units</p>
                            </div>
                            <div>
                                <p className="text-[10px] text-slate-400 uppercase tracking-wide">Portal Credits</p>
                                <p className="text-lg font-black text-emerald-600">{formatCurrency(ps.credit_total || 0)}</p>
                                <p className="text-[10px] text-slate-400">{ps.credit_count || 0} units</p>
                            </div>
                            <div>
                                <p className="text-[10px] text-slate-400 uppercase tracking-wide">Clear</p>
                                <p className="text-lg font-black text-slate-600">{portalClearUnits} units</p>
                                {(ps.credit_count || 0) > 0 && (
                                    <p className="text-[10px] text-slate-400">includes {ps.credit_count} in credit</p>
                                )}
                            </div>
                            <div>
                                <p className="text-[10px] text-slate-400 uppercase tracking-wide">Portal Rate</p>
                                <p className="text-lg font-black text-violet-600">
                                    {ps.collection_rate != null ? `${ps.collection_rate.toFixed(1)}%` : '—'}
                                </p>
                                <p className="text-[10px] text-slate-400">lot-based proxy</p>
                            </div>
                        </div>
                        {portalCrossCheck?.requires_reconciliation && (
                            <div
                                data-testid="portal-reconciliation-warning"
                                className="mt-4 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-amber-800"
                            >
                                <p className="text-xs">
                                    Portal arrears ({formatCurrency(portalCrossCheck.portal_arrears_total)}) differ
                                    from ledger arrears ({formatCurrency(portalCrossCheck.ledger_arrears_total)}) by
                                    {' '}{formatCurrency(Math.abs(portalCrossCheck.delta))} — reconciliation required.
                                    The ledger figure above remains authoritative.
                                </p>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Levy Breakdown — 100% reconciliation */}
            <Card className="rounded-2xl border-0 shadow-sm bg-slate-900 text-white overflow-hidden">
                <CardContent className="p-6">
                    <p className="text-[10px] font-black uppercase tracking-widest text-slate-400 mb-3">Total Levy
                        Breakdown — FY {selectedYear} (must total 100%)</p>
                    <div className="flex flex-wrap gap-0 overflow-hidden rounded-xl h-3">
                        {[
                            {label: 'Collected', pct: collectionRate, color: 'bg-emerald-500'},
                            {label: 'In Grace', pct: inGracePct, color: 'bg-amber-400'},
                            {label: 'Arrears', pct: arrearsPct, color: 'bg-rose-500'},
                            {label: 'Not Yet Due', pct: notYetDuePct, color: 'bg-slate-600'},
                        ].map(s => s.pct > 0 && (
                            <div key={s.label} className={`h-full ${s.color}`} style={{width: `${s.pct}%`}}
                                 title={`${s.label}: ${s.pct.toFixed(1)}%`}/>
                        ))}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-4 text-xs">
                        {[
                            {
                                label: 'Collected',
                                pct: collectionRate,
                                amount: totalCollectedYtd,
                                color: 'text-emerald-400'
                            },
                            {label: 'In Grace Period', pct: inGracePct, amount: inGraceAmount, color: 'text-amber-400'},
                            {label: 'True Arrears', pct: arrearsPct, amount: trueArrearsAmount, color: 'text-rose-400'},
                            {label: 'Not Yet Due', pct: notYetDuePct, amount: notYetDueAmount, color: 'text-slate-400'},
                        ].map(s => (
                            <div key={s.label} className="flex items-center gap-1.5">
                                <span className={`font-black ${s.color}`}>{s.pct.toFixed(1)}%</span>
                                <span className="text-slate-400">{s.label}</span>
                                <span className="text-slate-500">({formatCurrency(s.amount)})</span>
                            </div>
                        ))}
                    </div>
                    <p className="text-[10px] text-slate-500 mt-2">
                        Annual levy total: {formatCurrency(annualLevyTotal)} · Q1
                        levied: {formatCurrency(ls.total_levied || 0)} · "Not Yet Due" = future quarterly instalments
                    </p>
                </CardContent>
            </Card>

            {/* Charts Row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <Card className="rounded-2xl border-0 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-sm font-black">Collection Mix</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="h-[250px]">
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={chartData}
                                        innerRadius={60}
                                        outerRadius={80}
                                        paddingAngle={5}
                                        dataKey="value"
                                    >
                                        {chartData.map((entry, index) => (
                                            <Cell key={`cell-${index}`} fill={entry.color}/>
                                        ))}
                                    </Pie>
                                    <RechartsTooltip formatter={(v) => formatCurrency(v)}/>
                                    <Legend verticalAlign="bottom" height={36}/>
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    </CardContent>
                </Card>

                <Card className="rounded-2xl border-0 shadow-sm">
                    <CardHeader>
                        <CardTitle className="text-sm font-black">Unit Status Distribution</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="h-[250px]">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={statusDistribution}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9"/>
                                    <XAxis dataKey="name" axisLine={false} tickLine={false}
                                           tick={{fontSize: 12, fontWeight: 600}}/>
                                    <YAxis axisLine={false} tickLine={false} tick={{fontSize: 12}}/>
                                    <RechartsTooltip/>
                                    <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                                        {statusDistribution.map((entry, index) => (
                                            <Cell key={`cell-${index}`} fill={entry.color}/>
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Detail Table */}
            <Card className="rounded-2xl border-0 shadow-sm overflow-hidden">
                <CardHeader className="bg-white border-b pb-4">
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="text-sm font-black text-slate-900">Unit-Level Performance</CardTitle>
                            <CardDescription>Comprehensive breakdown of collection by unit</CardDescription>
                        </div>
                        <Button variant="ghost" size="sm" className="rounded-xl text-xs font-bold gap-2">
                            <Filter size={14}/> Filter Units
                        </Button>
                    </div>
                </CardHeader>
                <Table>
                    <TableHeader>
                        <TableRow className="bg-slate-50/50">
                            <TableHead className="text-[10px] font-black uppercase tracking-wider">Unit</TableHead>
                            <TableHead className="text-[10px] font-black uppercase tracking-wider">Type</TableHead>
                            <TableHead
                                className="text-[10px] font-black uppercase tracking-wider text-right">Levied</TableHead>
                            <TableHead
                                className="text-[10px] font-black uppercase tracking-wider text-right">Paid</TableHead>
                            <TableHead className="text-[10px] font-black uppercase tracking-wider text-right">Rate
                                %</TableHead>
                            <TableHead
                                className="text-[10px] font-black uppercase tracking-wider text-center">Status</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {ledger.map((entry) => {
                            // entry.total_paid is NOT reliably scoped to this year -- confirmed live
                            // 2026-08-01 (it can be back-solved from a portal balance snapshot,
                            // i.e. cumulative payment history through the scrape date). paid_this_year
                            // (backend: total_levied - net_balance) is the correctly year-scoped field;
                            // falls back to total_paid only if an older cached response lacks it.
                            const paidThisYear = entry.paid_this_year ?? entry.total_paid;
                            const rate = collectionPercentage(paidThisYear, entry.total_levied);
                            const progressRate = clampedCollectionPercentage(paidThisYear, entry.total_levied);
                            return (
                                <TableRow key={entry.id} className="hover:bg-slate-50/50 cursor-pointer group"
                                          onClick={() => router.push(`/financials/unit/${entry.unit_number}`)}>
                                    <TableCell
                                        className="font-black text-sm text-slate-900">{entry.unit_number}</TableCell>
                                    <TableCell
                                        className="text-xs font-bold text-slate-500 capitalize">{getUnitType(entry)}</TableCell>
                                    <TableCell
                                        className="text-right text-sm font-medium">{formatCurrency(entry.total_levied)}</TableCell>
                                    <TableCell
                                        className="text-right text-sm font-bold text-emerald-600">{formatCurrency(paidThisYear)}</TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex flex-col items-end gap-1">
                      <span
                          className={`text-sm font-black ${rate >= 100 ? 'text-emerald-600' : rate > 0 ? 'text-amber-500' : 'text-rose-500'}`}>
                        {rate.toFixed(1)}%
                      </span>
                                            <div className="w-16 h-1 bg-slate-100 rounded-full overflow-hidden">
                                                <div className="h-full bg-current rounded-full" style={{
                                                    width: `${progressRate}%`,
                                                    color: rate >= 100 ? '#10b981' : rate > 0 ? '#f59e0b' : '#f43f5e'
                                                }}/>
                                            </div>
                                        </div>
                                    </TableCell>
                                    <TableCell className="text-center">
                                        {rate >= 100 ? (
                                            <Badge className="bg-emerald-100 text-emerald-700 border-none text-[10px]">FULLY
                                                PAID</Badge>
                                        ) : rate > 0 ? (
                                            <Badge
                                                className="bg-amber-100 text-amber-700 border-none text-[10px]">PARTIAL</Badge>
                                        ) : (
                                            <Badge
                                                className="bg-rose-100 text-rose-700 border-none text-[10px]">UNPAID</Badge>
                                        )}
                                    </TableCell>
                                </TableRow>
                            );
                        })}
                    </TableBody>
                </Table>
            </Card>
        </div>
        </TooltipProvider>
    );
};

export default CollectionRatePage;
