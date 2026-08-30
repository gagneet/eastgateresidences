// @featuretrace:occupancy_intelligence — 12-month trend chart for occupancy changes
// Layer: frontend
// Data flow: OccupancyIntelligencePage → OccupancyTrendChart (trends data, building-scoped)
// Related: frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx
// Scope: (building-scoped)

"use client";

import React from "react";
import {
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import {Card, CardContent, CardHeader, CardTitle} from "@/components/ui/card";
import {TrendingUp} from "lucide-react";

interface TrendPoint {
    month: string;
    tenant_count: number;
    owner_occupied_count: number;
    investor_unknown_count: number;
    total: number;
}

interface OccupancyTrendChartProps {
    trend: TrendPoint[];
}
/**
 * @generated FunctionHeader
 * Function: formatMonth
 * Path: frontend/src/components/occupancy/OccupancyTrendChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatMonth(ym: string): string {
    try {
        const [year, month] = ym.split("-");
        return new Date(parseInt(year), parseInt(month) - 1).toLocaleDateString("en-AU", {
            month: "short",
            year: "2-digit",
        });
    } catch {
        return ym;
    }
}
/**
 * @generated FunctionHeader
 * Function: CustomTooltip
 * Path: frontend/src/components/occupancy/OccupancyTrendChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const CustomTooltip = ({active, payload, label}: any) => {
    if (!active || !payload?.length) return null;
    return (
        <div className="bg-card border border-border rounded-lg shadow-lg px-3 py-2 text-sm space-y-1 min-w-[160px]">
            <p className="font-semibold text-foreground">{label}</p>
            {payload.map((entry: any) => (
                <div key={entry.dataKey} className="flex justify-between gap-4">
                    <span style={{color: entry.stroke}} className="font-medium">{entry.name}</span>
                    <span className="text-muted-foreground">{entry.value}</span>
                </div>
            ))}
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: OccupancyTrendChart
 * Path: frontend/src/components/occupancy/OccupancyTrendChart.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const OccupancyTrendChart: React.FC<OccupancyTrendChartProps> = ({trend}) => {
    const chartData = trend.map(p => ({
        ...p,
        month_label: formatMonth(p.month),
    }));

    return (
        <Card data-testid="occupancy-trend-chart">
            <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                    <TrendingUp className="h-4 w-4 text-emerald-500"/>
                    Occupancy Trend — 12 Months
                </CardTitle>
                <p className="text-xs text-muted-foreground">Monthly breakdown by classification</p>
            </CardHeader>
            <CardContent>
                {chartData.length === 0 ? (
                    <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                        No trend data available yet.
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height={220}>
                        <LineChart data={chartData} margin={{top: 4, right: 16, left: 0, bottom: 0}}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9"/>
                            <XAxis
                                dataKey="month_label"
                                tick={{fontSize: 11, fill: "#94a3b8"}}
                                axisLine={false}
                                tickLine={false}
                            />
                            <YAxis
                                tick={{fontSize: 11, fill: "#94a3b8"}}
                                axisLine={false}
                                tickLine={false}
                                allowDecimals={false}
                            />
                            <Tooltip content={<CustomTooltip/>}/>
                            <Legend
                                iconType="circle"
                                iconSize={8}
                                formatter={(v) => (
                                    <span className="text-xs font-medium text-foreground">{v}</span>
                                )}
                            />
                            <Line
                                type="monotone"
                                dataKey="tenant_count"
                                name="Tenant"
                                stroke="#ef4444"
                                strokeWidth={2}
                                dot={false}
                                activeDot={{r: 4}}
                            />
                            <Line
                                type="monotone"
                                dataKey="owner_occupied_count"
                                name="Owner-Occupied"
                                stroke="#22c55e"
                                strokeWidth={2}
                                dot={false}
                                activeDot={{r: 4}}
                            />
                            <Line
                                type="monotone"
                                dataKey="investor_unknown_count"
                                name="Investor / Unknown"
                                stroke="#94a3b8"
                                strokeWidth={2}
                                dot={false}
                                activeDot={{r: 4}}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                )}
            </CardContent>
        </Card>
    );
};

export default OccupancyTrendChart;