// @ts-nocheck
// @featuretrace:financial_core — Finance intelligence page: sinking-fund plan, anomaly scanner, forecast generator, levy simulator, what-if analysis.
// Layer: frontend
// Data flow: FinanceIntelligencePage → GET/POST /finance/sinking-fund-plan, /finance/forecast/*, /finance/anomalies/*, /intelligence/* → financial_forecasts + sinking_fund_plan (building-scoped).
// Related: backend/routers/finance.py
//           backend/routers/intelligence.py
//           frontend/src/pages/dashboard/FinancePage.tsx
// Toggle: finance_intelligence
"use client";

import React, { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AnimatePresence, motion } from 'framer-motion';
import { toast } from 'sonner';
import {
    Activity,
    ArrowRight,
    BarChart3,
    Brain,
    Building2,
    CheckCircle2,
    DollarSign,
    Download,
    FileText,
    FileUp,
    Loader2,
    RefreshCw,
    ShieldAlert
} from 'lucide-react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis, } from 'recharts';

import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { useFinanceData } from '../../hooks/useFinanceData';
import { useTaxSummary } from '../../hooks/useTaxSummary';
import { formatCurrency } from '../../lib/utils';

// Premium Components
import {
    AnomalyPanelPremium,
    BudgetVarianceDetailedPremium,
    CashflowChartPremium,
    ExpenseConcentrationPremium,
    ForecastChartPremium,
    HealthScorePremium,
    InfoButton,
    LevyProjectionPremium,
    LevySimulatorPremium,
    LotCostDistributionPremium,
    SinkingFundPremium,
} from '../../components/finance/premium';
import { MetricCard } from '../../components/dashboard/premium';
import { PageHeader } from '@/components/shared/PageHeader';
import GaugeCard from '../../components/dashboard/GaugeCard';
import ChartCard from '../../components/dashboard/ChartCard';
import CollectionRateDetailDialog from '../../components/finance/CollectionRateDetailDialog';
import YearSelector from '../../components/widgets/YearSelector';
import { Button } from '../../components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Card, CardContent } from '../../components/ui/card';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Badge } from '../../components/ui/badge';

// ─── Constants ───────────────────────────────────────────────────────────────

const ALLOWED_ROLES = ['super_admin', 'strata_manager', 'ec_member'];
const GENERATE_ROLES = ['super_admin'];
const INGEST_ROLES = ['super_admin'];
// ─── Main Page Component ──────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: FinanceIntelligencePage
 * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const FinanceIntelligencePage = () => {
    const {user, api, selectedYear, selectedBuilding} = useAuth();
    const {activeUnit} = useActiveUnit();
    const router = useRouter();

    const userRole = user?.role || '';
    const canManage = ['super_admin', 'strata_manager'].includes(userRole);
    const canGenerate = GENERATE_ROLES.includes(userRole);
    const canIngest = INGEST_ROLES.includes(userRole);
    const canEditPlan = userRole === 'super_admin';

    // Data fetching via custom hook
    const {
        health, forecasts, anomalies, categories, lotSummaries,
        cashflow, levyProjection, loading, refresh,
    } = useFinanceData(api, selectedYear, {refreshInterval: 300000});

    const {loading: downloadingTax, downloadTaxSummary} = useTaxSummary(api);

    // Local state for actions
    const [activeTab, setActiveTab] = useState('overview');
    const [generatingForecast, setGeneratingForecast] = useState(false);
    const [downloadingReport, setDownloadingReport] = useState(false);
    const [downloadingSpecialLevyReport, setDownloadingSpecialLevyReport] = useState(false);
    const [uploadingPdf, setUploadingPdf] = useState(false);

    // Sinking fund plan + inflation projection data
    const [sinkingFundData, setSinkingFundData] = useState(null);
    const [sinkingFundProjection, setSinkingFundProjection] = useState(null);
    const [sinkingFundInsights, setSinkingFundInsights] = useState(null);
    const [sinkingFundLoading, setSinkingFundLoading] = useState(false);
    const [specialLevyForecast, setSpecialLevyForecast] = useState(null);
    const [levyStability, setLevyStability] = useState(null);
    const [riskModelsLoading, setRiskModelsLoading] = useState(false);
    const [computingLotSummaries, setComputingLotSummaries] = useState(false);
    const [whatIfResult, setWhatIfResult] = useState(null);
    const [whatIfRunning, setWhatIfRunning] = useState(false);
    const [whatIfScenario, setWhatIfScenario] = useState({
        sinkingFundIncreasePerUnit: 0,
        deferCapitalYears: 0,
        loanAmount: 0,
        loanInterestRate: 0.05,
        loanTermYears: 10,
    });

    // PDF Upload states
    const [pdfFile, setPdfFile] = useState(null);
    const [pdfYear, setPdfYear] = useState('');
    const [pdfDocType, setPdfDocType] = useState('budget');

    // Building KPIs — dollar-based collection rate (total_paid / total_levied)
    // Matches the Management Cockpit dashboard calculation exactly.
    const [kpis, setKpis] = useState(null);

    // Collection Rate detail dialog
    const [collectionRateDialogOpen, setCollectionRateDialogOpen] = useState(false);

    // Tax summary unit picker (for admin users who don't have a unit_number)
    const [taxPickerOpen, setTaxPickerOpen] = useState(false);
    const [taxPickerUnit, setTaxPickerUnit] = useState('');

    // Redirect unauthorised users
    useEffect(() => {
        if (user && !ALLOWED_ROLES.includes(user.role)) {
            router.replace('/dashboard');
        }
    }, [user, router]);

    // Fetch dollar-based collection rate whenever selected year changes
    // METRIC[collection_rate]: source endpoint — /stats/building-kpis.
    // Formula: confirmed_paid / (total_levied + total_opening_arrears).
    useEffect(() => {
        if (!selectedYear) return;
        api.get(`/stats/building-kpis?financial_year=${selectedYear}`)
            .then(res => setKpis(res.data))
            .catch(() => setKpis(null));
    }, [selectedYear, api]);

    useEffect(() => {
        if (!specialLevyForecast && !riskModelsLoading) {
            refreshRiskModels();
        }
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: refreshSinkingFundData
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const refreshSinkingFundData = async () => {
        setSinkingFundLoading(true);
        try {
            const [planResult, projResult, insightResult] = await Promise.allSettled([
                api.get('/finance/sinking-fund-plan'),
                api.get('/finance/sinking-fund-projection?inflation_rate=0.035'),
                api.get('/intelligence/capital-shock'),
            ]);

            setSinkingFundData(planResult.status === 'fulfilled' ? planResult.value.data : null);
            setSinkingFundProjection(projResult.status === 'fulfilled' ? projResult.value.data : null);
            setSinkingFundInsights(insightResult.status === 'fulfilled' ? insightResult.value.data : null);
        } catch {
            // Unexpected error — clear all related state
            setSinkingFundData(null);
            setSinkingFundProjection(null);
            setSinkingFundInsights(null);
        } finally {
            setSinkingFundLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: refreshRiskModels
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const refreshRiskModels = async () => {
        setRiskModelsLoading(true);
        try {
            const [specialRes, stabilityRes] = await Promise.allSettled([
                api.get('/intelligence/special-levy-forecast'),
                api.get('/intelligence/levy-stability'),
            ]);
            setSpecialLevyForecast(specialRes.status === 'fulfilled' ? specialRes.value.data : null);
            setLevyStability(stabilityRes.status === 'fulfilled' ? stabilityRes.value.data : null);
        } catch {
            setSpecialLevyForecast(null);
            setLevyStability(null);
        } finally {
            setRiskModelsLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleComputeLotSummaries
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleComputeLotSummaries = async () => {
        setComputingLotSummaries(true);
        const toastId = toast.loading('Computing lot cost summaries...');
        try {
            await api.post(`/finance/lot-summary/compute?year=${selectedYear}`);
            await refresh();
            toast.success('Lot summaries computed successfully', {id: toastId});
        } catch (error) {
            toast.error('Failed to compute lot summaries', {id: toastId});
        } finally {
            setComputingLotSummaries(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleWhatIf
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleWhatIf = async () => {
        setWhatIfRunning(true);
        const toastId = toast.loading('Running what-if simulation...');
        try {
            const payload = {
                sinking_fund_increase_per_unit: Number(whatIfScenario.sinkingFundIncreasePerUnit || 0),
                defer_capital_years: Number(whatIfScenario.deferCapitalYears || 0),
                loan_amount: Number(whatIfScenario.loanAmount || 0),
                loan_interest_rate: Number(whatIfScenario.loanInterestRate || 0),
                loan_term_years: Number(whatIfScenario.loanTermYears || 0),
            };
            const [specialRes, stabilityRes] = await Promise.all([
                api.post('/intelligence/special-levy-forecast/what-if', payload),
                api.post('/intelligence/levy-stability/what-if', payload),
            ]);
            setSpecialLevyForecast(specialRes.data);
            setLevyStability(stabilityRes.data);
            setWhatIfResult({special: specialRes.data, stability: stabilityRes.data});
            toast.success('What-if simulation complete', {id: toastId});
        } catch (error) {
            console.error('What-if error:', error);
            toast.error('Failed to run what-if simulation', {id: toastId});
        } finally {
            setWhatIfRunning(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleResetWhatIf
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleResetWhatIf = async () => {
        setWhatIfScenario({
            sinkingFundIncreasePerUnit: 0,
            deferCapitalYears: 0,
            loanAmount: 0,
            loanInterestRate: 0.05,
            loanTermYears: 10,
        });
        await refreshRiskModels();
    };
    /**
     * @generated FunctionHeader
     * Function: handleDownloadSpecialLevyReport
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadSpecialLevyReport = async () => {
        setDownloadingSpecialLevyReport(true);
        const toastId = toast.loading('Preparing special levy report...');
        try {
            const response = await api.get('/intelligence/special-levy-report', {responseType: 'blob'});
            const blobUrl = URL.createObjectURL(new Blob([response.data]));
            const link = document.createElement('a');
            link.href = blobUrl;
            link.download = `EastGate_SpecialLevyRisk_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}.pdf`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(blobUrl);
            toast.success('Special levy report downloaded', {id: toastId});
        } catch (error) {
            console.error('Download error:', error);
            toast.error('Failed to download report', {id: toastId});
        } finally {
            setDownloadingSpecialLevyReport(false);
        }
    };

    // Fetch sinking fund plan + inflation projection when Capital Works tab first opens
    useEffect(() => {
        if (activeTab === 'capital-works' && !sinkingFundData && !sinkingFundLoading) {
            refreshSinkingFundData();
        }
    }, [activeTab, sinkingFundData, sinkingFundLoading, api]);
    /**
     * @generated FunctionHeader
     * Function: handleSaveSinkingFundPlan
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveSinkingFundPlan = async (planRows) => {
        const toastId = toast.loading('Saving sinking fund plan...');
        try {
            await api.put('/finance/sinking-fund-plan', {plan: planRows});
            toast.success('Sinking fund plan updated', {id: toastId});
            await refreshSinkingFundData();
        } catch {
            toast.error('Failed to update sinking fund plan', {id: toastId});
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveCapitalEvents
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveCapitalEvents = async (events) => {
        const toastId = toast.loading('Saving capital events...');
        try {
            await api.put('/finance/sinking-fund-capital-events', {events});
            toast.success('Capital events updated', {id: toastId});
            await refreshSinkingFundData();
        } catch {
            toast.error('Failed to update capital events', {id: toastId});
        }
    };

    // ─── Computed Metrics ──────────────────────────────────────────────────────

    const metrics = useMemo(() => {
        if (loading || !selectedYear) return null;

        const criticalAnomalies = anomalies.filter(a => a.severity === 'critical' && !a.resolved).length;
        const highAnomalies = anomalies.filter(a => a.severity === 'high' && !a.resolved).length;

        // Canonical due-date Collection Rate (GAP-FIN-035): allocated ÷ charged-to-date, per-unit
        // clamped. Prefer the new `due_date_collection_rate_pct` field; fall back to the legacy
        // `collection_rate` (which is the full-year fund-health/coverage number, inflated by
        // not-yet-due instalments) only for older backends that don't return the canonical one.
        const collectionRate = kpis?.due_date_collection_rate_pct ?? kpis?.collection_rate ?? 0;
        const unitsInArrears = kpis?.units_in_arrears ?? lotSummaries.filter(s => s.arrears_flag).length;

        // Only compute budget variance when there is actual spending recorded.
        // If all actual_amount values are 0, show null to indicate no spending data.
        const categoriesWithBudget = categories.filter(c => c.budgeted_amount > 0);
        const totalActualSpending = categoriesWithBudget.reduce((acc, c) => acc + ( c.actual_amount || 0 ), 0);
        const budgetVariance = totalActualSpending === 0
            ? null  // null signals "no spending recorded"
            : categoriesWithBudget.reduce((acc, c) => acc + ( ( c.actual_amount || 0 ) - c.budgeted_amount ), 0);

        const budgetOverruns = categories.filter(c => ( c.actual_amount || 0 ) > c.budgeted_amount && c.budgeted_amount > 0).length;

        return {
            healthScore: health?.score || 0,
            riskLevel: health?.risk_level || 'moderate',
            criticalAnomalies,
            highAnomalies,
            collectionRate,
            unitsInArrears,
            budgetVariance,
            budgetOverruns,
            noSpendingData: totalActualSpending === 0 && categoriesWithBudget.length > 0
        };
    }, [health, anomalies, lotSummaries, categories, kpis, loading, selectedYear]);

    const specialLevyRisk = useMemo(() => {
        const prob = specialLevyForecast?.probability || 0;
        if (prob < 10) return {label: 'Low', color: '#22c55e'};
        if (prob < 25) return {label: 'Medium', color: '#f59e0b'};
        return {label: 'High', color: '#ef4444'};
    }, [specialLevyForecast]);

    const levyStabilityColor = useMemo(() => {
        const score = levyStability?.levy_stability_score || 0;
        if (score > 85) return '#22c55e';
        if (score > 70) return '#86efac';
        if (score > 55) return '#f59e0b';
        if (score > 40) return '#f97316';
        return '#ef4444';
    }, [levyStability]);

    const levyDistributionData = useMemo(() => {
        const values = specialLevyForecast?.per_unit_distribution || [];
        if (!values.length) return [];
        const maxVal = Math.max(...values);
        const buckets = 8;
        const size = maxVal > 0 ? maxVal / buckets : 1;
        const data = Array.from({length: buckets}, (_, idx) => ( {
            label: `${formatCurrency(idx * size)}-${formatCurrency(( idx + 1 ) * size)}`,
            count: 0,
        } ));
        values.forEach((v) => {
            const bucketIdx = Math.min(buckets - 1, Math.floor(v / size));
            data[ bucketIdx ].count += 1;
        });
        return data;
    }, [specialLevyForecast]);

    const shockTimelineData = useMemo(() => {
        const dist = specialLevyForecast?.shock_year_distribution || {};
        return Object.keys(dist).sort().map((year) => ( {
            year,
            probability: dist[ year ],
        } ));
    }, [specialLevyForecast]);

    const reserveFanData = useMemo(() => (
        specialLevyForecast?.reserve_fan || []
    ), [specialLevyForecast]);
    // ─── Action Handlers ──────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: handleGenerateForecast
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleGenerateForecast = async () => {
        if (!selectedYear) return;
        setGeneratingForecast(true);
        const toastId = toast.loading('Generating strategic forecast...');
        try {
            const res = await api.post(`/finance/forecast/generate?year=${selectedYear}`);
            toast.success(`Generated ${res.data.count} forecasts`, {id: toastId});
            refresh();
        } catch {
            toast.error('Failed to generate forecast', {id: toastId});
        } finally {
            setGeneratingForecast(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRescan
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRescan = async () => {
        if (!selectedYear) return;
        const toastId = toast.loading('Rescanning for anomalies...');
        try {
            await api.post(`/finance/anomalies/scan?year=${selectedYear}`);
            toast.success('Audit re-scan complete', {id: toastId});
            await refresh();
        } catch {
            toast.error('Rescan failed', {id: toastId});
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleResolveAnomaly
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleResolveAnomaly = async (id) => {
        try {
            await api.put(`/finance/anomalies/${id}/resolve`);
            toast.success('Anomaly resolved');
            await refresh();
        } catch {
            toast.error('Failed to resolve anomaly');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSimulate
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSimulate = async (increasePct) => {
        if (!selectedYear) return;
        const res = await api.get(`/finance/levy-simulator?year=${selectedYear}&increase_pct=${increasePct}`);
        return res.data;
    };
    /**
     * @generated FunctionHeader
     * Function: handleDownloadReport
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadReport = async () => {
        if (!selectedYear) return;
        setDownloadingReport(true);
        const toastId = toast.loading('Generating PDF intelligence report...');
        try {
            const res = await api.get(`/finance/report/${selectedYear}`, {responseType: 'blob'});
            const url = URL.createObjectURL(new Blob([res.data], {type: 'application/pdf'}));
            const a = document.createElement('a');
            a.href = url;
            a.download = `eastgate_financial_intelligence_${selectedYear}.pdf`;
            a.click();
            toast.success('Report downloaded', {id: toastId});
        } catch {
            toast.error('Failed to generate report', {id: toastId});
        } finally {
            setDownloadingReport(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDownloadTaxSummary
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadTaxSummary = async () => {
        if (activeUnit) {
            // Owner/tenant: download the unit currently active in the sidebar switcher
            await downloadTaxSummary(activeUnit, selectedYear);
        } else {
            // Admin/manager: show unit picker
            setTaxPickerUnit('');
            setTaxPickerOpen(true);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleTaxPickerConfirm
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleTaxPickerConfirm = async () => {
        const unit = taxPickerUnit.trim().toUpperCase();
        if (!unit) {
            toast.error('Please enter a unit number');
            return;
        }
        setTaxPickerOpen(false);
        await downloadTaxSummary(unit, selectedYear);
    };
    /**
     * @generated FunctionHeader
     * Function: handlePdfUpload
     * Path: frontend/src/pages/dashboard/FinanceIntelligencePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePdfUpload = async () => {
        if (!pdfFile || !pdfYear) {
            toast.error('Select PDF and enter year');
            return;
        }
        setUploadingPdf(true);
        const toastId = toast.loading('Processing document...');
        try {
            const formData = new FormData();
            formData.append('file', pdfFile);
            formData.append('year', pdfYear);
            formData.append('document_type', pdfDocType);
            const res = await api.post('/finance/documents/upload', formData, {
                headers: {'Content-Type': 'multipart/form-data'},
            });
            toast.success('Document processed', {id: toastId});
            refresh();
        } catch {
            toast.error('Upload failed', {id: toastId});
        } finally {
            setUploadingPdf(false);
        }
    };

    // ─── Render ────────────────────────────────────────────────────────────────

    if (!user || !ALLOWED_ROLES.includes(user.role)) return null;

    return (
        <div className="p-6 md:p-10 space-y-8 pb-24">

            {/* Canonical page chrome. This was a hand-rolled header with a 3xl title and a
                large accent icon chip — the same role PageHeader fills on every other
                dashboard page, at a different size. Using the shared component is what makes
                the page sit at the same visual altitude as the rest of the app, and it
                supplies the single <h1> the hand-rolled version also had. */}
            <PageHeader
                title="Financial Intelligence"
                icon={<Brain className="h-5 w-5"/>}
                description="Strategic forecasting & anomaly detection hub"
                actions={
                    <>
                        <YearSelector/>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleDownloadTaxSummary}
                            disabled={downloadingTax}
                            className="gap-2"
                        >
                            <FileText size={16}/>
                            Annual Tax Summary
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleDownloadReport}
                            disabled={downloadingReport}
                            className="gap-2"
                        >
                            <Download size={16}/>
                            Full Intelligence Report
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={refresh}
                            disabled={loading}
                            aria-label="Refresh financial intelligence"
                        >
                            <RefreshCw size={18} className={loading ? 'animate-spin' : ''}/>
                        </Button>
                    </>
                }
            />

            {/* ── KPI Grid ── */}
            <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                <MetricCard
                    title="Financial Health"
                    value={metrics?.healthScore || 0}
                    subtitle={`Risk Level: ${metrics?.riskLevel.toUpperCase()}`}
                    icon={Activity}
                    colorClass={
                        metrics?.riskLevel === 'excellent' || metrics?.riskLevel === 'good'
                            ? "bg-emerald-50 text-emerald-600"
                            : metrics?.riskLevel === 'moderate'
                                ? "bg-amber-50 text-amber-600"
                                : "bg-rose-50 text-rose-600"
                    }
                    delay={0.1}
                    onClick={() => setActiveTab('risk')}
                    info={
                        <InfoButton
                            title="Financial Health"
                            description="Financial-health score from 7 weighted financial metrics (surplus, arrears, cashflow buffer, forecast stability, budget discipline, expense volatility, reserve adequacy). This is a purely financial measure and is DIFFERENT from the dashboard's operational 'Property Pulse' score, which blends collection, compliance, maintenance and governance signals — the two are not the same number."
                            dataSources={["multiple_collections"]}
                        />
                    }
                />
                <MetricCard
                    title="Critical Flags"
                    value={metrics?.criticalAnomalies || 0}
                    subtitle={`${metrics?.highAnomalies || 0} high-severity issues`}
                    icon={ShieldAlert}
                    colorClass={metrics?.criticalAnomalies > 0 ? "bg-rose-50 text-rose-600" : "bg-emerald-50 text-emerald-600"}
                    delay={0.2}
                    onClick={() => setActiveTab('risk')}
                    info={
                        <InfoButton
                            title="Audit Flags"
                            description="Total number of unresolved high-severity financial irregularities detected by the AI audit engine."
                            dataSources={["financial_anomalies"]}
                        />
                    }
                />
                <MetricCard
                    title="Collection Rate"
                    value={`${metrics?.collectionRate || 0}%`}
                    subtitle={`${metrics?.unitsInArrears || 0} units outstanding`}
                    icon={DollarSign}
                    colorClass="bg-primary/10 text-primary"
                    delay={0.3}
                    onClick={() => setCollectionRateDialogOpen(true)}
                    info={
                        <InfoButton
                            title="Collection Rate"
                            description="Due-date collection rate: of the levies CHARGED and due as of today, the percentage collected (per-unit, so paying ahead doesn't inflate it). This is NOT full-year coverage — money for instalments not yet due is excluded from both sides."
                            dataSources={["unit_levy_ledger", "levy_payments"]}
                        />
                    }
                />
                <MetricCard
                    title="Budget Variance"
                    value={
                        metrics?.budgetVariance === null
                            ? '$—.—'
                            : metrics?.budgetVariance != null
                                ? formatCurrency(metrics.budgetVariance)
                                : '$0'
                    }
                    subtitle={
                        metrics?.noSpendingData
                            ? 'No spending recorded this year'
                            : `${metrics?.budgetOverruns || 0} categories over budget`
                    }
                    icon={BarChart3}
                    colorClass={
                        metrics?.budgetVariance === null
                            ? "bg-muted text-muted-foreground"
                            : metrics?.budgetVariance > 0
                                ? "bg-rose-50 text-rose-600"
                                : "bg-emerald-50 text-emerald-600"
                    }
                    delay={0.4}
                    onClick={() => setActiveTab('forecast')}
                    info={
                        <InfoButton
                            title="Budget Variance"
                            description="Net difference between actual expenditure and budgeted amounts across all active categories."
                            dataSources={["levy_categories"]}
                        />
                    }
                />
            </section>

            {/* ── Collection Rate Detail Dialog ── */}
            <CollectionRateDetailDialog
                open={collectionRateDialogOpen}
                onOpenChange={setCollectionRateDialogOpen}
                selectedYear={selectedYear || '2026'}
            />

            {/* ── Tax Summary Unit Picker (for admin users without a unit_number) ── */}
            <Dialog open={taxPickerOpen} onOpenChange={setTaxPickerOpen}>
                <DialogContent className="sm:max-w-sm">
                    <DialogHeader>
                        <DialogTitle>Annual Tax Summary</DialogTitle>
                    </DialogHeader>
                    <div className="py-4 space-y-3">
                        <p className="text-sm text-muted-foreground">
                            Enter the unit number to generate the tax summary for financial
                            year <strong>{selectedYear}</strong>.
                        </p>
                        <div className="space-y-1.5">
                            <Label htmlFor="tax-unit-input">Unit Number</Label>
                            <Input
                                id="tax-unit-input"
                                placeholder="e.g. TH017 or UA031"
                                value={taxPickerUnit}
                                onChange={e => setTaxPickerUnit(e.target.value)}
                                onKeyDown={e => e.key === 'Enter' && handleTaxPickerConfirm()}
                                className="uppercase"
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setTaxPickerOpen(false)}>Cancel</Button>
                        <Button onClick={handleTaxPickerConfirm} disabled={downloadingTax || !taxPickerUnit.trim()}>
                            <FileText size={14} className="mr-2"/>
                            Generate PDF
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Intelligence Tabs ── */}
            <Tabs value={activeTab} className="w-full space-y-10" onValueChange={setActiveTab}>
                <div className="flex justify-center">
                    <TabsList
                        className="bg-muted p-1.5 rounded-xl border border-border gap-1 overflow-x-auto max-w-full">
                        <TabsTrigger value="overview"
                                     className="rounded-2xl px-6 py-2.5 text-xs font-semibold uppercase tracking-widest data-[state=active]:bg-card data-[state=active]:shadow-xl">Overview</TabsTrigger>
                        <TabsTrigger value="forecast"
                                     className="rounded-2xl px-6 py-2.5 text-xs font-semibold uppercase tracking-widest data-[state=active]:bg-card data-[state=active]:shadow-xl">Forecast</TabsTrigger>
                        <TabsTrigger value="risk"
                                     className="rounded-2xl px-6 py-2.5 text-xs font-semibold uppercase tracking-widest data-[state=active]:bg-card data-[state=active]:shadow-xl">Risk
                            & Audit</TabsTrigger>
                        <TabsTrigger value="units"
                                     className="rounded-2xl px-6 py-2.5 text-xs font-semibold uppercase tracking-widest data-[state=active]:bg-card data-[state=active]:shadow-xl">Unit
                            Impacts</TabsTrigger>
                        <TabsTrigger value="levy"
                                     className="rounded-2xl px-6 py-2.5 text-xs font-semibold uppercase tracking-widest data-[state=active]:bg-card data-[state=active]:shadow-xl">Levy
                            Modeling</TabsTrigger>
                        <TabsTrigger value="capital-works"
                                     className="rounded-2xl px-6 py-2.5 text-xs font-semibold uppercase tracking-widest data-[state=active]:bg-card data-[state=active]:shadow-xl">Capital
                            Works</TabsTrigger>
                        {canIngest && (
                            <TabsTrigger value="ingest"
                                         className="rounded-2xl px-6 py-2.5 text-xs font-semibold uppercase tracking-widest data-[state=active]:bg-card data-[state=active]:shadow-xl">Import</TabsTrigger>
                        )}
                    </TabsList>
                </div>

                <AnimatePresence mode="wait">
                    {/* ── Overview Tab ── */}
                    <TabsContent value="overview">
                        <motion.div
                            initial={{opacity: 0, scale: 0.98}}
                            animate={{opacity: 1, scale: 1}}
                            exit={{opacity: 0, scale: 0.98}}
                            className="grid grid-cols-1 lg:grid-cols-2 gap-8"
                        >
                            <HealthScorePremium
                                score={health?.score || 0}
                                breakdown={health?.breakdown || {}}
                                riskLevel={health?.risk_level || 'moderate'}
                                year={selectedYear}
                                details={health?.details}
                            />
                            <ExpenseConcentrationPremium categories={categories} year={selectedYear}/>
                            <ForecastChartPremium forecasts={forecasts} year={selectedYear} categories={categories}/>
                            <CashflowChartPremium
                                months={cashflow?.months || []}
                                annualIncome={cashflow?.annual_income || 0}
                                annualExpenses={cashflow?.annual_expenses || 0}
                                minBalance={cashflow?.min_balance || 0}
                                riskMonths={cashflow?.risk_months || []}
                                openingBalance={cashflow?.opening_balance || 0}
                                year={selectedYear}
                            />
                            <GaugeCard
                                title="Special Levy Risk"
                                value={`${specialLevyForecast?.probability || 0}%`}
                                percentage={specialLevyForecast?.probability || 0}
                                color={specialLevyRisk.color}
                                subtitle={`${specialLevyRisk.label} Risk`}
                            />
                            <GaugeCard
                                title="Levy Stability Score"
                                value={Math.round(levyStability?.levy_stability_score || 0)}
                                percentage={levyStability?.levy_stability_score || 0}
                                color={levyStabilityColor}
                                subtitle="Stability Score"
                            />
                        </motion.div>
                    </TabsContent>

                    {/* ── Forecast Tab ── */}
                    <TabsContent value="forecast">
                        <motion.div
                            initial={{opacity: 0, y: 20}}
                            animate={{opacity: 1, y: 0}}
                            className="space-y-8"
                        >
                            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                                <div className="lg:col-span-2">
                                    <ForecastChartPremium forecasts={forecasts} year={selectedYear}
                                                          categories={categories}/>
                                </div>
                                <div>
                                    <Card
                                        className="rounded-xl border-border bg-primary text-primary-foreground shadow-md h-full p-8 overflow-hidden relative">
                                        <div
                                            className="absolute top-0 right-0 w-32 h-32 bg-card/10 blur-[50px] rounded-full -mr-16 -mt-16"/>
                                        <h3 className="text-xl font-semibold mb-2">Strategic Forecast</h3>
                                        <p className="text-primary text-sm mb-4 leading-relaxed">
                                            AI models use historical expenditure, CPI trends, and capital works to
                                            project your building's finances for the next 3 years.
                                        </p>
                                        {forecasts.length === 0 && (
                                            <div
                                                className="mb-4 p-3 bg-card/10 rounded-xl text-xs text-primary leading-relaxed border border-border">
                                                <p className="font-semibold mb-1">ℹ️ No forecast data yet</p>
                                                <p>Click <span className="font-semibold">Regenerate Forecast</span> to run
                                                    the AI model. It reads your approved levy categories and historical
                                                    actuals to compute projections.</p>
                                            </div>
                                        )}
                                        <div className="space-y-4">
                                            <div className="flex items-center gap-4">
                                                <div
                                                    className="w-10 h-10 rounded-xl bg-card/20 flex items-center justify-center">
                                                    <ShieldAlert size={20}/>
                                                </div>
                                                <div>
                                                    <p className="text-xs font-semibold uppercase tracking-widest text-primary">Model
                                                        Confidence</p>
                                                    <p className="text-lg font-semibold">{forecasts.length > 0 ? Math.round(forecasts.reduce((a, b) => a + ( b.confidence_score || 0 ), 0) / forecasts.length * 100) : 0}%
                                                        Avg</p>
                                                </div>
                                            </div>
                                            <div className="flex items-center gap-4">
                                                <div
                                                    className="w-10 h-10 rounded-xl bg-card/20 flex items-center justify-center">
                                                    <Activity size={20}/>
                                                </div>
                                                <div>
                                                    <p className="text-xs font-semibold uppercase tracking-widest text-primary">Categories
                                                        Modelled</p>
                                                    <p className="text-lg font-semibold">{forecasts.length}</p>
                                                </div>
                                            </div>
                                            {canGenerate && (
                                                <Button
                                                    onClick={handleGenerateForecast}
                                                    disabled={generatingForecast}
                                                    className="w-full bg-card text-primary hover:bg-primary/10 font-semibold rounded-xl py-6 mt-2"
                                                >
                                                    <RefreshCw
                                                        className={`mr-2 h-4 w-4 ${generatingForecast ? 'animate-spin' : ''}`}/>
                                                    {generatingForecast ? 'Generating...' : 'Regenerate Forecast'}
                                                </Button>
                                            )}
                                            <p className="text-[10px] text-primary/70 leading-relaxed">
                                                Uses: Linear regression, CPI inflation (3.5%), and capital works
                                                schedule from the approved budget.
                                            </p>
                                        </div>
                                    </Card>
                                </div>
                            </div>
                            <BudgetVarianceDetailedPremium categories={categories} year={selectedYear}/>
                        </motion.div>
                    </TabsContent>

                    {/* ── Risk & Audit Tab ── */}
                    <TabsContent value="risk">
                        <motion.div
                            initial={{opacity: 0, y: 20}}
                            animate={{opacity: 1, y: 0}}
                            className="grid grid-cols-1 lg:grid-cols-3 gap-8"
                        >
                            <div className="lg:col-span-2">
                                <AnomalyPanelPremium
                                    anomalies={anomalies}
                                    year={selectedYear}
                                    canManage={canManage}
                                    onRescan={handleRescan}
                                    onResolve={handleResolveAnomaly}
                                />
                            </div>
                            <div className="space-y-8">
                                <HealthScorePremium
                                    score={health?.score || 0}
                                    breakdown={health?.breakdown || {}}
                                    riskLevel={health?.risk_level || 'moderate'}
                                    year={selectedYear}
                                    details={health?.details}
                                />
                                <Card
                                    className="rounded-xl border border-border bg-card shadow-sm p-6">
                                    <h3 className="text-foreground text-lg font-semibold tracking-tight mb-4 flex items-center gap-2">
                                        <ShieldAlert className="text-rose-500 w-5 h-5"/>
                                        Audit Policy
                                    </h3>
                                    <p className="text-muted-foreground text-xs font-medium leading-relaxed mb-6">
                                        System performs a full financial sweep daily, checking for budget overruns,
                                        unexpected utility spikes, and collection delays.
                                    </p>
                                    <div className="space-y-4">
                                        <div
                                            className="flex justify-between items-center py-2 border-b border-border">
                                            <span
                                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Next Auto-Scan</span>
                                            <span className="text-xs font-bold text-foreground">Tonight 02:00 AM</span>
                                        </div>
                                        <div
                                            className="flex justify-between items-center py-2 border-b border-border">
                                            <span
                                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Data Integrity</span>
                                            <Badge className="bg-emerald-500 text-white text-[8px] font-semibold uppercase">Verified</Badge>
                                        </div>
                                    </div>
                                </Card>
                            </div>
                        </motion.div>
                        <div className="mt-10 grid grid-cols-1 lg:grid-cols-3 gap-8">
                            <ChartCard title="Levy Distribution" description="Per-unit levy size">
                                <BarChart data={levyDistributionData}
                                          margin={{top: 5, right: 10, left: 20, bottom: 20}}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                    <XAxis dataKey="label" tick={{fontSize: 10}} interval={0} angle={-20}
                                           textAnchor="end" height={60}/>
                                    <YAxis tick={{fontSize: 11}} width={35} axisLine={false} tickLine={false}/>
                                    <Tooltip/>
                                    <Bar dataKey="count" fill="#6366f1" radius={[6, 6, 0, 0]}/>
                                </BarChart>
                            </ChartCard>
                            <ChartCard title="Shock Timeline" description="Probability by year">
                                <BarChart data={shockTimelineData} margin={{top: 5, right: 10, left: 20, bottom: 5}}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                    <XAxis dataKey="year" tick={{fontSize: 11}}/>
                                    <YAxis tick={{fontSize: 11}} width={40} axisLine={false} tickLine={false}
                                           tickFormatter={(v) => `${v}%`}/>
                                    <Tooltip formatter={(value) => [`${value}%`, 'Probability']}/>
                                    <Bar dataKey="probability" fill="#f59e0b" radius={[6, 6, 0, 0]}/>
                                </BarChart>
                            </ChartCard>
                            <ChartCard title="Reserve Trajectory" description="Fan chart projection">
                                <AreaChart data={reserveFanData} margin={{top: 5, right: 10, left: 70, bottom: 5}}>
                                    <defs>
                                        <linearGradient id="colorP90" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#c7d2fe" stopOpacity={0.8}/>
                                            <stop offset="95%" stopColor="#c7d2fe" stopOpacity={0.1}/>
                                        </linearGradient>
                                        <linearGradient id="colorP75" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#a5b4fc" stopOpacity={0.7}/>
                                            <stop offset="95%" stopColor="#a5b4fc" stopOpacity={0.1}/>
                                        </linearGradient>
                                        <linearGradient id="colorP50" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.7}/>
                                            <stop offset="95%" stopColor="#6366f1" stopOpacity={0.1}/>
                                        </linearGradient>
                                    </defs>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                                    <XAxis dataKey="year" tick={{fontSize: 11}}/>
                                    <YAxis tick={{fontSize: 11}} width={70} axisLine={false} tickLine={false}
                                           tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`}/>
                                    <Tooltip formatter={(value) => [formatCurrency(value), 'Reserve']}/>
                                    <Area type="monotone" dataKey="p90" stroke="#c7d2fe" fill="url(#colorP90)"/>
                                    <Area type="monotone" dataKey="p75" stroke="#a5b4fc" fill="url(#colorP75)"/>
                                    <Area type="monotone" dataKey="p50" stroke="#6366f1" fill="url(#colorP50)"/>
                                </AreaChart>
                            </ChartCard>
                        </div>
                        <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-8">
                            <Card
                                className="rounded-xl border border-border bg-card shadow-sm p-6">
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="text-foreground text-lg font-semibold tracking-tight">Owner Summary</h3>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={handleDownloadSpecialLevyReport}
                                        disabled={downloadingSpecialLevyReport}
                                    >
                                        <Download className="mr-2 h-4 w-4"/>
                                        Export Report
                                    </Button>
                                </div>
                                <p className="text-muted-foreground text-sm leading-relaxed">
                                    {specialLevyForecast?.explanation || 'Run the forecast to generate a levy risk summary.'}
                                </p>
                                <div className="mt-6 grid grid-cols-2 gap-4 text-xs">
                                    <div>
                                        <p className="text-muted-foreground uppercase tracking-widest font-semibold">Median
                                            Levy</p>
                                        <p className="text-foreground font-bold">{formatCurrency(specialLevyForecast?.median_amount || 0)}</p>
                                    </div>
                                    <div>
                                        <p className="text-muted-foreground uppercase tracking-widest font-semibold">Worst
                                            Case</p>
                                        <p className="text-foreground font-bold">{formatCurrency(specialLevyForecast?.worst_case || 0)}</p>
                                    </div>
                                </div>
                            </Card>
                            <Card
                                className="rounded-xl border border-border bg-card shadow-sm p-6">
                                <h3 className="text-foreground text-lg font-semibold tracking-tight mb-4">Stability
                                    Breakdown</h3>
                                <div className="space-y-4">
                                    {[
                                        {label: 'Reserve Adequacy', value: levyStability?.reserve_score || 0},
                                        {label: 'Levy Volatility', value: levyStability?.volatility_score || 0},
                                        {label: 'Capital Shock Risk', value: levyStability?.shock_score || 0},
                                        {label: 'Funding Sufficiency', value: levyStability?.funding_score || 0},
                                    ].map((item) => (
                                        <div key={item.label}>
                                            <div
                                                className="flex items-center justify-between text-xs font-semibold text-muted-foreground">
                                                <span>{item.label}</span>
                                                <span>{Math.round(item.value)}</span>
                                            </div>
                                            <div className="h-2 rounded-full bg-muted mt-2">
                                                <div
                                                    className="h-2 rounded-full"
                                                    style={{
                                                        width: `${item.value}%`,
                                                        backgroundColor: levyStabilityColor
                                                    }}
                                                />
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </Card>
                        </div>
                    </TabsContent>

                    {/* ── Unit Impacts Tab ── */}
                    <TabsContent value="units">
                        <motion.div
                            initial={{opacity: 0, y: 20}}
                            animate={{opacity: 1, y: 0}}
                            className="space-y-8"
                        >
                            {lotSummaries.length === 0 ? (
                                <div
                                    className="flex flex-col items-center justify-center py-20 gap-6 rounded-xl border border-dashed border-border bg-muted/40">
                                    <div
                                        className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
                                        <Building2 className="w-8 h-8 text-primary"/>
                                    </div>
                                    <div className="text-center max-w-sm">
                                        <h3 className="text-lg font-semibold text-foreground mb-2">No Cost Distribution
                                            Data</h3>
                                        <p className="text-sm text-muted-foreground leading-relaxed">
                                            Per-unit cost summaries haven't been computed for FY {selectedYear} yet.
                                            This combines levy payments, water bills, council rates and land tax to show
                                            true ownership cost per unit.
                                        </p>
                                    </div>
                                    {canManage && (
                                        <Button
                                            onClick={handleComputeLotSummaries}
                                            disabled={computingLotSummaries}
                                            className="rounded-xl gap-2"
                                        >
                                            {computingLotSummaries ? (
                                                <><Loader2 className="w-4 h-4 animate-spin"/> Computing…</>
                                            ) : (
                                                <><RefreshCw className="w-4 h-4"/> Compute Unit Cost Distribution</>
                                            )}
                                        </Button>
                                    )}
                                </div>
                            ) : (
                                <LotCostDistributionPremium summaries={lotSummaries} year={selectedYear}/>
                            )}
                        </motion.div>
                    </TabsContent>

                    {/* ── Levy Modeling Tab ── */}
                    <TabsContent value="levy">
                        <motion.div
                            initial={{opacity: 0, y: 20}}
                            animate={{opacity: 1, y: 0}}
                            className="grid grid-cols-1 lg:grid-cols-3 gap-8"
                        >
                            {levyProjection && <LevyProjectionPremium data={levyProjection} year={selectedYear}/>}
                            <LevySimulatorPremium year={selectedYear} onSimulate={handleSimulate}/>
                            <Card
                                className="rounded-xl border border-border bg-card shadow-sm p-6 space-y-5">
                                <div>
                                    <h3 className="text-foreground text-lg font-semibold tracking-tight">Special Levy
                                        What-If</h3>
                                    <p className="text-muted-foreground text-xs mt-1">Simulate funding changes and re-run the
                                        risk model.</p>
                                </div>
                                <div className="space-y-3">
                                    <div className="space-y-1">
                                        <Label className="text-xs">Increase sinking fund ($/unit/year)</Label>
                                        <Input
                                            type="number"
                                            value={whatIfScenario.sinkingFundIncreasePerUnit}
                                            onChange={(e) => setWhatIfScenario(prev => ( {
                                                ...prev,
                                                sinkingFundIncreasePerUnit: parseFloat(e.target.value) || 0
                                            } ))}
                                        />
                                    </div>
                                    <div className="space-y-1">
                                        <Label className="text-xs">Defer capital works (years)</Label>
                                        <Input
                                            type="number"
                                            value={whatIfScenario.deferCapitalYears}
                                            onChange={(e) => setWhatIfScenario(prev => ( {
                                                ...prev,
                                                deferCapitalYears: parseInt(e.target.value, 10) || 0
                                            } ))}
                                        />
                                    </div>
                                    <div className="space-y-1">
                                        <Label className="text-xs">Loan amount ($)</Label>
                                        <Input
                                            type="number"
                                            value={whatIfScenario.loanAmount}
                                            onChange={(e) => setWhatIfScenario(prev => ( {
                                                ...prev,
                                                loanAmount: parseFloat(e.target.value) || 0
                                            } ))}
                                        />
                                    </div>
                                    <div className="grid grid-cols-2 gap-3">
                                        <div className="space-y-1">
                                            <Label className="text-xs">Loan rate (decimal)</Label>
                                            <Input
                                                type="number"
                                                step="0.01"
                                                value={whatIfScenario.loanInterestRate}
                                                onChange={(e) => setWhatIfScenario(prev => ( {
                                                    ...prev,
                                                    loanInterestRate: parseFloat(e.target.value) || 0
                                                } ))}
                                            />
                                        </div>
                                        <div className="space-y-1">
                                            <Label className="text-xs">Loan term (years)</Label>
                                            <Input
                                                type="number"
                                                value={whatIfScenario.loanTermYears}
                                                onChange={(e) => setWhatIfScenario(prev => ( {
                                                    ...prev,
                                                    loanTermYears: parseInt(e.target.value, 10) || 0
                                                } ))}
                                            />
                                        </div>
                                    </div>
                                </div>
                                <div className="flex gap-3">
                                    <Button onClick={handleWhatIf} disabled={whatIfRunning} className="flex-1">
                                        {whatIfRunning ? <><Loader2
                                            className="w-4 h-4 mr-2 animate-spin"/>Running…</> : '▶ Run What-If'}
                                    </Button>
                                    <Button variant="outline" onClick={() => {
                                        handleResetWhatIf();
                                        setWhatIfResult(null);
                                    }}>Reset</Button>
                                </div>
                            </Card>
                            {/* ── What-If Results (shown inline after running) ── */}
                            {whatIfResult && (
                                <Card
                                    className="rounded-xl border border-border bg-muted p-6 space-y-4 lg:col-span-3">
                                    <div className="flex items-center justify-between">
                                        <h3 className="text-foreground text-lg font-semibold tracking-tight flex items-center gap-2">
                                            <CheckCircle2 className="w-5 h-5 text-primary"/>
                                            What-If Simulation Results
                                        </h3>
                                        <Button variant="ghost" size="sm"
                                                onClick={() => setWhatIfResult(null)}>Dismiss</Button>
                                    </div>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                        <div className="p-4 rounded-2xl bg-card shadow-sm">
                                            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Special
                                                Levy Risk</p>
                                            <p className="text-2xl font-semibold text-primary">{whatIfResult.special?.probability != null ? `${( whatIfResult.special.probability * 100 ).toFixed(0)}%` : '—'}</p>
                                        </div>
                                        <div className="p-4 rounded-2xl bg-card shadow-sm">
                                            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Median
                                                Special Levy</p>
                                            <p className="text-2xl font-semibold text-primary">{whatIfResult.special?.median_amount != null ? formatCurrency(whatIfResult.special.median_amount) : '—'}</p>
                                        </div>
                                        <div className="p-4 rounded-2xl bg-card shadow-sm">
                                            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Reserve
                                                Score</p>
                                            <p className="text-2xl font-semibold text-primary">{whatIfResult.stability?.reserve_score != null ? Math.round(whatIfResult.stability.reserve_score) : '—'}</p>
                                        </div>
                                        <div className="p-4 rounded-2xl bg-card shadow-sm">
                                            <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-1">Explanation</p>
                                            <p className="text-xs text-muted-foreground leading-relaxed">{whatIfResult.special?.explanation || whatIfResult.stability?.explanation || 'See Risk & Audit tab for full breakdown.'}</p>
                                        </div>
                                    </div>
                                </Card>
                            )}
                        </motion.div>
                    </TabsContent>

                    {/* ── Capital Works Tab ── */}
                    <TabsContent value="capital-works">
                        <motion.div
                            initial={{opacity: 0, y: 20}}
                            animate={{opacity: 1, y: 0}}
                            className="space-y-8"
                        >
                            <SinkingFundPremium
                                data={sinkingFundData}
                                projectionData={sinkingFundProjection}
                                insights={sinkingFundInsights}
                                canEdit={canEditPlan}
                                onSavePlan={handleSaveSinkingFundPlan}
                                onSaveEvents={handleSaveCapitalEvents}
                                loading={sinkingFundLoading}
                            />
                        </motion.div>
                    </TabsContent>

                    {/* ── PDF Ingest Tab ── */}
                    {canIngest && (
                        <TabsContent value="ingest">
                            <motion.div
                                initial={{opacity: 0, y: 20}}
                                animate={{opacity: 1, y: 0}}
                                className="max-w-2xl mx-auto"
                            >
                                <Card
                                    className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
                                    {/* Inverted banner: a deliberate emphasis device, kept, but
                                        expressed with the design system's own inverted pair. The
                                        original was an indigo->violet gradient with white text —
                                        a second accent family that exists nowhere else in the app. */}
                                    <div className="p-10 bg-primary text-primary-foreground relative">
                                        <FileUp className="w-10 h-10 mb-6 text-primary-foreground"/>
                                        <h3 className="text-2xl font-semibold mb-2">Import Financial Statement</h3>
                                        <p className="text-primary-foreground/80 font-medium">Extract and map financial
                                            data directly from PDF reports.</p>
                                    </div>
                                    <CardContent className="p-10 space-y-8">
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                            <div className="space-y-2">
                                                <label
                                                    className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Financial
                                                    Year</label>
                                                <input
                                                    type="text"
                                                    placeholder="e.g. 2026"
                                                    value={pdfYear}
                                                    onChange={(e) => setPdfYear(e.target.value)}
                                                    className="w-full bg-muted border border-border rounded-xl px-4 py-3 text-sm focus:ring-2 focus:ring-ring outline-none"
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <label
                                                    className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Document
                                                    Type</label>
                                                <select
                                                    value={pdfDocType}
                                                    onChange={(e) => setPdfDocType(e.target.value)}
                                                    className="w-full bg-muted border border-border rounded-xl px-4 py-3 text-sm focus:ring-2 focus:ring-ring outline-none"
                                                >
                                                    <option value="budget">Proposed Budget</option>
                                                    <option value="actual">Audited Actuals</option>
                                                    <option value="audit">Full Audit Report</option>
                                                    <option value="statement">Bank Statement</option>
                                                </select>
                                            </div>
                                        </div>

                                        <div className="space-y-2">
                                            <label
                                                className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">PDF
                                                Document</label>
                                            <div
                                                className="border-2 border-dashed border-border rounded-xl p-8 flex flex-col items-center justify-center hover:border-primary/20 transition-colors bg-muted group cursor-pointer relative">
                                                <input
                                                    type="file"
                                                    accept=".pdf"
                                                    onChange={(e) => setPdfFile(e.target.files?.[ 0 ] || null)}
                                                    className="absolute inset-0 opacity-0 cursor-pointer"
                                                />
                                                <FileUp
                                                    className="w-8 h-8 text-muted-foreground group-hover:text-primary mb-2 transition-colors"/>
                                                <p className="text-sm font-bold text-muted-foreground">{pdfFile ? pdfFile.name : 'Click or drag PDF here'}</p>
                                                <p className="text-[10px] font-medium text-muted-foreground mt-1 uppercase">Max
                                                    size 10MB · PDF only</p>
                                            </div>
                                        </div>

                                        <Button
                                            onClick={handlePdfUpload}
                                            disabled={uploadingPdf || !pdfFile}
                                            className="w-full bg-primary hover:bg-primary/90 text-primary-foreground font-semibold rounded-2xl py-8 shadow-xl active:scale-[0.98] transition-all"
                                        >
                                            {uploadingPdf ? (
                                                <>
                                                    <RefreshCw className="mr-2 h-5 w-5 animate-spin"/>
                                                    Analyzing Document...
                                                </>
                                            ) : (
                                                <>
                                                    <ArrowRight className="mr-2 h-5 w-5"/>
                                                    Process Financial Document
                                                </>
                                            )}
                                        </Button>

                                        <p className="text-center text-[10px] text-muted-foreground font-medium">
                                            Note: Our extraction engine uses fuzzy matching to map data to your existing
                                            chart of accounts.
                                        </p>
                                    </CardContent>
                                </Card>
                            </motion.div>
                        </TabsContent>
                    )}
                </AnimatePresence>
            </Tabs>

            {/* ── Footer ── */}
            <footer
                className="pt-10 border-t border-border flex flex-col md:flex-row justify-between items-center gap-4">
                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest">© {new Date().getFullYear()} {selectedBuilding?.name || 'Your Building'} · Financial Intelligence Engine v2.4</p>
                <div className="flex gap-6">
                    <button
                        className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest hover:text-primary transition-colors">Help
                        Center
                    </button>
                    <button
                        className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest hover:text-primary transition-colors">API
                        Docs
                    </button>
                    <button
                        className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest hover:text-primary transition-colors">Audit
                        Policy
                    </button>
                </div>
            </footer>

        </div>
    );
};

export default FinanceIntelligencePage;
