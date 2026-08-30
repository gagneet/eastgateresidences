// @featuretrace:capital-funding — Owner election surface for capital funding motions.
// Layer: frontend
// Data flow: CapitalFundingElectionsPage placeholder → future AGM/SGM/GM motion election APIs.
// Toggle: capital_funding_owner_elections

"use client";

import React from "react";
import {FileText, HandCoins, ShieldCheck, Vote} from "lucide-react";
import {Badge} from "../../components/ui/badge";
import {Button} from "../../components/ui/button";
import {Card, CardContent, CardHeader, CardTitle} from "../../components/ui/card";

const steps = [
    {icon: Vote, title: "Vote on the motion", text: "Review the AGM, SGM or GM agenda item and submit your vote when voting is open."},
    {icon: HandCoins, title: "Choose a funding path", text: "Where legally permitted, compare upfront and loan-funded impacts for your lot."},
    {icon: FileText, title: "Download documents", text: "Read the lender term sheet, funding explanation and legal disclaimers before acknowledging."},
    {icon: ShieldCheck, title: "Ask for support", text: "Request hardship or payment-plan review without automatic debt escalation while it is assessed."},
];

export default function CapitalFundingElectionsPage() {
    return (
        <div className="min-h-screen bg-slate-50">
            <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
                <header className="border-b border-slate-200 pb-5">
                    <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">Planned/Pilot</Badge>
                        <Badge variant="outline" className="border-slate-300 bg-white text-slate-700">Owner election preview</Badge>
                    </div>
                    <h1 className="mt-3 text-2xl font-semibold text-slate-950">Capital Funding Vote &amp; Election</h1>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
                        This page will extend the AGM/SGM/GM voting flow with personal impact statements, funding
                        elections, acknowledgements and hardship requests for approved capital funding projects.
                    </p>
                </header>

                <section className="grid gap-4 md:grid-cols-2">
                    {steps.map((step) => {
                        const Icon = step.icon;
                        return (
                            <Card key={step.title} className="border-slate-200 shadow-sm">
                                <CardHeader>
                                    <CardTitle className="flex items-center gap-2 text-base">
                                        <Icon className="h-5 w-5 text-indigo-700"/>
                                        {step.title}
                                    </CardTitle>
                                </CardHeader>
                                <CardContent className="text-sm leading-6 text-slate-600">{step.text}</CardContent>
                            </Card>
                        );
                    })}
                </section>

                <section className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
                    Funding choices are not personal financial advice. Hybrid, self-funding or levy-credit arrangements
                    depend on the approved motion, lender terms, tax advice and jurisdiction-specific legal review.
                </section>

                <div>
                    <Button variant="outline" disabled>Open Current Funding Project</Button>
                </div>
            </div>
        </div>
    );
}
