// @featuretrace:capital-funding — Preview-only capital funding workspace for special levy and strata-loan modelling.
// Layer: frontend
// Data flow: CapitalFundingPage local calculator state → future /capital-funding preview APIs
//            → jurisdictional rule engine + canonical ownership snapshot + PDF preview batch service.
// Toggle: capital_funding_workspace, capital_funding_notice_preview

"use client";

import React, {useMemo, useState} from "react";
import {
    AlertTriangle,
    Calculator,
    CheckCircle2,
    FileText,
    Landmark,
    Scale,
    ShieldCheck,
} from "lucide-react";
import {Badge} from "../../components/ui/badge";
import {Button} from "../../components/ui/button";
import {Card, CardContent, CardHeader, CardTitle} from "../../components/ui/card";
import {Input} from "../../components/ui/input";
import {Label} from "../../components/ui/label";
import {useAuth} from "../../contexts/AuthContext";

import {formatMoneyFromDollars} from '@/lib/currency';
type LotPreview = {
    lot: string;
    entitlement: number;
    choice: "Upfront" | "Loan";
};

const SAMPLE_LOTS: LotPreview[] = [
    {lot: "1", entitlement: 120, choice: "Upfront"},
    {lot: "2", entitlement: 95, choice: "Loan"},
    {lot: "3", entitlement: 88, choice: "Loan"},
    {lot: "TH01", entitlement: 170, choice: "Loan"},
];

const JURISDICTION_SUMMARY: Record<string, string> = {
    ACT: "Special-purpose fund gate; unit entitlement default.",
    NSW: "30-day standard notice, 14-day emergency repair notice, hardship disclosure.",
    VIC: "28-day fee notice, lot liability default, benefit principle and tenant impacts tracked.",
    QLD: "30-day contribution notice, ordinary resolution, contribution schedule entitlement.",
    SA: "Unit entitlement default; no verified platform notice period, legal review required.",
    WA: "Unit entitlement subject to registered by-laws; no separate levy invoice period assumed.",
    TAS: "Ordinary resolution and written notice evidence; no verified platform notice period.",
    NT: "Corporation resolution and borrowing evidence; no verified platform notice period.",
};

// Whole dollars: this page shows capital-works magnitudes, where cents are noise.
const formatCurrency = (value: number) => formatMoneyFromDollars(value, {whole: true});

const toNumber = (value: string) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
};

export default function CapitalFundingPage() {
    const {hasFeatureAccess} = useAuth() as { hasFeatureAccess?: (featureKey: string) => boolean };
    const [jurisdiction, setJurisdiction] = useState("NSW");
    const [projectCost, setProjectCost] = useState("7500000");
    const [gstFees, setGstFees] = useState("280000");
    const [contingency, setContingency] = useState("650000");
    const [recoveries, setRecoveries] = useState("400000");
    const [reserveContribution, setReserveContribution] = useState("300000");
    const [interestRate, setInterestRate] = useState("6.2");
    const [termYears, setTermYears] = useState("7");
    const [legalReview, setLegalReview] = useState(false);
    const [motionApproved, setMotionApproved] = useState(false);
    const [ownershipSnapshot, setOwnershipSnapshot] = useState(false);
    const [noticeTemplate, setNoticeTemplate] = useState(false);

    const summary = useMemo(() => {
        const requirement = Math.max(
            0,
            toNumber(projectCost) + toNumber(gstFees) + toNumber(contingency)
            - toNumber(recoveries) - toNumber(reserveContribution),
        );
        const loanPrincipal = Math.round(requirement * 0.68);
        const upfrontTotal = requirement - loanPrincipal;
        const years = Math.max(1, toNumber(termYears));
        const rate = Math.max(0, toNumber(interestRate)) / 100;
        const interest = Math.round(loanPrincipal * rate * years);
        const totalEntitlement = SAMPLE_LOTS.reduce((sum, lot) => sum + lot.entitlement, 0);
        const paymentCount = years * 4;
        const lotRows = SAMPLE_LOTS.map((lot) => {
            const share = Math.round(requirement * lot.entitlement / totalEntitlement);
            const financed = lot.choice === "Loan" ? Math.round(loanPrincipal * lot.entitlement / totalEntitlement) : 0;
            const lotInterest = lot.choice === "Loan" ? Math.round(interest * lot.entitlement / totalEntitlement) : 0;
            return {
                ...lot,
                share,
                financed,
                interest: lotInterest,
                dueNow: lot.choice === "Upfront" ? share : 0,
                quarterly: lot.choice === "Loan" ? Math.round((financed + lotInterest) / paymentCount) : 0,
                total: lot.choice === "Loan" ? financed + lotInterest : share,
            };
        });
        return {requirement, loanPrincipal, upfrontTotal, interest, lotRows};
    }, [contingency, gstFees, interestRate, projectCost, recoveries, reserveContribution, termYears]);

    const noticePreviewEnabled = Boolean(hasFeatureAccess?.("capital_funding_notice_preview"));
    const readyForPreview = legalReview && motionApproved && ownershipSnapshot && noticeTemplate && noticePreviewEnabled;

    return (
        <div className="min-h-screen bg-slate-50">
            <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
                <header className="flex flex-col gap-3 border-b border-slate-200 pb-5 lg:flex-row lg:items-end lg:justify-between">
                    <div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">Planned/Pilot</Badge>
                            <Badge variant="outline" className="border-slate-300 bg-white text-slate-700">Preview only</Badge>
                        </div>
                        <h1 className="mt-3 text-2xl font-semibold text-slate-950">Capital Funding &amp; Special Levy Workspace</h1>
                        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
                            Model major works funding, strata-loan scenarios, owner elections and levy notice PDF
                            preview batches before any legal issuance or ledger posting gate is enabled.
                        </p>
                    </div>
                    <Button disabled={!readyForPreview} className="gap-2">
                        <FileText className="h-4 w-4"/>
                        Generate Levy Notice PDFs
                    </Button>
                </header>

                <section className="grid gap-4 lg:grid-cols-[1.2fr_.8fr]">
                    <Card className="border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <Calculator className="h-5 w-5 text-emerald-700"/>
                                Funding Calculator
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                            {[
                                ["Project cost", projectCost, setProjectCost],
                                ["GST and fees", gstFees, setGstFees],
                                ["Contingency", contingency, setContingency],
                                ["Recoveries and grants", recoveries, setRecoveries],
                                ["Existing fund contribution", reserveContribution, setReserveContribution],
                                ["Loan term years", termYears, setTermYears],
                                ["Interest rate percent", interestRate, setInterestRate],
                            ].map(([label, value, setter]) => (
                                <div key={label as string} className="space-y-2">
                                    <Label>{label as string}</Label>
                                    <Input
                                        inputMode="decimal"
                                        value={value as string}
                                        onChange={(event) => (setter as React.Dispatch<React.SetStateAction<string>>)(event.target.value)}
                                    />
                                </div>
                            ))}
                            <div className="space-y-2">
                                <Label>Jurisdiction</Label>
                                <select
                                    className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                                    value={jurisdiction}
                                    onChange={(event) => setJurisdiction(event.target.value)}
                                >
                                    {Object.keys(JURISDICTION_SUMMARY).map((code) => (
                                        <option key={code} value={code}>{code}</option>
                                    ))}
                                </select>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <Scale className="h-5 w-5 text-indigo-700"/>
                                Rule Gate
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4 text-sm text-slate-700">
                            <p>{JURISDICTION_SUMMARY[jurisdiction]}</p>
                            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-900">
                                Hybrid/self-funding structures require recorded legal review before notices, credits or
                                owner loan terms can be used.
                            </div>
                            {!noticePreviewEnabled && (
                                <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-slate-700">
                                    PDF preview generation is controlled by the separate
                                    capital_funding_notice_preview feature gate.
                                </div>
                            )}
                            <div className="grid grid-cols-2 gap-3">
                                <Metric label="Requirement" value={formatCurrency(summary.requirement)}/>
                                <Metric label="Loan model" value={formatCurrency(summary.loanPrincipal)}/>
                                <Metric label="Upfront model" value={formatCurrency(summary.upfrontTotal)}/>
                                <Metric label="Interest" value={formatCurrency(summary.interest)}/>
                            </div>
                        </CardContent>
                    </Card>
                </section>

                <section className="grid gap-4 lg:grid-cols-[1fr_.8fr]">
                    <Card className="border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <Landmark className="h-5 w-5 text-sky-700"/>
                                Per-Lot Impact Preview
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="overflow-x-auto">
                            <table className="w-full min-w-[760px] text-left text-sm">
                                <thead className="border-b text-xs uppercase text-slate-500">
                                <tr>
                                    <th className="py-2">Lot</th>
                                    <th>Entitlement</th>
                                    <th>Election</th>
                                    <th>Project share</th>
                                    <th>Due now</th>
                                    <th>Quarterly</th>
                                    <th>Total cost</th>
                                </tr>
                                </thead>
                                <tbody>
                                {summary.lotRows.map((row) => (
                                    <tr key={row.lot} className="border-b last:border-b-0">
                                        <td className="py-3 font-medium text-slate-900">{row.lot}</td>
                                        <td>{row.entitlement}</td>
                                        <td>{row.choice}</td>
                                        <td>{formatCurrency(row.share)}</td>
                                        <td>{formatCurrency(row.dueNow)}</td>
                                        <td>{formatCurrency(row.quarterly)}</td>
                                        <td>{formatCurrency(row.total)}</td>
                                    </tr>
                                ))}
                                </tbody>
                            </table>
                        </CardContent>
                    </Card>

                    <Card className="border-slate-200 shadow-sm">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <ShieldCheck className="h-5 w-5 text-emerald-700"/>
                                PDF Preview Controls
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <Gate label="Legal review attached" checked={legalReview} onChange={setLegalReview}/>
                            <Gate label="AGM/SGM/GM motion approved" checked={motionApproved} onChange={setMotionApproved}/>
                            <Gate label="Current ownership snapshot frozen" checked={ownershipSnapshot} onChange={setOwnershipSnapshot}/>
                            <Gate label="Notice template approved" checked={noticeTemplate} onChange={setNoticeTemplate}/>
                            <div className="mt-4 rounded-md border border-slate-200 bg-white p-3 text-sm">
                                {readyForPreview ? (
                                    <span className="flex items-center gap-2 text-emerald-700">
                                        <CheckCircle2 className="h-4 w-4"/> Preview batch can be generated.
                                    </span>
                                ) : (
                                    <span className="flex items-center gap-2 text-slate-600">
                                        <AlertTriangle className="h-4 w-4 text-amber-600"/>
                                        Complete all checklist gates and enable PDF preview before generation.
                                    </span>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                </section>
            </div>
        </div>
    );
}

function Metric({label, value}: { label: string; value: string }) {
    return (
        <div className="rounded-md border border-slate-200 bg-white p-3">
            <div className="text-xs uppercase text-slate-500">{label}</div>
            <div className="mt-1 font-semibold text-slate-950">{value}</div>
        </div>
    );
}

function Gate({label, checked, onChange}: { label: string; checked: boolean; onChange: (next: boolean) => void }) {
    return (
        <label className="flex min-h-10 items-center gap-3 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm">
            <input
                type="checkbox"
                checked={checked}
                onChange={(event) => onChange(event.target.checked)}
                className="h-4 w-4"
            />
            {label}
        </label>
    );
}
