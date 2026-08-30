"use client";

import {useCallback, useEffect, useState} from "react";
import {useSession} from "next-auth/react";
import axios from "axios";
import {Tabs, TabsContent, TabsList, TabsTrigger} from "@/components/ui/tabs";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {Badge} from "@/components/ui/badge";
import {TrustAccountCard} from "@/components/trust/TrustAccountCard";
import {LevyScheduleTable} from "@/components/trust/LevyScheduleTable";
import {TrustTransactionLedger} from "@/components/trust/TrustTransactionLedger";
import {AlertTriangle, CheckSquare, DollarSign, TrendingDown} from "lucide-react";
import {toast} from "sonner";

interface TrustAccount {
    id: string;
    account_type: "admin_fund" | "sinking_fund" | "special_purpose";
    bank_name: string;
    account_name: string;
    bsb: string;
    account_number_masked: string;
    current_balance_cents: number;
    current_balance_display: string;
    last_reconciled_at: string | null;
    last_reconciled_balance_cents: number;
    last_reconciled_balance_display: string;
    deft_biller_code?: string | null;
}

interface LevySummary {
    raised: { cents: number; display: string };
    received: { cents: number; display: string };
    outstanding: { cents: number; display: string };
    collection_rate_percent: number;
    overdue_count: number;
    overdue: { cents: number; display: string };
}

interface LevySchedule {
    id: string;
    unit_number: string;
    lot_number: number;
    quarter: string;
    financial_year: string;
    due_date: string;
    admin_fund_display: string;
    sinking_fund_display: string;
    total_display: string;
    total_cents: number;
    deft_crn: string;
    status: string;
    paid_display: string;
    outstanding_display: string;
    outstanding_cents: number;
}

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8003";
/**
 * @generated FunctionHeader
 * Function: TrustAccountingPage
 * Path: frontend/src/pages/dashboard/TrustAccountingPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function TrustAccountingPage() {
    const sessionResult = useSession();
    const session = sessionResult?.data;
    const [accounts, setAccounts] = useState<TrustAccount[]>([]);
    const [levySchedules, setLevySchedules] = useState<LevySchedule[]>([]);
    const [levySummary, setLevySummary] = useState<LevySummary | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("levy-schedule");
    const [selectedAccountId, setSelectedAccountId] = useState<string>("");

    const buildingId = (session as any)?.user?.data?.building_id ?? "";
    const buildingName = (session as any)?.user?.data?.building_name ?? "";

    // Derive current fiscal quarter dynamically (Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec)
    const currentQuarter = (() => {
        const now = new Date();
        const q = Math.floor(now.getMonth() / 3) + 1;
        return `${now.getFullYear()}-Q${q}`;
    })();

    const api = useCallback(
        (path: string, config = {}) => {
            const token = (session as any)?.accessToken;
            return axios.get(`${BACKEND_URL}/api/trust/v2${path}`, {
                ...config,
                headers: {Authorization: `Bearer ${token}`},
            });
        },
        [session]
    );

    useEffect(() => {
        if (!session) return;
        let mounted = true;

        Promise.allSettled([
            api("/accounts"),
            api(`/levies/${buildingId}/schedule?quarter=${currentQuarter}&limit=100`),
            api("/financial-summary"),
        ]).then(([accountsRes, leviesRes, summaryRes]) => {
            if (!mounted) return;

            if (accountsRes.status === "fulfilled") {
                const accs = accountsRes.value.data.data ?? [];
                setAccounts(accs);
                if (accs.length > 0) setSelectedAccountId(accs[0].id);
            }
            if (leviesRes.status === "fulfilled") {
                setLevySchedules(leviesRes.value.data.data ?? []);
            }
            if (summaryRes.status === "fulfilled") {
                setLevySummary(summaryRes.value.data.data?.levies ?? null);
            }
            setIsLoading(false);
        });

        return () => {
            mounted = false;
        };
    }, [session, buildingId, api]);
    /**
     * @generated FunctionHeader
     * Function: handleArrearsCheck
     * Path: frontend/src/pages/dashboard/TrustAccountingPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleArrearsCheck = async (dryRun: boolean) => {
        const token = (session as any)?.accessToken;
        try {
            const res = await axios.post(
                `${BACKEND_URL}/api/trust/v2/arrears/escalate`,
                {dry_run: dryRun},
                {headers: {Authorization: `Bearer ${token}`}}
            );
            const report = res.data.data;
            const msg = dryRun
                ? `[DRY RUN] Would send ${report.reminders_sent} reminders, ${report.formal_notices_sent} formal notices.`
                : `Escalation complete: ${report.reminders_sent} reminders, ${report.formal_notices_sent} formal notices, ${report.legal_flagged} legal flags.`;
            toast.success(msg);
        } catch {
            toast.error("Failed to run arrears check.");
        }
    };

    const adminAccount = accounts.find(a => a.account_type === "admin_fund");
    const sinkingAccount = accounts.find(a => a.account_type === "sinking_fund");

    return (
        <div className="space-y-6 p-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight">Trust Accounting (Cash & Reconciliation)</h1>
                    <p className="text-muted-foreground">
                        {buildingName || "Building"} · Phase 1 Foundation
                    </p>
                </div>
            </div>

            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                These cards show trust-managed cash accounts and trust levy schedules. They do not replace the legacy
                levy balances shown on unit finance pages or the Levy KPI dashboard.
            </div>

            {/* Trust Account Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {isLoading ? (
                    <>
                        <Card className="h-40 animate-pulse bg-muted"/>
                        <Card className="h-40 animate-pulse bg-muted"/>
                    </>
                ) : (
                    <>
                        {adminAccount && (
                            <TrustAccountCard account={adminAccount} buildingName={buildingName}/>
                        )}
                        {sinkingAccount && (
                            <TrustAccountCard account={sinkingAccount} buildingName={buildingName}/>
                        )}
                        {accounts.length === 0 && (
                            <Card className="col-span-2">
                                <CardContent className="py-8 text-center text-muted-foreground">
                                    No trust accounts configured. Please complete trust setup.
                                </CardContent>
                            </Card>
                        )}
                    </>
                )}
            </div>

            {/* Summary Stats */}
            {levySummary && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                                <DollarSign className="h-3 w-3"/> Levies Raised
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-xl font-bold">{levySummary.raised.display}</div>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                                <CheckSquare className="h-3 w-3"/> Received
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-xl font-bold text-green-600">{levySummary.received.display}</div>
                            <p className="text-xs text-muted-foreground">{levySummary.collection_rate_percent}%
                                collection</p>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                                <TrendingDown className="h-3 w-3"/> Outstanding
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-xl font-bold text-orange-600">{levySummary.outstanding.display}</div>
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader className="pb-2">
                            <CardTitle className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                                <AlertTriangle className="h-3 w-3"/> Overdue Units
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-xl font-bold text-red-600">{levySummary.overdue_count}</div>
                            <p className="text-xs text-muted-foreground">{levySummary.overdue.display}</p>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="levy-schedule">Levy Schedule</TabsTrigger>
                    <TabsTrigger value="transactions">Transaction Ledger</TabsTrigger>
                    <TabsTrigger value="arrears">Arrears</TabsTrigger>
                </TabsList>

                <TabsContent value="levy-schedule" className="mt-4">
                    <LevyScheduleTable
                        schedules={levySchedules}
                        isLoading={isLoading}
                        onRunArrearsCheck={handleArrearsCheck}
                        currentQuarter={currentQuarter}
                    />
                </TabsContent>

                <TabsContent value="transactions" className="mt-4">
                    {selectedAccountId ? (
                        <div className="space-y-4">
                            {accounts.length > 1 && (
                                <div className="flex gap-2">
                                    {accounts.map(acc => (
                                        <button
                                            key={acc.id}
                                            onClick={() => setSelectedAccountId(acc.id)}
                                            className={`px-3 py-1 text-sm rounded-md border transition-colors ${
                                                selectedAccountId === acc.id
                                                    ? "bg-primary text-primary-foreground"
                                                    : "bg-background hover:bg-muted"
                                            }`}
                                        >
                                            {acc.account_type === "admin_fund" ? "Admin Fund" : "Sinking Fund"}
                                        </button>
                                    ))}
                                </div>
                            )}
                            <TrustTransactionLedger
                                accountId={selectedAccountId}
                                accountName={accounts.find(a => a.id === selectedAccountId)?.account_name}
                            />
                        </div>
                    ) : (
                        <div className="text-center py-8 text-muted-foreground">
                            No trust accounts available.
                        </div>
                    )}
                </TabsContent>

                <TabsContent value="arrears" className="mt-4">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Arrears Management</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <p className="text-sm text-muted-foreground mb-4">
                                The arrears engine automatically escalates overdue levies through stages:
                                Reminder (Day 14) → Formal Notice (Day 21) → Interest Charge (Day 30) → Legal (Day 60).
                                Thresholds are configured per-building.
                            </p>
                            {levySummary && (
                                <div className="space-y-2">
                                    <div className="flex justify-between text-sm">
                                        <span>Overdue units:</span>
                                        <Badge variant="destructive">{levySummary.overdue_count}</Badge>
                                    </div>
                                    <div className="flex justify-between text-sm">
                                        <span>Total outstanding:</span>
                                        <span
                                            className="font-medium text-orange-600">{levySummary.outstanding.display}</span>
                                    </div>
                                    <div className="flex justify-between text-sm">
                                        <span>Total overdue:</span>
                                        <span className="font-medium text-red-600">{levySummary.overdue.display}</span>
                                    </div>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
