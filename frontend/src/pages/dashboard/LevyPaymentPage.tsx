// @featuretrace:levy — Owner-facing levy payment page: shows current balance, due dates, payment history.
// Layer: frontend
// Data flow: LevyPaymentPage → GET /levy-status/{unit}, /levy-payments → unit_levy_ledger + levy_payments (building-scoped).
// Related: backend/routers/finance.py (_update_ledger_after_payment is the write path)
//           backend/utils/finance_helpers.py (compute_next_estimated_payment)
//           frontend/src/pages/dashboard/MyFinancesPage.jsx
"use client";
import React, {useCallback, useEffect, useState} from 'react';
import {useSearchParams} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Badge} from '../../components/ui/badge';
import {Label} from '../../components/ui/label';
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,} from '../../components/ui/dialog';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from '../../components/ui/select';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from '../../components/ui/table';
import {Tabs, TabsContent, TabsList, TabsTrigger,} from '../../components/ui/tabs';
import {Textarea} from '../../components/ui/textarea';
import {
    AlertTriangle,
    Banknote,
    Building2,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    Clock,
    CreditCard,
    DollarSign,
    Droplets,
    ExternalLink,
    Info,
    Landmark,
    Loader2,
    Plus,
    Receipt,
    Search,
    ShieldCheck,
    ShieldX,
    Trash2,
    TrendingDown,
    TrendingUp,
    XCircle,
    Zap
} from 'lucide-react';
import {formatCurrency, formatDate} from '../../lib/utils';
import {toast} from 'sonner';
import PaymentModal from '../../components/payments/PaymentModal';
/**
 * @generated FunctionHeader
 * Function: statusIcon
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const statusIcon = (status: string) => {
    if (status === 'paid') return <CheckCircle2 className="h-4 w-4 text-green-500"/>;
    if (status === 'pending') return <Clock className="h-4 w-4 text-blue-500"/>;
    if (status === 'overdue') return <XCircle className="h-4 w-4 text-red-500"/>;
    if (status === 'partial') return <AlertTriangle className="h-4 w-4 text-amber-500"/>;
    return <Clock className="h-4 w-4 text-muted-foreground"/>;
};
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const statusBadge = (status: string) => {
    if (status === 'paid') return <Badge className="text-xs bg-green-100 text-green-800 border-green-200">Paid</Badge>;
    if (status === 'pending') return <Badge className="text-xs bg-blue-100 text-blue-800 border-blue-200">Pending
        Confirmation</Badge>;
    if (status === 'overdue') return <Badge variant="destructive" className="text-xs">Overdue</Badge>;
    if (status === 'partial') return <Badge
        className="text-xs bg-amber-100 text-amber-800 border-amber-200">Partial</Badge>;
    if (status === 'unpaid') return <Badge variant="outline"
                                           className="text-xs text-red-700 border-red-200">Unpaid</Badge>;
    return <Badge variant="secondary" className="text-xs">Upcoming</Badge>;
};
/**
 * @generated FunctionHeader
 * Function: periodCardClass
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const periodCardClass = (status: string, hasCarryForward: boolean) => {
    if (status === 'paid') return 'border-green-200 bg-green-50';
    if (status === 'pending') return 'border-blue-200 bg-blue-50';
    if (status === 'overdue') return 'border-red-300 bg-red-50';
    if (status === 'partial') return 'border-amber-300 bg-amber-50';
    if (hasCarryForward) return 'border-amber-200 bg-amber-50/50';
    return 'border-gray-200 bg-gray-50';
};

interface RecordPaymentDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    description: string;
    defaultAmount: number | null | undefined;
    onSubmit: (data: any) => void;
    submitting: boolean;
    paymentMethods?: any[];
}
/**
 * @generated FunctionHeader
 * Function: RecordPaymentDialog
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const RecordPaymentDialog: React.FC<RecordPaymentDialogProps> = ({
                                                                     open,
                                                                     onOpenChange,
                                                                     title,
                                                                     description,
                                                                     defaultAmount,
                                                                     onSubmit,
                                                                     submitting,
                                                                     paymentMethods = [],
                                                                 }) => {
    const [amount, setAmount] = useState('');
    const [method, setMethod] = useState('');
    const [reference, setReference] = useState('');
    const [paymentDate, setPaymentDate] = useState(new Date().toISOString().split('T')[0]);
    const [notes, setNotes] = useState('');
    const [mode, setMode] = useState<'paid' | 'partial' | 'advance'>('paid');

    useEffect(() => {
        if (open) {
            setAmount(defaultAmount != null ? String(defaultAmount.toFixed(2)) : '');
            setMode('paid');
            setReference('');
            setNotes('');
            setPaymentDate(new Date().toISOString().split('T')[0]);
        }
    }, [open, defaultAmount]);

    const parsedAmount = parseFloat(amount) || 0;
    const creditPreview = mode === 'advance' && defaultAmount != null && parsedAmount > defaultAmount
        ? parsedAmount - defaultAmount
        : null;
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        const paymentType = mode === 'paid' ? 'standard' : mode;
        onSubmit({amount: parsedAmount, method, reference, paymentDate, notes, mode, paymentType});
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="space-y-4">
                    <div className="flex gap-1.5">
                        <Button type="button" size="sm" variant={mode === 'paid' ? 'default' : 'outline'}
                                onClick={() => {
                                    setMode('paid');
                                    setAmount(defaultAmount != null ? String(defaultAmount.toFixed(2)) : '');
                                }}
                                className="flex-1"
                        >
                            <CheckCircle2 className="mr-1.5 h-3.5 w-3.5"/>Full
                        </Button>
                        <Button type="button" size="sm" variant={mode === 'partial' ? 'default' : 'outline'}
                                onClick={() => {
                                    setMode('partial');
                                    setAmount('');
                                }}
                                className="flex-1"
                        >
                            <AlertTriangle className="mr-1.5 h-3.5 w-3.5"/>Partial
                        </Button>
                        <Button type="button" size="sm" variant={mode === 'advance' ? 'default' : 'outline'}
                                onClick={() => {
                                    setMode('advance');
                                    setAmount('');
                                }}
                                className="flex-1"
                        >
                            <TrendingUp className="mr-1.5 h-3.5 w-3.5"/>Advance
                        </Button>
                    </div>

                    {mode === 'advance' && (
                        <p className="text-xs text-blue-700 bg-blue-50 border border-blue-100 rounded p-2.5">
                            <TrendingUp className="inline h-3 w-3 mr-1"/>
                            Enter any amount — including more than the quarterly levy. The excess will be held as a
                            <strong> credit balance</strong> and automatically applied to your next quarter.
                        </p>
                    )}

                    <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-1.5">
                            <Label>Amount Paid (AUD)</Label>
                            <Input
                                type="number" step="0.01" min="0.01"
                                value={amount}
                                onChange={e => setAmount((e.target as HTMLInputElement).value)}
                                readOnly={mode === 'paid' && defaultAmount != null}
                                className={mode === 'paid' && defaultAmount != null ? 'bg-muted' : ''}
                                required
                            />
                            {creditPreview != null && creditPreview > 0 && (
                                <p className="text-xs text-green-700 font-medium">
                                    <TrendingUp className="inline h-3 w-3 mr-0.5"/>
                                    {formatCurrency(creditPreview)} will be held as credit
                                </p>
                            )}
                        </div>
                        <div className="space-y-1.5">
                            <Label>Payment Date</Label>
                            <Input type="date" value={paymentDate}
                                   onChange={e => setPaymentDate((e.target as HTMLInputElement).value)} required/>
                        </div>
                    </div>

                    {paymentMethods.length > 0 && (
                        <div className="space-y-1.5">
                            <Label>Payment Method</Label>
                            <Select value={method} onValueChange={setMethod}>
                                <SelectTrigger><SelectValue placeholder="Select method (optional)"/></SelectTrigger>
                                <SelectContent>
                                    {paymentMethods.filter(m => m.enabled).map(m => (
                                        <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    )}

                    <div className="space-y-1.5">
                        <Label>Reference / Receipt Number</Label>
                        <Input value={reference} onChange={e => setReference((e.target as HTMLInputElement).value)}
                               placeholder="DEFT/BPAY reference, bank receipt no."/>
                    </div>

                    <div className="space-y-1.5">
                        <Label>Notes (optional)</Label>
                        <Input value={notes} onChange={e => setNotes((e.target as HTMLInputElement).value)}
                               placeholder="Any additional notes"/>
                    </div>

                    <p className="text-xs text-muted-foreground bg-blue-50 border border-blue-100 rounded p-2.5">
                        <Info className="inline h-3 w-3 mr-1"/>
                        This records that you have paid outside the app (e.g. via DEFT or BPAY).
                        The strata management team will verify and confirm during the next reconciliation.
                    </p>

                    <div className="flex gap-2 pt-1">
                        <Button type="button" variant="outline" className="flex-1"
                                onClick={() => onOpenChange(false)}>Cancel</Button>
                        <Button type="submit" className="flex-1" disabled={submitting || !amount}>
                            {submitting ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin"/> :
                                <Receipt className="mr-1.5 h-4 w-4"/>}
                            Record {mode === 'paid' ? 'Full' : mode === 'partial' ? 'Partial' : 'Advance'} Payment
                        </Button>
                    </div>
                </form>
            </DialogContent>
        </Dialog>
    );
};
/**
 * @generated FunctionHeader
 * Function: dueDayForMonth
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function dueDayForMonth(year: number, month1based: number, dayType: string, customDay: number | null, customDates: Record<string, number>) {
    const lastDay = new Date(year, month1based, 0).getDate();
    if (dayType === 'first') return 1;
    if (dayType === 'middle') return 15;
    if (dayType === 'custom') {
        const perMonth = customDates?.[String(month1based)];
        const day = perMonth != null ? perMonth : (customDay || lastDay);
        return Math.min(day, lastDay);
    }
    return lastDay;
}
/**
 * @generated FunctionHeader
 * Function: LevyDueDatesCard
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const LevyDueDatesCard: React.FC = () => {
    const {api} = useAuth();
    const [dueDates, setDueDates] = useState<string[]>([]);
    const [frequency, setFrequency] = useState('quarterly');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchLevySchedule
         * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchLevySchedule = async () => {
            try {
                const response = await api.get('/settings');
                const settings = response.data;
                const levyFrequency = settings.levy_collection_frequency || 'quarterly';
                const levyMonths = settings.levy_due_months || [3, 6, 9, 12];
                const dayType = settings.levy_due_day_type || 'last';
                const customDay = settings.levy_due_day || null;
                const customDates = settings.levy_due_custom_dates || {};
                const monthNames = ["", "January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"];
                const year = new Date().getFullYear();
                const dates = levyMonths.map((month: number) => {
                    const day = dueDayForMonth(year, month, dayType, customDay, customDates);
                    return `${day} ${monthNames[month]}`;
                });
                setDueDates(dates);
                setFrequency(levyFrequency);
            } catch {
                setDueDates(['31 March', '1 June', '1 September', '1 December']);
                setFrequency('quarterly');
            } finally {
                setLoading(false);
            }
        };
        fetchLevySchedule();
    }, [api]);

    const frequencyLabels: Record<string, string> = {
        monthly: 'Monthly',
        quarterly: 'Quarterly',
        half_yearly: 'Half-Yearly',
        yearly: 'Yearly'
    };

    if (loading) return (
        <Card className="card-dashboard">
            <CardContent className="p-6">
                <div className="skeleton h-32 w-full"/>
            </CardContent>
        </Card>
    );

    return (
        <Card className="card-dashboard">
            <CardHeader>
                <CardTitle className="text-lg">{frequencyLabels[frequency]} Payment Due Dates</CardTitle>
                <CardDescription>Make sure to pay by these dates to avoid late fees and interest
                    charges</CardDescription>
            </CardHeader>
            <CardContent>
                <div
                    className={`grid gap-4 ${dueDates.length === 12 ? 'grid-cols-3 md:grid-cols-4 lg:grid-cols-6' : dueDates.length === 4 ? 'grid-cols-2 md:grid-cols-4' : dueDates.length === 2 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                    {dueDates.map((d, i) => (
                        <div key={i} className="p-4 bg-muted/50 rounded-lg text-center">
                            <p className="font-semibold text-lg">
                                {frequency === 'quarterly' ? `Q${i + 1}` : frequency === 'half_yearly' ? `H${i + 1}` : frequency === 'monthly' ? d.split(' ')[1]?.slice(0, 3) : 'Annual'}
                            </p>
                            <p className="text-sm text-primary font-medium">{d}</p>
                        </div>
                    ))}
                </div>
            </CardContent>
        </Card>
    );
};
/**
 * @generated FunctionHeader
 * Function: getActFinancialYear
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function getActFinancialYear() {
    const now = new Date();
    const m = now.getMonth();
    const y = now.getFullYear();
    const fyStart = m >= 6 ? y : y - 1;
    return `${fyStart}-${String(fyStart + 1).slice(2)}`;
}

interface CouncilRatesSectionProps {
    unitNumber: string;
    paymentMethods: any[];
}
/**
 * @generated FunctionHeader
 * Function: CouncilRatesSection
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CouncilRatesSection: React.FC<CouncilRatesSectionProps> = ({unitNumber, paymentMethods}) => {
    const {api, selectedBuilding} = useAuth() as any;
    const [rates, setRates] = useState<any>(null);
    const [ratePayments, setRatePayments] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [infoOpen, setInfoOpen] = useState(false);
    const [payOpen, setPayOpen] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);

    const currentFinancialYear = getActFinancialYear();

    const fetchRates = useCallback(async () => {
        if (!unitNumber) return;
        setLoading(true);
        try {
            const [ratesRes, paymentsRes] = await Promise.allSettled([
                api.get(`/council-rates/${unitNumber}?financial_year=${currentFinancialYear}`),
                api.get(`/council-rates/${unitNumber}/payments?financial_year=${currentFinancialYear}`),
            ]);
            if (ratesRes.status === 'fulfilled') setRates(ratesRes.value.data);
            if (paymentsRes.status === 'fulfilled') setRatePayments(paymentsRes.value.data || []);
        } catch (err: any) {
            if (err.response?.status !== 404) {
                toast.error('Could not load council rates');
            }
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber, currentFinancialYear]);

    useEffect(() => {
        fetchRates();
    }, [fetchRates]);
    /**
     * @generated FunctionHeader
     * Function: handleRecordPayment
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRecordPayment = async ({amount, reference, paymentDate, mode}: any) => {
        setSubmitting(true);
        try {
            await api.post(`/council-rates/${unitNumber}/payments`, {
                financial_year: currentFinancialYear,
                payment_date: paymentDate,
                amount,
                payment_type: mode === 'paid' ? 'full' : 'partial',
                notes: [reference && `Ref: ${reference}`].filter(Boolean).join(' — ') || undefined,
            });
            toast.success('Council rates payment recorded');
            setPayOpen(null);
            fetchRates();
        } catch (err: any) {
            toast.error(err.response?.data?.detail || 'Failed to record payment');
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) return (
        <div className="animate-pulse h-24 bg-muted rounded-lg"/>
    );

    const quarterly = rates?.total_rates ? rates.total_rates / 4 : null;
    const totalPaid = rates?.total_paid ?? 0;
    const quartersPaidCount = quarterly && quarterly > 0 ? Math.min(4, Math.floor(totalPaid / quarterly + 0.01)) : 0;

    return (
        <div className="space-y-3">
            <button
                type="button"
                onClick={() => setExpanded(v => !v)}
                className="w-full flex items-center justify-between p-4 rounded-lg border bg-slate-50 hover:bg-slate-100 transition-colors text-left"
            >
                <div className="flex items-center gap-3">
                    <Landmark className="h-5 w-5 text-slate-600"/>
                    <div>
                        <p className="font-semibold text-sm">Council Rates & Land Tax</p>
                        <p className="text-xs text-muted-foreground">
                            {rates ? `${currentFinancialYear}: ${formatCurrency(rates.total_rates || 0)} annual` : 'ACT Revenue Office'}
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {rates?.total_rates && (
                        <Badge variant="outline" className="text-xs">{formatCurrency(quarterly || 0)}/quarter</Badge>
                    )}
                    {expanded ? <ChevronUp className="h-4 w-4 text-muted-foreground"/> :
                        <ChevronDown className="h-4 w-4 text-muted-foreground"/>}
                </div>
            </button>

            {expanded && (
                <div className="pl-2 space-y-3">
                    <button
                        type="button"
                        onClick={() => setInfoOpen(true)}
                        className="w-full text-left p-3 rounded-lg border border-dashed border-blue-200 bg-blue-50 hover:bg-blue-100 transition-colors"
                    >
                        <div className="flex items-start gap-2">
                            <Info className="h-4 w-4 text-blue-600 mt-0.5 shrink-0"/>
                            <div className="text-xs text-blue-700">
                                <p className="font-medium">How are council rates calculated?</p>
                                <p className="mt-0.5">Click to see the ACT Revenue Office rate formula and charge
                                    breakdown for your unit.</p>
                            </div>
                        </div>
                    </button>

                    {rates ? (
                        <>
                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                                {rates.auv && (
                                    <div className="p-3 bg-white rounded border text-center">
                                        <p className="text-xs text-muted-foreground">AUV</p>
                                        <p className="font-semibold">{formatCurrency(rates.auv)}</p>
                                    </div>
                                )}
                                {rates.fixed_charge && (
                                    <div className="p-3 bg-white rounded border text-center">
                                        <p className="text-xs text-muted-foreground">Fixed Charge</p>
                                        <p className="font-semibold">{formatCurrency(rates.fixed_charge)}</p>
                                    </div>
                                )}
                                {rates.total_rates && (
                                    <div className="p-3 bg-white rounded border text-center">
                                        <p className="text-xs text-muted-foreground">Annual Total</p>
                                        <p className="font-semibold text-primary">{formatCurrency(rates.total_rates)}</p>
                                    </div>
                                )}
                                {quarterly && (
                                    <div className="p-3 bg-white rounded border text-center">
                                        <p className="text-xs text-muted-foreground">Per Quarter</p>
                                        <p className="font-semibold text-blue-600">{formatCurrency(quarterly)}</p>
                                    </div>
                                )}
                            </div>

                            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                {[
                                    {q: 'Q1', due: '31 Aug', label: 'Jul–Sep'},
                                    {q: 'Q2', due: '30 Nov', label: 'Oct–Dec'},
                                    {q: 'Q3', due: '28 Feb', label: 'Jan–Mar'},
                                    {q: 'Q4', due: '31 May', label: 'Apr–Jun'},
                                ].map(({q, due, label}, i) => {
                                    const isPaid = i < quartersPaidCount;
                                    return (
                                        <div key={q}
                                             className={`p-3 rounded-lg border-2 space-y-2 ${isPaid ? 'border-green-200 bg-green-50' : 'border-slate-200 bg-slate-50'}`}>
                                            <div className="flex items-center justify-between">
                                                <span className="font-semibold text-sm">{q}</span>
                                                <span className="text-xs text-muted-foreground">{due}</span>
                                            </div>
                                            <p className="text-xs text-muted-foreground">{label}</p>
                                            <p className="text-sm font-bold">{formatCurrency(quarterly || 0)}</p>
                                            {isPaid ? (
                                                <Badge
                                                    className="text-xs bg-green-100 text-green-800 border-green-200 w-full justify-center">
                                                    <CheckCircle2 className="mr-1 h-3 w-3"/>Paid
                                                </Badge>
                                            ) : (
                                                <Button
                                                    size="sm" variant="outline"
                                                    className="w-full text-xs h-7"
                                                    onClick={() => setPayOpen(`cr-${q}`)}
                                                >
                                                    <Receipt className="mr-1 h-3 w-3"/>Record Payment
                                                </Button>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                            {ratePayments.length > 0 && (
                                <div className="mt-2 space-y-1">
                                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Recorded
                                        Payments</p>
                                    {ratePayments.map((p, idx) => (
                                        <div key={p.id || idx}
                                             className="flex items-center justify-between text-xs p-2 rounded bg-white border">
                                            <span className="text-muted-foreground">{p.payment_date || '—'}</span>
                                            <span className="font-medium">{formatCurrency(p.amount)}</span>
                                            <Badge variant="outline"
                                                   className="capitalize text-[10px]">{p.payment_type || 'payment'}</Badge>
                                        </div>
                                    ))}
                                </div>
                            )}

                            {rates.land_tax_total > 0 && (
                                <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 space-y-3">
                                    <div className="flex items-center justify-between">
                                        <p className="font-semibold text-sm text-blue-900 flex items-center gap-2">
                                            <Receipt className="h-4 w-4 text-blue-600"/>
                                            Land Tax
                                        </p>
                                        <Badge variant="outline"
                                               className="text-xs text-blue-700 border-blue-300 bg-white">
                                            {formatCurrency(rates.land_tax_total)} / year
                                        </Badge>
                                    </div>

                                    {rates.land_tax_note && (
                                        <div
                                            className="flex items-start gap-2 p-2.5 rounded-md bg-white border border-blue-100 text-xs text-blue-800">
                                            <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-blue-500"/>
                                            <p>{rates.land_tax_note}</p>
                                        </div>
                                    )}

                                    <div className="grid grid-cols-2 gap-3 text-sm">
                                        <div className="p-3 bg-white rounded-lg border border-blue-100 text-center">
                                            <p className="text-xs text-muted-foreground">Fixed Charge</p>
                                            <p className="font-semibold tabular-nums">
                                                {formatCurrency(rates.land_tax_fixed ?? 1693)}
                                            </p>
                                        </div>
                                        <div className="p-3 bg-white rounded-lg border border-blue-100 text-center">
                                            <p className="text-xs text-muted-foreground">Variable Charge</p>
                                            <p className="font-semibold tabular-nums">
                                                {formatCurrency(rates.land_tax_variable ?? 0)}
                                            </p>
                                        </div>
                                    </div>

                                    {rates.land_tax_q1 != null && (
                                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                                            {[
                                                {q: 'Q1', amount: rates.land_tax_q1, due: '31 Aug'},
                                                {q: 'Q2', amount: rates.land_tax_q2, due: '30 Nov'},
                                                {q: 'Q3', amount: rates.land_tax_q3, due: '28 Feb'},
                                                {q: 'Q4', amount: rates.land_tax_q4, due: '31 May'},
                                            ].map(({q, amount, due}) => (
                                                <div key={q}
                                                     className="p-2.5 rounded-lg border border-blue-100 bg-white text-center">
                                                    <p className="text-[10px] font-bold text-blue-400 uppercase">{q}</p>
                                                    <p className="text-sm font-bold text-blue-800 tabular-nums">
                                                        {formatCurrency(amount)}
                                                    </p>
                                                    <p className="text-[10px] text-slate-400">due {due}</p>
                                                </div>
                                            ))}
                                        </div>
                                    )}

                                    <p className="text-[11px] text-blue-600">
                                        Land tax is assessed on 1 Jul, 1 Oct, 1 Jan, 1 Apr.
                                        Owner-occupied (PPR) properties are exempt.{' '}
                                        <a
                                            href="https://www.revenue.act.gov.au/land-tax"
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="underline"
                                        >
                                            Learn more
                                        </a>
                                    </p>
                                </div>
                            )}

                            {!rates.land_tax_total && rates.land_tax_note && (
                                <div
                                    className="flex items-start gap-2 p-3 rounded-lg border bg-orange-50 border-orange-200 text-orange-800 text-xs">
                                    <Info className="h-3.5 w-3.5 mt-0.5 shrink-0"/>
                                    <p>{rates.land_tax_note}</p>
                                </div>
                            )}
                        </>
                    ) : (
                        <p className="text-sm text-muted-foreground p-3 bg-muted rounded text-center">
                            Council rates data not yet available. Quarterly estimates: ≈ $600–$900 depending on AUV.
                        </p>
                    )}
                </div>
            )}

            <Dialog open={infoOpen} onOpenChange={setInfoOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2"><Landmark className="h-5 w-5"/>How ACT Council
                            Rates Are Calculated</DialogTitle>
                        <DialogDescription>ACT Revenue Office formula for {selectedBuilding?.name || 'your building'} units</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 text-sm">
                        <div className="p-3 bg-slate-50 rounded-lg border font-mono text-xs space-y-1">
                            <p className="font-semibold text-slate-700 font-sans text-sm mb-2">Rate Formula:</p>
                            <p>Total = Fixed Charge</p>
                            <p className="pl-4">+ (AUV × General Rates Factor)</p>
                            <p className="pl-4">+ PFESL (Planning & Fire Emergency Services Levy)</p>
                            <p className="pl-4">+ Safer Families Levy</p>
                        </div>
                        <ul className="space-y-2 text-muted-foreground">
                            <li><span className="font-medium text-foreground">AUV</span> — Average Unimproved Value,
                                assessed annually by the ACT Government based on land valuations.
                            </li>
                            <li><span className="font-medium text-foreground">Fixed Charge</span> — A flat amount
                                charged to every residential property in the ACT.
                            </li>
                            <li><span className="font-medium text-foreground">PFESL</span> — Planning and Fire Emergency
                                Services Levy — funds territory-wide emergency services.
                            </li>
                            <li><span className="font-medium text-foreground">Safer Families Levy</span> — A small levy
                                supporting domestic violence services in the ACT.
                            </li>
                        </ul>
                        <div className="p-3 bg-blue-50 border border-blue-100 rounded text-blue-800 text-xs">
                            <p className="font-medium mb-1">Payment Schedule</p>
                            <p>Council rates are due quarterly: Q1 31 Aug · Q2 30 Nov · Q3 28 Feb · Q4 31 May</p>
                        </div>
                        {rates?.land_tax_applicable && (
                            <div className="p-3 bg-orange-50 border border-orange-100 rounded text-orange-800 text-xs">
                                <p className="font-medium mb-1">Land Tax</p>
                                <p>Land tax applies to this unit because it is not owner-occupied (investment/rental
                                    property). Land tax is assessed separately by the ACT Revenue Office.</p>
                            </div>
                        )}
                        <a
                            href="https://www.revenue.act.gov.au/rates-and-land-tax/council-rates"
                            target="_blank" rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-primary hover:underline text-xs"
                        >
                            <ExternalLink className="h-3 w-3"/>Learn more at revenue.act.gov.au
                        </a>
                    </div>
                </DialogContent>
            </Dialog>

            {['Q1', 'Q2', 'Q3', 'Q4'].map(q => (
                <RecordPaymentDialog
                    key={q}
                    open={payOpen === `cr-${q}`}
                    onOpenChange={open => !open && setPayOpen(null)}
                    title={`Record Council Rates Payment — ${q}`}
                    description={`Record that you paid ${formatCurrency(quarterly || 0)} (Q${q.slice(1)} council rates) outside the app.`}
                    defaultAmount={quarterly}
                    onSubmit={handleRecordPayment}
                    submitting={submitting}
                    paymentMethods={paymentMethods}
                />
            ))}
        </div>
    );
};

interface WaterBillsSectionProps {
    unitNumber: string;
    paymentMethods: any[];
}
/**
 * @generated FunctionHeader
 * Function: WaterBillsSection
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const WaterBillsSection: React.FC<WaterBillsSectionProps> = ({unitNumber, paymentMethods}) => {
    const {api, selectedBuilding} = useAuth() as any;
    const [bills, setBills] = useState<any[]>([]);
    const [estimates, setEstimates] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [infoOpen, setInfoOpen] = useState(false);
    const [payOpen, setPayOpen] = useState<string | null>(null);
    const [payingBill, setPayingBill] = useState<any>(null);
    const [submitting, setSubmitting] = useState(false);
    const currentYear = new Date().getFullYear();

    const fetchData = useCallback(async () => {
        if (!unitNumber) return;
        setLoading(true);
        try {
            const [billsRes, estRes] = await Promise.all([
                api.get(`/water-bills/${unitNumber}?limit=8`),
                api.get(`/water-bills/quarterly-estimates?year=${currentYear}`),
            ]);
            setBills(billsRes.data || []);
            setEstimates(estRes.data || null);
        } catch (err: any) {
            // ignore
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber, currentYear]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    /**
     * @generated FunctionHeader
     * Function: handleMarkPaid
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleMarkPaid = async ({amount, reference, paymentDate, mode}: any, billId: string) => {
        setSubmitting(true);
        try {
            if (mode === 'paid') {
                await api.patch(`/water-bills/${billId}/mark-paid`, {
                    payment_date: paymentDate,
                    payment_reference: reference || undefined,
                });
            } else {
                await api.patch(`/water-bills/${billId}/mark-partial`, {
                    amount_paid: amount,
                    payment_date: paymentDate,
                    notes: [reference && `Ref: ${reference}`].filter(Boolean).join(' — ') || undefined,
                });
            }
            toast.success('Water bill payment recorded');
            setPayOpen(null);
            setPayingBill(null);
            fetchData();
        } catch (err: any) {
            toast.error(err.response?.data?.detail || 'Failed to record payment');
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) return <div className="animate-pulse h-24 bg-muted rounded-lg"/>;

    const currentBill = bills[0] || null;
    // Served, not typed. This page was already calling /water-bills/quarterly-estimates
    // for the per-quarter totals below while keeping its own copy of the two rates that
    // produce them — so the rates and the totals could drift apart on the same screen.
    const waterDaily = estimates?.water_daily_rate;
    const sewerDaily = estimates?.sewer_daily_rate;
    const dailyTotal =
        typeof waterDaily === 'number' && typeof sewerDaily === 'number' ? waterDaily + sewerDaily : null;
    const rate = (v: number | undefined | null) =>
        typeof v === 'number' && Number.isFinite(v) ? `$${v.toFixed(4)} / day` : '—';
    // The endpoint already knows each quarter's real day count. Multiplying the daily
    // rate by a nominal 91 here produced a fifth, slightly different number for the
    // same thing.
    const currentQuarterEstimate =
        estimates?.estimates?.find(
            (q: any) => q.quarter === `Q${Math.floor(new Date().getMonth() / 3) + 1}`,
        ) ?? null;

    return (
        <div className="space-y-3">
            <button
                type="button"
                onClick={() => setExpanded(v => !v)}
                className="w-full flex items-center justify-between p-4 rounded-lg border bg-cyan-50 hover:bg-cyan-100 transition-colors text-left"
            >
                <div className="flex items-center gap-3">
                    <Droplets className="h-5 w-5 text-cyan-600"/>
                    <div>
                        <p className="font-semibold text-sm">Icon Water Bills</p>
                        <p className="text-xs text-muted-foreground">
                            {currentBill
                                ? `Latest: ${currentBill.quarter} — ${formatCurrency(currentBill.amount)}`
                                : currentQuarterEstimate
                                    ? `Est. ${formatCurrency(currentQuarterEstimate.supply_total)} supply for ${currentQuarterEstimate.quarter}`
                                    : 'Supply estimate unavailable'}
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {currentBill && statusBadge(currentBill.status)}
                    {expanded ? <ChevronUp className="h-4 w-4 text-muted-foreground"/> :
                        <ChevronDown className="h-4 w-4 text-muted-foreground"/>}
                </div>
            </button>

            {expanded && (
                <div className="pl-2 space-y-3">
                    <button
                        type="button"
                        onClick={() => setInfoOpen(true)}
                        className="w-full text-left p-3 rounded-lg border border-dashed border-cyan-200 bg-cyan-50 hover:bg-cyan-100 transition-colors"
                    >
                        <div className="flex items-start gap-2">
                            <Info className="h-4 w-4 text-cyan-600 mt-0.5 shrink-0"/>
                            <div className="text-xs text-cyan-700">
                                <p className="font-medium">How are water charges calculated?</p>
                                <p className="mt-0.5">Icon Water charges fixed daily supply rates + per-kL usage. Click
                                    for the full breakdown.</p>
                            </div>
                        </div>
                    </button>

                    {estimates?.estimates && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            {estimates.estimates.map((q: any) => {
                                const matchingBill = bills.find(b => b.quarter === `${q.quarter} ${currentYear}`);
                                return (
                                    <div key={q.quarter}
                                         className={`p-3 rounded-lg border-2 space-y-1.5 ${matchingBill ? periodCardClass(matchingBill.status, false) : 'border-cyan-200 bg-cyan-50/50'}`}>
                                        <div className="flex items-center justify-between">
                                            <span className="font-semibold text-sm">{q.quarter}</span>
                                            <span className="text-xs text-muted-foreground">{q.label}</span>
                                        </div>
                                        {matchingBill ? (
                                            <>
                                                <p className="text-sm font-bold">{formatCurrency(matchingBill.amount)}</p>
                                                <p className="text-xs text-muted-foreground">Supply: {formatCurrency(matchingBill.supply_breakdown?.supply_total || q.supply_total)}</p>
                                                {statusBadge(matchingBill.status)}
                                                {matchingBill.status !== 'paid' && (
                                                    <Button
                                                        size="sm" variant="outline"
                                                        className="w-full text-xs h-7 mt-1"
                                                        onClick={() => {
                                                            setPayOpen(matchingBill.id);
                                                            setPayingBill(matchingBill);
                                                        }}
                                                    >
                                                        <Receipt className="mr-1 h-3 w-3"/>Record Payment
                                                    </Button>
                                                )}
                                            </>
                                        ) : (
                                            <>
                                                <p className="text-xs text-muted-foreground">Est. supply only</p>
                                                <p className="text-sm font-bold text-muted-foreground">~{formatCurrency(q.supply_total)}</p>
                                                <Badge variant="secondary" className="text-xs">No bill yet</Badge>
                                            </>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {bills.length > 0 && (
                        <div className="space-y-2">
                            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Recent
                                Bills</p>
                            {bills.slice(0, 4).map(bill => (
                                <div key={bill.id}
                                     className="flex items-center justify-between p-3 rounded-lg border bg-white text-sm">
                                    <div>
                                        <p className="font-medium">{bill.quarter}</p>
                                        <p className="text-xs text-muted-foreground">{bill.billing_period_start} – {bill.billing_period_end}</p>
                                    </div>
                                    <div className="text-right space-y-1">
                                        <p className="font-bold">{formatCurrency(bill.amount)}</p>
                                        {statusBadge(bill.status)}
                                    </div>
                                    {bill.status !== 'paid' && (
                                        <Button
                                            size="sm" variant="outline"
                                            className="ml-3 text-xs h-7"
                                            onClick={() => {
                                                setPayOpen(bill.id);
                                                setPayingBill(bill);
                                            }}
                                        >
                                            <Receipt className="mr-1 h-3 w-3"/>Pay
                                        </Button>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}

                    {bills.length === 0 && (
                        <p className="text-sm text-muted-foreground p-3 bg-muted rounded text-center">
                            No water bills entered yet. Your strata manager will add bills when they arrive from Icon
                            Water.
                        </p>
                    )}
                </div>
            )}

            <Dialog open={infoOpen} onOpenChange={setInfoOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2"><Droplets className="h-5 w-5 text-cyan-600"/>How
                            Icon Water Charges Are Calculated</DialogTitle>
                        <DialogDescription>ACT regulated daily supply rates — identical for every unit in the
                            building</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 text-sm">
                        <div className="p-3 bg-cyan-50 rounded-lg border border-cyan-100 space-y-2">
                            <p className="font-semibold text-cyan-900 mb-1">
                                Fixed Daily Supply Charges
                                {estimates?.rate_schedule?.schedule_label
                                    ? ` (${estimates.rate_schedule.schedule_label})`
                                    : ''}
                            </p>
                            <div className="grid grid-cols-2 gap-2 text-xs">
                                <div className="flex justify-between"><span>Water Supply:</span><span
                                    className="font-mono font-bold">{rate(waterDaily)}</span></div>
                                <div className="flex justify-between"><span>Sewerage Supply:</span><span
                                    className="font-mono font-bold">{rate(sewerDaily)}</span></div>
                                <div className="flex justify-between font-medium col-span-2 border-t pt-1"><span>Total Daily:</span><span
                                    className="font-mono font-bold">{rate(dailyTotal)}</span></div>
                            </div>
                        </div>
                        <div className="space-y-1.5 text-xs text-muted-foreground">
                            {estimates?.estimates?.map((q: any) => (
                                <div key={q.quarter} className="flex justify-between px-2">
                                    <span>{q.quarter} ({q.label}, {q.days} days):</span>
                                    <span className="font-mono">{formatCurrency(q.supply_total)} supply only</span>
                                </div>
                            ))}
                        </div>
                        <div className="p-3 bg-amber-50 border border-amber-100 rounded text-amber-800 text-xs">
                            <p className="font-medium mb-1">Usage Charges</p>
                            <p>Water usage (per kL) is charged separately and varies based on your quarterly meter
                                reading. These appear on your individual bill from Icon Water.</p>
                        </div>
                        <div className="p-3 bg-blue-50 border border-blue-100 rounded text-blue-800 text-xs">
                            <p className="font-medium mb-1">Same for All 87 Units</p>
                            <p>Supply charges are regulated by the ACT Government and apply equally to every residential
                                unit at {selectedBuilding?.name || 'this building'} — they do not vary by unit size or entitlement.</p>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>

            {payingBill && (
                <RecordPaymentDialog
                    open={!!payOpen}
                    onOpenChange={open => {
                        if (!open) {
                            setPayOpen(null);
                            setPayingBill(null);
                        }
                    }}
                    title={`Record Water Bill Payment — ${payingBill.quarter}`}
                    description={`Record that you paid your Icon Water bill of ${formatCurrency(payingBill.amount)} outside the app.`}
                    defaultAmount={payingBill.balance_due}
                    onSubmit={(data) => handleMarkPaid(data, payingBill.id)}
                    submitting={submitting}
                    paymentMethods={paymentMethods}
                />
            )}
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: LevyPaymentPage
 * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const LevyPaymentPage: React.FC = () => {
    const {api, user, hasPermission, isOwner, isECMember, selectedBuilding} = useAuth();
    const {activeUnit} = useActiveUnit();
    const searchParams = useSearchParams();
    const [paymentMethods, setPaymentMethods] = useState<any[]>([]);
    const [payments, setPayments] = useState<any[]>([]);
    const [levyStatus, setLevyStatus] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [unitSearch, setUnitSearch] = useState('');
    const [activeTab, setActiveTab] = useState(searchParams?.get('tab') || 'overview');
    const [isPaymentModalOpen, setIsPaymentModalOpen] = useState(false);
    const [paymentData, setPaymentData] = useState<any>(null);
    const [quarterPayOpen, setQuarterPayOpen] = useState<string | null>(null);
    const [quarterPayData, setQuarterPayData] = useState<any>(null);
    const [quarterSubmitting, setQuarterSubmitting] = useState(false);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [rejectDialog, setRejectDialog] = useState<{ open: boolean, payment: any }>({open: false, payment: null});
    const [rejectionReason, setRejectionReason] = useState('');
    const [deleteConfirm, setDeleteConfirm] = useState<{ open: boolean, payment: any }>({open: false, payment: null});

    const canManage = hasPermission('can_manage_finances');
    const canSearchAll = ['super_admin'].includes(user?.role || '');
    const currentYear = new Date().getFullYear();

    const [formData, setFormData] = useState({
        unit_number: '', amount: '', payment_method: '', payment_reference: '',
        quarter: 'Q1', year: String(currentYear), notes: ''
    });

    const fetchData = useCallback(async () => {
        try {
            const [methodsResult, paymentsResult] = await Promise.allSettled([
                api.get('/payment-methods'),
                api.get('/levy-payments')
            ]);
            if (methodsResult.status === 'fulfilled') {
                setPaymentMethods(methodsResult.value.data?.methods || []);
            }
            if (paymentsResult.status === 'fulfilled') {
                setPayments(paymentsResult.value.data || []);
            }
        } catch {
            // ignore
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchLevyStatus = useCallback(async (unitNumber: string) => {
        if (!unitNumber) return;
        try {
            const response = await api.get(`/levy-status/${unitNumber}`);
            if (!response.data?.error) {
                setLevyStatus(response.data);
            } else {
                toast.error('Unit not found');
                setLevyStatus(null);
            }
        } catch {
            toast.error('Failed to fetch levy status');
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    // Auto-load levy status for unit owners and EC members who own a unit.
    // isOwner() from AuthContext correctly returns true for ec_member/chairman with unit_number.
    // Seeded from the sidebar's active unit, not the account default, so switching
    // units re-runs the lookup instead of leaving the owner on the old unit's levies.
    // The search Input below stays editable — this only changes what it starts on.
    useEffect(() => {
        if (isOwner() && activeUnit) {
            setUnitSearch(activeUnit);
            fetchLevyStatus(activeUnit);
        }
    }, [activeUnit, fetchLevyStatus, isOwner]);

    // Auto-navigate owners and EC members to the Levy Status tab on first load
    // so their personal levy position is immediately visible (not the payment methods overview).
    const autoTabRef = React.useRef(false);
    useEffect(() => {
        if (autoTabRef.current) return;
        if (!searchParams?.get('tab') && isOwner() && activeUnit) {
            autoTabRef.current = true;
            setActiveTab('status');
        }
    }, [activeUnit, isOwner, searchParams]);

    useEffect(() => {
        const tab = searchParams?.get('tab');
        if (tab) setActiveTab(tab);
    }, [searchParams]);

    const unitParamHandled = React.useRef(false);
    useEffect(() => {
        if (unitParamHandled.current) return;
        const unitParam = searchParams?.get('unit');
        if (unitParam && (canManage || canSearchAll)) {
            unitParamHandled.current = true;
            setUnitSearch(unitParam);
            fetchLevyStatus(unitParam);
        }
    }, [searchParams, canManage, canSearchAll, fetchLevyStatus]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/levy-payments', {...formData, amount: parseFloat(formData.amount)});
            toast.success('Payment recorded successfully');
            setDialogOpen(false);
            setFormData({
                unit_number: '',
                amount: '',
                payment_method: '',
                payment_reference: '',
                quarter: 'Q1',
                year: String(currentYear),
                notes: ''
            });
            fetchData();
        } catch (err: any) {
            toast.error(err.response?.data?.detail || 'Failed to record payment');
        } finally {
            setSubmitting(false);
        }
    };
    // GAP-FIN-014: a confirmed levy_payments row for this quarter/year/unit that
    // hasn't yet moved the ledger-derived quarter status to "paid" means the
    // payment exists but has not been reconciled into unit_levy_ledger — distinct
    // from the existing owner-submitted "pending" (awaiting manager verification)
    // status, which is a different, already-handled workflow.
    /**
     * @generated FunctionHeader
     * Function: isPendingLedgerReconciliation
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const isPendingLedgerReconciliation = (q: any) => {
        if (!levyStatus?.unit_number || q.status === 'paid') return false;
        return payments.some(p =>
            p.unit_number === levyStatus.unit_number &&
            p.quarter === q.quarter &&
            String(p.year) === String(levyStatus.year) &&
            p.status === 'confirmed'
        );
    };
    /**
     * @generated FunctionHeader
     * Function: handlePayNow
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePayNow = () => {
        if (!levyStatus || levyStatus.balance_due <= 0) {
            toast.info('No payment required — your account is up to date!');
            return;
        }
        const totalAnnual = levyStatus.total_annual || levyStatus.annual_levy || 0;
        const adminRatio = totalAnnual > 0 ? (levyStatus.admin_annual || 0) / totalAnnual : 0.773;
        const adminAmount = Math.round(levyStatus.balance_due * adminRatio * 100) / 100;
        const sinkingAmount = Math.round((levyStatus.balance_due - adminAmount) * 100) / 100;
        setPaymentData({
            unit_number: levyStatus.unit_number,
            amount: levyStatus.balance_due,
            levy_period: 'Current Period',
            admin_fund_amount: adminAmount,
            sinking_fund_amount: sinkingAmount,
            description: `Levy Payment - Unit ${levyStatus.unit_number}`
        });
        setIsPaymentModalOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handlePaymentSuccess
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePaymentSuccess = () => {
        toast.success('Payment successful! Your balance has been updated.');
        if (unitSearch) fetchLevyStatus(unitSearch);
        fetchData();
    };
    /**
     * @generated FunctionHeader
     * Function: handleQuarterPayment
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleQuarterPayment = async ({amount, method, reference, paymentDate, notes, mode, paymentType}: any) => {
        if (!quarterPayData || !levyStatus) return;
        setQuarterSubmitting(true);
        try {
            const modeLabel = mode === 'partial' ? 'Partial payment' : mode === 'advance' ? 'Advance payment' : 'Full payment';
            await api.post('/levy-payments', {
                unit_number: levyStatus.unit_number,
                amount,
                payment_method: method || 'other',
                payment_reference: reference || '',
                quarter: quarterPayData.quarter,
                year: String(levyStatus.year),
                payment_type: paymentType || 'standard',
                notes: [modeLabel, notes].filter(Boolean).join(' — '),
            });
            toast.success(`${quarterPayData.quarter} payment recorded`);
            setQuarterPayOpen(null);
            setQuarterPayData(null);
            fetchLevyStatus(levyStatus.unit_number);
            fetchData();
        } catch (err: any) {
            toast.error(err.response?.data?.detail || 'Failed to record payment');
        } finally {
            setQuarterSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleVerify
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleVerify = async (paymentId: string) => {
        setActionLoading(paymentId);
        try {
            await api.patch(`/levy-payments/${paymentId}/verify`, {});
            toast.success('Payment confirmed');
            fetchData();
            if (levyStatus) fetchLevyStatus(levyStatus.unit_number);
        } catch (err: any) {
            toast.error(err.response?.data?.detail || 'Failed to confirm payment');
        } finally {
            setActionLoading(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleReject
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReject = async () => {
        const payment = rejectDialog.payment;
        if (!payment) return;
        if (!rejectionReason.trim() || rejectionReason.trim().length < 5) {
            toast.error('Please provide a rejection reason (min 5 characters)');
            return;
        }
        setActionLoading(payment.id);
        try {
            await api.patch(`/levy-payments/${payment.id}/reject`, {rejection_reason: rejectionReason.trim()});
            toast.success('Payment rejected');
            setRejectDialog({open: false, payment: null});
            setRejectionReason('');
            fetchData();
            if (levyStatus) fetchLevyStatus(levyStatus.unit_number);
        } catch (err: any) {
            toast.error(err.response?.data?.detail || 'Failed to reject payment');
        } finally {
            setActionLoading(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (payment: any) => {
        setActionLoading(payment.id);
        try {
            await api.delete(`/levy-payments/${payment.id}`);
            toast.success('Payment record removed');
            setDeleteConfirm({open: false, payment: null});
            fetchData();
            if (levyStatus) fetchLevyStatus(levyStatus.unit_number);
        } catch (err: any) {
            toast.error(err.response?.data?.detail || 'Failed to delete payment');
        } finally {
            setActionLoading(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: methodIcon
     * Path: frontend/src/pages/dashboard/LevyPaymentPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const methodIcon = (id: string) => {
        const icons: Record<string, any> = {
            stripe: Zap,
            deft: ExternalLink,
            bpay: Banknote,
            credit_card: CreditCard,
            bank_transfer: Building2,
            australia_post: Receipt
        };
        const Icon = icons[id] || DollarSign;
        return <Icon className="h-6 w-6"/>;
    };

    return (
        <div className="space-y-6" data-testid="levy-payment-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Levy Payments</h1>
                    <p className="text-muted-foreground">Pay and track your strata levy, council rates, and water
                        bills</p>
                </div>
                {canManage && (
                    <>
                        <Button onClick={() => setDialogOpen(true)}><Plus className="mr-2 h-4 w-4"/>Record
                            Payment</Button>
                        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Record Levy Payment</DialogTitle>
                                    <DialogDescription>Record a levy payment for a unit</DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleSubmit} className="space-y-4">
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Unit Number</Label>
                                            <Input value={formData.unit_number} onChange={(e) => setFormData(p => ({
                                                ...p,
                                                unit_number: (e.target as HTMLInputElement).value
                                            }))} placeholder="e.g., TH017" required/>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Amount (AUD)</Label>
                                            <Input type="number" step="0.01" value={formData.amount}
                                                   onChange={(e) => setFormData(p => ({
                                                       ...p,
                                                       amount: (e.target as HTMLInputElement).value
                                                   }))} required/>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Quarter</Label>
                                            <Select value={formData.quarter}
                                                    onValueChange={(v) => setFormData(p => ({...p, quarter: v}))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>{['Q1', 'Q2', 'Q3', 'Q4'].map(q => <SelectItem key={q}
                                                                                                              value={q}>{q}</SelectItem>)}</SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Year</Label>
                                            <Select value={formData.year}
                                                    onValueChange={(v) => setFormData(p => ({...p, year: v}))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem
                                                        value={String(currentYear - 1)}>{currentYear - 1}</SelectItem>
                                                    <SelectItem value={String(currentYear)}>{currentYear}</SelectItem>
                                                    <SelectItem
                                                        value={String(currentYear + 1)}>{currentYear + 1}</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Payment Method</Label>
                                        <Select value={formData.payment_method}
                                                onValueChange={(v) => setFormData(p => ({...p, payment_method: v}))}>
                                            <SelectTrigger><SelectValue placeholder="Select method"/></SelectTrigger>
                                            <SelectContent>{paymentMethods.filter(m => m.enabled).map(m => <SelectItem
                                                key={m.id} value={m.id}>{m.name}</SelectItem>)}</SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Payment Reference</Label>
                                        <Input value={formData.payment_reference} onChange={(e) => setFormData(p => ({
                                            ...p,
                                            payment_reference: (e.target as HTMLInputElement).value
                                        }))} placeholder="Transaction reference"/>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Notes (optional)</Label>
                                        <Input value={formData.notes} onChange={(e) => setFormData(p => ({
                                            ...p,
                                            notes: (e.target as HTMLInputElement).value
                                        }))}/>
                                    </div>
                                    <Button type="submit" className="w-full" disabled={submitting}>
                                        {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                            <Receipt className="mr-2 h-4 w-4"/>}
                                        Record Payment
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    </>
                )}
            </div>

            {/* Role-context banner for EC members: they are unit owners AND committee members.
                Shows only for ec_member role (chairman/super_admin have their own context via canSearchAll). */}
            {user?.role === 'ec_member' && (
                <div
                    role="note"
                    aria-label="You are an Executive Committee Member viewing your own unit levy"
                    className="flex items-start gap-3 p-4 rounded-lg border bg-teal-50 border-teal-200 text-teal-800"
                    data-testid="ec-member-levy-banner"
                >
                    <ShieldCheck className="h-5 w-5 shrink-0 mt-0.5 text-teal-600" aria-hidden="true"/>
                    <div>
                        <p className="font-semibold text-sm">
                            Executive Committee Member
                            {activeUnit && (
                                <span className="ml-2 font-normal text-teal-700">
                                    · Unit {activeUnit} (Owner)
                                </span>
                            )}
                        </p>
                        <p className="text-sm mt-0.5 text-teal-700">
                            {activeUnit
                                ? 'Your levy details as a unit owner are shown in the Levy Status tab. Your EC role also lets you record and verify payments for the building.'
                                : 'No unit is linked to your account. Contact your strata manager to associate your unit so your personal levy details appear here.'}
                        </p>
                    </div>
                </div>
            )}

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList className="grid w-full grid-cols-3 lg:w-auto lg:grid-cols-none lg:inline-flex">
                    <TabsTrigger value="overview"><CreditCard className="mr-2 h-4 w-4"/>Payment Methods</TabsTrigger>
                    <TabsTrigger value="status"><Search className="mr-2 h-4 w-4"/>Levy Status</TabsTrigger>
                    <TabsTrigger value="history"><Receipt className="mr-2 h-4 w-4"/>Payment History</TabsTrigger>
                </TabsList>

                <TabsContent value="overview" className="space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        <Card className="card-dashboard hover:shadow-md transition-shadow border-t-4 border-t-primary">
                            <CardContent className="p-6">
                                <div className="flex items-start gap-4">
                                    <div
                                        className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                                        <CreditCard className="h-6 w-6 text-primary"/>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <h3 className="font-bold text-lg">Card or Direct Debit (DEFT)</h3>
                                        <div className="mt-3 space-y-3">
                                            <div>
                                                <p className="text-xs text-muted-foreground uppercase font-bold tracking-wider">DEFT
                                                    Reference Number</p>
                                                <p className="font-mono text-sm bg-muted p-1.5 rounded mt-1 select-all">{paymentMethods.find(m => m.id === 'deft')?.deft_ref || '—'}</p>
                                            </div>
                                            <div className="text-sm space-y-1">
                                                <p className="font-medium flex items-center gap-1.5">
                                                    <Info className="h-3.5 w-3.5"/> How to pay:
                                                </p>
                                                <p className="text-muted-foreground">Visit <a href="https://deft.com.au"
                                                                                              target="_blank"
                                                                                              rel="noopener noreferrer"
                                                                                              className="text-primary hover:underline font-bold">deft.com.au</a> and
                                                    pay by card or direct debit.</p>
                                            </div>
                                            <p className="text-xs text-orange-600 bg-orange-50 p-2 rounded border border-orange-100 italic">
                                                Note: Card payments may attract a surcharge.
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>

                        <Card className="card-dashboard hover:shadow-md transition-shadow border-t-4 border-t-blue-600">
                            <CardContent className="p-6">
                                <div className="flex items-start gap-4">
                                    <div
                                        className="w-12 h-12 rounded-lg bg-blue-50 flex items-center justify-center shrink-0">
                                        <Banknote className="h-6 w-6 text-blue-600"/>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <h3 className="font-bold text-lg">BPAY</h3>
                                        <div className="mt-3 space-y-3">
                                            <div className="grid grid-cols-2 gap-2">
                                                <div>
                                                    <p className="text-xs text-muted-foreground uppercase font-bold tracking-wider">Biller
                                                        Code</p>
                                                    <p className="font-mono text-sm bg-muted p-1.5 rounded mt-1">{paymentMethods.find(m => m.id === 'bpay')?.biller_code || '—'}</p>
                                                </div>
                                                <div>
                                                    <p className="text-xs text-muted-foreground uppercase font-bold tracking-wider">Ref</p>
                                                    <p className="font-mono text-sm bg-muted p-1.5 rounded mt-1 select-all">{paymentMethods.find(m => m.id === 'bpay')?.bpay_ref || '—'}</p>
                                                </div>
                                            </div>
                                            <div className="text-sm space-y-1">
                                                <p className="font-medium flex items-center gap-1.5">
                                                    <Info className="h-3.5 w-3.5"/> How to pay:
                                                </p>
                                                <p className="text-muted-foreground">Use your bank’s mobile or internet
                                                    banking to make a BPAY payment.</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>

                        <Card className="card-dashboard hover:shadow-md transition-shadow border-t-4 border-t-red-600">
                            <CardContent className="p-6">
                                <div className="flex items-start gap-4">
                                    <div
                                        className="w-12 h-12 rounded-lg bg-red-50 flex items-center justify-center shrink-0">
                                        <Receipt className="h-6 w-6 text-red-600"/>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <h3 className="font-bold text-lg">Post Billpay</h3>
                                        <div className="mt-3 space-y-3">
                                            <div className="grid grid-cols-2 gap-2">
                                                <div>
                                                    <p className="text-xs text-muted-foreground uppercase font-bold tracking-wider">Code</p>
                                                    <p className="font-mono text-sm bg-muted p-1.5 rounded mt-1">{paymentMethods.find(m => m.id === 'australia_post')?.billpay_code || '—'}</p>
                                                </div>
                                                <div>
                                                    <p className="text-xs text-muted-foreground uppercase font-bold tracking-wider">Ref</p>
                                                    <p className="font-mono text-sm bg-muted p-1.5 rounded mt-1 select-all text-[10px]">{paymentMethods.find(m => m.id === 'australia_post')?.aus_post_ref || '—'}</p>
                                                </div>
                                            </div>
                                            <div className="text-sm space-y-2">
                                                <p className="font-medium flex items-center gap-1.5">
                                                    <Info className="h-3.5 w-3.5"/> How to pay:
                                                </p>
                                                <p className="text-muted-foreground">Pay in-store at Australia Post via
                                                    EFTPOS or cheque.</p>
                                                <p className="text-[11px] bg-slate-100 p-2 rounded border">
                                                    <span
                                                        className="font-bold block uppercase text-[9px] text-muted-foreground">Cheque payable to:</span>
                                                    The Proprietors Of Units Plan {selectedBuilding?.building_id || '—'}
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>

                        {paymentMethods.filter(m => m.enabled && !['deft', 'bpay', 'australia_post'].includes(m.id)).map(method => (
                            <Card key={method.id} className="card-dashboard hover:shadow-md transition-shadow">
                                <CardContent className="p-6">
                                    <div className="flex items-start gap-4">
                                        <div
                                            className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                                            {methodIcon(method.id)}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <h3 className="font-semibold text-lg">{method.name}</h3>
                                            <p className="text-sm text-muted-foreground mb-3">{method.description}</p>
                                            {method.biller_code && <p className="text-sm"><span className="font-medium">Biller Code:</span> {method.biller_code}
                                            </p>}
                                            {method.reference_format && <p className="text-sm"><span
                                                className="font-medium">Reference:</span> {method.reference_format}</p>}
                                            {method.bsb && (
                                                <div className="text-sm space-y-0.5">
                                                    <p><span className="font-medium">BSB:</span> {method.bsb}</p>
                                                    <p><span
                                                        className="font-medium">Account:</span> {method.account_number}
                                                    </p>
                                                    <p><span className="font-medium">Name:</span> {method.account_name}
                                                    </p>
                                                </div>
                                            )}
                                            {method.surcharge && <Badge variant="outline"
                                                                        className="mt-2 text-orange-600 border-orange-200">Surcharge: {method.surcharge}</Badge>}
                                            {method.note &&
                                                <p className="text-xs text-orange-600 mt-2">{method.note}</p>}
                                            {method.url && (
                                                <a href={method.url} target="_blank" rel="noopener noreferrer"
                                                   className="inline-flex items-center gap-1 text-sm text-primary hover:underline mt-2">
                                                    Pay Online <ExternalLink className="h-3 w-3"/>
                                                </a>
                                            )}
                                            {method.id === 'stripe' && (
                                                <Button size="sm" className="w-full mt-4" onClick={() => {
                                                    if (levyStatus && levyStatus.balance_due > 0) {
                                                        const totalAnnual = levyStatus.total_annual || levyStatus.annual_levy || 0;
                                                        const adminRatio = totalAnnual > 0 ? (levyStatus.admin_annual || 0) / totalAnnual : 0.773;
                                                        const adminAmount = Math.round(levyStatus.balance_due * adminRatio * 100) / 100;
                                                        const sinkingAmount = Math.round((levyStatus.balance_due - adminAmount) * 100) / 100;
                                                        setPaymentData({
                                                            unit_number: levyStatus.unit_number,
                                                            amount: levyStatus.balance_due,
                                                            levy_period: 'Current Period',
                                                            admin_fund_amount: adminAmount,
                                                            sinking_fund_amount: sinkingAmount,
                                                            description: `Levy Payment - Unit ${levyStatus.unit_number}`
                                                        });
                                                        setIsPaymentModalOpen(true);
                                                    } else {
                                                        setActiveTab('status');
                                                    }
                                                }}>Pay via Stripe</Button>
                                            )}
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                    <LevyDueDatesCard/>
                </TabsContent>

                <TabsContent value="status" className="space-y-6">
                    <Card className="card-dashboard">
                        <CardHeader>
                            <CardTitle className="text-lg">Check Levy Status</CardTitle>
                            <CardDescription>
                                {canSearchAll ? 'Enter a unit number to view payment status' : 'Payment status and breakdown for your unit.'}
                            </CardDescription>
                        </CardHeader>
                        {canSearchAll && (
                            <CardContent>
                                <div className="flex gap-2 max-w-md">
                                    <Input placeholder="Unit number (e.g., UA001)" value={unitSearch}
                                           onChange={(e) => setUnitSearch((e.target as HTMLInputElement).value)}
                                           onKeyDown={(e) => e.key === 'Enter' && fetchLevyStatus(unitSearch)}/>
                                    <Button onClick={() => fetchLevyStatus(unitSearch)} disabled={!unitSearch}>
                                        <Search className="mr-2 h-4 w-4"/>Check
                                    </Button>
                                </div>
                            </CardContent>
                        )}
                    </Card>

                    {levyStatus && (
                        <>
                            {levyStatus.in_credit && levyStatus.credit_balance > 0 && (
                                <div
                                    className="flex items-start gap-3 p-4 rounded-lg border bg-green-50 border-green-200 text-green-800">
                                    <TrendingUp className="h-5 w-5 shrink-0 mt-0.5 text-green-600"/>
                                    <div>
                                        <p className="font-semibold text-sm">
                                            Your account is in credit — {formatCurrency(levyStatus.credit_balance)}
                                        </p>
                                        <p className="text-sm mt-0.5">
                                            You have paid {formatCurrency(levyStatus.credit_balance)} above
                                            the {levyStatus.year} annual levy.
                                            This credit will automatically apply to your next quarterly levy.
                                        </p>
                                    </div>
                                </div>
                            )}

                            {levyStatus.prev_year_balance !== 0 && (() => {
                                const q1 = levyStatus.quarters?.[0];
                                const q1Paid = q1?.status === 'paid';
                                if (levyStatus.prev_year_balance > 0) {
                                    return (
                                        <div
                                            className="flex items-start gap-3 p-4 rounded-lg border bg-amber-50 border-amber-200 text-amber-800">
                                            <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5"/>
                                            <div>
                                                <p className="font-semibold text-sm">
                                                    {q1Paid ? `${levyStatus.prev_year} Outstanding Balance — Cleared` : `${levyStatus.prev_year} Outstanding Balance — Included in Q1 ${levyStatus.year}`}
                                                </p>
                                                <p className="text-sm mt-0.5">
                                                    Unit {levyStatus.unit_number} had {formatCurrency(levyStatus.prev_year_balance)} outstanding
                                                    at end of {levyStatus.prev_year}.
                                                    {!q1Paid && ` This is included in the Q1 ${levyStatus.year} amount.`}
                                                </p>
                                            </div>
                                        </div>
                                    );
                                }
                                return (
                                    <div
                                        className="flex items-start gap-3 p-4 rounded-lg border bg-green-50 border-green-200 text-green-800">
                                        <TrendingDown className="h-5 w-5 shrink-0 mt-0.5"/>
                                        <div>
                                            <p className="font-semibold text-sm">{levyStatus.prev_year} Credit — Applied
                                                to Q1 {levyStatus.year}</p>
                                            <p className="text-sm mt-0.5">Unit {levyStatus.unit_number} had
                                                a {formatCurrency(Math.abs(levyStatus.prev_year_balance))} credit at end
                                                of {levyStatus.prev_year}.</p>
                                        </div>
                                    </div>
                                );
                            })()}

                            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                                <Card className="card-dashboard">
                                    <CardContent className="p-6">
                                        <p className="text-sm text-muted-foreground mb-1">Unit {levyStatus.unit_number}</p>
                                        <p className="text-2xl font-bold">{formatCurrency(levyStatus.annual_levy || levyStatus.total_annual)}</p>
                                        <p className="text-xs text-muted-foreground">Annual Levy
                                            ({levyStatus.year || levyStatus.financial_year})</p>
                                    </CardContent>
                                </Card>
                                <Card className="card-dashboard">
                                    <CardContent className="p-6">
                                        <p className="text-sm text-muted-foreground mb-1">Standard Quarterly</p>
                                        <p className="text-2xl font-bold text-blue-600">{formatCurrency(levyStatus.period_levy || levyStatus.quarterly_levy)}</p>
                                        <p className="text-xs text-muted-foreground">{levyStatus.entitlement_units || levyStatus.uoe} entitlement
                                            units</p>
                                    </CardContent>
                                </Card>
                                <Card className="card-dashboard">
                                    <CardContent className="p-6">
                                        <p className="text-sm text-muted-foreground mb-1">Total Paid</p>
                                        <p className="text-2xl font-bold text-green-600">{formatCurrency(levyStatus.total_paid)}</p>
                                        {levyStatus.pending_total > 0 && (
                                            <p className="text-xs text-blue-700 mt-1 flex items-center gap-1">
                                                <Clock
                                                    className="h-3 w-3"/>{formatCurrency(levyStatus.pending_total)} pending
                                                confirmation
                                            </p>
                                        )}
                                    </CardContent>
                                </Card>
                                <Card className="card-dashboard">
                                    <CardContent className="p-6">
                                        <div className="flex items-center justify-between mb-1">
                                            <p className="text-sm text-muted-foreground">Balance Due</p>
                                            {levyStatus.balance_due > 0 && !canSearchAll && (
                                                <Button size="sm" variant="outline" className="h-7 px-2 text-xs"
                                                        onClick={handlePayNow}>Pay Now</Button>
                                            )}
                                        </div>
                                        <p className={`text-2xl font-bold ${levyStatus.balance_due > 0 ? 'text-red-600' : 'text-green-600'}`}>
                                            {formatCurrency(levyStatus.balance_due)}
                                        </p>
                                        {levyStatus.prev_year_balance > 0 && (
                                            <p className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                                                <Info
                                                    className="h-3.5 w-3.5"/>Includes {formatCurrency(levyStatus.prev_year_balance)} carry-forward
                                            </p>
                                        )}
                                    </CardContent>
                                </Card>
                            </div>

                            <Card className="card-dashboard">
                                <CardHeader>
                                    <div className="flex items-center justify-between">
                                        <CardTitle className="text-lg">
                                            {levyStatus.year} Strata Levy — Quarterly Breakdown
                                            {levyStatus.prev_year_balance > 0 && (
                                                <span className="ml-2 text-sm font-normal text-amber-600">
                          (Q1 includes {formatCurrency(levyStatus.prev_year_balance)} from {levyStatus.prev_year})
                        </span>
                                            )}
                                        </CardTitle>
                                        <p className="text-xs text-muted-foreground">Click a quarter to record a payment
                                            made via DEFT or BPAY</p>
                                    </div>
                                </CardHeader>
                                <CardContent>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                        {levyStatus.quarters?.map((q: any) => (
                                            <div
                                                key={q.quarter}
                                                className={`p-4 rounded-lg border-2 transition-all space-y-2 ${periodCardClass(q.status, q.carry_forward !== 0 || q.rolled_forward > 0)}`}
                                            >
                                                <div className="flex items-center justify-between">
                                                    <span className="font-semibold">{q.quarter}</span>
                                                    {statusIcon(q.status)}
                                                </div>
                                                <p className="text-sm font-bold">{formatCurrency(q.amount_due)}</p>
                                                {q.carry_forward > 0 && (
                                                    <p className="text-xs text-amber-700">+{formatCurrency(q.carry_forward)} from {levyStatus.prev_year}</p>
                                                )}
                                                {q.carry_forward < 0 && (
                                                    <p className="text-xs text-green-700">{formatCurrency(Math.abs(q.carry_forward))} CR
                                                        from {levyStatus.prev_year}</p>
                                                )}
                                                {q.rolled_forward > 0 && q.status !== 'paid' && (
                                                    <p className="text-xs text-red-700">Incl. {formatCurrency(q.rolled_forward)} rolled over</p>
                                                )}
                                                {q.due_date && (
                                                    <p className="text-xs text-muted-foreground">
                                                        Due {new Date(q.due_date).toLocaleDateString('en-AU', {
                                                        day: 'numeric',
                                                        month: 'short'
                                                    })}
                                                        {levyStatus.grace_period_days > 0 && ` (+${levyStatus.grace_period_days}d)`}
                                                    </p>
                                                )}
                                                {statusBadge(q.status)}
                                                {q.amount_paid > 0 && q.status !== 'paid' && q.status !== 'pending' && (
                                                    <p className="text-xs text-muted-foreground">Paid: {formatCurrency(q.amount_paid)}</p>
                                                )}
                                                {q.status === 'pending' && (
                                                    <p className="text-xs text-blue-700 italic flex items-center gap-1">
                                                        <Clock className="h-3 w-3"/>{formatCurrency(q.amount_paid)} —
                                                        awaiting confirmation
                                                    </p>
                                                )}
                                                {isPendingLedgerReconciliation(q) && (
                                                    <p
                                                        data-testid={`pending-ledger-reconciliation-${q.quarter}`}
                                                        className="text-xs text-amber-700 italic flex items-center gap-1"
                                                    >
                                                        <Info className="h-3 w-3"/>Pending ledger reconciliation
                                                    </p>
                                                )}
                                                {(() => {
                                                    const isDisabled =
                                                        q.status === 'paid' ||
                                                        q.status === 'pending' ||
                                                        (q.status === 'partial' && q.past_deadline);
                                                    if (isDisabled) return null;
                                                    return (
                                                        <Button
                                                            size="sm" variant="outline"
                                                            className="w-full text-xs h-7 mt-1"
                                                            onClick={() => {
                                                                setQuarterPayData({
                                                                    quarter: q.quarter,
                                                                    amount: q.amount_due
                                                                });
                                                                setQuarterPayOpen(q.quarter);
                                                            }}
                                                            data-testid={`levy-record-payment-${q.quarter}`}
                                                        >
                                                            <Receipt className="mr-1 h-3 w-3"/>
                                                            {q.status === 'partial' ? 'Record Remaining' : 'Record Payment'}
                                                        </Button>
                                                    );
                                                })()}
                                            </div>
                                        ))}
                                    </div>
                                </CardContent>
                            </Card>

                            <div>
                                <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3 flex items-center gap-2">
                                    <Landmark className="h-4 w-4"/>Property Taxes
                                </h3>
                                <CouncilRatesSection unitNumber={levyStatus.unit_number}
                                                     paymentMethods={paymentMethods}/>
                            </div>

                            <div>
                                <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3 flex items-center gap-2">
                                    <Droplets className="h-4 w-4"/>Water Bills
                                </h3>
                                <WaterBillsSection unitNumber={levyStatus.unit_number} paymentMethods={paymentMethods}/>
                            </div>
                        </>
                    )}
                </TabsContent>

                <TabsContent value="history" className="space-y-4">
                    {canManage && payments.filter(p => p.status === 'pending_verification').length > 0 && (
                        <Card className="card-dashboard border-amber-300 bg-amber-50/50">
                            <CardHeader className="pb-3">
                                <CardTitle className="text-base flex items-center gap-2 text-amber-800">
                                    <Clock className="h-4 w-4"/>
                                    {payments.filter(p => p.status === 'pending_verification').length} Payment{payments.filter(p => p.status === 'pending_verification').length !== 1 ? 's' : ''} Awaiting
                                    Verification
                                </CardTitle>
                                <CardDescription className="text-amber-700">Reconcile these against the bank statement,
                                    then confirm or reject.</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Receipt</TableHead>
                                                <TableHead>Unit</TableHead>
                                                <TableHead>Quarter</TableHead>
                                                <TableHead>Year</TableHead>
                                                <TableHead>Method</TableHead>
                                                <TableHead className="text-right">Amount</TableHead>
                                                <TableHead>Submitted</TableHead>
                                                <TableHead>Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {payments.filter(p => p.status === 'pending_verification').map(p => (
                                                <TableRow key={p.id}>
                                                    <TableCell
                                                        className="font-mono text-sm">{p.receipt_number}</TableCell>
                                                    <TableCell className="font-medium">{p.unit_number}</TableCell>
                                                    <TableCell>{p.quarter}</TableCell>
                                                    <TableCell>{p.year}</TableCell>
                                                    <TableCell
                                                        className="capitalize">{p.payment_method?.replace('_', ' ')}</TableCell>
                                                    <TableCell
                                                        className="text-right font-medium">{formatCurrency(p.amount)}</TableCell>
                                                    <TableCell>{formatDate(p.created_at)}</TableCell>
                                                    <TableCell>
                                                        <div className="flex items-center gap-1">
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                className="h-7 px-2 text-xs text-green-700 border-green-300 hover:bg-green-50"
                                                                disabled={actionLoading === p.id}
                                                                onClick={() => handleVerify(p.id)}
                                                                title="Confirm this payment"
                                                            >
                                                                {actionLoading === p.id ?
                                                                    <Loader2 className="h-3 w-3 animate-spin"/> :
                                                                    <ShieldCheck className="h-3 w-3"/>}
                                                                <span className="ml-1">Confirm</span>
                                                            </Button>
                                                            <Button
                                                                size="sm"
                                                                variant="outline"
                                                                className="h-7 px-2 text-xs text-red-700 border-red-300 hover:bg-red-50"
                                                                disabled={actionLoading === p.id}
                                                                onClick={() => {
                                                                    setRejectDialog({open: true, payment: p});
                                                                    setRejectionReason('');
                                                                }}
                                                                title="Reject this payment"
                                                            >
                                                                <ShieldX className="h-3 w-3"/>
                                                                <span className="ml-1">Reject</span>
                                                            </Button>
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            </CardContent>
                        </Card>
                    )}

                    <Card className="card-dashboard">
                        <CardHeader>
                            <CardTitle className="text-lg">Payment History</CardTitle>
                            <CardDescription>All recorded levy payments</CardDescription>
                        </CardHeader>
                        <CardContent>
                            {payments.length === 0 && levyStatus ? (
                                <div>
                                    <p className="text-sm text-muted-foreground mb-4">
                                        No payment transactions recorded yet. Showing levy period status
                                        for {levyStatus.year}:
                                    </p>
                                    {/* Backend returns levy periods as `quarters`, not `periods` */}
                                    {levyStatus.quarters?.length > 0 ? (
                                        <div className="overflow-x-auto">
                                            <Table aria-label={`Levy period breakdown for ${levyStatus.year}`}>
                                                <TableHeader>
                                                    <TableRow>
                                                        <TableHead scope="col">Period</TableHead>
                                                        <TableHead scope="col">Year</TableHead>
                                                        <TableHead scope="col">Due Date</TableHead>
                                                        <TableHead className="text-right" scope="col">Amount
                                                            Due</TableHead>
                                                        <TableHead className="text-right" scope="col">Amount
                                                            Paid</TableHead>
                                                        <TableHead scope="col">Status</TableHead>
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    {(levyStatus.quarters ?? []).map((period: any, i: number) => (
                                                        <TableRow key={i}>
                                                            <TableCell
                                                                className="font-medium">{period.quarter}</TableCell>
                                                            <TableCell>{levyStatus.year}</TableCell>
                                                            <TableCell>
                                                                {period.due_date
                                                                    ? new Date(period.due_date).toLocaleDateString('en-AU', {
                                                                        day: 'numeric',
                                                                        month: 'short',
                                                                        year: 'numeric'
                                                                    })
                                                                    : '—'}
                                                            </TableCell>
                                                            <TableCell
                                                                className="text-right font-medium">{formatCurrency(period.amount_due || 0)}</TableCell>
                                                            <TableCell
                                                                className="text-right font-medium">{formatCurrency(period.amount_paid || 0)}</TableCell>
                                                            <TableCell>
                                                                <Badge
                                                                    aria-label={`Status: ${period.status || 'upcoming'}`}
                                                                    variant={
                                                                        period.status === 'paid' ? 'default' :
                                                                            period.status === 'partial' ? 'secondary' :
                                                                                period.status === 'overdue' ? 'destructive' : 'outline'
                                                                    }
                                                                >
                                                                    {period.status === 'paid' ? 'Paid'
                                                                        : period.status === 'partial' ? 'Partial'
                                                                            : period.status === 'overdue' ? 'Overdue'
                                                                                : period.status === 'pending' ? 'Pending'
                                                                                    : 'Upcoming'}
                                                                </Badge>
                                                            </TableCell>
                                                        </TableRow>
                                                    ))}
                                                </TableBody>
                                            </Table>
                                        </div>
                                    ) : (
                                        <div
                                            className="flex items-start gap-2 p-3 rounded-md bg-muted/50 text-sm text-muted-foreground">
                                            <Info className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true"/>
                                            <p>No levy periods found for {levyStatus.year}. Levy period configuration
                                                can be set in <strong>Building Settings → Financial</strong>.</p>
                                        </div>
                                    )}
                                </div>
                            ) : payments.length === 0 ? (
                                <div className="py-12 text-center">
                                    <Receipt className="h-12 w-12 text-muted-foreground/50 mx-auto mb-4"/>
                                    <p className="text-muted-foreground">No payments recorded yet</p>
                                    <p className="text-sm text-muted-foreground mt-2">Payments made via DEFT, BPAY or
                                        online will appear here.</p>
                                </div>
                            ) : (
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead>Receipt</TableHead>
                                                <TableHead>Unit</TableHead>
                                                <TableHead>Quarter</TableHead>
                                                <TableHead>Year</TableHead>
                                                <TableHead>Method</TableHead>
                                                <TableHead className="text-right">Amount</TableHead>
                                                <TableHead>Status</TableHead>
                                                <TableHead>Date</TableHead>
                                                <TableHead></TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {payments.map(p => (
                                                <TableRow key={p.id}>
                                                    <TableCell
                                                        className="font-mono text-sm">{p.receipt_number}</TableCell>
                                                    <TableCell className="font-medium">{p.unit_number}</TableCell>
                                                    <TableCell>{p.quarter}</TableCell>
                                                    <TableCell>{p.year || p.financial_year}</TableCell>
                                                    <TableCell
                                                        className="capitalize">{p.payment_method?.replace('_', ' ')}</TableCell>
                                                    <TableCell
                                                        className="text-right font-medium">{formatCurrency(p.amount)}</TableCell>
                                                    <TableCell>
                                                        <div className="flex flex-col gap-0.5">
                                                            <Badge variant={
                                                                p.status === 'confirmed' ? 'default' :
                                                                    p.status === 'rejected' ? 'destructive' : 'secondary'
                                                            } className="text-xs w-fit">
                                                                {p.status === 'pending_verification' ? 'Pending' : p.status}
                                                            </Badge>
                                                            {p.status === 'rejected' && p.rejection_reason && (
                                                                <span
                                                                    className="text-[10px] text-red-600 max-w-[140px] truncate"
                                                                    title={p.rejection_reason}>
                                  {p.rejection_reason}
                                </span>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>{formatDate(p.created_at)}</TableCell>
                                                    <TableCell>
                                                        {!canManage && p.status === 'pending_verification' && p.paid_by === user?.id && (
                                                            <Button
                                                                size="sm" variant="ghost"
                                                                className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                                                                disabled={actionLoading === p.id}
                                                                onClick={() => setDeleteConfirm({
                                                                    open: true,
                                                                    payment: p
                                                                })}
                                                                title="Cancel this pending payment"
                                                            >
                                                                {actionLoading === p.id ?
                                                                    <Loader2 className="h-3 w-3 animate-spin"/> :
                                                                    <Trash2 className="h-3 w-3"/>}
                                                            </Button>
                                                        )}
                                                        {canManage && (
                                                            <Button
                                                                size="sm" variant="ghost"
                                                                className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                                                                disabled={actionLoading === p.id}
                                                                onClick={() => setDeleteConfirm({
                                                                    open: true,
                                                                    payment: p
                                                                })}
                                                                title="Delete payment record"
                                                            >
                                                                {actionLoading === p.id ?
                                                                    <Loader2 className="h-3 w-3 animate-spin"/> :
                                                                    <Trash2 className="h-3 w-3"/>}
                                                            </Button>
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                <Dialog open={rejectDialog.open} onOpenChange={open => {
                    if (!open) {
                        setRejectDialog({open: false, payment: null});
                        setRejectionReason('');
                    }
                }}>
                    <DialogContent>
                        <DialogHeader>
                            <DialogTitle>Reject Payment — {rejectDialog.payment?.receipt_number}</DialogTitle>
                            <DialogDescription>
                                This will mark the payment as rejected and notify the owner. Please provide a reason.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="space-y-3 py-2">
                            <div className="text-sm text-muted-foreground border rounded p-3 bg-muted/40 space-y-1">
                                <p><span className="font-medium">Unit:</span> {rejectDialog.payment?.unit_number}</p>
                                <p><span
                                    className="font-medium">Amount:</span> {formatCurrency(rejectDialog.payment?.amount)}
                                </p>
                                <p><span
                                    className="font-medium">Quarter:</span> {rejectDialog.payment?.quarter} {rejectDialog.payment?.year}
                                </p>
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="rejection-reason">Rejection Reason <span
                                    className="text-destructive">*</span></Label>
                                <Textarea
                                    id="rejection-reason"
                                    placeholder="e.g. Payment not found in bank statement for this period"
                                    value={rejectionReason}
                                    onChange={e => setRejectionReason((e.target as HTMLTextAreaElement).value)}
                                    rows={3}
                                />
                                <p className="text-xs text-muted-foreground">The owner will see this reason in their
                                    payment history.</p>
                            </div>
                        </div>
                        <div className="flex justify-end gap-2">
                            <Button variant="outline" onClick={() => {
                                setRejectDialog({open: false, payment: null});
                                setRejectionReason('');
                            }}>
                                Cancel
                            </Button>
                            <Button
                                variant="destructive"
                                disabled={actionLoading === rejectDialog.payment?.id || rejectionReason.trim().length < 5}
                                onClick={handleReject}
                            >
                                {actionLoading === rejectDialog.payment?.id ?
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                    <ShieldX className="mr-2 h-4 w-4"/>}
                                Reject Payment
                            </Button>
                        </div>
                    </DialogContent>
                </Dialog>

                <Dialog open={deleteConfirm.open} onOpenChange={open => {
                    if (!open) setDeleteConfirm({open: false, payment: null});
                }}>
                    <DialogContent>
                        <DialogHeader>
                            <DialogTitle>Remove Payment Record</DialogTitle>
                            <DialogDescription>
                                {canManage
                                    ? 'Are you sure you want to permanently delete this payment record? This cannot be undone.'
                                    : 'Are you sure you want to cancel this pending payment? This cannot be undone.'}
                            </DialogDescription>
                        </DialogHeader>
                        {deleteConfirm.payment && (
                            <div className="text-sm text-muted-foreground border rounded p-3 bg-muted/40 space-y-1">
                                <p><span className="font-medium">Receipt:</span> {deleteConfirm.payment.receipt_number}
                                </p>
                                <p><span
                                    className="font-medium">Amount:</span> {formatCurrency(deleteConfirm.payment.amount)}
                                </p>
                                <p><span className="font-medium">Status:</span> {deleteConfirm.payment.status}</p>
                            </div>
                        )}
                        <div className="flex justify-end gap-2">
                            <Button variant="outline" onClick={() => setDeleteConfirm({open: false, payment: null})}>
                                Keep It
                            </Button>
                            <Button
                                variant="destructive"
                                disabled={actionLoading === deleteConfirm.payment?.id}
                                onClick={() => handleDelete(deleteConfirm.payment)}
                            >
                                {actionLoading === deleteConfirm.payment?.id ?
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                    <Trash2 className="mr-2 h-4 w-4"/>}
                                {canManage ? 'Yes, Delete' : 'Yes, Remove'}
                            </Button>
                        </div>
                    </DialogContent>
                </Dialog>
            </Tabs>

            <PaymentModal
                isOpen={isPaymentModalOpen}
                onClose={() => setIsPaymentModalOpen(false)}
                paymentData={paymentData}
                onPaymentSuccess={handlePaymentSuccess}
            />

            {quarterPayData && (
                <RecordPaymentDialog
                    open={!!quarterPayOpen}
                    onOpenChange={open => {
                        if (!open) {
                            setQuarterPayOpen(null);
                            setQuarterPayData(null);
                        }
                    }}
                    title={`Record Strata Levy Payment — ${quarterPayData.quarter} ${levyStatus?.year}`}
                    description={`Record that you paid your ${quarterPayData.quarter} ${levyStatus?.year} strata levy outside the app (e.g. via DEFT or BPAY).`}
                    defaultAmount={quarterPayData.amount}
                    onSubmit={handleQuarterPayment}
                    submitting={quarterSubmitting}
                    paymentMethods={paymentMethods}
                />
            )}
        </div>
    );
};

export default LevyPaymentPage;
