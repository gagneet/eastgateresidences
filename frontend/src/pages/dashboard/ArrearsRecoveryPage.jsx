// @featuretrace:arrears — Arrears Recovery Board page (admin finance view).
// Layer: frontend
// Data flow: ArrearsRecoveryPage → GET /arrears/detail → unit_levy_ledger,
//            units (arrears_metadata.inherited_arrears) (building-scoped).
// Related: backend/routers/finance.py  (GET /arrears/detail)
//          backend/server.py            (_cascade_owner_change — writes inherited_arrears)
"use client";
// @ts-nocheck
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Input } from '../../components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';
import {
    ArrowLeft,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    Download,
    ExternalLink,
    FileText,
    History,
    Info,
    Loader2,
    Minus,
    Printer,
    Scale,
    Search,
    Send,
    TrendingDown,
    TrendingUp
} from 'lucide-react';
import { formatCurrency } from '../../lib/utils';

const CURRENT_FY = new Date().getFullYear();
const ALL_YEARS = Array.from({length: CURRENT_FY - 2021 + 1}, (_, i) => String(2021 + i));
const CHART_YEARS = ALL_YEARS.slice(-4); // last 4 for bar chart
// Keep YEARS as an alias for the full list used throughout
const YEARS = ALL_YEARS;
/**
 * @generated FunctionHeader
 * Function: sortIcon
 * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const sortIcon = (field, sortField, sortDir) => {
    if (sortField !== field) return null;
    return sortDir === 'asc' ? <ChevronUp size={12} className="ml-1 inline"/> :
        <ChevronDown size={12} className="ml-1 inline"/>;
};
/**
 * @generated FunctionHeader
 * Function: yoyBadge
 * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const yoyBadge = (current, previous) => {
    if (!previous || previous === 0) return null;
    const pct = ( ( current - previous ) / previous ) * 100;
    if (Math.abs(pct) < 1) return <Badge variant="outline" className="text-xs gap-1"><Minus size={10}/>0%</Badge>;
    if (pct > 0) return <Badge className="text-xs bg-rose-100 text-rose-700 border-rose-200 gap-1"><TrendingUp
        size={10}/>+{pct.toFixed(0)}%</Badge>;
    return <Badge className="text-xs bg-emerald-100 text-emerald-700 border-emerald-200 gap-1"><TrendingDown
        size={10}/>{pct.toFixed(0)}%</Badge>;
};

const SEVERITY_COLORS = {
    critical: '#e11d48', // rose-600
    serious: '#f59e0b', // amber-500
    overdue: '#3b82f6', // blue-500
    current: '#10b981', // emerald-500
};
/**
 * @generated FunctionHeader
 * Function: getSeverityBadge
 * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const getSeverityBadge = (severity) => {
    switch (severity) {
        case 'critical':
            return <Badge className="bg-rose-100 text-rose-700 border-rose-200">CRITICAL (90d+)</Badge>;
        case 'serious':
            return <Badge className="bg-amber-100 text-amber-700 border-amber-200">SERIOUS (60d+)</Badge>;
        case 'overdue':
            return <Badge className="bg-primary/10 text-primary border-primary/20">OVERDUE (14d+)</Badge>;
        case 'current':
            return <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200">CURRENT</Badge>;
        default:
            return <Badge variant="outline">UNKNOWN</Badge>;
    }
};
/**
 * @generated FunctionHeader
 * Function: ArrearsRecoveryPage
 * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ArrearsRecoveryPage = () => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const {api, hasPermission, user} = useAuth();

    const canManage = hasPermission('can_manage_finances');

    const [selectedYear, setSelectedYear] = useState(searchParams.get('year') || '2026');
    const [kpisMap, setKpisMap] = useState({});
    // /finance/kpi-contract for the selected year — its collection_mix.collection_rate_due_to_date_pct
    // is the CANONICAL due-date Collection Rate (metric 1), the same figure /financials/
    // collection-rate shows. /stats/building-kpis.collection_rate is fund_health (metric 2, ~99%)
    // and must NOT be shown as "Collection Rate" (CLAUDE.md).
    const [kpiContract, setKpiContract] = useState(null);
    const [arrearsList, setArrearsList] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [sortField, setSortField] = useState('total_arrears');
    const [sortDir, setSortDir] = useState('desc');
    const [actionLoading, setActionLoading] = useState(null); // unit_number of active action
    const [selectedUnitForLog, setSelectedUnitForLog] = useState(null);
    const [selectedMetric, setSelectedMetric] = useState(null);
    const [contactLog, setContactLog] = useState([]);
    const [logLoading, setLogLoading] = useState(false);
    const [newLogEntry, setNewLogEntry] = useState({method: 'email', description: ''});

    // Fetch all data
    const fetchData = useCallback(async () => {
        setLoading(true);
        try {
            const [arrearsRes, kpiContractRes, ...kpiResults] = await Promise.allSettled([
                api.get(`/arrears/detail?year=${selectedYear}`),
                api.get(`/finance/kpi-contract?year=${selectedYear}`),
                ...YEARS.map(yr => api.get(`/stats/building-kpis?financial_year=${yr}`)),
            ]);

            if (arrearsRes.status === 'fulfilled') {
                setArrearsList(Array.isArray(arrearsRes.value.data) ? arrearsRes.value.data : []);
            }
            setKpiContract(kpiContractRes.status === 'fulfilled' ? (kpiContractRes.value?.data ?? null) : null);

            const map = {};
            kpiResults.forEach((r, i) => {
                if (r.status === 'fulfilled') map[ YEARS[ i ] ] = r.value.data;
            });
            setKpisMap(map);
        } finally {
            setLoading(false);
        }
    }, [api, selectedYear]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const currentKpi = kpisMap[ selectedYear ] || {};
    const prevYear = String(parseInt(selectedYear) - 1);
    const prevKpi = kpisMap[ prevYear ] || {};

    // Canonical due-date Collection Rate (metric 1) for the KPI card — the SAME figure the
    // /financials/collection-rate page shows. Falls back to '—' (never the ~99% fund_health
    // value) if the contract failed to load, so a mislabelled rate can never reappear.
    const dueDateCollectionRate = kpiContract?.collection_mix?.collection_rate_due_to_date_pct;
    const collectionRateDisplay = dueDateCollectionRate != null
        ? `${parseFloat(Number(dueDateCollectionRate).toFixed(1))}%`
        : '—';

    const recoverySummary = useMemo(() => {
        const principal = arrearsList.reduce((sum, item) => sum + Number(item.total_arrears || 0), 0);
        const interest = arrearsList.reduce((sum, item) => sum + Number(item.accrued_interest || 0), 0);
        const totalOwing = arrearsList.reduce(
            (sum, item) => sum + Number(item.total_owing ?? item.total_arrears ?? 0),
            0,
        );
        return {
            principal: Math.round(principal * 100) / 100,
            interest: Math.round(interest * 100) / 100,
            totalOwing: Math.round(totalOwing * 100) / 100,
            // Canonical "in arrears" = grace-aware true_arrears > 0 (item.total_arrears is the
            // canonical unit_arrears_and_credit figure). This matches /finance/summary's
            // units_owing and the Levy Status tab, so all three agree. In-grace-only rows
            // (total_arrears == 0, still within their grace window) are NOT in arrears.
            units: arrearsList.filter(i => Number(i.total_arrears || 0) > 0.005).length,
            // Units behind but still within their grace window — the "reminder" bucket. Counted
            // separately so managers/EC can chase them before the debt becomes formal arrears.
            inGraceUnits: arrearsList.filter(
                i => Number(i.total_arrears || 0) <= 0.005 && Number(i.current_year_outstanding || 0) > 0.005,
            ).length,
        };
    }, [arrearsList]);

    // Keep these source contracts separate: the board headline is the sum of
    // /arrears/detail rows, while YoY trend remains KPI-to-KPI from /stats/building-kpis.
    const totalArrears = recoverySummary.principal;
    const currentKpiArrears = currentKpi.total_arrears ?? 0;
    const prevArrears = prevKpi.total_arrears ?? 0;
    const yoyChange = prevArrears > 0 ? ( ( currentKpiArrears - prevArrears ) / prevArrears * 100 ) : 0;
    const unitsInArrears = recoverySummary.units;
    const totalBuildingUnits = Number(currentKpi.total_units || 0);

    // Compute chart data
    const severityChartData = useMemo(() => {
        const counts = {critical: 0, serious: 0, overdue: 0, current: 0};
        arrearsList.forEach(item => {
            if (counts[ item.severity ] !== undefined) counts[ item.severity ]++;
        });
        return Object.entries(counts)
            .filter(([_, count]) => count > 0)
            .map(([name, value]) => ( {name, value} ));
    }, [arrearsList]);

    const topTenChart = useMemo(() => {
        return [...arrearsList]
            .sort((a, b) => b.total_arrears - a.total_arrears)
            .slice(0, 10)
            .map(item => ( {
                unit: item.unit_number,
                amount: item.total_arrears,
            } ));
    }, [arrearsList]);

    const metricDetails = {
        total_arrears: {
            title: 'Recoverable Arrears',
            description: 'Sum of recovery-board principal rows from /arrears/detail for the selected levy year — every unit’s own past-grace-deadline unpaid levy, never netted against another unit’s credit. As of 2026-08-03 this uses the same canonical per-unit calculation as the other dashboard KPIs, so it now agrees with them by construction.',
            rows: [
                ['Principal', formatCurrency(recoverySummary.principal)],
                ['Accrued interest', formatCurrency(recoverySummary.interest)],
                ['Total owing', formatCurrency(recoverySummary.totalOwing)],
                ['Units on board', unitsInArrears],
            ],
        },
        units: {
            title: 'Units in Recovery',
            description: 'Count of units returned by /arrears/detail for the selected levy year. Rows with no recoverable prior-year carry-forward debt are intentionally excluded.',
            rows: [
                ['Units on board', unitsInArrears],
                ['Total building units', totalBuildingUnits > 0 ? totalBuildingUnits : 'Unavailable'],
                ['Critical / serious', arrearsList.filter(i => ['critical', 'serious'].includes(i.severity)).length],
                ['With payment plan', arrearsList.filter(i => i.active_payment_plan).length],
                ['Referred to DCA', arrearsList.filter(i => i.dca_status === 'referred').length],
            ],
        },
        collection_rate: {
            title: 'Collection Rate',
            description: 'Collection Rate is the canonical DUE-DATE rate (metric 1) from /finance/kpi-contract — the same figure /financials/collection-rate shows. It is NOT /stats/building-kpis.collection_rate, which is fund_health (full-year coverage, ~99%) and must never be labelled "Collection Rate". The two arrears rows below are the same canonical calculation and should always match.',
            rows: [
                ['Collection rate (due-to-date)', collectionRateDisplay],
                ['Broader KPI arrears', formatCurrency(currentKpi.total_arrears ?? 0)],
                ['Recovery board arrears', formatCurrency(recoverySummary.principal)],
            ],
        },
        yoy: {
            title: 'KPI Year-on-Year Change',
            description: 'Compares the selected-year KPI arrears total to the prior-year KPI arrears total from /stats/building-kpis.',
            rows: [
                [`FY ${selectedYear} KPI arrears`, formatCurrency(currentKpiArrears)],
                [`FY ${prevYear}`, formatCurrency(prevArrears)],
                ['Change', prevArrears > 0 ? `${yoyChange > 0 ? '+' : ''}${yoyChange.toFixed(1)}%` : 'No prior-year baseline'],
            ],
        },
    };

    // Filtered + sorted table
    const filtered = useMemo(() => {
        // Show EVERY unit that is behind on payment — both true arrears (past grace) AND units
        // still within their grace window. The Strata Manager/EC need to see the in-grace units
        // so they can send a reminder before the debt becomes formal arrears; they carry
        // total_arrears == 0 but current_year_outstanding > 0 and are badged "In Grace" in the
        // table. (The "Units in Arrears" KPI still counts only true arrears — see recoverySummary.)
        let list = [...arrearsList];
        if (search) {
            const q = search.toLowerCase();
            list = list.filter(item =>
                item.unit_number?.toLowerCase().includes(q) ||
                item.owner_name?.toLowerCase().includes(q)
            );
        }
        list.sort((a, b) => {
            let va = a[ sortField ] ?? 0;
            let vb = b[ sortField ] ?? 0;
            if (typeof va === 'string') va = va.toLowerCase();
            if (typeof vb === 'string') vb = vb.toLowerCase();
            if (va < vb) return sortDir === 'asc' ? -1 : 1;
            if (va > vb) return sortDir === 'asc' ? 1 : -1;
            return 0;
        });
        return list;
    }, [arrearsList, search, sortField, sortDir]);
    /**
     * @generated FunctionHeader
     * Function: handleSort
     * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSort = (field) => {
        if (sortField === field) {
            setSortDir(d => d === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDir('desc');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: exportCsv
     * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const exportCsv = () => {
        const rows = [
            ['Unit', 'Owner', 'Arrears (Principal)', 'Accrued Interest', 'Interest Rate %', 'Total Owing', 'Current Total Owing', 'Current Credit', 'DCA Status', 'Legal Status', 'Email'].join(','),
            ...filtered.map(item => [
                item.unit_number,
                `"${item.owner_name || ''}"`,
                item.total_arrears,
                item.accrued_interest ?? 0,
                item.interest_rate_pct ?? '',
                item.total_owing ?? item.total_arrears,
                item.current_year_outstanding ?? 0,
                item.current_year_credit ?? 0,
                item.dca_status,
                item.legal_referral_status,
                item.owner_email || '',
            ].join(',')),
        ];
        const blob = new Blob([rows.join('\n')], {type: 'text/csv'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `arrears_detail_${new Date().toISOString().split('T')[ 0 ]}.csv`;
        a.click();
    };
    /**
     * @generated FunctionHeader
     * Function: handleSendNotice
     * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSendNotice = async (unit_number) => {
        setActionLoading(unit_number);
        try {
            const res = await api.post(`/arrears/${unit_number}/send-notice?year=${selectedYear}`, {}, {responseType: 'blob'});
            const url = window.URL.createObjectURL(new Blob([res.data]));
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', `Arrears_Notice_${unit_number}_${selectedYear}.pdf`);
            document.body.appendChild(link);
            link.click();
            fetchData(); // Refresh board
        } catch (err) {
            console.error("Failed to send notice", err);
            alert("Failed to generate notice. Please try again.");
        } finally {
            setActionLoading(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleReferDCA
     * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReferDCA = async (unit_number) => {
        if (!window.confirm(`Refer Unit ${unit_number} to Debt Collection Agency?`)) return;
        setActionLoading(unit_number);
        try {
            await api.post(`/arrears/${unit_number}/refer-dca`);
            fetchData();
        } catch (err) {
            console.error("DCA referral failed", err);
        } finally {
            setActionLoading(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getDcaBadge
     * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getDcaBadge = (status) => {
        switch (status) {
            case 'referred':
                return <Badge className="bg-amber-100 text-amber-700 border-amber-200">DCA REFERRED</Badge>;
            case 'eligible':
                return <Badge className="bg-rose-100 text-rose-700 border-rose-200">DCA ELIGIBLE</Badge>;
            case 'recovering':
                return <Badge className="bg-primary/10 text-primary border-primary/20">RECOVERING</Badge>;
            case 'resolved':
                return <Badge className="bg-emerald-100 text-emerald-700 border-emerald-200">RESOLVED</Badge>;
            default:
                return <Badge variant="outline" className="text-muted-foreground">NONE</Badge>;
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleViewLog
     * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleViewLog = async (unit_number) => {
        setSelectedUnitForLog(unit_number);
        setLogLoading(true);
        try {
            const res = await api.get(`/arrears/${unit_number}/contact-log`);
            setContactLog(res.data || []);
        } catch (err) {
            console.error("Failed to fetch contact log", err);
        } finally {
            setLogLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAddLogEntry
     * Path: frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddLogEntry = async () => {
        if (!newLogEntry.description.trim()) return;
        setLogLoading(true);
        try {
            await api.post(`/arrears/${selectedUnitForLog}/contact-log`, newLogEntry);
            setNewLogEntry({method: 'email', description: ''});
            handleViewLog(selectedUnitForLog);
        } catch (err) {
            console.error("Failed to add log entry", err);
        } finally {
            setLogLoading(false);
        }
    };

    return (
        <div className="min-h-screen bg-muted p-6 md:p-10 space-y-6 pb-24">
            {/* Header */}
            <div className="flex flex-col md:flex-row items-start md:items-center gap-4 justify-between">
                <div className="flex items-center gap-4">
                    <button
                        onClick={() => router.back()}
                        className="p-2 rounded-xl border bg-card hover:bg-muted transition-colors"
                    >
                        <ArrowLeft size={18} className="text-muted-foreground"/>
                    </button>
                    <div>
                        <h1 className="text-2xl font-semibold text-foreground">Debt Recovery Board</h1>
                        <p className="text-muted-foreground text-sm font-medium mt-0.5">Track and manage outstanding levy
                            arrears</p>
                    </div>
                </div>

                <div className="flex items-center gap-2 flex-wrap">
                    <Button size="sm" variant="ghost" asChild
                            className="rounded-xl text-xs font-medium text-muted-foreground print:hidden">
                        <a href="/intelligence/financial">Finance Intelligence →</a>
                    </Button>
                    <select
                        value={selectedYear}
                        onChange={e => setSelectedYear(e.target.value)}
                        className="text-sm border rounded-xl px-3 py-1.5 bg-card font-bold text-foreground"
                    >
                        {YEARS.map(yr => <option key={yr} value={yr}>FY {yr}</option>)}
                    </select>
                    <Button size="sm" variant="outline" onClick={() => window.print()}
                            className="rounded-xl text-xs font-bold gap-2 print:hidden">
                        <Printer size={14}/> Print Report
                    </Button>
                    <Button size="sm" variant="outline" onClick={exportCsv}
                            className="rounded-xl text-xs font-bold print:hidden">
                        <Download className="mr-1 h-3.5 w-3.5"/>Export CSV
                    </Button>
                </div>
            </div>

            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    transition={{delay: 0.1}}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedMetric('total_arrears')}
                    onKeyDown={(e) => e.key === 'Enter' && setSelectedMetric('total_arrears')}
                    className="p-4 rounded-2xl border bg-card shadow-sm hover:shadow-md transition-shadow cursor-pointer focus:outline-none focus:ring-2 focus:ring-rose-300"
                >
                    <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-1">Recoverable Arrears</p>
                    <p className="text-xl font-semibold text-rose-600">{loading ? '...' : formatCurrency(totalArrears)}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">FY {selectedYear} - click for source</p>
                </motion.div>
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    transition={{delay: 0.2}}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedMetric('units')}
                    onKeyDown={(e) => e.key === 'Enter' && setSelectedMetric('units')}
                    className="p-4 rounded-2xl border bg-card shadow-sm hover:shadow-md transition-shadow cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring"
                >
                    <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-1">Units in Arrears</p>
                    <p className="text-xl font-semibold text-foreground">{loading ? '...' : unitsInArrears}</p>
                    {!loading && recoverySummary.inGraceUnits > 0 && (
                        <p className="text-xs text-amber-600 mt-0.5">+{recoverySummary.inGraceUnits} in grace — send reminder</p>
                    )}
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {totalBuildingUnits > 0 ? `of ${totalBuildingUnits} units` : `FY ${selectedYear}`}
                    </p>
                </motion.div>
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    transition={{delay: 0.3}}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedMetric('collection_rate')}
                    onKeyDown={(e) => e.key === 'Enter' && setSelectedMetric('collection_rate')}
                    className="p-4 rounded-2xl border bg-card shadow-sm hover:shadow-md transition-shadow cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring"
                >
                    <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-1">Collection Rate</p>
                    <p className="text-xl font-semibold text-foreground">
                        {loading ? '...' : collectionRateDisplay}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">FY {selectedYear}</p>
                </motion.div>
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    transition={{delay: 0.4}}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedMetric('yoy')}
                    onKeyDown={(e) => e.key === 'Enter' && setSelectedMetric('yoy')}
                    className="p-4 rounded-2xl border bg-card shadow-sm hover:shadow-md transition-shadow cursor-pointer focus:outline-none focus:ring-2 focus:ring-emerald-300"
                >
                    <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground mb-1">KPI YoY Change</p>
                    <div className="flex items-center gap-2 mt-1">
                        {!loading && prevArrears > 0 ? (
                            <>
                                {yoyChange > 0 ? (
                                    <TrendingUp size={18} className="text-rose-500"/>
                                ) : yoyChange < 0 ? (
                                    <TrendingDown size={18} className="text-emerald-500"/>
                                ) : (
                                    <Minus size={18} className="text-muted-foreground"/>
                                )}
                                <p className={`text-xl font-semibold ${yoyChange > 0 ? 'text-rose-600' : yoyChange < 0 ? 'text-emerald-600' : 'text-muted-foreground'}`}>
                                    {yoyChange > 0 ? '+' : ''}{yoyChange.toFixed(1)}%
                                </p>
                            </>
                        ) : (
                            <p className="text-xl font-semibold text-muted-foreground">—</p>
                        )}
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">vs FY {prevYear}</p>
                </motion.div>
            </div>

            {/* Charts Row */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 print:hidden">
                {/* Severity Breakdown (Donut) */}
                <Card className="rounded-2xl border-0 shadow-sm">
                    <CardHeader className="pb-0">
                        <CardTitle className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Aging
                            Breakdown</CardTitle>
                    </CardHeader>
                    <CardContent className="flex items-center justify-center pt-0">
                        <ResponsiveContainer width="100%" height={200}>
                            <PieChart>
                                <Pie
                                    data={severityChartData}
                                    innerRadius={60}
                                    outerRadius={80}
                                    paddingAngle={5}
                                    dataKey="value"
                                >
                                    {severityChartData.map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={SEVERITY_COLORS[ entry.name ]}/>
                                    ))}
                                </Pie>
                                <Tooltip/>
                            </PieChart>
                        </ResponsiveContainer>
                        <div className="space-y-1 ml-4">
                            {Object.entries(SEVERITY_COLORS).map(([name, color]) => (
                                <div key={name} className="flex items-center gap-2">
                                    <div className="w-2 h-2 rounded-full" style={{backgroundColor: color}}/>
                                    <span className="text-[10px] font-bold uppercase text-muted-foreground">{name}</span>
                                </div>
                            ))}
                        </div>
                    </CardContent>
                </Card>

                {/* Multi-year Trend */}
                <Card className="rounded-2xl border-0 shadow-sm">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Building
                            Trend</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <ResponsiveContainer width="100%" height={160}>
                            <BarChart
                                data={CHART_YEARS.map(yr => ( {
                                    year: `FY${yr}`,
                                    amount: kpisMap[ yr ]?.total_arrears ?? 0
                                } ))}
                                margin={{top: 4, right: 8, left: -20, bottom: 4}}
                            >
                                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false}/>
                                <XAxis dataKey="year" tick={{fontSize: 10, fontWeight: 700}} axisLine={false}
                                       tickLine={false}/>
                                <YAxis tickFormatter={v => `$${( v / 1000 ).toFixed(0)}k`} tick={{fontSize: 9}}
                                       axisLine={false} tickLine={false}/>
                                <Tooltip formatter={(v) => formatCurrency(v)} cursor={{fill: '#f8fafc'}}/>
                                <Bar dataKey="amount" radius={[4, 4, 0, 0]}>
                                    {CHART_YEARS.map((yr, idx) => (
                                        <Cell key={idx} fill={yr === selectedYear ? '#f43f5e' : '#e2e8f0'}/>
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </CardContent>
                </Card>

                {/* Top 10 Bar Chart */}
                <Card className="rounded-2xl border-0 shadow-sm">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Top
                            Exposures</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <ResponsiveContainer width="100%" height={160}>
                            <BarChart
                                data={topTenChart}
                                layout="vertical"
                                margin={{top: 4, right: 8, left: -10, bottom: 4}}
                            >
                                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" horizontal={false}/>
                                <XAxis type="number" hide/>
                                <YAxis dataKey="unit" type="category" tick={{fontSize: 10, fontWeight: 700}} width={40}
                                       axisLine={false} tickLine={false}/>
                                <Tooltip formatter={(v) => formatCurrency(v)}/>
                                <Bar dataKey="amount" fill="#f43f5e" radius={[0, 4, 4, 0]}/>
                            </BarChart>
                        </ResponsiveContainer>
                    </CardContent>
                </Card>
            </div>

            {/* Full arrears table */}
            <Card className="rounded-2xl border-0 shadow-sm print:shadow-none print:border print:mt-8">
                <CardHeader className="pb-4 print:pb-2">
                    <div className="flex items-center justify-between gap-4">
                        <CardTitle className="text-sm font-semibold">All Arrears Units — FY {selectedYear}</CardTitle>
                        <div className="relative">
                            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"/>
                            <Input
                                placeholder="Search unit or owner..."
                                value={search}
                                onChange={e => setSearch(e.target.value)}
                                className="pl-8 h-8 text-sm rounded-xl w-48"
                            />
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="print:p-0">
                    {loading ? (
                        <div className="h-32 bg-muted animate-pulse rounded-xl"/>
                    ) : filtered.length === 0 ? (
                        <div className="p-8 text-center text-muted-foreground bg-muted rounded-xl">
                            {search ? `No arrears units matching "${search}"` : `No units in arrears for FY ${selectedYear}.`}
                        </div>
                    ) : (
                        <div className="border rounded-xl overflow-hidden">
                            <Table>
                                <TableHeader>
                                    <TableRow className="bg-muted">
                                        <TableHead
                                            className="text-xs font-semibold uppercase cursor-pointer select-none"
                                            onClick={() => handleSort('unit_number')}
                                        >
                                            Unit {sortIcon('unit_number', sortField, sortDir)}
                                        </TableHead>
                                        <TableHead
                                            className="text-xs font-semibold uppercase cursor-pointer select-none"
                                            onClick={() => handleSort('owner_name')}
                                        >
                                            Owner {sortIcon('owner_name', sortField, sortDir)}
                                        </TableHead>
                                        <TableHead
                                            className="text-xs font-semibold uppercase cursor-pointer select-none text-right"
                                            onClick={() => handleSort('total_arrears')}
                                        >
                                            Recoverable Principal {sortIcon('total_arrears', sortField, sortDir)}
                                        </TableHead>
                                        <TableHead
                                            className="text-xs font-semibold uppercase cursor-pointer select-none text-right"
                                            onClick={() => handleSort('current_year_outstanding')}
                                            title="Total owing right now (opening + this year's levy, less all payments) — distinct from Recoverable Principal, which is prior-year carry-forward only"
                                        >
                                            Current Total Owing {sortIcon('current_year_outstanding', sortField, sortDir)}
                                        </TableHead>
                                        <TableHead
                                            className="text-xs font-semibold uppercase cursor-pointer select-none text-center"
                                            onClick={() => handleSort('dca_status')}
                                        >
                                            Recovery Status {sortIcon('dca_status', sortField, sortDir)}
                                        </TableHead>
                                        <TableHead className="text-xs font-semibold uppercase">Legal/Plan</TableHead>
                                        <TableHead className="text-xs font-semibold uppercase">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filtered.map(item => {
                                        const isActing = actionLoading === item.unit_number;
                                        const isSerious = ['serious', 'critical'].includes(item.severity);

                                        return (
                                            <TableRow
                                                key={item.unit_number}
                                                className={`hover:bg-muted cursor-pointer group transition-colors ${isSerious ? 'border-l-4 border-l-rose-500' : 'border-l-4 border-l-transparent'}`}
                                                onClick={() => router.push(`/financials/unit/${item.unit_number}`)}
                                            >
                                                <TableCell className="font-semibold text-sm">{item.unit_number}</TableCell>
                                                <TableCell>
                                                    <div className="max-w-[160px]">
                                                        <p className="text-sm font-bold text-foreground truncate">{item.owner_name || '—'}</p>
                                                        <p className="text-[10px] text-muted-foreground truncate">{item.owner_email || ''}</p>
                                                        {item.inherited_arrears > 0 && item.previous_owner && (
                                                            <p className="text-[9px] text-amber-600 font-semibold mt-0.5 flex items-center gap-1">
                                                                <History size={9}/>
                                                                {formatCurrency(item.inherited_arrears)} carried
                                                                from {item.previous_owner}
                                                                {item.transferred_at ? ` (${item.transferred_at.slice(0, 10)})` : ''}
                                                            </p>
                                                        )}
                                                    </div>
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <p className="font-semibold text-rose-600">{formatCurrency(item.total_arrears)}</p>
                                                    {item.accrued_interest > 0 && (
                                                        <p className="text-[9px] font-semibold text-amber-600">
                                                            + {formatCurrency(item.accrued_interest)} interest =
                                                            {' '}{formatCurrency(item.total_owing ?? item.total_arrears)} total owing
                                                            {item.interest_rate_pct != null ? ` @ ${item.interest_rate_pct}% p.a.` : ''}
                                                        </p>
                                                    )}
                                                    <p className="text-[9px] font-bold text-muted-foreground uppercase">{item.days_overdue} days
                                                        overdue</p>
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    {item.current_year_credit > 0 ? (
                                                        <p className="font-semibold text-emerald-600">
                                                            {formatCurrency(item.current_year_credit)} credit
                                                        </p>
                                                    ) : (
                                                        <p className="font-semibold text-foreground">
                                                            {formatCurrency(item.current_year_outstanding ?? 0)}
                                                        </p>
                                                    )}
                                                </TableCell>
                                                <TableCell className="text-center">
                                                    <div className="flex flex-col items-center gap-1">
                                                        {getSeverityBadge(item.severity)}
                                                        {getDcaBadge(item.dca_status)}
                                                    </div>
                                                </TableCell>
                                                <TableCell className="hidden md:table-cell">
                                                    {item.active_payment_plan ? (
                                                        <Badge
                                                            className="bg-emerald-100 text-emerald-700 border-none flex items-center gap-1 w-fit">
                                                            <CheckCircle2 size={10}/> PAYMENT PLAN
                                                        </Badge>
                                                    ) : item.legal_referral_status === 'referred' ? (
                                                        <Badge
                                                            className="bg-rose-100 text-rose-700 border-none flex items-center gap-1 w-fit">
                                                            <Scale size={10}/> LEGAL ACTION
                                                        </Badge>
                                                    ) : (
                                                        <Badge variant="outline" className="text-muted-foreground">NO
                                                            PLAN</Badge>
                                                    )}
                                                </TableCell>
                                                <TableCell onClick={e => e.stopPropagation()}>
                                                    <div
                                                        className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                        {canManage && (
                                                            <>
                                                                <button
                                                                    disabled={isActing}
                                                                    onClick={() => handleSendNotice(item.unit_number)}
                                                                    className="p-1.5 rounded-lg text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
                                                                    title="Send Section 83 Notice"
                                                                >
                                                                    {isActing ?
                                                                        <Loader2 size={14} className="animate-spin"/> :
                                                                        <FileText size={14}/>}
                                                                </button>
                                                                <button
                                                                    disabled={isActing || item.dca_status === 'referred'}
                                                                    onClick={() => handleReferDCA(item.unit_number)}
                                                                    className={`p-1.5 rounded-lg transition-colors ${item.dca_status === 'referred' ? 'text-muted-foreground/40' : 'text-muted-foreground hover:text-rose-600 hover:bg-rose-50'}`}
                                                                    title="Refer to DCA"
                                                                >
                                                                    <Scale size={14}/>
                                                                </button>
                                                            </>
                                                        )}
                                                        <button
                                                            onClick={() => handleViewLog(item.unit_number)}
                                                            className="p-1.5 rounded-lg text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
                                                            title="Contact Log"
                                                        >
                                                            <History size={14}/>
                                                        </button>
                                                        <button
                                                            onClick={() => router.push(`/financials/unit/${item.unit_number}`)}
                                                            className="p-1.5 rounded-lg text-muted-foreground hover:text-primary hover:bg-primary/10 transition-colors"
                                                            title="View Unit History"
                                                        >
                                                            <ExternalLink size={14}/>
                                                        </button>
                                                    </div>
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Contact Log Dialog */}
            <Dialog open={!!selectedMetric} onOpenChange={() => setSelectedMetric(null)}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Info size={16}/>
                            {selectedMetric ? metricDetails[selectedMetric]?.title : ''}
                        </DialogTitle>
                        <DialogDescription>
                            {selectedMetric ? metricDetails[selectedMetric]?.description : ''}
                        </DialogDescription>
                    </DialogHeader>
                    {selectedMetric && (
                        <div className="space-y-3">
                            {metricDetails[selectedMetric].rows.map(([label, value]) => (
                                <div key={label} className="flex items-center justify-between rounded-xl bg-muted px-3 py-2">
                                    <span className="text-xs font-bold uppercase tracking-wide text-muted-foreground">{label}</span>
                                    <span className="text-sm font-semibold text-foreground">{value}</span>
                                </div>
                            ))}
                            <div className="flex justify-end gap-2 pt-2">
                                {selectedMetric === 'total_arrears' && (
                                    <Button size="sm" variant="outline" onClick={() => setSearch('')}>
                                        Show all rows
                                    </Button>
                                )}
                                {selectedMetric === 'collection_rate' && (
                                    <Button size="sm" variant="outline" onClick={() => router.push('/financials/collection-rate')}>
                                        Open Collection Rate
                                    </Button>
                                )}
                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            <Dialog open={!!selectedUnitForLog} onOpenChange={() => setSelectedUnitForLog(null)}>
                <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
                    <DialogHeader>
                        <DialogTitle>Contact Log — Unit {selectedUnitForLog}</DialogTitle>
                        <DialogDescription>History of communications and recovery actions</DialogDescription>
                    </DialogHeader>

                    <div className="flex-1 overflow-y-auto space-y-4 py-4 pr-2">
                        {logLoading && contactLog.length === 0 ? (
                            <div className="flex justify-center py-8"><Loader2 className="animate-spin text-muted-foreground"/>
                            </div>
                        ) : contactLog.length === 0 ? (
                            <p className="text-center text-muted-foreground py-8 text-sm">No contact history recorded.</p>
                        ) : (
                            contactLog.map((log, i) => (
                                <div key={i} className="p-3 rounded-xl border bg-muted relative group">
                                    <div className="flex items-center justify-between mb-1">
                                        <Badge variant="outline"
                                               className="text-[10px] uppercase font-semibold">{log.method}</Badge>
                                        <span className="text-[10px] text-muted-foreground font-medium">
                      {new Date(log.date).toLocaleString('en-AU')}
                    </span>
                                    </div>
                                    <p className="text-sm text-foreground leading-relaxed">{log.description}</p>
                                    <p className="text-[10px] text-muted-foreground mt-2 font-bold uppercase tracking-tight">BY: {log.performed_by_name || log.performed_by}</p>
                                </div>
                            ))
                        )}
                    </div>

                    <div className="border-t pt-4 space-y-3">
                        <div className="flex gap-2">
                            <select
                                value={newLogEntry.method}
                                onChange={e => setNewLogEntry({...newLogEntry, method: e.target.value})}
                                className="text-xs border rounded-lg px-2 py-1 bg-card font-bold"
                            >
                                <option value="email">Email</option>
                                <option value="phone">Phone</option>
                                <option value="letter">Letter</option>
                                <option value="meeting">Meeting</option>
                                <option value="system">System</option>
                            </select>
                            <Input
                                placeholder="Add a new note..."
                                value={newLogEntry.description}
                                onChange={e => setNewLogEntry({...newLogEntry, description: e.target.value})}
                                onKeyDown={e => e.key === 'Enter' && handleAddLogEntry()}
                                className="h-8 text-sm rounded-lg"
                            />
                            <Button size="sm" onClick={handleAddLogEntry}
                                    disabled={logLoading || !newLogEntry.description.trim()}
                                    className="h-8 rounded-lg px-3">
                                <Send size={14}/>
                            </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default ArrearsRecoveryPage;
