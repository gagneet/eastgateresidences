// @featuretrace:onboarding — 32-step new-scheme onboarding wizard (Track A / Track B).
// Layer: frontend
// Data flow: wizard UI → POST /onboarding/scheme/start → PATCH /step → CSV uploads → /finalize.
// Related: backend/routers/onboarding.py, docs/architecture/adr/ADR-023_phase_f_zero_reset.md
// Scope: (building-scoped)
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { Button } from '../../../components/ui/button';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import {
    AlertTriangle, ArrowLeft, Banknote, Building2, CheckCircle2, ChevronRight,
    FileText, GitBranch, History, Layers, Loader2, Mail, PenLine, Rocket, Scale,
    Settings2, Shield, Upload, Users,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { toast } from 'sonner';

// Track A ("Current Balances" / "Current Levy Schedule" manual-entry steps) has no CSV
// upload of its own — unlike Track B's import-opening-balances, nothing else ever turns
// this form data into the `funds` array finalize's clean-state genesis-posting path reads.
// Called right before finalize to PATCH it into step_data in the exact shape the backend
// already understands, so manually-entered balances actually reach the ledger instead of
// being silently discarded.
//
// Safe to call unconditionally regardless of which track the user picked: Track A and
// Track B render mutually-exclusive step lists (see getActiveSteps() below — a session is
// built from either TRACK_A_STEPS or TRACK_B_STEPS, never both), so a Track B session can
// never have `balance_date`/`admin_fund_balance` in stepData in the first place — the
// `!flat.balance_date` check below is what makes this a no-op for Track B, not a check
// against Track B's own CSV-import result (that result lives in the separate
// `uploadResults` state, never merged into stepData/flatStepData).
//
// Module-scope (not a component closure) so it's directly unit-testable — see
// tests/frontend/unit/pages/dashboard/admin/OnboardingWizard.test.tsx.
export function buildCurrentBalancesFunds(flat) {
    const adminDollars = parseFloat(flat.admin_fund_balance);
    if (!flat.balance_date || Number.isNaN(adminDollars)) return null;
    const toCents = (dollars) => Math.round(parseFloat(dollars) * 100);
    const funds = [{ fund_type: 'admin', opening_balance_cents: toCents(flat.admin_fund_balance), as_at_date: flat.balance_date }];
    const sinkingDollars = parseFloat(flat.sinking_fund_balance);
    if (!Number.isNaN(sinkingDollars)) {
        funds.push({ fund_type: 'sinking', opening_balance_cents: toCents(flat.sinking_fund_balance), as_at_date: flat.balance_date });
    }
    const trustDollars = parseFloat(flat.trust_balance);
    if (!Number.isNaN(trustDollars)) {
        const fundsTotalCents = funds.reduce((sum, f) => sum + f.opening_balance_cents, 0);
        if (Math.abs(fundsTotalCents - toCents(flat.trust_balance)) > 1) {
            toast.warning(`Trust account balance ($${trustDollars.toFixed(2)}) doesn't match Admin + Sinking fund totals ($${(fundsTotalCents / 100).toFixed(2)}). Proceeding, but please verify this after go-live.`);
        }
    }
    return funds;
}

// ── Step definitions ───────────────────────────────────────────────────────────
// Composed into Track A or Track B sequences by getActiveSteps().

const SETUP_STEPS = [
    { key: 'org_details',           name: 'Organisation & Scheme',  phase: 'Setup',         type: 'form',    icon: Building2 },
    { key: 'scheme_config',         name: 'Scheme Configuration',   phase: 'Setup',         type: 'form',    icon: Settings2 },
    { key: 'fund_structure',        name: 'Fund Structure',         phase: 'Setup',         type: 'form',    icon: Layers },
    { key: 'bank_accounts',         name: 'Bank Accounts',          phase: 'Setup',         type: 'form',    icon: Banknote },
    { key: 'insurance_overview',    name: 'Insurance Overview',     phase: 'Setup',         type: 'form',    icon: Shield },
    { key: 'lot_import',            name: 'Import Lots',            phase: 'Lot Register',  type: 'upload',  icon: Upload },
    { key: 'review_lots',           name: 'Review Lots',            phase: 'Lot Register',  type: 'review',  icon: FileText },
    { key: 'track_select',          name: 'Migration Track',        phase: 'Lot Register',  type: 'choice',  icon: GitBranch },
    { key: 'owner_import',          name: 'Owner Data',             phase: 'Lot Register',  type: 'upload',  icon: Users },
];

const TRACK_B_STEPS = [
    { key: 'opening_balances_hist', name: 'Opening Balances',       phase: 'Historical',    type: 'upload',  icon: Scale },
    { key: 'levy_history',          name: 'Historical Levies',      phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'payment_history',       name: 'Historical Payments',    phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'expense_history',       name: 'Historical Expenses',    phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'gl_journals',           name: 'GL Journals',            phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'transfer_history',      name: 'Owner Transfer History', phase: 'Historical',    type: 'upload',  icon: History },
    { key: 'bank_tx_hist',          name: 'Trust Transactions',     phase: 'Historical',    type: 'upload',  icon: Banknote },
    { key: 'maintenance_hist',      name: 'Maintenance History',    phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'meeting_records',       name: 'Meeting Records',        phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'insurance_hist',        name: 'Insurance History',      phase: 'Historical',    type: 'upload',  icon: Shield },
    { key: 'compliance_hist',       name: 'Compliance Records',     phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'document_registry',     name: 'Document Registry',      phase: 'Historical',    type: 'upload',  icon: FileText },
    { key: 'data_validation',       name: 'Data Validation',        phase: 'Historical',    type: 'review',  icon: CheckCircle2 },
    { key: 'variance_review',       name: 'Variance Review',        phase: 'Historical',    type: 'review',  icon: AlertTriangle },
    { key: 'reconciliation_b',      name: 'Reconciliation Gate',    phase: 'Historical',    type: 'gate',    icon: Scale },
];

const TRACK_A_STEPS = [
    { key: 'opening_balances_curr', name: 'Current Balances',       phase: 'Balances',      type: 'form',    icon: Scale },
    { key: 'current_levy_schedule', name: 'Levy Schedule',          phase: 'Balances',      type: 'form',    icon: FileText },
    { key: 'reconciliation_a',      name: 'Reconciliation Check',   phase: 'Balances',      type: 'gate',    icon: Scale },
];

const GO_LIVE_STEPS = [
    { key: 'feature_toggles',       name: 'Feature Toggles',        phase: 'Go Live',       type: 'form',    icon: Settings2 },
    { key: 'levy_settings',         name: 'Levy Settings',          phase: 'Go Live',       type: 'form',    icon: Layers },
    { key: 'notifications',         name: 'Notifications',          phase: 'Go Live',       type: 'form',    icon: Mail },
    { key: 'ec_invitations',        name: 'EC Invitations',         phase: 'Go Live',       type: 'form',    icon: Users },
    { key: 'owner_invitations',     name: 'Owner Invitations',      phase: 'Go Live',       type: 'form',    icon: Mail },
    { key: 'attestation',           name: 'Legal Attestation',      phase: 'Go Live',       type: 'form',    icon: PenLine },
    { key: 'finalize',              name: 'Finalise Scheme',        phase: 'Go Live',       type: 'action',  icon: Rocket },
    { key: 'go_live',               name: 'Go-Live Confirmation',   phase: 'Go Live',       type: 'summary', icon: CheckCircle2 },
];

const JURISDICTIONS = ['ACT', 'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'NT'];
const GATE_KEYS = new Set(['reconciliation_a', 'reconciliation_b']);
// ── Upload step contract ──────────────────────────────────────────────────────
// Each uploading step declares the endpoint it posts to AND the exact multipart
// FIELD NAME(s) that endpoint expects. This used to be a bare endpoint map and
// every upload was posted as a single field called `file` — but not one backend
// endpoint is declared that way (`transfers_file`, `opening_balances_file`,
// `expenses`, and a five-file group for the historical-financials import), so
// every historical upload in this wizard returned 422 before this contract
// existed. `templateType` keys into the backend template registry
// (`GET /onboarding/templates/{type}`), which is generated from the same column
// lists the import endpoints validate against — so a downloaded template always
// imports cleanly.
const UPLOAD_STEP_SPECS = {
    owner_import: {
        endpoint: (sid) => `/onboarding/scheme/${sid}/import-owner-transfers`,
        files: [{ field: 'transfers_file', templateType: 'owner_transfers', label: 'Owner transfer history' }],
    },
    transfer_history: {
        endpoint: (sid) => `/onboarding/scheme/${sid}/import-owner-transfers`,
        files: [{ field: 'transfers_file', templateType: 'owner_transfers', label: 'Owner transfer history' }],
    },
    opening_balances_hist: {
        endpoint: (sid) => `/onboarding/scheme/${sid}/import-opening-balances`,
        files: [{ field: 'opening_balances_file', templateType: 'opening_balances', label: 'Fund opening balances' }],
    },
    // The historical-financials endpoint takes all five files in ONE request and
    // cross-validates them against each other (quarterly levy totals must sum to
    // the annual fund summaries), so they cannot be uploaded a step at a time.
    levy_history: {
        endpoint: (sid) => `/onboarding/scheme/${sid}/import-historical-financials`,
        files: [
            { field: 'quarterly_levies', templateType: 'quarterly_levies', label: 'Quarterly levy issuances' },
            { field: 'admin_fund_summary', templateType: 'admin_fund_summary', label: 'Admin fund annual summary' },
            { field: 'sinking_fund_summary', templateType: 'sinking_fund_summary', label: 'Sinking fund annual summary' },
            { field: 'arrears', templateType: 'arrears', label: 'Per-lot arrears snapshot' },
            { field: 'outstanding', templateType: 'outstanding', label: 'Per-lot outstanding balances' },
        ],
    },
    expense_history: {
        endpoint: (sid) => `/onboarding/scheme/${sid}/import-historical-expenses`,
        files: [{ field: 'expenses', templateType: 'historical_expenses', label: 'Historical expense transactions' }],
    },
};

// Steps that are genuinely upload-shaped in the UI but have NO import endpoint
// behind them yet. Saying so explicitly beats posting to the wrong endpoint and
// showing the user a 422 they cannot act on.
const UPLOAD_STEPS_WITHOUT_ENDPOINT = {
    payment_history: 'Historical payments are DERIVED from the levy history and the arrears/outstanding snapshots during reconstruction — there is no separate payment file to upload. Complete the Historical Levies step instead.',
    gl_journals: 'GL journals are produced by the reconstruction pipeline from the imported evidence; they are never imported directly. Nothing to upload here.',
    bank_tx_hist: 'Trust/bank transactions enter through the Financial Evidence Gateway (Admin → Financial Data → Demo Bank), not through onboarding. Attach here as a reminder only.',
    maintenance_hist: 'No import endpoint yet — attach as a reminder; the file is recorded in the step notes.',
    meeting_records: 'No import endpoint yet — attach as a reminder; the file is recorded in the step notes.',
    insurance_hist: 'No import endpoint yet — attach as a reminder; the file is recorded in the step notes.',
    compliance_hist: 'No import endpoint yet — attach as a reminder; the file is recorded in the step notes.',
    document_registry: 'No import endpoint yet — attach as a reminder; the file is recorded in the step notes.',
};

const LOT_TEMPLATE_TYPE = 'lots';

/** Compose the active step sequence for the chosen migration track. */
function getActiveSteps(track) {
    if (track === 'A') return [...SETUP_STEPS, ...TRACK_A_STEPS, ...GO_LIVE_STEPS];
    if (track === 'B') return [...SETUP_STEPS, ...TRACK_B_STEPS, ...GO_LIVE_STEPS];
    return SETUP_STEPS;
}

/**
 * Download an import template from the backend registry.
 *
 * Templates are NOT generated in the browser any more: the header strings that
 * used to live here had drifted from every endpoint's actual column contract.
 * `format` is 'csv' or 'xlsx' — both are accepted back by the import endpoints.
 */
async function downloadTemplate(api, templateType, format = 'csv') {
    try {
        const res = await api.get(`/onboarding/templates/${templateType}`, {
            params: { format },
            responseType: 'blob',
        });
        const url = URL.createObjectURL(new Blob([res.data]));
        const a = document.createElement('a');
        a.href = url;
        a.download = `${templateType}_template.${format}`;
        a.click();
        URL.revokeObjectURL(url);
    } catch {
        toast.error('Could not download the template. Please try again.');
    }
}

/** Template download control offering both CSV and Excel. */
function TemplateDownloadButtons({ api, templateType, compact }) {
    return (
        <div className="flex items-center gap-1.5">
            <Button variant="outline" size="sm" onClick={() => downloadTemplate(api, templateType, 'csv')}>
                <FileText className="h-3.5 w-3.5 mr-1.5" /> {compact ? 'CSV' : 'Template (CSV)'}
            </Button>
            <Button variant="outline" size="sm" onClick={() => downloadTemplate(api, templateType, 'xlsx')}>
                <FileText className="h-3.5 w-3.5 mr-1.5" /> {compact ? 'Excel' : 'Template (Excel)'}
            </Button>
        </div>
    );
}
// ── Step content sub-components ───────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: FieldRow
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FieldRow({ label, children, required }) {
    return (
        <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium text-slate-700">
                {label} {required && <span className="text-red-500">*</span>}
            </label>
            {children}
        </div>
    );
}

const inputCls = 'w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30 bg-white';
const selectCls = `${inputCls} appearance-none`;

export function buildSchemeStartPayload(orgDetails = {}) {
    const schemeNumber = (orgDetails.scheme_number || '').trim();
    const schemeName = (orgDetails.scheme_name || '').trim();
    const jurisdiction = ((orgDetails.jurisdiction || 'ACT') + '').trim().toUpperCase();
    return {
        scheme_number: schemeNumber,
        scheme_name: schemeName,
        jurisdiction,
        tenant_id: orgDetails.tenant_id || undefined,
        abn: orgDetails.abn?.trim() || undefined,
    };
}

export function extractOnboardingErrorDetail(err, fallback = 'Failed to start onboarding session.') {
    const data = err?.response?.data;
    const detail = data?.detail;
    if (typeof detail === 'string') return detail;
    if (typeof detail?.detail === 'string') return detail.detail;
    if (typeof detail?.message === 'string') return detail.message;
    if (typeof data?.message === 'string') return data.message;
    return fallback;
}

// Step 1
/**
 * @generated FunctionHeader
 * Function: OrgDetailsStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function OrgDetailsStep({ stepData, onChange, organisations }) {
    return (
        <div className="space-y-5">
            <FieldRow label="Organisation / Tenant" required>
                <select className={selectCls} value={stepData.tenant_id || ''} onChange={e => onChange('tenant_id', e.target.value)}>
                    <option value="">Select organisation</option>
                    {(organisations || []).map(org => (
                        <option key={org.tenant_id} value={org.tenant_id}>{org.tenant_name}</option>
                    ))}
                </select>
            </FieldRow>
            <FieldRow label="Scheme / Unit Plan Number" required>
                <input className={inputCls} placeholder="e.g. 13195" value={stepData.scheme_number || ''} onChange={e => onChange('scheme_number', e.target.value)} />
            </FieldRow>
            <FieldRow label="Legal Scheme Name" required>
                <input className={inputCls} placeholder="e.g. East Gate Residences" value={stepData.scheme_name || ''} onChange={e => onChange('scheme_name', e.target.value)} />
            </FieldRow>
            <FieldRow label="Jurisdiction" required>
                <select className={selectCls} value={stepData.jurisdiction || 'ACT'} onChange={e => onChange('jurisdiction', e.target.value)}>
                    {JURISDICTIONS.map(j => <option key={j} value={j}>{j}</option>)}
                </select>
            </FieldRow>
            <FieldRow label="ABN (optional)">
                <input className={inputCls} placeholder="e.g. 12 345 678 901" value={stepData.abn || ''} onChange={e => onChange('abn', e.target.value)} />
            </FieldRow>
        </div>
    );
}
// Step 2
/**
 * @generated FunctionHeader
 * Function: SchemeConfigStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SchemeConfigStep({ stepData, onChange }) {
    return (
        <div className="space-y-5">
            <FieldRow label="Number of Lots (estimate)">
                <input type="number" min="1" className={inputCls} value={stepData.lot_count || ''} onChange={e => onChange('lot_count', e.target.value)} />
            </FieldRow>
            <FieldRow label="Financial Year Start Month">
                <select className={selectCls} value={stepData.fy_start_month || '1'} onChange={e => onChange('fy_start_month', e.target.value)}>
                    {['January','February','March','April','May','June','July','August','September','October','November','December']
                        .map((m, i) => <option key={i} value={i + 1}>{m}</option>)}
                </select>
            </FieldRow>
            <FieldRow label="Current Financial Year">
                <input className={inputCls} placeholder="e.g. 2026" value={stepData.current_fy || ''} onChange={e => onChange('current_fy', e.target.value)} />
            </FieldRow>
            <FieldRow label="Levy Frequency">
                <select className={selectCls} value={stepData.levy_frequency || 'quarterly'} onChange={e => onChange('levy_frequency', e.target.value)}>
                    <option value="quarterly">Quarterly</option>
                    <option value="monthly">Monthly</option>
                    <option value="annual">Annual</option>
                </select>
            </FieldRow>
        </div>
    );
}
// Step 3
/**
 * @generated FunctionHeader
 * Function: FundStructureStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FundStructureStep({ stepData, onChange }) {
    /**
     * @generated FunctionHeader
     * Function: toggle
     * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggle = (key) => onChange(key, !stepData[key]);
    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Select which funds this scheme operates. Admin fund is mandatory.</p>
            {[
                { key: 'fund_admin', label: 'Administrative Fund', required: true, desc: 'Day-to-day operating expenses' },
                { key: 'fund_capital_works', label: 'Capital Works Fund', required: false, desc: 'Long-term capital improvements' },
                { key: 'fund_sinking', label: 'Sinking Fund (legacy)', required: false, desc: 'Pre-2016 NSW / QLD sinking fund' },
                { key: 'fund_special', label: 'Special Purpose Fund', required: false, desc: 'Optional special-purpose reserves' },
            ].map(({ key, label, required, desc }) => (
                <div key={key} className={cn('flex items-center justify-between p-4 border rounded-lg', (stepData[key] || required) ? 'bg-emerald-50 border-emerald-200' : 'bg-white border-slate-200')}>
                    <div>
                        <p className="font-medium text-sm">{label} {required && <Badge variant="outline" className="ml-1 text-[10px]">Required</Badge>}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>
                    </div>
                    <input type="checkbox" checked={!!(stepData[key] || required)} disabled={required} onChange={() => toggle(key)} className="h-4 w-4 accent-primary cursor-pointer" />
                </div>
            ))}
        </div>
    );
}
// Step 4
/**
 * @generated FunctionHeader
 * Function: BankAccountsStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function BankAccountsStep({ stepData, onChange }) {
    return (
        <div className="space-y-5">
            <p className="text-sm text-muted-foreground">Enter the trust account details for levy receipts and disbursements.</p>
            <FieldRow label="Account Name" required>
                <input className={inputCls} placeholder="e.g. East Gate OC Trust Account" value={stepData.trust_account_name || ''} onChange={e => onChange('trust_account_name', e.target.value)} />
            </FieldRow>
            <div className="grid grid-cols-2 gap-4">
                <FieldRow label="BSB" required>
                    <input className={inputCls} placeholder="000-000" value={stepData.trust_bsb || ''} onChange={e => onChange('trust_bsb', e.target.value)} />
                </FieldRow>
                <FieldRow label="Account Number" required>
                    <input className={inputCls} placeholder="12345678" value={stepData.trust_account_no || ''} onChange={e => onChange('trust_account_no', e.target.value)} />
                </FieldRow>
            </div>
            <FieldRow label="Bank Name">
                <input className={inputCls} placeholder="e.g. Commonwealth Bank" value={stepData.trust_bank_name || ''} onChange={e => onChange('trust_bank_name', e.target.value)} />
            </FieldRow>
            <FieldRow label="Opening Balance (AUD)">
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" value={stepData.trust_opening_balance || ''} onChange={e => onChange('trust_opening_balance', e.target.value)} />
            </FieldRow>
        </div>
    );
}
// Step 5
/**
 * @generated FunctionHeader
 * Function: InsuranceStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function InsuranceStep({ stepData, onChange }) {
    return (
        <div className="space-y-5">
            <p className="text-sm text-muted-foreground">Basic insurance details for compliance records. Upload documentation in Step 19 (Track B) or skip for now.</p>
            <FieldRow label="Insurance Provider">
                <input className={inputCls} placeholder="e.g. CHU Underwriting Agencies" value={stepData.insurer_name || ''} onChange={e => onChange('insurer_name', e.target.value)} />
            </FieldRow>
            <FieldRow label="Policy Number">
                <input className={inputCls} placeholder="e.g. CHU-2026-123456" value={stepData.policy_number || ''} onChange={e => onChange('policy_number', e.target.value)} />
            </FieldRow>
            <FieldRow label="Policy Expiry Date">
                <input type="date" className={inputCls} value={stepData.policy_expiry || ''} onChange={e => onChange('policy_expiry', e.target.value)} />
            </FieldRow>
            <FieldRow label="Sum Insured (AUD)">
                <input type="number" step="1000" className={inputCls} placeholder="0" value={stepData.sum_insured || ''} onChange={e => onChange('sum_insured', e.target.value)} />
            </FieldRow>
        </div>
    );
}
// Steps 9, 10-21 — CSV/XLSX upload against the real endpoint contract
/**
 * Upload step for every import that posts files to an onboarding endpoint.
 *
 * Renders one picker per multipart field the target endpoint declares (the
 * historical-financials import needs five files in a single request, because it
 * cross-validates the quarterly levy totals against the annual fund summaries),
 * and posts each file under that endpoint's exact field name. Steps with no
 * endpoint behind them say so and keep the file as a step-note attachment
 * instead of posting it somewhere that would reject it.
 */
function CsvUploadStep({ step, sessionId, uploadResults, onUploadResult, api }) {
    const spec = UPLOAD_STEP_SPECS[step.key];
    const noEndpointReason = UPLOAD_STEPS_WITHOUT_ENDPOINT[step.key];
    const [uploading, setUploading] = useState(false);
    const [selected, setSelected] = useState({});
    const result = uploadResults[step.key];

    const files = spec?.files ?? [];
    const allSelected = files.length > 0 && files.every(f => selected[f.field]);

    const handleNoEndpointFile = (file) => {
        if (!file) return;
        onUploadResult(step.key, { skipped: true, filename: file.name });
        toast.info('File noted for this step — there is no import endpoint behind it.');
    };

    const handleUpload = async () => {
        if (!spec || !allSelected) return;
        setUploading(true);
        try {
            const fd = new FormData();
            // Field names must match the endpoint signature exactly — see UPLOAD_STEP_SPECS.
            files.forEach(f => fd.append(f.field, selected[f.field]));
            const res = await api.post(spec.endpoint(sessionId), fd, {
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            onUploadResult(step.key, { success: true, ...res.data });
            toast.success(files.length > 1 ? `${files.length} files imported successfully.` : 'File imported successfully.');
        } catch (err) {
            const raw = err.response?.data?.detail;
            const detail = typeof raw === 'string' ? raw
                : Array.isArray(raw) ? raw.map(d => d.msg || JSON.stringify(d)).join('; ')
                : raw?.detail || 'Upload failed.';
            toast.error(detail);
            onUploadResult(step.key, { success: false, error: detail });
        } finally {
            setUploading(false);
        }
    };

    if (!spec) {
        return (
            <div className="space-y-5">
                <Alert>
                    <AlertDescription>
                        {noEndpointReason || 'No import endpoint for this step — the file is recorded in the step notes.'}
                    </AlertDescription>
                </Alert>
                <label className="flex items-center gap-2 rounded-lg border border-dashed border-slate-200 px-3 py-3 cursor-pointer hover:border-primary/40 hover:bg-primary/5 transition-colors">
                    <Upload className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm text-muted-foreground">Attach a file as a reminder (optional)</span>
                    <input
                        type="file"
                        className="hidden"
                        data-testid={`upload-note-${step.key}`}
                        onChange={e => handleNoEndpointFile(e.target.files?.[0])}
                    />
                </label>
                {result?.skipped && (
                    <Alert><AlertDescription>File noted: {result.filename}</AlertDescription></Alert>
                )}
            </div>
        );
    }

    return (
        <div className="space-y-5">
            <p className="text-sm text-muted-foreground">
                {files.length > 1
                    ? `${step.name} needs all ${files.length} files in one submission — they are cross-validated against each other before anything is stored.`
                    : `Upload the file for ${step.name}.`}{' '}
                Download a template (CSV or Excel) for the exact columns; both formats can be uploaded back.
            </p>

            <div className="space-y-3">
                {files.map(f => (
                    <div key={f.field} className={`rounded-xl border-2 p-3 transition-colors ${selected[f.field] ? 'border-green-200 bg-green-50/30' : 'border-dashed border-slate-200'}`}>
                        <div className="flex items-center justify-between gap-3 flex-wrap">
                            <div className="min-w-0">
                                <p className="text-sm font-semibold">{f.label}</p>
                                <p className="text-xs text-muted-foreground font-mono">{f.field}</p>
                            </div>
                            <TemplateDownloadButtons api={api} templateType={f.templateType} compact />
                        </div>
                        <label className="mt-2 flex items-center gap-2 rounded-lg border border-dashed border-slate-200 px-3 py-2.5 cursor-pointer hover:border-primary/40 hover:bg-primary/5 transition-colors">
                            {selected[f.field] ? (
                                <>
                                    <CheckCircle2 className="h-4 w-4 text-green-600 flex-shrink-0" />
                                    <span className="text-sm text-green-800 truncate flex-1">{selected[f.field].name}</span>
                                </>
                            ) : (
                                <>
                                    <Upload className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                                    <span className="text-sm text-muted-foreground">Click to choose a CSV or Excel file</span>
                                </>
                            )}
                            <input
                                type="file"
                                className="hidden"
                                accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                data-testid={`upload-${f.field}`}
                                onChange={e => {
                                    const file = e.target.files?.[0];
                                    if (file) setSelected(prev => ({ ...prev, [f.field]: file }));
                                }}
                            />
                        </label>
                    </div>
                ))}
            </div>

            <div className="flex items-center gap-3">
                <Button onClick={handleUpload} disabled={!allSelected || uploading} data-testid={`submit-upload-${step.key}`}>
                    {uploading ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Upload className="h-4 w-4 mr-2" />}
                    {uploading ? 'Importing…' : files.length > 1 ? `Import ${files.length} files` : 'Import file'}
                </Button>
                <span className="text-xs text-muted-foreground">Max 10 MB per file · CSV (UTF-8) or .xlsx</span>
            </div>

            {result && (
                <Alert variant={result.success ? 'default' : result.skipped ? 'default' : 'destructive'}>
                    <AlertDescription>
                        {result.skipped ? `File noted: ${result.filename}` :
                         result.success ? `Imported successfully. ${result.imported ?? ''} rows processed.` :
                         `Error: ${result.error}`}
                    </AlertDescription>
                </Alert>
            )}
        </div>
    );
}
// Step 6 — Lot import
// POST /onboarding/scheme/{id}/lots expects JSON { lots: LotInput[] }, NOT multipart.
// Parse the CSV here and send JSON so the correct endpoint is called.
/**
 * @generated FunctionHeader
 * Function: LotImportStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LotImportStep({ step, sessionId, uploadResults, onUploadResult, api }) {
    const fileRef = useRef(null);
    const [parsing, setParsing] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [parsedLots, setParsedLots] = useState(null);
    const result = uploadResults[step.key];
    /**
     * @generated FunctionHeader
     * Function: parseLotCsv
     * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    /**
     * Parse the chosen file through the backend template parser.
     *
     * The browser used to split the CSV itself, which meant an .xlsx of lots —
     * the format most strata managers actually hold — could not be used at all
     * here (the `/lots` endpoint takes JSON, not multipart, so there was nowhere
     * to send the workbook). `POST /onboarding/templates/lots/parse` stores
     * nothing; it just returns the rows and enforces the required columns, so
     * schema errors surface before the operator commits.
     */
    const handleFile = async (file) => {
        if (!file) return;
        setParsing(true);
        try {
            const fd = new FormData();
            fd.append('file', file);
            const res = await api.post(`/onboarding/templates/${LOT_TEMPLATE_TYPE}/parse`, fd, {
                headers: { 'Content-Type': 'multipart/form-data' },
            });
            const lots = (res.data?.rows || [])
                .filter(r => r.lot_number)
                .map(r => ({
                    ...r,
                    lot_use: r.lot_use || 'residential',
                    floor_area_sqm: r.floor_area_sqm ? parseFloat(r.floor_area_sqm) || null : null,
                    entitlement_units: r.entitlement_units ? parseFloat(r.entitlement_units) || null : null,
                }));
            if (lots.length === 0) {
                toast.error('No valid lots found. Check your file has a lot_number column and at least one data row.');
                return;
            }
            setParsedLots(lots);
            toast.success(`Parsed ${lots.length} lot(s). Click Upload to import.`);
        } catch (err) {
            const raw = err.response?.data?.detail;
            toast.error(typeof raw === 'string' ? raw : 'Failed to read file. Upload a CSV or .xlsx matching the template.');
        } finally {
            setParsing(false);
        }
    };
    const handleUpload = async () => {
        if (!parsedLots || !sessionId) return;
        setUploading(true);
        try {
            const res = await api.post(
                `/onboarding/scheme/${sessionId}/lots`,
                { lots: parsedLots },
            );
            onUploadResult(step.key, { success: true, lots: parsedLots, imported: parsedLots.length, ...res.data });
            toast.success(`${parsedLots.length} lot(s) imported successfully.`);
        } catch (err) {
            const detail = err.response?.data?.detail || 'Upload failed.';
            toast.error(detail);
            onUploadResult(step.key, { success: false, error: detail });
        } finally {
            setUploading(false);
        }
    };

    return (
        <div className="space-y-5">
            <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">Upload a CSV or Excel file of lots. Download the template to see the expected columns.</p>
                <TemplateDownloadButtons api={api} templateType={LOT_TEMPLATE_TYPE} />
            </div>
            <div
                className="border-2 border-dashed border-slate-200 rounded-xl p-10 text-center cursor-pointer hover:border-primary/40 hover:bg-primary/5 transition-all"
                onClick={() => fileRef.current?.click()}
                onDragOver={e => e.preventDefault()}
                onDrop={e => { e.preventDefault(); handleFile(e.dataTransfer.files[0]); }}
            >
                {parsing ? (
                    <Loader2 className="h-8 w-8 mx-auto animate-spin text-primary mb-2" />
                ) : (
                    <Upload className="h-8 w-8 mx-auto text-slate-300 mb-2" />
                )}
                <p className="text-sm font-medium">
                    {parsing ? 'Parsing CSV…' :
                     parsedLots ? `${parsedLots.length} lot(s) parsed — click Upload below` :
                     'Click or drag CSV here'}
                </p>
                <p className="text-xs text-muted-foreground mt-1">Max 10 MB · UTF-8 encoding</p>
                <input ref={fileRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" className="hidden"
                    onChange={e => handleFile(e.target.files?.[0])} />
            </div>
            {parsedLots && parsedLots.length > 0 && (
                <div className="overflow-x-auto border rounded-lg max-h-48">
                    <table className="w-full text-xs">
                        <thead className="bg-slate-50 border-b">
                            <tr>{['Lot #', 'Unit #', 'Use', 'Entitlement', 'Owner Email'].map(h =>
                                <th key={h} className="px-3 py-2 text-left font-semibold text-slate-600">{h}</th>
                            )}</tr>
                        </thead>
                        <tbody>
                            {parsedLots.slice(0, 5).map((r, i) => (
                                <tr key={i} className="border-b last:border-0">
                                    <td className="px-3 py-1.5">{r.lot_number}</td>
                                    <td className="px-3 py-1.5">{r.unit_number || '—'}</td>
                                    <td className="px-3 py-1.5">{r.lot_use}</td>
                                    <td className="px-3 py-1.5">{r.entitlement_units ?? '—'}</td>
                                    <td className="px-3 py-1.5 text-slate-500">{r.owner_email || '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {parsedLots.length > 5 && (
                        <p className="text-xs text-muted-foreground px-3 py-2">… and {parsedLots.length - 5} more row(s)</p>
                    )}
                </div>
            )}
            {parsedLots && (
                <Button className="w-full" onClick={handleUpload}
                    disabled={uploading || !sessionId || !!result?.success}>
                    {uploading
                        ? <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        : <Upload className="h-4 w-4 mr-2" />}
                    {result?.success ? 'Lots Imported ✓' : `Upload ${parsedLots.length} Lot(s)`}
                </Button>
            )}
            {result && (
                <Alert variant={result.success ? 'default' : 'destructive'}>
                    <AlertDescription>
                        {result.success
                            ? `${result.imported ?? parsedLots?.length ?? 0} lot(s) imported successfully.`
                            : `Error: ${result.error}`}
                    </AlertDescription>
                </Alert>
            )}
        </div>
    );
}
// Step 7 — Review lots table
/**
 * @generated FunctionHeader
 * Function: ReviewLotsStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ReviewLotsStep({ uploadResults }) {
    const lotResult = uploadResults['lot_import'];
    const rows = lotResult?.lots || [];
    if (!lotResult) return (
        <Alert><AlertDescription>No lot file uploaded yet. Go back to Step 6 to upload your lots CSV.</AlertDescription></Alert>
    );
    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Review the lots imported from your CSV. Return to Step 6 to re-upload if corrections are needed.</p>
            {rows.length > 0 ? (
                <div className="overflow-x-auto border rounded-lg">
                    <table className="w-full text-xs">
                        <thead className="bg-slate-50 border-b">
                            <tr>{['Lot #', 'Unit #', 'Use', 'Entitlement', 'Owner Email'].map(h => <th key={h} className="px-3 py-2 text-left font-semibold text-slate-600">{h}</th>)}</tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => (
                                <tr key={i} className="border-b last:border-0 hover:bg-slate-50">
                                    <td className="px-3 py-2">{r.lot_number}</td>
                                    <td className="px-3 py-2">{r.unit_number || '—'}</td>
                                    <td className="px-3 py-2">{r.lot_use || 'residential'}</td>
                                    <td className="px-3 py-2">{r.entitlement_units ?? '—'}</td>
                                    <td className="px-3 py-2 text-slate-500">{r.owner_email || '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="text-sm text-muted-foreground">Upload result received but no lot rows were returned. Check the uploaded file.</p>
            )}
            <div className="flex gap-2 flex-wrap">
                <Badge variant="secondary">{lotResult.imported ?? rows.length} lots imported</Badge>
                {lotResult.errors > 0 && <Badge variant="destructive">{lotResult.errors} errors</Badge>}
            </div>
        </div>
    );
}
// Step 8 — Track selection
/**
 * @generated FunctionHeader
 * Function: TrackSelectStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function TrackSelectStep({ track, onTrackChange }) {
    const options = [
        {
            value: 'A',
            title: 'Track A — Current Balances',
            desc: 'Enter today\'s opening balances and current levy schedule only. Fastest path to go live. Ideal for schemes starting fresh on StrataOS with no historical data requirement.',
            steps: '25 active steps',
            badge: 'Recommended for new schemes',
        },
        {
            value: 'B',
            title: 'Track B — Full Historical Records',
            desc: 'Import complete financial history: levies, payments, expenses, GL journals, owner transfers, and bank transactions. Full audit trail from day one.',
            steps: '32 steps',
            badge: 'Required for compliance-heavy schemes',
        },
    ];
    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Choose the migration approach for this scheme. This cannot be changed after you proceed past this step.</p>
            {options.map(opt => (
                <button
                    key={opt.value}
                    onClick={() => onTrackChange(opt.value)}
                    className={cn(
                        'w-full text-left p-5 rounded-xl border-2 transition-all',
                        track === opt.value ? 'border-primary bg-primary/5 shadow-md' : 'border-slate-200 hover:border-slate-300 bg-white'
                    )}
                >
                    <div className="flex items-start justify-between gap-3">
                        <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                                <p className="font-bold text-sm">{opt.title}</p>
                                <Badge variant={track === opt.value ? 'default' : 'outline'} className="text-[10px]">{opt.steps}</Badge>
                            </div>
                            <p className="text-sm text-muted-foreground leading-relaxed">{opt.desc}</p>
                            <p className="text-[11px] text-primary font-medium mt-2">{opt.badge}</p>
                        </div>
                        <div className={cn('h-5 w-5 rounded-full border-2 flex-shrink-0 mt-0.5 flex items-center justify-center', track === opt.value ? 'border-primary bg-primary' : 'border-slate-300')}>
                            {track === opt.value && <div className="h-2 w-2 rounded-full bg-white" />}
                        </div>
                    </div>
                </button>
            ))}
        </div>
    );
}
// Step 16A — Current opening balances (Track A)
/**
 * @generated FunctionHeader
 * Function: OpeningBalancesStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function OpeningBalancesStep({ stepData, onChange }) {
    return (
        <div className="space-y-5">
            <p className="text-sm text-muted-foreground">Enter the current balances as at the cutover date. These will become the opening balances in StrataOS.</p>
            <FieldRow label="Cutover / Balance Date" required>
                <input type="date" className={inputCls} value={stepData.balance_date || ''} onChange={e => onChange('balance_date', e.target.value)} />
            </FieldRow>
            <FieldRow label="Administrative Fund Balance (AUD)" required>
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" value={stepData.admin_fund_balance || ''} onChange={e => onChange('admin_fund_balance', e.target.value)} />
            </FieldRow>
            <FieldRow label="Capital Works / Sinking Fund Balance (AUD)">
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" value={stepData.sinking_fund_balance || ''} onChange={e => onChange('sinking_fund_balance', e.target.value)} />
            </FieldRow>
            <FieldRow label="Trust Account Bank Balance (AUD)" required>
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" value={stepData.trust_balance || ''} onChange={e => onChange('trust_balance', e.target.value)} />
            </FieldRow>
            <FieldRow label="Notes">
                <textarea rows={2} className={inputCls} placeholder="Source document, bank statement date, etc." value={stepData.balance_notes || ''} onChange={e => onChange('balance_notes', e.target.value)} />
            </FieldRow>
        </div>
    );
}
// Step 17A — Current levy schedule (Track A)
/**
 * @generated FunctionHeader
 * Function: CurrentLevyScheduleStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function CurrentLevyScheduleStep({ stepData, onChange }) {
    return (
        <div className="space-y-5">
            <p className="text-sm text-muted-foreground">Enter the current period levy amounts. You can upload a CSV or enter the totals manually.</p>
            <FieldRow label="Current Quarter / Period">
                <select className={selectCls} value={stepData.levy_quarter || ''} onChange={e => onChange('levy_quarter', e.target.value)}>
                    <option value="">Select quarter</option>
                    {['Q1 (Jan–Mar)', 'Q2 (Apr–Jun)', 'Q3 (Jul–Sep)', 'Q4 (Oct–Dec)'].map(q => <option key={q} value={q}>{q}</option>)}
                </select>
            </FieldRow>
            <FieldRow label="Total Admin Fund Levy (AUD)" required>
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" value={stepData.total_admin_levy || ''} onChange={e => onChange('total_admin_levy', e.target.value)} />
            </FieldRow>
            <FieldRow label="Total Capital Works Levy (AUD)">
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" value={stepData.total_cw_levy || ''} onChange={e => onChange('total_cw_levy', e.target.value)} />
            </FieldRow>
            <FieldRow label="GST Included">
                <div className="flex items-center gap-2 mt-1">
                    <input type="checkbox" id="levy_gst" className="h-4 w-4 accent-primary" checked={!!stepData.levy_gst_included} onChange={e => onChange('levy_gst_included', e.target.checked)} />
                    <label htmlFor="levy_gst" className="text-sm">Levy amounts include GST</label>
                </div>
            </FieldRow>
        </div>
    );
}
// Steps 22-23 — generic review/validation
/**
 * @generated FunctionHeader
 * Function: GenericReviewStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function GenericReviewStep({ step }) {
    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
                {step.key === 'data_validation'
                    ? 'The system has validated all imported records. Review any warnings or errors before proceeding.'
                    : 'Review any variances between imported data sets before the reconciliation gate.'}
            </p>
            <Alert>
                <AlertDescription>
                    Automated validation runs server-side after each CSV import. Check the import result badges on each upload step for error counts. Return to fix issues before proceeding to the reconciliation gate.
                </AlertDescription>
            </Alert>
            <div className="p-4 bg-slate-50 rounded-lg border">
                <p className="text-sm font-medium mb-2">Validation checklist</p>
                {[
                    'All lot numbers match imported lots',
                    'Financial totals balance (debits = credits)',
                    'Owner emails are valid format',
                    'Dates are in valid range',
                    'No duplicate records detected',
                ].map(item => (
                    <div key={item} className="flex items-center gap-2 py-1.5 text-sm">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500 flex-shrink-0" />
                        <span>{item}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}
// Reconciliation gate (both tracks)
/**
 * @generated FunctionHeader
 * Function: ReconciliationGateStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ReconciliationGateStep({ step, stepData, onChange, gateCleared, onGateCleared }) {
    const isCleared = gateCleared[step.key];
    const variance = parseFloat(stepData.reconciliation_variance_cents || '0') / 100;
    const isZeroVariance = variance === 0;

    return (
        <div className="space-y-5">
            <Alert variant={isCleared ? 'default' : 'destructive'} className={isCleared ? 'border-emerald-200 bg-emerald-50' : ''}>
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                    {isCleared
                        ? 'Reconciliation approved. You may proceed.'
                        : 'This is a hard gate. You cannot advance until the variance is zero or an operator has approved it with a written reason.'}
                </AlertDescription>
            </Alert>
            <FieldRow label="Bank Statement Closing Balance (AUD)">
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" value={stepData.bank_statement_balance || ''} onChange={e => onChange('bank_statement_balance', e.target.value)} />
            </FieldRow>
            <FieldRow label="System Calculated Balance (AUD)">
                <input type="number" step="0.01" className={inputCls} placeholder="0.00" readOnly value={stepData.system_balance || ''} onChange={e => onChange('system_balance', e.target.value)} />
            </FieldRow>
            <FieldRow label="Variance (cents) — 0 required to auto-clear">
                <input type="number" className={inputCls} placeholder="0" value={stepData.reconciliation_variance_cents || ''} onChange={e => onChange('reconciliation_variance_cents', e.target.value)} />
            </FieldRow>
            {!isZeroVariance && (
                <FieldRow label="Operator Approval Reason (required if variance ≠ 0)">
                    <textarea rows={3} className={inputCls} placeholder="Explain why the variance is acceptable (e.g. timing difference — payment cleared after statement date)." value={stepData.reconciliation_approval_reason || ''} onChange={e => onChange('reconciliation_approval_reason', e.target.value)} />
                </FieldRow>
            )}
            <Button
                className="w-full"
                disabled={isCleared || (!isZeroVariance && !stepData.reconciliation_approval_reason?.trim())}
                onClick={() => onGateCleared(step.key)}
                variant={isCleared ? 'outline' : 'default'}
            >
                {isCleared ? <><CheckCircle2 className="h-4 w-4 mr-2" /> Gate Cleared</> : 'Approve & Clear Gate'}
            </Button>
        </div>
    );
}
// Step 25 — Feature toggles
/**
 * @generated FunctionHeader
 * Function: FeatureTogglesStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FeatureTogglesStep({ stepData, onChange }) {
    const features = [
        { key: 'ft_levy_management', label: 'Levy Management', desc: 'Issue and track quarterly levies' },
        { key: 'ft_maintenance', label: 'Maintenance Requests', desc: 'Log and manage maintenance work orders' },
        { key: 'ft_trust_accounting', label: 'Trust Accounting', desc: 'Trust receipts and disbursement ledger' },
        { key: 'ft_compliance', label: 'Compliance Tracking', desc: 'Insurance, WHS, and regulatory deadlines' },
        { key: 'ft_document_store', label: 'Document Store', desc: 'Store and share scheme documents' },
        { key: 'ft_owner_portal', label: 'Owner Portal', desc: 'Owner-facing dashboard and levy views' },
        { key: 'ft_voting', label: 'E-Voting', desc: 'Digital voting for AGMs and special resolutions' },
    ];
    return (
        <div className="space-y-3">
            <p className="text-sm text-muted-foreground">Enable features for this scheme. These can be changed later in the Feature Toggles admin page.</p>
            {features.map(({ key, label, desc }) => (
                <div key={key} className="flex items-center justify-between p-3 border rounded-lg">
                    <div>
                        <p className="text-sm font-medium">{label}</p>
                        <p className="text-xs text-muted-foreground">{desc}</p>
                    </div>
                    <input type="checkbox" className="h-4 w-4 accent-primary cursor-pointer" checked={!!(stepData[key] ?? true)} onChange={e => onChange(key, e.target.checked)} />
                </div>
            ))}
        </div>
    );
}
// Step 26 — Levy settings
/**
 * @generated FunctionHeader
 * Function: LevySettingsStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function LevySettingsStep({ stepData, onChange }) {
    return (
        <div className="space-y-5">
            <FieldRow label="Days After Due Date for Arrears Notification">
                <input type="number" min="1" max="90" className={inputCls} value={stepData.arrears_notice_days || '14'} onChange={e => onChange('arrears_notice_days', e.target.value)} />
            </FieldRow>
            <FieldRow label="Interest Rate on Overdue Levies (% p.a.)">
                <input type="number" min="0" step="0.1" className={inputCls} value={stepData.interest_rate_pa || '10'} onChange={e => onChange('interest_rate_pa', e.target.value)} />
            </FieldRow>
            <FieldRow label="Default Payment Method">
                <select className={selectCls} value={stepData.default_payment_method || 'bpay'} onChange={e => onChange('default_payment_method', e.target.value)}>
                    {['bpay', 'bank_transfer', 'cheque', 'card'].map(m => <option key={m} value={m}>{m.replace('_', ' ')}</option>)}
                </select>
            </FieldRow>
            <div className="flex items-center gap-2">
                <input type="checkbox" id="gst_registered" className="h-4 w-4 accent-primary" checked={!!stepData.gst_registered} onChange={e => onChange('gst_registered', e.target.checked)} />
                <label htmlFor="gst_registered" className="text-sm">OC is GST registered (levies include GST)</label>
            </div>
        </div>
    );
}
// Step 27 — Notification settings
/**
 * @generated FunctionHeader
 * Function: NotificationsStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function NotificationsStep({ stepData, onChange }) {
    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Configure how owners and the committee receive notifications from StrataOS.</p>
            {[
                { key: 'notify_levy_issued', label: 'Levy Notice Issued', desc: 'Email owners when a new levy run is created' },
                { key: 'notify_payment_received', label: 'Payment Received', desc: 'Confirm receipt of levy payments' },
                { key: 'notify_maintenance_update', label: 'Maintenance Updates', desc: 'Notify when work order status changes' },
                { key: 'notify_agm_announcement', label: 'AGM Announcement', desc: 'Send AGM notices to all owners' },
                { key: 'notify_arrears_reminder', label: 'Arrears Reminder', desc: 'Automated overdue payment reminders' },
            ].map(({ key, label, desc }) => (
                <div key={key} className="flex items-center justify-between p-3 border rounded-lg">
                    <div>
                        <p className="text-sm font-medium">{label}</p>
                        <p className="text-xs text-muted-foreground">{desc}</p>
                    </div>
                    <input type="checkbox" className="h-4 w-4 accent-primary cursor-pointer" checked={!!(stepData[key] ?? true)} onChange={e => onChange(key, e.target.checked)} />
                </div>
            ))}
        </div>
    );
}
// Step 28 — EC invitations
/**
 * @generated FunctionHeader
 * Function: ECInvitationsStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ECInvitationsStep({ stepData, onChange }) {
    const members = stepData.ec_members || [];
    /**
     * @generated FunctionHeader
     * Function: addMember
     * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addMember = () => onChange('ec_members', [...members, { name: '', email: '', role: 'ec_member' }]);
    /**
     * @generated FunctionHeader
     * Function: updateMember
     * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateMember = (i, field, val) => {
        const updated = members.map((m, idx) => idx === i ? { ...m, [field]: val } : m);
        onChange('ec_members', updated);
    };
    /**
     * @generated FunctionHeader
     * Function: removeMember
     * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeMember = (i) => onChange('ec_members', members.filter((_, idx) => idx !== i));

    return (
        <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Invite Executive Committee members. They will receive an email to set up their StrataOS account.</p>
            {members.map((m, i) => (
                <div key={i} className="grid grid-cols-12 gap-2 items-start border p-3 rounded-lg">
                    <div className="col-span-3">
                        <input className={inputCls} placeholder="Full name" value={m.name} onChange={e => updateMember(i, 'name', e.target.value)} />
                    </div>
                    <div className="col-span-4">
                        <input type="email" className={inputCls} placeholder="email@example.com" value={m.email} onChange={e => updateMember(i, 'email', e.target.value)} />
                    </div>
                    <div className="col-span-4">
                        <select className={selectCls} value={m.role} onChange={e => updateMember(i, 'role', e.target.value)}>
                            <option value="ec_member">EC Member</option>
                            <option value="ec_member_chair">Chairperson</option>
                            <option value="ec_member_secretary">Secretary</option>
                            <option value="ec_member_treasurer">Treasurer</option>
                        </select>
                    </div>
                    <button onClick={() => removeMember(i)} className="col-span-1 text-slate-400 hover:text-red-500 pt-2">✕</button>
                </div>
            ))}
            <Button variant="outline" size="sm" onClick={addMember}><Users className="h-3.5 w-3.5 mr-1.5" /> Add EC Member</Button>
        </div>
    );
}
// Step 29 — Owner invitations
/**
 * @generated FunctionHeader
 * Function: OwnerInvitationsStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function OwnerInvitationsStep({ stepData, onChange, uploadResults }) {
    const lots = uploadResults['lot_import']?.lots || [];
    const invited = stepData.invite_owners || {};
    /**
     * @generated FunctionHeader
     * Function: toggleAll
     * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleAll = (val) => {
        const next = {};
        lots.forEach(l => { if (l.owner_email) next[l.lot_number] = val; });
        onChange('invite_owners', next);
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">Select which owners to invite to the StrataOS portal on go-live.</p>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => toggleAll(true)}>All</Button>
                    <Button variant="outline" size="sm" onClick={() => toggleAll(false)}>None</Button>
                </div>
            </div>
            {lots.filter(l => l.owner_email).length === 0 ? (
                <Alert><AlertDescription>No owners with email addresses found in the imported lot data. Invitations will be skipped.</AlertDescription></Alert>
            ) : (
                <div className="space-y-2 max-h-64 overflow-y-auto border rounded-lg p-2">
                    {lots.filter(l => l.owner_email).map(l => (
                        <div key={l.lot_number} className="flex items-center gap-3 p-2 hover:bg-slate-50 rounded">
                            <input type="checkbox" className="h-4 w-4 accent-primary" checked={!!invited[l.lot_number]} onChange={e => onChange('invite_owners', { ...invited, [l.lot_number]: e.target.checked })} />
                            <span className="text-sm font-medium w-12">Lot {l.lot_number}</span>
                            <span className="text-sm text-muted-foreground">{l.owner_email}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
// Step 30 — Legal attestation
/**
 * @generated FunctionHeader
 * Function: AttestationStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function AttestationStep({ stepData, onChange }) {
    return (
        <div className="space-y-5">
            <div className="bg-slate-50 border rounded-lg p-4 text-sm leading-relaxed text-slate-700">
                <p className="font-semibold mb-2">Strata Manager Certification</p>
                <p>I certify that the information entered in this onboarding wizard is accurate and complete to the best of my knowledge. I am authorised by the owners corporation to create this scheme record in StrataOS and to invite owners and committee members on its behalf.</p>
                <p className="mt-2">I acknowledge that this record will be used for levy management, financial reporting, and regulatory compliance under the relevant strata legislation of the nominated jurisdiction.</p>
            </div>
            <div className="flex items-start gap-3">
                <input type="checkbox" id="attest_confirm" className="h-4 w-4 accent-primary mt-0.5" checked={!!stepData.attested} onChange={e => onChange('attested', e.target.checked)} />
                <label htmlFor="attest_confirm" className="text-sm font-medium">I confirm the above certification and accept responsibility for the accuracy of this data.</label>
            </div>
            <FieldRow label="Full Name (Strata Manager)" required>
                <input className={inputCls} placeholder="Your full legal name" value={stepData.attestation_name || ''} onChange={e => onChange('attestation_name', e.target.value)} />
            </FieldRow>
            <FieldRow label="Date">
                <input type="date" className={inputCls} value={stepData.attestation_date || new Date().toISOString().split('T')[0]} onChange={e => onChange('attestation_date', e.target.value)} />
            </FieldRow>
        </div>
    );
}
// Step 31 — Finalize
/**
 * @generated FunctionHeader
 * Function: FinalizeStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FinalizeStep({ stepData, sessionId, onFinalize, submitting }) {
    const invited = Object.values(stepData.invite_owners || {}).filter(Boolean).length;
    const ecMembers = (stepData.ec_members || []).filter(m => m.email).length;
    return (
        <div className="space-y-6">
            <p className="text-sm text-muted-foreground">Review the summary below, then click Finalise to activate the scheme and send invitations.</p>
            <div className="grid grid-cols-2 gap-3">
                {[
                    { label: 'Scheme', value: stepData.scheme_name || '—' },
                    { label: 'Jurisdiction', value: stepData.jurisdiction || '—' },
                    { label: 'Lots Imported', value: stepData.lot_count || '—' },
                    { label: 'Owners to Invite', value: invited },
                    { label: 'EC Members to Invite', value: ecMembers },
                    { label: 'Attested by', value: stepData.attestation_name || '—' },
                ].map(({ label, value }) => (
                    <div key={label} className="bg-slate-50 border rounded-lg p-3">
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className="font-semibold text-sm mt-0.5">{String(value)}</p>
                    </div>
                ))}
            </div>
            <Alert>
                <AlertDescription>Finalising will mark the scheme as <strong>active</strong> and dispatch invitation emails. This action cannot be undone from the wizard.</AlertDescription>
            </Alert>
            <Button className="w-full" size="lg" onClick={onFinalize} disabled={submitting || !sessionId}>
                {submitting ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Rocket className="h-4 w-4 mr-2" />}
                Finalise Scheme
            </Button>
        </div>
    );
}
// Step 32 — Go-live confirmation
/**
 * @generated FunctionHeader
 * Function: GoLiveStep
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function GoLiveStep({ finalizeResult, stepData }) {
    return (
        <div className="space-y-6 text-center">
            <div className="flex justify-center">
                <div className="h-20 w-20 rounded-full bg-emerald-100 flex items-center justify-center">
                    <CheckCircle2 className="h-10 w-10 text-emerald-500" />
                </div>
            </div>
            <div>
                <h2 className="text-2xl font-bold text-emerald-800">Scheme is Live!</h2>
                <p className="text-muted-foreground mt-1">{stepData.scheme_name} has been activated on StrataOS.</p>
            </div>
            {finalizeResult && (
                <div className="grid grid-cols-2 gap-3 text-left">
                    <div className="bg-slate-50 border rounded-lg p-3">
                        <p className="text-xs text-muted-foreground">Owner Invitations</p>
                        <p className="font-semibold">{finalizeResult.owner_invites_sent ?? 0} sent</p>
                    </div>
                    <div className="bg-slate-50 border rounded-lg p-3">
                        <p className="text-xs text-muted-foreground">EC Invitations</p>
                        <p className="font-semibold">{finalizeResult.ec_invites_sent ?? 0} sent</p>
                    </div>
                </div>
            )}
            <div className="space-y-2 text-left">
                <p className="text-sm font-semibold">Next steps</p>
                {['Owners click their email invite link to set a password', 'EC members receive their invitation with role assignment', 'First levy run can be created from Levy Management', 'Upload supporting documents to the Document Store'].map(step => (
                    <div key={step} className="flex items-center gap-2 text-sm">
                        <ChevronRight className="h-3.5 w-3.5 text-primary flex-shrink-0" />
                        <span>{step}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}
// ── Step content dispatcher ────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: StepContent
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StepContent({ step, stepData, onChange, organisations, track, onTrackChange, sessionId, uploadResults, onUploadResult, gateCleared, onGateCleared, onFinalize, submitting, finalizeResult, api }) {
    const props = { step, stepData: stepData[step.key] || {},
 onChange: (k, v) => onChange(step.key, k, v), sessionId, organisations, track, uploadResults,
 onUploadResult: (k, r) => onUploadResult(k, r), gateCleared, onGateCleared, onFinalize, submitting, finalizeResult, api };

    switch (step.key) {
        case 'org_details':           return <OrgDetailsStep {...props} />;
        case 'scheme_config':         return <SchemeConfigStep {...props} />;
        case 'fund_structure':        return <FundStructureStep {...props} />;
        case 'bank_accounts':         return <BankAccountsStep {...props} />;
        case 'insurance_overview':    return <InsuranceStep {...props} />;
        // lot_import POSTs JSON to /lots — must use LotImportStep, not CsvUploadStep
        case 'lot_import':            return <LotImportStep {...props} />;
        case 'owner_import':
        case 'opening_balances_hist':
        case 'levy_history':
        case 'payment_history':
        case 'expense_history':
        case 'gl_journals':
        case 'transfer_history':
        case 'bank_tx_hist':
        case 'maintenance_hist':
        case 'meeting_records':
        case 'insurance_hist':
        case 'compliance_hist':
        case 'document_registry':     return <CsvUploadStep {...props} />;
        case 'review_lots':           return <ReviewLotsStep {...props} />;
        case 'track_select':          return <TrackSelectStep track={track} onTrackChange={onTrackChange} />;
        case 'opening_balances_curr': return <OpeningBalancesStep {...props} />;
        case 'current_levy_schedule': return <CurrentLevyScheduleStep {...props} />;
        case 'reconciliation_a':
        case 'reconciliation_b':      return <ReconciliationGateStep {...props} />;
        case 'data_validation':
        case 'variance_review':       return <GenericReviewStep step={step} />;
        case 'feature_toggles':       return <FeatureTogglesStep {...props} />;
        case 'levy_settings':         return <LevySettingsStep {...props} />;
        case 'notifications':         return <NotificationsStep {...props} />;
        case 'ec_invitations':        return <ECInvitationsStep {...props} />;
        case 'owner_invitations':     return <OwnerInvitationsStep {...props} />;
        case 'attestation':           return <AttestationStep {...props} />;
        case 'finalize': {
            // FinalizeStep needs cross-step data (ec_invitations, owner_invitations, attestation).
            // The default props slice only passes stepData['finalize'] — override it here.
            const finalizeData = {
                scheme_name:      stepData['org_details']?.scheme_name || '',
                jurisdiction:     stepData['org_details']?.jurisdiction || '',
                lot_count:        stepData['scheme_config']?.lot_count || '',
                ec_members:       stepData['ec_invitations']?.ec_members || [],
                invite_owners:    stepData['owner_invitations']?.invite_owners || {},
                attestation_name: stepData['attestation']?.attestation_name || '',
            };
            return <FinalizeStep step={step} stepData={finalizeData} sessionId={sessionId} onFinalize={onFinalize} submitting={submitting} />;
        }
        case 'go_live':               return <GoLiveStep {...props} />;
        default:                      return <p className="text-sm text-muted-foreground">Configure: {step.name}</p>;
    }
}
// ── Main component ─────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: OnboardingWizard
 * Path: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const OnboardingWizard = () => {
    const { api, user, isAdmin, loading: authLoading } = useAuth();
    const searchParams = useSearchParams();
    const effectiveRole = user?.effective_role || user?.role;
    const canAccess = effectiveRole === 'super_admin'
        || effectiveRole === 'strata_admin'
        || effectiveRole === 'strata_manager';
    const isSuperAdmin = isAdmin();
    const [resumeBanner, setResumeBanner] = useState(null);
    const [resumeError, setResumeError] = useState(null);

    // Overview
    const [organisations, setOrganisations] = useState([]);
    const [loading, setLoading] = useState(true);
    const [overviewError, setOverviewError] = useState(null);

    // Wizard
    const [wizardMode, setWizardMode] = useState(false);
    const [sessionId, setSessionId] = useState(null);
    const [track, setTrack] = useState(null);
    const [stepIdx, setStepIdx] = useState(0);
    const [stepData, setStepData] = useState({});
    const [gateCleared, setGateCleared] = useState({});
    const [uploadResults, setUploadResults] = useState({});
    const [finalizeResult, setFinalizeResult] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [wizardError, setWizardError] = useState(null);

    const activeSteps = useMemo(() => getActiveSteps(track), [track]);
    const currentStep = activeSteps[stepIdx] ?? activeSteps[0];
    const progress = Math.round(((stepIdx + 1) / activeSteps.length) * 100);

    // Group active steps by phase for sidebar
    const phaseGroups = useMemo(() => {
        const groups = {};
        activeSteps.forEach((s, i) => {
            if (!groups[s.phase]) groups[s.phase] = [];
            groups[s.phase].push({ ...s, idx: i });
        });
        return groups;
    }, [activeSteps]);

    useEffect(() => {
        if (authLoading) return;
        if (!canAccess) {
            setLoading(false);
            return;
        }
        if (!isSuperAdmin) {
            const tenantId = user?.tenant_id || '';
            setOrganisations(tenantId ? [{
                tenant_id: tenantId,
                tenant_name: user?.tenant_name || 'Current organisation',
            }] : []);
            setStepData(prev => ({
                ...prev,
                org_details: {
                    ...(prev.org_details || {}),
                    tenant_id: tenantId,
                },
            }));
            setLoading(false);
            return;
        }

        let active = true;
        (async () => {
            setLoading(true);
            try {
                const res = await api.get('/admin/sm-organisations', { params: { include_self_managed: true, status: 'active' } });
                if (!active) return;
                setOrganisations(res.data?.items || []);
            } catch {
                if (!active) return;
                setOverviewError('Failed to load organisations.');
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [api, authLoading, canAccess, isSuperAdmin, user?.tenant_id, user?.tenant_name]);

    // Resume-by-link (GAP-ONBOARD-002 Item 2, 2026-08-19): the Quick Create form
    // (BuildingOnboardingForm.jsx) creates a scheme + onboarding session directly,
    // bypassing this wizard's own Setup-phase steps entirely — it has no equivalent
    // for the wizard's stricter multi-file historical-import contract, so its success
    // screen instead deep-links here with ?session_id=<id>&step=<key> so the same
    // already-created session can be resumed straight at the Historical-records phase.
    // KNOWN LIMITATION: this validates the session belongs to the current user and
    // jumps directly to the requested step, but does NOT rehydrate stepData for the
    // Setup-phase steps (org_details, fund_structure, bank_accounts, ...) — those were
    // never captured by the Quick Create form's simpler payload, so they render blank
    // if the user navigates back. The underlying scheme record itself is unaffected;
    // only this wizard's local step-by-step UI state doesn't reflect it.
    useEffect(() => {
        if (authLoading || !canAccess) return;
        const resumeSessionId = searchParams?.get('session_id');
        if (!resumeSessionId) return;

        let active = true;
        (async () => {
            try {
                await api.get(`/onboarding/scheme/${resumeSessionId}`);
                if (!active) return;
                const resumeTrack = searchParams?.get('track') === 'A' ? 'A' : 'B';
                const requestedStepKey = searchParams?.get('step') || 'levy_history';
                const steps = getActiveSteps(resumeTrack);
                const foundIdx = steps.findIndex(s => s.key === requestedStepKey);
                setSessionId(resumeSessionId);
                setTrack(resumeTrack);
                setStepIdx(foundIdx >= 0 ? foundIdx : 0);
                setWizardMode(true);
                setResumeBanner(
                    'Resumed your onboarding session — continue below. Setup-phase steps '
                    + "(Organisation, Fund Structure, Bank Accounts, etc.) won't show "
                    + 'previously-entered data here since this scheme was created via the '
                    + 'Quick Create form; the scheme record itself is unaffected.'
                );
            } catch (err) {
                if (!active) return;
                setResumeError(
                    err.response?.status === 404
                        ? 'That onboarding session could not be found — it may have already been finalised.'
                        : 'Could not resume that onboarding session. Please start a new one below.'
                );
            }
        })();
        return () => { active = false; };
    }, [api, authLoading, canAccess, searchParams]);

    const handleStepDataChange = useCallback((stepKey, field, value) => {
        setStepData(prev => ({
            ...prev,
            [stepKey]: { ...(prev[stepKey] || {}), [field]: value },
        }));
    }, []);

    const flatStepData = useMemo(() => {
        const flat = {};
        Object.values(stepData).forEach(d => Object.assign(flat, d));
        return flat;
    }, [stepData]);

    const saveStepToServer = useCallback(async (idx, data) => {
        if (!sessionId) return;
        try {
            await api.patch(`/onboarding/scheme/${sessionId}`, { current_step: idx + 1, step_data: data });
        } catch (err) {
            console.warn('Failed to persist step data', err);
        }
    }, [api, sessionId]);

    const canGoNext = useCallback(() => {
        if (!currentStep) return false;
        if (GATE_KEYS.has(currentStep.key)) return !!gateCleared[currentStep.key];
        if (currentStep.key === 'track_select') return !!track;
        if (currentStep.key === 'attestation') {
            const d = stepData['attestation'] || {};
            return !!(d.attested && d.attestation_name?.trim());
        }
        return true;
    }, [currentStep, gateCleared, track, stepData]);

    const handleNext = useCallback(async () => {
        if (!canGoNext() || stepIdx >= activeSteps.length - 1) return;
        await saveStepToServer(stepIdx, flatStepData);
        setStepIdx(prev => prev + 1);
        setWizardError(null);
    }, [canGoNext, stepIdx, activeSteps.length, saveStepToServer, flatStepData]);

    const handlePrev = useCallback(() => {
        setStepIdx(prev => Math.max(0, prev - 1));
        setWizardError(null);
    }, []);

    const handleStartWizard = useCallback(async () => {
        const d = stepData['org_details'] || {};
        const payload = buildSchemeStartPayload(d);
        if (!payload.scheme_number || !payload.scheme_name || !payload.jurisdiction) {
            setWizardError('Please fill in Scheme Number, Scheme Name, and Jurisdiction before starting.');
            return;
        }
        setSubmitting(true);
        setWizardError(null);
        try {
            const res = await api.post('/onboarding/scheme/start', payload);
            setSessionId(res.data.session_id);
            setStepIdx(1);
            toast.success('Onboarding session created.');
        } catch (err) {
            const detail = extractOnboardingErrorDetail(err);
            setWizardError(detail);
            toast.error(detail);
        } finally {
            setSubmitting(false);
        }
    }, [api, stepData]);

    const handleTrackChange = useCallback((newTrack) => {
        setTrack(newTrack);
    }, []);

    const handleGateCleared = useCallback((key) => {
        setGateCleared(prev => ({ ...prev, [key]: true }));
    }, []);

    const handleUploadResult = useCallback((key, result) => {
        setUploadResults(prev => ({ ...prev, [key]: result }));
    }, []);

    const handleFinalize = useCallback(async () => {
        if (!sessionId) return;
        setSubmitting(true);
        setWizardError(null);
        try {
            const funds = buildCurrentBalancesFunds(flatStepData);
            const levyScheduleCurrent = stepData['current_levy_schedule'] || null;
            if (funds || levyScheduleCurrent) {
                // levy_schedule_current is preserved for reference/audit. The levy
                // *totals* (total_admin_levy / total_cw_levy, already in step_data from
                // the Current Levy Schedule step) are now consumed at finalize, which
                // creates the admin + capital-works levy_plans rows for the building
                // (GAP-ONBOARD-002 Item 1). funds carries the fund opening balances.
                await api.patch(`/onboarding/scheme/${sessionId}`, {
                    step_data: {
                        ...(funds ? { funds } : {}),
                        ...(levyScheduleCurrent ? { levy_schedule_current: levyScheduleCurrent } : {}),
                    },
                });
            }
            const ec = (stepData['ec_invitations']?.ec_members || []).filter(m => m.email);
            const ownerInvites = Object.values(stepData['owner_invitations']?.invite_owners || {}).some(Boolean);
            const res = await api.post(`/onboarding/scheme/${sessionId}/finalize`, {
                send_owner_invites: ownerInvites,
                send_ec_invites: ec.length > 0,
            });
            setFinalizeResult(res.data);
            setStepIdx(prev => Math.min(activeSteps.length - 1, prev + 1));
            toast.success('Scheme finalised successfully!');
        } catch (err) {
            const detail = err.response?.data?.detail || 'Finalisation failed.';
            setWizardError(detail);
            toast.error(detail);
        } finally {
            setSubmitting(false);
        }
    }, [api, sessionId, stepData, activeSteps.length, flatStepData]);

    if (authLoading || loading) {
        return (
            <div className="flex items-center justify-center py-24">
                <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
        );
    }

    if (!canAccess) {
        return (
            <div className="p-6 max-w-2xl mx-auto">
                <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4"/>
                    <AlertDescription>Access restricted to Super Admins, Strata Admins, and Strata Managers.</AlertDescription>
                </Alert>
            </div>
        );
    }

    // ── Overview mode ───────────────────────────────────────────────────────────
    if (!wizardMode) {
        return (
            <div className="max-w-4xl mx-auto space-y-8">
                <div className="flex items-center gap-4">
                    <Button variant="ghost" size="sm" asChild>
                        <a href="/admin"><ArrowLeft className="h-4 w-4 mr-1" /> Back to Portfolio</a>
                    </Button>
                </div>
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Scheme Onboarding</h1>
                    <p className="text-muted-foreground text-lg mt-1">Start a new scheme onboarding session or resume one in progress.</p>
                </div>
                {overviewError && <Alert variant="destructive"><AlertDescription>{overviewError}</AlertDescription></Alert>}
                {resumeError && <Alert variant="destructive"><AlertDescription>{resumeError}</AlertDescription></Alert>}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <Card className="border-2 border-dashed border-primary/30 hover:border-primary/60 transition-all cursor-pointer" onClick={() => setWizardMode(true)}>
                        <CardContent className="p-8 flex flex-col items-center text-center gap-4">
                            <div className="h-14 w-14 rounded-full bg-primary/10 flex items-center justify-center">
                                <Rocket className="h-7 w-7 text-primary" />
                            </div>
                            <div>
                                <CardTitle className="mb-1">Start New Scheme Onboarding</CardTitle>
                                <CardDescription>Walk through the 32-step wizard to onboard a new strata scheme. Choose Track A (current balances) or Track B (full historical records).</CardDescription>
                            </div>
                            <Button className="mt-2">Begin Onboarding Wizard</Button>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Active Organisations</CardTitle>
                            <CardDescription>{organisations.length} organisation{organisations.length !== 1 ? 's' : ''} available</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {organisations.slice(0, 6).map(org => (
                                <div key={org.tenant_id} className="flex items-center justify-between p-3 border rounded-lg hover:bg-slate-50">
                                    <div>
                                        <p className="text-sm font-medium">{org.tenant_name}</p>
                                        <p className="text-xs text-muted-foreground">{org.scheme_count ?? 0} scheme(s)</p>
                                    </div>
                                    <Badge variant={org.is_self_managed ? 'secondary' : 'outline'}>{org.is_self_managed ? 'Self-managed' : 'SM firm'}</Badge>
                                </div>
                            ))}
                            {organisations.length === 0 && <p className="text-sm text-muted-foreground py-2">No organisations found.</p>}
                        </CardContent>
                    </Card>
                </div>
            </div>
        );
    }

    // ── Wizard mode ─────────────────────────────────────────────────────────────
    const StepIcon = currentStep?.icon || Building2;
    const isFirstStep = stepIdx === 0;
    const isLastStep = stepIdx === activeSteps.length - 1;
    const isGateLocked = GATE_KEYS.has(currentStep?.key) && !gateCleared[currentStep?.key];

    return (
        <div className="max-w-6xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <Button variant="ghost" size="sm" onClick={() => setWizardMode(false)}>
                    <ArrowLeft className="h-4 w-4 mr-1" /> Back to Overview
                </Button>
                <div className="flex items-center gap-3">
                    {sessionId && <Badge variant="outline" className="text-[10px]">Session: {sessionId.slice(0, 8)}…</Badge>}
                    {track && <Badge variant={track === 'A' ? 'secondary' : 'default'}>Track {track}</Badge>}
                    <span className="text-sm font-bold text-primary">{progress}%</span>
                    <div className="h-2 w-32 bg-slate-100 rounded-full overflow-hidden border">
                        <div className="h-full bg-primary transition-all duration-500" style={{ width: `${progress}%` }} />
                    </div>
                </div>
            </div>

            {resumeBanner && (
                <Alert>
                    <AlertDescription>{resumeBanner}</AlertDescription>
                </Alert>
            )}

            {wizardError && (
                <Alert variant="destructive">
                    <AlertDescription>{wizardError}</AlertDescription>
                </Alert>
            )}

            <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                {/* Sidebar */}
                <div className="md:col-span-1 space-y-4">
                    {Object.entries(phaseGroups).map(([phase, steps]) => (
                        <div key={phase}>
                            <p className="text-[10px] font-black uppercase tracking-widest text-muted-foreground mb-1.5 px-1">{phase}</p>
                            <div className="space-y-1">
                                {steps.map(({ key, name, idx, icon: Icon }) => {
                                    const isActive = idx === stepIdx;
                                    const isPast = idx < stepIdx;
                                    const isGate = GATE_KEYS.has(key);
                                    return (
                                        <button
                                            key={key}
                                            onClick={() => idx <= stepIdx && setStepIdx(idx)}
                                            disabled={idx > stepIdx}
                                            className={cn(
                                                'w-full text-left px-3 py-2.5 rounded-lg text-xs font-medium flex items-center gap-2 transition-all',
                                                isActive ? 'bg-primary text-primary-foreground shadow-sm' :
                                                isPast ? 'text-emerald-700 bg-emerald-50 hover:bg-emerald-100' :
                                                'text-slate-400 cursor-not-allowed'
                                            )}
                                        >
                                            {isPast && !isActive ? (
                                                <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0 text-emerald-500" />
                                            ) : isGate && !gateCleared[key] ? (
                                                <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
                                            ) : (
                                                <Icon className="h-3.5 w-3.5 flex-shrink-0" />
                                            )}
                                            <span className="truncate">{name}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    ))}
                </div>

                {/* Step content */}
                <Card className="md:col-span-3 border-none shadow-lg bg-white">
                    <CardHeader className="border-b bg-slate-50/50">
                        <div className="flex items-center gap-3 mb-2">
                            <div className="p-2 rounded-lg bg-primary/10 text-primary">
                                <StepIcon size={20} />
                            </div>
                            <Badge variant="outline" className="text-[10px] uppercase tracking-wider">
                                Step {stepIdx + 1} of {activeSteps.length}
                            </Badge>
                            {isGateLocked && <Badge variant="destructive" className="text-[10px]">Gate Locked</Badge>}
                        </div>
                        <CardTitle>{currentStep?.name}</CardTitle>
                        <CardDescription className="capitalize">{currentStep?.phase} — {currentStep?.type}</CardDescription>
                    </CardHeader>
                    <CardContent className="p-8">
                        <StepContent
                            step={currentStep}
                            stepData={stepData}
                            onChange={handleStepDataChange}
                            organisations={organisations}
                            track={track}
                            onTrackChange={handleTrackChange}
                            sessionId={sessionId}
                            uploadResults={uploadResults}
                            onUploadResult={handleUploadResult}
                            gateCleared={gateCleared}
                            onGateCleared={handleGateCleared}
                            onFinalize={handleFinalize}
                            submitting={submitting}
                            finalizeResult={finalizeResult}
                            api={api}
                        />
                        {/* Navigation */}
                        {currentStep?.key !== 'go_live' && (
                            <div className="pt-8 border-t mt-8 flex items-center justify-between">
                                <Button variant="outline" onClick={handlePrev} disabled={isFirstStep}>
                                    <ArrowLeft className="h-4 w-4 mr-1" /> Previous
                                </Button>
                                {currentStep?.key === 'org_details' && !sessionId ? (
                                    <Button onClick={handleStartWizard} disabled={submitting}>
                                        {submitting ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : null}
                                        Create Session & Continue
                                    </Button>
                                ) : currentStep?.key !== 'finalize' ? (
                                    <Button onClick={handleNext} disabled={!canGoNext() || isLastStep || submitting}>
                                        {isGateLocked ? (
                                            <><AlertTriangle className="h-4 w-4 mr-1" /> Gate Locked</>
                                        ) : (
                                            <>Next <ChevronRight className="h-4 w-4 ml-1" /></>
                                        )}
                                    </Button>
                                ) : null}
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    );
};

export default OnboardingWizard;
