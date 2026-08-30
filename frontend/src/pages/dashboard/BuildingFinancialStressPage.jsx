// @ts-nocheck
// @featuretrace:financial-health-score — building financial-stress snapshot, timeline, mitigations, and per-unit impact.
// Layer: frontend
// Data flow: BuildingFinancialStressPage → GET /building-stress/{snapshot,timeline,mitigation-options},
//            /building-stress/unit-impact/{unit} → building_summaries + unit_levy_ledger (building-scoped).
// Related: backend/routers/building_stress.py
//           frontend/src/hooks/useActiveUnit.ts (the unit whose impact is shown)
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { AlertTriangle, DollarSign, Loader2, RefreshCw, Shield, TrendingDown } from 'lucide-react';
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis
} from 'recharts';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '../../components/ui/accordion';
import { formatCurrency } from '../../lib/utils';

const ALLOWED_ROLES = ['owner', 'ec_member', 'strata_manager', 'super_admin'];
/**
 * @generated FunctionHeader
 * Function: stressColor
 * Path: frontend/src/pages/dashboard/BuildingFinancialStressPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function stressColor(score) {
    if (score <= 25) return {
        text: 'text-green-600',
        ring: '#16a34a',
        label: 'Low Risk',
        badge: 'bg-green-100 text-green-800'
    };
    if (score <= 50) return {
        text: 'text-yellow-600',
        ring: '#ca8a04',
        label: 'Moderate',
        badge: 'bg-yellow-100 text-yellow-800'
    };
    if (score <= 75) return {
        text: 'text-orange-600',
        ring: '#ea580c',
        label: 'High Risk',
        badge: 'bg-orange-100 text-orange-800'
    };
    return {text: 'text-red-600', ring: '#dc2626', label: 'Critical', badge: 'bg-red-100 text-red-800'};
}
/**
 * @generated FunctionHeader
 * Function: StressGauge
 * Path: frontend/src/pages/dashboard/BuildingFinancialStressPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StressGauge({score, size = 200}) {
    const radius = ( size - 20 ) / 2;
    const circumference = 2 * Math.PI * radius;
    const capped = Math.min(100, Math.max(0, score));
    const offset = circumference - ( capped / 100 ) * circumference;
    const {ring, text} = stressColor(score);
    return (
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
            <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#e5e7eb" strokeWidth="14"/>
            <circle
                cx={size / 2} cy={size / 2} r={radius} fill="none"
                stroke={ring} strokeWidth="14"
                strokeDasharray={circumference} strokeDashoffset={offset}
                strokeLinecap="round"
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
                style={{transition: 'stroke-dashoffset 0.6s ease'}}
            />
            <text x={size / 2} y={size / 2 - 8} textAnchor="middle" fontSize="38" fontWeight="bold"
                  fill={ring}>{score}</text>
            <text x={size / 2} y={size / 2 + 16} textAnchor="middle" fontSize="13" fill="#6b7280">Risk Score</text>
        </svg>
    );
}

const RISK_COMPONENTS = [
    {key: 'reserve_gap_score', label: 'Reserve Gap', color: '#ef4444'},
    {key: 'expense_growth_score', label: 'Expense Growth', color: '#f59e0b'},
    {key: 'levy_collection_score', label: 'Levy Arrears', color: '#8b5cf6'},
    {key: 'maintenance_backlog_score', label: 'Maintenance Backlog', color: '#ec4899'},
    {key: 'capital_age_score', label: 'Capital Age', color: '#06b6d4'},
];
/**
 * @generated FunctionHeader
 * Function: BuildingFinancialStressPage
 * Path: frontend/src/pages/dashboard/BuildingFinancialStressPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BuildingFinancialStressPage = () => {
    const {user, api} = useAuth();
    const {activeUnit} = useActiveUnit();
    const router = useRouter();
    const [snapshot, setSnapshot] = useState(null);
    const [timeline, setTimeline] = useState([]);
    const [unitImpact, setUnitImpact] = useState(null);
    const [mitigations, setMitigations] = useState([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    const canAccess = user && ALLOWED_ROLES.includes(user.effective_role || user.role);

    const fetchAll = useCallback(async () => {
        setRefreshing(true);
        try {
            const reqs = [
                api.get('/building-stress/snapshot'),
                api.get('/building-stress/timeline'),
                api.get('/building-stress/mitigation-options'),
            ];
            const unitNum = activeUnit;
            if (unitNum) reqs.push(api.get(`/building-stress/unit-impact/${unitNum}`));

            const results = await Promise.allSettled(reqs);
            if (results[ 0 ].status === 'fulfilled') setSnapshot(results[ 0 ].value.data);
            if (results[ 1 ].status === 'fulfilled') setTimeline(results[ 1 ].value.data || []);
            if (results[ 2 ].status === 'fulfilled') setMitigations(results[ 2 ].value.data || []);
            if (results[ 3 ]?.status === 'fulfilled') setUnitImpact(results[ 3 ].value.data);
        } catch {
            toast.error('Failed to load building stress data');
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [api, activeUnit]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchAll();
    }, [canAccess, fetchAll, router]);

    if (!canAccess) return null;

    const riskScore = snapshot?.overall_risk_score ?? 0;
    const colors = stressColor(riskScore);
    const deficit = snapshot?.reserve_adequacy?.deficit ?? 0;
    const riskComponents = snapshot?.risk_components || {};
    const monthlyHistory = snapshot?.monthly_history || [];

    const componentChartData = RISK_COMPONENTS.map(c => ( {
        name: c.label,
        value: riskComponents[ c.key ] ?? 0,
        color: c.color,
    } ));

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-foreground">Building Financial Stress</h1>
                    <p className="text-sm text-muted-foreground">Early warning system for reserve fund health</p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchAll} disabled={refreshing}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>Refresh
                </Button>
            </div>

            {loading ? (
                <div className="flex justify-center py-24"><Loader2 className="h-10 w-10 animate-spin text-muted-foreground"/>
                </div>
            ) : !snapshot ? (
                <Card><CardContent className="py-12 text-center text-muted-foreground">No stress data
                    available.</CardContent></Card>
            ) : (
                <>
                    {/* Deficit Alert */}
                    {deficit > 0 && (
                        <div className="flex items-start gap-3 rounded-lg p-4 bg-red-50 border border-red-200">
                            <AlertTriangle className="h-5 w-5 text-red-600 shrink-0 mt-0.5"/>
                            <div>
                                <p className="font-semibold text-red-800">Reserve Deficit Detected</p>
                                <p className="text-sm text-red-700">
                                    The sinking fund is projected to go into deficit
                                    by <strong>{formatCurrency(deficit)}</strong>.
                                    Immediate action may be required.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Hero + Metrics */}
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <Card className="md:col-span-1 flex flex-col items-center justify-center py-6">
                            <StressGauge score={riskScore}/>
                            <span
                                className={`mt-3 inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${colors.badge}`}>
                {colors.label}
              </span>
                        </Card>
                        <Card>
                            <CardContent className="pt-6">
                                <p className="text-xs text-muted-foreground mb-1">Reserve Adequacy Ratio</p>
                                <p className="text-2xl font-bold">{snapshot.reserve_adequacy?.reserve_adequacy_ratio != null ? ( snapshot.reserve_adequacy.reserve_adequacy_ratio * 100 ).toFixed(1) : '—'}%</p>
                                <p className="text-xs text-muted-foreground mt-1">Target: ≥ 100%</p>
                            </CardContent>
                        </Card>
                        <Card>
                            <CardContent className="pt-6">
                                <p className="text-xs text-muted-foreground mb-1">Sinking Fund Balance</p>
                                <p className="text-2xl font-bold">{snapshot.reserve_adequacy?.sinking_fund_balance != null ? formatCurrency(snapshot.reserve_adequacy.sinking_fund_balance) : '—'}</p>
                                <p className="text-xs text-muted-foreground mt-1">As at today</p>
                            </CardContent>
                        </Card>
                        <Card>
                            <CardContent className="pt-6">
                                <p className="text-xs text-muted-foreground mb-1">10-Year Capital Forecast</p>
                                <p className="text-2xl font-bold">{snapshot.reserve_adequacy?.ten_year_capital_forecast != null ? formatCurrency(snapshot.reserve_adequacy.ten_year_capital_forecast) : '—'}</p>
                                <p className="text-xs text-muted-foreground mt-1">Projected spend</p>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Reserve Timeline Chart */}
                    {timeline.length > 0 && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base flex items-center gap-2">
                                    <TrendingDown className="h-4 w-4"/>Reserve Fund Timeline
                                </CardTitle>
                                <CardDescription className="text-xs">Red zone = projected deficit</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={260}>
                                    <AreaChart data={timeline}>
                                        <defs>
                                            <linearGradient id="balanceGrad" x1="0" y1="0" x2="0" y2="1">
                                                <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                                                <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.05}/>
                                            </linearGradient>
                                            <linearGradient id="deficitGrad" x1="0" y1="0" x2="0" y2="1">
                                                <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3}/>
                                                <stop offset="95%" stopColor="#ef4444" stopOpacity={0.05}/>
                                            </linearGradient>
                                        </defs>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0"/>
                                        <XAxis dataKey="year" tick={{fontSize: 11}}/>
                                        <YAxis tickFormatter={v => `$${( v / 1000 ).toFixed(0)}k`}
                                               tick={{fontSize: 11}}/>
                                        <Tooltip formatter={(v) => [formatCurrency(v), 'Balance']}/>
                                        <ReferenceLine y={0} stroke="#ef4444" strokeDasharray="4 2"
                                                       label={{value: '$0', fill: '#ef4444', fontSize: 11}}/>
                                        <Area type="monotone" dataKey="projected_balance" stroke="#3b82f6"
                                              fill="url(#balanceGrad)" strokeWidth={2}/>
                                    </AreaChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>
                    )}

                    {/* Risk components bar chart */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Risk Component Breakdown</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <ResponsiveContainer width="100%" height={200}>
                                <BarChart data={componentChartData} layout="vertical">
                                    <CartesianGrid strokeDasharray="3 3" horizontal={false}/>
                                    <XAxis type="number" domain={[0, 100]} tick={{fontSize: 11}}/>
                                    <YAxis type="category" dataKey="name" width={160} tick={{fontSize: 11}}/>
                                    <Tooltip formatter={(v) => [v, 'Score']}/>
                                    <Bar dataKey="value" radius={[0, 4, 4, 0]}>
                                        {componentChartData.map((entry, idx) => (
                                            <Cell key={idx} fill={entry.color}/>
                                        ))}
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </CardContent>
                    </Card>

                    {/* Unit Impact */}
                    {unitImpact && (
                        <Card className="border-amber-200 bg-amber-50">
                            <CardContent className="pt-6">
                                <div className="flex items-start gap-3">
                                    <DollarSign className="h-6 w-6 text-amber-600 shrink-0"/>
                                    <div>
                                        <p className="font-semibold text-amber-900">Your Estimated Impact
                                            (Unit {activeUnit})</p>
                                        <p className="text-amber-800 mt-1">
                                            Estimated special
                                            levy: <strong>{formatCurrency(unitImpact.projected_special_levy ?? 0)}</strong>
                                            {unitImpact.estimated_year && <span> by {unitImpact.estimated_year}</span>}
                                        </p>
                                        {unitImpact.probability_pct != null && (
                                            <p className="text-xs text-amber-700 mt-1">Probability of special
                                                levy: {unitImpact.probability_pct}%</p>
                                        )}
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    )}

                    {/* Mitigation Options */}
                    {mitigations.length > 0 && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base flex items-center gap-2">
                                    <Shield className="h-4 w-4"/>Mitigation Options
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <Accordion type="multiple" className="space-y-2">
                                    {mitigations.map((opt, i) => (
                                        <AccordionItem key={i} value={`opt-${i}`} className="border rounded-lg px-4">
                                            <AccordionTrigger className="text-sm font-medium py-3">
                                                {opt.title}
                                                {( opt.annual_cost_per_unit ?? opt.total_building_cost ) && (
                                                    <Badge variant="outline" className="ml-auto mr-4 text-xs">
                                                        {opt.annual_cost_per_unit ? `${formatCurrency(opt.annual_cost_per_unit)}/unit/yr` : formatCurrency(opt.total_building_cost)}
                                                    </Badge>
                                                )}
                                            </AccordionTrigger>
                                            <AccordionContent className="text-sm text-muted-foreground pb-3">
                                                {opt.description}
                                            </AccordionContent>
                                        </AccordionItem>
                                    ))}
                                </Accordion>
                            </CardContent>
                        </Card>
                    )}

                    {/* Monthly Stress History */}
                    {monthlyHistory.length > 1 && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base">Stress Score Trend (Last 6 Months)</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={180}>
                                    <AreaChart data={monthlyHistory}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0"/>
                                        <XAxis dataKey="month" tick={{fontSize: 11}}/>
                                        <YAxis domain={[0, 100]} tick={{fontSize: 11}}/>
                                        <Tooltip formatter={(v) => [v, 'Stress Score']}/>
                                        <Area type="monotone" dataKey="score" stroke="#ef4444" fill="#fef2f2"
                                              strokeWidth={2}/>
                                    </AreaChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>
                    )}
                </>
            )}
        </div>
    );
};

export default BuildingFinancialStressPage;
