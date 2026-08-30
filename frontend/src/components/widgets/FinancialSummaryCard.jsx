"use client";
import React from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { AlertCircle, ArrowRight, Calendar, CheckCircle2, DollarSign, TrendingDown, TrendingUp } from 'lucide-react';
import { formatCurrency, formatDate } from '../../lib/utils';
/**
 * Financial Summary Card Component
 * Displays owner's levy balance summary with Admin and Sinking fund details
 */
const FinancialSummaryCard = ({financialData, onPayClick, error}) => {
    if (error === 'not_found') {
        return (
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <DollarSign className="h-5 w-5"/>
                        Financial Summary
                    </CardTitle>
                    <CardDescription>No financial data available</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground space-y-2">
                        <AlertCircle className="h-12 w-12 mb-2 opacity-20"/>
                        <p className="text-center">
                            No financial records found for your unit.
                        </p>
                        <p className="text-sm text-center">
                            Financial data will be available once your first levy period is recorded.
                        </p>
                    </div>
                </CardContent>
            </Card>
        );
    }

    if (error === 'error') {
        return (
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <DollarSign className="h-5 w-5"/>
                        Financial Summary
                    </CardTitle>
                    <CardDescription>Unable to load financial data</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground space-y-2">
                        <AlertCircle className="h-12 w-12 mb-2 text-destructive opacity-50"/>
                        <p className="text-center">
                            There was a problem loading your financial data.
                        </p>
                        <p className="text-sm text-center">
                            Please try refreshing the page or contact support if the problem persists.
                        </p>
                    </div>
                </CardContent>
            </Card>
        );
    }

    if (!financialData) {
        return (
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <DollarSign className="h-5 w-5"/>
                        Financial Summary
                    </CardTitle>
                    <CardDescription>Loading financial data...</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-center py-8 text-muted-foreground">
                        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
                    </div>
                </CardContent>
            </Card>
        );
    }

    const {
        unit_number,
        admin_fund: _adminFundRaw,
        sinking_fund: _sinkingFundRaw,
        total_levied,
        total_paid,
        next_due_date,
    } = financialData;

    // GAP-DASH-001 Bug #4: /finance/unit-dashboard-overview returns balance_owing / balance_credit
    // and per-fund {annual, paid} — NOT the net_balance / opening_arrears / period_status /
    // *.levied / *.closing_balance shape this card was first written for, so every absent field
    // silently rendered $0.00 (formatCurrency(undefined) → "$0.00"). Map the current contract to
    // the names the render below uses. balance_owing (>0 owes) and balance_credit (>0 in credit)
    // collapse back to a signed net_balance; per-fund Levied = annual, Closing = annual − paid.
    const net_balance = financialData.net_balance
        ?? ((financialData.balance_owing ?? 0) - (financialData.balance_credit ?? 0));
    const opening_arrears = financialData.opening_arrears ?? net_balance;
    const next_payment_adjusted = financialData.next_payment_adjusted
        ?? financialData.next_payment_amount ?? 0;
    const period_levy = financialData.period_levy ?? 0;
    const _quarters = Array.isArray(financialData.quarters) ? financialData.quarters : [];
    const period_status = financialData.period_status
        ?? { any_overdue: _quarters.some((q) => q?.status === 'overdue') };
    const admin_fund = {
        ...(_adminFundRaw || {}),
        levied: _adminFundRaw?.levied ?? _adminFundRaw?.annual ?? 0,
        closing_balance: _adminFundRaw?.closing_balance
            ?? ((_adminFundRaw?.annual ?? 0) - (_adminFundRaw?.paid ?? 0)),
    };
    const sinking_fund = {
        ...(_sinkingFundRaw || {}),
        levied: _sinkingFundRaw?.levied ?? _sinkingFundRaw?.annual ?? 0,
        closing_balance: _sinkingFundRaw?.closing_balance
            ?? ((_sinkingFundRaw?.annual ?? 0) - (_sinkingFundRaw?.paid ?? 0)),
    };
    // Determine status and styling based on true carry-forward (opening_arrears) + period status
    /**
     * @generated FunctionHeader
     * Function: getStatusConfig
     * Path: frontend/src/components/widgets/FinancialSummaryCard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStatusConfig = () => {
        const arrears = opening_arrears ?? 0;
        const anyOverdue = period_status?.any_overdue ?? false;
        // Use the owner-specific next payment date (backend-computed, accounts for advance payments).
        // period_status.current_due_date is the building's next calendar period — irrelevant for
        // owners who have already pre-paid that period (e.g. UA063 paid 3 quarters, next = Dec 1).
        const currentDueDate = next_due_date ?? period_status?.current_due_date;

        if (arrears < -0.01) {
            return {
                label: 'In Credit',
                variant: 'default',
                className: 'bg-green-100 text-green-800 hover:bg-green-100',
                icon: TrendingDown,
                iconClass: 'text-green-600'
            };
        }

        if (anyOverdue && arrears > 0.01) {
            return {
                label: 'Overdue',
                variant: 'destructive',
                className: 'bg-red-100 text-red-800 hover:bg-red-100',
                icon: AlertCircle,
                iconClass: 'text-red-600'
            };
        }

        if (currentDueDate) {
            const daysUntil = Math.ceil(
                ( new Date(currentDueDate + 'T00:00:00') - new Date() ) / ( 1000 * 60 * 60 * 24 )
            );
            if (daysUntil <= 0) {
                // Past due date but within grace period
                return {
                    label: 'Grace Period',
                    variant: 'outline',
                    className: 'bg-orange-50 text-orange-800 border-orange-300',
                    icon: AlertCircle,
                    iconClass: 'text-orange-600'
                };
            }
            return {
                label: `Due in ${daysUntil} day${daysUntil === 1 ? '' : 's'}`,
                variant: 'outline',
                className: 'bg-yellow-50 text-yellow-800 border-yellow-300',
                icon: Calendar,
                iconClass: 'text-yellow-600'
            };
        }

        return {
            label: 'Paid Up',
            variant: 'outline',
            className: 'bg-blue-50 text-blue-700 border-blue-200',
            icon: TrendingUp,
            iconClass: 'text-blue-600'
        };
    };

    const statusConfig = getStatusConfig();
    const StatusIcon = statusConfig.icon;

    return (
        <Card className="card-dashboard">
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <DollarSign className="h-5 w-5"/>
                            Unit {unit_number} - Financial Summary
                        </CardTitle>
                        <CardDescription>Current levy position and fund balances</CardDescription>
                    </div>
                    <Badge variant={statusConfig.variant} className={statusConfig.className}>
                        <StatusIcon className={`h-3 w-3 mr-1 ${statusConfig.iconClass}`}/>
                        {statusConfig.label}
                    </Badge>
                </div>
            </CardHeader>
            <CardContent className="space-y-6">
                {/* Net Balance - Primary Info */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div
                        className="flex items-center justify-between p-4 rounded-lg bg-gradient-to-r from-primary/5 to-primary/10 border border-primary/20">
                        <div>
                            <p className="text-sm text-muted-foreground">Current Balance</p>
                            <p className={`text-3xl font-bold ${net_balance > 0 ? 'text-red-600' : net_balance < 0 ? 'text-green-600' : 'text-blue-600'}`}>
                                {formatCurrency(Math.abs(net_balance))}
                                {net_balance < 0 && <span className="text-sm font-normal ml-2">CR</span>}
                            </p>
                        </div>
                        {net_balance > 0 && onPayClick && (
                            <Button onClick={onPayClick} size="sm" variant="outline" className="gap-1 md:hidden">
                                <DollarSign className="h-4 w-4"/>
                                Pay
                            </Button>
                        )}
                    </div>

                    <div
                        className="flex items-center justify-between p-4 rounded-lg bg-primary/5 border border-primary/10">
                        <div className="flex-1">
                            <p className="text-sm text-muted-foreground">Next Estimated Payment</p>
                            <p className="text-3xl font-bold text-primary">
                                {formatCurrency(next_payment_adjusted || 0)}
                            </p>
                            {( () => {
                                const pl = period_levy || 0;
                                const oa = opening_arrears ?? 0;
                                const paid = total_paid || 0;
                                const nextAmt = next_payment_adjusted || 0;
                                const netBal = net_balance ?? 0;
                                const isOverdue = period_status?.any_overdue;
                                // period_status.any_overdue is calendar-based — it fires once a due
                                // date passes regardless of payment state.  Use net_balance as the
                                // authoritative "owner actually owes money" signal (mirrors the
                                // backend's own "Fully Paid" badge logic: net_balance <= 0).
                                const isFullyPaid = nextAmt < 0.01 && ( netBal <= 0 || !isOverdue );

                                // Priority 1 — fully paid for the year
                                if (isFullyPaid) {
                                    return (
                                        <p className="text-xs text-emerald-600 mt-1 flex items-center gap-1">
                                            <CheckCircle2 className="h-3 w-3"/>
                                            All levies fully paid for this financial year
                                        </p>
                                    );
                                }

                                // Priority 2 — owner has a prior-year credit.
                                // any_overdue is calendar-based (true whenever a due date has passed)
                                // so it fires for credit owners too — check credit BEFORE overdue.
                                if (oa < -0.01) {
                                    return (
                                        <p className="text-xs text-emerald-600 mt-1 flex items-center gap-1">
                                            <ArrowRight className="h-3 w-3"/>
                                            {formatCurrency(Math.abs(oa))} prior-year credit applied to next payment
                                        </p>
                                    );
                                }

                                // Priority 3 — overdue AND genuinely owing (net_balance > 0).
                                // Guard: skip entirely if net_balance <= 0 — the owner is paid up or
                                // in credit from current-year payments; any_overdue is calendar noise.
                                if (isOverdue && netBal > 0.01) {
                                    if (oa > 0.01) {
                                        const overdueAmt = financialData.owner_unit?.carry_forward_arrears ?? ( pl > 0.01 ? Math.max(0, nextAmt - pl) : 0 );
                                        return (
                                            <p className="text-xs text-rose-600 mt-1 flex items-center gap-1">
                                                <ArrowRight className="h-3 w-3"/>
                                                {overdueAmt > 0.01
                                                    ? `${formatCurrency(overdueAmt)} overdue from prior period included`
                                                    : `${formatCurrency(oa)} prior-year arrears included in payment`}
                                            </p>
                                        );
                                    }
                                    // After-grace rollup: backend adds the next period on top of the
                                    // overdue shortfall, so nextAmt > pl signals a bundled missed period.
                                    if (nextAmt > pl + 0.01) {
                                        return (
                                            <p className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                                                <ArrowRight className="h-3 w-3"/>
                                                Missed instalment included — payment overdue
                                            </p>
                                        );
                                    }
                                }

                                // Priority 4 — contextual labels (can stack).
                                // oa >= 0 is guaranteed (credit case handled at Priority 2).
                                // Backend contract: funding_base = effective_total_paid (no prior_year_credit term
                                // when oa >= 0). Prior-year arrears stay additive on top; they do not reduce
                                // the count of funded current-year periods.
                                const fundingBase = paid;
                                const periodsFullyFunded = pl > 0 ? Math.floor(Math.max(0, fundingBase) / pl) : 0;
                                const partialCredit = pl > 0 ? Math.max(0, fundingBase) - periodsFullyFunded * pl : 0;
                                const inYearPortion = partialCredit;

                                const labels = [];

                                // a) Prior-year arrears carried forward (not yet calendar-overdue)
                                if (oa > 0.01) {
                                    labels.push(
                                        <p key="arrears"
                                           className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                                            <ArrowRight className="h-3 w-3"/>
                                            {formatCurrency(oa)} prior-year arrears carried forward
                                        </p>
                                    );
                                }

                                // b) In-year advance payment reducing this period's instalment
                                if (inYearPortion > 0.01) {
                                    labels.push(
                                        <p key="advance" className="text-xs text-blue-600 mt-1 flex items-center gap-1">
                                            <ArrowRight className="h-3 w-3"/>
                                            {formatCurrency(inYearPortion)} advance payment credited
                                        </p>
                                    );
                                }

                                // c) Current instalments paid on time — no arrears, no credit,
                                // not overdue.  net_balance > 0 is normal here (future quarters
                                // are still owing) but nothing currently due has been missed.
                                if (labels.length === 0 && !isOverdue) {
                                    labels.push(
                                        <p key="paid" className="text-xs text-emerald-600 mt-1 flex items-center gap-1">
                                            <CheckCircle2 className="h-3 w-3"/>
                                            Levy amount paid in full
                                        </p>
                                    );
                                }

                                return labels.length > 0 ? <>{labels}</> : null;
                            } )()}
                        </div>
                        {onPayClick && (
                            <Button onClick={onPayClick} size="lg" className="gap-2 hidden md:flex ml-3">
                                <DollarSign className="h-4 w-4"/>
                                Pay Now
                            </Button>
                        )}
                    </div>
                </div>

                {/* Fund Breakdown */}
                <div className="grid grid-cols-2 gap-4">
                    {/* Admin Fund */}
                    <div className="space-y-2">
                        <p className="text-sm font-semibold text-muted-foreground">Administrative Fund</p>
                        <div className="space-y-1">
                            <div className="flex justify-between text-sm">
                                <span className="text-muted-foreground">Opening:</span>
                                <span className="font-medium">{formatCurrency(admin_fund?.opening_balance || 0)}</span>
                            </div>
                            <div className="flex justify-between text-sm">
                                <span className="text-muted-foreground">Levied:</span>
                                <span className="font-medium">{formatCurrency(admin_fund?.levied || 0)}</span>
                            </div>
                            {admin_fund?.special_levy > 0 && (
                                <div className="flex justify-between text-sm">
                                    <span className="text-muted-foreground">Special:</span>
                                    <span
                                        className="font-medium text-orange-600">{formatCurrency(admin_fund.special_levy)}</span>
                                </div>
                            )}
                            <div className="flex justify-between text-sm">
                                <span className="text-muted-foreground">Paid:</span>
                                <span
                                    className="font-medium text-green-600">{formatCurrency(admin_fund?.paid || 0)}</span>
                            </div>
                            <div className="flex justify-between text-sm font-semibold pt-1 border-t">
                                <span>Closing:</span>
                                <span className={admin_fund?.closing_balance > 0 ? 'text-red-600' : 'text-green-600'}>
                  {formatCurrency(Math.abs(admin_fund?.closing_balance || 0))}
                                    {admin_fund?.closing_balance < 0 && ' CR'}
                </span>
                            </div>
                        </div>
                    </div>

                    {/* Sinking Fund */}
                    <div className="space-y-2">
                        <p className="text-sm font-semibold text-muted-foreground">Sinking Fund</p>
                        <div className="space-y-1">
                            <div className="flex justify-between text-sm">
                                <span className="text-muted-foreground">Opening:</span>
                                <span
                                    className="font-medium">{formatCurrency(sinking_fund?.opening_balance || 0)}</span>
                            </div>
                            <div className="flex justify-between text-sm">
                                <span className="text-muted-foreground">Levied:</span>
                                <span className="font-medium">{formatCurrency(sinking_fund?.levied || 0)}</span>
                            </div>
                            {sinking_fund?.special_levy > 0 && (
                                <div className="flex justify-between text-sm">
                                    <span className="text-muted-foreground">Special:</span>
                                    <span
                                        className="font-medium text-orange-600">{formatCurrency(sinking_fund.special_levy)}</span>
                                </div>
                            )}
                            <div className="flex justify-between text-sm">
                                <span className="text-muted-foreground">Paid:</span>
                                <span
                                    className="font-medium text-green-600">{formatCurrency(sinking_fund?.paid || 0)}</span>
                            </div>
                            <div className="flex justify-between text-sm font-semibold pt-1 border-t">
                                <span>Closing:</span>
                                <span className={sinking_fund?.closing_balance > 0 ? 'text-red-600' : 'text-green-600'}>
                  {formatCurrency(Math.abs(sinking_fund?.closing_balance || 0))}
                                    {sinking_fund?.closing_balance < 0 && ' CR'}
                </span>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Summary Stats */}
                <div className="grid grid-cols-3 gap-4 pt-4 border-t">
                    <div className="text-center">
                        <p className="text-xs text-muted-foreground mb-1">Levied to date</p>
                        <p className="text-lg font-semibold">{formatCurrency(total_levied || 0)}</p>
                    </div>
                    <div className="text-center">
                        {/* GAP-DASH-001 L1: use paid_this_year (year-scoped), NOT the cumulative
                            back-solved total_paid which can be several years' worth of payments and
                            reads nonsensically beside the YTD "Levied to date". */}
                        <p className="text-xs text-muted-foreground mb-1">Paid this year</p>
                        <p className="text-lg font-semibold text-green-600">{formatCurrency(financialData.paid_this_year ?? total_paid ?? 0)}</p>
                    </div>
                    {next_due_date && (
                        <div className="text-center">
                            <p className="text-xs text-muted-foreground mb-1">Next Due Date</p>
                            <p className="text-sm font-semibold flex items-center justify-center gap-1">
                                <Calendar className="h-3 w-3"/>
                                {formatDate(next_due_date)}
                            </p>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
};

export default FinancialSummaryCard;
