"use client";
import React, { useCallback, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Skeleton } from '../ui/skeleton';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, } from '../ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger, } from '../ui/tooltip';
import {
    AlertCircle,
    CalendarDays,
    CheckCircle,
    Clock,
    DollarSign,
    Droplets,
    Gauge,
    Info,
    Plus,
    RefreshCw,
    XCircle,
} from 'lucide-react';
import { formatCurrency, formatDate } from '../../lib/utils';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { toast } from 'sonner';

/**
 * Render a served daily supply rate.
 *
 * Missing is not zero: with no served rate this returns an em dash, never
 * "$0.0000 / day" — which reads as a real free-water rate rather than an absent
 * one. The rates are ACT regulated figures with an effective period and come from
 * GET /water-bills/quarterly-estimates; this file used to carry typed copies of
 * them with a financial year written in beside them by hand.
 */
const dailyRate = (value) =>
    typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(4)} / day` : '—';

// ---------------------------------------------------------------------------
// Status badge helper
// ---------------------------------------------------------------------------
const statusConfig = {
    paid: {
        label: 'Paid',
        className: 'bg-emerald-100 text-emerald-700 border-emerald-200',
        icon: CheckCircle,
    },
    partial: {
        label: 'Partial',
        className: 'bg-amber-100 text-amber-700 border-amber-200',
        icon: Clock,
    },
    unpaid: {
        label: 'Unpaid',
        className: 'bg-red-100 text-red-700 border-red-200',
        icon: AlertCircle,
    },
};
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/components/dashboard/WaterBillCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StatusBadge = ({status}) => {
    const cfg = statusConfig[ status ] ?? statusConfig.unpaid;
    const Icon = cfg.icon;
    return (
        <Badge variant="outline" className={`text-xs font-semibold ${cfg.className}`}>
            <Icon size={11} className="mr-1"/>
            {cfg.label}
        </Badge>
    );
};
// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: WaterBillSkeleton
 * Path: frontend/src/components/dashboard/WaterBillCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const WaterBillSkeleton = () => (
    <Card className="border shadow-sm">
        <CardHeader className="pb-3">
            <Skeleton className="h-5 w-48"/>
            <Skeleton className="h-3 w-32 mt-1"/>
        </CardHeader>
        <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
                {[...Array(4)].map((_, i) => (
                    <div key={i} className="space-y-1.5">
                        <Skeleton className="h-3 w-20"/>
                        <Skeleton className="h-5 w-24"/>
                    </div>
                ))}
            </div>
            <Skeleton className="h-10 w-full"/>
            <div className="flex gap-2 pt-2">
                <Skeleton className="h-9 w-32"/>
                <Skeleton className="h-9 w-40"/>
            </div>
        </CardContent>
    </Card>
);
// ---------------------------------------------------------------------------
// Mark paid / partial dialog
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: PayDialog
 * Path: frontend/src/components/dashboard/WaterBillCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PayDialog = ({open, onOpenChange, mode, bill, onSuccess, api}) => {
    const [amount, setAmount] = useState('');
    const [reference, setReference] = useState('');
    const [paymentDate, setPaymentDate] = useState(
        new Date().toISOString().slice(0, 10)
    );
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        if (open) {
            const remaining = bill
                ? ( bill.amount ?? 0 ) - ( bill.amount_paid ?? 0 )
                : 0;
            setAmount(mode === 'full' ? String(remaining > 0 ? remaining : bill?.amount ?? '') : '');
            setReference('');
            setPaymentDate(new Date().toISOString().slice(0, 10));
        }
    }, [open, mode, bill]);

    const handleSubmit = useCallback(async () => {
        if (!bill) return;
        const parsed = parseFloat(amount);
        if (!parsed || parsed <= 0) {
            toast.error('Please enter a valid payment amount.');
            return;
        }
        setSubmitting(true);
        try {
            const endpoint =
                mode === 'full'
                    ? `/water-bills/${bill.id}/mark-paid`
                    : `/water-bills/${bill.id}/mark-partial`;

            await api.patch(endpoint, {
                amount_paid: parsed,
                reference: reference || undefined,
                payment_date: paymentDate,
            });

            toast.success(
                mode === 'full'
                    ? 'Water bill marked as paid.'
                    : `Partial payment of ${formatCurrency(parsed)} recorded.`
            );
            onSuccess();
            onOpenChange(false);
        } catch (err) {
            const msg =
                err?.response?.data?.detail || 'Failed to record payment. Please try again.';
            toast.error(msg);
        } finally {
            setSubmitting(false);
        }
    }, [amount, api, bill, mode, onOpenChange, onSuccess, paymentDate, reference]);

    const isFullPay = mode === 'full';
    const title = isFullPay ? 'Mark Water Bill as Paid' : 'Record Partial Payment';
    const description = isFullPay
        ? `Confirm payment of the ${bill?.quarter ?? ''} water bill.`
        : `Record a partial payment toward the ${bill?.quarter ?? ''} water bill.`;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div className="space-y-1.5">
                        <Label htmlFor="wb-amount">
                            Amount (AUD)
                            {!isFullPay && <span className="text-red-500 ml-0.5">*</span>}
                        </Label>
                        <Input
                            id="wb-amount"
                            type="number"
                            min="0.01"
                            step="0.01"
                            placeholder="0.00"
                            value={amount}
                            onChange={(e) => setAmount(e.target.value)}
                            readOnly={isFullPay}
                            className={isFullPay ? 'bg-muted text-foreground' : ''}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="wb-date">Payment Date</Label>
                        <Input
                            id="wb-date"
                            type="date"
                            value={paymentDate}
                            onChange={(e) => setPaymentDate(e.target.value)}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="wb-ref">Reference / Receipt No. (optional)</Label>
                        <Input
                            id="wb-ref"
                            placeholder="e.g. ICON-2025-XXXX"
                            value={reference}
                            onChange={(e) => setReference(e.target.value)}
                        />
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
                        className="bg-emerald-600 hover:bg-emerald-700 text-white"
                    >
                        {submitting ? (
                            <>
                                <RefreshCw size={14} className="mr-2 animate-spin"/>
                                Saving...
                            </>
                        ) : (
                            <>
                                <CheckCircle size={14} className="mr-2"/>
                                {isFullPay ? 'Confirm Paid' : 'Record Payment'}
                            </>
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
// ---------------------------------------------------------------------------
// Add bill dialog (managers / admins only)
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: AddBillDialog
 * Path: frontend/src/components/dashboard/WaterBillCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AddBillDialog = ({open, onOpenChange, unitNumber, onSuccess, api}) => {
    const [form, setForm] = useState({
        quarter: '',
        billing_period_start: '',
        billing_period_end: '',
        amount: '',
        due_date: '',
        water_usage_kl: '',
    });
    const [submitting, setSubmitting] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: handleChange
     * Path: frontend/src/components/dashboard/WaterBillCard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (field) => (e) =>
        setForm((prev) => ( {...prev, [ field ]: e.target.value} ));

    const handleSubmit = useCallback(async () => {
        if (!form.quarter || !form.amount || !form.due_date) {
            toast.error('Quarter, amount, and due date are required.');
            return;
        }
        const parsed = parseFloat(form.amount);
        if (!parsed || parsed <= 0) {
            toast.error('Enter a valid amount.');
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
            toast.success('Water bill added successfully.');
            onSuccess();
            onOpenChange(false);
            setForm({
                quarter: '',
                billing_period_start: '',
                billing_period_end: '',
                amount: '',
                due_date: '',
                water_usage_kl: '',
            });
        } catch (err) {
            const msg =
                err?.response?.data?.detail || 'Failed to add water bill. Please try again.';
            toast.error(msg);
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
                        Enter the details for the new Icon Water bill for Unit {unitNumber}.
                        Icon Water charges are GST-free — enter the total amount as shown on the bill.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div className="grid grid-cols-2 gap-3">
                        <div className="col-span-2 space-y-1.5">
                            <Label htmlFor="wb-quarter">
                                Quarter <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="wb-quarter"
                                placeholder="e.g. Q1 2025-26"
                                value={form.quarter}
                                onChange={handleChange('quarter')}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="wb-bp-start">Billing Period Start</Label>
                            <Input
                                id="wb-bp-start"
                                type="date"
                                value={form.billing_period_start}
                                onChange={handleChange('billing_period_start')}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="wb-bp-end">Billing Period End</Label>
                            <Input
                                id="wb-bp-end"
                                type="date"
                                value={form.billing_period_end}
                                onChange={handleChange('billing_period_end')}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="wb-bill-amount">
                                Amount (AUD) <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="wb-bill-amount"
                                type="number"
                                min="0.01"
                                step="0.01"
                                placeholder="0.00"
                                value={form.amount}
                                onChange={handleChange('amount')}
                            />
                        </div>

                        <div className="space-y-1.5">
                            <Label htmlFor="wb-due">
                                Due Date <span className="text-red-500">*</span>
                            </Label>
                            <Input
                                id="wb-due"
                                type="date"
                                value={form.due_date}
                                onChange={handleChange('due_date')}
                            />
                        </div>

                        <div className="col-span-2 space-y-1.5">
                            <Label htmlFor="wb-usage">Water Usage (kL, optional)</Label>
                            <Input
                                id="wb-usage"
                                type="number"
                                min="0"
                                step="0.01"
                                placeholder="e.g. 45.2"
                                value={form.water_usage_kl}
                                onChange={handleChange('water_usage_kl')}
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
                        className="bg-primary text-primary-foreground hover:bg-primary/90"
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
// Small history bill mini-card
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: HistoryBillCard
 * Path: frontend/src/components/dashboard/WaterBillCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const HistoryBillCard = ({bill}) => {
    const isPaid = bill.status === 'paid';
    const isPartial = bill.status === 'partial';
    const amountPaid = bill.amount_paid ?? 0;
    const balance = Math.max(0, ( bill.amount ?? 0 ) - amountPaid);

    return (
        <div className="rounded-xl border border-border bg-muted p-3 space-y-1.5">
            <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground">{bill.quarter}</span>
                <StatusBadge status={bill.status}/>
            </div>

            <p className="text-sm font-bold text-foreground tabular-nums">
                {formatCurrency(bill.amount)}
            </p>

            {/* Partial: show paid vs total */}
            {isPartial && amountPaid > 0 && (
                <p className="text-[11px] text-amber-600 font-medium tabular-nums">
                    Paid {formatCurrency(amountPaid)} · Balance {formatCurrency(balance)}
                </p>
            )}

            {/* Paid: show payment date */}
            {isPaid && bill.payment_date && (
                <p className="text-[11px] text-emerald-600 flex items-center gap-1">
                    <CheckCircle size={10}/>
                    Paid {formatDate(bill.payment_date)}
                </p>
            )}

            {/* Water usage */}
            {bill.water_usage_kl != null && (
                <p className="text-[11px] text-muted-foreground flex items-center gap-1">
                    <Gauge size={10}/>
                    {bill.water_usage_kl} kL
                </p>
            )}

            {/* Due date (only when not yet paid) */}
            {!isPaid && bill.due_date && (
                <p className="text-[11px] text-muted-foreground flex items-center gap-1">
                    <CalendarDays size={10}/>
                    Due {formatDate(bill.due_date)}
                </p>
            )}
        </div>
    );
};
// ---------------------------------------------------------------------------
// Main card
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: WaterBillCard
 * Path: frontend/src/components/dashboard/WaterBillCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const WaterBillCard = ({unitNumber: propUnitNumber}) => {
    const {api, hasPermission, isManager, selectedBuilding} = useAuth();
    const {activeUnit} = useActiveUnit();

    // Fall back to the sidebar's active unit, not the account default, so a
    // multi-unit owner's switch reaches cards rendered without an explicit prop.
    const unitNumber = propUnitNumber || activeUnit;

    const [bills, setBills] = useState([]);
    // Supply rates and per-quarter estimates, served together by the rate endpoint.
    const [estimates, setEstimates] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    const [fullPayDialog, setFullPayDialog] = useState(false);
    const [partialPayDialog, setPartialPayDialog] = useState(false);
    const [addBillDialog, setAddBillDialog] = useState(false);
    const [supplyBreakdownDialog, setSupplyBreakdownDialog] = useState(false);

    const canManage = hasPermission('can_manage_finances');
    const canAdd = isManager();

    const fetchBills = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        setLoading(true);
        setError(null);
        try {
            // Estimates are fetched with allSettled, not Promise.all: a rate-service
            // outage must not turn into "no water bills found for this unit", which
            // is a different and much more alarming statement.
            const [billsRes, estRes] = await Promise.allSettled([
                api.get(`/water-bills/${unitNumber}`),
                api.get(`/water-bills/quarterly-estimates?year=${new Date().getFullYear()}`),
            ]);
            setEstimates(estRes.status === 'fulfilled' ? (estRes.value.data ?? null) : null);
            if (billsRes.status === 'rejected') throw billsRes.reason;
            setBills(billsRes.value.data ?? []);
        } catch (err) {
            const msg =
                err?.response?.status === 404
                    ? 'No water bills found for this unit.'
                    : err?.response?.data?.detail || 'Failed to load water bills.';
            setError(msg);
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetchBills();
    }, [fetchBills]);

    const handleSuccess = useCallback(() => fetchBills(), [fetchBills]);

    const waterDaily = estimates?.water_daily_rate;
    const sewerDaily = estimates?.sewer_daily_rate;
    const combinedDaily =
        typeof waterDaily === 'number' && typeof sewerDaily === 'number' ? waterDaily + sewerDaily : null;
    const scheduleLabel = estimates?.rate_schedule?.schedule_label;
    const quarterEstimates = estimates?.estimates ?? [];
    // Calendar-year quarter, matching how the endpoint buckets them (Jan-Mar = Q1).
    const currentQuarterEstimate =
        quarterEstimates.find((q) => q.quarter === `Q${Math.floor(new Date().getMonth() / 3) + 1}`) ?? null;

    // The most recent bill is treated as the "current" bill
    const currentBill = bills[ 0 ] ?? null;
    const historyBills = bills.slice(1, 4);

    const balanceDue = currentBill
        ? Math.max(0, ( currentBill.amount ?? 0 ) - ( currentBill.amount_paid ?? 0 ))
        : 0;

    // --- Loading ---
    if (loading) return <WaterBillSkeleton/>;

    // --- No unit ---
    if (!unitNumber) {
        return (
            <Card className="border shadow-sm">
                <CardContent className="p-6 text-center text-muted-foreground">
                    <Droplets className="mx-auto mb-2 text-muted-foreground" size={36}/>
                    <p className="text-sm">No unit assigned to your account.</p>
                </CardContent>
            </Card>
        );
    }

    // --- Error ---
    if (error) {
        return (
            <Card className="border shadow-sm border-red-100">
                <CardContent className="p-6">
                    <div className="flex flex-col items-center gap-3 text-center">
                        <XCircle className="text-red-400" size={36}/>
                        <div>
                            <p className="font-medium text-foreground">Unable to load water bills</p>
                            <p className="text-sm text-muted-foreground mt-1">{error}</p>
                        </div>
                        <Button variant="outline" size="sm" onClick={fetchBills}>
                            <RefreshCw size={14} className="mr-2"/>
                            Try Again
                        </Button>
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <>
            <Card className="border shadow-sm">
                <CardHeader className="pb-3">
                    <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                            <div className="p-2 rounded-xl bg-primary/10 text-primary">
                                <Droplets size={18}/>
                            </div>
                            <div>
                                <div className="flex items-center gap-2">
                                    <CardTitle className="text-base font-bold leading-tight">
                                        Icon Water Bills
                                    </CardTitle>
                                    <Badge variant="outline"
                                           className="text-[10px] font-semibold bg-green-50 text-green-700 border-green-200 px-1.5 py-0">
                                        GST-free
                                    </Badge>
                                </div>
                                <CardDescription className="text-xs mt-0.5">
                                    Unit {unitNumber}
                                    {currentBill ? ` — Latest: ${currentBill.quarter}` : ''}
                                </CardDescription>
                            </div>
                        </div>

                        <div className="flex items-center gap-2">
                            {currentBill && <StatusBadge status={currentBill.status}/>}
                            {canAdd && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setAddBillDialog(true)}
                                    data-testid="water-bill-add"
                                >
                                    <Plus size={14} className="mr-1"/>
                                    Add Bill
                                </Button>
                            )}
                        </div>
                    </div>
                </CardHeader>

                <CardContent className="space-y-4">
                    {bills.length === 0 ? (
                        <div className="space-y-4">
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <motion.div
                                        className="rounded-xl bg-muted/60 border border-border p-4 space-y-3 cursor-pointer hover:bg-muted transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                                        onClick={() => setSupplyBreakdownDialog(true)}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter' || e.key === ' ') {
                                                e.preventDefault();
                                                setSupplyBreakdownDialog(true);
                                            }
                                        }}
                                        tabIndex={0}
                                        role="button"
                                        whileTap={{scale: 0.98}}
                                        aria-label="View Icon Water fixed supply charge estimates"
                                    >
                                        <div className="flex items-center gap-3 text-muted-foreground mb-2">
                                            <Info size={16} className="text-primary"/>
                                            <p className="text-sm font-medium text-foreground">Fixed Supply Estimates</p>
                                        </div>

                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="p-3 bg-card rounded-lg border border-border">
                                                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest mb-1">Daily
                                                    Supply</p>
                                                <p className="text-lg font-semibold text-foreground">
                                                    {combinedDaily != null ? `$${combinedDaily.toFixed(4)}` : '—'}
                                                    {combinedDaily != null && (
                                                        <span className="text-xs font-medium text-muted-foreground"> / day</span>
                                                    )}
                                                </p>
                                            </div>
                                            <div className="p-3 bg-card rounded-lg border border-border">
                                                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest mb-1">
                                                    {currentQuarterEstimate ? `${currentQuarterEstimate.quarter} Est.` : 'Quarterly Est.'}
                                                </p>
                                                <p className="text-lg font-semibold text-foreground">
                                                    {currentQuarterEstimate
                                                        ? formatCurrency(currentQuarterEstimate.supply_total)
                                                        : '—'}
                                                </p>
                                            </div>
                                        </div>

                                        <div className="bg-primary/5 p-2.5 rounded-lg border border-primary/15">
                                            <p className="text-[10px] text-primary leading-relaxed">
                                                Icon Water (ACT) charges the same fixed daily supply rate to every unit.
                                                Usage charges are additional based on shared metered consumption.
                                            </p>
                                        </div>

                                        <p className="text-[10px] text-muted-foreground flex items-center gap-1">
                                            <Droplets size={10}/>
                                            Click to view detailed supply charge breakdown
                                        </p>
                                    </motion.div>
                                </TooltipTrigger>
                                <TooltipContent>
                                    <p>Click to view fixed supply charge estimates</p>
                                </TooltipContent>
                            </Tooltip>

                            {canAdd && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    className="w-full"
                                    onClick={() => setAddBillDialog(true)}
                                >
                                    <Plus size={14} className="mr-1.5"/>
                                    Add First Unit Bill
                                </Button>
                            )}
                        </div>
                    ) : (
                        <>
                            {/* Current bill detail panel — clickable to show supply charge breakdown */}
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <motion.div
                                        className="rounded-xl bg-muted/60 border border-border p-4 space-y-3 cursor-pointer hover:bg-muted transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                                        onClick={() => setSupplyBreakdownDialog(true)}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter' || e.key === ' ') {
                                                e.preventDefault();
                                                setSupplyBreakdownDialog(true);
                                            }
                                        }}
                                        tabIndex={0}
                                        role="button"
                                        whileTap={{scale: 0.98}}
                                        aria-label={`View Icon Water supply charge breakdown for ${currentBill.quarter}`}
                                        data-testid="water-bill-supply-breakdown-trigger"
                                    >
                                        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
                                            <div>
                                                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest mb-0.5">
                                                    Quarter
                                                </p>
                                                <p className="text-sm font-semibold text-foreground">
                                                    {currentBill.quarter}
                                                </p>
                                            </div>

                                            <div>
                                                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest mb-0.5">
                                                    Amount
                                                </p>
                                                <p className="text-sm font-bold text-foreground tabular-nums">
                                                    {formatCurrency(currentBill.amount)}
                                                </p>
                                            </div>

                                            <div>
                                                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest mb-0.5">
                                                    Due Date
                                                </p>
                                                <p className="text-sm text-foreground flex items-center gap-1">
                                                    <CalendarDays size={12} className="text-muted-foreground"/>
                                                    {currentBill.due_date ? formatDate(currentBill.due_date) : 'N/A'}
                                                </p>
                                            </div>

                                            {currentBill.water_usage_kl !== undefined && (
                                                <div>
                                                    <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest mb-0.5">
                                                        Usage
                                                    </p>
                                                    <p className="text-sm text-foreground flex items-center gap-1">
                                                        <Gauge size={12} className="text-muted-foreground"/>
                                                        {currentBill.water_usage_kl} kL
                                                    </p>
                                                </div>
                                            )}
                                        </div>

                                        {currentBill.billing_period_start && currentBill.billing_period_end && (
                                            <p className="text-[11px] text-muted-foreground">
                                                Billing period: {formatDate(currentBill.billing_period_start)} —{' '}
                                                {formatDate(currentBill.billing_period_end)}
                                            </p>
                                        )}

                                        <p className="text-[10px] text-muted-foreground flex items-center gap-1">
                                            <Droplets size={10}/>
                                            Click to view Icon Water supply charge breakdown
                                        </p>
                                    </motion.div>
                                </TooltipTrigger>
                                <TooltipContent>
                                    <p>Click to see how supply charges are calculated</p>
                                </TooltipContent>
                            </Tooltip>

                            {/* Payment summary */}
                            {currentBill.amount_paid !== undefined && currentBill.amount_paid > 0 && (
                                <div
                                    className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 flex items-center justify-between">
                  <span className="text-sm text-emerald-800 font-medium flex items-center gap-1.5">
                    <CheckCircle size={14}/>
                    Amount Paid
                  </span>
                                    <span className="text-sm font-semibold text-emerald-800 tabular-nums">
                    {formatCurrency(currentBill.amount_paid)}
                  </span>
                                </div>
                            )}

                            {balanceDue > 0 && (
                                <div
                                    className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-3 flex items-center justify-between">
                  <span className="text-sm text-amber-800 font-medium flex items-center gap-1.5">
                    <AlertCircle size={14}/>
                    Balance Due
                  </span>
                                    <span className="text-sm font-semibold text-amber-900 tabular-nums">
                    {formatCurrency(balanceDue)}
                  </span>
                                </div>
                            )}

                            {/* Action buttons */}
                            {canManage && currentBill.status !== 'paid' && (
                                <div className="flex flex-wrap gap-2 pt-1">
                                    <Button
                                        size="sm"
                                        className="bg-emerald-600 hover:bg-emerald-700 text-white"
                                        onClick={() => setFullPayDialog(true)}
                                        data-testid="water-bill-mark-paid"
                                    >
                                        <CheckCircle size={14} className="mr-1.5"/>
                                        Mark as Paid
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setPartialPayDialog(true)}
                                        data-testid="water-bill-partial-pay"
                                    >
                                        <DollarSign size={14} className="mr-1.5"/>
                                        Record Partial Payment
                                    </Button>
                                </div>
                            )}

                            {currentBill.status === 'paid' && (
                                <div className="flex items-center gap-2 text-sm text-emerald-700 font-medium pt-1">
                                    <CheckCircle size={16} className="text-emerald-500"/>
                                    {currentBill.quarter} bill fully paid.
                                </div>
                            )}

                            {/* Payment history (last 3 previous bills) */}
                            {historyBills.length > 0 && (
                                <div className="space-y-2 pt-2">
                                    <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest">
                                        Payment History
                                    </p>
                                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                                        {historyBills.map((bill) => (
                                            <HistoryBillCard key={bill.id} bill={bill}/>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </CardContent>
            </Card>

            <PayDialog
                open={fullPayDialog}
                onOpenChange={setFullPayDialog}
                mode="full"
                bill={currentBill}
                onSuccess={handleSuccess}
                api={api}
            />

            <PayDialog
                open={partialPayDialog}
                onOpenChange={setPartialPayDialog}
                mode="partial"
                bill={currentBill}
                onSuccess={handleSuccess}
                api={api}
            />

            <AddBillDialog
                open={addBillDialog}
                onOpenChange={setAddBillDialog}
                unitNumber={unitNumber}
                onSuccess={handleSuccess}
                api={api}
            />

            {/* Icon Water supply charge breakdown dialog */}
            <Dialog open={supplyBreakdownDialog} onOpenChange={setSupplyBreakdownDialog}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Droplets size={18} className="text-primary"/>
                            Icon Water Supply Charge Breakdown
                        </DialogTitle>
                        <DialogDescription>
                            {currentBill?.quarter
                                ? `${currentBill.quarter} bill breakdown for Unit ${unitNumber}`
                                : 'Icon Water (ACT) charges fixed daily supply rates regardless of usage.'}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2 text-sm text-foreground">
                        {/* Daily rates table */}
                        <div className="rounded-xl bg-muted border border-border px-4 py-3 space-y-2">
                            <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-1">
                                Daily Supply Rates{scheduleLabel ? ` (${scheduleLabel})` : ''}
                            </p>
                            <div className="space-y-1.5">
                                <div className="flex items-center justify-between">
                                    <span className="text-muted-foreground">Water Supply</span>
                                    <span className="font-semibold tabular-nums text-foreground">{dailyRate(waterDaily)}</span>
                                </div>
                                <div className="flex items-center justify-between">
                                    <span className="text-muted-foreground">Sewerage Supply</span>
                                    <span className="font-semibold tabular-nums text-foreground">{dailyRate(sewerDaily)}</span>
                                </div>
                                <div
                                    className="flex items-center justify-between border-t border-border pt-1.5 mt-1">
                                    <span className="font-semibold text-foreground">Total Daily Supply Charge</span>
                                    <span className="font-bold tabular-nums text-foreground">{dailyRate(combinedDaily)}</span>
                                </div>
                            </div>
                            {!estimates && (
                                <p className="text-[11px] text-muted-foreground">
                                    Rates unavailable — the supply-rate service did not respond.
                                </p>
                            )}
                        </div>

                        {/* Current bill actual breakdown (if stored) */}
                        {currentBill?.supply_breakdown && (
                            <div className="rounded-xl bg-primary/5 border border-primary/15 px-4 py-3 space-y-2">
                                <p className="text-xs font-bold text-primary uppercase tracking-widest mb-1">
                                    {currentBill.quarter} — Actual Bill Breakdown
                                </p>
                                <div className="space-y-1.5">
                                    <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">
                      Water Supply ({currentBill.supply_breakdown.days} days)
                    </span>
                                        <span className="font-semibold tabular-nums text-foreground">
                      {formatCurrency(currentBill.supply_breakdown.water_supply_charge)}
                    </span>
                                    </div>
                                    <div className="flex items-center justify-between">
                    <span className="text-muted-foreground">
                      Sewerage Supply ({currentBill.supply_breakdown.days} days)
                    </span>
                                        <span className="font-semibold tabular-nums text-foreground">
                      {formatCurrency(currentBill.supply_breakdown.sewerage_supply_charge)}
                    </span>
                                    </div>
                                    {currentBill.supply_breakdown.usage_charge != null && currentBill.water_usage_kl != null && (
                                        <div className="flex items-center justify-between">
                      <span className="text-muted-foreground flex items-center gap-1">
                        <Gauge size={12} className="text-muted-foreground"/>
                        Usage ({currentBill.water_usage_kl} kL)
                      </span>
                                            <span className="font-semibold tabular-nums text-foreground">
                        {formatCurrency(currentBill.supply_breakdown.usage_charge)}
                      </span>
                                        </div>
                                    )}
                                    <div
                                        className="flex items-center justify-between border-t border-primary/25 pt-1.5 mt-1">
                                        <span className="font-bold text-foreground">Total Bill</span>
                                        <span className="font-bold tabular-nums text-foreground">
                      {formatCurrency(currentBill.amount)}
                    </span>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Quarterly estimates (when no stored breakdown) */}
                        {!currentBill?.supply_breakdown && (
                            <div className="space-y-1">
                                <p className="font-semibold text-foreground">Estimated Quarterly Supply Charges</p>
                                <p className="text-muted-foreground text-xs mb-2">
                                    Supply charges only — excludes usage charges (per kL consumed).
                                </p>
                                {quarterEstimates.length > 0 ? (
                                    <div className="grid grid-cols-2 gap-2">
                                        {quarterEstimates.map((q) => (
                                            <div
                                                key={q.quarter}
                                                className="rounded-lg bg-primary/5 border border-primary/15 px-3 py-2"
                                            >
                                                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
                                                    {q.quarter} ({q.days} days)
                                                </p>
                                                <p className="text-sm font-bold text-foreground tabular-nums">
                                                    {formatCurrency(q.supply_total)}
                                                </p>
                                                <p className="text-[10px] text-muted-foreground">supply only</p>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <p className="text-xs text-muted-foreground">
                                        Quarterly estimates unavailable — the supply-rate service did not respond.
                                    </p>
                                )}
                            </div>
                        )}

                        {/* Usage charges note */}
                        <div className="rounded-xl bg-amber-50 border border-amber-100 px-4 py-3 flex gap-2">
                            <Gauge size={14} className="text-amber-500 flex-shrink-0 mt-0.5"/>
                            <p className="text-xs text-amber-700 leading-relaxed">
                                <strong>Usage charges</strong> (per kL consumed) are <strong>additional</strong>{' '}
                                and vary based on each meter reading. They are calculated separately and added to
                                the fixed supply charge to form your total quarterly bill.
                            </p>
                        </div>

                        {/* All units note */}
                        <div className="rounded-xl bg-muted border border-border px-4 py-3 flex gap-2">
                            <Droplets size={14} className="text-muted-foreground flex-shrink-0 mt-0.5"/>
                            <p className="text-xs text-muted-foreground leading-relaxed">
                                These fixed daily rates apply to units
                                at {selectedBuilding?.name || 'this complex'} equally.
                                Rates are as published by Icon Water ACT for the current financial year.
                            </p>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
};

export default WaterBillCard;
