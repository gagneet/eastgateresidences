// @featuretrace:occupancy_intelligence — Main page for Building Occupancy Intelligence
// Layer: frontend
// Data flow: OccupancyIntelligencePage → /api/occupancy/* → occupancy_status (building-scoped)
// Related: backend/routers/occupancy.py
//           backend/services/occupancy_service.py
//           frontend/src/components/occupancy/OccupancyPieChart.tsx
//           frontend/src/components/occupancy/OccupancyTrendChart.tsx
//           frontend/src/components/occupancy/OccupancyHeatmap.tsx

import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { AlertCircle, Building2, Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import OccupancyPieChart from '../../components/occupancy/OccupancyPieChart';
import OccupancyTrendChart from '../../components/occupancy/OccupancyTrendChart';
import OccupancyHeatmap from '../../components/occupancy/OccupancyHeatmap';
import {PageHeader} from "../../components/shared/PageHeader";

const ADMIN_ROLES = ['super_admin', 'strata_manager'];
const VIEW_ROLES = [...ADMIN_ROLES, 'ec_member'];
/**
 * @generated FunctionHeader
 * Function: ConfidenceBadge
 * Path: frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ConfidenceBadge({value}) {
    const pct = Math.round(value * 100);
    if (pct >= 85) return <Badge className="bg-green-100 text-green-700 border-green-200">{pct}% confident</Badge>;
    if (pct >= 65) return <Badge className="bg-amber-100 text-amber-700 border-amber-200">{pct}% confident</Badge>;
    return <Badge className="bg-muted text-muted-foreground border-border">{pct}% confident</Badge>;
}
/**
 * @generated FunctionHeader
 * Function: StatCard
 * Path: frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatCard({label, value, sub, colorClass = 'text-foreground'}) {
    return (
        <Card>
            <CardContent className="pt-5 pb-4">
                <p className="text-xs text-muted-foreground uppercase tracking-wide">{label}</p>
                <p className={`text-3xl font-extrabold mt-1 ${colorClass}`}>{value}</p>
                {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: OccupancyIntelligencePage
 * Path: frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function OccupancyIntelligencePage() {
    const {user, api} = useAuth();

    const [summary, setSummary] = useState(null);
    const [lots, setLots] = useState([]);
    const [trends, setTrends] = useState([]);
    const [loading, setLoading] = useState(true);
    const [recomputing, setRecomputing] = useState(false);
    const [error, setError] = useState(null);

    const effectiveRole = user?.effective_role || user?.role || '';
    const canView = VIEW_ROLES.includes(effectiveRole) || user?.is_elevated || user?.temp_elevation;
    const canRecompute = ADMIN_ROLES.includes(effectiveRole) || user?.role === 'super_admin';

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [summaryRes, lotsRes, trendsRes] = await Promise.all([
                api.get('/occupancy/summary'),
                api.get('/occupancy/lots'),
                api.get('/occupancy/trends'),
            ]);
            setSummary(summaryRes.data);
            setLots(lotsRes.data || []);
            setTrends(trendsRes.data?.trend || []);
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to load occupancy data';
            setError(msg);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        loadData();
    }, [loadData]);
    /**
     * @generated FunctionHeader
     * Function: handleRecompute
     * Path: frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRecompute = async () => {
        setRecomputing(true);
        try {
            await api.post('/occupancy/recompute');
            toast.success('Occupancy recomputed successfully');
            await loadData();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Recompute failed');
        } finally {
            setRecomputing(false);
        }
    };

    if (!canView) {
        return (
            <div className="p-6 max-w-lg mx-auto">
                <Card className="border-dashed border-destructive/50 bg-destructive/5">
                    <CardContent className="pt-6 text-center space-y-2">
                        <AlertCircle className="h-8 w-8 text-destructive mx-auto"/>
                        <p className="font-semibold">Access Restricted</p>
                        <p className="text-sm text-muted-foreground">
                            Occupancy Intelligence is available to EC members, Chairman, and Strata Manager.
                        </p>
                    </CardContent>
                </Card>
            </div>
        );
    }

    return (
        <div className="p-6 max-w-6xl mx-auto space-y-6" data-testid="occupancy-intelligence-page">
            {/* ── Header ── */}
            <PageHeader
                title="Building Occupancy Intelligence"
                icon={<Building2 className="h-5 w-5"/>}
                description="Classify each lot as tenant, owner-occupied, or investor-unknown"
                actions={
                    canRecompute && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleRecompute}
                            disabled={recomputing || loading}
                            data-testid="recompute-btn"
                        >
                            {recomputing
                                ? <Loader2 className="h-4 w-4 mr-2 animate-spin"/>
                                : <RefreshCw className="h-4 w-4 mr-2"/>
                            }
                            Recompute
                        </Button>
                    )
                }
            />

            {/* ── Error ── */}
            {error && (
                <Card className="border-amber-200 bg-amber-50">
                    <CardContent className="pt-4 flex items-center gap-2 text-amber-800 text-sm">
                        <AlertCircle className="h-4 w-4 flex-shrink-0"/>
                        {error}
                    </CardContent>
                </Card>
            )}

            {/* ── Loading ── */}
            {loading ? (
                <div className="flex justify-center py-20">
                    <Loader2 className="h-10 w-10 animate-spin text-muted-foreground"/>
                </div>
            ) : (
                <>
                    {/* ── Summary stat cards ── */}
                    {summary && (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <StatCard
                                label="Total Lots"
                                value={summary.total_lots}
                                sub="classified"
                            />
                            <StatCard
                                label="Tenants"
                                value={summary.tenant_count}
                                sub={`${summary.tenant_pct}% of building`}
                                colorClass="text-red-600"
                            />
                            <StatCard
                                label="Owner-Occupied"
                                value={summary.owner_occupied_count}
                                sub={`${summary.owner_occupied_pct}% of building`}
                                colorClass="text-green-600"
                            />
                            <StatCard
                                label="Investor / Unknown"
                                value={summary.investor_unknown_count}
                                sub={`${summary.investor_unknown_pct}% of building`}
                                colorClass="text-muted-foreground"
                            />
                        </div>
                    )}

                    {/* ── Confidence + last computed ── */}
                    {summary && (
                        <div className="flex items-center gap-3 flex-wrap">
                            <ConfidenceBadge value={summary.avg_confidence}/>
                            {summary.last_computed_at && (
                                <span className="text-xs text-muted-foreground">
                                    Last computed:{' '}
                                    {new Date(summary.last_computed_at).toLocaleString('en-AU', {
                                        dateStyle: 'medium',
                                        timeStyle: 'short',
                                    })}
                                </span>
                            )}
                            {!summary.total_lots && (
                                <span className="text-xs text-amber-600 font-medium">
                                    No data yet — click Recompute to classify lots.
                                </span>
                            )}
                        </div>
                    )}

                    {/* ── Pie + Trend side-by-side ── */}
                    {summary && (
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <OccupancyPieChart
                                tenantCount={summary.tenant_count}
                                ownerOccupiedCount={summary.owner_occupied_count}
                                investorUnknownCount={summary.investor_unknown_count}
                                totalLots={summary.total_lots}
                            />
                            <OccupancyTrendChart trend={trends}/>
                        </div>
                    )}

                    {/* ── Heatmap ── */}
                    {lots.length > 0 && <OccupancyHeatmap lots={lots}/>}

                    {/* ── Empty state ── */}
                    {!loading && !error && !summary?.total_lots && (
                        <Card className="border-dashed">
                            <CardContent className="pt-8 pb-8 text-center space-y-3">
                                <Building2 className="h-10 w-10 mx-auto text-muted-foreground opacity-40"/>
                                <p className="font-semibold text-muted-foreground">No occupancy data yet</p>
                                <p className="text-sm text-muted-foreground max-w-sm mx-auto">
                                    Run the recompute engine to classify all lots using rental certificates
                                    and owner address data.
                                </p>
                                {canRecompute && (
                                    <Button onClick={handleRecompute} disabled={recomputing} size="sm">
                                        {recomputing
                                            ? <Loader2 className="h-4 w-4 mr-2 animate-spin"/>
                                            : <RefreshCw className="h-4 w-4 mr-2"/>
                                        }
                                        Run Now
                                    </Button>
                                )}
                            </CardContent>
                        </Card>
                    )}
                </>
            )}
        </div>
    );
}
