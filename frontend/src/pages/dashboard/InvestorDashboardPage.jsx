// @ts-nocheck
// @featuretrace:levy-fairness — investor view: per-unit investment grade, subsidy map, true cost, PDF report.
// Layer: frontend
// Data flow: InvestorDashboardPage → GET /units, /intelligence/investor/summary/{unit},
//            /intelligence/levy-fairness/subsidy-map, /intelligence/investor/building-health-report
//            → units + unit_levy_ledger (building-scoped).
// Related: backend/routers/intelligence.py
//           frontend/src/hooks/useActiveUnit.ts (seeds the non-admin unit selection)
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { AlertTriangle, BarChart2, CheckCircle, DollarSign, Info, Loader2, RefreshCw, TrendingUp } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';

const ALLOWED_ROLES = ['super_admin', 'strata_manager', 'ec_member'];
/**
 * @generated FunctionHeader
 * Function: gradeConfig
 * Path: frontend/src/pages/dashboard/InvestorDashboardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function gradeConfig(grade) {
    switch (grade) {
        case 'A':
            return {
                bg: 'bg-green-50',
                border: 'border-green-200',
                badge: 'bg-green-100 text-green-800',
                ring: '#16a34a',
                label: 'Prime Investment'
            };
        case 'B':
            return {
                bg: 'bg-primary/10',
                border: 'border-primary/20',
                badge: 'bg-primary/10 text-primary',
                ring: '#2563eb',
                label: 'Good Investment'
            };
        case 'C':
            return {
                bg: 'bg-amber-50',
                border: 'border-amber-200',
                badge: 'bg-amber-100 text-amber-800',
                ring: '#d97706',
                label: 'Moderate Risk'
            };
        case 'D':
            return {
                bg: 'bg-red-50',
                border: 'border-red-200',
                badge: 'bg-red-100 text-red-800',
                ring: '#dc2626',
                label: 'High Risk'
            };
        default:
            return {
                bg: 'bg-muted',
                border: 'border-border',
                badge: 'bg-muted text-foreground',
                ring: '#9ca3af',
                label: 'Unrated'
            };
    }
}
/**
 * @generated FunctionHeader
 * Function: MetricBar
 * Path: frontend/src/pages/dashboard/InvestorDashboardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function MetricBar({label, value, max = 100, colorClass = 'bg-primary', suffix = '%'}) {
    const pct = Math.min(100, Math.max(0, ( value / max ) * 100));
    return (
        <div>
            <div className="flex justify-between text-sm mb-1">
                <span className="text-muted-foreground">{label}</span>
                <span
                    className="font-semibold text-foreground">{value != null ? `${value.toFixed(1)}${suffix}` : '—'}</span>
            </div>
            <div className="h-2 rounded-full bg-muted overflow-hidden">
                <div className={`h-full rounded-full ${colorClass}`}
                     style={{width: `${pct}%`, transition: 'width 0.5s ease'}}/>
            </div>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: GradeBadge
 * Path: frontend/src/pages/dashboard/InvestorDashboardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function GradeBadge({grade}) {
    const cfg = gradeConfig(grade);
    return (
        <div className={`flex flex-col items-center justify-center rounded-xl p-6 ${cfg.bg} border ${cfg.border}`}>
            <span className="text-6xl font-semibold" style={{color: cfg.ring}}>{grade ?? '—'}</span>
            <span className={`mt-2 inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${cfg.badge}`}>
        {cfg.label}
      </span>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: InvestorDashboardPage
 * Path: frontend/src/pages/dashboard/InvestorDashboardPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InvestorDashboardPage = () => {
    const {user, api} = useAuth();
    const {activeUnit} = useActiveUnit();
    const router = useRouter();

    const [selectedUnit, setSelectedUnit] = useState(null);
    const [units, setUnits] = useState([]);
    const [gradeData, setGradeData] = useState(null);
    const [subsidyData, setSubsidyData] = useState(null);
    const [trueCostData, setTrueCostData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [dataError, setDataError] = useState(null);
    const [downloadingReport, setDownloadingReport] = useState(false);

    // ec_member is also treated as admin for the purpose of the unit selector
    // so they can view all units, not just their own.
    const isAdmin = user && ['super_admin', 'strata_manager', 'ec_member'].includes(user.role);
    const canAccess = user && ALLOWED_ROLES.includes(user.role);

    // Load unit list for admin selectors
    useEffect(() => {
        if (!canAccess) return;
        if (isAdmin) {
            api.get('/units?limit=200')
                .then(res => {
                    const list = res.data?.units || res.data || [];
                    setUnits(list);
                    if (list.length > 0 && !selectedUnit) {
                        setSelectedUnit(list[ 0 ].unit_number);
                    } else if (list.length === 0) {
                        setLoading(false);
                    }
                })
                .catch(() => {
                    setLoading(false);
                });
        } else {
            // Non-admin owner / ec_member: use the sidebar's active unit so a
            // multi-unit owner's switch reaches this page too.
            setSelectedUnit(activeUnit || null);
        }
    }, [canAccess, isAdmin, activeUnit]); // eslint-disable-line react-hooks/exhaustive-deps

    const fetchData = useCallback(async (unitNum) => {
        if (!unitNum) return;
        setRefreshing(true);
        setDataError(null);
        try {
            const [summaryRes, subsidyRes] = await Promise.allSettled([
                api.get(`/intelligence/investor/summary/${unitNum}`),
                api.get('/intelligence/levy-fairness/subsidy-map'),
            ]);
            if (summaryRes.status === 'fulfilled') {
                const d = summaryRes.value.data;
                // Normalise backend field names to what the UI expects
                setGradeData({
                    ...d,
                    grade: d.investor_grade,
                    arrears_rate_pct: d.building_arrears_rate_pct,
                    compliance_score: d.compliance_score_pct,
                    red_flags_and_signals: d.red_flags || [],
                });
                // TCO data lives in the same summary response
                setTrueCostData({
                    annual_total_cents: d.current_annual_tco_cents,
                    projection_5yr_cents: d.projected_5yr_tco_cents,
                    projection_10yr_cents: null,
                    breakdown: [],
                });
            } else {
                setGradeData(null);
                setTrueCostData(null);
                const errMsg = summaryRes.reason?.response?.data?.detail || 'Intelligence data not yet generated for this unit.';
                setDataError(errMsg);
            }
            if (subsidyRes.status === 'fulfilled') setSubsidyData(subsidyRes.value.data);
            else setSubsidyData(null);
        } catch {
            toast.error('Failed to load investor intelligence data');
            setDataError('Failed to load data. Please try refreshing.');
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handleDownloadReport
     * Path: frontend/src/pages/dashboard/InvestorDashboardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadReport = async () => {
        setDownloadingReport(true);
        try {
            const resp = await api.get('/intelligence/investor/building-health-report', {responseType: 'blob'});
            // PDF generation can fall back to a JSON summary server-side (e.g. ReportLab
            // unavailable). Detect that case instead of silently downloading a corrupt
            // "PDF" made of JSON bytes.
            const contentType = resp.headers?.['content-type'] || '';
            if (!contentType.includes('application/pdf')) {
                const text = await resp.data.text();
                let detail = 'PDF generation is temporarily unavailable; a summary could not be produced.';
                try {
                    const parsed = JSON.parse(text);
                    detail = parsed.note || parsed.detail || detail;
                } catch { /* not JSON either — use default message */ }
                toast.error(detail);
                return;
            }
            const url = URL.createObjectURL(new Blob([resp.data], {type: 'application/pdf'}));
            const a = document.createElement('a');
            a.href = url;
            a.download = 'investor_building_health_report.pdf';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            toast.success('Report downloaded');
        } catch {
            toast.error('Failed to download report');
        } finally {
            setDownloadingReport(false);
        }
    };

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
        }
    }, [canAccess, router]);

    useEffect(() => {
        if (selectedUnit) {
            setLoading(true);
            fetchData(selectedUnit);
        }
    }, [selectedUnit, fetchData]);

    if (!canAccess) return null;

    const redFlags = gradeData?.red_flags_and_signals || [];
    const subsidyEntries = subsidyData?.subsidy_entries || [];
    const breakdown = trueCostData?.breakdown || [];
    const annualTotalCents = trueCostData?.annual_total_cents ?? 0;
    const proj5yr = trueCostData?.projection_5yr_cents ?? null;
    const proj10yr = trueCostData?.projection_10yr_cents ?? null;
    /**
     * @generated FunctionHeader
     * Function: fmtCents
     * Path: frontend/src/pages/dashboard/InvestorDashboardPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fmtCents = (cents) => cents != null ? `$${( cents / 100 ).toLocaleString('en-AU', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    })}` : '—';

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                        <TrendingUp className="h-6 w-6 text-primary"/>
                        Investor Intelligence
                    </h1>
                    <p className="text-sm text-muted-foreground mt-0.5">
                        Due-diligence grade, true cost of ownership, and cross-subsidy analysis
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {isAdmin && units.length > 0 && (
                        <Select value={selectedUnit || ''} onValueChange={setSelectedUnit}>
                            <SelectTrigger className="w-40">
                                <SelectValue placeholder="Select unit"/>
                            </SelectTrigger>
                            <SelectContent>
                                {units.map(u => (
                                    <SelectItem key={u.unit_number} value={u.unit_number}>
                                        {u.unit_number}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    )}
                    {isAdmin && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleDownloadReport}
                            disabled={downloadingReport}
                        >
                            <BarChart2 className={`h-4 w-4 mr-2 ${downloadingReport ? 'animate-pulse' : ''}`}/>
                            {downloadingReport ? 'Generating…' : 'Building Report'}
                        </Button>
                    )}
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => fetchData(selectedUnit)}
                        disabled={refreshing || !selectedUnit}
                    >
                        <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>
                        Refresh
                    </Button>
                </div>
            </div>

            {loading ? (
                <div className="flex justify-center py-24">
                    <Loader2 className="h-10 w-10 animate-spin text-muted-foreground"/>
                </div>
            ) : !selectedUnit ? (
                <Card>
                    <CardContent className="py-12 text-center">
                        {isAdmin ? (
                            <p className="text-muted-foreground">Select a unit above to view investor intelligence.</p>
                        ) : (
                            <div className="space-y-2">
                                <p className="text-muted-foreground font-medium">Unit not linked to your account</p>
                                <p className="text-muted-foreground text-sm">Please ask your strata manager to link a unit to
                                    your profile to view investor intelligence.</p>
                            </div>
                        )}
                    </CardContent>
                </Card>
            ) : dataError && !gradeData ? (
                <Card className="border-amber-200 bg-amber-50">
                    <CardContent className="py-10 text-center space-y-4">
                        <AlertTriangle className="h-10 w-10 text-amber-500 mx-auto"/>
                        <div>
                            <p className="font-semibold text-amber-800">Intelligence data not yet available</p>
                            <p className="text-amber-700 text-sm mt-1">{dataError}</p>
                        </div>
                        <div className="flex justify-center gap-3">
                            <Button variant="outline" size="sm" onClick={() => fetchData(selectedUnit)}
                                    disabled={refreshing}>
                                <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>
                                Retry
                            </Button>
                            {isAdmin && (
                                <Button size="sm" onClick={handleDownloadReport} disabled={downloadingReport}>
                                    <BarChart2 className="h-4 w-4 mr-2"/>
                                    Download Building Health Report
                                </Button>
                            )}
                        </div>
                        <p className="text-xs text-amber-600">
                            Run <strong>Seed Intelligence Data</strong> via Admin or ask your system administrator to
                            populate the intelligence collections.
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <>
                    {/* Red Flags Alert */}
                    {redFlags.length > 0 && (
                        <Alert className="border-red-200 bg-red-50">
                            <AlertTriangle className="h-4 w-4 text-red-600"/>
                            <AlertDescription className="text-red-800">
                                <strong>{redFlags.length} risk
                                    signal{redFlags.length > 1 ? 's' : ''} detected</strong> for unit {selectedUnit}.
                                Review flags below.
                            </AlertDescription>
                        </Alert>
                    )}

                    {/* Grade + Key Metrics */}
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        {/* Grade card */}
                        <Card className="md:col-span-1">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm text-muted-foreground font-medium">Investor Grade</CardTitle>
                            </CardHeader>
                            <CardContent>
                                <GradeBadge grade={gradeData?.grade}/>
                                <p className="text-xs text-muted-foreground text-center mt-3">Unit {selectedUnit}</p>
                            </CardContent>
                        </Card>

                        {/* Stress Score */}
                        <Card>
                            <CardContent className="pt-6 space-y-4">
                                <div>
                                    <p className="text-xs text-muted-foreground mb-1">Building Stress Score</p>
                                    <p className="text-3xl font-bold text-foreground">{gradeData?.stress_score ?? '—'}</p>
                                    <p className="text-xs text-muted-foreground">out of 100 (lower is better)</p>
                                </div>
                                <MetricBar
                                    label="Stress"
                                    value={gradeData?.stress_score ?? 0}
                                    colorClass={
                                        ( gradeData?.stress_score ?? 0 ) <= 25 ? 'bg-green-500' :
                                            ( gradeData?.stress_score ?? 0 ) <= 50 ? 'bg-yellow-500' :
                                                ( gradeData?.stress_score ?? 0 ) <= 75 ? 'bg-orange-500' : 'bg-red-500'
                                    }
                                />
                            </CardContent>
                        </Card>

                        {/* Adequacy + Arrears */}
                        <Card>
                            <CardContent className="pt-6 space-y-4">
                                <MetricBar
                                    label="Reserve Adequacy"
                                    value={gradeData?.reserve_adequacy_pct ?? 0}
                                    colorClass="bg-primary"
                                />
                                <MetricBar
                                    label="Arrears Rate"
                                    value={gradeData?.arrears_rate_pct ?? 0}
                                    colorClass={
                                        ( gradeData?.arrears_rate_pct ?? 0 ) < 5 ? 'bg-green-500' :
                                            ( gradeData?.arrears_rate_pct ?? 0 ) < 15 ? 'bg-amber-500' : 'bg-red-500'
                                    }
                                />
                            </CardContent>
                        </Card>

                        {/* Compliance */}
                        <Card>
                            <CardContent className="pt-6 space-y-4">
                                <div>
                                    <p className="text-xs text-muted-foreground mb-1">Compliance Score</p>
                                    <p className="text-3xl font-bold text-foreground">
                                        {gradeData?.compliance_score != null ? `${gradeData.compliance_score.toFixed(0)}%` : '—'}
                                    </p>
                                    <p className="text-xs text-muted-foreground">Regulatory compliance</p>
                                </div>
                                <MetricBar
                                    label="Compliance"
                                    value={gradeData?.compliance_score ?? 0}
                                    colorClass={
                                        ( gradeData?.compliance_score ?? 0 ) >= 90 ? 'bg-green-500' :
                                            ( gradeData?.compliance_score ?? 0 ) >= 70 ? 'bg-primary' :
                                                ( gradeData?.compliance_score ?? 0 ) >= 50 ? 'bg-amber-500' : 'bg-red-500'
                                    }
                                />
                            </CardContent>
                        </Card>
                    </div>

                    {/* Red Flags Detail */}
                    {redFlags.length > 0 && (
                        <Card className="border-red-200">
                            <CardHeader>
                                <CardTitle className="text-base flex items-center gap-2 text-red-700">
                                    <AlertTriangle className="h-4 w-4"/>
                                    Risk Flags &amp; Signals
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <ul className="space-y-2">
                                    {redFlags.map((flag, i) => (
                                        <li key={i} className="flex items-start gap-2 text-sm text-red-800">
                                            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-red-500"/>
                                            <span>{flag}</span>
                                        </li>
                                    ))}
                                </ul>
                            </CardContent>
                        </Card>
                    )}

                    {redFlags.length === 0 && gradeData && (
                        <Card className="border-green-200 bg-green-50">
                            <CardContent className="pt-5 pb-4">
                                <div className="flex items-center gap-2 text-green-800">
                                    <CheckCircle className="h-5 w-5 text-green-600"/>
                                    <span className="font-medium">No risk flags detected for unit {selectedUnit}</span>
                                </div>
                            </CardContent>
                        </Card>
                    )}

                    {/* True Cost of Ownership */}
                    {trueCostData && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base flex items-center gap-2">
                                    <DollarSign className="h-4 w-4 text-green-600"/>
                                    True Cost of Ownership — Unit {selectedUnit}
                                </CardTitle>
                                <CardDescription className="text-xs">
                                    All-in annual holding cost including levies, maintenance share, and amortised
                                    capital works
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-5">
                                    <div className="rounded-lg bg-muted border p-4 text-center">
                                        <p className="text-xs text-muted-foreground mb-1">Annual Total</p>
                                        <p className="text-2xl font-bold text-foreground">{fmtCents(annualTotalCents)}</p>
                                    </div>
                                    {proj5yr != null && (
                                        <div className="rounded-lg bg-primary/10 border border-primary/20 p-4 text-center">
                                            <p className="text-xs text-muted-foreground mb-1">5-Year Projection</p>
                                            <p className="text-2xl font-bold text-primary">{fmtCents(proj5yr)}</p>
                                        </div>
                                    )}
                                    {proj10yr != null && (
                                        <div
                                            className="rounded-lg bg-primary/10 border border-primary/20 p-4 text-center">
                                            <p className="text-xs text-muted-foreground mb-1">10-Year Projection</p>
                                            <p className="text-2xl font-bold text-primary">{fmtCents(proj10yr)}</p>
                                        </div>
                                    )}
                                </div>

                                {breakdown.length > 0 && (
                                    <div className="overflow-x-auto">
                                        <Table>
                                            <TableHeader>
                                            <TableRow className="border-b text-left text-muted-foreground">
                                                <TableHead className="pb-2 font-medium">Component</TableHead>
                                                <TableHead className="pb-2 font-medium text-right">Annual Cost</TableHead>
                                                <TableHead className="pb-2 font-medium text-right">% of Total</TableHead>
                                            </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                            {breakdown.map((row, i) => (
                                                <TableRow key={i} className="hover:bg-muted">
                                                    <TableCell className="py-2 text-foreground">{row.label || row.component}</TableCell>
                                                    <TableCell className="py-2 text-right font-medium">{fmtCents(row.amount_cents)}</TableCell>
                                                    <TableCell className="py-2 text-right text-muted-foreground">
                                                        {annualTotalCents > 0
                                                            ? `${( ( row.amount_cents / annualTotalCents ) * 100 ).toFixed(1)}%`
                                                            : '—'}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                            </TableBody>
                                        </Table>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    )}

                    {/* Cross-Subsidy Analysis */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base flex items-center gap-2">
                                <BarChart2 className="h-4 w-4 text-primary"/>
                                Cross-Subsidy Analysis
                            </CardTitle>
                            <CardDescription className="text-xs">
                                How shared facilities and costs are distributed across lot groups
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            {subsidyEntries.length === 0 ? (
                                <p className="text-sm text-muted-foreground py-4 text-center">No cross-subsidy data
                                    available.</p>
                            ) : (
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                        <TableRow className="border-b text-left text-muted-foreground">
                                            <TableHead className="pb-2 font-medium">Source Group</TableHead>
                                            <TableHead className="pb-2 font-medium">Benefit Group</TableHead>
                                            <TableHead className="pb-2 font-medium text-right">Subsidy</TableHead>
                                            <TableHead className="pb-2 font-medium text-right">Ratio</TableHead>
                                        </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                        {subsidyEntries.map((entry, i) => {
                                            const subsidyCents = entry.subsidy_cents ?? 0;
                                            const ratio = entry.ratio ?? 0;
                                            const isPositive = subsidyCents >= 0;
                                            return (
                                                <TableRow key={i} className="hover:bg-muted">
                                                    <TableCell className="py-2 text-foreground">{entry.source_group || '—'}</TableCell>
                                                    <TableCell className="py-2 text-foreground">{entry.benefit_group || '—'}</TableCell>
                                                    <TableCell className={`py-2 text-right font-medium ${isPositive ? 'text-red-600' : 'text-green-600'}`}>
                                                        {subsidyCents >= 0 ? '+' : ''}{fmtCents(subsidyCents)}
                                                    </TableCell>
                                                    <TableCell className="py-2 text-right text-muted-foreground">
                                                        {( ratio * 100 ).toFixed(1)}%
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}

                            <div
                                className="mt-4 flex items-start gap-2 text-xs text-muted-foreground bg-muted rounded-lg p-3">
                                <Info className="h-3.5 w-3.5 shrink-0 mt-0.5"/>
                                <span>
                  Cross-subsidy occurs when one lot group pays for facilities they benefit from less than another group.
                  Positive values indicate a group is subsidising others; negative values indicate a group is being subsidised.
                </span>
                            </div>
                        </CardContent>
                    </Card>
                </>
            )}
        </div>
    );
};

export default InvestorDashboardPage;
