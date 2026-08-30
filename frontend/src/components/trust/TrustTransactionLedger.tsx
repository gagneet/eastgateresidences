"use client";

import {useCallback, useEffect, useState} from "react";
import {Badge} from "@/components/ui/badge";
import {Button} from "@/components/ui/button";
import {Input} from "@/components/ui/input";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from "@/components/ui/table";
import axios from "axios";
import {useSession} from "next-auth/react";

interface TrustTransaction {
    id: string;
    type: string;
    amount_cents: number;
    amount_display: string;
    description: string;
    reference?: string | null;
    payee_payer?: string | null;
    payment_method?: string | null;
    is_reconciled: boolean;
    is_reversed: boolean;
    running_balance_cents: number;
    running_balance_display: string;
    created_at: string;
}

interface TrustTransactionLedgerProps {
    accountId: string;
    accountName?: string;
}

const TX_TYPE_COLORS: Record<string, string> = {
    receipt: "text-green-600",
    disbursement: "text-red-600",
    bank_charge: "text-orange-600",
    interest: "text-yellow-600",
    reversal: "text-muted-foreground line-through",
    adjustment: "text-blue-600",
};

const TX_TYPE_LABELS: Record<string, string> = {
    receipt: "Receipt",
    disbursement: "Disbursement",
    bank_charge: "Bank Charge",
    interest: "Interest",
    reversal: "Reversal",
    adjustment: "Adjustment",
};
/**
 * @generated FunctionHeader
 * Function: TrustTransactionLedger
 * Path: frontend/src/components/trust/TrustTransactionLedger.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function TrustTransactionLedger({accountId, accountName}: TrustTransactionLedgerProps) {
    const {data: session} = useSession();
    const [transactions, setTransactions] = useState<TrustTransaction[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const [fromDate, setFromDate] = useState("");
    const [toDate, setToDate] = useState("");
    const PAGE_SIZE = 25;

    const fetchTransactions = useCallback(async () => {
        if (!accountId) return;
        setIsLoading(true);
        try {
            const token = (session as any)?.accessToken;
            const params = new URLSearchParams({
                trust_account_id: accountId,
                page: String(page),
                limit: String(PAGE_SIZE),
            });
            if (fromDate) params.append("from", fromDate);
            if (toDate) params.append("to", toDate);

            const res = await axios.get(
                `${process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8003"}/api/trust/v2/transactions?${params}`,
                {headers: {Authorization: `Bearer ${token}`}}
            );
            setTransactions(res.data.data ?? []);
            setTotal(res.data.pagination?.total ?? 0);
        } catch (err) {
            console.error("Failed to load transactions", err);
        } finally {
            setIsLoading(false);
        }
    }, [accountId, session, page, fromDate, toDate]);

    useEffect(() => {
        fetchTransactions();
    }, [fetchTransactions]);
    /**
     * @generated FunctionHeader
     * Function: exportCsv
     * Path: frontend/src/components/trust/TrustTransactionLedger.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const exportCsv = () => {
        if (!transactions.length) return;
        const headers = ["Date", "Type", "Description", "Reference", "Amount", "Running Balance", "Reconciled"];
        const rows = transactions.map(t => [
            t.created_at.slice(0, 10),
            t.type,
            `"${t.description.replace(/"/g, '""')}"`,
            t.reference ?? "",
            t.amount_display,
            t.running_balance_display,
            t.is_reconciled ? "Yes" : "No",
        ]);
        const csv = [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `trust-ledger-${accountId}-${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const totalPages = Math.ceil(total / PAGE_SIZE);

    return (
        <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
                {accountName && <h3 className="text-sm font-medium">{accountName}</h3>}
                <div className="flex flex-wrap items-center gap-2">
                    <Input
                        type="date"
                        value={fromDate}
                        onChange={e => {
                            setFromDate(e.target.value);
                            setPage(1);
                        }}
                        className="w-36 h-8 text-sm"
                        placeholder="From"
                    />
                    <Input
                        type="date"
                        value={toDate}
                        onChange={e => {
                            setToDate(e.target.value);
                            setPage(1);
                        }}
                        className="w-36 h-8 text-sm"
                        placeholder="To"
                    />
                    <Button variant="outline" size="sm" onClick={exportCsv}>
                        Export CSV
                    </Button>
                </div>
            </div>

            {isLoading ? (
                <div className="text-center py-8 text-muted-foreground">Loading transactions...</div>
            ) : (
                <>
                    <div className="rounded-md border overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Date</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Description</TableHead>
                                    <TableHead>Reference</TableHead>
                                    <TableHead className="text-right">Amount</TableHead>
                                    <TableHead className="text-right">Balance</TableHead>
                                    <TableHead>Reconciled</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {transactions.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={7} className="text-center text-muted-foreground py-6">
                                            No transactions found.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    transactions.map(t => (
                                        <TableRow key={t.id} className={t.is_reversed ? "opacity-50" : ""}>
                                            <TableCell className="text-xs">{t.created_at.slice(0, 10)}</TableCell>
                                            <TableCell>
                                                <Badge variant="outline"
                                                       className={`text-xs ${TX_TYPE_COLORS[t.type] ?? ""}`}>
                                                    {TX_TYPE_LABELS[t.type] ?? t.type}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-sm max-w-xs truncate">{t.description}</TableCell>
                                            <TableCell
                                                className="text-xs text-muted-foreground">{t.reference ?? "—"}</TableCell>
                                            <TableCell
                                                className={`text-right text-sm font-mono ${TX_TYPE_COLORS[t.type] ?? ""}`}>
                                                {t.amount_display}
                                            </TableCell>
                                            <TableCell className="text-right text-sm font-mono font-medium">
                                                {t.running_balance_display}
                                            </TableCell>
                                            <TableCell>
                                                {t.is_reconciled ? (
                                                    <span className="text-xs text-green-600">✓</span>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">—</span>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    </div>

                    {totalPages > 1 && (
                        <div className="flex items-center justify-between">
                            <p className="text-sm text-muted-foreground">{total} transactions ·
                                Page {page} of {totalPages}</p>
                            <div className="flex gap-2">
                                <Button variant="outline" size="sm" disabled={page === 1}
                                        onClick={() => setPage(p => p - 1)}>Previous</Button>
                                <Button variant="outline" size="sm" disabled={page === totalPages}
                                        onClick={() => setPage(p => p + 1)}>Next</Button>
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

export default TrustTransactionLedger;
