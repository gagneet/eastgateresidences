"use client";

import {useState} from "react";
import {Badge} from "@/components/ui/badge";
import {Button} from "@/components/ui/button";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from "@/components/ui/table";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from "@/components/ui/select";

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

interface LevyScheduleTableProps {
    schedules: LevySchedule[];
    isLoading?: boolean;
    onRunArrearsCheck?: (dryRun: boolean) => void;
    currentQuarter?: string;
}
/**
 * @generated FunctionHeader
 * Function: getStatusVariant
 * Path: frontend/src/components/trust/LevyScheduleTable.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getStatusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
    switch (status) {
        case "paid":
            return "default";
        case "pending":
            return "outline";
        case "overdue":
            return "destructive";
        case "partial":
            return "secondary";
        case "legal":
            return "destructive";
        default:
            return "outline";
    }
}

const STATUS_LABELS: Record<string, string> = {
    paid: "Paid",
    pending: "Pending",
    overdue: "Overdue",
    partial: "Partial",
    waived: "Waived",
    payment_plan: "Payment Plan",
    legal: "Legal",
};

const PAGE_SIZE = 25;
/**
 * @generated FunctionHeader
 * Function: LevyScheduleTable
 * Path: frontend/src/components/trust/LevyScheduleTable.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function LevyScheduleTable({
                                      schedules,
                                      isLoading,
                                      onRunArrearsCheck,
                                      currentQuarter,
                                  }: LevyScheduleTableProps) {
    const [page, setPage] = useState(1);
    const [statusFilter, setStatusFilter] = useState<string>("all");

    const filtered = statusFilter === "all"
        ? schedules
        : schedules.filter(s => s.status === statusFilter);

    const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
    const paginated = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    {currentQuarter && (
                        <span className="text-sm font-medium">{currentQuarter}</span>
                    )}
                    <Select value={statusFilter} onValueChange={v => {
                        setStatusFilter(v);
                        setPage(1);
                    }}>
                        <SelectTrigger className="w-36">
                            <SelectValue placeholder="Filter status"/>
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Status</SelectItem>
                            <SelectItem value="pending">Pending</SelectItem>
                            <SelectItem value="paid">Paid</SelectItem>
                            <SelectItem value="overdue">Overdue</SelectItem>
                            <SelectItem value="partial">Partial</SelectItem>
                            <SelectItem value="legal">Legal</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                {onRunArrearsCheck && (
                    <div className="flex gap-2">
                        <Button variant="outline" size="sm" onClick={() => onRunArrearsCheck(true)}>
                            Preview Arrears
                        </Button>
                        <Button variant="default" size="sm" onClick={() => onRunArrearsCheck(false)}>
                            Run Arrears Check
                        </Button>
                    </div>
                )}
            </div>

            {isLoading ? (
                <div className="text-center py-8 text-muted-foreground">Loading levy schedules...</div>
            ) : (
                <>
                    <div className="rounded-md border overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Unit</TableHead>
                                    <TableHead>Quarter</TableHead>
                                    <TableHead>Due Date</TableHead>
                                    <TableHead className="text-right">Admin</TableHead>
                                    <TableHead className="text-right">Sinking</TableHead>
                                    <TableHead className="text-right">Total</TableHead>
                                    <TableHead className="text-right">Paid</TableHead>
                                    <TableHead className="text-right">Outstanding</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>DEFT CRN</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {paginated.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={10} className="text-center text-muted-foreground py-6">
                                            No levy schedules found.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    paginated.map(s => (
                                        <TableRow key={s.id}>
                                            <TableCell className="font-medium">{s.unit_number}</TableCell>
                                            <TableCell>{s.quarter}</TableCell>
                                            <TableCell>{s.due_date}</TableCell>
                                            <TableCell className="text-right">{s.admin_fund_display}</TableCell>
                                            <TableCell className="text-right">{s.sinking_fund_display}</TableCell>
                                            <TableCell className="text-right font-medium">{s.total_display}</TableCell>
                                            <TableCell
                                                className="text-right text-green-600">{s.paid_display}</TableCell>
                                            <TableCell
                                                className="text-right text-orange-600">{s.outstanding_display}</TableCell>
                                            <TableCell>
                                                <Badge variant={getStatusVariant(s.status)}>
                                                    {STATUS_LABELS[s.status] ?? s.status}
                                                </Badge>
                                            </TableCell>
                                            <TableCell
                                                className="text-xs font-mono text-muted-foreground">{s.deft_crn}</TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    </div>

                    {totalPages > 1 && (
                        <div className="flex items-center justify-between">
                            <p className="text-sm text-muted-foreground">
                                {filtered.length} schedules · Page {page} of {totalPages}
                            </p>
                            <div className="flex gap-2">
                                <Button variant="outline" size="sm" disabled={page === 1}
                                        onClick={() => setPage(p => p - 1)}>
                                    Previous
                                </Button>
                                <Button variant="outline" size="sm" disabled={page === totalPages}
                                        onClick={() => setPage(p => p + 1)}>
                                    Next
                                </Button>
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

export default LevyScheduleTable;
