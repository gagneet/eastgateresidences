"use client";
// @featuretrace:levy — Management cockpit dashboard: building-wide collection rate/arrears summary tiles.
// Layer: frontend
// Data flow: this page -> GET /finance/summary?year=, GET /finance/kpi-contract?year= ->
//            finance.py -> unit_levy_ledger (building-scoped).
// Related: backend/routers/finance.py
//           frontend/src/pages/dashboard/CollectionRatePage.jsx
// Collection: unit_levy_ledger
import React, {useCallback, useEffect, useState} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import OwnerDashboard from './OwnerDashboard';
import {Card, CardContent, CardHeader, CardTitle} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import YearSelector from '../../components/widgets/YearSelector';
import KPIStatCard from '../../components/dashboard/KPIStatCard';
import GaugeCard from '../../components/dashboard/GaugeCard';
import FinancialSummaryCard from '../../components/dashboard/FinancialSummaryCard';
import ChartCard from '../../components/dashboard/ChartCard';
import ActivityFeed from '../../components/dashboard/ActivityFeed';
import MarketSnapshotCard from '../../components/dashboard/MarketSnapshotCard';
import {
    ArrowRight,
    ArrowUpRight,
    Calendar,
    Clock,
    DollarSign,
    FileText,
    Shield,
    ShieldCheck,
    TrendingUp,
    Users,
    Wrench
} from 'lucide-react';
import {Area, AreaChart, CartesianGrid, Tooltip as RechartsTooltip, XAxis, YAxis,} from 'recharts';
import {toast} from 'sonner';
import {motion} from 'framer-motion';
import {formatCurrency} from '../../lib/utils';
/**
 * Regular Dashboard router:
 * - Chairman / EC Member / Strata Manager with a unit number → OwnerDashboard (personal property view)
 * - Others (super_admin viewing, no-unit management roles) → Management Cockpit view
 */
const RegularDashboard: React.FC = () => {
    const {user, hasPermission, api, isAdmin, isManager, isECMember, selectedYear} = useAuth();
    const {activeUnit} = useActiveUnit();
    const router = useRouter();

    const [loading, setLoading] = useState(true);

    // Data States
    const [financialData, setFinancialData] = useState<any>(null);
    const [financeSummary, setFinanceSummary] = useState<any>(null);
    const [kpiContract, setKpiContract] = useState<any>(null);
    const [sinkingFundForecast, setSinkingFundForecast] = useState<any>(null);
    const [maintenanceStats, setMaintenanceStats] = useState<any>(null);
    const [activities, setActivities] = useState<any[]>([]);
    const [marketSnapshot, setMarketSnapshot] = useState<any>(null);
    const [levyTrendData, setLevyTrendData] = useState<any[]>([]);
    const [upcomingAgm, setUpcomingAgm] = useState<any>(null);
    const [complianceSummary, setComplianceSummary] = useState<any>(null);

    const fetchDashboardData = useCallback(async () => {
        if (!selectedYear) return;
        setLoading(true);
        try {
            const yearParam = `?year=${selectedYear}`;
            const ownerYearParam = `?financial_year=${selectedYear}`;

            // 0. Fetch Personal Financial Summary
            if (activeUnit) {
                try {
                    const financialRes = await api.get(`/owners-units/${activeUnit}${ownerYearParam}`);
                    setFinancialData(financialRes.data);
                } catch (err) {
                    console.error('Failed to fetch personal financial data:', err);
                }
            }

            // 1. Fetch Finance Summary (Building-wide for EC/Chairman)
            try {
                const financeRes = await api.get(`/finance/summary${yearParam}`);
                setFinanceSummary(financeRes.data);
            } catch (err) {
                console.error('Failed to fetch finance summary:', err);
            }

            // 1b. Fetch ledger-derived KPI contract (GAP-FIN-014) — preferred
            // source for collection rate/arrears below, falls back to the
            // legacy total_paid/total_levied calc if unavailable.
            try {
                const kpiRes = await api.get(`/finance/kpi-contract${yearParam}`);
                setKpiContract(kpiRes.data);
            } catch (err) {
                console.error('Failed to fetch finance kpi contract:', err);
            }

            // 2. Fetch Sinking Fund Forecast
            try {
                const forecastRes = await api.get('/analytics/sinking-fund-forecast?years=10');
                setSinkingFundForecast(forecastRes.data);
            } catch (err) {
                console.error('Failed to fetch sinking fund forecast:', err);
            }

            // 3. Fetch Maintenance Stats
            try {
                const maintRes = await api.get('/analytics/maintenance-stats');
                setMaintenanceStats(maintRes.data);
            } catch (err) {
                console.error('Failed to fetch maintenance stats:', err);
            }

            // 4. Fetch Activities
            try {
                const activityRes = await api.get('/analytics/activities?limit=15');
                setActivities(activityRes.data || []);
            } catch (err) {
                console.error('Failed to fetch activities:', err);
            }

            // 5. Fetch Market Snapshot
            try {
                const marketRes = await api.get('/analytics/market-snapshot?suburb=Denman Prospect');
                setMarketSnapshot(marketRes.data);
            } catch (err) {
                console.error('Failed to fetch market snapshot:', err);
            }

            // 6. Fetch Levy Trend Data
            try {
                const trendRes = await api.get('/annual-levies');
                const sortedLevies = (trendRes.data || []).sort((a: any, b: any) => a.year.localeCompare(b.year));
                const trend = sortedLevies.map((l: any) => ({
                    year: l.year,
                    admin: l.admin_fund.levy_income,
                    sinking: l.sinking_fund.levy_income,
                    total: l.admin_fund.levy_income + l.sinking_fund.levy_income
                }));
                setLevyTrendData(trend);
            } catch (err) {
                console.error('Failed to fetch levy trend:', err);
            }

            // 7. Fetch Upcoming AGM
            try {
                const agmRes = await api.get('/agm');
                const upcoming = (agmRes.data || [])
                    .filter((a: any) => new Date(a.date) > new Date())
                    .sort((a: any, b: any) => new Date(a.date).getTime() - new Date(b.date).getTime())[0];
                setUpcomingAgm(upcoming);
            } catch (err) {
                console.error('Failed to fetch AGM:', err);
            }

            // 8. Fetch Compliance Summary
            try {
                const compRes = await api.get('/analytics/compliance-summary');
                setComplianceSummary(compRes.data);
            } catch (err) {
                console.error('Failed to fetch compliance summary:', err);
            }

        } catch (error) {
            console.error('Failed to load dashboard data:', error);
            toast.error('Unable to load some dashboard components');
        } finally {
            setLoading(false);
        }
    }, [api, selectedYear, activeUnit]);

    useEffect(() => {
        fetchDashboardData();
    }, [fetchDashboardData]);

    const quickActions = [
        {
            label: 'Finances',
            icon: DollarSign,
            href: '/financials/overview',
            color: 'bg-emerald-50 text-emerald-600',
            show: hasPermission('can_view_finances')
        },
        {
            label: 'Maintenance',
            icon: Wrench,
            href: '/maintenance',
            color: 'bg-orange-50 text-orange-600',
            show: hasPermission('can_manage_meetings')
        },
        {
            label: 'Meetings',
            icon: Calendar,
            href: '/governance/meetings',
            color: 'bg-blue-50 text-blue-600',
            show: hasPermission('can_view_meetings')
        },
        {
            label: 'Compliance',
            icon: ShieldCheck,
            href: '/compliance',
            color: 'bg-purple-50 text-purple-600',
            show: true
        },
        {
            label: 'Documents',
            icon: FileText,
            href: '/documents',
            color: 'bg-indigo-50 text-indigo-600',
            show: hasPermission('can_view_documents')
        },
        {
            label: 'Admin Console',
            icon: Shield,
            href: '/admin/console',
            color: 'bg-slate-50 text-slate-600',
            show: isAdmin()
        },
    ].filter(a => a.show);

    // Chairman, EC Members and Strata Managers who have a unit number see the
    // full owner-style property intelligence view at /owner-view.
    //
    // Deliberately still keyed on `user.unit_number`, NOT on `activeUnit`: this is a
    // ROUTING decision — which dashboard a manager lands on — and `activeUnit` also
    // resolves via `owned_units[0]`, so switching to it would silently re-route
    // managers whose account carries a unit link but no `unit_number`. Propagating
    // the active unit to a page's DATA (above) is this ticket's scope; changing which
    // page a role sees is not.
    if ((isManager() || isECMember()) && user?.unit_number) {
        return <OwnerDashboard/>;
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[600px]">
                <div className="text-center">
                    <motion.div
                        animate={{rotate: 360}}
                        transition={{repeat: Infinity, duration: 1, ease: "linear"}}
                        className="h-12 w-12 border-4 border-primary border-t-transparent rounded-full mx-auto"
                    />
                    <p className="mt-4 text-slate-500 font-medium">Loading Management Cockpit...</p>
                </div>
            </div>
        );
    }

    // GAP-FIN-014: prefer the backend kpi-contract's ledger_collected_ytd /
    // annual_levy_total (canonical, ledger-quality-reconciled) over the naive
    // total_paid/total_levied ratio, which double-counts duplicate ledger rows
    // and only reflects YTD-charged amounts, not the full annual obligation.
    const annualLevyTotalForRate = financeSummary?.unit_ledger_summary?.annual_levy_total
        || financeSummary?.unit_ledger_summary?.total_levied || 0;
    const collectionRate = kpiContract?.collection_mix
        ? (annualLevyTotalForRate > 0
            ? Math.round((kpiContract.collection_mix.ledger_collected_ytd / annualLevyTotalForRate) * 100)
            : 0)
        : (financeSummary?.unit_ledger_summary
            ? Math.round((financeSummary.unit_ledger_summary.total_paid / financeSummary.unit_ledger_summary.total_levied) * 100)
            : 0);
    const arrearsAmount = kpiContract?.arrears_metrics?.true_arrears_amount
        ?? financeSummary?.unit_ledger_summary?.total_outstanding;

    const daysToAgm = upcomingAgm ? Math.ceil((new Date(upcomingAgm.date).getTime() - new Date().getTime()) / (1000 * 60 * 60 * 24)) : null;

    return (
        <div className="space-y-8 pb-12" data-testid="regular-dashboard">
            {/* Super Admin Banner */}
            {isAdmin() && (
                <motion.div initial={{opacity: 0, y: -10}} animate={{opacity: 1, y: 0}}>
                    <Card className="border-none shadow-sm bg-primary/5">
                        <CardContent className="p-4 flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <Shield className="h-5 w-5 text-primary"/>
                                <p className="text-sm font-medium text-slate-700">
                                    Viewing Management Cockpit as Super Admin
                                </p>
                            </div>
                            <Button variant="ghost" size="sm" onClick={() => router.push('/dashboard')}
                                    className="text-primary font-bold">
                                Back to Admin Dashboard <ArrowRight size={16} className="ml-2"/>
                            </Button>
                        </CardContent>
                    </Card>
                </motion.div>
            )}

            {/* Property Pulse Header */}
            <motion.div
                initial={{opacity: 0, y: -20}}
                animate={{opacity: 1, y: 0}}
                className="bg-slate-900 rounded-3xl p-1 shadow-[0_20px_50px_rgba(0,0,0,0.15)] overflow-hidden relative"
            >
                <div
                    className="absolute top-0 left-1/4 w-1/2 h-full bg-primary/20 blur-[100px] animate-pulse pointer-events-none"/>
                <div
                    className="bg-slate-800/40 backdrop-blur-2xl rounded-[1.6rem] px-8 py-5 flex flex-wrap items-center justify-between gap-8 relative z-10 border border-white/5">
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
                             onClick={() => router.push('/financials/overview')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Arrears</span>
                            <span className="text-sm font-black text-red-400">
                {arrearsAmount != null ? formatCurrency(arrearsAmount) : '$0'}
              </span>
                        </div>
                        <div className="flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/maintenance')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Open Works</span>
                            <span
                                className="text-sm font-black text-white">{maintenanceStats?.open_requests || 0} Open</span>
                        </div>
                        {daysToAgm !== null && (
                            <div
                                className="hidden lg:flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                                onClick={() => router.push('/governance/agm')}>
                                <span
                                    className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Next AGM</span>
                                <span className="text-sm font-black text-amber-400">{daysToAgm} Days</span>
                            </div>
                        )}
                    </div>

                    <div
                        className="flex items-center gap-2 text-white/30 text-[10px] font-black uppercase tracking-widest">
                        View Analytics <ArrowUpRight size={14} strokeWidth={3}/>
                    </div>
                </div>
            </motion.div>

            {/* Hero Section */}
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-6">
                <div>
                    <motion.div initial={{opacity: 0, x: -20}} animate={{opacity: 1, x: 0}}
                                className="flex items-center gap-3 mb-2">
                        <div className="p-2 rounded-xl bg-primary/10 text-primary">
                            <Shield size={24}/>
                        </div>
                        <span
                            className="text-sm font-bold text-primary uppercase tracking-widest">Committee Cockpit</span>
                    </motion.div>
                    <motion.h1 initial={{opacity: 0, y: 10}} animate={{opacity: 1, y: 0}} transition={{delay: 0.1}}
                               className="text-4xl font-black text-slate-900 tracking-tight">
                        Hello, {user?.full_name?.split(' ')[0]}
                    </motion.h1>
                    <motion.div initial={{opacity: 0}} animate={{opacity: 1}} transition={{delay: 0.2}}
                                className="flex items-center gap-4 mt-3">
                        <p className="text-slate-500 font-medium capitalize">
                            {user?.role?.replace('_', ' ')} • Management Overview
                        </p>
                        <YearSelector/>
                    </motion.div>
                </div>
            </div>

            {/* Row 1: KPI Overview */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {financialData ? (
                    <FinancialSummaryCard
                        financialData={financialData}
                        delay={0.1}
                        onClick={() => router.push('/financials/levy-payments?activeTab=levy-status')}
                    />
                ) : (
                    <GaugeCard
                        title="Levy Collection rate"
                        value={`${collectionRate}%`}
                        percentage={collectionRate}
                        color="#16A34A"
                        subtitle="Annual Target Progress"
                        delay={0.1}
                        onClick={() => router.push('/financials/overview')}
                    />
                )}

                <GaugeCard
                    title="Sinking Fund Health"
                    value={sinkingFundForecast?.summary?.status === 'healthy' ? "Healthy" : "Watch"}
                    percentage={(() => {
                        const proj = sinkingFundForecast?.projection;
                        if (!proj || proj.length === 0) return 0;
                        return Math.round((proj.filter((p: any) => p.closing_balance > 0).length / proj.length) * 100);
                    })()}
                    color={sinkingFundForecast?.summary?.status === 'healthy' ? "#2563EB" : "#F59E0B"}
                    subtitle={sinkingFundForecast?.summary?.status === 'healthy' ? 'WELL FUNDED' : 'FUNDING AT RISK'}
                    delay={0.2}
                    onClick={() => router.push('/intelligence/projections')}
                />

                <motion.div
                    initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} transition={{duration: 0.4, delay: 0.3}}
                    className="cursor-pointer" onClick={() => router.push('/financials/overview')}
                >
                    <Card className="h-full border-none card-3d bg-slate-900 text-white overflow-hidden group">
                        <div className="absolute top-0 right-0 p-6 opacity-10">
                            <TrendingUp size={80}/>
                        </div>
                        <CardHeader>
                            <CardTitle className="text-sm font-medium text-slate-400">Financial Position</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                <div>
                                    <p className="text-3xl font-bold">
                                        {financeSummary?.admin_fund ? formatCurrency(financeSummary.admin_fund.closing_balance + (financeSummary.sinking_fund?.closing_balance || 0)) : '$0'}
                                    </p>
                                    <p className="text-xs text-slate-400 mt-1 uppercase font-semibold tracking-wider">Total
                                        Combined Cash</p>
                                </div>

                                <div className="pt-4 space-y-3">
                                    <div
                                        className="flex items-center justify-between text-sm border-t border-white/10 pt-3">
                                        <span className="text-slate-400">Admin Fund</span>
                                        <span
                                            className="font-bold">{financeSummary?.admin_fund ? formatCurrency(financeSummary.admin_fund.closing_balance) : '$0'}</span>
                                    </div>
                                    <div
                                        className="flex items-center justify-between text-sm border-t border-white/10 pt-3">
                                        <span className="text-slate-400">Sinking Fund</span>
                                        <span
                                            className="font-bold">{financeSummary?.sinking_fund ? formatCurrency(financeSummary.sinking_fund.closing_balance) : '$0'}</span>
                                    </div>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </motion.div>
            </div>

            {/* Row 2: Building Operations */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <KPIStatCard
                    title="Building Maintenance" value={maintenanceStats?.open_requests || 0}
                    subtext="Active building-wide" trend={0} icon={Wrench} colorClass="bg-orange-50 text-orange-600"
                    delay={0.1}
                    onClick={() => router.push('/maintenance')}
                />
                <KPIStatCard
                    title="Safety Compliance"
                    value={complianceSummary ? `${complianceSummary.percentage}%` : '—'}
                    subtext={complianceSummary ? `${complianceSummary.completed}/${complianceSummary.total} items checked` : 'Loading...'}
                    trend={0}
                    icon={ShieldCheck}
                    colorClass={complianceSummary?.overdue > 0 ? 'bg-red-50 text-red-600' : complianceSummary?.pending > 0 ? 'bg-amber-50 text-amber-600' : 'bg-emerald-50 text-emerald-600'}
                    delay={0.2}
                    onClick={() => router.push('/compliance')}
                />
                <KPIStatCard
                    title="Resolution Time" value={`${maintenanceStats?.avg_resolution_days || 0} Days`}
                    subtext="Avg. this month" trend={-15} icon={Clock} colorClass="bg-blue-50 text-blue-600" delay={0.3}
                    onClick={() => router.push('/maintenance')}
                />
                <KPIStatCard
                    title="Upcoming Meetings" value={upcomingAgm ? "1" : "0"}
                    subtext={upcomingAgm ? upcomingAgm.title : "None scheduled"} trend={0} icon={Calendar}
                    colorClass="bg-purple-50 text-purple-600" delay={0.4}
                    onClick={() => router.push('/governance/meetings')}
                />
            </div>

            {/* Row 3: Insights & Feed */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                    <ChartCard
                        title="Total Building Levies"
                        description="Combined Admin & Sinking fund revenue"
                        delay={0.1}
                        onClick={() => router.push('/financials/overview')}
                    >
                        <AreaChart data={levyTrendData} margin={{top: 10, right: 10, left: -10, bottom: 0}}>
                            <defs>
                                <linearGradient id="colorTotal" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#6366F1" stopOpacity={0.3}/>
                                    <stop offset="95%" stopColor="#6366F1" stopOpacity={0}/>
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9"/>
                            <XAxis
                                dataKey="year"
                                axisLine={false}
                                tickLine={false}
                                tick={{fill: '#94a3b8', fontSize: 12, fontWeight: 600}}
                            />
                            <YAxis
                                axisLine={false}
                                tickLine={false}
                                tick={{fill: '#94a3b8', fontSize: 12, fontWeight: 600}}
                                tickFormatter={(value) => `$${(value / 1000).toFixed(0)}k`}
                            />
                            <RechartsTooltip
                                contentStyle={{
                                    borderRadius: '16px',
                                    border: 'none',
                                    boxShadow: '0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)',
                                    padding: '12px'
                                }}
                                itemStyle={{fontWeight: 700, fontSize: '12px'}}
                                labelStyle={{fontWeight: 800, color: '#0f172a', marginBottom: '4px', fontSize: '14px'}}
                                cursor={{stroke: '#e2e8f0', strokeWidth: 2}}
                            />
                            <Area
                                type="monotone"
                                dataKey="total"
                                name="Total Levies"
                                stroke="#6366F1"
                                fillOpacity={1}
                                fill="url(#colorTotal)"
                                strokeWidth={4}
                                animationDuration={1500}
                            />
                        </AreaChart>
                    </ChartCard>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <MarketSnapshotCard data={marketSnapshot} delay={0.2}
                                            onClick={() => router.push('/community/marketplace')}/>

                        <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}}
                                    transition={{delay: 0.3}}>
                            <Card
                                className="h-full border-none shadow-md bg-indigo-600 text-white overflow-hidden group cursor-pointer"
                                onClick={() => router.push('/community/directory')}>
                                <CardContent className="p-8 flex flex-col justify-between h-full">
                                    <div className="flex justify-between items-start">
                                        <div className="p-3 rounded-2xl bg-white/20">
                                            <Users size={24}/>
                                        </div>
                                        <ArrowUpRight
                                            className="text-white/40 group-hover:text-white transition-colors"/>
                                    </div>
                                    <div className="mt-8">
                                        <h3 className="text-xl font-bold mb-1">Resident Directory</h3>
                                        <p className="text-indigo-100 text-sm">Manage community members and unit
                                            assignments.</p>
                                    </div>
                                </CardContent>
                            </Card>
                        </motion.div>
                    </div>
                </div>

                <div className="lg:col-span-1">
                    <ActivityFeed activities={activities} delay={0.2}/>
                </div>
            </div>

            {/* Row 4: Quick Actions */}
            <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} transition={{delay: 0.4}}>
                <Card className="border-none shadow-lg bg-white overflow-hidden">
                    <CardHeader className="bg-slate-50/50 border-b">
                        <CardTitle className="text-lg font-bold">Management Tools</CardTitle>
                    </CardHeader>
                    <CardContent className="p-6">
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                            {quickActions.map((action, index) => (
                                <button
                                    key={index}
                                    onClick={() => router.push(action.href)}
                                    className="flex flex-col items-center justify-center p-6 rounded-3xl border border-slate-50 bg-white hover:border-primary/20 hover:bg-primary/5 transition-all duration-300 group shadow-sm hover:shadow-xl hover:-translate-y-1"
                                >
                                    <div
                                        className={`p-4 rounded-2xl ${action.color} mb-4 group-hover:scale-110 transition-transform duration-300 shadow-sm group-hover:shadow-md`}>
                                        <action.icon size={28} strokeWidth={2.5}/>
                                    </div>
                                    <span
                                        className="font-black text-[11px] text-slate-500 group-hover:text-primary uppercase tracking-widest transition-colors">{action.label}</span>
                                </button>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            </motion.div>
        </div>
    );
};

export default RegularDashboard;
