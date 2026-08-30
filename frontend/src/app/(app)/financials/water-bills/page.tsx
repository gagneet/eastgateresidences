// @ts-nocheck
"use client";
import React, {useCallback, useEffect, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {useActiveUnitSync} from "@/hooks/useActiveUnit";
import WaterBillCard from "@/components/dashboard/WaterBillCard";
import {Badge} from "@/components/ui/badge";
import {Button} from "@/components/ui/button";
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from "@/components/ui/select";
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from "@/components/ui/table";
import {Skeleton} from "@/components/ui/skeleton";
import {Card, CardContent, CardDescription, CardHeader, CardTitle,} from "@/components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {Input} from "@/components/ui/input";
import {Label} from "@/components/ui/label";
import {AlertCircle, CalendarDays, CheckCircle, Clock, Droplets, Gauge, Plus, RefreshCw,} from "lucide-react";
import {formatCurrency, formatDate} from "@/lib/utils";
import {toast} from "sonner";

// ---------------------------------------------------------------------------
// Status badge helper
// ---------------------------------------------------------------------------
const statusConfig: Record<
    string,
    { label: string; className: string; Icon: any }
> = {
    paid: {
        label: "Paid",
        className: "bg-emerald-100 text-emerald-700 border-emerald-200",
        Icon: CheckCircle,
    },
    partial: {
        label: "Partial",
        className: "bg-amber-100 text-amber-700 border-amber-200",
        Icon: Clock,
    },
    unpaid: {
        label: "Unpaid",
        className: "bg-red-100 text-red-700 border-red-200",
        Icon: AlertCircle,
    },
};
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/app/(app)/financials/water-bills/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StatusBadge = ({status}: { status: string }) => {
    const cfg = statusConfig[status] ?? statusConfig.unpaid;
    const {Icon} = cfg;
    return (
        <Badge variant="outline" className={`text-xs font-semibold ${cfg.className}`}>
            <Icon size={11} className="mr-1"/>
            {cfg.label}
        </Badge>
    );
};
// ---------------------------------------------------------------------------
// Add bill dialog (for manager row-level action)
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: AddBillDialog
 * Path: frontend/src/app/(app)/financials/water-bills/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AddBillDialog = ({
                           open,
                           onOpenChange,
                           unitNumber,
                           onSuccess,
                           api,
                       }: any) => {
    const [form, setForm] = useState({
        quarter: "",
        billing_period_start: "",
        billing_period_end: "",
        amount: "",
        due_date: "",
        water_usage_kl: "",
    });
    const [submitting, setSubmitting] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: handleChange
     * Path: frontend/src/app/(app)/financials/water-bills/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (field: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
        setForm((prev) => ({...prev, [field]: e.target.value}));

    const handleSubmit = useCallback(async () => {
        if (!form.quarter || !form.amount || !form.due_date) {
            toast.error("Quarter, amount, and due date are required.");
            return;
        }
        const parsed = parseFloat(form.amount);
        if (!parsed || parsed <= 0) {
            toast.error("Enter a valid amount.");
            return;
        }
        setSubmitting(true);
        try {
            await api.post(`/water-bills/${unitNumber}`, {
                quarter: form.quarter,
                billing_period_start: form.billing_period_start || undefined,
                billing_period_end: form.billing_period_end || undefined,
                amount: parsed,
                due_date: form.due_date,
                water_usage_kl: form.water_usage_kl
                    ? parseFloat(form.water_usage_kl)
                    : undefined,
            });
            toast.success("Water bill added successfully.");
            onSuccess();
            onOpenChange(false);
            setForm({
                quarter: "",
                billing_period_start: "",
                billing_period_end: "",
                amount: "",
                due_date: "",
                water_usage_kl: "",
            });
        } catch (err: any) {
            toast.error(
                err?.response?.data?.detail || "Failed to add bill. Please try again."
            );
        } finally {
            setSubmitting(false);
        }
    }, [api, form, onOpenChange, onSuccess, unitNumber]);

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>Add Water Bill</DialogTitle>
                    <DialogDescription>
                        Enter details for a new Icon Water bill for Unit {unitNumber}.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div className="grid grid-cols-2 gap-3">
                        <div className="col-span-2 space-y-1.5">
                            <Label htmlFor="add-quarter">
                                Quarter <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="add-quarter"
                                placeholder="e.g. Q1 2025-26"
                                value={form.quarter}
                                onChange={handleChange("quarter")}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="add-bp-start">Billing Period Start</Label>
                            <Input
                                id="add-bp-start"
                                type="date"
                                value={form.billing_period_start}
                                onChange={handleChange("billing_period_start")}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="add-bp-end">Billing Period End</Label>
                            <Input
                                id="add-bp-end"
                                type="date"
                                value={form.billing_period_end}
                                onChange={handleChange("billing_period_end")}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="add-amount">
                                Amount (AUD) <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="add-amount"
                                type="number"
                                min="0.01"
                                step="0.01"
                                placeholder="0.00"
                                value={form.amount}
                                onChange={handleChange("amount")}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="add-due">
                                Due Date <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="add-due"
                                type="date"
                                value={form.due_date}
                                onChange={handleChange("due_date")}
                            />
                        </div>

                        <div className="col-span-2 space-y-1.5">
                            <Label htmlFor="add-usage">Water Usage (kL, optional)</Label>
                            <Input
                                id="add-usage"
                                type="number"
                                min="0"
                                step="0.01"
                                placeholder="e.g. 45.2"
                                value={form.water_usage_kl}
                                onChange={handleChange("water_usage_kl")}
                            />
                        </div>
                    </div>
                </div>

                <DialogFooter className="gap-2">
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={submitting}
                    >
                        Cancel
                    </Button>
                    <Button
                        onClick={handleSubmit}
                        disabled={submitting}
                        className="bg-blue-600 hover:bg-blue-700 text-white"
                    >
                        {submitting ? (
                            <>
                                <RefreshCw size={14} className="mr-2 animate-spin"/>
                                Saving...
                            </>
                        ) : (
                            <>
                                <Plus size={14} className="mr-2"/>
                                Add Bill
                            </>
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
// ---------------------------------------------------------------------------
// Table skeleton rows
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: TableSkeleton
 * Path: frontend/src/app/(app)/financials/water-bills/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const TableSkeleton = ({rows = 5}: { rows?: number }) => (
    <>
        {[...Array(rows)].map((_, i) => (
            <TableRow key={i}>
                {[...Array(8)].map((__, j) => (
                    <TableCell key={j}>
                        <Skeleton className="h-4 w-full"/>
                    </TableCell>
                ))}
            </TableRow>
        ))}
    </>
);
// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: Page
 * Path: frontend/src/app/(app)/financials/water-bills/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function Page() {
    const {api, hasPermission, isManager} = useAuth();

    const [selectedUnit, setSelectedUnit] = useState<string>("");
    const [units, setUnits] = useState<any[]>([]);
    const [bills, setBills] = useState<any[]>([]);
    const [tableLoading, setTableLoading] = useState(false);
    const [tableError, setTableError] = useState<string | null>(null);
    const [addBillOpen, setAddBillOpen] = useState(false);

    const canManage = hasPermission("can_manage_finances");
    const managerView = isManager();

    // Fetch units list for manager unit selector
    useEffect(() => {
        if (!managerView) return;
        api
            .get("/units?limit=200")
            .then((res: any) => setUnits(res.data ?? []))
            .catch(() => {
            });
    }, [api, managerView]);

    // Follow the sidebar's active unit: seed on load, and re-point on a genuine
    // unit switch. A manager's manual pick from the selector below is left alone
    // (the active unit has not changed, so nothing is re-applied).
    useActiveUnitSync(setSelectedUnit);

    // Fetch full history table for selected unit
    const fetchBills = useCallback(async () => {
        if (!selectedUnit) return;
        setTableLoading(true);
        setTableError(null);
        try {
            const res = await api.get(`/water-bills/${selectedUnit}`);
            setBills(res.data ?? []);
        } catch (err: any) {
            setTableError(
                err?.response?.data?.detail || "Failed to load water bill history."
            );
        } finally {
            setTableLoading(false);
        }
    }, [api, selectedUnit]);

    useEffect(() => {
        fetchBills();
    }, [fetchBills]);

    // Mark paid / partial from table
    const handleMarkPaid = useCallback(
        async (bill: any) => {
            try {
                await api.patch(`/water-bills/${bill.id}/mark-paid`, {
                    amount_paid: bill.amount,
                    payment_date: new Date().toISOString().slice(0, 10),
                });
                toast.success(`${bill.quarter} marked as paid.`);
                fetchBills();
            } catch (err: any) {
                toast.error(
                    err?.response?.data?.detail || "Failed to mark as paid."
                );
            }
        },
        [api, fetchBills]
    );

    return (
        <div className="p-6 space-y-6">
            {/* Page header */}
            <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
                        <Droplets className="text-blue-500" size={26}/>
                        Icon Water Bills
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        View and manage water bill payments for your unit.
                    </p>
                </div>

                {/* Manager: unit selector + add button */}
                {managerView && (
                    <div className="flex items-center gap-3 flex-wrap">
                        <Select
                            value={selectedUnit}
                            onValueChange={(v) => setSelectedUnit(v)}
                        >
                            <SelectTrigger
                                className="w-44"
                                data-testid="wb-unit-selector"
                            >
                                <SelectValue placeholder="Select unit"/>
                            </SelectTrigger>
                            <SelectContent>
                                {units.map((u: any) => (
                                    <SelectItem key={u.unit_number} value={u.unit_number}>
                                        {u.unit_number}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        <Button
                            size="sm"
                            className="bg-blue-600 hover:bg-blue-700 text-white"
                            onClick={() => setAddBillOpen(true)}
                            data-testid="wb-page-add-bill"
                        >
                            <Plus size={14} className="mr-1.5"/>
                            Add Bill
                        </Button>
                    </div>
                )}
            </div>

            {/* Current bill card (full width) */}
            <WaterBillCard unitNumber={selectedUnit || undefined}/>

            {/* Full history table */}
            <Card className="border shadow-sm">
                <CardHeader className="pb-3">
                    <CardTitle className="text-base font-bold">
                        Complete Bill History
                    </CardTitle>
                    <CardDescription className="text-xs">
                        All water bills recorded for Unit {selectedUnit || "—"}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {tableError ? (
                        <div className="text-center py-10 text-slate-400">
                            <AlertCircle className="mx-auto mb-2 text-red-400" size={28}/>
                            <p className="text-sm">{tableError}</p>
                            <Button
                                variant="outline"
                                size="sm"
                                className="mt-3"
                                onClick={fetchBills}
                            >
                                <RefreshCw size={14} className="mr-2"/>
                                Retry
                            </Button>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Quarter</TableHead>
                                        <TableHead>Billing Period</TableHead>
                                        <TableHead className="text-right">Amount</TableHead>
                                        <TableHead>Due Date</TableHead>
                                        <TableHead className="text-right">Paid</TableHead>
                                        <TableHead className="text-right">Balance</TableHead>
                                        <TableHead className="text-center">
                      <span className="flex items-center gap-1">
                        <Gauge size={12}/>
                        Usage
                      </span>
                                        </TableHead>
                                        <TableHead>Status</TableHead>
                                        {canManage && (
                                            <TableHead className="text-center">Actions</TableHead>
                                        )}
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {tableLoading ? (
                                        <TableSkeleton rows={5}/>
                                    ) : bills.length === 0 ? (
                                        <TableRow>
                                            <TableCell
                                                colSpan={canManage ? 9 : 8}
                                                className="text-center text-slate-400 py-12"
                                            >
                                                <Droplets
                                                    className="mx-auto mb-2 text-slate-200"
                                                    size={32}
                                                />
                                                <p className="text-sm">No water bills found.</p>
                                            </TableCell>
                                        </TableRow>
                                    ) : (
                                        bills.map((bill: any) => {
                                            const balance = Math.max(
                                                0,
                                                (bill.amount ?? 0) - (bill.amount_paid ?? 0)
                                            );
                                            return (
                                                <TableRow key={bill.id}>
                                                    <TableCell className="font-medium">
                                                        {bill.quarter}
                                                    </TableCell>
                                                    <TableCell className="text-xs text-slate-500">
                                                        {bill.billing_period_start && bill.billing_period_end
                                                            ? `${formatDate(bill.billing_period_start)} — ${formatDate(bill.billing_period_end)}`
                                                            : "—"}
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums font-medium">
                                                        {formatCurrency(bill.amount)}
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                            <span className="flex items-center gap-1 text-slate-600">
                              <CalendarDays size={12}/>
                                {bill.due_date ? formatDate(bill.due_date) : "—"}
                            </span>
                                                    </TableCell>
                                                    <TableCell className="text-right tabular-nums text-emerald-700">
                                                        {bill.amount_paid
                                                            ? formatCurrency(bill.amount_paid)
                                                            : "—"}
                                                    </TableCell>
                                                    <TableCell
                                                        className={`text-right tabular-nums font-medium ${
                                                            balance > 0
                                                                ? "text-amber-700"
                                                                : "text-emerald-600"
                                                        }`}
                                                    >
                                                        {balance > 0 ? formatCurrency(balance) : "Nil"}
                                                    </TableCell>
                                                    <TableCell className="text-sm text-slate-500">
                                                        {bill.water_usage_kl != null
                                                            ? `${bill.water_usage_kl} kL`
                                                            : "—"}
                                                    </TableCell>
                                                    <TableCell>
                                                        <StatusBadge status={bill.status ?? "unpaid"}/>
                                                    </TableCell>
                                                    {canManage && (
                                                        <TableCell className="text-center">
                                                            {bill.status !== "paid" && (
                                                                <Button
                                                                    variant="ghost"
                                                                    size="sm"
                                                                    className="text-emerald-600 hover:text-emerald-700 hover:bg-emerald-50 text-xs h-7 px-2"
                                                                    onClick={() => handleMarkPaid(bill)}
                                                                    data-testid={`wb-row-paid-${bill.id}`}
                                                                >
                                                                    <CheckCircle size={12} className="mr-1"/>
                                                                    Mark Paid
                                                                </Button>
                                                            )}
                                                            {bill.status === "paid" && (
                                                                <span
                                                                    className="text-xs text-emerald-600 flex items-center gap-1">
                                  <CheckCircle size={12}/>
                                  Paid
                                </span>
                                                            )}
                                                        </TableCell>
                                                    )}
                                                </TableRow>
                                            );
                                        })
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Add bill dialog (manager) */}
            <AddBillDialog
                open={addBillOpen}
                onOpenChange={setAddBillOpen}
                unitNumber={selectedUnit}
                onSuccess={fetchBills}
                api={api}
            />
        </div>
    );
}
