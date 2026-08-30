// @featuretrace:gst-bas — GST & BAS Ledger page for strata_manager, chairman, super_admin roles.
// Layer: frontend
// Data flow: this page → /api/gst/* endpoints → annual_levies, expense_transactions,
//            water_bills, council_rates, trust_transactions_v2 (building-scoped).
// Related: backend/routers/gst_bas.py
//           backend/models/gst_bas.py
//           backend/services/gst_service.py

import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Badge } from '../../../components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../../components/ui/tabs';
import { Alert, AlertDescription, AlertTitle } from '../../../components/ui/alert';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import {
    AlertCircle,
    AlertTriangle,
    CheckCircle2,
    Download,
    FileText,
    Loader2,
    Receipt,
    TrendingDown,
    TrendingUp,
} from 'lucide-react';
import { toast } from 'sonner';
import {formatMoneyFromDollars} from '@/lib/currency';
// ─── Formatters ───────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: fmtPct
 * Path: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtPct = (n) => {
    const num = typeof n === 'number' ? n : parseFloat(n ?? 0);
    return `${( num * 100 ).toFixed(1)}%`;
};
/**
 * @generated FunctionHeader
 * Function: fmtDate
 * Path: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtDate = (iso) => {
    if (!iso) return '—';
    const [y, m, d] = iso.split('-');
    return `${d}/${m}/${y}`;
};

// ─── Year options ─────────────────────────────────────────────────────────────

const currentYear = new Date().getFullYear();
const YEAR_OPTIONS = Array.from({length: 5}, (_, i) => String(currentYear - i));
const QUARTER_OPTIONS = ['Annual', 'Q1', 'Q2', 'Q3', 'Q4'];
// ─── Subcomponents ────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: SummaryCard
 * Path: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SummaryCard({title, value, subtitle, icon: Icon, variant = 'default'}) {
    const colours = {
        default: 'text-slate-700',
        positive: 'text-red-600',   // owes ATO
        negative: 'text-green-600', // refund due
        info: 'text-blue-600',
        muted: 'text-slate-500',
    };
    return (
        <Card>
            <CardContent className="pt-5 pb-4">
                <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium text-slate-500 uppercase tracking-wide truncate">{title}</p>
                        <p className={`text-2xl font-bold mt-1 ${colours[ variant ]}`}>{value}</p>
                        {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
                    </div>
                    {Icon && (
                        <div className="ml-3 p-2 rounded-full bg-slate-50">
                            <Icon className="h-5 w-5 text-slate-400"/>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: BASFieldRow
 * Path: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function BASFieldRow({field, description, amount, highlight}) {
    return (
        <TableRow className={highlight ? 'bg-blue-50 font-semibold' : ''}>
            <TableCell className="font-mono font-semibold w-16">{field}</TableCell>
            <TableCell className="text-slate-600">{description}</TableCell>
            <TableCell className="text-right font-mono">{formatMoneyFromDollars(amount ?? 0)}</TableCell>
        </TableRow>
    );
}
/**
 * @generated FunctionHeader
 * Function: EmptyState
 * Path: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function EmptyState({message}) {
    return (
        <div className="flex flex-col items-center justify-center py-12 text-slate-400">
            <FileText className="h-10 w-10 mb-3 opacity-40"/>
            <p className="text-sm">{message}</p>
        </div>
    );
}
// ─── Main Page ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: GSTBASLedgerPage
 * Path: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function GSTBASLedgerPage() {
    const {user, api, isAdmin, isStrataAdmin} = useAuth();
    const canAccess = isAdmin() || isStrataAdmin() || user?.role === 'strata_manager';

    const [selectedYear, setSelectedYear] = useState(String(currentYear));
    const [selectedQuarter, setSelectedQuarter] = useState('Annual');
    const [activeTab, setActiveTab] = useState('bas-worksheet');

    const [loading, setLoading] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [error, setError] = useState(null);

    const [settings, setSettings] = useState(null);
    const [summary, setSummary] = useState(null);
    const [worksheet, setWorksheet] = useState(null);
    const [outputTax, setOutputTax] = useState([]);
    const [inputTax, setInputTax] = useState(null);
    const [gstFree, setGstFree] = useState(null);
    const [quarterlyWorksheets, setQuarterlyWorksheets] = useState(null);
    const [incomeTaxPrep, setIncomeTaxPrep] = useState(null);

    const loadData = useCallback(async () => {
        if (!selectedYear) return;
        setLoading(true);
        setError(null);
        try {
            const [settingsRes, summaryRes, worksheetRes, outputRes, inputRes, gstFreeRes, qwRes] =
                await Promise.all([
                    api.get('/gst/settings'),
                    api.get(`/gst/summary?financial_year=${selectedYear}&quarter=${selectedQuarter}`),
                    api.get(`/gst/bas-worksheet?financial_year=${selectedYear}&quarter=${selectedQuarter}`),
                    api.get(`/gst/output-tax?financial_year=${selectedYear}`),
                    api.get(`/gst/input-tax?financial_year=${selectedYear}`),
                    api.get(`/gst/gst-free?financial_year=${selectedYear}`),
                    api.get(`/gst/quarterly-worksheets?financial_year=${selectedYear}`),
                ]);
            setSettings(settingsRes.data);
            setSummary(summaryRes.data);
            setWorksheet(worksheetRes.data);
            setOutputTax(outputRes.data || []);
            setInputTax(inputRes.data);
            setGstFree(gstFreeRes.data);
            setQuarterlyWorksheets(qwRes.data);

            // Income tax prep is optional — don't fail if not available
            try {
                const itRes = await api.get(`/gst/income-tax-prep?financial_year=${selectedYear}`);
                setIncomeTaxPrep(itRes.data);
            } catch {
                setIncomeTaxPrep(null);
            }
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to load GST data.';
            setError(msg);
            toast.error(msg);
        } finally {
            setLoading(false);
        }
    }, [selectedYear, selectedQuarter, api]);

    useEffect(() => {
        if (canAccess) loadData();
    }, [loadData, canAccess]);
    /**
     * @generated FunctionHeader
     * Function: handleExportPDF
     * Path: frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExportPDF = async () => {
        setExporting(true);
        try {
            const response = await api.get(
                `/gst/export/pdf?financial_year=${selectedYear}`,
                {responseType: 'blob'},
            );
            const url = URL.createObjectURL(new Blob([response.data], {type: 'application/pdf'}));
            const a = document.createElement('a');
            a.href = url;
            a.download = `GST_BAS_${selectedYear}.pdf`;
            a.click();
            URL.revokeObjectURL(url);
            toast.success('PDF downloaded successfully.');
        } catch {
            toast.error('Failed to generate PDF.');
        } finally {
            setExporting(false);
        }
    };

    // ── Access guard ──────────────────────────────────────────────────────────
    if (!canAccess) {
        return (
            <div className="p-6">
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertTitle>Access Denied</AlertTitle>
                    <AlertDescription>
                        GST & BAS Ledger is accessible to Strata Managers, Chairman, and Super Admins only.
                    </AlertDescription>
                </Alert>
            </div>
        );
    }

    const isGSTRegistered = settings?.gst_registered ?? true;
    const netPayable = summary?.net_gst_payable ?? 0;
    const netVariant = netPayable > 0 ? 'positive' : netPayable < 0 ? 'negative' : 'default';
    const netLabel = netPayable < 0 ? 'Refund due' : netPayable > 0 ? 'Payable to ATO' : 'Nil';

    return (
        <div className="p-6 space-y-6 max-w-7xl mx-auto">
            {/* ── Header ──────────────────────────────────────────────────── */}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-slate-800 flex items-center gap-2">
                        <Receipt className="h-6 w-6 text-slate-600"/>
                        GST &amp; BAS Ledger
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        Business Activity Statement preparation — Australian Owners Corporation
                    </p>
                </div>
                <div className="flex items-center gap-3 flex-wrap">
                    <Select value={selectedYear} onValueChange={setSelectedYear}>
                        <SelectTrigger className="w-32">
                            <SelectValue placeholder="Year"/>
                        </SelectTrigger>
                        <SelectContent>
                            {YEAR_OPTIONS.map((y) => (
                                <SelectItem key={y} value={y}>{y}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Select value={selectedQuarter} onValueChange={setSelectedQuarter}>
                        <SelectTrigger className="w-28">
                            <SelectValue placeholder="Period"/>
                        </SelectTrigger>
                        <SelectContent>
                            {QUARTER_OPTIONS.map((q) => (
                                <SelectItem key={q} value={q}>{q}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleExportPDF}
                        disabled={exporting || loading}
                    >
                        {exporting ? (
                            <><Loader2 className="h-4 w-4 mr-2 animate-spin"/>Exporting…</>
                        ) : (
                            <><Download className="h-4 w-4 mr-2"/>Export PDF</>
                        )}
                    </Button>
                </div>
            </div>

            {/* ── GST Registration badge ───────────────────────────────────── */}
            {settings && (
                <div className="flex items-center gap-3">
                    {isGSTRegistered ? (
                        <Badge className="bg-green-100 text-green-800 border-green-200 gap-1">
                            <CheckCircle2 className="h-3.5 w-3.5"/>
                            GST Registered — {fmtPct(settings.levy_gst_rate)} on levies
                        </Badge>
                    ) : (
                        <Badge variant="destructive" className="gap-1">
                            <AlertCircle className="h-3.5 w-3.5"/>
                            Not GST Registered
                        </Badge>
                    )}
                </div>
            )}

            {/* ── Not registered warning ───────────────────────────────────── */}
            {settings && !isGSTRegistered && (
                <Alert className="border-amber-200 bg-amber-50">
                    <AlertTriangle className="h-4 w-4 text-amber-600"/>
                    <AlertTitle className="text-amber-800">OC is NOT registered for GST</AlertTitle>
                    <AlertDescription className="text-amber-700">
                        No GST is collected on levies and no input tax credits (ITCs) are claimable.
                        All BAS fields will be zero. Register via the ATO Business Portal if annual
                        turnover reaches or is expected to reach $150,000.
                    </AlertDescription>
                </Alert>
            )}

            {/* ── Error ───────────────────────────────────────────────────── */}
            {error && (
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            )}

            {/* ── Loading skeleton ─────────────────────────────────────────── */}
            {loading && (
                <div className="flex items-center justify-center py-16 text-slate-400">
                    <Loader2 className="h-8 w-8 animate-spin mr-3"/>
                    <span>Loading GST data…</span>
                </div>
            )}

            {!loading && summary && (
                <>
                    {/* ── Summary cards ─────────────────────────────────────── */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                        <SummaryCard
                            title="GST Collected (Output Tax)"
                            value={formatMoneyFromDollars(summary.output_tax)}
                            subtitle={`${fmtPct(summary.effective_gst_rate)} on ${formatMoneyFromDollars(summary.levy_ex_gst)} levies`}
                            icon={TrendingUp}
                            variant="info"
                        />
                        <SummaryCard
                            title="Input Tax Credits"
                            value={formatMoneyFromDollars(summary.itc_total)}
                            subtitle="Claimable on GST-inclusive expenses"
                            icon={TrendingDown}
                            variant="info"
                        />
                        <SummaryCard
                            title="Net GST Payable"
                            value={formatMoneyFromDollars(Math.abs(netPayable))}
                            subtitle={netLabel}
                            icon={Receipt}
                            variant={netVariant}
                        />
                        <SummaryCard
                            title="GST-Free Items"
                            value={formatMoneyFromDollars(summary.gst_free_total)}
                            subtitle="Water + council rates (no ITCs)"
                            icon={FileText}
                            variant="muted"
                        />
                    </div>

                    {/* ── Tabs ──────────────────────────────────────────────── */}
                    <Tabs value={activeTab} onValueChange={setActiveTab}>
                        <TabsList className="flex-wrap h-auto">
                            <TabsTrigger value="bas-worksheet">BAS Worksheet</TabsTrigger>
                            <TabsTrigger value="output-tax">Output Tax</TabsTrigger>
                            <TabsTrigger value="input-tax">Input Tax Credits</TabsTrigger>
                            <TabsTrigger value="gst-free">GST-Free Items</TabsTrigger>
                            <TabsTrigger value="annual-summary">Annual Summary</TabsTrigger>
                            {incomeTaxPrep && (
                                <TabsTrigger value="income-tax">Income Tax Prep</TabsTrigger>
                            )}
                        </TabsList>

                        {/* ── Tab 1: BAS Worksheet ────────────────────────── */}
                        <TabsContent value="bas-worksheet" className="mt-4">
                            <Card>
                                <CardHeader>
                                    <CardTitle className="text-base">
                                        BAS Worksheet — ATO GST Return Fields
                                        {worksheet && (
                                            <span className="ml-2 text-xs font-normal text-slate-500">
                                                {fmtDate(worksheet.period_start)} – {fmtDate(worksheet.period_end)}
                                            </span>
                                        )}
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    {worksheet ? (
                                        <>
                                            <Table>
                                                <TableHeader>
                                                    <TableRow>
                                                        <TableHead className="w-16">Field</TableHead>
                                                        <TableHead>Description</TableHead>
                                                        <TableHead className="text-right">Amount (AUD)</TableHead>
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    <BASFieldRow field="G1"
                                                                 description="Total sales including GST (levy income)"
                                                                 amount={worksheet.G1}/>
                                                    <BASFieldRow field="G2"
                                                                 description="Export sales (N/A for residential OC)"
                                                                 amount={worksheet.G2}/>
                                                    <BASFieldRow field="G3" description="Other GST-free sales"
                                                                 amount={worksheet.G3}/>
                                                    <BASFieldRow field="G10"
                                                                 description="Capital purchases including GST"
                                                                 amount={worksheet.G10}/>
                                                    <BASFieldRow field="G11"
                                                                 description="Non-capital purchases including GST"
                                                                 amount={worksheet.G11}/>
                                                    <BASFieldRow field="1A" description="GST on sales — Output Tax"
                                                                 amount={worksheet.field_1A} highlight/>
                                                    <BASFieldRow field="1B"
                                                                 description="GST credits — Input Tax Credits"
                                                                 amount={worksheet.field_1B} highlight/>
                                                    <BASFieldRow
                                                        field="Net 9"
                                                        description={netPayable >= 0 ? 'Net GST payable to ATO' : 'Net GST refund due from ATO'}
                                                        amount={worksheet.net_9}
                                                        highlight
                                                    />
                                                </TableBody>
                                            </Table>
                                            {worksheet.notes?.length > 0 && (
                                                <div
                                                    className="mt-4 space-y-1.5 p-3 bg-slate-50 rounded-md border text-xs text-slate-500">
                                                    {worksheet.notes.map((note, i) => (
                                                        <p key={i}>• {note}</p>
                                                    ))}
                                                </div>
                                            )}
                                        </>
                                    ) : (
                                        <EmptyState message="No BAS worksheet data available."/>
                                    )}
                                </CardContent>
                            </Card>
                        </TabsContent>

                        {/* ── Tab 2: Output Tax ────────────────────────────── */}
                        <TabsContent value="output-tax" className="mt-4">
                            <Card>
                                <CardHeader>
                                    <CardTitle className="text-base">
                                        Output Tax — GST Collected on Levies ({selectedYear})
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    {outputTax.length > 0 ? (
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Quarter</TableHead>
                                                    <TableHead>Period</TableHead>
                                                    <TableHead className="text-right">Admin (ex-GST)</TableHead>
                                                    <TableHead className="text-right">Sinking (ex-GST)</TableHead>
                                                    <TableHead className="text-right">Total (ex-GST)</TableHead>
                                                    <TableHead className="text-right">GST Rate</TableHead>
                                                    <TableHead className="text-right">GST Amount</TableHead>
                                                    <TableHead className="text-right">Total (inc-GST)</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {outputTax.map((row) => (
                                                    <TableRow
                                                        key={row.quarter}
                                                        className={row.quarter === 'Annual' ? 'bg-blue-50 font-semibold' : ''}
                                                    >
                                                        <TableCell className="font-medium">{row.quarter}</TableCell>
                                                        <TableCell className="text-slate-500 text-xs">
                                                            {fmtDate(row.period_start)} – {fmtDate(row.period_end)}
                                                        </TableCell>
                                                        <TableCell
                                                            className="text-right font-mono">{formatMoneyFromDollars(row.admin_levy_ex_gst)}</TableCell>
                                                        <TableCell
                                                            className="text-right font-mono">{formatMoneyFromDollars(row.sinking_levy_ex_gst)}</TableCell>
                                                        <TableCell
                                                            className="text-right font-mono">{formatMoneyFromDollars(row.total_levy_ex_gst)}</TableCell>
                                                        <TableCell
                                                            className="text-right font-mono">{fmtPct(row.gst_rate)}</TableCell>
                                                        <TableCell
                                                            className="text-right font-mono text-blue-700">{formatMoneyFromDollars(row.gst_amount)}</TableCell>
                                                        <TableCell
                                                            className="text-right font-mono">{formatMoneyFromDollars(row.total_levy_inc_gst)}</TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    ) : (
                                        <EmptyState message="No levy data found for this year."/>
                                    )}
                                </CardContent>
                            </Card>
                        </TabsContent>

                        {/* ── Tab 3: Input Tax Credits ──────────────────────── */}
                        <TabsContent value="input-tax" className="mt-4">
                            <Card>
                                <CardHeader>
                                    <CardTitle className="text-base">
                                        Input Tax Credits (ITCs) — {selectedYear}
                                        {inputTax && (
                                            <Badge variant="outline" className="ml-2 text-blue-700 border-blue-200">
                                                Total: {formatMoneyFromDollars(inputTax.itc_total)}
                                            </Badge>
                                        )}
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    {inputTax?.by_category?.length > 0 ? (
                                        <div className="space-y-6">
                                            {inputTax.by_category.map((cat) => (
                                                <div key={cat.category}>
                                                    <div className="flex items-center justify-between mb-2">
                                                        <h4 className="text-sm font-semibold text-slate-700">{cat.category}</h4>
                                                        <span className="text-sm font-semibold text-blue-700">
                                                            ITC: {formatMoneyFromDollars(cat.itc_total)}
                                                        </span>
                                                    </div>
                                                    <Table>
                                                        <TableHeader>
                                                            <TableRow>
                                                                <TableHead>Date</TableHead>
                                                                <TableHead>Supplier</TableHead>
                                                                <TableHead>Invoice</TableHead>
                                                                <TableHead className="text-right">Amount
                                                                    (ex-GST)</TableHead>
                                                                <TableHead className="text-right">GST</TableHead>
                                                                <TableHead className="text-right">Amount
                                                                    (inc-GST)</TableHead>
                                                            </TableRow>
                                                        </TableHeader>
                                                        <TableBody>
                                                            {cat.items.map((item, j) => (
                                                                <TableRow key={j}>
                                                                    <TableCell
                                                                        className="font-mono text-xs">{fmtDate(item.date)}</TableCell>
                                                                    <TableCell
                                                                        className="text-xs">{item.supplier_name || '—'}</TableCell>
                                                                    <TableCell
                                                                        className="text-xs font-mono">{item.invoice_number || '—'}</TableCell>
                                                                    <TableCell
                                                                        className="text-right font-mono text-xs">{formatMoneyFromDollars(item.amount_ex_gst)}</TableCell>
                                                                    <TableCell
                                                                        className="text-right font-mono text-xs text-blue-600">{formatMoneyFromDollars(item.gst_amount)}</TableCell>
                                                                    <TableCell
                                                                        className="text-right font-mono text-xs">{formatMoneyFromDollars(item.amount_inc_gst)}</TableCell>
                                                                </TableRow>
                                                            ))}
                                                        </TableBody>
                                                    </Table>
                                                </div>
                                            ))}
                                            <div
                                                className="flex justify-end p-3 bg-blue-50 rounded-md border border-blue-100">
                                                <span className="font-semibold text-blue-800">
                                                    Total Input Tax Credits (1B): {formatMoneyFromDollars(inputTax.itc_total)}
                                                </span>
                                            </div>
                                        </div>
                                    ) : (
                                        <EmptyState
                                            message="No GST-inclusive expense transactions found for this year."/>
                                    )}
                                </CardContent>
                            </Card>
                        </TabsContent>

                        {/* ── Tab 4: GST-Free Items ─────────────────────────── */}
                        <TabsContent value="gst-free" className="mt-4">
                            <Card>
                                <CardHeader>
                                    <CardTitle className="text-base">
                                        GST-Free Items — {selectedYear}
                                    </CardTitle>
                                </CardHeader>
                                <CardContent className="space-y-6">
                                    {gstFree && (
                                        <>
                                            <Alert className="border-slate-200 bg-slate-50">
                                                <AlertCircle className="h-4 w-4 text-slate-500"/>
                                                <AlertDescription className="text-slate-600 text-xs">
                                                    {gstFree.note}
                                                </AlertDescription>
                                            </Alert>

                                            {/* Water bills */}
                                            <div>
                                                <div className="flex items-center justify-between mb-2">
                                                    <h4 className="text-sm font-semibold text-slate-700">Icon Water
                                                        (ACT) — Utility Charges</h4>
                                                    <span
                                                        className="text-sm font-semibold">{formatMoneyFromDollars(gstFree.water_total)}</span>
                                                </div>
                                                {gstFree.water_bills?.length > 0 ? (
                                                    <Table>
                                                        <TableHeader>
                                                            <TableRow>
                                                                <TableHead>Period Start</TableHead>
                                                                <TableHead>Description</TableHead>
                                                                <TableHead className="text-right">Amount</TableHead>
                                                                <TableHead className="text-right">GST</TableHead>
                                                            </TableRow>
                                                        </TableHeader>
                                                        <TableBody>
                                                            {gstFree.water_bills.map((item, i) => (
                                                                <TableRow key={i}>
                                                                    <TableCell
                                                                        className="font-mono text-xs">{fmtDate(item.date)}</TableCell>
                                                                    <TableCell
                                                                        className="text-xs">{item.description}</TableCell>
                                                                    <TableCell
                                                                        className="text-right font-mono text-xs">{formatMoneyFromDollars(item.amount_ex_gst)}</TableCell>
                                                                    <TableCell
                                                                        className="text-right text-xs text-slate-400">None
                                                                        (GST-free)</TableCell>
                                                                </TableRow>
                                                            ))}
                                                        </TableBody>
                                                    </Table>
                                                ) : (
                                                    <p className="text-sm text-slate-400 py-3">No water bills found for
                                                        this period.</p>
                                                )}
                                            </div>

                                            {/* Council rates */}
                                            <div>
                                                <div className="flex items-center justify-between mb-2">
                                                    <h4 className="text-sm font-semibold text-slate-700">ACT Revenue
                                                        Office — Council Rates</h4>
                                                    <span
                                                        className="text-sm font-semibold">{formatMoneyFromDollars(gstFree.council_rates_total)}</span>
                                                </div>
                                                {gstFree.council_rates?.length > 0 ? (
                                                    <Table>
                                                        <TableHeader>
                                                            <TableRow>
                                                                <TableHead>Unit</TableHead>
                                                                <TableHead>Description</TableHead>
                                                                <TableHead className="text-right">Total
                                                                    Rates</TableHead>
                                                                <TableHead className="text-right">GST</TableHead>
                                                            </TableRow>
                                                        </TableHeader>
                                                        <TableBody>
                                                            {gstFree.council_rates.map((item, i) => (
                                                                <TableRow key={i}>
                                                                    <TableCell
                                                                        className="font-mono text-xs">{item.description?.split('—')[ 1 ]?.trim() || '—'}</TableCell>
                                                                    <TableCell
                                                                        className="text-xs">{item.description}</TableCell>
                                                                    <TableCell
                                                                        className="text-right font-mono text-xs">{formatMoneyFromDollars(item.amount_ex_gst)}</TableCell>
                                                                    <TableCell
                                                                        className="text-right text-xs text-slate-400">None
                                                                        (GST-free)</TableCell>
                                                                </TableRow>
                                                            ))}
                                                        </TableBody>
                                                    </Table>
                                                ) : (
                                                    <p className="text-sm text-slate-400 py-3">No council rates found
                                                        for this period.</p>
                                                )}
                                            </div>

                                            <div
                                                className="flex justify-between items-center p-3 bg-slate-50 rounded-md border text-sm font-semibold">
                                                <span>Total GST-Free Items (informational)</span>
                                                <span>{formatMoneyFromDollars(gstFree.gst_free_total)}</span>
                                            </div>
                                            <p className="text-xs text-slate-400">
                                                No input tax credits (ITCs) are available on GST-free items. These
                                                amounts are excluded from G11 and 1B.
                                            </p>
                                        </>
                                    )}
                                </CardContent>
                            </Card>
                        </TabsContent>

                        {/* ── Tab 5: Annual Summary ──────────────────────────── */}
                        <TabsContent value="annual-summary" className="mt-4">
                            <div className="space-y-4">
                                <Card>
                                    <CardHeader>
                                        <div className="flex items-center justify-between">
                                            <CardTitle className="text-base">
                                                Annual GST Summary — {selectedYear}
                                            </CardTitle>
                                            <Button
                                                size="sm"
                                                onClick={handleExportPDF}
                                                disabled={exporting}
                                            >
                                                {exporting ? (
                                                    <><Loader2 className="h-4 w-4 mr-2 animate-spin"/>Exporting…</>
                                                ) : (
                                                    <><Download className="h-4 w-4 mr-2"/>Download PDF for ATO / CPA</>
                                                )}
                                            </Button>
                                        </div>
                                    </CardHeader>
                                    <CardContent>
                                        <Table>
                                            <TableBody>
                                                <TableRow>
                                                    <TableCell className="font-medium">Levy Income (ex-GST)</TableCell>
                                                    <TableCell
                                                        className="text-right font-mono">{formatMoneyFromDollars(summary.levy_ex_gst)}</TableCell>
                                                    <TableCell className="text-xs text-slate-400">Admin + Sinking fund
                                                        levies (base)</TableCell>
                                                </TableRow>
                                                <TableRow>
                                                    <TableCell className="font-medium">GST on Levies — Output Tax
                                                        (1A)</TableCell>
                                                    <TableCell
                                                        className="text-right font-mono text-blue-700">{formatMoneyFromDollars(summary.output_tax)}</TableCell>
                                                    <TableCell className="text-xs text-slate-400">Collected from
                                                        owners</TableCell>
                                                </TableRow>
                                                <TableRow>
                                                    <TableCell className="font-medium">Levy Income (inc-GST)</TableCell>
                                                    <TableCell
                                                        className="text-right font-mono">{formatMoneyFromDollars(summary.levy_inc_gst)}</TableCell>
                                                    <TableCell className="text-xs text-slate-400">Owner-payable
                                                        total</TableCell>
                                                </TableRow>
                                                <TableRow>
                                                    <TableCell className="font-medium">Input Tax Credits
                                                        (1B)</TableCell>
                                                    <TableCell
                                                        className="text-right font-mono text-green-700">{formatMoneyFromDollars(summary.itc_total)}</TableCell>
                                                    <TableCell className="text-xs text-slate-400">Claimable on
                                                        expenses</TableCell>
                                                </TableRow>
                                                <TableRow className="bg-blue-50 font-semibold">
                                                    <TableCell>Net GST Payable / (Refundable) — Net 9</TableCell>
                                                    <TableCell
                                                        className={`text-right font-mono font-bold ${netPayable > 0 ? 'text-red-600' : 'text-green-600'}`}>
                                                        {netPayable < 0 ? `(${formatMoneyFromDollars(Math.abs(netPayable))})` : formatMoneyFromDollars(netPayable)}
                                                    </TableCell>
                                                    <TableCell className="text-xs">
                                                        {netPayable > 0 ? 'Payable to ATO' : netPayable < 0 ? 'Refund due from ATO' : 'Nil balance'}
                                                    </TableCell>
                                                </TableRow>
                                                <TableRow>
                                                    <TableCell className="font-medium text-slate-500">GST-Free Items
                                                        (informational)</TableCell>
                                                    <TableCell
                                                        className="text-right font-mono text-slate-400">{formatMoneyFromDollars(summary.gst_free_total)}</TableCell>
                                                    <TableCell className="text-xs text-slate-400">Water + council rates
                                                        — no ITCs</TableCell>
                                                </TableRow>
                                            </TableBody>
                                        </Table>
                                    </CardContent>
                                </Card>

                                {/* Quarterly breakdown */}
                                {quarterlyWorksheets?.quarterly?.length > 0 && (
                                    <Card>
                                        <CardHeader>
                                            <CardTitle className="text-base">Quarterly BAS Summary</CardTitle>
                                        </CardHeader>
                                        <CardContent>
                                            <Table>
                                                <TableHeader>
                                                    <TableRow>
                                                        <TableHead>Quarter</TableHead>
                                                        <TableHead>Period</TableHead>
                                                        <TableHead className="text-right">G1 (Sales incl
                                                            GST)</TableHead>
                                                        <TableHead className="text-right">1A (Output Tax)</TableHead>
                                                        <TableHead className="text-right">1B (ITCs)</TableHead>
                                                        <TableHead className="text-right">Net 9</TableHead>
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    {quarterlyWorksheets.quarterly.map((qws) => (
                                                        <TableRow key={qws.quarter}>
                                                            <TableCell className="font-medium">{qws.quarter}</TableCell>
                                                            <TableCell className="text-xs text-slate-500">
                                                                {fmtDate(qws.period_start)} – {fmtDate(qws.period_end)}
                                                            </TableCell>
                                                            <TableCell
                                                                className="text-right font-mono">{formatMoneyFromDollars(qws.G1)}</TableCell>
                                                            <TableCell
                                                                className="text-right font-mono text-blue-700">{formatMoneyFromDollars(qws.field_1A)}</TableCell>
                                                            <TableCell
                                                                className="text-right font-mono text-green-700">{formatMoneyFromDollars(qws.field_1B)}</TableCell>
                                                            <TableCell
                                                                className={`text-right font-mono font-semibold ${qws.net_9 > 0 ? 'text-red-600' : 'text-green-600'}`}>
                                                                {formatMoneyFromDollars(qws.net_9)}
                                                            </TableCell>
                                                        </TableRow>
                                                    ))}
                                                </TableBody>
                                            </Table>
                                        </CardContent>
                                    </Card>
                                )}

                                {/* Filing notes */}
                                <Card>
                                    <CardHeader>
                                        <CardTitle className="text-base">Filing Guidance</CardTitle>
                                    </CardHeader>
                                    <CardContent className="text-sm text-slate-600 space-y-2">
                                        <p>• BAS is lodged quarterly (or annually for small turnover) via the ATO
                                            Business Portal or through a registered BAS agent.</p>
                                        <p>• The OC must register for GST if annual turnover reaches or is expected to
                                            reach <strong>$150,000</strong> (non-profit body threshold).</p>
                                        <p>• Retain all tax invoices and records for a minimum of <strong>5
                                            years</strong> from the date of lodgement.</p>
                                        <p>• The PDF export includes a BAS worksheet, quarterly breakdown, itemised
                                            ITCs, and income tax preparation notes — suitable for your CPA or BAS agent
                                            for July filing.</p>
                                        <p className="text-xs text-slate-400 pt-2">
                                            <strong>Disclaimer:</strong> This system generates data as a preparation aid
                                            only. Always verify figures with your registered tax agent or BAS agent
                                            before lodging. This is NOT a lodged BAS or official ATO document.
                                        </p>
                                    </CardContent>
                                </Card>
                            </div>
                        </TabsContent>

                        {/* ── Tab 6: Income Tax Prep (conditional) ─────────── */}
                        {incomeTaxPrep && (
                            <TabsContent value="income-tax" className="mt-4">
                                <Card>
                                    <CardHeader>
                                        <CardTitle className="text-base">
                                            Annual Income Tax Preparation — {selectedYear}
                                        </CardTitle>
                                    </CardHeader>
                                    <CardContent className="space-y-4">
                                        <Alert className="border-amber-200 bg-amber-50">
                                            <AlertTriangle className="h-4 w-4 text-amber-600"/>
                                            <AlertTitle className="text-amber-800">Annual Company Tax Return (ATO NAT
                                                0656)</AlertTitle>
                                            <AlertDescription className="text-amber-700 text-xs mt-1">
                                                Owners Corporations are treated as <strong>public companies</strong> for
                                                income tax.
                                                Mutual income (levies from members) is exempt. Non-mutual income (bank
                                                interest,
                                                common area rentals) is assessable. File NAT 0656 with your CPA if
                                                assessable income exists.
                                            </AlertDescription>
                                        </Alert>

                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Income Type</TableHead>
                                                    <TableHead>Tax Treatment</TableHead>
                                                    <TableHead className="text-right">Amount (AUD)</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                <TableRow>
                                                    <TableCell>Strata Levies (Admin + Sinking)</TableCell>
                                                    <TableCell><Badge variant="outline"
                                                                      className="text-green-700 border-green-200">Mutual
                                                        — Exempt</Badge></TableCell>
                                                    <TableCell
                                                        className="text-right font-mono">{formatMoneyFromDollars(summary?.levy_ex_gst ?? 0)}</TableCell>
                                                </TableRow>
                                                {incomeTaxPrep.interest_income > 0 && (
                                                    <TableRow className="bg-amber-50">
                                                        <TableCell>Trust Account Interest Income</TableCell>
                                                        <TableCell><Badge variant="outline"
                                                                          className="text-amber-700 border-amber-200">Non-Mutual
                                                            — Assessable</Badge></TableCell>
                                                        <TableCell
                                                            className="text-right font-mono text-amber-700">{formatMoneyFromDollars(incomeTaxPrep.interest_income)}</TableCell>
                                                    </TableRow>
                                                )}
                                                {incomeTaxPrep.other_non_mutual_income > 0 && (
                                                    <TableRow className="bg-amber-50">
                                                        <TableCell>Other Non-Mutual Income</TableCell>
                                                        <TableCell><Badge variant="outline"
                                                                          className="text-amber-700 border-amber-200">Non-Mutual
                                                            — Assessable</Badge></TableCell>
                                                        <TableCell
                                                            className="text-right font-mono text-amber-700">{formatMoneyFromDollars(incomeTaxPrep.other_non_mutual_income)}</TableCell>
                                                    </TableRow>
                                                )}
                                                <TableRow className="font-semibold bg-slate-50">
                                                    <TableCell>Total Assessable Income</TableCell>
                                                    <TableCell><Badge variant="outline" className="text-slate-600">File
                                                        NAT 0656 if &gt; $0</Badge></TableCell>
                                                    <TableCell
                                                        className="text-right font-mono">{formatMoneyFromDollars(incomeTaxPrep.total_assessable_income)}</TableCell>
                                                </TableRow>
                                            </TableBody>
                                        </Table>

                                        <div
                                            className="text-xs text-slate-500 space-y-1 p-3 bg-slate-50 rounded-md border">
                                            <p>• <strong>Mutual income</strong>: Levies and contributions from lot
                                                owners — generally exempt from income tax under the mutuality principle.
                                            </p>
                                            <p>• <strong>Non-mutual income</strong>: Income earned from non-members
                                                (bank interest, leasing common property, parking fees to visitors) —
                                                taxable at company rate.</p>
                                            <p>• File ATO Company Tax Return (NAT 0656) by 31 October each year if
                                                assessable income exists (or via tax agent extension).</p>
                                        </div>
                                    </CardContent>
                                </Card>
                            </TabsContent>
                        )}
                    </Tabs>
                </>
            )}
        </div>
    );
}
