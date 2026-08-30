"use client";

import {Badge} from "@/components/ui/badge";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {AlertCircle, Building2, CheckCircle, XCircle} from "lucide-react";
import Link from "next/link";

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

interface TrustAccountCardProps {
    account: TrustAccount;
    buildingName?: string;
}
/**
 * @generated FunctionHeader
 * Function: getAccountTypeLabel
 * Path: frontend/src/components/trust/TrustAccountCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getAccountTypeLabel(type: TrustAccount["account_type"]): string {
    switch (type) {
        case "admin_fund":
            return "Admin Fund";
        case "sinking_fund":
            return "Sinking Fund";
        case "special_purpose":
            return "Special Purpose";
        default:
            return type;
    }
}
/**
 * @generated FunctionHeader
 * Function: getReconciliationStatus
 * Path: frontend/src/components/trust/TrustAccountCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getReconciliationStatus(lastReconciledAt: string | null): {
    color: "green" | "amber" | "red";
    label: string;
    icon: React.ReactNode;
} {
    if (!lastReconciledAt) {
        return {color: "red", label: "Not reconciled", icon: <XCircle className="h-4 w-4 text-red-500"/>};
    }
    const days = Math.floor((Date.now() - new Date(lastReconciledAt).getTime()) / (1000 * 60 * 60 * 24));
    if (days <= 30) return {
        color: "green",
        label: `${days}d ago`,
        icon: <CheckCircle className="h-4 w-4 text-green-500"/>
    };
    if (days <= 60) return {
        color: "amber",
        label: `${days}d ago`,
        icon: <AlertCircle className="h-4 w-4 text-yellow-500"/>
    };
    return {color: "red", label: `${days}d ago`, icon: <XCircle className="h-4 w-4 text-red-500"/>};
}
/**
 * @generated FunctionHeader
 * Function: TrustAccountCard
 * Path: frontend/src/components/trust/TrustAccountCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function TrustAccountCard({account, buildingName}: TrustAccountCardProps) {
    const recon = getReconciliationStatus(account.last_reconciled_at);

    return (
        <Card className="relative overflow-hidden">
            <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2">
                    <Building2 className="h-4 w-4 text-muted-foreground"/>
                    {buildingName && <span className="text-muted-foreground">{buildingName} —</span>}
                    <Badge variant={account.account_type === "admin_fund" ? "default" : "secondary"}>
                        {getAccountTypeLabel(account.account_type)}
                    </Badge>
                </CardTitle>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    {recon.icon}
                    <span>{recon.label === "Not reconciled" ? "⚠ Not reconciled" : recon.label}</span>
                </div>
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold">{account.current_balance_display}</div>
                <p className="text-xs text-muted-foreground mt-1">
                    {account.account_name} · {account.bank_name}
                </p>
                <p className="text-xs text-muted-foreground">
                    BSB {account.bsb} · {account.account_number_masked}
                </p>
                {account.last_reconciled_at && (
                    <p className="text-xs text-muted-foreground mt-1">
                        Last reconciled: {account.last_reconciled_balance_display}
                    </p>
                )}
                {account.deft_biller_code && (
                    <p className="text-xs text-muted-foreground">
                        DEFT: {account.deft_biller_code}
                    </p>
                )}
                <div className="mt-3">
                    <Link
                        href="/financials/overview"
                        className="text-xs text-blue-600 hover:underline"
                    >
                        View Reconciliation →
                    </Link>
                </div>
            </CardContent>
        </Card>
    );
}

export default TrustAccountCard;
