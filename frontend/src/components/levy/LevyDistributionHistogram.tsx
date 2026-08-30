// @ts-nocheck
"use client";
import React from "react";
import {Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,} from "recharts";
import {Card, CardContent, CardDescription, CardHeader, CardTitle,} from "@/components/ui/card";

interface DistributionData {
    boundaries: number[];
    current_counts: number[];
    proposed_counts: number[];
    current_stats: { mean: number; median: number; stdev: number };
    proposed_stats: { mean: number; median: number; stdev: number };
    equity_improvement: number;
}

interface Props {
    distribution: DistributionData | null | undefined;
}
/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/components/levy/LevyDistributionHistogram.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt = (v: number) => `$${Math.round(v).toLocaleString("en-AU")}`;
/**
 * @generated FunctionHeader
 * Function: LevyDistributionHistogram
 * Path: frontend/src/components/levy/LevyDistributionHistogram.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LevyDistributionHistogram: React.FC<Props> = ({distribution}) => {
    if (!distribution || !distribution.boundaries?.length) {
        return (
            <Card>
                <CardContent className="py-12 text-center text-muted-foreground">
                    No distribution data available — click Regenerate Model.
                </CardContent>
            </Card>
        );
    }

    const chartData = distribution.boundaries.slice(0, -1).map((lo: number, i: number) => ({
        range: `${fmt(lo)}–${fmt(distribution.boundaries[i + 1])}`,
        Current: distribution.current_counts[i],
        Proposed: distribution.proposed_counts[i],
    }));

    const improved = distribution.equity_improvement > 0;

    return (
        <div className="space-y-4">
            {/* Stats summary */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                    {label: "Current Mean", value: fmt(distribution.current_stats.mean)},
                    {label: "Proposed Mean", value: fmt(distribution.proposed_stats.mean)},
                    {label: "Current Std Dev", value: fmt(distribution.current_stats.stdev)},
                    {label: "Proposed Std Dev", value: fmt(distribution.proposed_stats.stdev)},
                ].map((s) => (
                    <Card key={s.label}>
                        <CardContent className="pt-4 pb-3">
                            <p className="text-xs text-muted-foreground">{s.label}</p>
                            <p className="font-bold">{s.value}</p>
                        </CardContent>
                    </Card>
                ))}
            </div>

            {/* Equity improvement banner */}
            {Math.abs(distribution.equity_improvement) > 1 && (
                <div
                    className={`rounded-lg p-3 text-sm font-medium ${
                        improved
                            ? "bg-emerald-50 text-emerald-800"
                            : "bg-amber-50 text-amber-800"
                    }`}
                >
                    {improved
                        ? `Equity improves — spread (std dev) reduces by ${fmt(distribution.equity_improvement)}, meaning levies become more tightly clustered around the fair share.`
                        : `Spread increases by ${fmt(Math.abs(distribution.equity_improvement))} — review transition caps to smooth the redistribution.`}
                </div>
            )}

            {/* Histogram */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-sm">Levy Distribution — Before vs After</CardTitle>
                    <CardDescription className="text-xs">
                        Number of units in each annual levy bracket
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <ResponsiveContainer width="100%" height={280}>
                        <BarChart data={chartData} margin={{left: 0, right: 16, top: 8, bottom: 48}}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false}/>
                            <XAxis
                                dataKey="range"
                                tick={{fontSize: 9}}
                                angle={-35}
                                textAnchor="end"
                                interval={0}
                            />
                            <YAxis tick={{fontSize: 10}} allowDecimals={false}/>
                            <Tooltip formatter={(v: any) => [`${v} units`, ""]}/>
                            <Legend wrapperStyle={{fontSize: 11}}/>
                            <Bar dataKey="Current" fill="#94a3b8" radius={[3, 3, 0, 0]}/>
                            <Bar dataKey="Proposed" fill="#3b82f6" radius={[3, 3, 0, 0]}/>
                        </BarChart>
                    </ResponsiveContainer>
                </CardContent>
            </Card>
        </div>
    );
};
