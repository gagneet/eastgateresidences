"use client";
import React, {useState} from "react";
import {CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis} from "recharts";
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from "@/components/ui/card";
import {Button} from "@/components/ui/button";
import {Badge} from "@/components/ui/badge";
import {Download, TrendingUp} from "lucide-react";

import {formatMoneyCompact} from '@/lib/currency';
interface ForecastItem {
    category: string;
    fund_type: string;
    projection_year_1: number;
    projection_year_2: number;
    projection_year_3: number;
    method: string;
    confidence_score: number;
}

interface ForecastChartProps {
    forecasts: ForecastItem[];
    year: string;
}

const METHOD_COLORS: Record<string, string> = {
    linear: "#2F4F4F",
    inflation: "#E07A5F",
    capital_works: "#F2CC8F",
};
/**
 * @generated FunctionHeader
 * Function: ForecastChart
 * Path: frontend/src/components/finance/ForecastChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const ForecastChart: React.FC<ForecastChartProps> = ({forecasts, year}) => {
    const [fundFilter, setFundFilter] = useState<"all" | "administrative" | "sinking">("all");

    const filtered = forecasts.filter(
        (f) => fundFilter === "all" || f.fund_type === fundFilter
    );

    // Build chart data — one row per category showing Y1/Y2/Y3
    const chartData = filtered.slice(0, 15).map((f) => {
        const cat = f.category ?? "";
        return {
            name: cat.length > 18 ? cat.slice(0, 18) + "…" : cat,
            [`Y+1`]: f.projection_year_1 ?? 0,
            [`Y+2`]: f.projection_year_2 ?? 0,
            [`Y+3`]: f.projection_year_3 ?? 0,
            method: f.method ?? "unknown",
            confidence: Math.round((f.confidence_score ?? 0) * 100),
        };
    });

    const totalY1 = filtered.reduce((s, f) => s + f.projection_year_1, 0);
    const totalY2 = filtered.reduce((s, f) => s + f.projection_year_2, 0);
    const totalY3 = filtered.reduce((s, f) => s + f.projection_year_3, 0);
    /**
     * @generated FunctionHeader
     * Function: handleExportCSV
     * Path: frontend/src/components/finance/ForecastChart.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExportCSV = () => {
        const rows = [
            ["Category", "Fund", "Method", "Confidence", "Y+1", "Y+2", "Y+3"],
            ...filtered.map((f) => [
                f.category ?? "", f.fund_type ?? "", f.method ?? "",
                `${Math.round((f.confidence_score ?? 0) * 100)}%`,
                f.projection_year_1 ?? 0, f.projection_year_2 ?? 0, f.projection_year_3 ?? 0,
            ]),
        ];
        const csv = rows.map((r) => r.join(",")).join("\n");
        const blob = new Blob([csv], {type: "text/csv"});
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `forecast_${year}.csv`;
        a.click();
    };

    return (
        <Card data-testid="forecast-chart">
            <CardHeader>
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <TrendingUp className="w-5 h-5 text-emerald-600"/>
                            3-Year Expense Forecast
                        </CardTitle>
                        <CardDescription>Projections for {parseInt(year) + 1}–{parseInt(year) + 3}</CardDescription>
                    </div>
                    <div className="flex gap-2">
                        {(["all", "administrative", "sinking"] as const).map((f) => (
                            <Button
                                key={f}
                                size="sm"
                                variant={fundFilter === f ? "default" : "outline"}
                                onClick={() => setFundFilter(f)}
                                className="capitalize"
                            >
                                {f === "all" ? "All" : f === "administrative" ? "Admin" : "Sinking"}
                            </Button>
                        ))}
                        <Button size="sm" variant="outline" onClick={handleExportCSV}>
                            <Download className="w-4 h-4 mr-1"/> CSV
                        </Button>
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                {/* Summary tiles */}
                <div className="grid grid-cols-3 gap-3 mb-4">
                    {[
                        {label: `${parseInt(year) + 1}`, value: totalY1},
                        {label: `${parseInt(year) + 2}`, value: totalY2},
                        {label: `${parseInt(year) + 3}`, value: totalY3},
                    ].map((t) => (
                        <div key={t.label} className="bg-emerald-50 rounded-lg p-3 text-center">
                            <div className="text-xs text-gray-500">Year {t.label}</div>
                            <div className="font-bold text-emerald-700">
                                ${t.value.toLocaleString("en-AU", {maximumFractionDigits: 0})}
                            </div>
                        </div>
                    ))}
                </div>

                <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={chartData} margin={{top: 5, right: 20, bottom: 60, left: 20}}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb"/>
                        <XAxis dataKey="name" angle={-40} textAnchor="end" tick={{fontSize: 10}}/>
                        <YAxis tickFormatter={(v) => formatMoneyCompact(v)} tick={{fontSize: 10}}/>
                        <Tooltip
                            formatter={(value: number | undefined): React.ReactNode => `$${(value ?? 0).toLocaleString("en-AU", {minimumFractionDigits: 2})}`}
                        />
                        <Legend/>
                        <Line type="monotone" dataKey="Y+1" stroke="#2F4F4F" strokeWidth={2} dot={{r: 3}}/>
                        <Line type="monotone" dataKey="Y+2" stroke="#E07A5F" strokeWidth={2} dot={{r: 3}}/>
                        <Line type="monotone" dataKey="Y+3" stroke="#F2CC8F" strokeWidth={2} strokeDasharray="5 5"
                              dot={{r: 3}}/>
                    </LineChart>
                </ResponsiveContainer>

                <div className="mt-2 flex flex-wrap gap-2">
                    {Object.entries(METHOD_COLORS).map(([method, color]) => {
                        const count = filtered.filter((f) => f.method === method).length;
                        if (!count) return null;
                        return (
                            <Badge key={method} variant="outline" style={{borderColor: color, color}}>
                                {method} × {count}
                            </Badge>
                        );
                    })}
                </div>
            </CardContent>
        </Card>
    );
};

export default ForecastChart;
