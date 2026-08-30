// @ts-nocheck
"use client";
import React from "react";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from "@/components/ui/table";
import {Button} from "@/components/ui/button";
import {Download} from "lucide-react";
import axios from "axios";

const API = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/components/levy/CrossSubsidyTable.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt = (v: number) =>
    `$${Math.abs(v).toLocaleString("en-AU", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}`;

interface GroupRow {
    group: string;
    unit_count: number;
    current_total: number;
    fair_total: number;
    proposed_total: number;
    current_share_pct: number;
    fair_share_pct: number;
    net_subsidy: number;
    net_subsidy_per_unit: number;
    role: "Contributor" | "Recipient" | "Neutral";
}

interface FacilityRow {
    facility_name: string;
    annual_cost: number;
    pct_of_total: number;
}

interface CrossSubsidyData {
    group_rows: GroupRow[];
    facility_rows: FacilityRow[];
    total_current: number;
    total_fair: number;
}

interface Props {
    crossSubsidy: CrossSubsidyData | null | undefined;
}

const ROLE_STYLE: Record<string, string> = {
    Contributor: "bg-rose-100 text-rose-800",
    Recipient: "bg-emerald-100 text-emerald-800",
    Neutral: "bg-slate-100 text-slate-600",
};
/**
 * @generated FunctionHeader
 * Function: CrossSubsidyTable
 * Path: frontend/src/components/levy/CrossSubsidyTable.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const CrossSubsidyTable: React.FC<Props> = ({crossSubsidy}) => {
    if (!crossSubsidy || !crossSubsidy.group_rows?.length) {
        return (
            <p className="text-sm text-muted-foreground py-8 text-center">
                No cross-subsidy data — click Regenerate Model.
            </p>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: handleDownloadCsv
     * Path: frontend/src/components/levy/CrossSubsidyTable.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadCsv = async () => {
        try {
            const token = localStorage.getItem("token");
            const resp = await axios.get(
                `${API}/api/intelligence/levy-fairness/cross-subsidy-report.csv`,
                {
                    headers: {Authorization: `Bearer ${token}`},
                    responseType: "blob",
                }
            );
            const url = URL.createObjectURL(new Blob([resp.data]));
            const a = document.createElement("a");
            a.href = url;
            a.download = "EastGate_CrossSubsidy.csv";
            a.click();
            URL.revokeObjectURL(url);
        } catch {
            alert("Failed to download CSV");
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-start gap-4 flex-wrap">
                <p className="text-sm text-muted-foreground flex-1">
                    Shows how each unit group currently cross-subsidises others, and what the fair allocation
                    would be. <strong> Contributor</strong> = currently overpaying vs benefit received.
                    <strong> Recipient</strong> = currently underpaying.
                </p>
                <Button size="sm" variant="outline" onClick={handleDownloadCsv} className="shrink-0">
                    <Download className="w-3 h-3 mr-1"/> Export CSV
                </Button>
            </div>

            {/* Group summary */}
            <div>
                <p className="font-semibold text-sm mb-2">Cross-Subsidy by Unit Group</p>
                <div className="rounded-md border overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Group</TableHead>
                                <TableHead className="text-right">Units</TableHead>
                                <TableHead className="text-right">Current Total</TableHead>
                                <TableHead className="text-right">Fair Total</TableHead>
                                <TableHead className="text-right">Net Subsidy</TableHead>
                                <TableHead className="text-right">Per Unit</TableHead>
                                <TableHead>Role</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {crossSubsidy.group_rows.map((row) => (
                                <TableRow key={row.group}>
                                    <TableCell className="font-medium">{row.group}</TableCell>
                                    <TableCell className="text-right">{row.unit_count}</TableCell>
                                    <TableCell className="text-right">{fmt(row.current_total)}</TableCell>
                                    <TableCell className="text-right">{fmt(row.fair_total)}</TableCell>
                                    <TableCell
                                        className={`text-right font-medium ${
                                            row.net_subsidy > 0
                                                ? "text-rose-600"
                                                : row.net_subsidy < 0
                                                    ? "text-emerald-600"
                                                    : ""
                                        }`}
                                    >
                                        {row.net_subsidy > 0 ? "+" : ""}
                                        {fmt(row.net_subsidy)}
                                    </TableCell>
                                    <TableCell
                                        className={`text-right text-xs ${
                                            row.net_subsidy > 0
                                                ? "text-rose-600"
                                                : row.net_subsidy < 0
                                                    ? "text-emerald-600"
                                                    : ""
                                        }`}
                                    >
                                        {row.net_subsidy_per_unit > 0 ? "+" : ""}
                                        {fmt(row.net_subsidy_per_unit)}
                                    </TableCell>
                                    <TableCell>
                    <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                            ROLE_STYLE[row.role] || "bg-slate-100 text-slate-600"
                        }`}
                    >
                      {row.role}
                    </span>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </div>

            {/* Facility breakdown */}
            {crossSubsidy.facility_rows?.length > 0 && (
                <div>
                    <p className="font-semibold text-sm mb-2">Facility Cost Centres (Sorted by Cost)</p>
                    <div className="rounded-md border overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Facility</TableHead>
                                    <TableHead className="text-right">Annual Cost</TableHead>
                                    <TableHead className="text-right">% of Total</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {crossSubsidy.facility_rows.map((row) => (
                                    <TableRow key={row.facility_name}>
                                        <TableCell className="text-sm">{row.facility_name}</TableCell>
                                        <TableCell className="text-right">{fmt(row.annual_cost)}</TableCell>
                                        <TableCell className="text-right">{row.pct_of_total}%</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                </div>
            )}
        </div>
    );
};
