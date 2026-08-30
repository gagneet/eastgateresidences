// @featuretrace:land-tax — ACT Land Tax card: shows land tax assessment for investment/rental units.
// Layer: frontend
// Data flow: LandTaxCard.jsx → /council-rates/{unit} + /units/{unit}/occupants → ACT Revenue API (24h cache),
//            db.council_rates_cache, db.units (building-scoped).
// Related: backend/routers/council_rates.py
//           backend/services/tax_service.py
//           frontend/src/pages/dashboard/OwnerDashboard.tsx  (@featuretrace:fund-health)

"use client";
import React, { useEffect, useState, useCallback } from 'react';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Skeleton } from '../ui/skeleton';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogFooter,
} from '../ui/dialog';
import {
    Receipt,
    Info,
    CheckCircle,
    AlertCircle,
    Clock,
    RefreshCw,
    XCircle,
    Users,
    DollarSign,
    Home,
    ShieldOff
} from 'lucide-react';
import { formatCurrency, formatDate } from '../../lib/utils';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: LandTaxCard
 * Path: frontend/src/components/dashboard/LandTaxCard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const LandTaxCard = ({unitNumber: propUnitNumber}) => {
    const {api, hasPermission} = useAuth();
    const {activeUnit} = useActiveUnit();
    // Fall back to the sidebar's active unit, not the account default, so a
    // multi-unit owner's switch reaches cards rendered without an explicit prop.
    const unitNumber = propUnitNumber || activeUnit;

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [hasTenant, setHasTenant] = useState(false);
    const [isOwnerOccupied, setIsOwnerOccupied] = useState(true);
    const [payDialogOpen, setPayDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);

    const [paymentForm, setPaymentForm] = useState({
        amount: '',
        date: new Date().toISOString().slice(0, 10),
        reference: ''
    });

    const canManage = hasPermission('can_manage_finances');

    const fetchData = useCallback(async () => {
        if (!unitNumber) {
            setLoading(false);
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const [ratesRes, occupantsRes] = await Promise.all([
                api.get(`/council-rates/${unitNumber}`),
                api.get(`/units/${unitNumber}/occupants`)
            ]);
            setData(ratesRes.data);

            const occupants = occupantsRes.data.occupants || [];
            setHasTenant(occupants.some(o => o.role === 'tenant'));

            // Derive owner-occupied from API response or occupant list
            const apiIsOwnerOccupied = ratesRes.data?.is_owner_occupied ?? true;
            setIsOwnerOccupied(apiIsOwnerOccupied);

            if (ratesRes.data?.land_tax_total) {
                setPaymentForm(prev => ( {...prev, amount: ratesRes.data.land_tax_total.toString()} ));
            }
        } catch (err) {
            console.error(err);
            setError('Failed to load land tax details');
        } finally {
            setLoading(false);
        }
    }, [api, unitNumber]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    /**
     * @generated FunctionHeader
     * Function: handlePay
     * Path: frontend/src/components/dashboard/LandTaxCard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePay = async () => {
        const amount = parseFloat(paymentForm.amount);
        if (!amount || amount <= 0) {
            toast.error('Please enter a valid amount');
            return;
        }

        setSubmitting(true);
        try {
            await api.post(`/council-rates/${unitNumber}/payments`, {
                amount: amount,
                payment_type: 'land_tax',
                reference: paymentForm.reference || undefined,
                payment_date: paymentForm.date,
                financial_year: data?.financial_year || '2025-26',
            });
            toast.success('Land tax payment recorded successfully');
            setPayDialogOpen(false);
            fetchData();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to record payment');
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) return <Skeleton className="h-64 w-full rounded-xl" aria-label="Loading land tax card"/>;

    if (!unitNumber) return null;

    const landTaxTotal = data?.land_tax_total || 0;
    const isApplicable = data?.land_tax_applicable || ( !isOwnerOccupied && landTaxTotal > 0 );
    // Investment property = not owner-occupied (land tax applies)
    const isInvestment = !isOwnerOccupied;

    return (
        <>
            <Card className="border shadow-sm overflow-hidden bg-white" role="region" aria-label="ACT Land Tax">
                <CardHeader className="pb-3 bg-slate-50/50">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <div className="p-2 rounded-xl bg-blue-100 text-blue-600" aria-hidden="true">
                                <Receipt size={18}/>
                            </div>
                            <div>
                                <CardTitle className="text-base font-bold text-slate-800">ACT Land Tax</CardTitle>
                                <CardDescription className="text-xs">Investment / Rental Property Tax</CardDescription>
                            </div>
                        </div>
                        {isOwnerOccupied ? (
                            <Badge variant="outline" className="bg-slate-50 text-slate-500 border-slate-200"
                                   aria-label="Owner-occupied — land tax exempt">
                                Owner-Occupied
                            </Badge>
                        ) : isApplicable ? (
                            <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200"
                                   aria-label="Land tax applicable">
                                Applicable
                            </Badge>
                        ) : null}
                    </div>
                </CardHeader>

                <CardContent className="pt-4 space-y-4">
                    {/* Key information note */}
                    <div className="bg-amber-50 border border-amber-100 p-3 rounded-xl flex gap-3" role="note">
                        <Info size={16} className="text-amber-500 shrink-0 mt-0.5" aria-hidden="true"/>
                        <p className="text-xs text-amber-800 leading-relaxed">
                            <strong>Note:</strong> Land Tax <strong>only applies</strong> to investment properties where
                            a tenant resides.
                            Owner-occupied residences are <strong>exempt</strong> from ACT Land Tax.
                        </p>
                    </div>

                    {/* Scenario 1: Owner-occupied — no land tax, hide amounts */}
                    {isOwnerOccupied && (
                        <div
                            className="py-5 text-center border-2 border-dashed border-emerald-100 rounded-xl bg-emerald-50/30"
                            role="status" aria-label="Land tax exempt">
                            <Home className="mx-auto h-9 w-9 text-emerald-300 mb-2" aria-hidden="true"/>
                            <p className="text-sm font-semibold text-emerald-700">Owner-Occupied — Land Tax Exempt</p>
                            <p className="text-xs text-emerald-600 mt-1">
                                This unit is recorded as owner-occupied. ACT Land Tax does not apply.
                            </p>
                            <p className="text-[10px] text-slate-400 mt-2">
                                If this is incorrect, please contact your strata manager to update unit occupancy
                                status.
                            </p>
                        </div>
                    )}

                    {/* Scenario 2: Investment property */}
                    {!isOwnerOccupied && (
                        <>
                            {/* Tenant status indicator */}
                            {hasTenant ? (
                                <div
                                    className="bg-emerald-50 border border-emerald-100 p-3 rounded-xl flex items-center gap-3"
                                    role="status">
                                    <Users size={16} className="text-emerald-500" aria-hidden="true"/>
                                    <p className="text-xs text-emerald-800 font-medium">Active tenant record detected
                                        for Unit {unitNumber}</p>
                                </div>
                            ) : (
                                <div
                                    className="bg-amber-50 border border-amber-200 p-3 rounded-xl flex items-start gap-3"
                                    role="alert">
                                    <AlertCircle size={16} className="text-amber-500 shrink-0 mt-0.5"
                                                 aria-hidden="true"/>
                                    <div>
                                        <p className="text-xs font-semibold text-amber-800">No active tenant
                                            registered</p>
                                        <p className="text-xs text-amber-700 mt-0.5">
                                            Land tax liability still applies as this is an investment property. Payment
                                            recording is disabled until a tenant is registered.
                                        </p>
                                    </div>
                                </div>
                            )}

                            {/* Land tax amounts */}
                            {landTaxTotal > 0 ? (
                                <div className={`space-y-3 ${!hasTenant ? 'opacity-70' : ''}`}
                                     aria-label={`Annual land tax: ${formatCurrency(landTaxTotal)}`}>
                                    <div className="flex justify-between items-end border-b pb-2 border-slate-100">
                                        <span className="text-sm text-slate-600 font-medium">Annual Assessment</span>
                                        <span
                                            className="text-xl font-black text-slate-900 tabular-nums">{formatCurrency(landTaxTotal)}</span>
                                    </div>

                                    <div className="grid grid-cols-2 gap-2">
                                        {[
                                            {label: 'Q1 Aug', amount: data.land_tax_q1},
                                            {label: 'Q2 Nov', amount: data.land_tax_q2},
                                            {label: 'Q3 Feb', amount: data.land_tax_q3},
                                            {label: 'Q4 May', amount: data.land_tax_q4},
                                        ].map(q => (
                                            <div
                                                key={q.label}
                                                className={`p-2 rounded-lg border ${q.amount ? 'bg-slate-50 border-slate-100' : 'bg-slate-50/30 border-dashed border-slate-200'}`}
                                                aria-label={`${q.label}: ${q.amount ? formatCurrency(q.amount) : 'Not yet due'}`}
                                            >
                                                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-tighter">{q.label}</p>
                                                <p className="text-sm font-bold text-slate-800 tabular-nums">{q.amount ? formatCurrency(q.amount) : '—'}</p>
                                            </div>
                                        ))}
                                    </div>

                                    {!hasTenant && (
                                        <p className="text-[10px] text-slate-400 italic">
                                            Amounts above reflect your land tax liability even without an active tenant.
                                        </p>
                                    )}
                                </div>
                            ) : (
                                <div className="py-4 text-center border-2 border-dashed border-slate-100 rounded-xl">
                                    <Receipt className="mx-auto h-8 w-8 text-slate-200 mb-2" aria-hidden="true"/>
                                    <p className="text-xs text-slate-400">Land tax figures not yet loaded for
                                        FY {data?.financial_year || '2025-26'}</p>
                                </div>
                            )}

                            <div className="pt-2">
                                <Button
                                    className="w-full font-bold rounded-xl shadow-lg shadow-indigo-100 transition-all active:scale-95"
                                    disabled={!hasTenant || !canManage}
                                    onClick={() => setPayDialogOpen(true)}
                                    aria-label={hasTenant ? 'Record land tax payment' : 'Payment disabled — no tenant registered'}
                                    data-testid="land-tax-pay-btn"
                                >
                                    <CheckCircle className="mr-2 h-4 w-4" aria-hidden="true"/>
                                    {hasTenant ? 'Record Land Tax Payment' : 'Payment Disabled (No Tenant)'}
                                </Button>
                                {!hasTenant && (
                                    <p className="text-[10px] text-center text-slate-400 mt-3 font-medium">
                                        Register a tenant via the Requests page to enable payment recording.
                                    </p>
                                )}
                            </div>
                        </>
                    )}
                </CardContent>
            </Card>

            <Dialog open={payDialogOpen} onOpenChange={setPayDialogOpen} aria-label="Record land tax payment">
                <DialogContent className="sm:max-w-md rounded-[1.5rem]">
                    <DialogHeader>
                        <DialogTitle>Record Land Tax Payment</DialogTitle>
                        <DialogDescription>
                            Confirm payment of ACT Land Tax for Unit {unitNumber}.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-3">
                        <div className="space-y-2">
                            <Label htmlFor="lt-amount">Payment Amount</Label>
                            <div className="relative">
                                <DollarSign className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400"
                                            aria-hidden="true"/>
                                <Input
                                    id="lt-amount"
                                    type="number"
                                    className="pl-9"
                                    value={paymentForm.amount}
                                    onChange={(e) => setPaymentForm({...paymentForm, amount: e.target.value})}
                                    aria-label="Payment amount in dollars"
                                    data-testid="lt-amount-input"
                                />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="lt-date">Payment Date</Label>
                            <Input
                                id="lt-date"
                                type="date"
                                value={paymentForm.date}
                                onChange={(e) => setPaymentForm({...paymentForm, date: e.target.value})}
                                aria-label="Payment date"
                            />
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="lt-ref">Reference (Optional)</Label>
                            <Input
                                id="lt-ref"
                                placeholder="e.g. Receipt #12345"
                                value={paymentForm.reference}
                                onChange={(e) => setPaymentForm({...paymentForm, reference: e.target.value})}
                                aria-label="Payment reference"
                            />
                        </div>
                    </div>

                    <DialogFooter className="sm:justify-end gap-2">
                        <Button variant="ghost" onClick={() => setPayDialogOpen(false)}
                                disabled={submitting}>Cancel</Button>
                        <Button
                            onClick={handlePay}
                            disabled={submitting}
                            className="bg-indigo-600 hover:bg-indigo-700"
                            data-testid="lt-confirm-payment"
                        >
                            {submitting ? <><RefreshCw className="mr-2 h-4 w-4 animate-spin"
                                                       aria-hidden="true"/> Recording...</> : 'Confirm Payment'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
};

export default LandTaxCard;
