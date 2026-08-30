"use client";

// @featuretrace:trust-accounting — Per-building trust account settings, interest posting,
// advance-payment interest, interest history, and account creation (V2 API only).
// Layer: frontend
// Data flow: this page → GET/POST/PATCH /api/trust/v2/accounts* → Mongo trust_accounts_v2
//            (building-scoped, via building_id in the caller's JWT).
// Related: backend/routers/trust_phase1.py
//           backend/routers/onboarding.py (finalize also creates trust_accounts_v2 rows)
// Collection: trust_accounts_v2
// Tests: tests/frontend/unit/pages/dashboard/TrustBankAccountsPage.test.tsx
/**
 * TrustBankAccountsPage
 * ---------------------
 * Manage per-building trust account bank settings:
 *  - Tab 1: Account Settings — configure interest rate, account category, bank details
 *  - Tab 2: Post Interest    — preview and post monthly bank interest income
 *  - Tab 3: Advance Payments — list levies paid early and interest earned by the OC
 *  - Tab 4: Interest History — timeline of all interest postings
 */

import {useCallback, useEffect, useState} from "react";
import {useSession} from "next-auth/react";
import axios from "axios";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from "@/components/ui/select";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from "@/components/ui/table";
import {AlertCircle, CheckCircle2, Clock, Landmark, Pencil, PiggyBank, RefreshCw, TrendingUp,} from "lucide-react";
import {toast} from "sonner";

import {formatMoneyFromCents} from '@/lib/currency';
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8003";

interface TrustAccount {
    id: string;
    building_id: string;
    account_type: "admin_fund" | "sinking_fund" | "special_purpose";
    bank_name: string;
    account_name: string;
    bsb: string;
    account_number_masked: string;
    account_category: "transaction" | "savings" | "term_deposit" | "offset";
    interest_rate_pa: number;
    interest_calc_method: "daily_balance" | "monthly_balance";
    term_deposit_maturity_date?: string | null;
    computed_balance_cents: number;
    computed_balance_display: string;
    current_balance_cents: number;
    current_balance_display: string;
    opening_balance_cents: number;
    last_reconciled_balance_cents: number;
    last_reconciled_balance_display: string;
    last_reconciled_at?: string | null;
    reconciliation_gap_cents: number;
    reconciliation_gap_display: string;
    last_interest_posted_at?: string | null;
    last_interest_period_end?: string | null;
    interest_ytd_cents: number;
    interest_ytd_display: string;
    deft_biller_code?: string | null;
    is_active: boolean;
    notes?: string | null;
}

interface AdvancePayment {
    levy_schedule_id: string;
    unit_number: string;
    lot_number: number;
    quarter: string;
    financial_year: string;
    due_date: string;
    paid_date: string;
    advance_days: number;
    total_paid_cents: number;
    total_paid_display: string;
    interest_earned_cents: number;
    interest_earned_display: string;
    interest_posted: boolean;
    interest_posted_at?: string | null;
}

interface InterestPosting {
    id: string;
    account_id: string;
    account_name: string;
    period_start: string;
    period_end: string;
    total_interest_cents: number;
    total_interest_display: string;
    admin_interest_display: string;
    sinking_interest_display: string;
    posted_at: string;
    posted_by: string;
    is_auto_posted: boolean;
    reference?: string;
}

const ACCOUNT_CATEGORY_LABELS: Record<string, string> = {
    transaction: "Transaction Account",
    savings: "High-Interest Savings",
    term_deposit: "Term Deposit",
    offset: "Offset Account",
};

const FUND_TYPE_LABELS: Record<string, string> = {
    admin_fund: "Admin Fund",
    sinking_fund: "Sinking Fund",
    special_purpose: "Special Purpose",
};
/**
 * @generated FunctionHeader
 * Function: categoryBadge
 * Path: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const categoryBadge = (cat: string) => {
    const colours: Record<string, string> = {
        transaction: "bg-slate-100 text-slate-700",
        savings: "bg-emerald-100 text-emerald-700",
        term_deposit: "bg-blue-100 text-blue-700",
        offset: "bg-purple-100 text-purple-700",
    };
    return (
        <Badge className={colours[cat] || "bg-gray-100 text-gray-700"}>
            {ACCOUNT_CATEGORY_LABELS[cat] || cat}
        </Badge>
    );
};
/**
 * @generated FunctionHeader
 * Function: TrustBankAccountsPage
 * Path: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TrustBankAccountsPage() {
    const sessionResult = useSession();
    const session = sessionResult?.data as any;
    const token = session?.accessToken;
    const buildingId = session?.user?.data?.building_id ?? "";
    const userRole = session?.user?.data?.role ?? "";

    // Kept in sync with backend TRUST_MANAGE_ROLES (backend/routers/trust_phase1.py) — that
    // set includes strata_admin, which was missing here (pre-existing gap, fixed during audit):
    // a strata_admin's requests would have been accepted by the backend but the button/link
    // to make them was invisible on this page.
    const canManage = ["super_admin", "strata_admin", "strata_manager", "treasurer"].includes(userRole);

    const [accounts, setAccounts] = useState<TrustAccount[]>([]);
    const [advancePayments, setAdvancePayments] = useState<AdvancePayment[]>([]);
    const [advanceInterestSummary, setAdvanceInterestSummary] = useState<{
        unposted_interest_cents: number;
        unposted_interest_display: string;
    } | null>(null);
    const [interestHistory, setInterestHistory] = useState<InterestPosting[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("accounts");

    // Edit account dialog
    const [editAccount, setEditAccount] = useState<TrustAccount | null>(null);
    const [editForm, setEditForm] = useState<Partial<TrustAccount>>({});
    const [isSaving, setIsSaving] = useState(false);
    const [bsbLookupState, setBsbLookupState] = useState<"idle" | "loading" | "found" | "not_found">("idle");
    const [bsbBankHint, setBsbBankHint] = useState<string>("");

    // Create account dialog
    const [showCreateDialog, setShowCreateDialog] = useState(false);
    const [createForm, setCreateForm] = useState({
        account_type: "admin_fund",
        bank_name: "",
        account_name: "",
        bsb: "",
        account_number: "",
        account_category: "transaction",
        opening_balance: "",
    });
    const [isCreating, setIsCreating] = useState(false);

    // Post interest dialog
    const [postInterestAccount, setPostInterestAccount] = useState<TrustAccount | null>(null);
    const [interestPeriodStart, setInterestPeriodStart] = useState("");
    const [interestPeriodEnd, setInterestPeriodEnd] = useState("");
    const [interestAmountOverride, setInterestAmountOverride] = useState("");
    const [forecastData, setForecastData] = useState<any>(null);
    const [isForecastLoading, setIsForecastLoading] = useState(false);
    const [isPosting, setIsPosting] = useState(false);

    const api = useCallback(
        (path: string, config = {}) =>
            axios.get(`${BACKEND_URL}/api/trust/v2${path}`, {
                ...config,
                headers: {Authorization: `Bearer ${token}`},
            }),
        [token]
    );

    const apiPost = useCallback(
        (path: string, data: any) =>
            axios.post(`${BACKEND_URL}/api/trust/v2${path}`, data, {
                headers: {Authorization: `Bearer ${token}`},
            }),
        [token]
    );

    const apiPatch = useCallback(
        (path: string, data: any) =>
            axios.patch(`${BACKEND_URL}/api/trust/v2${path}`, data, {
                headers: {Authorization: `Bearer ${token}`},
            }),
        [token]
    );

    const loadData = useCallback(async () => {
        if (!session) return;
        setIsLoading(true);
        try {
            const [accountsRes, advanceRes] = await Promise.allSettled([
                api("/accounts"),
                api("/levies/advance-payments?limit=100"),
            ]);

            if (accountsRes.status === "fulfilled") {
                setAccounts(accountsRes.value.data.data ?? []);
            }
            if (advanceRes.status === "fulfilled") {
                setAdvancePayments(advanceRes.value.data.data?.items ?? []);
                setAdvanceInterestSummary({
                    unposted_interest_cents: advanceRes.value.data.data?.unposted_interest_cents ?? 0,
                    unposted_interest_display: advanceRes.value.data.data?.unposted_interest_display ?? "",
                });
            }
        } finally {
            setIsLoading(false);
        }
    }, [session, api]);

    const loadInterestHistory = useCallback(async () => {
        if (!session) return;
        try {
            const res = await axios.get(`${BACKEND_URL}/api/trust/v2/accounts/interest-history`, {
                headers: {Authorization: `Bearer ${token}`},
            }).catch(() => ({data: {data: []}}));
            setInterestHistory(res.data?.data ?? []);
        } catch { /* silently fail */
        }
    }, [session, token]);

    useEffect(() => {
        loadData();
    }, [loadData]);

    useEffect(() => {
        if (activeTab === "history") loadInterestHistory();
    }, [activeTab, loadInterestHistory]);

    // ── Set default period (previous calendar month) on dialog open ──────────
    useEffect(() => {
        if (postInterestAccount) {
            const now = new Date();
            const firstOfMonth = new Date(now.getFullYear(), now.getMonth(), 1);
            const prevMonthEnd = new Date(firstOfMonth.getTime() - 86400000);
            const prevMonthStart = new Date(prevMonthEnd.getFullYear(), prevMonthEnd.getMonth(), 1);
            setInterestPeriodStart(prevMonthStart.toISOString().slice(0, 10));
            setInterestPeriodEnd(prevMonthEnd.toISOString().slice(0, 10));
            setForecastData(null);
            setInterestAmountOverride("");
        }
    }, [postInterestAccount]);
    /**
     * @generated FunctionHeader
     * Function: handleFetchForecast
     * Path: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleFetchForecast = async () => {
        if (!postInterestAccount || !interestPeriodStart || !interestPeriodEnd) return;
        setIsForecastLoading(true);
        try {
            const res = await api(
                `/accounts/${postInterestAccount.id}/interest-forecast?period_start=${interestPeriodStart}&period_end=${interestPeriodEnd}`
            );
            setForecastData(res.data.data);
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to fetch forecast.");
        } finally {
            setIsForecastLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handlePostInterest
     * Path: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePostInterest = async () => {
        if (!postInterestAccount) return;
        setIsPosting(true);
        try {
            const body: any = {
                period_start: interestPeriodStart,
                period_end: interestPeriodEnd,
            };
            if (interestAmountOverride) {
                body.amount_cents = Math.round(parseFloat(interestAmountOverride) * 100);
            }
            await apiPost(`/accounts/${postInterestAccount.id}/post-interest`, body);
            toast.success("Interest income posted successfully.");
            setPostInterestAccount(null);
            loadData();
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to post interest.");
        } finally {
            setIsPosting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSaveAccount
     * Path: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveAccount = async () => {
        if (!editAccount) return;
        setIsSaving(true);
        try {
            const patch: any = {};
            if (editForm.bank_name !== undefined) patch.bank_name = editForm.bank_name;
            if (editForm.account_name !== undefined) patch.account_name = editForm.account_name;
            if (editForm.bsb !== undefined) patch.bsb = editForm.bsb;
            if (editForm.account_number_masked !== undefined) patch.account_number_masked = editForm.account_number_masked;
            if (editForm.account_category !== undefined) patch.account_category = editForm.account_category;
            if (editForm.interest_rate_pa !== undefined) patch.interest_rate_pa = editForm.interest_rate_pa;
            if (editForm.interest_calc_method !== undefined) patch.interest_calc_method = editForm.interest_calc_method;
            if (editForm.term_deposit_maturity_date !== undefined) patch.term_deposit_maturity_date = editForm.term_deposit_maturity_date;
            if (editForm.deft_biller_code !== undefined) patch.deft_biller_code = editForm.deft_biller_code;
            if (editForm.notes !== undefined) patch.notes = editForm.notes;

            await apiPatch(`/accounts/${editAccount.id}`, patch);
            toast.success("Account settings updated.");
            setEditAccount(null);
            loadData();
        } catch (e: any) {
            toast.error(e.response?.data?.detail || "Failed to save account.");
        } finally {
            setIsSaving(false);
        }
    };

    const resetCreateForm = () => setCreateForm({
        account_type: "admin_fund",
        bank_name: "",
        account_name: "",
        bsb: "",
        account_number: "",
        account_category: "transaction",
        opening_balance: "",
    });

    const handleCreateAccount = async () => {
        const {account_type, bank_name, account_name, bsb, account_number, account_category, opening_balance} = createForm;
        if (!bank_name.trim() || !account_name.trim() || !bsb.trim() || !account_number.trim()) {
            toast.error("Bank name, account name, BSB, and account number are all required.");
            return;
        }
        const bsbDigits = bsb.replace(/\D/g, "");
        if (bsbDigits.length !== 6) {
            toast.error("BSB must be 6 digits.");
            return;
        }
        const accountDigits = account_number.replace(/\D/g, "");
        if (accountDigits.length < 4 || accountDigits.length > 10) {
            toast.error("Account number must be 4-10 digits.");
            return;
        }
        setIsCreating(true);
        try {
            const maskedNumber = `****${accountDigits.slice(-4)}`;
            await apiPost("/accounts", {
                account_type,
                bank_name: bank_name.trim(),
                account_name: account_name.trim(),
                bsb,
                account_number_masked: maskedNumber,
                account_category,
                current_balance_cents: opening_balance ? Math.round(parseFloat(opening_balance) * 100) : 0,
            });
            toast.success("Trust account created.");
            setShowCreateDialog(false);
            resetCreateForm();
            loadData();
        } catch (e: any) {
            // POST /trust/v2/accounts is gated by require_feature("trust_accounting"), whose
            // 403 body is a typed {code, message, feature_key, retryable} object, not a plain
            // string — toast.error(detail) on that shape renders unreadably. Unwrap it.
            const detail = e.response?.data?.detail;
            const message = typeof detail === "string" ? detail : (detail?.message || detail?.detail || "Failed to create account.");
            toast.error(message);
        } finally {
            setIsCreating(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openEdit
     * Path: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEdit = (acc: TrustAccount) => {
        setEditAccount(acc);
        setEditForm({...acc});
        setBsbLookupState("idle");
        setBsbBankHint("");
    };
    /**
     * @generated FunctionHeader
     * Function: handleBsbChange
     * Path: frontend/src/pages/dashboard/TrustBankAccountsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleBsbChange = async (raw: string) => {
        setEditForm(f => ({...f, bsb: raw}));
        const clean = raw.replace(/\D/g, "");
        if (clean.length !== 6) {
            setBsbLookupState("idle");
            setBsbBankHint("");
            return;
        }
        setBsbLookupState("loading");
        try {
            const res = await axios.get(
                `${BACKEND_URL}/api/trust/v2/bsb/${clean}`,
                {headers: {Authorization: `Bearer ${token}`}},
            );
            const d = res.data?.data;
            if (d?.bank) {
                setBsbBankHint(`${d.bank} — ${d.branch}, ${d.suburb} ${d.state}`);
                setBsbLookupState("found");
                // Auto-fill bank_name only if it's currently blank or unchanged from original
                setEditForm(f => ({
                    ...f,
                    bsb: d.bsb,
                    bank_name: f.bank_name && f.bank_name !== editAccount?.bank_name
                        ? f.bank_name
                        : d.bank,
                }));
            } else {
                setBsbLookupState("not_found");
                setBsbBankHint("");
            }
        } catch {
            setBsbLookupState("not_found");
            setBsbBankHint("");
        }
    };

    // ── Summary stats ──────────────────────────────────────────────────────────
    const totalBalance = accounts.reduce((s, a) => s + (a.computed_balance_cents ?? a.current_balance_cents ?? 0), 0);
    const totalYTDInterest = accounts.reduce((s, a) => s + (a.interest_ytd_cents || 0), 0);
    // Trust backend's own total (trust_phase1.py::list_advance_payments) rather than
    // re-summing the (paginated) advancePayments array client-side — avoids drift if
    // either side's "unposted" predicate changes independently.
    const unpostedAdvanceInterest = advanceInterestSummary?.unposted_interest_cents ?? 0;
    const advanceCount = advancePayments.filter(p => !p.interest_posted).length;

    if (isLoading) {
        return (
            <div className="flex items-center justify-center h-64">
                <RefreshCw className="animate-spin h-6 w-6 text-muted-foreground"/>
            </div>
        );
    }

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
                        <Landmark className="h-6 w-6 text-primary"/>
                        Trust Account & Bank Accounts
                    </h1>
                    <p className="text-muted-foreground text-sm mt-1">
                        Configure bank account settings, track interest income, and manage advance levy payments.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    {canManage && (
                        <Button size="sm" onClick={() => setShowCreateDialog(true)} data-testid="add-trust-account">
                            <Landmark className="h-4 w-4 mr-1"/> Add Account
                        </Button>
                    )}
                    <Button variant="outline" size="sm" onClick={loadData}>
                        <RefreshCw className="h-4 w-4 mr-1"/> Refresh
                    </Button>
                </div>
            </div>

            {/* Summary Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <Landmark className="h-4 w-4"/> Total Trust Balance
                        </div>
                        <div className="text-2xl font-bold mt-1">{formatMoneyFromCents(totalBalance)}</div>
                        <div className="text-xs text-muted-foreground mt-1">{accounts.length} active accounts</div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <TrendingUp className="h-4 w-4 text-emerald-600"/> Interest Earned YTD
                        </div>
                        <div className="text-2xl font-bold mt-1 text-emerald-700">{formatMoneyFromCents(totalYTDInterest)}</div>
                        <div className="text-xs text-muted-foreground mt-1">Credited to OC funds</div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <Clock className="h-4 w-4 text-amber-600"/> Advance Payment Interest
                        </div>
                        <div className="text-2xl font-bold mt-1 text-amber-700">{formatMoneyFromCents(unpostedAdvanceInterest)}</div>
                        <div className="text-xs text-muted-foreground mt-1">{advanceCount} payments pending post</div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-muted-foreground text-sm">
                            <PiggyBank className="h-4 w-4 text-blue-600"/> Accounts Earning Interest
                        </div>
                        <div className="text-2xl font-bold mt-1 text-blue-700">
                            {accounts.filter(a => a.interest_rate_pa > 0).length}
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">
                            of {accounts.length} configured accounts
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="accounts">Account Settings</TabsTrigger>
                    <TabsTrigger value="post-interest">Post Interest</TabsTrigger>
                    <TabsTrigger value="advance-payments">
                        Advance Payments
                        {advanceCount > 0 && (
                            <Badge className="ml-2 bg-amber-100 text-amber-700 text-xs">{advanceCount}</Badge>
                        )}
                    </TabsTrigger>
                    <TabsTrigger value="history" onClick={() => loadInterestHistory()}>Interest History</TabsTrigger>
                </TabsList>

                {/* ── Tab 1: Account Settings ────────────────────────────────────── */}
                <TabsContent value="accounts" className="space-y-4">
                    {accounts.length === 0 ? (
                        <Card>
                            <CardContent className="pt-6 text-center text-muted-foreground">
                                No trust accounts configured yet.
                                {canManage && (
                                    <>
                                        {" "}
                                        <button
                                            type="button"
                                            className="text-primary underline underline-offset-2"
                                            onClick={() => setShowCreateDialog(true)}
                                        >
                                            Add one now.
                                        </button>
                                    </>
                                )}
                            </CardContent>
                        </Card>
                    ) : (
                        accounts.map(acc => (
                            <Card key={acc.id}
                                  className={`border-l-4 ${acc.account_type === "admin_fund" ? "border-l-blue-500" : acc.account_type === "sinking_fund" ? "border-l-emerald-500" : "border-l-purple-500"}`}>
                                <CardHeader className="pb-2">
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <CardTitle className="text-base">{acc.account_name}</CardTitle>
                                            <CardDescription>{FUND_TYPE_LABELS[acc.account_type] || acc.account_type}</CardDescription>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            {categoryBadge(acc.account_category || "transaction")}
                                            {canManage && (
                                                <Button variant="outline" size="sm" onClick={() => openEdit(acc)}>
                                                    <Pencil className="h-3 w-3 mr-1"/> Edit
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                        <div>
                                            <div className="text-muted-foreground">Bank</div>
                                            <div className="font-medium">{acc.bank_name || "—"}</div>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">BSB / Account</div>
                                            <div
                                                className="font-medium font-mono">{acc.bsb} / {acc.account_number_masked}</div>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">System Balance</div>
                                            <div
                                                className="font-medium">{acc.computed_balance_display || acc.current_balance_display}</div>
                                            {acc.last_reconciled_balance_cents !== undefined && (
                                                <div className="text-xs text-muted-foreground mt-0.5">
                                                    Bank: {acc.last_reconciled_balance_display}
                                                    {acc.reconciliation_gap_cents !== 0 && (
                                                        <span
                                                            className={`ml-1 font-semibold ${acc.reconciliation_gap_cents > 0 ? 'text-amber-600' : 'text-blue-600'}`}>
                                                            ({acc.reconciliation_gap_display} gap)
                                                        </span>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">Interest Rate (p.a.)</div>
                                            <div
                                                className={`font-medium ${acc.interest_rate_pa > 0 ? "text-emerald-700" : "text-muted-foreground"}`}>
                                                {acc.interest_rate_pa > 0 ? `${(acc.interest_rate_pa * 100).toFixed(2)}%` : "Not earning interest"}
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">Interest YTD</div>
                                            <div
                                                className="font-medium text-emerald-700">{acc.interest_ytd_display || "$0.00"}</div>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">Last Interest Posted</div>
                                            <div className="font-medium">
                                                {acc.last_interest_period_end
                                                    ? `Period ending ${acc.last_interest_period_end}`
                                                    : "—"}
                                            </div>
                                        </div>
                                        {acc.account_category === "term_deposit" && (
                                            <div>
                                                <div className="text-muted-foreground">Maturity Date</div>
                                                <div
                                                    className="font-medium">{acc.term_deposit_maturity_date || "—"}</div>
                                            </div>
                                        )}
                                        {acc.notes && (
                                            <div className="col-span-2">
                                                <div className="text-muted-foreground">Notes</div>
                                                <div className="font-medium">{acc.notes}</div>
                                            </div>
                                        )}
                                    </div>
                                    {canManage && acc.interest_rate_pa > 0 && (
                                        <div className="mt-3 pt-3 border-t">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                className="text-emerald-700 border-emerald-300 hover:bg-emerald-50"
                                                onClick={() => {
                                                    setPostInterestAccount(acc);
                                                    setActiveTab("post-interest");
                                                }}
                                            >
                                                <TrendingUp className="h-3 w-3 mr-1"/> Post Interest Income
                                            </Button>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        ))
                    )}
                </TabsContent>

                {/* ── Tab 2: Post Interest ───────────────────────────────────────── */}
                <TabsContent value="post-interest" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Post Monthly Bank Interest Income</CardTitle>
                            <CardDescription>
                                Select a trust account and period, then preview the calculated interest before posting.
                                Interest is split proportionally between Admin Fund and Sinking Fund.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div>
                                    <Label>Trust Account</Label>
                                    <Select
                                        value={postInterestAccount?.id || ""}
                                        onValueChange={v => setPostInterestAccount(accounts.find(a => a.id === v) || null)}
                                    >
                                        <SelectTrigger>
                                            <SelectValue placeholder="Select account…"/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {accounts.filter(a => a.interest_rate_pa > 0).map(a => (
                                                <SelectItem key={a.id} value={a.id}>
                                                    {a.account_name} ({(a.interest_rate_pa * 100).toFixed(2)}% p.a.)
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div>
                                    <Label>Period Start</Label>
                                    <Input
                                        type="date"
                                        value={interestPeriodStart}
                                        onChange={e => setInterestPeriodStart(e.target.value)}
                                    />
                                </div>
                                <div>
                                    <Label>Period End</Label>
                                    <Input
                                        type="date"
                                        value={interestPeriodEnd}
                                        onChange={e => setInterestPeriodEnd(e.target.value)}
                                    />
                                </div>
                            </div>

                            <Button
                                variant="outline"
                                onClick={handleFetchForecast}
                                disabled={!postInterestAccount || !interestPeriodStart || !interestPeriodEnd || isForecastLoading}
                            >
                                {isForecastLoading ? <RefreshCw className="h-4 w-4 animate-spin mr-1"/> :
                                    <TrendingUp className="h-4 w-4 mr-1"/>}
                                Calculate Interest
                            </Button>

                            {forecastData && (
                                <div className="rounded-lg border bg-emerald-50 border-emerald-200 p-4 space-y-3">
                                    <div className="font-semibold text-emerald-800 flex items-center gap-1">
                                        <CheckCircle2 className="h-4 w-4"/> Interest Preview
                                        {forecastData.already_posted_this_period && (
                                            <Badge className="ml-2 bg-amber-100 text-amber-700">Already posted this
                                                period</Badge>
                                        )}
                                    </div>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                                        <div>
                                            <div className="text-muted-foreground">Account Balance</div>
                                            <div className="font-semibold">{forecastData.average_balance_display}</div>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">Rate × Days</div>
                                            <div
                                                className="font-semibold">{forecastData.interest_rate_display} × {forecastData.days_in_period} days
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">Total Interest</div>
                                            <div
                                                className="font-semibold text-emerald-700 text-base">{forecastData.forecast_interest_display}</div>
                                        </div>
                                        <div>
                                            <div className="text-muted-foreground">Admin / Capital Works Split</div>
                                            <div
                                                className="font-semibold">{forecastData.admin_split_display} / {forecastData.sinking_split_display}</div>
                                        </div>
                                    </div>

                                    <div>
                                        <Label className="text-xs">Override Amount (optional)</Label>
                                        <Input
                                            type="number"
                                            step="0.01"
                                            placeholder="Leave blank to use calculated amount"
                                            value={interestAmountOverride}
                                            onChange={e => setInterestAmountOverride(e.target.value)}
                                            className="mt-1 max-w-xs"
                                        />
                                        <p className="text-xs text-muted-foreground mt-1">
                                            Enter the actual amount from your bank statement if it differs from the
                                            estimate.
                                        </p>
                                    </div>

                                    <Button
                                        onClick={handlePostInterest}
                                        disabled={isPosting || forecastData.already_posted_this_period}
                                        className="bg-emerald-600 hover:bg-emerald-700"
                                    >
                                        {isPosting ? <RefreshCw className="h-4 w-4 animate-spin mr-1"/> :
                                            <TrendingUp className="h-4 w-4 mr-1"/>}
                                        Post Interest Income
                                    </Button>
                                </div>
                            )}

                            {accounts.filter(a => a.interest_rate_pa === 0 || !a.interest_rate_pa).length > 0 && (
                                <div
                                    className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
                                    <AlertCircle className="h-4 w-4 mt-0.5 shrink-0"/>
                                    <span>
                    {accounts.filter(a => !a.interest_rate_pa || a.interest_rate_pa === 0).length} account(s) have no interest rate configured.
                    Edit the account settings to set the annual interest rate paid by your bank.
                  </span>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Tab 3: Advance Payments ────────────────────────────────────── */}
                <TabsContent value="advance-payments" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Advance Levy Payments</CardTitle>
                            <CardDescription>
                                Owners who paid their levy before the due date. The Owners Corporation earns bank
                                interest
                                on these advance amounts from the payment date until the due date.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            {advancePayments.length === 0 ? (
                                <p className="text-muted-foreground text-sm text-center py-8">
                                    No advance levy payments recorded.
                                </p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Unit</TableHead>
                                            <TableHead>Quarter</TableHead>
                                            <TableHead>Due Date</TableHead>
                                            <TableHead>Paid Date</TableHead>
                                            <TableHead>Days Early</TableHead>
                                            <TableHead>Amount Paid</TableHead>
                                            <TableHead>Interest Earned</TableHead>
                                            <TableHead>Status</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {advancePayments.map(p => (
                                            <TableRow key={p.levy_schedule_id}>
                                                <TableCell className="font-medium">Unit {p.unit_number}</TableCell>
                                                <TableCell>{p.quarter}</TableCell>
                                                <TableCell>{p.due_date}</TableCell>
                                                <TableCell>{p.paid_date}</TableCell>
                                                <TableCell>
                                                    <Badge variant="outline" className="text-blue-700 border-blue-300">
                                                        {p.advance_days}d early
                                                    </Badge>
                                                </TableCell>
                                                <TableCell>{p.total_paid_display}</TableCell>
                                                <TableCell className="text-emerald-700 font-medium">
                                                    {p.interest_earned_cents > 0 ? p.interest_earned_display : "—"}
                                                </TableCell>
                                                <TableCell>
                                                    {p.interest_posted ? (
                                                        <Badge className="bg-emerald-100 text-emerald-700">
                                                            <CheckCircle2 className="h-3 w-3 mr-1"/> Posted
                                                        </Badge>
                                                    ) : (
                                                        <Badge className="bg-amber-100 text-amber-700">
                                                            <Clock className="h-3 w-3 mr-1"/> Pending
                                                        </Badge>
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}

                            {unpostedAdvanceInterest > 0 && (
                                <div
                                    className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 flex items-center justify-between">
                                    <div className="text-sm text-amber-800">
                                        <span className="font-semibold">{formatMoneyFromCents(unpostedAdvanceInterest)}</span> in
                                        advance payment interest not yet posted to funds.
                                        The cron job will auto-post this on the 1st of next month, or you can manually
                                        trigger it from the Post Interest tab.
                                    </div>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Tab 4: Interest History ────────────────────────────────────── */}
                <TabsContent value="history" className="space-y-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Interest Income History</CardTitle>
                            <CardDescription>All interest postings (bank interest credited to funds)</CardDescription>
                        </CardHeader>
                        <CardContent>
                            {interestHistory.length === 0 ? (
                                <p className="text-muted-foreground text-sm text-center py-8">
                                    No interest postings yet.
                                </p>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Account</TableHead>
                                            <TableHead>Period</TableHead>
                                            <TableHead>Total Interest</TableHead>
                                            <TableHead>Admin Fund</TableHead>
                                            <TableHead>Capital Works</TableHead>
                                            <TableHead>Posted</TableHead>
                                            <TableHead>Source</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {interestHistory.map(h => (
                                            <TableRow key={h.id}>
                                                <TableCell className="font-medium">{h.account_name}</TableCell>
                                                <TableCell
                                                    className="text-sm">{h.period_start} → {h.period_end}</TableCell>
                                                <TableCell
                                                    className="text-emerald-700 font-medium">{h.total_interest_display}</TableCell>
                                                <TableCell>{h.admin_interest_display}</TableCell>
                                                <TableCell>{h.sinking_interest_display}</TableCell>
                                                <TableCell className="text-xs text-muted-foreground">
                                                    {new Date(h.posted_at).toLocaleDateString("en-AU")}
                                                </TableCell>
                                                <TableCell>
                                                    {h.is_auto_posted ? (
                                                        <Badge variant="outline" className="text-slate-600">Auto</Badge>
                                                    ) : (
                                                        <Badge variant="outline"
                                                               className="text-blue-700">Manual</Badge>
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* ── Edit Account Dialog ─────────────────────────────────────────────── */}
            <Dialog open={!!editAccount} onOpenChange={v => !v && setEditAccount(null)}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Edit Account Settings</DialogTitle>
                        <DialogDescription>
                            Update bank details and interest configuration for {editAccount?.account_name}
                        </DialogDescription>
                    </DialogHeader>
                    {editAccount && (
                        <div className="space-y-4">
                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label>Bank Name</Label>
                                    <Input
                                        value={editForm.bank_name || ""}
                                        onChange={e => setEditForm(f => ({...f, bank_name: e.target.value}))}
                                        placeholder="e.g. NAB, Macquarie Bank"
                                    />
                                </div>
                                <div>
                                    <Label>Account Name</Label>
                                    <Input
                                        value={editForm.account_name || ""}
                                        onChange={e => setEditForm(f => ({...f, account_name: e.target.value}))}
                                    />
                                </div>
                                <div>
                                    <Label>BSB</Label>
                                    <Input
                                        value={editForm.bsb || ""}
                                        onChange={e => handleBsbChange(e.target.value)}
                                        placeholder="182-266"
                                    />
                                    {bsbLookupState === "loading" && (
                                        <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                                            <RefreshCw className="h-3 w-3 animate-spin"/> Looking up BSB…
                                        </p>
                                    )}
                                    {bsbLookupState === "found" && (
                                        <p className="text-xs text-emerald-700 mt-1">✓ {bsbBankHint}</p>
                                    )}
                                    {bsbLookupState === "not_found" && (
                                        <p className="text-xs text-amber-600 mt-1">BSB not found — please verify.</p>
                                    )}
                                </div>
                                <div>
                                    <Label>Account Number (masked)</Label>
                                    <Input
                                        value={editForm.account_number_masked || ""}
                                        onChange={e => setEditForm(f => ({
                                            ...f,
                                            account_number_masked: e.target.value
                                        }))}
                                        placeholder="****4567"
                                    />
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-3">
                                <div>
                                    <Label>Account Category</Label>
                                    <Select
                                        value={editForm.account_category || "transaction"}
                                        onValueChange={v => setEditForm(f => ({...f, account_category: v as any}))}
                                    >
                                        <SelectTrigger>
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="transaction">Transaction Account</SelectItem>
                                            <SelectItem value="savings">High-Interest Savings</SelectItem>
                                            <SelectItem value="term_deposit">Term Deposit</SelectItem>
                                            <SelectItem value="offset">Offset Account</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div>
                                    <Label>Annual Interest Rate (%)</Label>
                                    <Input
                                        type="number"
                                        step="0.01"
                                        min="0"
                                        max="100"
                                        value={editForm.interest_rate_pa != null ? (editForm.interest_rate_pa * 100).toFixed(2) : ""}
                                        onChange={e => setEditForm(f => ({
                                            ...f,
                                            interest_rate_pa: parseFloat(e.target.value) / 100 || 0
                                        }))}
                                        placeholder="e.g. 4.50"
                                    />
                                    <p className="text-xs text-muted-foreground mt-1">Enter as percentage, e.g. 4.50 for
                                        4.50% p.a.</p>
                                </div>
                            </div>

                            {editForm.account_category === "term_deposit" && (
                                <div>
                                    <Label>Term Deposit Maturity Date</Label>
                                    <Input
                                        type="date"
                                        value={editForm.term_deposit_maturity_date || ""}
                                        onChange={e => setEditForm(f => ({
                                            ...f,
                                            term_deposit_maturity_date: e.target.value
                                        }))}
                                    />
                                </div>
                            )}

                            <div>
                                <Label>DEFT Biller Code</Label>
                                <Input
                                    value={editForm.deft_biller_code || ""}
                                    onChange={e => setEditForm(f => ({...f, deft_biller_code: e.target.value}))}
                                    placeholder="Optional"
                                />
                            </div>

                            <div>
                                <Label>Notes</Label>
                                <Input
                                    value={editForm.notes || ""}
                                    onChange={e => setEditForm(f => ({...f, notes: e.target.value}))}
                                    placeholder="Optional internal notes"
                                />
                            </div>
                        </div>
                    )}
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setEditAccount(null)}>Cancel</Button>
                        <Button onClick={handleSaveAccount} disabled={isSaving}>
                            {isSaving ? <RefreshCw className="h-4 w-4 animate-spin mr-1"/> : null}
                            Save Changes
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Create Account Dialog ───────────────────────────────────────────── */}
            <Dialog open={showCreateDialog} onOpenChange={v => { if (!v) { setShowCreateDialog(false); resetCreateForm(); } }}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Add Trust Account</DialogTitle>
                        <DialogDescription>
                            Register a new bank account used to hold Owners Corporation funds.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                        <div>
                            <Label>Fund</Label>
                            <Select
                                value={createForm.account_type}
                                onValueChange={v => setCreateForm(f => ({...f, account_type: v}))}
                            >
                                <SelectTrigger>
                                    <SelectValue/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="admin_fund">Admin Fund</SelectItem>
                                    <SelectItem value="sinking_fund">Sinking Fund</SelectItem>
                                    <SelectItem value="special_purpose">Special Purpose</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label htmlFor="create-bank-name">Bank Name</Label>
                                <Input
                                    id="create-bank-name"
                                    value={createForm.bank_name}
                                    onChange={e => setCreateForm(f => ({...f, bank_name: e.target.value}))}
                                    placeholder="e.g. NAB, Macquarie Bank"
                                />
                            </div>
                            <div>
                                <Label htmlFor="create-account-name">Account Name</Label>
                                <Input
                                    id="create-account-name"
                                    value={createForm.account_name}
                                    onChange={e => setCreateForm(f => ({...f, account_name: e.target.value}))}
                                    placeholder="e.g. OC Trust Account"
                                />
                            </div>
                            <div>
                                <Label htmlFor="create-bsb">BSB</Label>
                                <Input
                                    id="create-bsb"
                                    value={createForm.bsb}
                                    onChange={e => setCreateForm(f => ({...f, bsb: e.target.value}))}
                                    placeholder="182-266"
                                />
                            </div>
                            <div>
                                <Label htmlFor="create-account-number">Account Number</Label>
                                <Input
                                    id="create-account-number"
                                    value={createForm.account_number}
                                    onChange={e => setCreateForm(f => ({...f, account_number: e.target.value}))}
                                    placeholder="12345678"
                                />
                                <p className="text-xs text-muted-foreground mt-1">Only the last 4 digits are stored.</p>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <Label>Account Category</Label>
                                <Select
                                    value={createForm.account_category}
                                    onValueChange={v => setCreateForm(f => ({...f, account_category: v}))}
                                >
                                    <SelectTrigger>
                                        <SelectValue/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="transaction">Transaction Account</SelectItem>
                                        <SelectItem value="savings">High-Interest Savings</SelectItem>
                                        <SelectItem value="term_deposit">Term Deposit</SelectItem>
                                        <SelectItem value="offset">Offset Account</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div>
                                <Label htmlFor="create-opening-balance">Opening Balance (AUD)</Label>
                                <Input
                                    id="create-opening-balance"
                                    type="number"
                                    step="0.01"
                                    value={createForm.opening_balance}
                                    onChange={e => setCreateForm(f => ({...f, opening_balance: e.target.value}))}
                                    placeholder="0.00"
                                />
                            </div>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => { setShowCreateDialog(false); resetCreateForm(); }}>Cancel</Button>
                        <Button onClick={handleCreateAccount} disabled={isCreating} data-testid="create-trust-account-submit">
                            {isCreating ? <RefreshCw className="h-4 w-4 animate-spin mr-1"/> : null}
                            Create Account
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
