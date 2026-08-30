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
    Building2,
    CheckCircle,
    ChevronDown,
    ChevronUp,
    Clock,
    DollarSign,
    Info,
    Landmark,
    RefreshCw,
    XCircle,
} from 'lucide-react';
import { formatCurrency } from '../../lib/utils';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { toast } from 'sonner';
// ---------------------------------------------------------------------------
// Small labelled row used inside the rates breakdown table
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: RatesRow
 * Path: frontend/src/components/dashboard/CouncilRatesCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const RatesRow = ({label, value, highlight = false, tooltip, muted = false}) => (
    <div
        className={`flex items-center justify-between py-2 border-b last:border-b-0 border-slate-100 ${
            highlight ? 'font-semibold' : ''
        }`}
    >
    <span
        className={`text-sm flex items-center gap-1 ${
            muted ? 'text-slate-400' : highlight ? 'text-slate-800' : 'text-slate-600'
        }`}
    >
      {label}
        {tooltip && (
            <Tooltip>
                <TooltipTrigger asChild>
            <span
                className="ml-1 text-slate-400 cursor-help focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-full outline-none inline-flex"
                tabIndex={0}
                role="img"
                aria-label="More information"
            >
              <Info size={12}/>
            </span>
                </TooltipTrigger>
                <TooltipContent>
                    <p className="max-w-[200px] text-xs leading-relaxed">{tooltip}</p>
                </TooltipContent>
            </Tooltip>
        )}
    </span>
        <span
            className={`text-sm tabular-nums ${
                muted ? 'text-slate-400' : highlight ? 'text-slate-900 text-base' : 'text-slate-700'
            }`}
        >
      {value}
    </span>
    </div>
);
// ---------------------------------------------------------------------------
// Quarterly breakdown mini-table
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: QuarterlyTable
 * Path: frontend/src/components/dashboard/CouncilRatesCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const QuarterlyTable = ({q1, q2, q3, q4, labels}) => {
    const quarters = [
        {key: 'q1', label: labels?.[ 0 ] ?? 'Q1 (Jul–Sep)', due: 'due 31 Aug', amount: q1},
        {key: 'q2', label: labels?.[ 1 ] ?? 'Q2 (Oct–Dec)', due: 'due 30 Nov', amount: q2},
        {key: 'q3', label: labels?.[ 2 ] ?? 'Q3 (Jan–Mar)', due: 'due 28 Feb', amount: q3},
        {key: 'q4', label: labels?.[ 3 ] ?? 'Q4 (Apr–Jun)', due: 'due 31 May', amount: q4},
    ].filter((q) => q.amount != null);

    if (quarters.length === 0) return null;

    return (
        <div className="grid grid-cols-2 gap-2 mt-2">
            {quarters.map((q) => (
                <div key={q.key} className="rounded-lg bg-white border border-slate-100 px-3 py-2">
                    <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{q.label}</p>
                    <p className="text-sm font-bold text-slate-800 tabular-nums">{formatCurrency(q.amount)}</p>
                    <p className="text-[10px] text-slate-400">{q.due}</p>
                </div>
            ))}
        </div>
    );
};
// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: CouncilRatesSkeleton
 * Path: frontend/src/components/dashboard/CouncilRatesCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CouncilRatesSkeleton = () => (
    <Card className="border shadow-sm">
        <CardHeader className="pb-3">
            <Skeleton className="h-5 w-48"/>
            <Skeleton className="h-3 w-32 mt-1"/>
        </CardHeader>
        <CardContent className="space-y-3">
            {[...Array(6)].map((_, i) => (
                <div key={i} className="flex justify-between">
                    <Skeleton className="h-4 w-36"/>
                    <Skeleton className="h-4 w-20"/>
                </div>
            ))}
            <div className="flex gap-2 pt-4">
                <Skeleton className="h-9 w-32"/>
                <Skeleton className="h-9 w-40"/>
            </div>
        </CardContent>
    </Card>
);
// ---------------------------------------------------------------------------
// Payment dialog – reused for both full and partial modes
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: PaymentDialog
 * Path: frontend/src/components/dashboard/CouncilRatesCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PaymentDialog = ({open, onOpenChange, mode, totalAmount, unitNumber, onSuccess, api}) => {
    const [amount, setAmount] = useState(
        mode === 'full' ? String(totalAmount ?? '') : ''
    );
    const [reference, setReference] = useState('');
    const [paymentDate, setPaymentDate] = useState(
        new Date().toISOString().slice(0, 10)
    );
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        if (open) {
            setAmount(mode === 'full' ? String(totalAmount ?? '') : '');
            setReference('');
            setPaymentDate(new Date().toISOString().slice(0, 10));
        }
    }, [open, mode, totalAmount]);

    const handleSubmit = useCallback(async () => {
        const parsed = parseFloat(amount);
        if (!parsed || parsed <= 0) {
            toast.error('Please enter a valid payment amount.');
            return;
        }
        setSubmitting(true);
        try {
            await api.post(`/council-rates/${unitNumber}/payments`, {
                amount: parsed,
                payment_type: mode === 'full' ? 'full' : 'partial',
                reference: reference || undefined,
                payment_date: paymentDate,
                financial_year: '2025-26',
            });
            toast.success(
                mode === 'full'
                    ? 'Council rates marked as paid.'
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
    }, [amount, api, mode, onOpenChange, onSuccess, paymentDate, reference, unitNumber]);

    const isFullPay = mode === 'full';
    const title = isFullPay ? 'Mark Council Rates as Paid' : 'Record Partial Payment';
    const description = isFullPay
        ? 'Confirm that the full council rates amount has been paid for this financial year.'
        : 'Record a partial payment toward the council rates for this unit.';

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div className="space-y-1.5">
                        <Label htmlFor="cr-amount">
                            Amount (AUD)
                            {!isFullPay && <span className="text-red-500 ml-0.5">*</span>}
                        </Label>
                        <Input
                            id="cr-amount"
                            type="number"
                            min="0.01"
                            step="0.01"
                            placeholder="0.00"
                            value={amount}
                            onChange={(e) => setAmount(e.target.value)}
                            readOnly={isFullPay}
                            className={isFullPay ? 'bg-slate-50 text-slate-700' : ''}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="cr-date">Payment Date</Label>
                        <Input
                            id="cr-date"
                            type="date"
                            value={paymentDate}
                            onChange={(e) => setPaymentDate(e.target.value)}
                        />
                    </div>

                    <div className="space-y-1.5">
                        <Label htmlFor="cr-ref">Reference / Receipt No. (optional)</Label>
                        <Input
                            id="cr-ref"
                            placeholder="e.g. ACT-2025-XXXX"
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
// Main card component
// ---------------------------------------------------------------------------
/**
 * @generated FunctionHeader
 * Function: CouncilRatesCard
 * Path: frontend/src/components/dashboard/CouncilRatesCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CouncilRatesCard = ({unitNumber: propUnitNumber}) => {
    const {api, hasPermission, selectedBuilding} = useAuth();
    const {activeUnit} = useActiveUnit();

    // Fall back to the sidebar's active unit, not the account default, so a
    // multi-unit owner's switch reaches cards rendered without an explicit prop.
    const unitNumber = propUnitNumber || activeUnit;

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState(null);
    const [fullPayDialog, setFullPayDialog] = useState(false);
    const [partialPayDialog, setPartialPayDialog] = useState(false);
    const [breakdownDialog, setBreakdownDialog] = useState(false);
    const [showQuarterlyRates, setShowQuarterlyRates] = useState(false);

    const canManage = hasPermission('can_manage_finances');

    const fetchRates = useCallback(
        async (forceRefresh = false) => {
            if (!unitNumber) {
                setLoading(false);
                return;
            }
            if (forceRefresh) setRefreshing(true);
            else setLoading(true);
            setError(null);
            try {
                const res = await api.get(`/council-rates/${unitNumber}`);
                setData(res.data);
            } catch (err) {
                const msg =
                    err?.response?.status === 404
                        ? 'No council rates data found for this unit.'
                        : err?.response?.data?.detail || 'Failed to load council rates.';
                setError(msg);
            } finally {
                setLoading(false);
                setRefreshing(false);
            }
        },
        [api, unitNumber]
    );

    useEffect(() => {
        fetchRates(false);
    }, [fetchRates]);

    const handleRefresh = useCallback(() => {
        fetchRates(true);
        toast.info('Refreshing council rates from ACT Revenue...');
    }, [fetchRates]);

    const handlePaymentSuccess = useCallback(() => {
        fetchRates(false);
    }, [fetchRates]);
    /**
     * @generated FunctionHeader
     * Function: getStatusBadge
     * Path: frontend/src/components/dashboard/CouncilRatesCard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusBadge = () => {
        if (!data) return null;
        const s = data.payment_status;
        if (s === 'paid')
            return {label: 'Paid', className: 'bg-emerald-100 text-emerald-700 border-emerald-200'};
        if (s === 'partial')
            return {label: 'Partial', className: 'bg-amber-100 text-amber-700 border-amber-200'};
        return {label: 'Unpaid', className: 'bg-red-100 text-red-700 border-red-200'};
    };

    const badge = getStatusBadge();

    if (loading) return <CouncilRatesSkeleton/>;

    if (!unitNumber) {
        return (
            <Card className="border shadow-sm">
                <CardContent className="p-6 text-center text-slate-500">
                    <Landmark className="mx-auto mb-2 text-slate-300" size={36}/>
                    <p className="text-sm">No unit assigned to your account.</p>
                </CardContent>
            </Card>
        );
    }

    if (error) {
        return (
            <Card className="border shadow-sm border-red-100">
                <CardContent className="p-6">
                    <div className="flex flex-col items-center gap-3 text-center">
                        <XCircle className="text-red-400" size={36}/>
                        <div>
                            <p className="font-medium text-slate-800">Unable to load council rates</p>
                            <p className="text-sm text-slate-500 mt-1">{error}</p>
                        </div>
                        <Button variant="outline" size="sm" onClick={() => fetchRates(false)}>
                            <RefreshCw size={14} className="mr-2"/>
                            Try Again
                        </Button>
                    </div>
                </CardContent>
            </Card>
        );
    }

    if (!data || data.source === 'unavailable') {
        return (
            <Card className="border shadow-sm">
                <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                        <div className="p-2 rounded-xl bg-slate-100 text-slate-600">
                            <Landmark size={18}/>
                        </div>
                        <CardTitle className="text-base font-bold">ACT Council Rates</CardTitle>
                    </div>
                </CardHeader>
                <CardContent className="p-4 pt-0 text-center text-slate-500">
                    <Landmark className="mx-auto mb-2 text-slate-300" size={32}/>
                    <p className="text-sm font-medium text-slate-700">Rates not yet available</p>
                    <p className="text-xs text-slate-500 mt-1">
                        Your strata manager needs to configure the block AUV from the ACT Revenue rates notice.
                    </p>
                    <Button
                        variant="outline"
                        size="sm"
                        className="mt-3"
                        onClick={handleRefresh}
                        disabled={refreshing}
                    >
                        <RefreshCw size={14} className={`mr-2 ${refreshing ? 'animate-spin' : ''}`}/>
                        Retry
                    </Button>
                </CardContent>
            </Card>
        );
    }

    const totalRates =
        data.total_rates ??
        ( ( data.fixed_charge ?? 0 ) +
            ( data.variable_charge ?? data.valuation_charge ?? 0 ) +
            ( data.fesl ?? data.pfesl ?? 0 ) +
            ( data.sfl ?? data.safer_families_levy ?? 0 ) +
            ( data.health_levy ?? 0 ) );

    const balanceDue = Math.max(0, totalRates - ( data.total_paid ?? 0 ));
    const blockAuv = data.block_auv ?? data.auv;
    const hasQuarterlyRates = data.rates_q1 != null;
    const hasLandTax = data.land_tax_total != null && data.land_tax_total > 0;

    return (
        <>
            <Card className="border shadow-sm">
                <CardHeader className="pb-3">
                    <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                            <div className="p-2 rounded-xl bg-slate-100 text-slate-600">
                                <Landmark size={18}/>
                            </div>
                            <div>
                                <CardTitle className="text-base font-bold leading-tight">
                                    ACT Council Rates
                                </CardTitle>
                                <CardDescription className="text-xs mt-0.5">
                                    Unit {unitNumber} — FY {data.financial_year ?? '2025-26'}
                                </CardDescription>
                            </div>
                        </div>

                        <div className="flex items-center gap-2 flex-shrink-0">
                            {badge && (
                                <Badge variant="outline" className={`text-xs font-semibold ${badge.className}`}>
                                    {badge.label === 'Paid' && <CheckCircle size={11} className="mr-1"/>}
                                    {badge.label === 'Unpaid' && <AlertCircle size={11} className="mr-1"/>}
                                    {badge.label === 'Partial' && <Clock size={11} className="mr-1"/>}
                                    {badge.label}
                                </Badge>
                            )}
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 text-slate-500 hover:text-slate-700"
                                        onClick={handleRefresh}
                                        disabled={refreshing}
                                        aria-label="Refresh from ACT Revenue"
                                        data-testid="council-rates-refresh"
                                    >
                                        <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''}/>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>
                                    <p>Refresh from ACT Revenue</p>
                                </TooltipContent>
                            </Tooltip>
                        </div>
                    </div>
                </CardHeader>

                <CardContent className="space-y-4">
                    {/* ── Rates breakdown — clickable for explanation ── */}
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <motion.div
                                className="rounded-xl bg-slate-50 border border-slate-100 px-4 py-1 cursor-pointer hover:bg-slate-100 hover:border-slate-200 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                                onClick={() => setBreakdownDialog(true)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        setBreakdownDialog(true);
                                    }
                                }}
                                tabIndex={0}
                                role="button"
                                aria-label="How ACT Council Rates are calculated (opens dialog)"
                                aria-haspopup="dialog"
                                whileTap={{scale: 0.98}}
                                data-testid="council-rates-breakdown-trigger"
                            >
                                {/* Block AUV — clearly labelled as block-level (strata) */}
                                {blockAuv != null && (
                                    <RatesRow
                                        label={
                                            <span className="flex items-center gap-1">
                    <Building2 size={11} className="text-slate-400"/>
                    Block AUV{data.is_estimated_auv ? ' (default)' : ''}
                  </span>
                                        }
                                        value={formatCurrency(blockAuv)}
                                        tooltip="Average Unimproved Value for the whole block. Every unit shares this figure — each unit's rates are calculated from it using their unit entitlement %."
                                        muted={false}
                                    />
                                )}
                                {data.unit_entitlement_pct != null && (
                                    <RatesRow
                                        label="Unit Entitlement"
                                        value={`${data.unit_entitlement_pct}%`}
                                        tooltip="This unit's share of the total block entitlement (e.g. 161/10,000 = 1.61%). The variable charge = f(Block AUV) × this percentage."
                                        muted
                                    />
                                )}

                                <div className="border-t border-slate-100 mt-1 pt-1">
                                    <RatesRow label="Fixed Charge" value={formatCurrency(data.fixed_charge ?? 0)}/>
                                    <RatesRow
                                        label="Variable Charge"
                                        value={formatCurrency(data.variable_charge ?? data.valuation_charge ?? 0)}
                                        tooltip="Marginal rate applied to Block AUV then multiplied by unit entitlement %."
                                    />
                                    <RatesRow
                                        label="PFESL"
                                        value={formatCurrency(data.fesl ?? data.pfesl ?? 0)}
                                        tooltip="Police, Fire & Emergency Services Levy"
                                    />
                                    <RatesRow
                                        label="Safer Families Levy"
                                        value={formatCurrency(data.sfl ?? data.safer_families_levy ?? 0)}
                                    />
                                    {( data.health_levy ?? 0 ) > 0 && (
                                        <RatesRow
                                            label="Health Levy"
                                            value={formatCurrency(data.health_levy ?? 0)}
                                        />
                                    )}
                                    <RatesRow label="Total Annual Rates" value={formatCurrency(totalRates)} highlight/>
                                </div>

                                <p className="text-[10px] text-slate-400 flex items-center gap-1 pb-2 pt-1">
                                    <Info size={10}/>
                                    Click to learn how ACT council rates are calculated
                                </p>
                            </motion.div>
                        </TooltipTrigger>
                        <TooltipContent>
                            <p>Click to learn how ACT council rates are calculated</p>
                        </TooltipContent>
                    </Tooltip>

                    {/* ── Quarterly rates instalments (collapsible) ── */}
                    {hasQuarterlyRates && (
                        <div className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-3">
                            <button
                                className="flex items-center justify-between w-full text-left focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:outline-none rounded"
                                onClick={() => setShowQuarterlyRates((v) => !v)}
                                aria-expanded={showQuarterlyRates}
                                aria-controls="quarterly-rates-content"
                            >
                <span className="text-xs font-bold text-slate-500 uppercase tracking-widest">
                  Quarterly Instalments
                </span>
                                {showQuarterlyRates ? (
                                    <ChevronUp size={14} className="text-slate-400"/>
                                ) : (
                                    <ChevronDown size={14} className="text-slate-400"/>
                                )}
                            </button>
                            {showQuarterlyRates && (
                                <div id="quarterly-rates-content">
                                    <QuarterlyTable
                                        q1={data.rates_q1}
                                        q2={data.rates_q2}
                                        q3={data.rates_q3}
                                        q4={data.rates_q4}
                                    />
                                </div>
                            )}
                        </div>
                    )}


                    {/* ── Payment summary banners ── */}
                    {data.total_paid != null && data.total_paid > 0 && (
                        <div
                            className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 flex items-center justify-between">
              <span className="text-sm text-emerald-800 font-medium flex items-center gap-1.5">
                <CheckCircle size={14}/>
                Amount Paid
              </span>
                            <span className="text-sm font-semibold text-emerald-800 tabular-nums">
                {formatCurrency(data.total_paid)}
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

                    {/* ── Action buttons ── */}
                    {canManage && data.payment_status !== 'paid' && (
                        <div className="flex flex-wrap gap-2 pt-1">
                            <Button
                                size="sm"
                                className="bg-emerald-600 hover:bg-emerald-700 text-white"
                                onClick={() => setFullPayDialog(true)}
                                data-testid="council-rates-mark-paid"
                            >
                                <CheckCircle size={14} className="mr-1.5"/>
                                Mark as Paid
                            </Button>
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setPartialPayDialog(true)}
                                data-testid="council-rates-partial-pay"
                            >
                                <DollarSign size={14} className="mr-1.5"/>
                                Record Partial Payment
                            </Button>
                        </div>
                    )}

                    {data.payment_status === 'paid' && (
                        <div className="flex items-center gap-2 text-sm text-emerald-700 font-medium pt-1">
                            <CheckCircle size={16} className="text-emerald-500"/>
                            Council rates fully paid for FY {data.financial_year ?? '2025-26'}.
                        </div>
                    )}
                </CardContent>
            </Card>

            <PaymentDialog
                open={fullPayDialog}
                onOpenChange={setFullPayDialog}
                mode="full"
                totalAmount={totalRates}
                unitNumber={unitNumber}
                onSuccess={handlePaymentSuccess}
                api={api}
            />

            <PaymentDialog
                open={partialPayDialog}
                onOpenChange={setPartialPayDialog}
                mode="partial"
                totalAmount={totalRates}
                unitNumber={unitNumber}
                onSuccess={handlePaymentSuccess}
                api={api}
            />

            {/* ACT Council Rates calculation breakdown dialog */}
            <Dialog open={breakdownDialog} onOpenChange={setBreakdownDialog}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Landmark size={18} className="text-slate-600"/>
                            How ACT Council Rates Are Calculated
                        </DialogTitle>
                        <DialogDescription>
                            {selectedBuilding?.name || 'Your Building'}{selectedBuilding?.address ? ` — ${selectedBuilding.address}` : ''}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2 text-sm text-slate-700">
                        {/* Strata AUV explanation */}
                        <div className="rounded-xl bg-blue-50 border border-blue-100 px-4 py-3 space-y-2">
                            <p className="text-xs font-bold text-blue-500 uppercase tracking-widest">
                                Strata Property — Block AUV
                            </p>
                            <p className="text-blue-800 leading-relaxed text-xs">
                                For strata (unit-title) properties, the ACT Government values the{' '}
                                <strong>whole block of land</strong> as a single parcel.
                                Each unit&apos;s rates are based on the block AUV multiplied by its{' '}
                                <strong>unit entitlement percentage</strong> from the unit plan.
                            </p>
                            {blockAuv && (
                                <div
                                    className="flex items-center justify-between rounded-lg bg-white border border-blue-100 px-3 py-2">
                                    <span className="text-slate-600 text-xs">Block AUV (Section 75 Block 4)</span>
                                    <span
                                        className="font-bold text-blue-800 tabular-nums">{formatCurrency(blockAuv)}</span>
                                </div>
                            )}
                            {data.unit_entitlement_pct && (
                                <div
                                    className="flex items-center justify-between rounded-lg bg-white border border-blue-100 px-3 py-2">
                                    <span className="text-slate-600 text-xs">Unit {unitNumber} entitlement</span>
                                    <span className="font-bold text-blue-800">{data.unit_entitlement_pct}%</span>
                                </div>
                            )}
                        </div>

                        {/* Formula */}
                        <div className="rounded-xl bg-slate-50 border border-slate-100 px-4 py-3 space-y-2">
                            <p className="text-xs font-bold text-slate-500 uppercase tracking-widest">
                                Calculation Formula (2025-26)
                            </p>
                            <p className="font-mono text-slate-800 text-xs bg-white border border-slate-200 rounded-lg px-3 py-2 leading-relaxed">
                                Variable = marginal_rate(Block AUV) × Unit Entitlement %{'\n'}
                                Total Rates = $985 Fixed + Variable + $426 PFESL + $60 SFL + $100 Health
                            </p>
                            <p className="text-xs text-slate-500 mt-1">
                                The marginal rate table for residential units applies 0.5481% up to $600k, then higher
                                brackets above that — all calculated on the full block AUV before applying your unit
                                entitlement %.
                            </p>
                        </div>

                        {/* Payment schedule */}
                        <div className="space-y-1">
                            <p className="font-semibold text-slate-800">Quarterly Payment Due Dates</p>
                            <div className="grid grid-cols-2 gap-2">
                                {[
                                    {q: 'Q1 (Jul–Sep)', due: '31 August'},
                                    {q: 'Q2 (Oct–Dec)', due: '30 November'},
                                    {q: 'Q3 (Jan–Mar)', due: '28 February'},
                                    {q: 'Q4 (Apr–Jun)', due: '31 May'},
                                ].map(({q, due}) => (
                                    <div key={q} className="rounded-lg bg-slate-50 border border-slate-100 px-3 py-2">
                                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{q}</p>
                                        <p className="text-xs text-slate-700">Due {due}</p>
                                    </div>
                                ))}
                            </div>
                        </div>


                        <p className="text-xs text-slate-500">
                            <a
                                href="https://www.revenue.act.gov.au/rates/calculating-rates"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-600 hover:underline"
                            >
                                Full calculation details at revenue.act.gov.au
                            </a>
                        </p>
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
};

export default CouncilRatesCard;
