// @ts-nocheck
// @featuretrace:insurance — insurance risk signals and per-unit lending/valuation signals.
// Layer: frontend
// Data flow: InsuranceLendingPage → GET /intelligence/insurance/risk-signals,
//            /intelligence/lending/valuation-signals/{unit} → insurance_policies + units (building-scoped).
// Related: backend/routers/intelligence.py
//           frontend/src/hooks/useActiveUnit.ts (the unit valuation signals are drawn for)
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import {
    AlertTriangle,
    Building2,
    CheckCircle,
    Info,
    Landmark,
    Loader2,
    RefreshCw,
    Shield,
    TrendingDown
} from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Alert, AlertDescription } from '../../components/ui/alert';

const ALLOWED_ROLES = ['super_admin', 'strata_manager'];
// Risk tier config: insurance
/**
 * @generated FunctionHeader
 * Function: insuranceTierConfig
 * Path: frontend/src/pages/dashboard/InsuranceLendingPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function insuranceTierConfig(tier) {
    switch (tier) {
        case 'LOW':
            return {
                badge: 'bg-green-100 text-green-800 border-green-200',
                border: 'border-green-200',
                bg: 'bg-green-50',
                icon: <CheckCircle className="h-8 w-8 text-green-600"/>,
                label: 'Low Risk'
            };
        case 'MODERATE':
            return {
                badge: 'bg-amber-100 text-amber-800 border-amber-200',
                border: 'border-amber-200',
                bg: 'bg-amber-50',
                icon: <AlertTriangle className="h-8 w-8 text-amber-500"/>,
                label: 'Moderate Risk'
            };
        case 'HIGH':
            return {
                badge: 'bg-orange-100 text-orange-800 border-orange-200',
                border: 'border-orange-200',
                bg: 'bg-orange-50',
                icon: <AlertTriangle className="h-8 w-8 text-orange-500"/>,
                label: 'High Risk'
            };
        case 'CRITICAL':
            return {
                badge: 'bg-red-100 text-red-800 border-red-200',
                border: 'border-red-200',
                bg: 'bg-red-50',
                icon: <AlertTriangle className="h-8 w-8 text-red-600"/>,
                label: 'Critical Risk'
            };
        default:
            return {
                badge: 'bg-muted text-foreground border-border',
                border: 'border-border',
                bg: 'bg-muted',
                icon: <Info className="h-8 w-8 text-muted-foreground"/>,
                label: 'Unknown'
            };
    }
}
// Lending tier config
/**
 * @generated FunctionHeader
 * Function: lendingTierConfig
 * Path: frontend/src/pages/dashboard/InsuranceLendingPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function lendingTierConfig(tier) {
    switch (tier) {
        case 'PRIME':
            return {
                badge: 'bg-green-100 text-green-800 border-green-200',
                border: 'border-green-200',
                bg: 'bg-green-50',
                icon: <CheckCircle className="h-8 w-8 text-green-600"/>,
                label: 'Prime'
            };
        case 'STANDARD':
            return {
                badge: 'bg-primary/10 text-primary border-primary/20',
                border: 'border-primary/20',
                bg: 'bg-primary/10',
                icon: <Building2 className="h-8 w-8 text-primary"/>,
                label: 'Standard'
            };
        case 'CAUTION':
            return {
                badge: 'bg-amber-100 text-amber-800 border-amber-200',
                border: 'border-amber-200',
                bg: 'bg-amber-50',
                icon: <AlertTriangle className="h-8 w-8 text-amber-500"/>,
                label: 'Caution'
            };
        case 'DECLINE':
            return {
                badge: 'bg-red-100 text-red-800 border-red-200',
                border: 'border-red-200',
                bg: 'bg-red-50',
                icon: <TrendingDown className="h-8 w-8 text-red-600"/>,
                label: 'Decline Risk'
            };
        default:
            return {
                badge: 'bg-muted text-foreground border-border',
                border: 'border-border',
                bg: 'bg-muted',
                icon: <Info className="h-8 w-8 text-muted-foreground"/>,
                label: 'Unknown'
            };
    }
}
/**
 * @generated FunctionHeader
 * Function: ScoreRing
 * Path: frontend/src/pages/dashboard/InsuranceLendingPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ScoreRing({score, max = 100, color = '#3b82f6', size = 80}) {
    const radius = ( size - 10 ) / 2;
    const circumference = 2 * Math.PI * radius;
    const capped = Math.min(max, Math.max(0, score));
    const offset = circumference - ( capped / max ) * circumference;
    return (
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
            <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#e5e7eb" strokeWidth="8"/>
            <circle
                cx={size / 2} cy={size / 2} r={radius} fill="none"
                stroke={color} strokeWidth="8"
                strokeDasharray={circumference} strokeDashoffset={offset}
                strokeLinecap="round"
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
                style={{transition: 'stroke-dashoffset 0.5s ease'}}
            />
            <text x={size / 2} y={size / 2 + 5} textAnchor="middle" fontSize="16" fontWeight="bold" fill={color}>
                {score}
            </text>
        </svg>
    );
}
/**
 * @generated FunctionHeader
 * Function: SignalsList
 * Path: frontend/src/pages/dashboard/InsuranceLendingPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SignalsList({signals}) {
    if (!signals || signals.length === 0) {
        return (
            <div className="flex items-center gap-2 text-sm text-green-700 py-2">
                <CheckCircle className="h-4 w-4 text-green-500"/>
                No adverse signals detected
            </div>
        );
    }
    return (
        <ul className="space-y-2">
            {signals.map((signal, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-foreground">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-500"/>
                    <span>{signal}</span>
                </li>
            ))}
        </ul>
    );
}
/**
 * @generated FunctionHeader
 * Function: InsuranceLendingPage
 * Path: frontend/src/pages/dashboard/InsuranceLendingPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InsuranceLendingPage = () => {
    const {user, api} = useAuth();
    const {activeUnit} = useActiveUnit();
    const router = useRouter();

    const [insuranceData, setInsuranceData] = useState(null);
    const [lendingData, setLendingData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);

    const canAccess = user && ALLOWED_ROLES.includes(user.role);

    const fetchData = useCallback(async () => {
        setRefreshing(true);
        try {
            const unitNumber = activeUnit;
            const requests = [
                api.get('/intelligence/insurance/risk-signals'),
                unitNumber
                    ? api.get(`/intelligence/lending/valuation-signals/${unitNumber}`)
                    : Promise.resolve(null),
            ];
            const [insRes, lendRes] = await Promise.allSettled(requests);
            if (insRes.status === 'fulfilled') setInsuranceData(insRes.value.data);
            else setInsuranceData(null);
            if (lendRes.status === 'fulfilled' && lendRes.value !== null) setLendingData(lendRes.value.data);
            else setLendingData(null);
        } catch {
            toast.error('Failed to load insurance and lending data');
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [api, activeUnit]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchData();
    }, [canAccess, fetchData, router]);

    if (!canAccess) return null;

    const insCfg = insuranceTierConfig(insuranceData?.risk_tier);
    const lendCfg = lendingTierConfig(lendingData?.lending_tier);

    // Aggregate criticality for top banner
    const hasCritical = insuranceData?.risk_tier === 'CRITICAL' || lendingData?.lending_tier === 'DECLINE';
    const hasWarning = !hasCritical && (
        ['HIGH', 'MODERATE'].includes(insuranceData?.risk_tier) ||
        ['CAUTION'].includes(lendingData?.lending_tier)
    );

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                        <Shield className="h-6 w-6 text-primary"/>
                        Insurance &amp; Lending Risk Signals
                    </h1>
                    <p className="text-sm text-muted-foreground mt-0.5">
                        Building-level risk assessment for insurers and mortgage lenders
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchData} disabled={refreshing}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>
                    Refresh
                </Button>
            </div>

            {loading ? (
                <div className="flex justify-center py-24">
                    <Loader2 className="h-10 w-10 animate-spin text-muted-foreground"/>
                </div>
            ) : (
                <>
                    {/* Critical / Warning Banner */}
                    {hasCritical && (
                        <Alert className="border-red-200 bg-red-50">
                            <AlertTriangle className="h-4 w-4 text-red-600"/>
                            <AlertDescription className="text-red-800">
                                <strong>Critical risk level detected.</strong> Immediate action is required to address
                                insurance and/or lending concerns. This may affect the building's ability to obtain
                                affordable coverage or financing.
                            </AlertDescription>
                        </Alert>
                    )}
                    {hasWarning && !hasCritical && (
                        <Alert className="border-amber-200 bg-amber-50">
                            <AlertTriangle className="h-4 w-4 text-amber-600"/>
                            <AlertDescription className="text-amber-800">
                                <strong>Elevated risk signals present.</strong> Review the details below and consider
                                remediation steps to improve the building's risk profile.
                            </AlertDescription>
                        </Alert>
                    )}

                    {/* Side-by-side Cards */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                        {/* Insurance Risk Card */}
                        <Card className={`border-2 ${insCfg.border}`}>
                            <CardHeader className={`rounded-t-lg ${insCfg.bg} pb-3`}>
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-base flex items-center gap-2">
                                        <Shield className="h-5 w-5 text-primary"/>
                                        Insurance Risk
                                    </CardTitle>
                                    <span
                                        className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold border ${insCfg.badge}`}>
                    {insuranceData?.risk_tier ?? 'N/A'}
                  </span>
                                </div>
                                <CardDescription className="text-xs">
                                    Assessment of the building's insurability profile
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="pt-5 space-y-5">
                                {insuranceData ? (
                                    <>
                                        {/* Score + tier label */}
                                        <div className="flex items-center gap-4">
                                            <ScoreRing
                                                score={insuranceData.risk_score ?? 0}
                                                color={
                                                    insuranceData.risk_tier === 'LOW' ? '#16a34a' :
                                                        insuranceData.risk_tier === 'MODERATE' ? '#d97706' :
                                                            insuranceData.risk_tier === 'HIGH' ? '#ea580c' : '#dc2626'
                                                }
                                            />
                                            <div>
                                                <p className="text-2xl font-bold text-foreground">{insCfg.label}</p>
                                                <p className="text-sm text-muted-foreground">Risk
                                                    Score: {insuranceData.risk_score ?? '—'}/100</p>
                                            </div>
                                        </div>
                                        <div>
                                            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Risk
                                                Signals</p>
                                            <SignalsList signals={insuranceData.signals}/>
                                        </div>
                                    </>
                                ) : (
                                    <p className="text-sm text-muted-foreground py-4 text-center">No insurance risk data
                                        available.</p>
                                )}
                            </CardContent>
                        </Card>

                        {/* Lending Valuation Card */}
                        <Card className={`border-2 ${lendCfg.border}`}>
                            <CardHeader className={`rounded-t-lg ${lendCfg.bg} pb-3`}>
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-base flex items-center gap-2">
                                        <Landmark className="h-5 w-5 text-primary"/>
                                        Lending Valuation
                                    </CardTitle>
                                    <span
                                        className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold border ${lendCfg.badge}`}>
                    {lendingData?.lending_tier ?? 'N/A'}
                  </span>
                                </div>
                                <CardDescription className="text-xs">
                                    Lender's perspective on building financial health
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="pt-5 space-y-5">
                                {lendingData ? (
                                    <>
                                        {/* Tier + Discount */}
                                        <div className="flex items-center gap-4">
                                            {lendCfg.icon}
                                            <div>
                                                <p className="text-2xl font-bold text-foreground">{lendCfg.label}</p>
                                                {lendingData.discount_bps != null && lendingData.discount_bps !== 0 ? (
                                                    <p className="text-sm text-red-600 font-medium">
                                                        Discount: {lendingData.discount_bps > 0 ? '-' : '+'}{Math.abs(lendingData.discount_bps)} bps
                                                        on valuation
                                                    </p>
                                                ) : (
                                                    <p className="text-sm text-green-600 font-medium">No valuation
                                                        discount applied</p>
                                                )}
                                            </div>
                                        </div>
                                        <div>
                                            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Lending
                                                Signals</p>
                                            <SignalsList signals={lendingData.signals}/>
                                        </div>
                                    </>
                                ) : (
                                    <p className="text-sm text-muted-foreground py-4 text-center">No lending valuation data
                                        available.</p>
                                )}
                            </CardContent>
                        </Card>
                    </div>

                    {/* Explainer */}
                    <Card className="bg-muted border-border">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm flex items-center gap-2 text-foreground">
                                <Info className="h-4 w-4"/>
                                What these signals mean
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="text-sm text-muted-foreground space-y-3">
                            <div>
                                <p className="font-medium text-foreground">Insurance Risk Tiers</p>
                                <div className="mt-1 grid grid-cols-2 md:grid-cols-4 gap-2">
                                    {['LOW', 'MODERATE', 'HIGH', 'CRITICAL'].map(tier => {
                                        const cfg = insuranceTierConfig(tier);
                                        return (
                                            <div key={tier}
                                                 className={`rounded-md px-2 py-1 text-xs font-medium border ${cfg.badge}`}>
                                                <span className="font-bold">{tier}:</span>{' '}
                                                {tier === 'LOW' && 'Standard premiums expected'}
                                                {tier === 'MODERATE' && 'Possible loading on premium'}
                                                {tier === 'HIGH' && 'Significant loading or exclusions'}
                                                {tier === 'CRITICAL' && 'Coverage may be refused or unaffordable'}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                            <div>
                                <p className="font-medium text-foreground">Lending Valuation Tiers</p>
                                <div className="mt-1 grid grid-cols-2 md:grid-cols-4 gap-2">
                                    {['PRIME', 'STANDARD', 'CAUTION', 'DECLINE'].map(tier => {
                                        const cfg = lendingTierConfig(tier);
                                        return (
                                            <div key={tier}
                                                 className={`rounded-md px-2 py-1 text-xs font-medium border ${cfg.badge}`}>
                                                <span className="font-bold">{tier}:</span>{' '}
                                                {tier === 'PRIME' && 'Full LVR lending available'}
                                                {tier === 'STANDARD' && 'Normal lending conditions'}
                                                {tier === 'CAUTION' && 'Reduced LVR or conditions apply'}
                                                {tier === 'DECLINE' && 'Lenders likely to decline or add large discount'}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                            <p className="text-xs text-muted-foreground pt-1">
                                Signals are derived from reserve adequacy, arrears rates, compliance status, deferred
                                maintenance, and capital works planning data. Consult your strata insurer and financier
                                for binding advice.
                            </p>
                        </CardContent>
                    </Card>
                </>
            )}
        </div>
    );
};

export default InsuranceLendingPage;
