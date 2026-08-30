// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { AlertTriangle, Loader2, RefreshCw, TrendingUp } from 'lucide-react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Label } from '../../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';

const ALLOWED_ROLES = ['owner', 'real_estate_agent', 'super_admin', 'strata_manager', 'ec_member'];

const COMPONENTS = [
    {key: 'income_stability', label: 'Income Stability', weight: 25, color: '#3b82f6'},
    {key: 'expense_volatility', label: 'Expense Volatility', weight: 15, color: '#f59e0b'},
    {key: 'maintenance_backlog', label: 'Maintenance Backlog', weight: 20, color: '#ef4444'},
    {key: 'tenant_stability', label: 'Tenant Stability', weight: 15, color: '#10b981'},
    {key: 'capital_risk', label: 'Capital Risk', weight: 15, color: '#8b5cf6'},
    {key: 'compliance', label: 'Compliance', weight: 10, color: '#06b6d4'},
];
/**
 * @generated FunctionHeader
 * Function: scoreColor
 * Path: frontend/src/pages/dashboardhub/PropertyHealthScorePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function scoreColor(score) {
    if (score >= 75) return {
        text: 'text-green-600',
        ring: '#16a34a',
        label: 'Healthy',
        badge: 'bg-green-100 text-green-800'
    };
    if (score >= 50) return {
        text: 'text-yellow-600',
        ring: '#ca8a04',
        label: 'Moderate',
        badge: 'bg-yellow-100 text-yellow-800'
    };
    if (score >= 30) return {
        text: 'text-orange-600',
        ring: '#ea580c',
        label: 'High Risk',
        badge: 'bg-orange-100 text-orange-800'
    };
    return {text: 'text-red-600', ring: '#dc2626', label: 'Critical', badge: 'bg-red-100 text-red-800'};
}
/**
 * @generated FunctionHeader
 * Function: CircularGauge
 * Path: frontend/src/pages/dashboardhub/PropertyHealthScorePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function CircularGauge({score, size = 180}) {
    const radius = ( size - 20 ) / 2;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - ( Math.min(100, Math.max(0, score)) / 100 ) * circumference;
    const {ring, text} = scoreColor(score);
    return (
        <div className="flex flex-col items-center">
            <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
                <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#e5e7eb" strokeWidth="12"/>
                <circle
                    cx={size / 2} cy={size / 2} r={radius} fill="none"
                    stroke={ring} strokeWidth="12"
                    strokeDasharray={circumference} strokeDashoffset={offset}
                    strokeLinecap="round"
                    transform={`rotate(-90 ${size / 2} ${size / 2})`}
                    style={{transition: 'stroke-dashoffset 0.6s ease'}}
                />
                <text x={size / 2} y={size / 2 - 6} textAnchor="middle" fontSize="36" fontWeight="bold"
                      fill={ring}>{score}</text>
                <text x={size / 2} y={size / 2 + 18} textAnchor="middle" fontSize="12" fill="#6b7280">/100</text>
            </svg>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: PropertyHealthScorePage
 * Path: frontend/src/pages/dashboardhub/PropertyHealthScorePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PropertyHealthScorePage = () => {
    const {user, api} = useAuth();
    const router = useRouter();
    const [properties, setProperties] = useState([]);
    const [selectedId, setSelectedId] = useState('');
    const [healthData, setHealthData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    const canAccess = user && ALLOWED_ROLES.includes(user.role);

    const fetchProperties = useCallback(async () => {
        try {
            const res = await api.get('/owner-hub/properties');
            const props = res.data || [];
            setProperties(props);
            if (props.length > 0) setSelectedId(props[ 0 ].id);
        } catch {
            toast.error('Failed to load properties');
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchHealthData = useCallback(async (id) => {
        if (!id) return;
        setRefreshing(true);
        try {
            const res = await api.get(`/owner-hub/properties/${id}/health-score`);
            const d = res.data;
            if (d) {
                // Normalise backend field names to what this component expects
                setHealthData({
                    score: d.health_score ?? d.score ?? 0,
                    risk_level: d.risk_level,
                    risk_flags: d.risk_flags || [],
                    last_updated: d.computed_at || d.last_updated,
                    history: d.history || [],
                    components: {
                        income_stability: d.income_stability_score ?? d.components?.income_stability ?? 0,
                        expense_volatility: d.expense_volatility_score ?? d.components?.expense_volatility ?? 0,
                        maintenance_backlog: d.maintenance_backlog_score ?? d.components?.maintenance_backlog ?? 0,
                        tenant_stability: d.tenant_stability_score ?? d.components?.tenant_stability ?? 0,
                        capital_risk: d.capital_risk_score ?? d.components?.capital_risk ?? 0,
                        compliance: d.compliance_score ?? d.components?.compliance ?? 0,
                    },
                });
            } else {
                setHealthData(null);
            }
        } catch {
            toast.error('Failed to load health score');
            setHealthData(null);
        } finally {
            setRefreshing(false);
        }
    }, [api]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchProperties();
    }, [canAccess, fetchProperties, router]);

    useEffect(() => {
        if (selectedId) fetchHealthData(selectedId);
    }, [selectedId, fetchHealthData]);

    if (!canAccess) return null;

    const score = healthData?.score ?? 0;
    const colors = scoreColor(score);
    const components = healthData?.components || {};
    const flags = healthData?.risk_flags || [];
    const history = healthData?.history || [];

    return (
        <div className="space-y-6 p-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Property Health Score</h1>
                    <p className="text-sm text-gray-500">AI-powered property risk assessment</p>
                </div>
                <Button variant="outline" size="sm" onClick={() => fetchHealthData(selectedId)} disabled={refreshing}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>Refresh
                </Button>
            </div>

            {/* Property selector */}
            {properties.length > 1 && (
                <div className="flex items-center gap-3">
                    <Label className="shrink-0">Property:</Label>
                    <Select value={selectedId} onValueChange={setSelectedId}>
                        <SelectTrigger className="w-72">
                            <SelectValue placeholder="Select property"/>
                        </SelectTrigger>
                        <SelectContent>
                            {properties.map(p => (
                                <SelectItem key={p.id} value={p.id}>{p.address} — {p.suburb}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            )}

            {loading || refreshing ? (
                <div className="flex justify-center py-20"><Loader2 className="h-10 w-10 animate-spin text-gray-400"/>
                </div>
            ) : !healthData ? (
                <Card><CardContent className="py-16 text-center text-gray-400">No health score data
                    available.</CardContent></Card>
            ) : (
                <>
                    {/* Main score card */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        <Card className="md:col-span-1 flex flex-col items-center justify-center py-8">
                            <CardHeader className="pb-2 text-center">
                                <CardTitle className="text-base text-gray-500">Overall Health Score</CardTitle>
                            </CardHeader>
                            <CardContent className="flex flex-col items-center gap-3">
                                <CircularGauge score={score}/>
                                <span
                                    className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${colors.badge}`}>
                  {colors.label}
                </span>
                                {healthData.last_updated && (
                                    <p className="text-xs text-gray-400">Updated: {new Date(healthData.last_updated).toLocaleDateString('en-AU')}</p>
                                )}
                            </CardContent>
                        </Card>

                        {/* Component breakdown */}
                        <Card className="md:col-span-2">
                            <CardHeader>
                                <CardTitle className="text-base">Score Components</CardTitle>
                                <CardDescription>Weighted contribution to overall score</CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                {COMPONENTS.map(comp => {
                                    const val = components[ comp.key ] ?? 0;
                                    return (
                                        <div key={comp.key}>
                                            <div className="flex items-center justify-between text-sm mb-1">
                                                <span className="font-medium text-gray-700">{comp.label}</span>
                                                <div className="flex items-center gap-2">
                                                    <span className="text-xs text-gray-400">weight {comp.weight}%</span>
                                                    <span className="font-semibold"
                                                          style={{color: comp.color}}>{val}</span>
                                                </div>
                                            </div>
                                            <div className="h-2.5 bg-gray-100 rounded-full overflow-hidden">
                                                <div
                                                    className="h-full rounded-full transition-all duration-500"
                                                    style={{width: `${val}%`, backgroundColor: comp.color}}
                                                />
                                            </div>
                                        </div>
                                    );
                                })}
                            </CardContent>
                        </Card>
                    </div>

                    {/* Risk Flags */}
                    {flags.length > 0 && (
                        <Card className="border-orange-200">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-base flex items-center gap-2 text-orange-700">
                                    <AlertTriangle className="h-4 w-4"/>Risk Flags ({flags.length})
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <ul className="space-y-2">
                                    {flags.map((flag, i) => (
                                        <li key={i} className="flex items-start gap-2 text-sm">
                                            <span
                                                className={`mt-0.5 h-2 w-2 rounded-full shrink-0 ${flag.severity === 'high' ? 'bg-red-500' : flag.severity === 'medium' ? 'bg-orange-500' : 'bg-yellow-500'}`}/>
                                            <span className="text-gray-700">{flag.message || flag}</span>
                                        </li>
                                    ))}
                                </ul>
                            </CardContent>
                        </Card>
                    )}

                    {/* Score History Chart */}
                    {history.length > 1 && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base flex items-center gap-2">
                                    <TrendingUp className="h-4 w-4"/>Score History (Last 8 Weeks)
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={220}>
                                    <LineChart data={history}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0"/>
                                        <XAxis dataKey="week" tick={{fontSize: 11}}/>
                                        <YAxis domain={[0, 100]} tick={{fontSize: 11}}/>
                                        <Tooltip formatter={(v) => [v, 'Score']}/>
                                        <Line
                                            type="monotone" dataKey="score"
                                            stroke={colors.ring} strokeWidth={2.5}
                                            dot={{fill: colors.ring, r: 4}}
                                            activeDot={{r: 6}}
                                        />
                                    </LineChart>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>
                    )}
                </>
            )}
        </div>
    );
};

export default PropertyHealthScorePage;
