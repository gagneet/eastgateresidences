"use client";
import React from "react";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {Loader2, TrendingUp} from "lucide-react";

import {formatMoneyFromCents} from '@/lib/currency';
interface SavingsYTDCardProps {
    savingsYtdCents?: number;
    savingsByCategory?: Record<string, number>;
    totalLots?: number;
    loading?: boolean;
}
/**
 * @generated FunctionHeader
 * Function: SavingsYTDCard
 * Path: frontend/src/components/community-os/SavingsYTDCard.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function SavingsYTDCard({
                                           savingsYtdCents = 0,
                                           savingsByCategory = {},
                                           totalLots = 1,
                                           loading = false,
                                       }: SavingsYTDCardProps) {
    const perLot = totalLots > 0 ? savingsYtdCents / totalLots : 0;
    const categories = Object.entries(savingsByCategory).sort(([, a], [, b]) => b - a);
    const maxCents = categories[0]?.[1] ?? 1;

    if (loading) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm font-medium">Savings YTD</CardTitle>
                </CardHeader>
                <CardContent className="flex items-center justify-center py-10">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium flex items-center gap-2">
                    <TrendingUp className="h-4 w-4 text-green-500"/>
                    Savings YTD
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div>
                    <p className="text-3xl font-bold text-green-600">{formatMoneyFromCents(savingsYtdCents)}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {formatMoneyFromCents(perLot)} per lot average
                    </p>
                </div>

                {categories.length > 0 && (
                    <div className="space-y-2">
                        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                            By Category
                        </p>
                        {categories.map(([category, cents]) => (
                            <div key={category} className="space-y-0.5">
                                <div className="flex justify-between text-xs">
                                    <span className="capitalize">{category.replace(/_/g, " ")}</span>
                                    <span className="font-medium text-green-700">{formatMoneyFromCents(cents)}</span>
                                </div>
                                <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                                    <div
                                        className="h-full rounded-full bg-green-500"
                                        style={{width: `${(cents / maxCents) * 100}%`}}
                                    />
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                {categories.length === 0 && (
                    <p className="text-sm text-muted-foreground">No savings recorded yet.</p>
                )}
            </CardContent>
        </Card>
    );
}
