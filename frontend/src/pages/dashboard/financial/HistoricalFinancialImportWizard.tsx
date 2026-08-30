// @featuretrace:financial-onboarding — gap-tolerant historical financial data import wizard
//   (building-agnostic): upload CSV/JSON -> preview coverage/gaps -> generate merged
//   income+expense manifest -> submit for review.
// Layer: frontend
// Data flow: upload -> POST .../import-financial-data -> POST .../reconstruction-batches ->
//            .../extract -> .../generate-preview -> .../submit-review. (building-scoped)
// Related: HistoricalReconstructionPage.tsx, financial_onboarding.py,
//          gap_tolerant_financial_import.py, budget_levy_generator.py, expense_category_generator.py.
// Toggle: historical_financial_reconstruction
//
// Full data flow detail: HistoricalFinancialImportWizard -> POST
// /financials/onboarding/building/{id}/import-financial-data (annual_levies/levy_categories
// upsert) -> POST .../reconstruction-batches (draft) -> POST .../reconstruction-batches/{id}/extract
// -> POST .../generate-preview (dry-run, merged income+expense manifest) -> POST .../submit-review
// (needs_review) — dual-control Review/Approve/Reject then happens on the Batches tab
// (HistoricalReconstructionPage.tsx), under a separate login, by design.
"use client";

import React, {useCallback, useRef, useState} from "react";
import {AlertCircle, CheckCircle2, FileText, Loader2, Upload, X} from "lucide-react";
import {useAuth} from "../../../contexts/AuthContext";
import {Alert, AlertDescription, AlertTitle} from "../../../components/ui/alert";
import {Badge} from "../../../components/ui/badge";
import {Button} from "../../../components/ui/button";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "../../../components/ui/card";
import {Input} from "../../../components/ui/input";
import {Label} from "../../../components/ui/label";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from "../../../components/ui/table";
import {toast} from "sonner";

import {formatMoneyFromCents} from '@/lib/currency';
const GENERATE_FROM_BUDGET_METHOD = "generate_from_budget_v1";
const ACCEPTED_EXTENSIONS = [".csv", ".json"];


interface CoverageRow {
    year: number;
    proposed_admin_present: boolean;
    proposed_sinking_present: boolean;
    closing_balance_admin_present: boolean;
    closing_balance_sinking_present: boolean;
    category_count: number;
}

const CoverageCell = ({present}: { present: boolean }) => (
    present ? (
        <CheckCircle2 className="h-4 w-4 text-emerald-600 mx-auto"/>
    ) : (
        <span className="text-muted-foreground text-xs">—</span>
    )
);

/** Generic CSV/JSON drag-and-drop upload zone. Written fresh — no existing dropzone in this
 * codebase supports both file types generically (FinancialDataManagementPage.jsx's DropZone is
 * CSV-only by construction; DemoBankTransactionsPage.tsx's is a plain non-drag <input>). */
const FileDropZone = ({file, onFileSelected}: { file: File | null; onFileSelected: (f: File | null) => void }) => {
    const [dragOver, setDragOver] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

    const accept = (name: string) => ACCEPTED_EXTENSIONS.some((ext) => name.toLowerCase().endsWith(ext));

    const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setDragOver(false);
        const dropped = e.dataTransfer.files[0];
        if (dropped && accept(dropped.name)) {
            onFileSelected(dropped);
        } else {
            toast.error("Please drop a CSV or JSON file.");
        }
    }, [onFileSelected]);

    return (
        <div
            data-testid="import-wizard-dropzone"
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
                dragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-primary/50"
            }`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => inputRef.current?.click()}
        >
            <Upload className="h-8 w-8 text-muted-foreground mx-auto mb-3"/>
            {file ? (
                <div className="flex items-center justify-center gap-2">
                    <FileText className="h-4 w-4 text-primary"/>
                    <span className="font-medium">{file.name}</span>
                    <span className="text-sm text-muted-foreground">({(file.size / 1024).toFixed(1)} KB)</span>
                    <button
                        onClick={(e) => { e.stopPropagation(); onFileSelected(null); }}
                        className="ml-1 text-muted-foreground hover:text-destructive"
                    >
                        <X className="h-3 w-3"/>
                    </button>
                </div>
            ) : (
                <>
                    <p className="text-sm text-muted-foreground">Drag and drop your CSV or JSON file here, or click to browse</p>
                    <p className="text-xs text-muted-foreground mt-1">.csv or .json — gaps are expected and fine</p>
                </>
            )}
            <input
                ref={inputRef} type="file" accept=".csv,.json" className="hidden"
                data-testid="import-wizard-file-input"
                onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) onFileSelected(f);
                }}
            />
        </div>
    );
};

interface HistoricalFinancialImportWizardProps {
    buildingId: string;
    onBatchSubmitted?: () => void;
}
/**
 * @generated FunctionHeader
 * Function: HistoricalFinancialImportWizard
 * Path: frontend/src/pages/dashboard/financial/HistoricalFinancialImportWizard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function HistoricalFinancialImportWizard({buildingId, onBatchSubmitted}: HistoricalFinancialImportWizardProps) {
    const {api} = useAuth() as any;

    const [step, setStep] = useState(1);
    const currentYear = new Date().getFullYear();
    const [fromYear, setFromYear] = useState(currentYear - 2);
    const [toYear, setToYear] = useState(currentYear);

    const [file, setFile] = useState<File | null>(null);
    const [uploading, setUploading] = useState(false);
    const [importResult, setImportResult] = useState<any>(null);
    const [importError, setImportError] = useState<string | null>(null);

    const [generating, setGenerating] = useState(false);
    const [previewResult, setPreviewResult] = useState<any>(null);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [batchId, setBatchId] = useState<string | null>(null);

    const [submitting, setSubmitting] = useState(false);
    const [submitted, setSubmitted] = useState(false);

    const batchesBase = `/financials/onboarding/building/${buildingId}/reconstruction-batches`;

    const resetFromStep = (targetStep: number) => {
        if (targetStep <= 2) { setImportResult(null); setImportError(null); }
        if (targetStep <= 3) { setPreviewResult(null); setPreviewError(null); setBatchId(null); }
        if (targetStep <= 4) setSubmitted(false);
        setStep(targetStep);
    };

    const handleUpload = useCallback(async () => {
        if (!file) return;
        setUploading(true);
        setImportError(null);
        try {
            const formData = new FormData();
            formData.append("file", file);
            const res = await api.post(
                `/financials/onboarding/building/${buildingId}/import-financial-data`,
                formData,
                {headers: {"Content-Type": "multipart/form-data"}},
            );
            setImportResult(res.data);
            setStep(3);
            toast.success(`Imported: ${res.data.fund_rows_written} budget row(s), ${res.data.category_rows_written} category row(s).`);
        } catch (err: any) {
            const msg = err?.response?.data?.detail ?? "Failed to import financial data.";
            setImportError(msg);
            toast.error(msg);
        } finally {
            setUploading(false);
        }
    }, [api, buildingId, file]);

    const handleGeneratePreview = useCallback(async () => {
        setGenerating(true);
        setPreviewError(null);
        try {
            // Create -> extract -> preview, in one step for the wizard's flow. The batch stays
            // in "extracted" (nothing persisted/reviewable yet) until Submit for Review below.
            const createRes = await api.post(batchesBase, {
                financial_year_start: fromYear,
                financial_year_end: toYear,
                reconstruction_method: GENERATE_FROM_BUDGET_METHOD,
                source_document_ids: [],
                is_test_data: false,
            });
            const newBatchId = createRes.data.batch_id;
            await api.post(`${batchesBase}/${newBatchId}/extract`);
            const previewRes = await api.post(`${batchesBase}/${newBatchId}/generate-preview`);
            setBatchId(newBatchId);
            setPreviewResult(previewRes.data);
            setStep(4);
        } catch (err: any) {
            const msg = err?.response?.data?.detail ?? "Failed to generate a preview from the uploaded data.";
            setPreviewError(msg);
            toast.error(msg);
        } finally {
            setGenerating(false);
        }
    }, [api, batchesBase, fromYear, toYear]);

    const handleSubmitForReview = useCallback(async () => {
        if (!batchId) return;
        setSubmitting(true);
        try {
            await api.post(`${batchesBase}/${batchId}/submit-review`);
            setSubmitted(true);
            toast.success("Submitted for review — a different admin must now review and approve it on the Reconstruction Batches tab.");
            onBatchSubmitted?.();
        } catch (err: any) {
            toast.error(err?.response?.data?.detail ?? "Failed to submit batch for review.");
        } finally {
            setSubmitting(false);
        }
    }, [api, batchesBase, batchId, onBatchSubmitted]);

    const coverage: CoverageRow[] = importResult?.coverage || [];

    return (
        <div className="space-y-6" data-testid="historical-financial-import-wizard">
            {/* Step indicator */}
            <div className="flex items-center gap-2 text-sm">
                {[
                    {n: 1, label: "Financial years"},
                    {n: 2, label: "Upload data"},
                    {n: 3, label: "Review coverage"},
                    {n: 4, label: "Generate & submit"},
                ].map(({n, label}, i) => (
                    <React.Fragment key={n}>
                        {i > 0 && <span className="text-muted-foreground">→</span>}
                        <Badge
                            variant={step === n ? "default" : "outline"}
                            className={step > n ? "opacity-60" : ""}
                        >
                            {n}. {label}
                        </Badge>
                    </React.Fragment>
                ))}
            </div>

            {/* Step 1: financial year range */}
            {step === 1 && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Financial year range</CardTitle>
                        <CardDescription>
                            Which years does your upload cover? Gaps within this range (a year with no proposed
                            budget, no closing balance, or no category spend) are expected and handled — you don't
                            need complete data for every year.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="grid grid-cols-2 gap-4 max-w-md">
                            <div className="space-y-2">
                                <Label htmlFor="import-wizard-from-year">From year</Label>
                                <Input id="import-wizard-from-year" type="number" value={fromYear}
                                       onChange={(e) => setFromYear(Number(e.target.value))}/>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="import-wizard-to-year">To year</Label>
                                <Input id="import-wizard-to-year" type="number" value={toYear}
                                       onChange={(e) => setToYear(Number(e.target.value))}/>
                            </div>
                        </div>
                        <Button onClick={() => setStep(2)} disabled={fromYear > toYear}>
                            Continue
                        </Button>
                        {fromYear > toYear && (
                            <p className="text-sm text-red-600">"From year" must not be after "To year".</p>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Step 2: upload */}
            {step === 2 && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Upload historical financial data</CardTitle>
                        <CardDescription>
                            One CSV or JSON file carrying proposed Admin/Sinking budget, closing balances, and
                            actual category spend — any subset, any combination of years. Columns: <code>year,
                            fund_type, proposed_amount, closing_balance, category_name,
                            category_proposed_amount, category_actual_amount</code>.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <FileDropZone file={file} onFileSelected={setFile}/>
                        {importError && (
                            <Alert variant="destructive">
                                <AlertCircle className="h-4 w-4"/>
                                <AlertTitle>Import failed</AlertTitle>
                                <AlertDescription>{importError}</AlertDescription>
                            </Alert>
                        )}
                        <div className="flex gap-2">
                            <Button variant="outline" onClick={() => resetFromStep(1)}>Back</Button>
                            <Button onClick={handleUpload} disabled={!file || uploading}>
                                {uploading && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                                Upload
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Step 3: coverage preview */}
            {step === 3 && importResult && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Data coverage</CardTitle>
                        <CardDescription>
                            What's present per year, before generating anything. Missing cells are gaps — the
                            generator will skip them with a warning, not fail.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        {importResult.row_warnings?.length > 0 && (
                            <Alert>
                                <AlertCircle className="h-4 w-4"/>
                                <AlertTitle>{importResult.row_warnings.length} row(s) skipped during import</AlertTitle>
                                <AlertDescription>
                                    <ul className="text-xs list-disc ml-4 mt-1 space-y-0.5">
                                        {importResult.row_warnings.slice(0, 10).map((w: string, i: number) => (
                                            <li key={i}>{w}</li>
                                        ))}
                                        {importResult.row_warnings.length > 10 && (
                                            <li>…and {importResult.row_warnings.length - 10} more</li>
                                        )}
                                    </ul>
                                </AlertDescription>
                            </Alert>
                        )}
                        <Table data-testid="import-wizard-coverage-grid">
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Year</TableHead>
                                    <TableHead className="text-center">Proposed Admin</TableHead>
                                    <TableHead className="text-center">Proposed Sinking</TableHead>
                                    <TableHead className="text-center">Closing Bal. (Admin)</TableHead>
                                    <TableHead className="text-center">Closing Bal. (Sinking)</TableHead>
                                    <TableHead className="text-center">Categories</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {coverage.map((row) => (
                                    <TableRow key={row.year}>
                                        <TableCell className="font-medium">{row.year}</TableCell>
                                        <TableCell className="text-center"><CoverageCell present={row.proposed_admin_present}/></TableCell>
                                        <TableCell className="text-center"><CoverageCell present={row.proposed_sinking_present}/></TableCell>
                                        <TableCell className="text-center"><CoverageCell present={row.closing_balance_admin_present}/></TableCell>
                                        <TableCell className="text-center"><CoverageCell present={row.closing_balance_sinking_present}/></TableCell>
                                        <TableCell className="text-center text-sm">{row.category_count}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        <div className="flex gap-2">
                            <Button variant="outline" onClick={() => resetFromStep(2)}>Upload more data</Button>
                            <Button onClick={handleGeneratePreview} disabled={generating}>
                                {generating && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                                Generate preview for {fromYear}–{toYear}
                            </Button>
                        </div>
                        {previewError && (
                            <Alert variant="destructive">
                                <AlertCircle className="h-4 w-4"/>
                                <AlertTitle>Generation failed</AlertTitle>
                                <AlertDescription>{previewError}</AlertDescription>
                            </Alert>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Step 4: generated preview + submit for review */}
            {step === 4 && previewResult && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Generated preview</CardTitle>
                        <CardDescription>
                            Dry-run — nothing is persisted yet. Submitting for review persists this manifest and
                            requires a different admin to review and approve it before anything reaches Demo Bank.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Alert className="border-amber-200 bg-amber-50">
                            <AlertCircle className="h-4 w-4 text-amber-700"/>
                            <AlertTitle className="text-amber-800">These are estimates, not confirmed transactions</AlertTitle>
                            <AlertDescription className="text-amber-800">
                                Income rows are <strong>estimated receipts</strong> generated from the proposed levy
                                assuming full on-time payment — not evidence anyone actually paid on the date shown.
                                Expense rows are <strong>annual category summaries</strong> from the reported totals
                                you uploaded, dated at financial-year end — not individual dated invoices. Every row is tagged
                                "Reconstructed / Estimated" once posted (visible on the Reconciliation page) so it
                                stays distinguishable from real bank-verified transactions.
                            </AlertDescription>
                        </Alert>
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                            <div className="rounded-lg border p-3">
                                <p className="text-xs text-muted-foreground">Transactions</p>
                                <p className="text-lg font-semibold">{previewResult.expected_transaction_count}</p>
                            </div>
                            <div className="rounded-lg border p-3">
                                <p className="text-xs text-muted-foreground">Income (credit)</p>
                                <p className="text-lg font-semibold text-emerald-700">{formatMoneyFromCents(previewResult.expected_credit_cents)}</p>
                            </div>
                            <div className="rounded-lg border p-3">
                                <p className="text-xs text-muted-foreground">Expenses (debit)</p>
                                <p className="text-lg font-semibold text-red-700">{formatMoneyFromCents(previewResult.expected_debit_cents)}</p>
                            </div>
                        </div>

                        {previewResult.fund_balance_reconciliation?.length > 0 && (
                            <div className="space-y-2">
                                <p className="text-sm font-medium">Fund balance reconciliation</p>
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Account</TableHead>
                                            <TableHead className="text-right">Opening</TableHead>
                                            <TableHead className="text-right">+ Income</TableHead>
                                            <TableHead className="text-right">− Expenses</TableHead>
                                            <TableHead className="text-right">Declared Closing</TableHead>
                                            <TableHead>Matches</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {previewResult.fund_balance_reconciliation.map((line: any, i: number) => (
                                            <TableRow key={i} className={!line.matches_expected_closing ? "bg-amber-50" : ""}>
                                                <TableCell className="text-sm">{line.account_ref}</TableCell>
                                                <TableCell className="text-right text-sm">{formatMoneyFromCents(line.opening_balance_cents)}</TableCell>
                                                <TableCell className="text-right text-sm text-emerald-700">{formatMoneyFromCents(line.reconstructed_credits_cents)}</TableCell>
                                                <TableCell className="text-right text-sm text-red-700">{formatMoneyFromCents(line.reconstructed_debits_cents)}</TableCell>
                                                <TableCell className="text-right text-sm">{formatMoneyFromCents(line.closing_balance_cents)}</TableCell>
                                                <TableCell>{line.matches_expected_closing ? "✓" : "⚠"}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        )}

                        {previewResult.warnings?.length > 0 && (
                            <Alert>
                                <AlertCircle className="h-4 w-4"/>
                                <AlertTitle>Warnings</AlertTitle>
                                <AlertDescription>
                                    <ul className="text-xs list-disc ml-4 mt-1 space-y-0.5">
                                        {previewResult.warnings.map((w: string, i: number) => (<li key={i}>{w}</li>))}
                                    </ul>
                                </AlertDescription>
                            </Alert>
                        )}

                        {submitted ? (
                            <Alert>
                                <CheckCircle2 className="h-4 w-4"/>
                                <AlertTitle>Submitted for review</AlertTitle>
                                <AlertDescription>
                                    Batch <code>{batchId}</code> is now in "needs_review" on the Reconstruction
                                    Batches tab. A different admin must review and approve it before it reaches
                                    Demo Bank.
                                </AlertDescription>
                            </Alert>
                        ) : (
                            <div className="flex gap-2">
                                <Button variant="outline" onClick={() => resetFromStep(3)}>Back</Button>
                                <Button onClick={handleSubmitForReview} disabled={submitting}>
                                    {submitting && <Loader2 className="h-4 w-4 animate-spin mr-1"/>}
                                    Submit for Review
                                </Button>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
