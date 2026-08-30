import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../components/ui/table';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../../components/ui/tooltip';
import {
    AlertTriangle,
    Building2,
    CheckCircle2,
    ExternalLink,
    Info,
    Loader2,
    Plus,
    RefreshCw,
    RotateCcw,
} from 'lucide-react';
import { toast } from 'sonner';
/** Wrap a column header with an info tooltip */
function ColHeader({label, tip}) {
    return (
        <div className="flex items-center gap-1">
            {label}
            <Tooltip>
                <TooltipTrigger asChild>
                    <Info className="h-3.5 w-3.5 text-muted-foreground cursor-help"/>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-xs text-xs">
                    {tip}
                </TooltipContent>
            </Tooltip>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: healthColor
 * Path: frontend/src/pages/dashboard/PortfolioDashboardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const healthColor = (score) => {
    if (score == null) return 'text-muted-foreground';
    if (score >= 75) return 'text-green-600';
    if (score >= 50) return 'text-yellow-600';
    return 'text-red-600';
};
/**
 * @generated FunctionHeader
 * Function: healthLabel
 * Path: frontend/src/pages/dashboard/PortfolioDashboardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const healthLabel = (score) => {
    if (score == null) return 'No data';
    if (score >= 91) return 'Grade A';
    if (score >= 76) return 'Grade B';
    if (score >= 51) return 'Grade C';
    return 'Grade D';
};
/**
 * @generated FunctionHeader
 * Function: PortfolioDashboardPage
 * Path: frontend/src/pages/dashboard/PortfolioDashboardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function PortfolioDashboardPage() {
    const {api} = useAuth();
    const router = useRouter();
    const [dashboardData, setDashboardData] = useState(null);
    const [buildings, setBuildings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [recomputing, setRecomputing] = useState(false);
    const [error, setError] = useState(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [dashRes, bldRes] = await Promise.allSettled([
                api.get('/portfolio/dashboard'),
                api.get('/portfolio/buildings'),
            ]);
            if (dashRes.status === 'fulfilled') setDashboardData(dashRes.value.data);
            if (bldRes.status === 'fulfilled') setBuildings(bldRes.value.data.buildings || []);
            if (dashRes.status === 'rejected')
                setError(dashRes.reason?.response?.data?.detail || 'Failed to load dashboard');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        load();
    }, [load]);
    /** Trigger a recompute of building_summaries for the current building */
    const recompute = async () => {
        setRecomputing(true);
        try {
            await api.post('/community-dashboard/recompute');
            toast.success('Building metrics recomputed successfully');
            await load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Recompute failed');
        } finally {
            setRecomputing(false);
        }
    };

    const summary = dashboardData?.summary || {};
    const alerts = dashboardData?.alerts || [];

    // Determine last-updated timestamp from buildings data
    const lastUpdated = buildings
        .map((b) => b.last_computed_at)
        .filter(Boolean)
        .sort()
        .at(-1);

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
            </div>
        );
    }

    return (
        <TooltipProvider>
            <div className="p-6 space-y-6">
                {/* Header */}
                <div className="flex items-center justify-between flex-wrap gap-3">
                    <div>
                        <h1 className="text-2xl font-bold">Portfolio Dashboard</h1>
                        <p className="text-muted-foreground text-sm">
                            Multi-building command centre — metrics across all managed strata buildings
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={recompute}
                                    disabled={recomputing || loading}
                                >
                                    {recomputing ? (
                                        <Loader2 className="h-4 w-4 animate-spin mr-1"/>
                                    ) : (
                                        <RotateCcw className="h-4 w-4 mr-1"/>
                                    )}
                                    Recompute
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent className="max-w-xs text-xs">
                                Recalculate health scores, arrears rates, and work order counts
                                for the current building. Portfolio data refreshes automatically
                                at 02:00 AEST each day.
                            </TooltipContent>
                        </Tooltip>
                        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
                            <RefreshCw className="h-4 w-4 mr-1"/>
                            Refresh
                        </Button>
                        <Button size="sm" asChild>
                            <a href="/admin/portfolio/onboard">
                                <Plus className="h-4 w-4 mr-1"/>
                                Full Onboarding Wizard
                            </a>
                        </Button>
                    </div>
                </div>

                {/* Info banner */}
                {lastUpdated && (
                    <div
                        className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/40 rounded px-3 py-2">
                        <Info className="h-3.5 w-3.5 flex-shrink-0"/>
                        Portfolio metrics are refreshed daily at 02:00 AEST.
                        Last computed: {new Date(lastUpdated).toLocaleString('en-AU', {
                        dateStyle: 'medium',
                        timeStyle: 'short'
                    })}
                    </div>
                )}

                {!lastUpdated && buildings.length > 0 && (
                    <Alert>
                        <AlertTriangle className="h-4 w-4"/>
                        <AlertDescription>
                            Metrics not yet computed for some buildings. Click{' '}
                            <button onClick={recompute} className="font-medium underline">
                                Recompute
                            </button>
                            {' '}
                            to generate health scores, arrears rates, and work order counts.
                        </AlertDescription>
                    </Alert>
                )}

                {error && (
                    <Alert variant="destructive">
                        <AlertTriangle className="h-4 w-4"/>
                        <AlertDescription>{error}</AlertDescription>
                    </Alert>
                )}

                {/* Summary cards */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Card className="cursor-default">
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-sm text-muted-foreground">Buildings</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <p className="text-3xl font-bold">{summary.total_buildings ?? '—'}</p>
                                </CardContent>
                            </Card>
                        </TooltipTrigger>
                        <TooltipContent className="text-xs">
                            Total active strata buildings in this portfolio
                        </TooltipContent>
                    </Tooltip>

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Card className="cursor-default">
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-sm text-muted-foreground">Total Lots</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <p className="text-3xl font-bold">{summary.total_lots ?? '—'}</p>
                                </CardContent>
                            </Card>
                        </TooltipTrigger>
                        <TooltipContent className="text-xs">
                            Sum of all strata lots (units) across every building
                        </TooltipContent>
                    </Tooltip>

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Card className="cursor-default">
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-sm text-muted-foreground">Avg Health</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <p className={`text-3xl font-bold ${healthColor(summary.avg_health_score ?? null)}`}>
                                        {summary.avg_health_score ?? '—'}
                                    </p>
                                </CardContent>
                            </Card>
                        </TooltipTrigger>
                        <TooltipContent className="text-xs max-w-xs">
                            Average building health score (0–100). A≥91, B≥76, C≥51, D&lt;51.
                            Calculated from sinking fund adequacy, arrears rate, maintenance
                            work orders, and compliance status.
                        </TooltipContent>
                    </Tooltip>

                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Card className="cursor-default">
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-sm text-muted-foreground">Alerts</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <p className={`text-3xl font-bold ${alerts.length > 0 ? 'text-red-600' : 'text-green-600'}`}>
                                        {alerts.length}
                                    </p>
                                </CardContent>
                            </Card>
                        </TooltipTrigger>
                        <TooltipContent className="text-xs">
                            Buildings with health score below 50 (low health) or arrears
                            rate above 10% (high arrears) trigger an alert
                        </TooltipContent>
                    </Tooltip>
                </div>

                {/* Alerts */}
                {alerts.length > 0 && (
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-red-600">
                                <AlertTriangle className="h-5 w-5"/>
                                Portfolio Alerts
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {alerts.map((alert, idx) => {
                                const bldName = buildings.find((b) => b.building_id === alert.building_id)?.name
                                    || `Building ${alert.building_id}`;
                                return (
                                    <div key={idx} className="flex items-center gap-2 text-sm">
                                        <Badge variant="destructive">
                                            {alert.type === 'low_health' ? 'Low Health' : 'High Arrears'}
                                        </Badge>
                                        <span>
                      {bldName}:{' '}
                                            {alert.type === 'low_health'
                                                ? `Health score ${alert.value}`
                                                : `${alert.value}% arrears rate`}
                    </span>
                                    </div>
                                );
                            })}
                        </CardContent>
                    </Card>
                )}

                {/* Buildings table */}
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Building2 className="h-5 w-5"/>
                            Buildings
                            <span className="text-sm font-normal text-muted-foreground ml-1">
                — click a row to view building details
              </span>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Building</TableHead>
                                    <TableHead>
                                        <ColHeader label="Lots" tip="Total strata lots in this building"/>
                                    </TableHead>
                                    <TableHead>
                                        <ColHeader
                                            label="Health"
                                            tip="Overall building health score 0–100. A≥91, B≥76, C≥51, D<51. Factors: sinking fund adequacy, arrears rate, open work orders, compliance."
                                        />
                                    </TableHead>
                                    <TableHead>
                                        <ColHeader
                                            label="Arrears %"
                                            tip="Percentage of lots with outstanding levy payments beyond their due date"
                                        />
                                    </TableHead>
                                    <TableHead>
                                        <ColHeader
                                            label="Open WOs"
                                            tip="Count of open or in-progress maintenance work orders"
                                        />
                                    </TableHead>
                                    <TableHead>
                                        <ColHeader
                                            label="Next Compliance"
                                            tip="The next scheduled compliance deadline (e.g. insurance renewal, fire safety inspection)"
                                        />
                                    </TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead></TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {buildings.length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                                            No buildings found
                                        </TableCell>
                                    </TableRow>
                                )}
                                {buildings.map((bld) => {
                                    const bid = bld.building_id || bld.id;
                                    const noData = !bld.has_data && bld.health_score == null;
                                    return (
                                        <TableRow
                                            key={bid}
                                            className="cursor-pointer hover:bg-muted/50"
                                            onClick={() => router.push(`/admin/buildings/${bid}`)}
                                            data-testid={`building-row-${bid}`}
                                        >
                                            <TableCell className="font-medium">{bld.name}</TableCell>
                                            <TableCell>{bld.lot_count ?? '—'}</TableCell>
                                            <TableCell>
                                                {noData ? (
                                                    <span className="text-muted-foreground text-sm">—</span>
                                                ) : (
                                                    <Tooltip>
                                                        <TooltipTrigger asChild>
                              <span className={`font-semibold ${healthColor(bld.health_score)}`}>
                                {bld.health_score ?? '—'}
                              </span>
                                                        </TooltipTrigger>
                                                        <TooltipContent className="text-xs">
                                                            {healthLabel(bld.health_score)}
                                                        </TooltipContent>
                                                    </Tooltip>
                                                )}
                                            </TableCell>
                                            <TableCell>
                                                {noData ? '—' : bld.arrears_rate != null ? `${bld.arrears_rate.toFixed(1)}%` : '—'}
                                            </TableCell>
                                            <TableCell>{noData ? '—' : ( bld.open_work_orders ?? '—' )}</TableCell>
                                            <TableCell className="text-sm text-muted-foreground">
                                                {bld.next_compliance_item || '—'}
                                            </TableCell>
                                            <TableCell>
                                                {noData ? (
                                                    <Badge variant="outline"
                                                           className="text-muted-foreground border-muted-foreground">
                                                        <Info className="h-3 w-3 mr-1"/> No data
                                                    </Badge>
                                                ) : ( bld.health_score ?? 100 ) >= 75 ? (
                                                    <Badge variant="outline"
                                                           className="text-green-600 border-green-600">
                                                        <CheckCircle2 className="h-3 w-3 mr-1"/> Healthy
                                                    </Badge>
                                                ) : (
                                                    <Badge variant="outline"
                                                           className="text-yellow-600 border-yellow-600">
                                                        <AlertTriangle className="h-3 w-3 mr-1"/> Attention
                                                    </Badge>
                                                )}
                                            </TableCell>
                                            <TableCell>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="h-7 px-2 text-xs gap-1"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        router.push(`/admin/buildings/${bid}`);
                                                    }}
                                                >
                                                    <ExternalLink className="h-3.5 w-3.5"/>
                                                    View
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            </div>
        </TooltipProvider>
    );
}
