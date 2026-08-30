"use client";

// @featuretrace:levy-financial-year-reports — Single report-generation workbench for building and portfolio finance reports.
// Layer: frontend
// Data flow: ReportsPage -> /finance/reports/{aged-receivables,general-ledger} -> PostgreSQL finance report contracts.
// Related: backend/routers/finance_reports.py
import React, {useEffect, useMemo, useState} from "react";
import {useSearchParams} from "next/navigation";
import {useAuth} from "@/contexts/AuthContext";
import {Badge} from "@/components/ui/badge";
import {Button} from "@/components/ui/button";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "@/components/ui/table";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {AlertTriangle, Building2, Download, FileSpreadsheet, Layers, Loader2, Play} from "lucide-react";

import {formatMoneyFromDollars} from '@/lib/currency';
type ReportKind = string;
type ReportCatalogueItem = {
    report_type: string;
    title: string;
    status: "implemented" | "planned";
    formats: string[];
    source_tables?: string[];
    reference_reports?: string[];
};

const FALLBACK_CATALOGUE: ReportCatalogueItem[] = [
    {report_type: "aged-receivables", title: "Aging", status: "implemented", formats: ["screen", "csv", "xlsx", "pdf", "docx"]},
    {report_type: "general-ledger", title: "General Ledger", status: "implemented", formats: ["screen", "csv", "xlsx", "pdf", "docx"]},
];

const REPORT_DESCRIPTIONS: Record<string, string> = {
    "aged-receivables": "Levy receivables aged from due date with gross outstanding, overdue and credit separation.",
    "general-ledger": "Posted journal lines for the selected Levy Financial Year, grouped by account with running balances.",
    "agm-pack": "Agenda, motions, governance pack and appendices for EC review and owner distribution. Not yet built -- needs the Financial Report and Proposed Budget below bundled with meeting metadata.",
    "financial-statement": "Narrative Financial Intelligence report (income vs budget, cashflow projection, fund health, forecasts). Existing StrataOS module, opened directly from here.",
    "gst-bas-statement": "GST/BAS statement -- annual GST summary, ATO BAS worksheet and quarterly breakdown. Existing StrataOS module, opened directly from here.",
    "bank-transactions": "Trust-account movement schedule with deposits, withdrawals, references and running balance.",
    "proposed-budget": "Budget schedule by fund and category with prior actuals, current budget and proposed amounts.",
    "levy-listing": "Lot-level levy charges, allocations and instalment schedule for the Levy Financial Year.",
    "levy-balance": "Lot-level opening balance, charges, receipts, adjustments, credits and closing balance.",
    "status-report": "Management status pack covering finance, payments, works, compliance and exceptions.",
    "roll-list": "Current roll list with lots, units, entitlement, contacts and ownership-transfer metadata.",
};

const MONEY_FIELDS = new Set(["outstanding", "debit", "credit", "running_balance"]);

function formatValue(key: string, value: unknown) {
    if (value == null || value === "") return "-";
    if (MONEY_FIELDS.has(key) && typeof value === "number") {
        return formatMoneyFromDollars(value);
    }
    return String(value);
}

function formatMoney(value: unknown) {
    if (typeof value !== "number") return "-";
    return formatMoneyFromDollars(value);
}

// Column order/labels mirror the AGING_BUCKETS tuple in
// backend/services/finance_reporting_service.py. StrataOS-native six-tier
// scheme (kept distinct from the five-column Current/30+/60+/90+/120+ layout
// in the supplied reference sample) so a 91-180-day debt stays distinguishable
// from a 181+-day one for arrears/recovery decisions.
const AGING_BUCKET_COLUMNS: Array<{ key: string; label: string }> = [
    {key: "current", label: "Current"},
    {key: "days_1_30", label: "1-30 Days Overdue"},
    {key: "days_31_60", label: "31-60 Days Overdue"},
    {key: "days_61_90", label: "61-90 Days Overdue"},
    {key: "days_91_180", label: "91-180 Days Overdue"},
    {key: "days_181_plus", label: "181+ Days Overdue"},
];

type AgingLotSummaryRow = {
    lot_number: string | null;
    unit_number: string | null;
    owner_name: string | null;
    balance: number;
    last_charge_due_date: string | null;
    buckets: Record<string, { label: string; amount: number }>;
};

function pickColumns(reportKind: ReportKind) {
    if (reportKind === "aged-receivables") {
        return ["lot_number", "unit_number", "fund_code", "quarter_no", "due_date", "bucket", "outstanding", "status"];
    }
    if (reportKind === "general-ledger") {
        return ["effective_on", "entry_number", "account_code", "account_name", "fund_code", "description", "debit", "credit", "running_balance", "status"];
    }
    return ["report_id", "financial_year", "completeness_state", "reconciliation_state"];
}

function stateTone(state?: string) {
    if (state === "complete" || state === "reconciled") return "bg-emerald-50 text-emerald-700 border-emerald-200";
    if (state === "partial" || state === "unreconciled") return "bg-amber-50 text-amber-700 border-amber-200";
    return "bg-slate-100 text-slate-700 border-slate-200";
}

export default function ReportsPage() {
    const {api, availableYears, selectedYear, selectedBuilding, availableBuildings, user, isAdmin, isManager, isECMember} = useAuth();
    // A nav item can deep-link a specific report via ?report= (e.g. the
    // "Financial reports" item → ?report=general-ledger), distinct from the
    // generic "Reports" landing which opens the default aging report.
    const searchParams = useSearchParams();
    const requestedReport = searchParams?.get?.("report") || "";
    const [reportKind, setReportKind] = useState<ReportKind>(requestedReport || "aged-receivables");
    const [financialYear, setFinancialYear] = useState(selectedYear || availableYears?.[0] || String(new Date().getFullYear()));
    const [asOf, setAsOf] = useState("");
    const [lotNumber, setLotNumber] = useState("");
    const [accountCode, setAccountCode] = useState("");
    const [catalogue, setCatalogue] = useState<ReportCatalogueItem[]>(FALLBACK_CATALOGUE);
    const [loading, setLoading] = useState(false);
    const [report, setReport] = useState<any | null>(null);
    const [error, setError] = useState<string | null>(null);

    const canUseReports = isAdmin() || isManager() || isECMember() || user?.role === "admin_staff";
    const visibleYears = useMemo(() => {
        const years = Array.from(new Set([financialYear, ...(availableYears || [])].filter(Boolean)));
        return years.length ? years : [String(new Date().getFullYear())];
    }, [availableYears, financialYear]);

    const selectedReport = catalogue.find((item) => item.report_type === reportKind) || catalogue[0];
    const columns = pickColumns(reportKind);
    const selectedReportImplemented = selectedReport?.status === "implemented";
    const reportRows = Array.isArray(report?.rows) ? report.rows : [];
    const qualityWarnings: string[] = Array.isArray(report?.quality_warnings)
        ? report.quality_warnings.filter((warning: unknown): warning is string => typeof warning === "string" && warning.trim().length > 0)
        : [];

    useEffect(() => {
        if (!canUseReports) return;
        api.get("/finance/reports/catalog")
            .then((response) => {
                const reports = Array.isArray(response.data?.reports) ? response.data.reports : FALLBACK_CATALOGUE;
                setCatalogue(reports);
                if (!reports.some((item: ReportCatalogueItem) => item.report_type === reportKind)) {
                    setReportKind(reports[0]?.report_type || "aged-receivables");
                }
            })
            .catch(() => setCatalogue(FALLBACK_CATALOGUE));
    }, [api, canUseReports, reportKind]);

    const buildParams = () => {
        const params: Record<string, string> = {financial_year: financialYear};
        if (reportKind === "aged-receivables" && asOf) params.as_of = asOf;
        if (lotNumber.trim()) params.lot_number = lotNumber.trim();
        if (reportKind === "general-ledger" && accountCode.trim()) params.account_code = accountCode.trim();
        return params;
    };

    const runReport = async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await api.get(`/finance/reports/payload/${reportKind}`, {params: buildParams()});
            setReport(response.data);
        } catch (err: any) {
            setReport(null);
            setError(err?.response?.data?.detail || "Unable to generate this report.");
        } finally {
            setLoading(false);
        }
    };

    const downloadReport = (format: "csv" | "xlsx" | "pdf" | "docx") => {
        const params = new URLSearchParams(buildParams()).toString();
        const baseUrl = api.defaults.baseURL || "";
        const safeReportKind = encodeURIComponent(reportKind);
        const safeFormat = encodeURIComponent(format);
        window.open(`${baseUrl}/finance/reports/${safeReportKind}.${safeFormat}?${params}`, "_blank", "noopener,noreferrer");
    };

    // Existing StrataOS modules (Financial Report, GST/BAS Statement) consolidated into this
    // workbench as a direct link rather than re-implemented -- see external_link on the report
    // payload. Opened the same way as downloadReport() above for auth-header consistency.
    const openExternalReport = () => {
        if (!report?.external_link) return;
        const baseUrl = api.defaults.baseURL || "";
        window.open(`${baseUrl}${report.external_link}`, "_blank", "noopener,noreferrer");
    };

    if (!canUseReports) {
        return (
            <div className="space-y-4">
                <h1 className="text-2xl font-semibold text-slate-900">Reports</h1>
                <p className="text-sm text-slate-500">Financial reports are not available for your current role.</p>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                <div>
                    <h1 className="text-2xl font-semibold text-slate-900">Financial Reports</h1>
                    <p className="text-sm text-slate-500">
                        Generate implemented Levy Financial Year reports, or preview draft shells for reports still awaiting database contracts.
                    </p>
                </div>
                <div className="flex items-center gap-2 text-sm text-slate-600">
                    <Building2 className="h-4 w-4"/>
                    <span>{selectedBuilding?.name || selectedBuilding?.building_id || "Current building"}</span>
                </div>
            </div>

            <Tabs defaultValue="building" className="space-y-4">
                <TabsList>
                    <TabsTrigger value="building">
                        <FileSpreadsheet className="h-4 w-4"/>
                        Building
                    </TabsTrigger>
                    <TabsTrigger value="portfolio">
                        <Layers className="h-4 w-4"/>
                        Portfolio Pack
                    </TabsTrigger>
                </TabsList>

                <TabsContent value="building" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle>{selectedReport.title}</CardTitle>
                            <CardDescription>
                                {REPORT_DESCRIPTIONS[selectedReport.report_type] || "Structured report generated from declared database contracts."}
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
                                <div className="space-y-2">
                                    <Label>Report</Label>
                                    <Select value={reportKind} onValueChange={(value) => setReportKind(value as ReportKind)}>
                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            {catalogue.map((item) => (
                                                <SelectItem key={item.report_type} value={item.report_type}>
                                                    {item.title} · {item.status}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>Levy Financial Year</Label>
                                    <Select value={financialYear} onValueChange={setFinancialYear}>
                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            {visibleYears.map((year) => (
                                                <SelectItem key={year} value={year}>{year}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>As of</Label>
                                    <Input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} disabled={reportKind !== "aged-receivables"}/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Lot or unit</Label>
                                    <Input value={lotNumber} onChange={(event) => setLotNumber(event.target.value)} placeholder="Optional"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Account code</Label>
                                    <Input value={accountCode} onChange={(event) => setAccountCode(event.target.value)} placeholder="GL only" disabled={reportKind !== "general-ledger"}/>
                                </div>
                            </div>

                            <div className="flex flex-wrap items-center gap-2">
                                <Button onClick={runReport} disabled={loading}>
                                    {loading ? <Loader2 className="h-4 w-4 animate-spin"/> : <Play className="h-4 w-4"/>}
                                    {selectedReportImplemented ? "Generate" : "Preview shell"}
                                </Button>
                                <Badge className={selectedReport.status === "implemented" ? stateTone("complete") : stateTone("partial")}>
                                    {selectedReport.status}
                                </Badge>
                                <Button variant="outline" onClick={() => downloadReport("csv")} disabled={!report || !selectedReport.formats.includes("csv")}>
                                    <Download className="h-4 w-4"/>
                                    CSV
                                </Button>
                                <Button variant="outline" onClick={() => downloadReport("xlsx")} disabled={!report || !selectedReport.formats.includes("xlsx")}>
                                    <Download className="h-4 w-4"/>
                                    XLSX
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={() => (report?.external_link ? openExternalReport() : downloadReport("pdf"))}
                                    disabled={!report || !selectedReport.formats.includes("pdf")}
                                >
                                    <Download className="h-4 w-4"/>
                                    {report?.external_link ? "Open PDF" : selectedReportImplemented ? "PDF" : "PDF shell"}
                                </Button>
                                <Button variant="outline" onClick={() => downloadReport("docx")} disabled={!report || !selectedReport.formats.includes("docx")}>
                                    <Download className="h-4 w-4"/>
                                    {selectedReportImplemented ? "Word draft" : "Word shell"}
                                </Button>
                            </div>
                            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                                <div className="rounded-md border p-3">
                                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                                        {selectedReportImplemented ? "Database contract" : "Required contract"}
                                    </p>
                                    <p className="mt-1 text-sm text-slate-700">{selectedReport.source_tables?.join(", ") || "Pending contract definition"}</p>
                                </div>
                                <div className="rounded-md border p-3">
                                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Report class informed by</p>
                                    <p className="mt-1 text-sm text-slate-700">{selectedReport.reference_reports?.join(", ") || "No prior industry report consulted"}</p>
                                    <p className="mt-1 text-xs text-slate-400">
                                        Used to understand the expected report class and accounting columns only. StrataOS generates its own layout, wording and branding -- it does not duplicate the source report.
                                    </p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-amber-200 bg-amber-50">
                        <CardContent className="flex gap-3 p-4 text-sm text-amber-900">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0"/>
                            <p>
                                Empty, partial or zero rows are not treated as proof that the financial year is complete.
                                Implemented reports must be checked against quality state, database coverage and reconciliation status;
                                planned reports are draft shells only.
                            </p>
                        </CardContent>
                    </Card>

                    {error && (
                        <Card className="border-red-200 bg-red-50">
                            <CardContent className="p-4 text-sm text-red-700">{error}</CardContent>
                        </Card>
                    )}

                    {report && (
                        <div className="space-y-4">
                            <Card className="border-slate-200 bg-slate-50">
                                <CardContent className="flex flex-wrap items-center gap-4 p-4">
                                    {report.logo_url && (
                                        // eslint-disable-next-line @next/next/no-img-element
                                        <img src={report.logo_url} alt="" className="h-10 w-10 rounded object-contain" />
                                    )}
                                    <div>
                                        <p className="text-sm font-semibold text-slate-900">{report.building_name || "StrataOS"}</p>
                                        <p className="text-xs text-slate-500">{report.building_address || ""}</p>
                                    </div>
                                    <div className="ml-auto text-right">
                                        <p className="text-sm font-semibold text-slate-900">{selectedReport.title}</p>
                                        <p className="text-xs text-slate-500">
                                            {report.financial_year_label || report.financial_year} · as of {report.as_of || "-"} · generated {report.generated_at ? new Date(report.generated_at).toLocaleString() : "-"}
                                        </p>
                                    </div>
                                </CardContent>
                            </Card>

                            <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                                <Card>
                                    <CardContent className="p-4">
                                        <p className="text-xs text-slate-500">Financial Year</p>
                                        <p className="font-semibold">{report.financial_year_label || report.financial_year}</p>
                                    </CardContent>
                                </Card>
                                <Card>
                                    <CardContent className="p-4">
                                        <p className="text-xs text-slate-500">Completeness</p>
                                        <Badge className={stateTone(report.completeness_state)}>{report.completeness_state}</Badge>
                                    </CardContent>
                                </Card>
                                <Card>
                                    <CardContent className="p-4">
                                        <p className="text-xs text-slate-500">Reconciliation</p>
                                        <Badge className={stateTone(report.reconciliation_state)}>{report.reconciliation_state}</Badge>
                                    </CardContent>
                                </Card>
                                <Card>
                                    <CardContent className="p-4">
                                        <p className="text-xs text-slate-500">Rows</p>
                                        <p className="font-semibold">{reportRows.length}</p>
                                    </CardContent>
                                </Card>
                            </div>

                            {!!qualityWarnings.length && (
                                <Card className="border-amber-200">
                                    <CardContent className="space-y-1 p-4 text-sm text-amber-800">
                                        {qualityWarnings.map((warning) => <p key={warning}>{warning}</p>)}
                                    </CardContent>
                                </Card>
                            )}

                            {reportKind === "aged-receivables" && Array.isArray(report.lot_summary) && (
                                <Card>
                                    <CardHeader>
                                        <CardTitle>Aged Balance by Lot</CardTitle>
                                        <CardDescription>
                                            One row per lot with a signed balance -- credit reduces the total rather than being stripped out -- so the summary reads the same way a strata manager reads any owner ledger, regardless of source system.
                                        </CardDescription>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="overflow-x-auto">
                                            <Table>
                                                <TableHeader>
                                                    <TableRow>
                                                        <TableHead>Lot</TableHead>
                                                        <TableHead>Unit</TableHead>
                                                        <TableHead>Owner</TableHead>
                                                        <TableHead className="text-right">Balance</TableHead>
                                                        {AGING_BUCKET_COLUMNS.map((bucket) => (
                                                            <TableHead key={bucket.key} className="text-right">{bucket.label}</TableHead>
                                                        ))}
                                                        <TableHead>Last charge due</TableHead>
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    {(report.lot_summary as AgingLotSummaryRow[]).map((lot, index) => (
                                                        <TableRow key={`${lot.lot_number || lot.unit_number || index}`}>
                                                            <TableCell>{lot.lot_number ?? "-"}</TableCell>
                                                            <TableCell>{lot.unit_number ?? "-"}</TableCell>
                                                            <TableCell>{lot.owner_name || "-"}</TableCell>
                                                            <TableCell className="text-right">{formatMoney(lot.balance)}</TableCell>
                                                            {AGING_BUCKET_COLUMNS.map((bucket) => (
                                                                <TableCell key={bucket.key} className="text-right">
                                                                    {formatMoney(lot.buckets?.[bucket.key]?.amount ?? 0)}
                                                                </TableCell>
                                                            ))}
                                                            <TableCell>{lot.last_charge_due_date || "-"}</TableCell>
                                                        </TableRow>
                                                    ))}
                                                    {report.lot_summary.length === 0 && (
                                                        <TableRow>
                                                            <TableCell colSpan={6 + AGING_BUCKET_COLUMNS.length} className="text-center text-sm text-slate-500">
                                                                No lot balances for this Levy Financial Year.
                                                            </TableCell>
                                                        </TableRow>
                                                    )}
                                                </TableBody>
                                            </Table>
                                        </div>
                                    </CardContent>
                                </Card>
                            )}

                            {report.completeness_state === "external_module" ? (
                                <Card>
                                    <CardHeader>
                                        <CardTitle>{selectedReport.title}</CardTitle>
                                        <CardDescription>
                                            This report is generated by an existing StrataOS module, consolidated here as a direct link rather than re-implemented.
                                        </CardDescription>
                                    </CardHeader>
                                    <CardContent className="flex flex-wrap items-center gap-3">
                                        <Button onClick={openExternalReport}>
                                            <Download className="h-4 w-4"/>
                                            Open {selectedReport.title}
                                        </Button>
                                        <p className="text-sm text-slate-500">Opens in a new tab from the module that already generates it.</p>
                                    </CardContent>
                                </Card>
                            ) : (
                                <Card>
                                    <CardHeader>
                                        <CardTitle>{reportKind === "aged-receivables" ? "Levy Charge Detail" : "Preview"}</CardTitle>
                                        <CardDescription>
                                            {report.source} | generated {report.generated_at ? new Date(report.generated_at).toLocaleString() : "-"}
                                        </CardDescription>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="overflow-x-auto">
                                            <Table>
                                                <TableHeader>
                                                    <TableRow>
                                                        {columns.map((column) => <TableHead key={column}>{column.replaceAll("_", " ")}</TableHead>)}
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    {reportRows.slice(0, 100).map((row: any, index: number) => (
                                                        <TableRow key={row.journal_line_id || row.levy_item_id || index}>
                                                            {columns.map((column) => <TableCell key={column}>{formatValue(column, row[column])}</TableCell>)}
                                                        </TableRow>
                                                    ))}
                                                    {reportRows.length === 0 && (
                                                        <TableRow>
                                                            <TableCell colSpan={columns.length} className="text-center text-sm text-slate-500">
                                                                No rows returned for this database contract and filter set.
                                                                {report.completeness_state === "contract_pending" ? " This report type still needs its database query contract." : ""}
                                                            </TableCell>
                                                        </TableRow>
                                                    )}
                                                </TableBody>
                                            </Table>
                                        </div>
                                    </CardContent>
                                </Card>
                            )}
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="portfolio">
                    <Card>
                        <CardHeader>
                            <CardTitle>Strata Management and Stakeholders Pack</CardTitle>
                            <CardDescription>
                                Cross-building report packs will use the same report contracts per building and then add portfolio-level rollups.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                                <div className="rounded-md border p-4">
                                    <p className="text-sm font-semibold">Managed buildings</p>
                                    <p className="mt-1 text-2xl font-semibold">{availableBuildings?.length || 0}</p>
                                </div>
                                <div className="rounded-md border p-4">
                                    <p className="text-sm font-semibold">Required structure</p>
                                    <p className="mt-1 text-sm text-slate-500">Building-level schedules plus portfolio summary, exceptions and stakeholder appendix.</p>
                                </div>
                                <div className="rounded-md border p-4">
                                    <p className="text-sm font-semibold">Status</p>
                                    <p className="mt-1 text-sm text-slate-500">Tasked for implementation after building report contracts are validated.</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
