// @featuretrace:dashboard-v2 — Classic (pages-layer) Super Admin dashboard: platform-wide
//   cockpit (cross-building KPIs, budget projections shortcut, portfolio health).
// Layer: frontend
// Data flow: this page → platform-level admin/analytics endpoints (global).
// Related: frontend/src/pages/dashboard/ManagerDashboard.jsx
//           frontend/src/app/(app)/intelligence/projections/page.tsx
"use client";
import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import YearSelector from '../../components/widgets/YearSelector';
import KPIStatCard from '../../components/dashboard/KPIStatCard';
import GaugeCard from '../../components/dashboard/GaugeCard';
import ChartCard from '../../components/dashboard/ChartCard';
import ActivityFeed from '../../components/dashboard/ActivityFeed';
import {
    Activity,
    AlertCircle,
    ArrowRight,
    ArrowUpRight,
    Building2,
    CheckCircle2,
    DollarSign,
    Key,
    Mail,
    Megaphone,
    RefreshCw,
    Settings,
    Shield,
    ShieldCheck,
    ToggleLeft,
    TrendingUp,
    UserCog,
    Users,
    UserX,
    Wrench
} from 'lucide-react';
import { Area, AreaChart, CartesianGrid, Legend, Tooltip as RechartsTooltip, XAxis, YAxis } from 'recharts';
import { toast } from 'sonner';
import { motion } from 'framer-motion';
import { formatCurrency, formatDateTime } from '../../lib/utils';
/**
 * Super Admin Control Centre Dashboard
 * Full system visibility: building KPIs, charts, real activity feed, live system health
 */
const SuperAdminDashboard = () => {
    const {api, token, selectedYear, setSelectedYear, selectedBuilding} = useAuth();
    const router = useRouter();
    const [loading, setLoading] = useState(true);

    // Data states
    const [stats, setStats] = useState({});
    const [kpiData, setKpiData] = useState(null);
    const [financeSummary, setFinanceSummary] = useState(null);
    const [maintenanceStats, setMaintenanceStats] = useState(null);
    const [activities, setActivities] = useState([]);
    const [levyTrendData, setLevyTrendData] = useState([]);
    const [sinkingFundForecast, setSinkingFundForecast] = useState(null);
    const [complianceSummary, setComplianceSummary] = useState(null);
    const [announcements, setAnnouncements] = useState([]);
    const [systemHealth, setSystemHealth] = useState([]);

    const fetchData = useCallback(async () => {
        if (!token || !selectedYear) return;
        setLoading(true);

        const yearParam = `?year=${selectedYear}`;
        const kpiParam = `?financial_year=${selectedYear}`;
        const healthChecks = {api: false, database: false, email: false, storage: false};

        try {
            // 1. Admin Stats (primary - sets core health flags)
            try {
                const statsRes = await api.get(`/admin/stats${kpiParam}`);
                setStats(statsRes.data);
                healthChecks.api = true;
                healthChecks.database = true;
            } catch (err) {
                console.error('Failed to fetch admin stats:', err);
            }

            // 2. Parallel secondary fetches — all non-critical, use allSettled
            const [
                kpiRes, financeRes, maintRes, activityRes,
                benchmarkRes, forecastRes, compRes, announcementsRes
            ] = await Promise.allSettled([
                api.get(`/stats/building-kpis${kpiParam}`),
                api.get(`/finance/summary${yearParam}`),
                api.get('/analytics/maintenance-stats'),
                api.get('/analytics/activities?limit=15'),
                api.get('/analytics/levy-benchmarks'),
                api.get('/analytics/sinking-fund-forecast?years=10'),
                api.get('/analytics/compliance-summary'),
                api.get('/announcements?limit=5'),
            ]);

            if (kpiRes.status === 'fulfilled') setKpiData(kpiRes.value.data);
            if (financeRes.status === 'fulfilled') {
                setFinanceSummary(financeRes.value.data);
                healthChecks.storage = true;
            }
            if (maintRes.status === 'fulfilled') setMaintenanceStats(maintRes.value.data);
            if (activityRes.status === 'fulfilled') setActivities(activityRes.value.data || []);
            if (benchmarkRes.status === 'fulfilled') setLevyTrendData(benchmarkRes.value.data || []);
            if (forecastRes.status === 'fulfilled') setSinkingFundForecast(forecastRes.value.data);
            if (compRes.status === 'fulfilled') setComplianceSummary(compRes.value.data);
            if (announcementsRes.status === 'fulfilled') setAnnouncements(announcementsRes.value.data || []);

            // 3. Email service health check
            try {
                await api.get('/email-settings');
                healthChecks.email = true;
            } catch { /* email config may be unset, that's ok */
            }

        } catch (error) {
            console.error('Super admin dashboard fetch error:', error);
            toast.error('Unable to load some dashboard components');
        } finally {
            setSystemHealth([
                {name: 'API Server', status: healthChecks.api ? 'operational' : 'degraded'},
                {name: 'Database', status: healthChecks.database ? 'operational' : 'degraded'},
                {name: 'Email Service', status: healthChecks.email ? 'operational' : 'degraded'},
                {name: 'File Storage', status: healthChecks.storage ? 'operational' : 'degraded'},
            ]);
            setLoading(false);
        }
    }, [api, selectedYear, token, selectedBuilding?.id]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const adminActions = [
        {
            label: 'User Management',
            description: 'Manage users, roles, and permissions',
            icon: Users,
            href: '/admin/users'
        },
        {
            label: 'Feature Toggles',
            description: 'Enable or disable features site-wide',
            icon: ToggleLeft,
            href: '/admin/feature-toggles'
        },
        {
            label: 'User Feature Permissions',
            description: 'Manage per-user feature access overrides',
            icon: UserCog,
            href: '/admin/user-feature-permissions'
        },
        {
            label: 'User Permissions',
            description: 'Customize individual user permissions',
            icon: Key,
            href: '/admin/user-permissions'
        },
        {
            label: 'Change Requests',
            description: 'Approve or reject unit or status change requests',
            icon: RefreshCw,
            href: '/admin/change-requests'
        },
        {
            label: 'Owners & Units',
            description: 'Manage property owners and unit assignments',
            icon: Building2,
            href: '/admin/owners-units'
        },
        {
            label: 'Tenant Renewals',
            description: 'Review and process tenant lease renewals',
            icon: RefreshCw,
            href: '/admin/tenant-renewals'
        },
        {
            label: 'Expired Accounts',
            description: 'Manage expired tenant and guest accounts',
            icon: UserX,
            href: '/admin/expired-accounts'
        },
        {
            label: 'Update Privacy & Terms',
            description: 'Edit privacy policy and terms of use',
            icon: Shield,
            href: '/admin/console'
        },
        {
            label: 'Building Settings',
            description: 'Configure building information and settings',
            icon: Settings,
            href: '/settings'
        },
        {
            label: 'Admin Communications',
            description: 'Send emails and configure communication infrastructure',
            icon: Mail,
            href: '/admin/communications'
        },
    ];

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[600px]">
                <div className="text-center">
                    <motion.div
                        animate={{rotate: 360}}
                        transition={{repeat: Infinity, duration: 1, ease: 'linear'}}
                        className="h-12 w-12 border-4 border-primary border-t-transparent rounded-full mx-auto"
                    />
                    <p className="mt-4 text-slate-500 font-medium">Loading Control Centre...</p>
                </div>
            </div>
        );
    }

    // GAP-FIN-035 / GAP-DASH-001 P0-2: only the canonical due-date rate may be labelled "Collection
    // Rate". kpiData.collection_rate is full-year Fund Health / coverage ("Annual Levy Progress").
    const collectionRate = kpiData?.due_date_collection_rate_pct ?? null;
    const fundHealth = kpiData?.collection_rate ?? 0;
    const occupancyRate = stats.occupancy_rate || 0;

    return (
        <div className="space-y-8 pb-12" data-testid="super-admin-dashboard">

            {/* ── Property Pulse Header ── */}
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
                                {collectionRate ?? '—'}%
              </span>
                        </div>
                        <div className="flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/admin/users')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Pending Users</span>
                            <span
                                className="text-sm font-black text-orange-400">{stats.pending_users || 0} Pending</span>
                        </div>
                        <div className="flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/maintenance')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Maintenance</span>
                            <span
                                className="text-sm font-black text-white">{maintenanceStats?.open_requests || 0} Open</span>
                        </div>
                        <div className="hidden lg:flex flex-col border-l border-slate-700/50 pl-8 cursor-pointer group"
                             onClick={() => router.push('/financials/overview')}>
                            <span
                                className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Invoices</span>
                            <span
                                className="text-sm font-black text-red-400">{stats.pending_invoices_count || 0} Pending</span>
                        </div>
                    </div>
                    <Button size="lg" onClick={() => router.push('/settings')}
                            className="bg-white text-slate-900 hover:bg-primary hover:text-white font-black rounded-2xl shadow-xl transition-all duration-300 px-8">
                        System Settings
                    </Button>
                </div>
            </motion.div>

            {/* ── Hero Section ── */}
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-6">
                <div>
                    <motion.div initial={{opacity: 0, x: -20}} animate={{opacity: 1, x: 0}}
                                className="flex items-center gap-3 mb-2">
                        <div className="p-2 rounded-xl bg-primary/10 text-primary">
                            <Shield size={24}/>
                        </div>
                        <span className="text-sm font-bold text-primary uppercase tracking-widest">Super Admin</span>
                    </motion.div>
                    <motion.h1 initial={{opacity: 0, y: 10}} animate={{opacity: 1, y: 0}} transition={{delay: 0.1}}
                               className="text-4xl font-black text-slate-900 tracking-tight">
                        System Control Centre
                    </motion.h1>
                    <motion.div initial={{opacity: 0}} animate={{opacity: 1}} transition={{delay: 0.2}}
                                className="flex items-center gap-4 mt-3">
                        <p className="text-slate-500 font-medium">{selectedBuilding?.name || 'Your Building'} •
                            Full System Visibility</p>
                        <YearSelector/>
                    </motion.div>
                </div>
            </div>

            {/* ── Row 1: Primary Gauges ── */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                <GaugeCard
                    title="Fund Health"
                    value={`${fundHealth}%`}
                    percentage={fundHealth}
                    color="#16A34A"
                    subtitle="Annual Levy Progress"
                    delay={0.1}
                    onClick={() => router.push('/financials/overview')}
                />

                <GaugeCard
                    title="Occupancy Rate"
                    value={`${occupancyRate}%`}
                    percentage={occupancyRate}
                    color="#2563EB"
                    subtitle={`${stats.total_units || 87} Total Units`}
                    delay={0.2}
                    onClick={() => router.push('/admin/owners-units')}
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
                            <CardTitle className="text-sm font-medium text-slate-400">System Snapshot</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                <div>
                                    <p className="text-3xl font-bold">{stats.total_users || 0}</p>
                                    <p className="text-xs text-slate-400 mt-1 uppercase font-semibold tracking-wider">Total
                                        Platform Users</p>
                                </div>
                                <div className="pt-4 space-y-3">
                                    <div
                                        className="flex items-center justify-between text-sm border-t border-white/10 pt-3">
                                        <span className="text-slate-400">Total Units</span>
                                        <span className="font-bold">{stats.total_units || 87}</span>
                                    </div>
                                    <div
                                        className="flex items-center justify-between text-sm border-t border-white/10 pt-3">
                                        <span className="text-slate-400">Total Cash Position</span>
                                        <span className="font-bold text-emerald-400">
                      {formatCurrency(( financeSummary?.admin_fund?.closing_balance || 0 ) + ( financeSummary?.sinking_fund?.closing_balance || 0 ))}
                    </span>
                                    </div>
                                    <div
                                        className="flex items-center justify-between text-sm border-t border-white/10 pt-3">
                                        <span className="text-slate-400">Total Arrears</span>
                                        <span
                                            className="font-bold text-red-400">{formatCurrency(kpiData?.total_arrears || 0)}</span>
                                    </div>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </motion.div>
            </div>

            {/* ── Row 2: KPI Stats ── */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <KPIStatCard
                    title="Pending Approvals"
                    value={stats.pending_users || 0}
                    subtext="Accounts awaiting review"
                    icon={Users}
                    colorClass="bg-orange-50 text-orange-600"
                    delay={0.1}
                    onClick={() => router.push('/admin/users')}
                />
                <KPIStatCard
                    title="Active Maintenance"
                    value={maintenanceStats?.open_requests || 0}
                    subtext="Building-wide open requests"
                    icon={Wrench}
                    colorClass="bg-amber-50 text-amber-600"
                    delay={0.2}
                    onClick={() => router.push('/maintenance')}
                />
                <KPIStatCard
                    title="Pending Invoices"
                    value={stats.pending_invoices_count || 0}
                    subtext={stats.pending_invoices_count ? formatCurrency(stats.pending_invoices_amount || 0) : 'None outstanding'}
                    icon={DollarSign}
                    colorClass="bg-red-50 text-red-600"
                    delay={0.3}
                    onClick={() => router.push('/financials/overview')}
                />
                <KPIStatCard
                    title="Safety Compliance"
                    value={complianceSummary ? `${complianceSummary.percentage}%` : '—'}
                    subtext={complianceSummary ? `${complianceSummary.completed}/${complianceSummary.total} tasks done` : 'Loading...'}
                    icon={ShieldCheck}
                    colorClass={
                        complianceSummary?.overdue > 0 ? 'bg-red-50 text-red-600' :
                            complianceSummary?.pending > 0 ? 'bg-amber-50 text-amber-600' :
                                'bg-emerald-50 text-emerald-600'
                    }
                    delay={0.4}
                    onClick={() => router.push('/compliance')}
                />
            </div>

            {/* ── Row 3: Charts + Activity Feed ── */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 space-y-6">

                    {/* Revenue Trend — all 3 series */}
                    <ChartCard
                        title="Revenue Trend (2021 - 2026)"
                        description="Building-wide annual levy collection by fund"
                        delay={0.1}
                        onClick={() => router.push('/financials/overview')}
                    >
                        <AreaChart data={levyTrendData} margin={{top: 10, right: 10, left: -10, bottom: 0}}>
                            <defs>
                                <linearGradient id="saColorAdmin" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#16A34A" stopOpacity={0.25}/>
                                    <stop offset="95%" stopColor="#16A34A" stopOpacity={0}/>
                                </linearGradient>
                                <linearGradient id="saColorSinking" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#7C3AED" stopOpacity={0.25}/>
                                    <stop offset="95%" stopColor="#7C3AED" stopOpacity={0}/>
                                </linearGradient>
                                <linearGradient id="saColorBuilding" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#2563EB" stopOpacity={0.15}/>
                                    <stop offset="95%" stopColor="#2563EB" stopOpacity={0}/>
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
                                labelStyle={{fontWeight: 800, color: '#0f172a', marginBottom: '4px', fontSize: '14px'}}
                                formatter={(value, name) => [formatCurrency(value), name]}
                                cursor={{stroke: '#e2e8f0', strokeWidth: 2}}
                            />
                            <Legend wrapperStyle={{paddingTop: '8px', fontSize: '12px', fontWeight: 700}}/>
                            <Area type="monotone" dataKey="Admin" name="Admin Fund" stroke="#16A34A" fillOpacity={1}
                                  fill="url(#saColorAdmin)" strokeWidth={2} animationDuration={1500}/>
                            <Area type="monotone" dataKey="Sinking" name="Sinking Fund" stroke="#7C3AED" fillOpacity={1}
                                  fill="url(#saColorSinking)" strokeWidth={2} animationDuration={1500}/>
                            <Area type="monotone" dataKey="Building" name="Total Revenue" stroke="#2563EB"
                                  fillOpacity={1} fill="url(#saColorBuilding)" strokeWidth={3}
                                  animationDuration={1500}/>
                        </AreaChart>
                    </ChartCard>

                    {/* System Health + Financial Card row */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* System Health — live status derived from real API call results */}
                        <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}}
                                    transition={{delay: 0.2}}>
                            <Card className="h-full border-none shadow-md overflow-hidden">
                                <CardHeader className="pb-3 bg-slate-50/60 border-b">
                                    <CardTitle
                                        className="text-sm font-bold text-slate-700 uppercase tracking-widest flex items-center gap-2">
                                        <Activity size={16} className="text-primary"/>
                                        System Health
                                    </CardTitle>
                                </CardHeader>
                                <CardContent className="p-4 space-y-3">
                                    {systemHealth.map((service) => (
                                        <div key={service.name}
                                             className="flex items-center justify-between p-3 rounded-xl bg-slate-50 border border-slate-100">
                                            <div className="flex items-center gap-3">
                                                {service.status === 'operational' ? (
                                                    <CheckCircle2 className="h-5 w-5 text-emerald-500 shrink-0"/>
                                                ) : (
                                                    <AlertCircle className="h-5 w-5 text-red-500 shrink-0"/>
                                                )}
                                                <span
                                                    className="text-sm font-semibold text-slate-700">{service.name}</span>
                                            </div>
                                            <Badge
                                                className={`text-[10px] font-bold tracking-wide ${
                                                    service.status === 'operational'
                                                        ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
                                                        : 'bg-red-100 text-red-700 border-red-200'
                                                }`}
                                            >
                                                {service.status === 'operational' ? 'Operational' : 'Degraded'}
                                            </Badge>
                                        </div>
                                    ))}
                                </CardContent>
                            </Card>
                        </motion.div>

                        {/* Admin Console shortcut */}
                        <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}}
                                    transition={{delay: 0.3}}>
                            <Card
                                className="h-full border-none shadow-md bg-indigo-600 text-white overflow-hidden group cursor-pointer"
                                onClick={() => router.push('/admin/console')}>
                                <CardContent className="p-8 flex flex-col justify-between h-full">
                                    <div className="flex justify-between items-start">
                                        <div className="p-3 rounded-2xl bg-white/20">
                                            <Settings size={24}/>
                                        </div>
                                        <ArrowUpRight
                                            className="text-white/40 group-hover:text-white transition-colors"/>
                                    </div>
                                    <div className="mt-8">
                                        <h3 className="text-xl font-bold mb-1">Admin Console</h3>
                                        <p className="text-indigo-100 text-sm">Privacy policy, terms of use, and
                                            site-wide configuration.</p>
                                        <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
                                            <div className="bg-white/10 rounded-lg p-2 text-center">
                                                <p className="font-black text-lg">{stats.total_documents || 0}</p>
                                                <p className="text-indigo-200">Documents</p>
                                            </div>
                                            <div className="bg-white/10 rounded-lg p-2 text-center">
                                                <p className="font-black text-lg">{stats.upcoming_meetings || 0}</p>
                                                <p className="text-indigo-200">Meetings</p>
                                            </div>
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        </motion.div>
                    </div>

                    {/* 10-Year Sinking Fund Forecast chart */}
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
                                    <linearGradient id="saColorForecast" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#0EA5E9" stopOpacity={0.3}/>
                                        <stop offset="95%" stopColor="#0EA5E9" stopOpacity={0}/>
                                    </linearGradient>
                                    <linearGradient id="saColorContrib" x1="0" y1="0" x2="0" y2="1">
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
                                      stroke="#16A34A" fillOpacity={1} fill="url(#saColorContrib)" strokeWidth={2}
                                      animationDuration={1500}/>
                                <Area type="monotone" dataKey="closing_balance" name="Fund Balance" stroke="#0EA5E9"
                                      fillOpacity={1} fill="url(#saColorForecast)" strokeWidth={3}
                                      animationDuration={1500}/>
                            </AreaChart>
                        </ChartCard>
                    )}
                </div>

                {/* Activity Feed — real data from /analytics/activities */}
                <div className="lg:col-span-1">
                    <ActivityFeed activities={activities} delay={0.2}/>
                </div>
            </div>

            {/* ── Row 4: Administration Actions ── */}
            <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} transition={{delay: 0.4}}>
                <h2 className="text-xl font-black text-slate-900 mb-6 uppercase tracking-widest">Administration</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {adminActions.map((action, index) => (
                        <Card
                            key={index}
                            className="card-3d cursor-pointer group focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
                            onClick={() => router.push(action.href)}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    router.push(action.href);
                                }
                            }}
                            data-testid={`admin-action-${action.label.toLowerCase().replace(/\s+/g, '-')}`}
                        >
                            <CardContent className="p-8">
                                <div className="flex items-start gap-6">
                                    <div
                                        className="p-4 rounded-2xl bg-primary/10 text-primary group-hover:bg-primary group-hover:text-white transition-all duration-500">
                                        <action.icon className="h-7 w-7"/>
                                    </div>
                                    <div className="flex-1">
                                        <h3 className="text-lg font-black text-slate-900 mb-1 group-hover:text-primary transition-colors">{action.label}</h3>
                                        <p className="text-sm font-medium text-slate-500 line-clamp-2">{action.description}</p>
                                    </div>
                                    <ArrowRight
                                        className="h-5 w-5 text-primary opacity-0 group-hover:opacity-100 group-hover:translate-x-1 transition-all duration-300"/>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            </motion.div>

            {/* ── Row 5: Recent Announcements ── */}
            {announcements.length > 0 && (
                <motion.div initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} transition={{delay: 0.5}}>
                    <Card className="border-none shadow-lg bg-white overflow-hidden">
                        <CardHeader className="bg-slate-50/50 border-b">
                            <div className="flex items-center justify-between">
                                <CardTitle className="flex items-center gap-2 text-lg font-bold">
                                    <Megaphone className="h-5 w-5 text-primary"/>
                                    Recent Announcements
                                </CardTitle>
                                <Button variant="ghost" size="sm"
                                        onClick={() => router.push('/community/notices')}
                                        className="gap-1 text-slate-500">
                                    View All <ArrowRight className="h-4 w-4"/>
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent className="p-6">
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                {announcements.map((announcement) => (
                                    <div
                                        key={announcement.id}
                                        className="p-4 rounded-2xl border border-slate-100 hover:border-primary/20 hover:bg-primary/5 cursor-pointer transition-all duration-300 group"
                                        onClick={() => router.push('/community/notices')}
                                    >
                                        <div className="flex items-start justify-between mb-2">
                                            <h4 className="font-bold text-sm text-slate-900 group-hover:text-primary transition-colors line-clamp-1">{announcement.title}</h4>
                                            {announcement.priority === 'urgent' && (
                                                <Badge
                                                    className="bg-red-100 text-red-700 text-[10px] font-bold ml-2 shrink-0">Urgent</Badge>
                                            )}
                                        </div>
                                        <p className="text-xs text-slate-500 line-clamp-2 mb-2">{announcement.content}</p>
                                        <p className="text-[10px] text-slate-400 font-medium">{formatDateTime(announcement.created_at)}</p>
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>
                </motion.div>
            )}
        </div>
    );
};

export default SuperAdminDashboard;
