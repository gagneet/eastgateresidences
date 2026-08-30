"use client";
import React, {useState} from "react";
import {Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,} from "recharts";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Download, TrendingUp} from "lucide-react";

import {formatMoneyCompact} from '@/lib/currency';
interface FundProjection {
    projected_expenses: number;
    required_rate_per_uoe: number;
    rate_change_pct: number;
    total_collection: number;
    model: string;
    cpi_rate: number;
}

interface LevyProjectionData {
    year: string;
    base_admin_rate: number;
    base_sinking_rate: number;
    total_uoe: number;
    admin: Record<string, FundProjection>;
    sinking: Record<string, FundProjection>;
    combined: Record<string, {
        projected_expenses: number;
        total_collection: number;
        combined_rate_per_uoe: number;
    }>;
}

interface LevyProjectionChartProps {
    data: LevyProjectionData;
    year: string;
}
/**
 * @generated FunctionHeader
 * Function: fmt
 * Path: frontend/src/components/finance/LevyProjectionChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmt = (v: number) =>
    `$${v.toLocaleString("en-AU", {maximumFractionDigits: 0})}`;
/**
 * @generated FunctionHeader
 * Function: fmtRate
 * Path: frontend/src/components/finance/LevyProjectionChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fmtRate = (v: number) =>
    `$${v.toLocaleString("en-AU", {minimumFractionDigits: 4, maximumFractionDigits: 4})}`;
/**
 * @generated FunctionHeader
 * Function: LevyProjectionChart
 * Path: frontend/src/components/finance/LevyProjectionChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const LevyProjectionChart: React.FC<LevyProjectionChartProps> = ({data, year}) => {
    const [view, setView] = useState<"collection" | "rate" | "expenses">("collection");

    const baseYear = parseInt(year);
    const projectionYears = Object.keys(data.combined).sort();
    // Shared tooltip formatter for currency values
    /**
     * @generated FunctionHeader
     * Function: currencyTooltipFormatter
     * Path: frontend/src/components/finance/LevyProjectionChart.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const currencyTooltipFormatter = (
        v: number | undefined,
        name: string | number | undefined,
    ): [React.ReactNode, React.ReactNode] => [fmt(v ?? 0), String(name ?? "")];
    // Shared tooltip formatter for rate values
    /**
     * @generated FunctionHeader
     * Function: rateTooltipFormatter
     * Path: frontend/src/components/finance/LevyProjectionChart.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const rateTooltipFormatter = (
        v: number | undefined,
        name: string | number | undefined,
    ): [React.ReactNode, React.ReactNode] => [fmtRate(v ?? 0), String(name ?? "")];

    // Build chart data
    const chartData = projectionYears.map((yr) => {
        const admin = data.admin[yr];
        const sinking = data.sinking[yr];
        const combined = data.combined[yr];
        return {
            year: yr,
            "Admin Rate": admin ? Math.round(admin.required_rate_per_uoe * 10000) / 10000 : 0,
            "Sinking Rate": sinking ? Math.round(sinking.required_rate_per_uoe * 10000) / 10000 : 0,
            "Admin Collection": admin ? Math.round(admin.total_collection) : 0,
            "Sinking Collection": sinking ? Math.round(sinking.total_collection) : 0,
            "Admin Expenses": admin ? Math.round(admin.projected_expenses) : 0,
            "Sinking Expenses": sinking ? Math.round(sinking.projected_expenses) : 0,
            "Total Collection": combined ? Math.round(combined.total_collection) : 0,
            "Total Expenses": combined ? Math.round(combined.projected_expenses) : 0,
            "Admin Change %": admin ? admin.rate_change_pct : 0,
            "Sinking Change %": sinking ? sinking.rate_change_pct : 0,
        };
    });
    /**
     * @generated FunctionHeader
     * Function: handleExport
     * Path: frontend/src/components/finance/LevyProjectionChart.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExport = () => {
        const rows = [
            ["Year", "Admin Rate/UOE", "Admin Change %", "Sinking Rate/UOE", "Sinking Change %",
                "Total Collection", "Total Expenses"],
            ...chartData.map((r) => [
                r.year,
                r["Admin Rate"], `${r["Admin Change %"].toFixed(2)}%`,
                r["Sinking Rate"], `${r["Sinking Change %"].toFixed(2)}%`,
                r["Total Collection"], r["Total Expenses"],
            ]),
        ];
        const csv = rows.map((row) => row.join(",")).join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `levy_projection_${year}.csv`;
        a.click();
    };
    /**
     * @generated FunctionHeader
     * Function: renderChart
     * Path: frontend/src/components/finance/LevyProjectionChart.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const renderChart = () => {
        if (view === "collection") {
            return (
                <BarChart data={chartData} margin={{top: 10, right: 20, bottom: 5, left: 20}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb"/>
                    <XAxis dataKey="year" tick={{fontSize: 11}}/>
                    <YAxis tickFormatter={(v) => formatMoneyCompact(v)} tick={{fontSize: 10}}/>
                    <Tooltip
                        formatter={currencyTooltipFormatter}
                        contentStyle={{fontSize: 11}}
                    />
                    <Legend wrapperStyle={{fontSize: 11}}/>
                    <Bar dataKey="Admin Collection" stackId="a" fill="#2F4F4F" name="Admin Collection"/>
                    <Bar dataKey="Sinking Collection" stackId="a" fill="#6b7280" name="Sinking Collection"
                         radius={[4, 4, 0, 0]}/>
                </BarChart>
            );
        }
        if (view === "rate") {
            return (
                <BarChart data={chartData} margin={{top: 10, right: 20, bottom: 5, left: 20}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb"/>
                    <XAxis dataKey="year" tick={{fontSize: 11}}/>
                    <YAxis tickFormatter={(v) => `$${v.toFixed(2)}`} tick={{fontSize: 10}}/>
                    <Tooltip
                        formatter={rateTooltipFormatter}
                        contentStyle={{fontSize: 11}}
                    />
                    <Legend wrapperStyle={{fontSize: 11}}/>
                    <Bar dataKey="Admin Rate" fill="#2F4F4F" name="Admin Rate / UOE" radius={[4, 4, 0, 0]}>
                        <LabelList
                            dataKey="Admin Change %"
                            position="top"
                            formatter={(v: unknown) => {
                                const n = Number(v);
                                return isNaN(n) ? "" : n > 0 ? `+${n.toFixed(1)}%` : `${n.toFixed(1)}%`;
                            }}
                            style={{fontSize: 9, fill: "#374151"}}
                        />
                    </Bar>
                    <Bar dataKey="Sinking Rate" fill="#E07A5F" name="Sinking Rate / UOE" radius={[4, 4, 0, 0]}>
                        <LabelList
                            dataKey="Sinking Change %"
                            position="top"
                            formatter={(v: unknown) => {
                                const n = Number(v);
                                return isNaN(n) ? "" : n > 0 ? `+${n.toFixed(1)}%` : `${n.toFixed(1)}%`;
                            }}
                            style={{fontSize: 9, fill: "#374151"}}
                        />
                    </Bar>
                </BarChart>
            );
        }
        // expenses view
        return (
            <BarChart data={chartData} margin={{top: 10, right: 20, bottom: 5, left: 20}}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb"/>
                <XAxis dataKey="year" tick={{fontSize: 11}}/>
                <YAxis tickFormatter={(v) => formatMoneyCompact(v)} tick={{fontSize: 10}}/>
                <Tooltip
                    formatter={currencyTooltipFormatter}
                    contentStyle={{fontSize: 11}}
                />
                <Legend wrapperStyle={{fontSize: 11}}/>
                <Bar dataKey="Admin Expenses" stackId="a" fill="#2F4F4F" name="Admin Expenses"/>
                <Bar dataKey="Sinking Expenses" stackId="a" fill="#6b7280" name="Sinking Expenses"
                     radius={[4, 4, 0, 0]}/>
            </BarChart>
        );
    };

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <TrendingUp className="w-5 h-5 text-emerald-600"/>
                            3-Year Levy Rate Projection
                        </CardTitle>
                        <CardDescription>
                            Projected levy rates for {baseYear + 1}–{baseYear + 3} based
                            on {(data.admin[projectionYears[0]]?.cpi_rate ?? 0.035) * 100}% CPI model
                        </CardDescription>
                    </div>
                    <div className="flex gap-2 flex-wrap">
                        {(["collection", "rate", "expenses"] as const).map((v) => (
                            <Button
                                key={v}
                                size="sm"
                                variant={view === v ? "default" : "outline"}
                                onClick={() => setView(v)}
                                className="capitalize text-xs"
                            >
                                {v === "collection" ? "Collection" : v === "rate" ? "Rate / UOE" : "Expenses"}
                            </Button>
                        ))}
                        <Button size="sm" variant="outline" onClick={handleExport}>
                            <Download className="w-4 h-4 mr-1"/> CSV
                        </Button>
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                {/* Base rates summary */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    {[
                        {label: `Base Admin Rate (${year})`, value: fmtRate(data.base_admin_rate)},
                        {label: `Base Sinking Rate (${year})`, value: fmtRate(data.base_sinking_rate)},
                        {label: "Total UOE", value: data.total_uoe.toLocaleString()},
                        {
                            label: `Combined Rate (${year})`,
                            value: fmtRate((data.base_admin_rate || 0) + (data.base_sinking_rate || 0)),
                        },
                    ].map((m) => (
                        <div key={m.label} className="bg-emerald-50 rounded-lg p-2 text-center">
                            <div className="text-xs text-gray-500">{m.label}</div>
                            <div className="font-bold text-emerald-700 text-sm">{m.value}</div>
                        </div>
                    ))}
                </div>

                <ResponsiveContainer width="100%" height={260}>
                    {renderChart()}
                </ResponsiveContainer>

                {/* Projection table */}
                <div className="mt-4 overflow-auto rounded border text-xs">
                    <table className="w-full">
                        <thead className="bg-gray-50 border-b">
                        <tr>
                            {["Year", "Admin Rate/UOE", "Rate Δ", "Sinking Rate/UOE", "Rate Δ", "Total Collection", "Total Expenses"].map((h) => (
                                <th key={h} className="px-3 py-2 text-left font-semibold text-gray-600">{h}</th>
                            ))}
                        </tr>
                        </thead>
                        <tbody>
                        {chartData.map((row, i) => (
                            <tr key={row.year} className={i % 2 === 0 ? "bg-white" : "bg-gray-50"}>
                                <td className="px-3 py-1.5 font-semibold">{row.year}</td>
                                <td className="px-3 py-1.5">{fmtRate(row["Admin Rate"])}</td>
                                <td className={`px-3 py-1.5 font-semibold ${row["Admin Change %"] > 0 ? "text-amber-600" : "text-emerald-600"}`}>
                                    {row["Admin Change %"] > 0 ? "+" : ""}{row["Admin Change %"].toFixed(2)}%
                                </td>
                                <td className="px-3 py-1.5">{fmtRate(row["Sinking Rate"])}</td>
                                <td className={`px-3 py-1.5 font-semibold ${row["Sinking Change %"] > 0 ? "text-amber-600" : "text-emerald-600"}`}>
                                    {row["Sinking Change %"] > 0 ? "+" : ""}{row["Sinking Change %"].toFixed(2)}%
                                </td>
                                <td className="px-3 py-1.5 text-emerald-700">{fmt(row["Total Collection"])}</td>
                                <td className="px-3 py-1.5 text-gray-700">{fmt(row["Total Expenses"])}</td>
                            </tr>
                        ))}
                        </tbody>
                    </table>
                </div>

                <p className="text-xs text-gray-400 mt-2">
                    Projection uses CPI inflation model. Actual levy decisions are made by the Owners Corporation.
                </p>
            </CardContent>
        </Card>
    );
};

export default LevyProjectionChart;
