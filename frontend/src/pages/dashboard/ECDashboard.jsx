// @featuretrace:dashboard-v2 — Classic (pages-layer) EC Member dashboard: committee-facing
//   cockpit with governance actions, meeting prep, and a "My Unit View" handoff to /owner-view.
// Layer: frontend
// Data flow: this page → dashboard KPI/activity endpoints (building-scoped); "My Unit View"
//            button → /owner-view.
// Related: frontend/src/pages/dashboard/ManagerDashboard.jsx
//           frontend/src/app/(app)/owner-view/page.tsx
import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import YearSelector from '../../components/widgets/YearSelector';
import KPIStatCard from '../../components/dashboard/KPIStatCard';
import GaugeCard from '../../components/dashboard/GaugeCard';
import ChartCard from '../../components/dashboard/ChartCard';
import ActivityFeed from '../../components/dashboard/ActivityFeed';
import MarketSnapshotCard from '../../components/dashboard/MarketSnapshotCard';
import DashboardDetailModal from '../../components/dashboard/DashboardDetailModal';
import {
    Activity,
    AlertTriangle,
    ArrowRight,
    ArrowUpRight,
    Building,
    Calendar,
    ClipboardCheck,
    Clock,
    DollarSign,
    FileText,
    MessageSquare,
    Shield,
    ShieldCheck,
    Users,
    Wrench
} from 'lucide-react';
import { Area, AreaChart, CartesianGrid, Legend, Tooltip as RechartsTooltip, XAxis, YAxis } from 'recharts';
import { toast } from 'sonner';
import { motion } from 'framer-motion';
import { formatCurrency } from '../../lib/utils';
/**
 * Modernized EC Dashboard
 * Building-wide metrics with the new Property Intelligence look
 */
const ECDashboard = () => {
    const {user, api, token, hasPermission, isAdmin, selectedYear, setSelectedYear, selectedBuilding} = useAuth();
    const router = useRouter();

    const [loading, setLoading] = useState(true);

    // Data States
    const [kpiData, setKpiData] = useState(null);
    const [financeSummary, setFinanceSummary] = useState(null);
    const [maintenanceStats, setMaintenanceStats] = useState(null);
    const [activities, setActivities] = useState([]);
    const [marketSnapshot, setMarketSnapshot] = useState(null);
    const [levyTrendData, setLevyTrendData] = useState([]);
    const [upcomingAgm, setUpcomingAgm] = useState(null);
    const [sinkingFundForecast, setSinkingFundForecast] = useState(null);
    const [recentPostsCount, setRecentPostsCount] = useState(null);
    const [isArrearsModalOpen, setIsArrearsModalOpen] = useState(false);
    const [importantDocs, setImportantDocs] = useState([]);
    const [dismissedDocs, setDismissedDocs] = useState(
        () => new Set(typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]') : [])
    );

    const fetchDashboardData = useCallback(async () => {
        if (!token || !selectedYear) return;
        setLoading(true);
        try {
            const yearParam = `?year=${selectedYear}`;
            const kpiParam = `?financial_year=${selectedYear}`;

            // 1. Fetch building-wide KPIs
            // METRIC[collection_rate]: source endpoint — /stats/building-kpis.
            // Formula: confirmed_paid / (total_levied + total_opening_arrears).
            // Must match fund_health from /finance/building-overview — see test_metric_consistency.py.
            try {
                const kpiRes = await api.get(`/stats/building-kpis${kpiParam}`);
                setKpiData(kpiRes.data);
            } catch (err) {
                console.error('Failed to fetch KPIs:', err);
            }

            // 2. Fetch Finance Summary
            try {
                const financeRes = await api.get(`/finance/summary${yearParam}`);
                setFinanceSummary(financeRes.data);
            } catch (err) {
                console.error('Failed to fetch finance summary:', err);
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

            // 6. Fetch Levy Benchmark Data (building-wide actuals 2021-2026)
            try {
                const benchmarkRes = await api.get('/analytics/levy-benchmarks');
                setLevyTrendData(benchmarkRes.data || []);
            } catch (err) {
                console.error('Failed to fetch levy benchmarks:', err);
            }

            // 7. Fetch Upcoming AGM
            try {
                const agmRes = await api.get('/agm');
                const upcoming = ( agmRes.data || [] )
                    .filter(a => new Date(a.date) > new Date())
                    .sort((a, b) => new Date(a.date) - new Date(b.date))[ 0 ];
                setUpcomingAgm(upcoming);
            } catch (err) {
                console.error('Failed to fetch AGM:', err);
            }

            // 8. Fetch Sinking Fund Forecast
            try {
                const forecastRes = await api.get('/analytics/sinking-fund-forecast?years=10');
                setSinkingFundForecast(forecastRes.data);
            } catch (err) {
                console.error('Failed to fetch sinking fund forecast:', err);
            }

            // 9. Fetch recent announcements count (last 7 days)
            try {
                const announcementsRes = await api.get('/announcements?limit=100');
                const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
                const recent = ( announcementsRes.data || [] ).filter(
                    a => new Date(a.created_at) > sevenDaysAgo
                );
                setRecentPostsCount(recent.length);
            } catch (err) {
                console.error('Failed to fetch announcements count:', err);
            }

        } catch (error) {
            console.error('Failed to load dashboard data:', error);
            toast.error('Unable to load some dashboard components');
        } finally {
            setLoading(false);
        }
    }, [api, selectedYear, token]);

    useEffect(() => {
        fetchDashboardData();
    }, [fetchDashboardData]);

    useEffect(() => {
        if (!api) return;
        api.get('/documents/important').then(res => setImportantDocs(res.data || [])).catch(() => {
        });
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: dismissDoc
     * Path: frontend/src/pages/dashboard/ECDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const dismissDoc = (docId) => {
        const newSet = new Set([...dismissedDocs, docId]);
        setDismissedDocs(newSet);
        localStorage.setItem('dismissed_important_docs', JSON.stringify([...newSet]));
    };

    const quickActions = [
        {label: 'Finances', icon: DollarSign, href: '/financials/overview', color: 'bg-emerald-50 text-emerald-600'},
        {label: 'Units', icon: Building, href: '/admin/owners-units', color: 'bg-blue-50 text-blue-600'},
        {label: 'Meetings', icon: Calendar, href: '/governance/meetings', color: 'bg-purple-50 text-purple-600'},
        {
            label: 'Announcements',
            icon: MessageSquare,
            href: '/community/notices',
            color: 'bg-orange-50 text-orange-600'
        },
        {label: 'Compliance', icon: ShieldCheck, href: '/compliance', color: 'bg-cyan-50 text-cyan-600'},
        {label: 'Documents', icon: FileText, href: '/documents', color: 'bg-indigo-50 text-indigo-600'},
    ];

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[600px]">
                <div className="text-center">
                    <motion.div animate={{rotate: 360}} transition={{repeat: Infinity, duration: 1, ease: "linear"}}
                                className="h-12 w-12 border-4 border-primary border-t-transparent rounded-full mx-auto"/>
                    <p className="mt-4 text-slate-500 font-medium">Loading EC Cockpit...</p>
                </div>
            </div>
        );
    }

    // GAP-FIN-035 / GAP-DASH-001 P0-1: the ONLY figure that may be labelled "Collection Rate" is the
    // canonical due-date rate. The legacy kpiData.collection_rate is full-year Fund Health / coverage
    // ("Annual Levy Progress") and must never be shown under a Collection Rate label.
    const collectionRate = kpiData?.due_date_collection_rate_pct ?? null;
    const fundHealth = kpiData?.collection_rate ?? 0;
    const daysToAgm = upcomingAgm ? Math.ceil(( new Date(upcomingAgm.date) - new Date() ) / ( 1000 * 60 * 60 * 24 )) : null;

    return (
        <div className="space-y-8 pb-12" data-testid="ec-dashboard">
            {/* Important Document Alerts */}
            {importantDocs.filter(d => !dismissedDocs.has(d.id)).map(doc => (
                <div key={doc.id}
                     className="flex items-start gap-3 p-4 rounded-lg border border-amber-400 bg-amber-50 text-amber-900 shadow-sm">
                    <AlertTriangle className="h-5 w-5 text-amber-500 mt-0.5 shrink-0"/>
                    <div className="flex-1 min-w-0">
                        <p className="font-semibold text-sm">Important: {doc.title}</p>
                        {doc.importance_summary && (
                            <p className="text-sm mt-0.5 line-clamp-3">{doc.importance_summary}</p>
                        )}
                        <p className="text-xs text-amber-600 mt-1">{( doc.category || '' ).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</p>
                    </div>
                    <button onClick={() => dismissDoc(doc.id)}
                            className="shrink-0 text-amber-400 hover:text-amber-700 transition-colors text-lg leading-none"
                            aria-label="Dismiss">×
                    </button>
                </div>
            ))}

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
                                {collectionRate ?? '—'}%
              </span>
                        </div>
                        <div className="flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/admin/owners-units')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Arrears</span>
                            <span
                                className="text-sm font-black text-red-400">{kpiData?.units_in_arrears || 0} Units</span>
                        </div>
                        <div className="flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/maintenance')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Open Works</span>
                            <span
                                className="text-sm font-black text-white">{maintenanceStats?.open_requests || 0} Active</span>
                        </div>
                        {daysToAgm !== null && (
                            <div
                                className="hidden lg:flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                                onClick={() => router.push('/governance/agm')}>
                                <span
                                    className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">AGM</span>
                                <span className="text-sm font-black text-amber-400">{daysToAgm} Days</span>
                            </div>
                        )}
                    </div>
                    <Button size="lg" variant="ghost" onClick={() => router.push('/owner-view')}
                            className="text-white hover:bg-white/10 font-black border border-white/20 rounded-2xl px-6">
                        My Unit View <ArrowRight size={16} className="ml-2"/>
                    </Button>
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
                        <span className="text-sm font-bold text-primary uppercase tracking-widest">EC Cockpit</span>
                    </motion.div>
                    <motion.h1 initial={{opacity: 0, y: 10}} animate={{opacity: 1, y: 0}} transition={{delay: 0.1}}
                               className="text-4xl font-black text-slate-900 tracking-tight">
                        Building Overview
                    </motion.h1>
                    <motion.div initial={{opacity: 0}} animate={{opacity: 1}} transition={{delay: 0.2}}
                                className="flex items-center gap-4 mt-3">
                        <p className="text-slate-500 font-medium">{selectedBuilding?.name || 'Your Building'} •
                            Executive Committee Dashboard</p>
                        <YearSelector/>
                    </motion.div>
                </div>
            </div>

            {/* Row 1: KPI Overview */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                <GaugeCard
                    title="Fund Health" value={`${fundHealth}%`} percentage={fundHealth}
                    color="#16A34A" subtitle="Annual Levy Progress" delay={0.1}
                    onClick={() => router.push('/financials/overview')}
                />

                <GaugeCard
                    title="Reserve Fund Status"
                    value={sinkingFundForecast?.summary.status === 'healthy' ? 'Healthy' : 'Watch'}
                    percentage={sinkingFundForecast ? Math.min(100, Math.round(( sinkingFundForecast.summary.current_balance / 300000 ) * 100)) : 85}
                    color={sinkingFundForecast?.summary.status === 'healthy' ? '#2563EB' : '#F59E0B'}
                    subtitle="Sinking Fund Level"
                    delay={0.2}
                    onClick={() => router.push('/intelligence/projections')}
                />

                <motion.div
                    initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} transition={{duration: 0.4, delay: 0.3}}
                    className="cursor-pointer" onClick={() => setIsArrearsModalOpen(true)}
                >
                    <Card className="h-full border-none card-3d bg-slate-900 text-white overflow-hidden group">
                        <div className="absolute top-0 right-0 p-6 opacity-10">
                            <Activity size={80}/>
                        </div>
                        <CardHeader>
                            <CardTitle className="text-sm font-medium text-slate-400">Arrears Snapshot</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                <div>
                                    <p className="text-3xl font-bold">{formatCurrency(kpiData?.total_arrears || 0)}</p>
                                    <p className="text-xs text-slate-400 mt-1 uppercase font-semibold tracking-wider">Total
                                        Outstanding</p>
                                </div>

                                <div className="pt-4 space-y-3">
                                    <div
                                        className="flex items-center justify-between text-sm border-t border-white/10 pt-3">
                                        <span className="text-slate-400">Units Owning</span>
                                        <span
                                            className="font-bold text-red-400">{kpiData?.units_in_arrears || 0} Units</span>
                                    </div>
                                </div>

                                <Button className="w-full mt-2 bg-white text-slate-900 hover:bg-slate-100 font-bold"
                                        onClick={() => router.push('/notifications')}>
                                    Send Reminders
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </motion.div>
            </div>

            {/* Row 2: Building Stats */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <Link href="/requests/my-approvals" className="block">
                    <KPIStatCard
                        title="Approvals" value={kpiData?.pending_approvals_count || 0} subtext="Pending review"
                        icon={ClipboardCheck} colorClass="bg-purple-50 text-purple-600" delay={0.05}
                    />
                </Link>
                <KPIStatCard
                    title="Active Maintenance" value={maintenanceStats?.open_requests || 0}
                    subtext="Building-wide requests" icon={Wrench} colorClass="bg-orange-50 text-orange-600" delay={0.1}
                    onClick={() => router.push('/maintenance')}
                />
                <KPIStatCard
                    title="SLA Compliance"
                    value={maintenanceStats?.sla_compliance_rate ? `${maintenanceStats.sla_compliance_rate}%` : '—'}
                    subtext="Resolution within target"
                    icon={ShieldCheck}
                    colorClass="bg-emerald-50 text-emerald-600"
                    delay={0.2}
                />
                <KPIStatCard
                    title="Avg. Resolution" value={`${maintenanceStats?.avg_resolution_days || 0} Days`}
                    subtext="Last 30 days" icon={Clock} colorClass="bg-blue-50 text-blue-600" delay={0.3}
                    onClick={() => router.push('/maintenance')}
                />
                <KPIStatCard
                    title="Announcements"
                    value={recentPostsCount !== null ? recentPostsCount : '—'}
                    subtext="Posted in last 7 days"
                    icon={MessageSquare}
                    colorClass="bg-purple-50 text-purple-600"
                    delay={0.4}
                    onClick={() => router.push('/community/notices')}
                />
            </div>

            {/* Row 3: Insights & Feed */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">
                    <ChartCard
                        title="Revenue Trend (2021 - 2026)"
                        description="Building-wide annual levy collection by fund"
                        delay={0.1}
                        onClick={() => router.push('/financials/overview')}
                    >
                        <AreaChart data={levyTrendData} margin={{top: 10, right: 10, left: -10, bottom: 0}}>
                            <defs>
                                <linearGradient id="ecColorAdmin" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#16A34A" stopOpacity={0.25}/>
                                    <stop offset="95%" stopColor="#16A34A" stopOpacity={0}/>
                                </linearGradient>
                                <linearGradient id="ecColorSinking" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#7C3AED" stopOpacity={0.25}/>
                                    <stop offset="95%" stopColor="#7C3AED" stopOpacity={0}/>
                                </linearGradient>
                                <linearGradient id="ecColorBuilding" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#2563EB" stopOpacity={0.15}/>
                                    <stop offset="95%" stopColor="#2563EB" stopOpacity={0}/>
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9"/>
                            <XAxis
                                dataKey="year"
                                axisLine={false}
                                tickLine={false}
                                tick={{fill: '#94a3b8', fontSize: 12, fontWeight: 600}}
                                dy={10}
                            />
                            <YAxis
                                axisLine={false}
                                tickLine={false}
                                tick={{fill: '#94a3b8', fontSize: 12, fontWeight: 600}}
                                tickFormatter={(value) => `$${( value / 1000 ).toFixed(0)}k`}
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
                                formatter={(value, name) => [formatCurrency(value), name]}
                                cursor={{stroke: '#e2e8f0', strokeWidth: 2}}
                            />
                            <Legend wrapperStyle={{paddingTop: '8px', fontSize: '12px', fontWeight: 700}}/>
                            <Area type="monotone" dataKey="Admin" name="Admin Fund" stroke="#16A34A" fillOpacity={1}
                                  fill="url(#ecColorAdmin)" strokeWidth={2} animationDuration={1500}/>
                            <Area type="monotone" dataKey="Sinking" name="Sinking Fund" stroke="#7C3AED" fillOpacity={1}
                                  fill="url(#ecColorSinking)" strokeWidth={2} animationDuration={1500}/>
                            <Area type="monotone" dataKey="Building" name="Total Revenue" stroke="#2563EB"
                                  fillOpacity={1} fill="url(#ecColorBuilding)" strokeWidth={3}
                                  animationDuration={1500}/>
                        </AreaChart>
                    </ChartCard>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <MarketSnapshotCard data={marketSnapshot} delay={0.2}
                                            onClick={() => router.push('/community/marketplace')}/>
                        <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}}
                                    transition={{delay: 0.3}}>
                            <Card
                                className="h-full border-none shadow-md bg-indigo-600 text-white overflow-hidden group cursor-pointer"
                                onClick={() => router.push('/admin/users')}>
                                <CardContent className="p-8 flex flex-col justify-between h-full">
                                    <div className="flex justify-between items-start">
                                        <div className="p-3 rounded-2xl bg-white/20">
                                            <Users size={24}/>
                                        </div>
                                        <ArrowUpRight
                                            className="text-white/40 group-hover:text-white transition-colors"/>
                                    </div>
                                    <div className="mt-8">
                                        <h3 className="text-xl font-bold mb-1">User Approvals</h3>
                                        <p className="text-indigo-100 text-sm">Review pending registrations and verify
                                            owners.</p>
                                    </div>
                                </CardContent>
                            </Card>
                        </motion.div>
                    </div>

                    {sinkingFundForecast?.projection && (
                        <ChartCard
                            title="10-Year Reserve Fund Projection"
                            description="Sinking fund balance forecast with major capital works"
                            delay={0.25}
                            onClick={() => router.push('/intelligence/projections')}
                        >
                            <AreaChart data={sinkingFundForecast.projection}
                                       margin={{top: 10, right: 10, left: -10, bottom: 0}}>
                                <defs>
                                    <linearGradient id="ecColorForecast" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#0EA5E9" stopOpacity={0.3}/>
                                        <stop offset="95%" stopColor="#0EA5E9" stopOpacity={0}/>
                                    </linearGradient>
                                    <linearGradient id="ecColorContrib" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#16A34A" stopOpacity={0.2}/>
                                        <stop offset="95%" stopColor="#16A34A" stopOpacity={0}/>
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9"/>
                                <XAxis dataKey="year" axisLine={false} tickLine={false}
                                       tick={{fill: '#94a3b8', fontSize: 12, fontWeight: 600}} dy={10}/>
                                <YAxis axisLine={false} tickLine={false}
                                       tick={{fill: '#94a3b8', fontSize: 12, fontWeight: 600}}
                                       tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`}/>
                                <RechartsTooltip
                                    contentStyle={{
                                        borderRadius: '16px',
                                        border: 'none',
                                        boxShadow: '0 20px 25px -5px rgb(0 0 0 / 0.1)',
                                        padding: '12px'
                                    }}
                                    itemStyle={{fontWeight: 700, fontSize: '12px'}}
                                    labelStyle={{
                                        fontWeight: 800,
                                        color: '#0f172a',
                                        marginBottom: '4px',
                                        fontSize: '14px'
                                    }}
                                    formatter={(value, name) => [formatCurrency(value), name]}
                                    cursor={{stroke: '#e2e8f0', strokeWidth: 2}}
                                />
                                <Legend wrapperStyle={{paddingTop: '8px', fontSize: '12px', fontWeight: 700}}/>
                                <Area type="monotone" dataKey="contributions" name="Annual Contributions"
                                      stroke="#16A34A" fillOpacity={1} fill="url(#ecColorContrib)" strokeWidth={2}
                                      animationDuration={1500}/>
                                <Area type="monotone" dataKey="closing_balance" name="Fund Balance" stroke="#0EA5E9"
                                      fillOpacity={1} fill="url(#ecColorForecast)" strokeWidth={3}
                                      animationDuration={1500}/>
                            </AreaChart>
                        </ChartCard>
                    )}
                </div>

                <div className="lg:col-span-1">
                    <ActivityFeed activities={activities} delay={0.2}/>
                </div>
            </div>

            {/* Arrears Detail Modal */}
            <DashboardDetailModal
                isOpen={isArrearsModalOpen}
                onClose={() => setIsArrearsModalOpen(false)}
                title="Arrears Intelligence"
                description="Detailed outstanding levy summary"
                actionLabel="Manage Arrears"
                onAction={() => router.push('/financials/overview')}
            >
                <div className="space-y-4">
                    <div
                        className="flex justify-between items-center p-4 rounded-2xl bg-white shadow-sm border border-slate-100">
                        <span className="text-sm font-bold text-slate-500">Total Outstanding</span>
                        <span
                            className="text-2xl font-black text-red-600">{formatCurrency(kpiData?.total_arrears || 0)}</span>
                    </div>
                    <div
                        className="flex justify-between items-center p-4 rounded-2xl bg-white shadow-sm border border-slate-100">
                        <span className="text-sm font-bold text-slate-500">Units in Debt</span>
                        <span
                            className="text-xl font-black text-slate-900">{kpiData?.units_in_arrears || 0} Units</span>
                    </div>
                    <p className="text-sm font-medium text-slate-500 leading-relaxed">
                        The building's collection rate is currently at <span
                        className="text-emerald-500 font-bold">{collectionRate ?? '—'}%</span>.
                        Most arrears are within the 30-60 day range. Consider sending automated reminders to
                        the {kpiData?.units_in_arrears || 0} units with outstanding balances.
                    </p>
                </div>
            </DashboardDetailModal>

            {/* Row 4: Tools */}
            <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} transition={{delay: 0.4}}>
                <Card className="border-none shadow-lg bg-white overflow-hidden">
                    <CardHeader className="bg-slate-50/50 border-b">
                        <CardTitle className="text-lg font-bold">Committee Tools</CardTitle>
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

export default ECDashboard;
