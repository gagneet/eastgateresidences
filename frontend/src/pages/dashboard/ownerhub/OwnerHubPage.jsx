// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import {
    Activity,
    AlertTriangle,
    ArrowRight,
    Building2,
    ChevronRight,
    Home,
    Plus,
    RefreshCw,
    TrendingUp,
    Users,
    Wrench
} from 'lucide-react';
import { motion } from 'framer-motion';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Badge } from '../../../components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger, } from '../../../components/ui/tooltip';
import { formatCurrency, formatDate } from '../../../lib/utils';

const ALLOWED_ROLES = ['owner', 'real_estate_agent', 'super_admin', 'strata_manager', 'ec_member'];
/**
 * @generated FunctionHeader
 * Function: healthColor
 * Path: frontend/src/pages/dashboardhub/OwnerHubPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function healthColor(score) {
    if (score >= 75) return {text: 'text-green-600', bg: 'bg-green-500', ring: '#16a34a'};
    if (score >= 50) return {text: 'text-yellow-600', bg: 'bg-yellow-500', ring: '#ca8a04'};
    if (score >= 30) return {text: 'text-orange-600', bg: 'bg-orange-500', ring: '#ea580c'};
    return {text: 'text-red-600', bg: 'bg-red-500', ring: '#dc2626'};
}
/**
 * @generated FunctionHeader
 * Function: HealthGauge
 * Path: frontend/src/pages/dashboardhub/OwnerHubPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function HealthGauge({score}) {
    const size = 52;
    const radius = 20;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - ( score / 100 ) * circumference;
    const colors = healthColor(score);
    return (
        <div className="flex flex-col items-center">
            <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
                <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#e5e7eb" strokeWidth="5"/>
                <circle
                    cx={size / 2} cy={size / 2} r={radius} fill="none"
                    stroke={colors.ring} strokeWidth="5"
                    strokeDasharray={circumference} strokeDashoffset={offset}
                    strokeLinecap="round"
                    transform={`rotate(-90 ${size / 2} ${size / 2})`}
                />
                <text x={size / 2} y={size / 2 + 5} textAnchor="middle" fontSize="11" fontWeight="bold"
                      fill={colors.ring}>{score}</text>
            </svg>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboardhub/OwnerHubPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatusBadge({status}) {
    const config = {
        occupied: 'bg-green-100 text-green-800',
        vacant: 'bg-amber-100 text-amber-800',
    };
    return (
        <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${config[ status ] || 'bg-gray-100 text-gray-800'}`}>
      {status === 'occupied' ? 'Occupied' : 'Vacant'}
    </span>
    );
}
/**
 * @generated FunctionHeader
 * Function: OwnerHubPage
 * Path: frontend/src/pages/dashboardhub/OwnerHubPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const OwnerHubPage = () => {
    const {user, api} = useAuth();
    const router = useRouter();
    const [properties, setProperties] = useState([]);
    const [radar, setRadar] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    const canAccess = user && ALLOWED_ROLES.includes(user.role);

    const fetchData = useCallback(async () => {
        try {
            const [propsRes, radarRes] = await Promise.all([
                api.get('/owner-hub/properties'),
                api.get('/owner-hub/weekly-radar'),
            ]);
            setProperties(propsRes.data || []);
            setRadar(radarRes.data || null);
        } catch (err) {
            toast.error('Failed to load Owner Hub data');
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [api]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchData();
    }, [canAccess, fetchData, router]);
    /**
     * @generated FunctionHeader
     * Function: handleRefresh
     * Path: frontend/src/pages/dashboardhub/OwnerHubPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRefresh = () => {
        setRefreshing(true);
        fetchData();
    };

    if (!canAccess) return null;

    const totalProperties = properties.length;
    const activeTenancies = properties.filter(p => p.status === 'occupied').length;
    const monthlyRent = properties.reduce((sum, p) => sum + ( p.weekly_rent || 0 ) * 52 / 12, 0);
    const avgHealth = properties.length
        ? Math.round(properties.reduce((sum, p) => sum + ( p.health_score || 0 ), 0) / properties.length)
        : 0;

    const recentMaintenance = ( radar?.recent_maintenance || [] ).slice(0, 3);

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Owner Hub</h1>
                    <p className="text-sm text-gray-500 mt-1">Your property portfolio at a glance</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>
                        Refresh
                    </Button>
                    <Link href="/owner-hub/properties">
                        <Button size="sm"><Plus className="h-4 w-4 mr-2"/>Add Property</Button>
                    </Link>
                </div>
            </div>

            {/* Stat cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-gray-500">Total Properties</p>
                                <p className="text-2xl font-bold">{loading ? '—' : totalProperties}</p>
                            </div>
                            <Home className="h-8 w-8 text-blue-500 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-gray-500">Active Tenancies</p>
                                <p className="text-2xl font-bold">{loading ? '—' : activeTenancies}</p>
                            </div>
                            <Users className="h-8 w-8 text-green-500 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-gray-500">Scheduled rent /mo</p>
                                <p className="text-2xl font-bold">{loading ? '—' : formatCurrency(monthlyRent)}</p>
                            </div>
                            <TrendingUp className="h-8 w-8 text-purple-500 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-sm text-gray-500">Avg Health Score</p>
                                <p className={`text-2xl font-bold ${healthColor(avgHealth).text}`}>{loading ? '—' : avgHealth}</p>
                            </div>
                            <Activity className="h-8 w-8 text-orange-500 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Property Cards */}
                <div className="lg:col-span-2 space-y-4">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold text-gray-900">Properties</h2>
                        <Link href="/owner-hub/properties">
                            <Button variant="ghost" size="sm">
                                View All <ArrowRight className="ml-1 h-4 w-4"/>
                            </Button>
                        </Link>
                    </div>
                    {loading ? (
                        <div className="space-y-3">
                            {[1, 2, 3].map(i => (
                                <Card key={i}><CardContent className="pt-4 h-24 animate-pulse bg-gray-50"/></Card>
                            ))}
                        </div>
                    ) : properties.length === 0 ? (
                        <Card>
                            <CardContent className="pt-6 text-center py-12">
                                <Building2 className="mx-auto h-12 w-12 text-gray-300 mb-3"/>
                                <p className="text-gray-500">No properties added yet.</p>
                                <Link href="/owner-hub/properties">
                                    <Button className="mt-4" size="sm"><Plus className="mr-2 h-4 w-4"/>Add First
                                        Property</Button>
                                </Link>
                            </CardContent>
                        </Card>
                    ) : (
                        properties.map(prop => {
                            const hc = healthColor(prop.health_score || 0);
                            const daysLeft = prop.lease_end
                                ? Math.max(0, Math.ceil(( new Date(prop.lease_end) - new Date() ) / ( 1000 * 60 * 60 * 24 )))
                                : null;
                            return (
                                <Card key={prop.id} className="hover:shadow-md transition-shadow">
                                    <motion.div whileTap={{scale: 0.98}}>
                                        <CardContent className="pt-4 pb-4">
                                            <div className="flex items-start justify-between gap-4">
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center gap-2 flex-wrap">
                                                        <p className="font-semibold text-gray-900 truncate">{prop.address}</p>
                                                        <StatusBadge status={prop.status}/>
                                                    </div>
                                                    <p className="text-sm text-gray-500 mt-0.5">{prop.suburb}, {prop.state} {prop.postcode}</p>
                                                    <div className="flex items-center gap-4 mt-2 text-sm text-gray-600">
                                                        <span
                                                            className="font-medium">{formatCurrency(prop.weekly_rent)}/wk</span>
                                                        {daysLeft !== null && (
                                                            <span
                                                                className={daysLeft < 30 ? 'text-red-600 font-medium' : ''}>
                              {daysLeft}d until lease end
                            </span>
                                                        )}
                                                        {prop.tenant_name &&
                                                            <span className="truncate">{prop.tenant_name}</span>}
                                                    </div>
                                                </div>
                                                <div className="flex flex-col items-center gap-1 shrink-0">
                                                    <HealthGauge score={prop.health_score || 0}/>
                                                    <span className={`text-xs ${hc.text} font-medium`}>Health</span>
                                                </div>
                                                <Link href={`/owner-hub/properties`}>
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                                                            <Button variant="ghost" size="icon" className="shrink-0"
                                                                    aria-label="View property details">
                                                                <ChevronRight className="h-4 w-4"/>
                                                            </Button>
                                                        </TooltipTrigger>
                                                        <TooltipContent>
                                                            <p>View Details</p>
                                                        </TooltipContent>
                                                    </Tooltip>
                                                </Link>
                                            </div>
                                        </CardContent>
                                    </motion.div>
                                </Card>
                            );
                        })
                    )}
                </div>

                {/* Right column */}
                <div className="space-y-4">
                    {/* Quick Links */}
                    <Card>
                        <CardHeader className="pb-3">
                            <CardTitle className="text-base">Quick Links</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2 pt-0">
                            <Link href="/owner-hub/properties">
                                <Button variant="outline" className="w-full justify-start gap-2">
                                    <Plus className="h-4 w-4"/>Add Property
                                </Button>
                            </Link>
                            <Link href="/owner-hub/ledger">
                                <Button variant="outline" className="w-full justify-start gap-2">
                                    <Users className="h-4 w-4"/>View All Tenancies
                                </Button>
                            </Link>
                            <Link href="/intelligence/building-stress">
                                <Button variant="outline" className="w-full justify-start gap-2">
                                    <AlertTriangle className="h-4 w-4"/>Building Financial Stress
                                </Button>
                            </Link>
                            <Link href="/owner-hub/health-score">
                                <Button variant="outline" className="w-full justify-start gap-2">
                                    <Activity className="h-4 w-4"/>Property Health Score
                                </Button>
                            </Link>
                        </CardContent>
                    </Card>

                    {/* Recent Maintenance */}
                    <Card>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-base">Recent Maintenance</CardTitle>
                                <Link href="/maintenance/tenant">
                                    <Button variant="ghost" size="sm" className="text-xs">View all</Button>
                                </Link>
                            </div>
                        </CardHeader>
                        <CardContent className="pt-0 space-y-3">
                            {loading ? (
                                <p className="text-sm text-gray-400 animate-pulse">Loading…</p>
                            ) : recentMaintenance.length === 0 ? (
                                <p className="text-sm text-gray-400">No recent requests.</p>
                            ) : (
                                recentMaintenance.map(item => (
                                    <div key={item.id} className="flex items-start gap-3">
                                        <Wrench className="h-4 w-4 text-gray-400 mt-0.5 shrink-0"/>
                                        <div className="min-w-0">
                                            <p className="text-sm font-medium truncate">{item.title}</p>
                                            <p className="text-xs text-gray-500">{item.property} · {formatDate(item.created_at)}</p>
                                        </div>
                                        <Badge variant="outline" className="shrink-0 text-xs">{item.status}</Badge>
                                    </div>
                                ))
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
};

export default OwnerHubPage;
